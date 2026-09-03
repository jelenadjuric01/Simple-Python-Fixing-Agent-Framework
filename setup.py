#!/usr/bin/env python3
"""The workshop's model setup: the tier, the models, the context window, the model choice.

Two models per tier, because the course has two kinds of agent in it: a coding model for the
first two editions and a *thinking* model for the third. Both are pulled in the same run.

Not the entry point. Run `./setup.sh` (macOS, Linux, WSL2, ChromeOS) or
`powershell -ExecutionPolicy Bypass -File setup.ps1` (Windows). Those install Python 3.12 when
the machine has none or has one too old, then run this file under it.

The split is drawn where the two halves genuinely differ:

  * Installing an interpreter is per-OS, and cannot be done from Python at all — a Python script
    cannot install the thing it needs in order to run. That is `setup.sh` / `setup.ps1`, and it
    is why a machine with no Python whatsoever still works.
  * Everything here is decisions and state: which tier this machine can run, whether the model
    is pulled, whether it was derived with the right context window, what goes in
    `.agentfix.env`. None of that differs by OS, so it exists once rather than once per
    platform, and the platform differences that do remain are a table (see `plan_steps`) instead
    of a second implementation.

Written to 3.8-compatible syntax so an old interpreter can still parse this and print something
useful instead of a SyntaxError. Standard library only.

Nothing runs without showing you the command first and asking, unless you pass --yes.

    ./setup.sh                       # the normal way in
    python3 setup.py --dry-run       # print the plan, change nothing
    python3 setup.py --tier qwen     # force the small-model tier
    python3 setup.py --tier colab    # print the notebook pointer and exit
    python3 setup.py --yes           # unattended: assume yes (run this before the session)
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent

# These mirror the three editions' config.py. Duplicated rather than imported, deliberately:
# setup.py has to run before the framework lessons' working directories exist, and the course
# root contains a *section* directory also called `agentfix/`, which Python would import instead
# of the package. If you change a model name in a config.py, change it here too.
#
# Two models per tier, because the course has two kinds of agent in it. The Instruct checkpoint
# is what the no-framework agent (`agentfix`) and the LangGraph one (`agentlang`) run on. The
# Thinking checkpoint — same 12B/A2.5B weights, trained to emit its reasoning inside
# `<think>...</think>` before it answers — is what the third edition (`agentgraph`) is *about*.
# A non-thinking model does not substitute for it: the agent still runs, and is silently the
# Act-only agent from the previous lesson, with every reasoning-shaped thing in the trace gone.
MELLUM_BASE_MODEL = "hf.co/JetBrains/Mellum2-12B-A2.5B-Instruct-GGUF-Q4_K_M"
MELLUM_THINKING_BASE_MODEL = "hf.co/JetBrains/Mellum2-12B-A2.5B-Thinking-GGUF-Q4_K_M"

# The small tier needs the same pair for the same reason, and it cannot be one model twice:
# `qwen2.5-coder:1.5b` has no thinking mode at all, so `agentgraph` gets qwen3 — the smallest
# thing that both reasons and calls tools.
QWEN_BASE_MODEL = "qwen2.5-coder:1.5b"
QWEN3_BASE_MODEL = "qwen3:1.7b"

MIN_CONTEXT_LENGTH = 16384

# The variables the editions read to override their default model. `agentfix` and `agentlang`
# share MELLUM_MODEL; `agentgraph` reads AGENTGRAPH_MODEL first, because a single variable
# cannot name both a coding model and a thinking one — pointing MELLUM_MODEL at the small
# tier's `agentfix-qwen` would silently take the reasoning out of the third lesson.
INSTRUCT_ENV_KEY = "MELLUM_MODEL"
THINKING_ENV_KEY = "AGENTGRAPH_MODEL"
ENV_KEYS = (INSTRUCT_ENV_KEY, THINKING_ENV_KEY)

MIN_PYTHON = (3, 12)
PYTHON_SERIES = "3.12"

# The tier boundaries, in terms of what has to be in memory AT ONCE. Each tier installs two
# models now, but a lesson runs one edition at a time, so the number that decides the tier is
# still the size of the LARGER single model rather than the sum: 16 GiB is the line the
# workshop's own RAM check uses, because one 8 GB model plus the OS does not fit comfortably
# below it. What the second model costs is disk, and — if you switch editions inside Ollama's
# five-minute keep-alive window — a second copy resident alongside the first. That is a
# scheduling problem, not a RAM tier: setup starts the server with OLLAMA_MAX_LOADED_MODELS=1
# wherever it starts the server itself, and prints the `ollama stop` line wherever it cannot.
#
# The 8 GiB floor is newer and comes from a fresh Chromebook with 3.4 GB, which the RAM check
# happily routed to the small model — where the IDE, the Ollama server and a 16k context still do
# not fit. Below the floor the honest answer is that no local model belongs on this machine.
MELLUM2_MIN_RAM_BYTES = 16 * 1024 ** 3
LOCAL_MIN_RAM_BYTES = 8 * 1024 ** 3

# Disk is where the second model actually shows up, and running out of it happens mid-pull:
# Ollama writes the blob, then the manifest, and a filesystem with nothing left over fails
# somewhere in between. The margin is for that, and for the derived models' manifests.
DISK_MARGIN_BYTES = 2 * 1024 ** 3

# Only one model in memory at a time. Ollama's default is three, which is right for a serving
# box and wrong here: two 8 GB models resident on a 16 GB laptop is the one way this course can
# make a correctly set-up machine look broken. Set for the server process, so it only applies
# where setup is the thing that starts the server.
SERVER_ENV = {"OLLAMA_MAX_LOADED_MODELS": "1"}

ENV_FILE = ROOT / ".agentfix.env"
# `Modelfile` is the one committed to the repo (and shown to learners as course content); the
# other three are generated, because `ollama create` runs with the course root as its working
# directory and every derived model needs its own file there.
MELLUM_MODELFILE = ROOT / "Modelfile"
MELLUM_THINKING_MODELFILE = ROOT / "Modelfile.agentgraph-thinking"
QWEN_MODELFILE = ROOT / "Modelfile.agentfix-qwen"
QWEN3_MODELFILE = ROOT / "Modelfile.agentgraph-qwen3"
NOTEBOOK = "notebooks/agentfix.ipynb"

PYTHON_ORG = "https://www.python.org/downloads/"
OLLAMA_DOWNLOAD = "https://ollama.com/download"

SERVER_TIMEOUT_S = 90.0

# `run_command`'s verdict when the learner said no, as opposed to when the command ran and
# failed. A caller that has to clean something up needs to tell those apart: a command that
# exits non-zero because there was nothing to remove is fine, and a command that never ran
# because it was declined leaves the machine in the state the step was there to fix.
DECLINED = "declined — nothing was changed"


# --------------------------------------------------------------------------------------------
# Options, platform, tiers
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Options:
    tier: str = "auto"
    # True when setup.sh / setup.ps1 launched us, so the interpreter is already known good.
    # Only affects wording: nothing here depends on having been bootstrapped.
    bootstrapped: bool = False
    yes: bool = False
    dry_run: bool = False
    shell_env: bool = True
    base_url: str = "http://localhost:11434"


@dataclass(frozen=True)
class Platform:
    """Everything about the machine that changes what we run. Resolved once, then passed around.

    Keeping it a value rather than calling `sys.platform` all over the place is what makes the
    platform matrix testable: the tests build a Platform for each OS and assert on the plan,
    with none of the tools installed.
    """

    system: str  # "macos" | "linux" | "windows"
    wsl: bool = False
    package_manager: Optional[str] = None  # "brew" | "apt" | "dnf" | "pacman" | "winget"
    total_ram_bytes: Optional[int] = None
    has_systemd: bool = False
    has_ollama_app: bool = False  # macOS: /Applications/Ollama.app, which `open -a` needs
    # Whether a package install needs a `sudo` in front of it. Already-root is not exotic — it
    # is every container and plenty of minimal Debian installs, and those often have no `sudo`
    # binary at all, so hardcoding it turns "install zstd" into "No such file or directory".
    root: bool = False
    has_sudo: bool = True

    @property
    def privilege_prefix(self) -> List[str]:
        if self.root or not self.has_sudo:
            return []
        return ["sudo"]

    @property
    def powershell(self) -> bool:
        return self.system == "windows"


@dataclass(frozen=True)
class Model:
    """One model this machine needs: what to pull, what to derive, and who reads it.

    A tier is a pair of these rather than a single model, because the course has two kinds of
    agent in it and the difference is not a detail: the first two editions write code, the third
    one reasons before it writes. Both halves are pulled in the same run so nobody meets an 8 GB
    download in the middle of the lesson that needs it.
    """

    kind: str  # "instruct" | "thinking" — what distinguishes it, and what names its steps
    editions: str  # which of the course's agents run on it, for the plan's wording
    base: str
    derived: str
    modelfile: Path
    # False only for the repo's committed `Modelfile`; the other three are written by setup.
    generate_modelfile: bool
    download: str  # the size, in the words the step uses to warn about it
    disk_bytes: int  # what the pull costs on disk, for the free-space note
    env_key: str  # the variable the editions using this model read
    # The value those editions need, or None when `derived` already is their config.DEFAULT_MODEL
    # and no override is required.
    env_value: Optional[str]


@dataclass(frozen=True)
class Tier:
    """What this machine can run: a name, and the models that go with it."""

    name: str
    models: Tuple[Model, ...]

    @property
    def local(self) -> bool:
        """Is there anything to install on this machine? False only for the colab verdict."""
        return bool(self.models)

    @property
    def env(self) -> Dict[str, str]:
        """The model overrides this tier needs — one entry per edition that is not on default."""
        return dict(
            (model.env_key, model.env_value)
            for model in self.models
            if model.env_value is not None
        )

    @property
    def disk_bytes(self) -> int:
        """Free disk the whole tier needs. A sum, unlike the RAM figure: both models are kept."""
        return sum(model.disk_bytes for model in self.models) + DISK_MARGIN_BYTES

    @property
    def largest_model_bytes(self) -> int:
        """The one that has to fit in memory. Never the sum — one edition runs at a time."""
        return max(model.disk_bytes for model in self.models)

    @property
    def derived_names(self) -> str:
        return " and ".join(model.derived for model in self.models)


# Not a model at all: the verdict "run this in a browser instead". It carries no models, so it
# never reaches plan_steps.
COLAB = Tier(name="colab", models=())

MELLUM2 = Tier(
    name="mellum2",
    models=(
        Model(
            kind="instruct",
            editions="agentfix and agentlang",
            base=MELLUM_BASE_MODEL,
            derived="agentfix-mellum2",
            modelfile=MELLUM_MODELFILE,
            generate_modelfile=False,
            download="about 8 GB",
            disk_bytes=8_700_000_000,
            env_key=INSTRUCT_ENV_KEY,
            env_value=None,
        ),
        Model(
            kind="thinking",
            editions="agentgraph",
            base=MELLUM_THINKING_BASE_MODEL,
            derived="agentgraph-mellum2-thinking",
            modelfile=MELLUM_THINKING_MODELFILE,
            generate_modelfile=True,
            download="another 8 GB",
            disk_bytes=8_700_000_000,
            env_key=THINKING_ENV_KEY,
            env_value=None,
        ),
    ),
)

QWEN = Tier(
    name="qwen",
    models=(
        Model(
            kind="instruct",
            editions="agentfix and agentlang",
            base=QWEN_BASE_MODEL,
            derived="agentfix-qwen",
            modelfile=QWEN_MODELFILE,
            generate_modelfile=True,
            download="about 1 GB",
            disk_bytes=1_000_000_000,
            env_key=INSTRUCT_ENV_KEY,
            env_value="agentfix-qwen",
        ),
        Model(
            kind="thinking",
            editions="agentgraph",
            base=QWEN3_BASE_MODEL,
            derived="agentgraph-qwen3",
            modelfile=QWEN3_MODELFILE,
            generate_modelfile=True,
            download="about 1.4 GB",
            disk_bytes=1_500_000_000,
            env_key=THINKING_ENV_KEY,
            env_value="agentgraph-qwen3",
        ),
    ),
)

TIERS = {"mellum2": MELLUM2, "qwen": QWEN}


# --------------------------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------------------------


class _MemoryStatusEx(ctypes.Structure):
    """Windows MEMORYSTATUSEX. Field order and widths are the ABI — do not reorder."""

    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def total_ram_bytes() -> Optional[int]:
    """Total physical RAM, or None on a platform we cannot read.

    None is not a failure — it means "ask instead of guessing", the same way `agentfix doctor`
    treats unreadable memory as "check by hand" rather than a failed check.

    On WSL2 /proc/meminfo reports the *WSL allocation*, not host RAM. That is the number we
    want: the allocation is what has to hold the model.
    """
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            return None
        return None

    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True
            ).stdout
            return int(out.strip())
        except (OSError, ValueError, subprocess.CalledProcessError):
            return None

    if os.name == "nt":
        # ctypes rather than a subprocess: `wmic` is deprecated since Windows 10 21H1 and absent
        # by default on current Windows 11, so it would pass here and fail on a learner's
        # machine. `Get-CimInstance` works but costs a subprocess. This is stdlib and in-process.
        try:
            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return int(status.ullTotalPhys)
        except (AttributeError, OSError, ValueError):
            return None

    return None


def _detect_package_manager(system: str) -> Optional[str]:
    if system == "macos":
        return "brew" if shutil.which("brew") else None
    if system == "windows":
        return "winget" if shutil.which("winget") else None
    for manager in ("apt-get", "dnf", "pacman"):
        if shutil.which(manager):
            return "apt" if manager == "apt-get" else manager
    return None


def _detect_wsl() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    if "microsoft" in os.environ.get("WSL_DISTRO_NAME", "").lower():
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def detect_platform() -> Platform:
    if sys.platform == "darwin":
        system = "macos"
    elif os.name == "nt":
        system = "windows"
    elif sys.platform.startswith("linux"):
        system = "linux"
    else:
        system = sys.platform

    return Platform(
        system=system,
        wsl=_detect_wsl(),
        package_manager=_detect_package_manager(system),
        total_ram_bytes=total_ram_bytes(),
        # `systemctl` on PATH is not enough: it exists in WSL2 images where systemd is off.
        # /run/systemd/system only exists when systemd is actually pid 1.
        has_systemd=bool(shutil.which("systemctl")) and Path("/run/systemd/system").is_dir(),
        has_ollama_app=Path("/Applications/Ollama.app").is_dir(),
        # geteuid does not exist on Windows, where the question does not arise either.
        root=hasattr(os, "geteuid") and os.geteuid() == 0,
        has_sudo=bool(shutil.which("sudo")),
    )


# --------------------------------------------------------------------------------------------
# Talking to Ollama, running commands
# --------------------------------------------------------------------------------------------


def ollama_api(base_url: str, path: str, timeout_s: float = 5.0) -> Optional[dict]:
    """GET Ollama's native API. None on any failure — callers treat that as "not running"."""
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + path, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def installed_models(base_url: str) -> Optional[List[str]]:
    """Model names known to the server, exactly as it reports them; None if it is not answering."""
    payload = ollama_api(base_url, "/api/tags")
    if payload is None:
        return None
    return [model.get("name", "") for model in payload.get("models", []) if model.get("name")]


