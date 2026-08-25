#!/usr/bin/env python3
"""The workshop's model setup: the tier, the models, the context window, the model choice.

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
from typing import Callable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent

# These mirror agentfix/config.py. Duplicated rather than imported, deliberately: setup.py has to
# run before the framework lesson's working directory exists, and the course root contains a
# *section* directory also called `agentfix/`, which Python would import instead of the package.
# If you change a model name in config.py, change it here too.
MELLUM_BASE_MODEL = "hf.co/JetBrains/Mellum2-12B-A2.5B-Instruct-GGUF-Q4_K_M"
QWEN_BASE_MODEL = "qwen2.5-coder:1.5b"
MIN_CONTEXT_LENGTH = 16384

MIN_PYTHON = (3, 12)
PYTHON_SERIES = "3.12"

# The tier boundaries. 16 GiB is the line the workshop's own RAM check uses: an 8 GB model plus
# the OS does not fit comfortably below it. The 8 GiB floor is newer and comes from a fresh
# Chromebook with 3.4 GB, which the RAM check happily routed to the small model — where the IDE,
# the Ollama server and a 16k context still do not fit. Below the floor the honest answer is that
# no local model belongs on this machine.
MELLUM2_MIN_RAM_BYTES = 16 * 1024 ** 3
LOCAL_MIN_RAM_BYTES = 8 * 1024 ** 3

ENV_FILE = ROOT / ".agentfix.env"
MELLUM_MODELFILE = ROOT / "Modelfile"
QWEN_MODELFILE = ROOT / "Modelfile.agentfix-qwen"
NOTEBOOK = "notebooks/agentfix.ipynb"

PYTHON_ORG = "https://www.python.org/downloads/"
OLLAMA_DOWNLOAD = "https://ollama.com/download"

SERVER_TIMEOUT_S = 90.0


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
class Tier:
    name: str
    base_model: str
    derived_model: str
    modelfile: Path
    # Only the fallback tier needs a generated Modelfile; mellum2 uses the one in the repo.
    generate_modelfile: bool
    # MELLUM_MODEL value the rest of the workshop needs, or None when the derived model already
    # is config.DEFAULT_MODEL and no override is required.
    env_model: Optional[str]

    @property
    def local(self) -> bool:
        """Is there anything to install on this machine? False only for the colab verdict."""
        return bool(self.base_model)


# Not a model at all: the verdict "run this in a browser instead". It carries no models, so it
# never reaches plan_steps.
COLAB = Tier(
    name="colab",
    base_model="",
    derived_model="",
    modelfile=Path(NOTEBOOK),
    generate_modelfile=False,
    env_model=None,
)

MELLUM2 = Tier(
    name="mellum2",
    base_model=MELLUM_BASE_MODEL,
    derived_model="agentfix-mellum2",
    modelfile=MELLUM_MODELFILE,
    generate_modelfile=False,
    env_model=None,
)

QWEN = Tier(
    name="qwen",
    base_model=QWEN_BASE_MODEL,
    derived_model="agentfix-qwen",
    modelfile=QWEN_MODELFILE,
    generate_modelfile=True,
    env_model="agentfix-qwen",
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


def _spawn_background(command: Sequence[str]) -> Tuple[bool, str]:
    """Start a blocking server command detached, so it outlives this script."""
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
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
) -> Tuple[bool, str]:
    """The single execution seam. Shows the command, asks, runs it, reports a verdict.

    Every command this script runs goes through here, including the backgrounded server — which
    is what makes --dry-run trustworthy and lets the tests assert that nothing ran. `background`
    is for a command that never returns (`ollama serve`): it is detached instead of waited on.
    """
    printable = command if isinstance(command, str) else " ".join(command)
    print("     $ %s%s" % (printable, "   (in the background)" if background else ""))
    if not confirm(opts, "run it?"):
        return False, "declined — nothing was changed"
    if background:
        return _spawn_background(command if isinstance(command, list) else list(command))
    try:
        completed = subprocess.run(
            command,
            shell=shell,
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL if quiet else None,
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
    print("    1) mellum2 — the reference path, needs 16 GB RAM and an 8 GB download")
    print("    2) qwen    — the small fallback, ~1 GB")
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
        print("  %.1f GB RAM -> mellum2 tier (the reference path)." % gigabytes)
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
            ok, detail = run_command(command, opts, background=blocking)
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


def base_model_step(tier: Tier, opts: Options) -> Step:
    def probe() -> Tuple[bool, str]:
        names = installed_models(opts.base_url)
        if names is None:
            return False, "the server is not answering, so its models cannot be listed"
        if has_model(names, tier.base_model):
            return True, tier.base_model
        return False, tier.base_model + " has not been pulled"

    command = ["ollama", "pull", tier.base_model]
    size = "about 8 GB" if tier is MELLUM2 else "about 1 GB"

    def apply() -> Tuple[bool, str]:
        return run_command(command, opts)

    return Step(
        "base model",
        probe,
        "Pull the base model (%s). Do this before the session, not during it." % size,
        " ".join(command),
        apply,
    )


QWEN_MODELFILE_TEXT = (
    "# Generated by setup.py. The context window has to be baked into the model: Ollama's\n"
    "# OpenAI-compatible /v1 endpoint drops per-request `options`, so num_ctx set there is\n"
    "# silently ignored and long runs lose their own history mid-task.\n"
    "FROM %s\n"
    "\n"
    "PARAMETER num_ctx %d\n"
) % (QWEN_BASE_MODEL, MIN_CONTEXT_LENGTH)


def derived_model_step(tier: Tier, opts: Options) -> Step:
    """Not optional. `num_ctx` cannot be set over Ollama's /v1 endpoint, so running the base
    model directly gives a 4,096-token context and an agent that silently forgets mid-run."""

    def probe() -> Tuple[bool, str]:
        names = installed_models(opts.base_url)
        if names is None:
            return False, "the server is not answering, so its models cannot be listed"
        if has_model(names, tier.derived_model):
            return True, "%s (num_ctx %d)" % (tier.derived_model, MIN_CONTEXT_LENGTH)
        return False, tier.derived_model + " has not been created"

    command = ["ollama", "create", tier.derived_model, "-f", tier.modelfile.name]

    def apply() -> Tuple[bool, str]:
        if tier.generate_modelfile:
            # In the repo, not /tmp: native Windows has no /tmp, and one code path beats two.
            print("     writing %s" % tier.modelfile.name)
            try:
                tier.modelfile.write_text(QWEN_MODELFILE_TEXT, encoding="utf-8")
            except OSError as error:
                return False, "could not write %s: %s" % (tier.modelfile.name, error)
        elif not tier.modelfile.is_file():
            return False, "%s is missing from the course root" % tier.modelfile.name
        return run_command(command, opts)

    return Step(
        "derived model",
        probe,
        "Derive the model that carries num_ctx %d — without it the agent's history is "
        "silently truncated." % MIN_CONTEXT_LENGTH,
        " ".join(command),
        apply,
    )


# --- The model choice ------------------------------------------------------------------------

ENV_FILE_HEADER = (
    "# Written by setup.py. `run.py` reads this file and passes it to the agent, so the model\n"
    "# choice survives a new terminal. A real environment variable wins over this file.\n"
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


def _managed_block(model: str, profile: Path) -> str:
    if profile.name == "config.fish":
        assignment = "set -gx MELLUM_MODEL %s" % model
    else:
        assignment = "export MELLUM_MODEL=%s" % model
    return "%s\n%s\n%s\n" % (SHELL_BLOCK_START, assignment, SHELL_BLOCK_END)


def write_env_file(model: Optional[str]) -> None:
    """MELLUM_MODEL for run.py to inject, or no file at all on the default tier."""
    if model is None:
        if ENV_FILE.exists():
            ENV_FILE.unlink()
        return
    ENV_FILE.write_text("%sMELLUM_MODEL=%s\n" % (ENV_FILE_HEADER, model), encoding="utf-8")


def _windows_env_commands(model: Optional[str]) -> Sequence[str]:
    if model is None:
        # `setx VAR ""` leaves an empty-but-present variable, which reads as a model named "".
        return ["reg", "delete", "HKCU\\Environment", "/F", "/V", "MELLUM_MODEL"]
    return ["setx", "MELLUM_MODEL", model]


def model_choice_step(tier: Tier, plat: Platform, opts: Options) -> Step:
    """Persist the tier's model in two places, because they solve different halves.

    `.agentfix.env` is what makes the workshop work: `run.py` reads it, so every learner command
    picks up the right model with no shell setup at all. The environment variable is what makes
    it work *outside* run.py — a bare `python -m agentfix.cli`, a debugger, the IDE's run
    configuration. A child process cannot set its parent shell's environment, so the variable
    has to be persisted at the user level (`setx` on Windows, the shell profile on POSIX) and
    only applies to terminals opened afterwards. Hence both, plus the line to paste right now.
    """
    profile = None if plat.powershell else _shell_profile()
    if plat.powershell:
        target = " and in the user environment"
    elif profile is not None:
        target = " and in %s" % profile
    else:
        target = " (no shell profile found to add MELLUM_MODEL to)"

    def _file_matches() -> bool:
        if tier.env_model is None:
            return not ENV_FILE.exists()
        if not ENV_FILE.is_file():
            return False
        return ("MELLUM_MODEL=" + tier.env_model) in ENV_FILE.read_text(encoding="utf-8")

    # Two states the re-probe has to treat as settled even though the profile does not contain
    # the line: there was no profile to write to, and the learner said no. Neither is a failure —
    # `.agentfix.env` already carries the model, and setup prints the export line either way.
    # Without this the step reports "ran, but the check still fails" and the whole run says
    # NOT DONE after having done everything correctly. Found by the Linux clean room, where the
    # container has no SHELL set.
    settled = []  # type: List[str]

    def _shell_matches() -> bool:
        if not opts.shell_env or settled:
            return True
        if plat.powershell:
            # Reading HKCU here would cost a subprocess on every run for a value we would set
            # again anyway; the current process environment is the useful signal.
            return os.environ.get("MELLUM_MODEL", "") == (tier.env_model or "")
        if profile is None or not profile.is_file():
            return tier.env_model is None
        text = profile.read_text(encoding="utf-8")
        if tier.env_model is None:
            return SHELL_BLOCK_START not in text
        return SHELL_BLOCK_START in text and tier.env_model in text

    def probe() -> Tuple[bool, str]:
        if not (_file_matches() and _shell_matches()):
            return False, "not set for the %s tier yet" % tier.name
        if tier.env_model is None:
            return True, "the default model needs no override"
        return True, "MELLUM_MODEL=%s" % tier.env_model

    if tier.env_model is None:
        preview = "remove .agentfix.env and the MELLUM_MODEL override"
    else:
        preview = "write .agentfix.env (MELLUM_MODEL=%s)%s" % (tier.env_model, target)

    def apply() -> Tuple[bool, str]:
        try:
            write_env_file(tier.env_model)
        except OSError as error:
            return False, "could not write .agentfix.env: %s" % error
        print("     %s .agentfix.env" % ("removed" if tier.env_model is None else "wrote"))

        if not opts.shell_env:
            return True, "skipped the environment variable (--no-shell-env)"

        if plat.powershell:
            command = _windows_env_commands(tier.env_model)
            print("     also setting MELLUM_MODEL for future terminals:")
            ok, detail = run_command(command, opts, quiet=True)
            if not ok and tier.env_model is None:
                # Deleting a variable that was never set is not a failure.
                return True, "no MELLUM_MODEL to remove"
            return (ok, detail) if not ok else (True, "set for new terminals")

        if profile is None:
            settled.append("no profile")
            return True, "no shell profile to update — use the export line below"
        print("     also adding MELLUM_MODEL to %s (for new terminals):" % profile)
        if not confirm(opts, "edit %s?" % profile.name):
            settled.append("declined")
            return True, "left %s alone — use the export line below" % profile.name
        try:
            existing = profile.read_text(encoding="utf-8") if profile.is_file() else ""
            updated = _strip_managed_block(existing)
            if tier.env_model is not None:
                if updated and not updated.endswith("\n"):
                    updated += "\n"
                updated += _managed_block(tier.env_model, profile)
            profile.parent.mkdir(parents=True, exist_ok=True)
            profile.write_text(updated, encoding="utf-8")
        except OSError as error:
            return False, "could not edit %s: %s" % (profile, error)
        return True, "updated %s" % profile

    return Step("model choice", probe, "Make the tier's model the one the workshop uses.",
                preview, apply)


def plan_steps(tier: Tier, plat: Platform, opts: Options) -> List[Step]:
    """The ordered plan. A pure function of (tier, platform, options) — which is what lets the
    tests assert the whole platform matrix with none of the tools installed."""
    return [
        python_step(plat, opts),
        ollama_binary_step(plat, opts),
        server_step(plat, opts),
        base_model_step(tier, opts),
        derived_model_step(tier, opts),
        model_choice_step(tier, plat, opts),
    ]


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
    print("The model, the Ollama server and the exercises all run in the Colab runtime.")


def export_line(tier: Tier, plat: Platform) -> Optional[str]:
    if tier.env_model is None:
        return None
    if plat.powershell:
        return "$env:MELLUM_MODEL = '%s'" % tier.env_model
    return "export MELLUM_MODEL=%s" % tier.env_model


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
    print("  tier: %s -> model %s\n" % (tier.name, tier.derived_model))

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
    line = export_line(tier, plat)
    if line and not opts.dry_run:
        print("\nThis terminal does not have MELLUM_MODEL yet. For this session:")
        print("  %s" % line)
        print("(`python run.py ...` does not need it — it reads .agentfix.env.)")
    print("\nnext: %s run.py doctor" % python_invocation())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\ninterrupted — nothing further was changed")
        sys.exit(130)