def has_model(names: Sequence[str], wanted: str) -> bool:
    """Is `wanted` one of these models?

    Ollama always reports a tag, and adds `:latest` to anything pulled without one. So
    `agentfix-qwen` matches `agentfix-qwen:latest`, while `qwen2.5-coder:1.5b` — where the tag
    is the whole point — has to match exactly. Case is preserved by the server, checked against
    a real `/api/tags` response, so `hf.co/JetBrains/...` compares as written.
    """
    for name in names:
        if name == wanted or name == wanted + ":latest":
            return True
    return False


def _spawn_background(
    command: Sequence[str], env: Optional[Dict[str, str]] = None
) -> Tuple[bool, str]:
    """Start a blocking server command detached, so it outlives this script."""
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if env:
        # Merged onto our own environment rather than replacing it: the server still needs PATH,
        # HOME and (on Windows) SystemRoot to find its own model directory.
        merged = dict(os.environ)
        merged.update(env)
        kwargs["env"] = merged
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: no console window, and Ctrl-C in this
        # script does not kill the server it just started.
        detached = 0x00000008
        kwargs["creationflags"] = detached | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(list(command), cwd=str(ROOT), **kwargs)
    except OSError as error:
        return False, "could not start it: %s" % error
    return True, "started in the background"


def confirm(opts: Options, question: str) -> bool:
    if opts.yes:
        return True
    if not sys.stdin.isatty():
        print("     (not a terminal, so nothing is assumed — re-run with --yes)")
        return False
    try:
        reply = input("     %s [Y/n] " % question).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return reply in ("", "y", "yes")


def run_command(
    command: Sequence[str] | str,
    opts: Options,
    shell: bool = False,
    quiet: bool = False,
    background: bool = False,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[bool, str]:
    """The single execution seam. Shows the command, asks, runs it, reports a verdict.

    Every command this script runs goes through here, including the backgrounded server — which
    is what makes --dry-run trustworthy and lets the tests assert that nothing ran. `background`
    is for a command that never returns (`ollama serve`): it is detached instead of waited on.
    """
    printable = command if isinstance(command, str) else " ".join(command)
    # The environment is part of the command as far as the learner is concerned — printing
    # `ollama serve` while quietly setting OLLAMA_MAX_LOADED_MODELS would make this script the
    # kind of thing that changes something it did not show you.
    prefix = "".join("%s=%s " % (key, env[key]) for key in sorted(env)) if env else ""
    print("     $ %s%s%s" % (prefix, printable, "   (in the background)" if background else ""))
    if not confirm(opts, "run it?"):
        return False, DECLINED
    if background:
        return _spawn_background(
            command if isinstance(command, list) else list(command), env=env
        )
    try:
        merged = None
        if env:
            merged = dict(os.environ)
            merged.update(env)
        completed = subprocess.run(
            command,
            shell=shell,
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL if quiet else None,
            env=merged,
        )
    except OSError as error:
        return False, "could not run it: %s" % error
    if completed.returncode != 0:
        return False, "exited with status %d" % completed.returncode
    return True, "done"


# --------------------------------------------------------------------------------------------
# Tier resolution
# --------------------------------------------------------------------------------------------


def _ask_tier(opts: Options) -> Optional[Tier]:
    if opts.yes:
        # Unattended and no RAM figure: take the tier that fits any machine and say so loudly,
        # rather than committing someone to an 8 GB model sight unseen.
        print("  ! RAM is unreadable here and --yes was given, so assuming the qwen tier.")
        print("    Re-run with --tier mellum2 if this machine has 16 GB or more.")
        return QWEN
    if not sys.stdin.isatty():
        return None
    print("  Which tier do you want?")
    print("    1) mellum2 — the reference path: 16 GB RAM, and two 8 GB models to download")
    print("    2) qwen    — the small fallback: two models, ~2.5 GB in total")
    try:
        reply = input("  1 or 2: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return {"1": MELLUM2, "2": QWEN}.get(reply)


def _wsl_memory_hint() -> None:
    print("    That is WSL2's allocation, not your machine's RAM, and you can raise it: put")
    print("    `[wsl2]` and `memory=16GB` in %UserProfile%\\.wslconfig, run `wsl --shutdown`")
    print("    in PowerShell, reopen the shell, and run this script again.")


def resolve_tier(opts: Options, plat: Platform) -> Optional[Tier]:
    """Flags plus RAM -> tier. Pure apart from the interactive fallback.

    Tiers are named, not numbered: the numbered names in the old instructions drifted between
    the README and doctor.py, and a name cannot drift.
    """
    total = plat.total_ram_bytes

    if opts.tier in TIERS:
        tier = TIERS[opts.tier]
        # An explicit flag is an instruction, not a suggestion — but say so if the machine is
        # under the floor, because the failure mode is a swapping laptop, not an error message.
        if total is not None and total < LOCAL_MIN_RAM_BYTES:
            print("  ! %.1f GB RAM is below the %d GB floor for any local model, and you asked"
                  % (total / 1024 ** 3, LOCAL_MIN_RAM_BYTES // 1024 ** 3))
            print("    for --tier %s anyway. Expect it to be slow or to fail to load." % tier.name)
        return tier

    if total is None:
        print("  Could not read this machine's RAM, so the tier cannot be chosen for you.")
        return _ask_tier(opts)

    gigabytes = total / 1024 ** 3
    if total >= MELLUM2_MIN_RAM_BYTES:
        # 16 GB is the line for ONE 8 GB model, and one is all a lesson uses. The tier's second
        # model costs disk, not RAM — see print_disk_note and print_one_at_a_time.
        print("  %.1f GB RAM -> mellum2 tier (the reference path): 16 GB holds one 8 GB model,"
              % gigabytes)
        print("    and the two lessons that need one never run at the same time.")
        return MELLUM2
    if total >= LOCAL_MIN_RAM_BYTES:
        print("  %.1f GB RAM, under 16 GB -> qwen tier (the small fallback)." % gigabytes)
        if plat.wsl:
            _wsl_memory_hint()
        return QWEN

    print("  %.1f GB RAM -> no local model. The IDE, the Ollama server and a 16,384-token"
          % gigabytes)
    print("    context do not fit under %d GB, so this machine gets the browser path."
          % (LOCAL_MIN_RAM_BYTES // 1024 ** 3))
    if plat.wsl:
        _wsl_memory_hint()
    return COLAB


# --------------------------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------------------------


@dataclass
class Step:
    """One unit of setup: probe, explain, run, re-probe.

    Same shape as doctor.py's `Check` on purpose, so the two tools read alike. The difference is
    what happens after a failure: `doctor` runs every check so one failure never hides another;
    `setup` stops, because later steps depend on earlier ones having succeeded.
    """

    name: str
    probe: Callable[[], Tuple[bool, str]]
    explain: str
    preview: str
    apply: Callable[[], Tuple[bool, str]]


def _blocked(reason: str) -> Callable[[], Tuple[bool, str]]:
    return lambda: (False, reason)


# --- Python ---------------------------------------------------------------------------------


def python_step(plat: Platform, opts: Options) -> Step:
    """A check, not an install.

    Installing Python is `setup.sh` / `setup.ps1`'s job, and it has to be: a Python script
    cannot install the interpreter it needs in order to run, so a machine with no Python at all
    could never be bootstrapped from here. Those scripts guarantee a 3.12 before they hand over,
    which is why this is only a safety net for someone running setup.py directly.
    """
    running = "%d.%d.%d" % sys.version_info[:3]
    if plat.powershell:
        bootstrap = "powershell -ExecutionPolicy Bypass -File setup.ps1"
    else:
        bootstrap = "./setup.sh"

    def probe() -> Tuple[bool, str]:
        if sys.version_info[:2] >= MIN_PYTHON:
            return True, running + " at " + sys.executable
        needed = "%d.%d" % MIN_PYTHON
        return False, "%s — the course needs %s or newer" % (running, needed)

    def apply() -> Tuple[bool, str]:
        return False, (
            "this script cannot install its own interpreter. Run `%s` instead — it installs "
            "Python %s (with your package manager, or uv where the distro has no %s package) "
            "and then runs this script under it." % (bootstrap, PYTHON_SERIES, PYTHON_SERIES)
        )

    return Step("python", probe, "Python %s or newer is needed." % PYTHON_SERIES,
                bootstrap, apply)


# --- Ollama binary ---------------------------------------------------------------------------


# What `https://ollama.com/install.sh` needs before it can do anything. curl fetches it, and
# zstd extracts the payload — the installer has required zstd since it switched its archive
# format, and on a machine without it the whole thing stops with an ERROR that has nothing to do
# with Ollama. A fresh Debian (and so ChromeOS's Linux container) has neither by default.
LINUX_INSTALLER_TOOLS = ("curl", "zstd")


def _install_tools_command(
    plat: Platform, tools: Sequence[str]
) -> Optional[Tuple[Sequence[str] | str, bool]]:
    """(command, needs_a_shell) to install small prerequisites, or None if we cannot.

    apt gets an `update` glued on with `&&`: package lists in a long-lived image or a machine
    that has not updated in months are stale, and `apt-get install` then fails with "Unable to
    locate package" for something that does exist. One shell command keeps it to one prompt.
    """
    manager = plat.package_manager
    prefix = " ".join(plat.privilege_prefix)
    prefix = (prefix + " ") if prefix else ""
    listed = " ".join(tools)
    if manager == "apt":
        return ("%sapt-get update && %sapt-get install -y %s" % (prefix, prefix, listed), True)
    if manager == "dnf":
        return (list(plat.privilege_prefix) + ["dnf", "install", "-y"] + list(tools), False)
    if manager == "pacman":
        return (
            list(plat.privilege_prefix) + ["pacman", "-S", "--needed", "--noconfirm"]
            + list(tools),
            False,
        )
    return None


def _ensure_tools(
    plat: Platform, tools: Sequence[str], opts: Options, why: str
) -> Tuple[bool, str]:
    """Install whichever of `tools` is missing, or explain why we cannot.

    Both remote installers this script runs are shell scripts with their own dependencies, and
    both fail confusingly without them: the Ollama script stops with `ERROR: This version
    requires zstd`, and `curl … | sh` with no curl reports the *shell's* exit status — zero —
    so it looks like it worked and only the follow-up check notices it did not.
    """
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if not missing:
        return True, "nothing needed"

    print("     %s needs %s, which this machine does not have yet:"
          % (why, " and ".join(missing)))
    prerequisite = _install_tools_command(plat, missing)
    if prerequisite is None:
        return False, (
            "install %s with your package manager first, then run this script again"
            % " and ".join(missing)
        )
    command, shell = prerequisite
    ok, detail = run_command(command, opts, shell=shell)
    if not ok:
        return False, detail
    still_missing = [tool for tool in missing if shutil.which(tool) is None]
    if still_missing:
        return False, "%s is still not on PATH after installing it" % " and ".join(still_missing)
    return True, "installed " + " and ".join(missing)


def ollama_binary_step(plat: Platform, opts: Options) -> Step:
    def probe() -> Tuple[bool, str]:
        found = shutil.which("ollama")
        if found:
            return True, found
        return False, "not installed"

    manager = plat.package_manager
    shell = False
    command = None  # type: Optional[Sequence[str] | str]

    if plat.system == "macos" and manager == "brew":
        # The `ollama` *formula* (CLI + server), not the `ollama-app` cask: the formula is what
        # `brew services` can supervise, and the two conflict, so we never install both.
        command = ["brew", "install", "ollama"]
    elif plat.system == "linux":
        # The only command in this script that runs through a shell, and the only one that
        # executes something fetched from the network. It is Ollama's own documented install
        # path and there is no published checksum to pin it to, so the mitigation is that you
        # see the command and agree to it before it runs. Prefer the tarball from
        # ollama.com/download if you would rather not pipe a script into sh.
        command = "curl -fsSL https://ollama.com/install.sh | sh"
        shell = True
    elif plat.system == "windows" and manager == "winget":
        command = [
            "winget", "install", "-e", "--id", "Ollama.Ollama",
            "--accept-package-agreements", "--accept-source-agreements",
        ]

    if command is None:
        preview = "download Ollama from %s" % OLLAMA_DOWNLOAD
        apply = _blocked(
            "no package manager to install Ollama with. Get the installer from %s, then re-run "
            "this script." % OLLAMA_DOWNLOAD
        )
    else:
        preview = command if isinstance(command, str) else " ".join(command)

        def apply() -> Tuple[bool, str]:
            # The Linux installer is a shell script with its own dependencies. Missing zstd
            # makes it fail with an error about zstd, which reads like a broken download.
            if plat.system == "linux":
                ok, detail = _ensure_tools(
                    plat, LINUX_INSTALLER_TOOLS, opts, "the Ollama install script"
                )
                if not ok:
                    return False, detail
            return run_command(command, opts, shell=shell)

    return Step("ollama installed", probe, "Install the Ollama CLI and server.", preview, apply)


# --- Ollama server ---------------------------------------------------------------------------


def _start_server_command(plat: Platform) -> Tuple[Optional[Sequence[str]], bool]:
    """(command, is_blocking). A blocking command needs backgrounding; a launcher does not."""
    if plat.system == "macos":
        if plat.has_ollama_app:
            return ["open", "-a", "Ollama"], False
        if plat.package_manager == "brew":
            # The formula ships a launchd service: starts now and again at login.
            return ["brew", "services", "start", "ollama"], False
        return ["ollama", "serve"], True
    if plat.system == "linux":
        if plat.has_systemd:
            return list(plat.privilege_prefix) + ["systemctl", "start", "ollama"], False
        # WSL2 without systemd, or a hand-unpacked tarball: run the server ourselves.
        return ["ollama", "serve"], True
    if plat.system == "windows":
        # The installer registers a background app, but it is not running right after a
        # scripted install, and `ollama serve` is the one command that works either way.
        return ["ollama", "serve"], True
    return None, False


def _wait_for_server(base_url: str, timeout_s: float = SERVER_TIMEOUT_S) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if ollama_api(base_url, "/api/tags") is not None:
            return True
        time.sleep(1.0)
    return False


def server_step(plat: Platform, opts: Options) -> Step:
    def probe() -> Tuple[bool, str]:
        if ollama_api(opts.base_url, "/api/tags") is not None:
            return True, "answering at " + opts.base_url
        return False, "not answering at " + opts.base_url

    command, blocking = _start_server_command(plat)
    if command is None:
        preview = "start Ollama by hand"
        apply = _blocked("no way to start the Ollama server on this platform — start it by hand")
    else:
        preview = " ".join(command) + (" &" if blocking else "")

        def apply() -> Tuple[bool, str]:
            # SERVER_ENV only reaches a server this script starts. Where the server is already
            # running, or is started by launchd/systemd, the cap has to be set there instead —
            # which is what the note printed after READY is for.
            ok, detail = run_command(command, opts, background=blocking, env=SERVER_ENV)
            if not ok:
                return False, detail
            print("     waiting for the server to answer...")
            if not _wait_for_server(opts.base_url):
                return False, (
                    "started, but nothing answered at %s within %ds — check for another Ollama "
                    "already running on that port" % (opts.base_url, int(SERVER_TIMEOUT_S))
                )
            return True, "answering"

    return Step("ollama server", probe, "Start the Ollama server.", preview, apply)


# --- Models ----------------------------------------------------------------------------------


def base_model_step(model: Model, opts: Options) -> Step:
    def probe() -> Tuple[bool, str]:
        names = installed_models(opts.base_url)
        if names is None:
            return False, "the server is not answering, so its models cannot be listed"
        if has_model(names, model.base):
            return True, model.base
        return False, model.base + " has not been pulled"

    command = ["ollama", "pull", model.base]

    def apply() -> Tuple[bool, str]:
        return run_command(command, opts)

    return Step(
        "base model (%s)" % model.kind,
        probe,
        "Pull the %s base model (%s), which %s run on. Do this before the session, not "
        "during it." % (model.kind, model.download, model.editions),
        " ".join(command),
        apply,
    )


def modelfile_text(base: str) -> str:
    """A Modelfile whose only job is to carry the context window."""
    return (
        "# Generated by setup.py. The context window has to be baked into the model: Ollama's\n"
        "# OpenAI-compatible /v1 endpoint drops per-request `options`, so num_ctx set there is\n"
        "# silently ignored and long runs lose their own history mid-task.\n"
        "FROM %s\n"
        "\n"
        "PARAMETER num_ctx %d\n"
    ) % (base, MIN_CONTEXT_LENGTH)


QWEN_MODELFILE_TEXT = modelfile_text(QWEN_BASE_MODEL)


def derived_model_step(model: Model, opts: Options) -> Step:
    """Not optional. `num_ctx` cannot be set over Ollama's /v1 endpoint, so running the base
    model directly gives a 4,096-token context and an agent that silently forgets mid-run."""

    def probe() -> Tuple[bool, str]:
        names = installed_models(opts.base_url)
        if names is None:
            return False, "the server is not answering, so its models cannot be listed"
        if has_model(names, model.derived):
            return True, "%s (num_ctx %d)" % (model.derived, MIN_CONTEXT_LENGTH)
        return False, model.derived + " has not been created"

    command = ["ollama", "create", model.derived, "-f", model.modelfile.name]

    def apply() -> Tuple[bool, str]:
        if model.generate_modelfile:
            # In the repo, not /tmp: native Windows has no /tmp, and one code path beats two.
            print("     writing %s" % model.modelfile.name)
            try:
                model.modelfile.write_text(modelfile_text(model.base), encoding="utf-8")
            except OSError as error:
                return False, "could not write %s: %s" % (model.modelfile.name, error)
        elif not model.modelfile.is_file():
            return False, "%s is missing from the course root" % model.modelfile.name
        return run_command(command, opts)

    return Step(
        "derived model (%s)" % model.kind,
        probe,
        "Derive the %s model that carries num_ctx %d — without it the agent's history is "
        "silently truncated." % (model.kind, MIN_CONTEXT_LENGTH),
        " ".join(command),
        apply,
    )


# --- The model choice ------------------------------------------------------------------------

ENV_FILE_HEADER = (
    "# Written by setup.py. `run.py` reads this file and passes it to the agent, so the model\n"
    "# choice survives a new terminal. A real environment variable wins over this file.\n"
    "#\n"
    "# One line per edition that is not on its default model: MELLUM_MODEL for the agentfix and\n"
    "# agentlang lessons, AGENTGRAPH_MODEL for the thinking one.\n"
)

SHELL_BLOCK_START = "# >>> agentfix setup >>>"
SHELL_BLOCK_END = "# <<< agentfix setup <<<"


def _shell_profile() -> Optional[Path]:
    """The profile the learner's login shell actually reads."""
    shell = os.environ.get("SHELL", "")
    home = Path.home()
    if shell.endswith("zsh"):
        return home / ".zshrc"
    if shell.endswith("bash"):
        # .bashrc on Linux; macOS bash logins read .bash_profile, which usually sources it.
        return home / ".bashrc"
    if shell.endswith("fish"):
        return home / ".config" / "fish" / "config.fish"
    if shell:
        return home / ".profile"
    return None


def _strip_managed_block(text: str) -> str:
    lines = text.splitlines(True)
    kept = []  # type: List[str]
    inside = False
    for line in lines:
        if line.strip() == SHELL_BLOCK_START:
            inside = True
            continue
        if line.strip() == SHELL_BLOCK_END:
            inside = False
            continue
        if not inside:
            kept.append(line)
    return "".join(kept)


def _assignment(key: str, value: str, profile: Path) -> str:
    if profile.name == "config.fish":
        return "set -gx %s %s" % (key, value)
    return "export %s=%s" % (key, value)


def _managed_block(env: Dict[str, str], profile: Path) -> str:
    """One marked block, however many variables the tier needs, so removing it stays one step."""
    lines = [_assignment(key, env[key], profile) for key in ENV_KEYS if key in env]
    return "%s\n%s\n%s\n" % (SHELL_BLOCK_START, "\n".join(lines), SHELL_BLOCK_END)


def env_file_assignments() -> Dict[str, str]:
    """`.agentfix.env` as a dict, parsed the way run.py parses it. Empty if it is not there."""
    found = {}  # type: Dict[str, str]
    if not ENV_FILE.is_file():
        return found
    try:
        text = ENV_FILE.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return found
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        found[key.strip()] = value.strip()
    return found


def write_env_file(env: Dict[str, str]) -> None:
    """Rewrite the model lines in `.agentfix.env`, leaving anything else in it alone.

    Not a plain overwrite: `run.py` documents AGENT_EDITION as something a learner can set in
    this file to fix which edition their commands run against, and re-running setup — now
    routine, since it installs two models — would silently delete it. So only the keys this
    script owns are rewritten, and the file is removed only when nothing at all is left in it.
    """
    others = dict(
        (key, value) for key, value in env_file_assignments().items() if key not in ENV_KEYS
    )
    if not env and not others:
        if ENV_FILE.exists():
            ENV_FILE.unlink()
        return
    lines = "".join("%s=%s\n" % (key, others[key]) for key in sorted(others))
    lines += "".join("%s=%s\n" % (key, env[key]) for key in ENV_KEYS if key in env)
    ENV_FILE.write_text(ENV_FILE_HEADER + lines, encoding="utf-8")


def _windows_env_commands(
    env: Dict[str, str], remove: Sequence[str] = ()
) -> List[List[str]]:
    """`setx` for the variables this tier overrides, `reg delete` for stale ones to clear.

    Removal is `reg delete` rather than `setx VAR ""`, which leaves an empty-but-present
    variable behind — and an empty MELLUM_MODEL reads as a model named "". Nothing is emitted
    for a variable that is neither wanted nor set: prompting to delete something that was never
    there teaches the learner that these prompts can be ignored.
    """
    commands = []  # type: List[List[str]]
    for key in ENV_KEYS:
        if key in env:
            commands.append(["setx", key, env[key]])
        elif key in remove:
            commands.append(["reg", "delete", "HKCU\\Environment", "/F", "/V", key])
    return commands


def model_choice_step(tier: Tier, plat: Platform, opts: Options) -> Step:
    """Persist the tier's models in two places, because they solve different halves.

    `.agentfix.env` is what makes the workshop work: `run.py` reads it, so every learner command
    picks up the right model with no shell setup at all. The environment variables are what make
    it work *outside* run.py — a bare `python -m agentgraph.cli`, a debugger, the IDE's run
    configuration. A child process cannot set its parent shell's environment, so they have to be
    persisted at the user level (`setx` on Windows, the shell profile on POSIX) and only apply to
    terminals opened afterwards. Hence both, plus the lines to paste right now.

    Two variables rather than one, because the two halves of the tier are different kinds of
    model: MELLUM_MODEL names the coding model the first two editions use, AGENTGRAPH_MODEL the
    thinking one the third needs. On the mellum2 tier both derived names already are those
    editions' defaults, so there is nothing to write — and a stale override from an earlier
    `--tier qwen` run has to be *removed*, which is the case this step gets wrong most easily.
    """
    env = tier.env
    profile = None if plat.powershell else _shell_profile()
    variables = " and ".join(ENV_KEYS)
    if plat.powershell:
        target = " and in the user environment"
    elif profile is not None:
        target = " and in %s" % profile
    else:
        target = " (no shell profile found to add %s to)" % variables

    def _file_matches() -> bool:
        """Only the keys this script owns. A file left holding someone else's AGENT_EDITION is
        still correct for this tier — see write_env_file."""
        written = env_file_assignments()
        return dict(
            (key, value) for key, value in written.items() if key in ENV_KEYS
        ) == env

    # States the re-probe has to treat as settled even though the environment it can see does
    # not contain the lines: there was no profile to write to, the learner declined an
    # ADDITION, and — on Windows — `setx` succeeded but only for future terminals. None of
    # those is a failure: `.agentfix.env` already carries the models, and setup prints the
    # export lines either way. Without this the step reports "ran, but the check still fails"
    # and the whole run says NOT DONE after having done everything correctly. Found by the
    # Linux clean room, where the container has no SHELL set, and on Windows by the qwen tier.
    #
    # A declined REMOVAL is the exception, and must never land here. There the shell would keep
    # exporting the previous tier's models while `.agentfix.env` names none, so every new
    # terminal silently contradicts the tier just chosen — the failure this whole step exists
    # to prevent. Those paths return a failure with the manual fix in it instead.
    settled = []  # type: List[str]

    def _shell_matches() -> bool:
        if not opts.shell_env or settled:
            return True
        if plat.powershell:
            # Reading HKCU here would cost a subprocess on every run for a value we would set
            # again anyway; the current process environment is the useful signal.
            return all(
                os.environ.get(key, "") == env.get(key, "") for key in ENV_KEYS
            )
        if profile is None or not profile.is_file():
            return not env
        text = profile.read_text(encoding="utf-8")
        if not env:
            return SHELL_BLOCK_START not in text
        return SHELL_BLOCK_START in text and all(value in text for value in env.values())

    def probe() -> Tuple[bool, str]:
        if not (_file_matches() and _shell_matches()):
            return False, "not set for the %s tier yet" % tier.name
        if not env:
            return True, "both editions' default models need no override"
        return True, ", ".join("%s=%s" % (key, env[key]) for key in ENV_KEYS if key in env)

    if not env:
        preview = "remove .agentfix.env and any %s override" % variables
    else:
        written = " ".join("%s=%s" % (key, env[key]) for key in ENV_KEYS if key in env)
        preview = "write .agentfix.env (%s)%s" % (written, target)

    def apply() -> Tuple[bool, str]:
        try:
            write_env_file(env)
        except OSError as error:
            return False, "could not write .agentfix.env: %s" % error
        print("     %s .agentfix.env" % ("removed" if not env else "wrote"))

        if not opts.shell_env:
            return True, "skipped the environment variables (--no-shell-env)"

        if plat.powershell:
            return _apply_windows()
        return _apply_profile()

    def _apply_windows() -> Tuple[bool, str]:
        # Which stale variables there are to clear, read from this process's environment: a
        # `setx` from an earlier run lands in HKCU\Environment, which every shell started after
        # it inherits — including the one that launched us. Nothing to clear means no prompt.
        stale = [key for key in ENV_KEYS if key not in env and os.environ.get(key)]
        commands = _windows_env_commands(env, stale)
        if not commands:
            settled.append("nothing to set or remove")
            return True, "no override to set, and none left over to remove"

        print("     also %s the model variables for future terminals:"
              % ("setting" if env else "removing"))
        for command in commands:
            ok, detail = run_command(command, opts, quiet=True)
            if ok:
                continue
            if detail == DECLINED:
                # Declining leaves the machine in exactly the state this step exists to fix,
                # whichever half was declined, so it cannot be reported as done.
                return False, (
                    "declined. %s is not recorded for new terminals; run this again, or set "
                    "the variables by hand with the lines setup prints." % variables
                )
            if command[0] == "setx":
                return False, detail
            # A `reg delete` that runs and fails means the variable was not in HKCU after all
            # — it came from this session only. Nothing to clean up, so nothing to report.
            print("     (%s was not set for new terminals, so there was nothing to remove)"
                  % command[-1])
        # `setx` writes HKCU\Environment, which the current process never sees: a child cannot
        # change its parent's environment, and this process is the one re-probing. Reading
        # os.environ back would therefore always miss, turning a correct run into "ran, but the
        # check still fails".
        settled.append("setx")
        return True, "set for new terminals" if env else "removed for new terminals"

    def _apply_profile() -> Tuple[bool, str]:
        if profile is None:
            settled.append("no profile")
            return True, "no shell profile to update — use the export lines below"
        try:
            existing = profile.read_text(encoding="utf-8") if profile.is_file() else ""
        except (OSError, ValueError) as error:
            return False, "could not read %s: %s" % (profile, error)

        stale = SHELL_BLOCK_START in existing
        if not env and not stale:
            settled.append("nothing to remove")
            return True, "no override in %s to remove" % profile.name

        print("     also %s %s in %s (for new terminals):"
              % ("setting" if env else "removing", variables, profile))
        if not confirm(opts, "edit %s?" % profile.name):
            if not env:
                return False, (
                    "%s still exports the previous tier's models, and .agentfix.env no longer "
                    "does — so a new terminal would disagree with this one. Delete the `%s` "
                    "block from it, or run this again and accept the edit."
                    % (profile, SHELL_BLOCK_START)
                )
            settled.append("declined")
            return True, "left %s alone — use the export lines below" % profile.name

        try:
            updated = _strip_managed_block(existing)
            if env:
                if updated and not updated.endswith("\n"):
                    updated += "\n"
                updated += _managed_block(env, profile)
            profile.parent.mkdir(parents=True, exist_ok=True)
            profile.write_text(updated, encoding="utf-8")
        except OSError as error:
            return False, "could not edit %s: %s" % (profile, error)
        return True, "updated %s" % profile

    return Step("model choice", probe, "Make the tier's models the ones the workshop uses.",
                preview, apply)


def plan_steps(tier: Tier, plat: Platform, opts: Options) -> List[Step]:
    """The ordered plan. A pure function of (tier, platform, options) — which is what lets the
    tests assert the whole platform matrix with none of the tools installed."""
    steps = [
        python_step(plat, opts),
        ollama_binary_step(plat, opts),
        server_step(plat, opts),
    ]
    # Pull-then-derive, per model, in the tier's own order: the instruct pair first, because
    # that is the edition the course opens with, so a run interrupted halfway still leaves a
    # machine that can start lesson 2.
    for model in tier.models:
        steps.append(base_model_step(model, opts))
        steps.append(derived_model_step(model, opts))
    steps.append(model_choice_step(tier, plat, opts))
    return steps


# --------------------------------------------------------------------------------------------
# Driving the plan
# --------------------------------------------------------------------------------------------


def python_command() -> str:
    """How to spell "run Python" on this machine.

    On a fresh Debian — which is what ChromeOS's Linux container is — there is no `python`, only
    `python3`. Printing the wrong one is the first command a learner runs and the first one that
    fails, so every instruction this script prints goes through here.
    """
    return "python" if os.name == "nt" else "python3"


def quote_path(path: str) -> str:
    """A path the learner can paste into the shell they are standing in.

    `shlex.quote` is POSIX-only — its single quotes are not quoting in cmd.exe — and Windows is
    where the paths with spaces in them live, so that platform gets its own rule.
    """
    if os.name == "nt":
        return '"%s"' % path if " " in path else path
    return shlex.quote(path)


def python_invocation() -> str:
    """How to spell "the interpreter this script is running under", ready to paste.

    Not simply `python3`. After the uv fallback the interpreter that matters is the one uv just
    installed, which may exist only as a full path or as `python3.12`, while `python3` still
    points at the system 3.11 that could not run the course. Printing the short name there would
    hand the learner a command that walks straight back into the problem setup just solved.

    So: the short name only when it demonstrably resolves to *this* interpreter, and the full
    path otherwise. The comparison is deliberately not `realpath`-based — a virtual environment's
    `python3` resolves to the system interpreter it was built from, which would make the two look
    identical when the difference (site-packages) is the entire point of the venv.
    """
    short = python_command()
    found = shutil.which(short)
    if found and found == sys.executable:
        return short
    return quote_path(sys.executable)


def print_colab() -> None:
    print("Nothing to install on this machine — the Colab tier runs in a browser.")
    print("Open %s in Google Colab and run it top to bottom." % NOTEBOOK)
    print("Ollama and both models run in the Colab runtime, and so do the three agents.")
    print("The exercises stay here, in the IDE: they are graded against a scripted fake")
    print("model, so they need no model at all.")


def export_lines(tier: Tier, plat: Platform) -> List[str]:
    """The lines that give THIS terminal the tier's models, one per override it needs."""
    lines = []  # type: List[str]
    env = tier.env
    for key in ENV_KEYS:
        if key not in env:
            continue
        if plat.powershell:
            lines.append("$env:%s = '%s'" % (key, env[key]))
        else:
            lines.append("export %s=%s" % (key, env[key]))
    return lines


def ollama_models_dir() -> Path:
    """Where Ollama keeps its blobs — the filesystem the pulls actually consume."""
    override = os.environ.get("OLLAMA_MODELS")
    if override:
        return Path(override)
    return Path.home() / ".ollama" / "models"


def free_bytes(path: Path) -> Optional[int]:
    """Free space on the filesystem holding `path`, or None if we cannot tell.

    Climbs to the first parent that exists: on a machine where Ollama has never run, the models
    directory itself does not exist yet, and the question is about the filesystem anyway.
    """
    candidates = [path]
    candidates.extend(path.parents)
    for candidate in candidates:
        try:
            return shutil.disk_usage(str(candidate)).free
        except OSError:
            continue
    return None


def print_disk_note(tier: Tier) -> None:
    """A warning, not a step.

    Deliberately not a Step: a Step that fails stops the run, and this is an estimate — the
    sizes are what the models measured, not what this tag will download today. Being told
    "12 GB free, this needs about 18" is useful; being blocked by a number that is 3% out and
    cannot be fixed from here is not.
    """
    needed = tier.disk_bytes
    where = ollama_models_dir()
    free = free_bytes(where)
    gigabytes = needed / 1024 ** 3
    if free is None:
        print("  disk: %d models, about %.0f GB in %s — check you have room."
              % (len(tier.models), gigabytes, where))
        return
    print("  disk: %d models, about %.0f GB in %s (%.1f GB free)."
          % (len(tier.models), gigabytes, where, free / 1024 ** 3))
    if free < needed:
        print("  ! that is less than the pulls need, and Ollama fails part-way through a pull")
        print("    rather than up front. Free some space, or use --tier qwen (about %.0f GB)."
              % (QWEN.disk_bytes / 1024 ** 3))


def print_one_at_a_time(tier: Tier, plat: Platform) -> None:
    """Why two 8 GB models do not mean a 32 GB machine — and the one case where they bite.

    The editions are separate lessons, so only one model is ever needed at once. Ollama keeps
    the last one loaded for five minutes though, so switching lessons inside that window can put
    both in memory at the same time. That is the difference between a 16 GB laptop that works
    and one that swaps, and it is fixable in one command.
    """
    if tier.largest_model_bytes < 4 * 1024 ** 3:
        return  # the small tier: both models together are smaller than one context window's fuss
    print("\nOne model at a time. The lessons use these one after the other, never together:")
    for model in tier.models:
        print("  %-28s %s" % (model.derived, model.editions))
    print("so this machine only has to hold the bigger of the two — the 16 GB line is about one")
    print("model, not the pair. Ollama does keep the last one loaded for five minutes, so when")
    print("you move on to the next lesson:")
    print("  ollama stop %s" % tier.models[0].derived)
    print("Or cap it once, in the server's own environment: OLLAMA_MAX_LOADED_MODELS=1")
    if plat.system == "macos":
        print("(setup does that for a server it starts itself; the menu-bar app needs")
        print(" `launchctl setenv OLLAMA_MAX_LOADED_MODELS 1` and a restart of Ollama.)")


def execute(steps: Sequence[Step], opts: Options) -> int:
    for step in steps:
        ok, detail = step.probe()
        if ok:
            print("[ok]   %s: %s" % (step.name, detail))
            continue

        print("[todo] %s: %s" % (step.name, detail))
        print("       %s" % step.explain)
        # The one gate for --dry-run. Deliberately here and nowhere else: a second check inside
        # each apply() would be a branch no run ever takes, which is a branch no test can trust.
        if opts.dry_run:
            print("       would run: %s" % step.preview)
            continue

        ok, detail = step.apply()
        if not ok:
            print("[stop] %s: %s" % (step.name, detail))
            print("\nNOT DONE — fix the line above and run this script again.")
            return 1

        ok, detail = step.probe()
        if not ok:
            print("[stop] %s: ran, but the check still fails: %s" % (step.name, detail))
            print("\nNOT DONE — fix the line above and run this script again.")
            return 1
        print("[ok]   %s: %s" % (step.name, detail))
    return 0


def parse_args(argv: Sequence[str]) -> Options:
    parser = argparse.ArgumentParser(
        description="Set up the workshop's model stack. Same command on every OS.",
    )
    parser.add_argument(
        "--tier",
        choices=("auto", "mellum2", "qwen", "colab"),
        default="auto",
        help="auto (default) reads this machine's RAM and picks; colab prints the notebook "
             "pointer and exits.",
    )
    parser.add_argument("--yes", action="store_true", help="assume yes at every prompt")
    parser.add_argument(
        "--bootstrapped",
        action="store_true",
        help=argparse.SUPPRESS,  # set by setup.sh / setup.ps1, not something to type by hand
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan and change nothing"
    )
    parser.add_argument(
        "--no-shell-env",
        action="store_true",
        help="do not set MELLUM_MODEL for future terminals (only write .agentfix.env)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("MELLUM_BASE_URL", "http://localhost:11434/v1"),
        help="where Ollama is (default http://localhost:11434/v1)",
    )
    parsed = parser.parse_args(list(argv))
    return Options(
        tier=parsed.tier,
        bootstrapped=parsed.bootstrapped,
        yes=parsed.yes,
        dry_run=parsed.dry_run,
        shell_env=not parsed.no_shell_env,
        # Ollama's native API is the parent of /v1: /api/tags and /api/ps live there.
        base_url=parsed.base_url.rsplit("/v1", 1)[0].rstrip("/"),
    )


def main(argv: Sequence[str]) -> int:
    opts = parse_args(argv)

    if opts.tier == "colab":
        print_colab()
        return 0

    plat = detect_platform()
    where = plat.system + (" (WSL2)" if plat.wsl else "")
    print("agentfix setup")
    print("  machine: %s, package manager: %s" % (where, plat.package_manager or "none found"))
    if opts.dry_run:
        print("  dry run: nothing will be changed")

    tier = resolve_tier(opts, plat)
    if tier is None:
        print("\nNo tier chosen. Re-run with --tier mellum2, --tier qwen or --tier colab.")
        return 1
    if not tier.local:
        print()
        print_colab()
        print("\nIf you want to try a local model on this machine anyway:")
        print("  %s setup.py --tier qwen" % python_invocation())
        return 0
    print("  tier: %s -> models %s" % (tier.name, tier.derived_names))
    print_disk_note(tier)
    print()

    if plat.system != "windows" and not plat.root and not plat.has_sudo:
        print("  note: not running as root and there is no `sudo` here, so a package install")
        print("        may fail with a permission error. Run this as root if it does.\n")

    if plat.system == "windows" and not plat.wsl:
        print("  note: native Windows works for setup and the exercises, but the sandbox that")
        print("        runs the agent's tests is untested there. WSL2 is the safer path.\n")

    status = execute(plan_steps(tier, plat, opts), opts)
    if status != 0:
        return status

    print("\nREADY" + (" (dry run — nothing was changed)" if opts.dry_run else ""))
    lines = export_lines(tier, plat)
    if lines and not opts.dry_run:
        print("\nThis terminal does not have the model variables yet. For this session:")
        for line in lines:
            print("  %s" % line)
        print("(`python run.py ...` does not need them — it reads .agentfix.env.)")
    if not opts.dry_run:
        print_one_at_a_time(tier, plat)
    print("\nnext: %s run.py doctor              # the coding model" % python_invocation())
    print("      %s run.py agentgraph doctor   # the thinking model" % python_invocation())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\ninterrupted — nothing further was changed")
        sys.exit(130)
