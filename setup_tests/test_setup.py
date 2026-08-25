"""Tests for setup.py — the one part of the workshop that runs before anything is installed.

None of these need Python 3.12, Ollama, or a network. Every platform is simulated by building a
`Platform` value, and every command that would change the machine goes through `run_command`,
which is stubbed to fail loudly if a test ever reaches it.

    python -m unittest discover -s setup_tests
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]


class NoExec(Exception):
    """Raised instead of replacing the test process."""


def setUpModule() -> None:
    """os.execve and the real installers are never allowed to run from a test.

    This is not paranoia: an earlier version of `test_no_package_manager_stops_with_the_download
    _link` fell through to the uv fallback, really installed a CPython, and really execve'd the
    test runner into setup.py. A suite that can do that cannot be trusted to prove anything.
    """
    original = setup.os.execve
    setup.os.execve = lambda *a, **k: (_ for _ in ()).throw(NoExec(a[0]))
    globals()["_restore_execve"] = lambda: setattr(setup.os, "execve", original)


def tearDownModule() -> None:
    globals()["_restore_execve"]()


@contextlib.contextmanager
def quiet():
    """setup.py narrates what it is doing; a passing test suite should not."""
    with contextlib.redirect_stdout(io.StringIO()) as captured:
        yield captured


def _load(name: str):
    """Import a script that lives at the repo root, by path.

    Neither setup.py nor run.py is on sys.path when the tests run, and setup.py deliberately
    must not be importable as part of a package.
    """
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before executing, because @dataclass resolves its own module out of
    # sys.modules while the class body runs.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


setup = _load("setup")
runner = _load("run")

GIB = 1024 ** 3


def platform_of(system: str, **kwargs) -> "setup.Platform":
    defaults = {"macos": "brew", "linux": "apt", "windows": "winget"}
    kwargs.setdefault("package_manager", defaults.get(system))
    return setup.Platform(system=system, **kwargs)


class TestResolveTier(unittest.TestCase):
    """RAM and flags in, tier out. The boundary is the interesting part."""

    def test_ram_table(self) -> None:
        cases = [
            (32 * GIB, "mellum2"),
            (16 * GIB, "mellum2"),  # both boundaries are inclusive
            (16 * GIB - 1, "qwen"),
            (8 * GIB, "qwen"),
            # Under the floor no local model belongs on the machine. A 3.4 GB Chromebook was
            # routed to qwen before this existed, where nothing would have fitted either.
            (8 * GIB - 1, "colab"),
            (3.4 * GIB, "colab"),
        ]
        for total, expected in cases:
            with self.subTest(total=total):
                with quiet():
                    tier = setup.resolve_tier(
                        setup.Options(), platform_of("linux", total_ram_bytes=total)
                    )
                self.assertEqual(tier.name, expected)

    def test_the_colab_verdict_carries_no_models(self) -> None:
        """`local` is what stops the colab verdict reaching plan_steps."""
        self.assertFalse(setup.COLAB.local)
        self.assertTrue(setup.MELLUM2.local)
        self.assertTrue(setup.QWEN.local)

    def test_a_machine_under_the_floor_is_told_why(self) -> None:
        with quiet() as printed:
            setup.resolve_tier(setup.Options(), platform_of("linux", total_ram_bytes=3 * GIB))
        said = printed.getvalue()
        self.assertIn("3.0 GB RAM", said)
        self.assertIn("no local model", said)

    def test_an_explicit_tier_under_the_floor_warns_but_obeys(self) -> None:
        """A flag is an instruction. It should still say the machine is too small."""
        with quiet() as printed:
            tier = setup.resolve_tier(
                setup.Options(tier="qwen"), platform_of("linux", total_ram_bytes=3 * GIB)
            )
        self.assertEqual(tier.name, "qwen")
        self.assertIn("below the 8 GB floor", printed.getvalue())

    def test_wsl2_under_the_floor_is_told_it_can_raise_the_allocation(self) -> None:
        """WSL2's number is a setting, not a spec sheet — do not send someone to Colab over it."""
        with quiet() as printed:
            setup.resolve_tier(
                setup.Options(), platform_of("linux", wsl=True, total_ram_bytes=4 * GIB)
            )
        self.assertIn(".wslconfig", printed.getvalue())

    def test_explicit_flag_beats_ram(self) -> None:
        plenty = platform_of("macos", total_ram_bytes=64 * GIB)
        with quiet():
            tier = setup.resolve_tier(setup.Options(tier="qwen"), plenty)
        self.assertEqual(tier.name, "qwen")

        tiny = platform_of("macos", total_ram_bytes=4 * GIB)
        with quiet():
            tier = setup.resolve_tier(setup.Options(tier="mellum2"), tiny)
        self.assertEqual(tier.name, "mellum2")

    def test_unreadable_ram_with_yes_takes_the_small_tier(self) -> None:
        """--yes is for unattended runs, so it must not commit anyone to an 8 GB download."""
        with quiet():
            tier = setup.resolve_tier(
                setup.Options(yes=True), platform_of("windows", total_ram_bytes=None)
            )
        self.assertEqual(tier.name, "qwen")

    def test_unreadable_ram_without_a_terminal_refuses_to_guess(self) -> None:
        original = sys.stdin
        sys.stdin = open(os.devnull, encoding="utf-8")  # not a tty
        try:
            with quiet():
                tier = setup.resolve_tier(
                    setup.Options(), platform_of("windows", total_ram_bytes=None)
                )
        finally:
            sys.stdin.close()
            sys.stdin = original
        self.assertIsNone(tier)


class TestPlanSteps(unittest.TestCase):
    """The platform matrix: one plan per OS, asserted by the commands it would run."""

    STEP_NAMES = [
        "python",
        "ollama installed",
        "ollama server",
        "base model",
        "derived model",
        "model choice",
    ]

    def plan(self, plat, tier=None, opts=None):
        return setup.plan_steps(tier or setup.MELLUM2, plat, opts or setup.Options())

    def previews(self, plat, tier=None):
        return {step.name: step.preview for step in self.plan(plat, tier)}

    def test_order_is_the_same_everywhere(self) -> None:
        for system in ("macos", "linux", "windows"):
            with self.subTest(system=system):
                names = [step.name for step in self.plan(platform_of(system))]
                self.assertEqual(names, self.STEP_NAMES)

    def test_macos_uses_the_formula_and_the_app(self) -> None:
        previews = self.previews(platform_of("macos", has_ollama_app=True))
        self.assertEqual(previews["ollama installed"], "brew install ollama")
        self.assertEqual(previews["ollama server"], "open -a Ollama")

    def test_macos_without_the_app_uses_brew_services(self) -> None:
        previews = self.previews(platform_of("macos", has_ollama_app=False))
        self.assertEqual(previews["ollama server"], "brew services start ollama")

    def test_macos_without_brew_points_at_the_ollama_download(self) -> None:
        previews = self.previews(platform_of("macos", package_manager=None))
        self.assertIn("ollama.com/download", previews["ollama installed"])

    def test_linux_uses_the_install_script_and_systemd(self) -> None:
        previews = self.previews(platform_of("linux", has_systemd=True))
        self.assertEqual(
            previews["ollama installed"], "curl -fsSL https://ollama.com/install.sh | sh"
        )
        self.assertEqual(previews["ollama server"], "sudo systemctl start ollama")

    def test_wsl2_without_systemd_runs_the_server_itself(self) -> None:
        previews = self.previews(platform_of("linux", wsl=True, has_systemd=False))
        self.assertEqual(previews["ollama server"], "ollama serve &")

    def test_windows_uses_winget(self) -> None:
        previews = self.previews(platform_of("windows"))
        self.assertIn("winget install -e --id Ollama.Ollama", previews["ollama installed"])
        self.assertEqual(previews["ollama server"], "ollama serve &")

    def test_root_gets_no_sudo_and_no_sudo_gets_no_sudo(self) -> None:
        """Found by running the Linux clean room as root, where `sudo` does not exist at all.

        Checked on the zstd prerequisite, which is the package install that still lives here.
        """
        self.assertEqual(
            setup._install_tools_command(platform_of("linux", root=True), ["zstd"])[0],
            "apt-get update && apt-get install -y zstd",
        )
        self.assertNotIn(
            "sudo",
            setup._install_tools_command(
                platform_of("linux", root=False, has_sudo=False), ["zstd"]
            )[0],
        )
        self.assertTrue(
            setup._install_tools_command(platform_of("linux"), ["zstd"])[0]
                .startswith("sudo apt-get")
        )

    def test_systemd_start_follows_the_same_rule(self) -> None:
        self.assertEqual(
            self.previews(platform_of("linux", has_systemd=True, root=True))["ollama server"],
            "systemctl start ollama",
        )
        self.assertEqual(
            self.previews(platform_of("linux", has_systemd=True))["ollama server"],
            "sudo systemctl start ollama",
        )

    def test_homebrew_never_gets_sudo(self) -> None:
        """brew refuses to run as root, so the prefix must not reach it even if we are root."""
        self.assertEqual(
            self.previews(platform_of("macos", root=True))["ollama installed"],
            "brew install ollama",
        )

    def test_the_tier_decides_which_model_is_pulled_and_derived(self) -> None:
        mellum = self.previews(platform_of("linux"), setup.MELLUM2)
        self.assertEqual(
            mellum["base model"],
            "ollama pull hf.co/JetBrains/Mellum2-12B-A2.5B-Instruct-GGUF-Q4_K_M",
        )
        self.assertEqual(mellum["derived model"], "ollama create agentfix-mellum2 -f Modelfile")

        qwen = self.previews(platform_of("linux"), setup.QWEN)
        self.assertEqual(qwen["base model"], "ollama pull qwen2.5-coder:1.5b")
        self.assertEqual(
            qwen["derived model"], "ollama create agentfix-qwen -f Modelfile.agentfix-qwen"
        )

    def test_the_generated_qwen_modelfile_carries_the_context_window(self) -> None:
        """The whole point of deriving a model: /v1 drops per-request num_ctx."""
        self.assertIn("FROM qwen2.5-coder:1.5b", setup.QWEN_MODELFILE_TEXT)
        self.assertIn("PARAMETER num_ctx 16384", setup.QWEN_MODELFILE_TEXT)


class TestModelChoice(unittest.TestCase):
    """.agentfix.env, the shell profile, and the export line — the qwen tier needs all three."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env_file = Path(self.temp.name) / ".agentfix.env"
        self.original = setup.ENV_FILE
        setup.ENV_FILE = self.env_file
        self.addCleanup(lambda: setattr(setup, "ENV_FILE", self.original))

    def test_qwen_writes_the_file_and_mellum2_removes_it(self) -> None:
        setup.write_env_file("agentfix-qwen")
        self.assertIn("MELLUM_MODEL=agentfix-qwen", self.env_file.read_text(encoding="utf-8"))

        setup.write_env_file(None)
        self.assertFalse(self.env_file.exists())

    def test_run_py_reads_the_file(self) -> None:
        setup.write_env_file("agentfix-qwen")
        original = runner.ENV_FILE
        runner.ENV_FILE = self.env_file
        try:
            os.environ.pop("MELLUM_MODEL", None)
            self.assertEqual(runner.env_from_file(), {"MELLUM_MODEL": "agentfix-qwen"})

            # A real variable wins: the file is the default setup chose, not an override of
            # what the learner asked for in this shell.
            os.environ["MELLUM_MODEL"] = "something-else"
            self.assertEqual(runner.env_from_file(), {})
        finally:
            os.environ.pop("MELLUM_MODEL", None)
            runner.ENV_FILE = original

    def test_run_py_survives_a_file_it_cannot_decode(self) -> None:
        """UnicodeDecodeError is a ValueError, not an OSError. A stray byte in .agentfix.env
        must not take down every command that goes through run.py."""
        self.env_file.write_bytes(b"MELLUM_MODEL=\xff\xfe\n")
        original = runner.ENV_FILE
        runner.ENV_FILE = self.env_file
        try:
            with contextlib.redirect_stderr(io.StringIO()) as complaint:
                self.assertEqual(runner.env_from_file(), {})
            self.assertIn("ignoring", complaint.getvalue())
        finally:
            runner.ENV_FILE = original

    def test_run_py_ignores_comments_and_blank_lines(self) -> None:
        self.env_file.write_text("# a comment\n\nMELLUM_MODEL=x\nnonsense\n", encoding="utf-8")
        original = runner.ENV_FILE
        runner.ENV_FILE = self.env_file
        try:
            os.environ.pop("MELLUM_MODEL", None)
            self.assertEqual(runner.env_from_file(), {"MELLUM_MODEL": "x"})
        finally:
            runner.ENV_FILE = original

    def test_shell_block_is_replaced_not_repeated(self) -> None:
        profile = Path(self.temp.name) / ".zshrc"
        first = setup._managed_block("agentfix-qwen", profile)
        text = "export EDITOR=vim\n" + first
        cleaned = setup._strip_managed_block(text)
        self.assertEqual(cleaned, "export EDITOR=vim\n")
        self.assertNotIn("MELLUM_MODEL", cleaned)

    def test_fish_gets_fish_syntax(self) -> None:
        block = setup._managed_block("agentfix-qwen", Path("config.fish"))
        self.assertIn("set -gx MELLUM_MODEL agentfix-qwen", block)

    def test_windows_removes_rather_than_blanks_the_variable(self) -> None:
        """`setx VAR ""` leaves an empty variable behind, which reads as a model named "" ."""
        self.assertEqual(
            list(setup._windows_env_commands("agentfix-qwen")),
            ["setx", "MELLUM_MODEL", "agentfix-qwen"],
        )
        self.assertIn("reg", setup._windows_env_commands(None))

    def _run_model_choice(self, tier, opts, shell="/bin/zsh", home=None):
        """Drive the step the way execute() does: probe, apply, probe again."""
        original = os.environ.get("SHELL")
        original_home = os.environ.get("HOME")
        if shell is None:
            os.environ.pop("SHELL", None)
        else:
            os.environ["SHELL"] = shell
        if home:
            os.environ["HOME"] = home
        try:
            step = setup.model_choice_step(tier, platform_of("linux"), opts)
            with quiet():
                before = step.probe()
                applied = step.apply()
                after = step.probe()
        finally:
            if original is None:
                os.environ.pop("SHELL", None)
            else:
                os.environ["SHELL"] = original
            if original_home is not None:
                os.environ["HOME"] = original_home
        return before, applied, after

    def test_no_shell_profile_is_not_a_failure(self) -> None:
        """A container, a cron job, an IDE terminal without SHELL: there is nothing to edit, and
        `.agentfix.env` is the whole answer. Reporting NOT DONE there is just wrong."""
        before, applied, after = self._run_model_choice(setup.QWEN, setup.Options(yes=True),
                                                        shell=None)
        self.assertFalse(before[0])
        self.assertTrue(applied[0], applied[1])
        self.assertTrue(after[0], "the re-probe still failed: %s" % (after[1],))

    def test_declining_the_profile_edit_is_not_a_failure(self) -> None:
        home = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(home, ignore_errors=True))
        pathlib_path = Path(home) / ".zshrc"
        pathlib_path.write_text("export EDITOR=vim\n", encoding="utf-8")

        original = setup.confirm
        # Yes to writing the file, no to touching the profile.
        setup.confirm = lambda opts, question: "edit" not in question
        try:
            before, applied, after = self._run_model_choice(
                setup.QWEN, setup.Options(), home=home
            )
        finally:
            setup.confirm = original
        self.assertTrue(applied[0], applied[1])
        self.assertTrue(after[0], "the re-probe still failed: %s" % (after[1],))
        self.assertNotIn("MELLUM_MODEL", pathlib_path.read_text(encoding="utf-8"))

    def test_switching_back_to_mellum2_still_cleans_the_profile(self) -> None:
        """The check that made the two states above tricky: this one MUST stay strict."""
        home = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(home, ignore_errors=True))
        profile = Path(home) / ".zshrc"
        profile.write_text(
            "export EDITOR=vim\n" + setup._managed_block("agentfix-qwen", profile),
            encoding="utf-8",
        )
        before, applied, after = self._run_model_choice(
            setup.MELLUM2, setup.Options(yes=True), home=home
        )
        self.assertFalse(before[0], "a stale MELLUM_MODEL block should not read as satisfied")
        self.assertTrue(after[0], after[1])
        self.assertEqual(profile.read_text(encoding="utf-8"), "export EDITOR=vim\n")

    def test_export_line_matches_the_shell(self) -> None:
        self.assertEqual(
            setup.export_line(setup.QWEN, platform_of("linux")),
            "export MELLUM_MODEL=agentfix-qwen",
        )
        self.assertEqual(
            setup.export_line(setup.QWEN, platform_of("windows")),
            "$env:MELLUM_MODEL = 'agentfix-qwen'",
        )
        self.assertIsNone(setup.export_line(setup.MELLUM2, platform_of("macos")))


class TestDryRun(unittest.TestCase):
    """--dry-run must be a promise, not an intention."""

    def setUp(self) -> None:
        self.calls = []

        def refuse(*args, **kwargs):
            raise AssertionError("a dry run tried to execute something: %r" % (args,))

        for name in ("run_command", "_spawn_background"):
            original = getattr(setup, name)
            setattr(setup, name, refuse)
            self.addCleanup(lambda n=name, o=original: setattr(setup, n, o))

        # Every probe reports "not done yet", so every step would want to act.
        setup_api = setup.ollama_api
        setattr(setup, "ollama_api", lambda *a, **k: None)
        self.addCleanup(lambda: setattr(setup, "ollama_api", setup_api))

    def test_nothing_runs_and_the_plan_is_printed(self) -> None:
        for system in ("macos", "linux", "windows"):
            for tier in (setup.MELLUM2, setup.QWEN):
                with self.subTest(system=system, tier=tier.name):
                    opts = setup.Options(dry_run=True, yes=True, tier=tier.name)
                    steps = setup.plan_steps(tier, platform_of(system), opts)
                    with quiet() as printed:
                        self.assertEqual(setup.execute(steps, opts), 0)
                    self.assertIn("would run:", printed.getvalue())

    def test_a_dry_run_does_not_write_the_qwen_modelfile(self) -> None:
        self.assertFalse(setup.QWEN_MODELFILE.exists(), "left over from an earlier run")

    def test_execute_never_calls_apply_on_a_dry_run(self) -> None:
        """execute() is the only gate on --dry-run, so this is the test that guards it.

        setup.py has no second dry-run check inside the apply() bodies on purpose: a branch no
        run ever reaches is a branch no test can trust. That makes this assertion the whole
        safety net, rather than a nice-to-have.
        """
        def explode() -> None:
            raise AssertionError("apply() was called during a dry run")

        opts = setup.Options(dry_run=True, yes=True)
        steps = setup.plan_steps(setup.QWEN, platform_of("linux"), opts)
        for step in steps:
            step.apply = explode
        with quiet():
            self.assertEqual(setup.execute(steps, opts), 0)

    def test_the_backgrounded_server_still_goes_through_run_command(self) -> None:
        """`ollama serve` never returns, so it is spawned rather than waited on — but it must
        not grow its own copy of the confirm-and-print logic."""
        seen = {}

        def record(command, opts, **kwargs):
            seen["command"] = list(command)
            seen["background"] = kwargs.get("background", False)
            return True, "done"

        original = setup.run_command
        setup.run_command = record
        try:
            step = setup.server_step(platform_of("linux", has_systemd=False), setup.Options())
            setattr(setup, "_wait_for_server", lambda *a, **k: True)
            with quiet():
                ok, _ = step.apply()
        finally:
            setup.run_command = original
        self.assertTrue(ok)
        self.assertEqual(seen["command"], ["ollama", "serve"])
        self.assertTrue(seen["background"])


class TestPythonStep(unittest.TestCase):
    """setup.py no longer installs Python. It checks, and points at the script that does."""

    def test_it_points_at_the_shell_bootstrap(self) -> None:
        step = setup.python_step(platform_of("linux"), setup.Options())
        self.assertEqual(step.preview, "./setup.sh")
        ok, detail = step.apply()
        self.assertFalse(ok, "setup.py must not claim it can install its own interpreter")
        self.assertIn("./setup.sh", detail)

    def test_windows_is_pointed_at_the_powershell_one(self) -> None:
        step = setup.python_step(platform_of("windows"), setup.Options())
        self.assertIn("setup.ps1", step.preview)
        self.assertIn("setup.ps1", step.apply()[1])

    def test_the_check_itself_still_works(self) -> None:
        ok, detail = setup.python_step(platform_of("linux"), setup.Options()).probe()
        self.assertEqual(ok, sys.version_info[:2] >= setup.MIN_PYTHON, detail)


class TestEntryPoints(unittest.TestCase):
    """The two bootstrap scripts are now part of the contract, so their absence is a failure."""

    def test_both_scripts_exist(self) -> None:
        self.assertTrue((ROOT / "setup.sh").is_file())
        self.assertTrue((ROOT / "setup.ps1").is_file())

    def test_the_shell_script_is_executable(self) -> None:
        self.assertTrue(os.access(str(ROOT / "setup.sh"), os.X_OK), "chmod +x setup.sh")

    def test_the_shell_script_is_posix_sh_not_bash(self) -> None:
        """/bin/sh on Debian is dash: no double brackets, no arrays, no pipefail.

        Comment lines are stripped before checking, since the comments discuss the very
        constructs being banned.
        """
        text = (ROOT / "setup.sh").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/sh"), text.splitlines()[0])
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        for bashism in ("[[", "pipefail", "declare ", "=(", "${!", "function "):
            self.assertNotIn(bashism, code, "bashism in a /bin/sh script: %s" % bashism)

    def test_both_hand_over_to_setup_py(self) -> None:
        self.assertIn("setup.py", (ROOT / "setup.sh").read_text(encoding="utf-8"))
        self.assertIn("--bootstrapped", (ROOT / "setup.sh").read_text(encoding="utf-8"))
        self.assertIn("--bootstrapped", (ROOT / "setup.ps1").read_text(encoding="utf-8"))

    def test_setup_py_accepts_being_bootstrapped(self) -> None:
        self.assertTrue(setup.parse_args(["--bootstrapped"]).bootstrapped)
        self.assertFalse(setup.parse_args([]).bootstrapped)


class TestOllamaInstallerPrerequisites(unittest.TestCase):
    """Found by running the Linux clean room: the install script needs zstd, and a fresh Debian
    does not have it. Without this the learner gets `ERROR: This version requires zstd`, which
    looks like a broken download rather than a missing package."""

    def stub_missing(self, missing) -> None:
        """`missing` is read on every call, so a caller can mutate it as tools get installed."""
        original = setup.shutil.which
        setup.shutil.which = lambda name: None if name in missing else "/usr/bin/" + name
        self.addCleanup(lambda: setattr(setup.shutil, "which", original))

    def test_zstd_is_installed_before_the_install_script_runs(self) -> None:
        # The set is mutable and apt-get removes from it, because the step re-checks after
        # installing: a stub that never becomes satisfied would fail for the wrong reason.
        missing = {"zstd", "ollama"}
        self.stub_missing(missing)
        ran = []

        def run(command, opts, **kwargs):
            listed = command if isinstance(command, str) else list(command)
            ran.append(listed)
            if isinstance(command, str) and "apt-get install" in command:
                missing.difference_update(command.split("install -y ", 1)[1].split())
            return True, "ok"

        original = setup.run_command
        setup.run_command = run
        try:
            step = setup.ollama_binary_step(platform_of("linux"), setup.Options(yes=True))
            with quiet():
                ok, _ = step.apply()
        finally:
            setup.run_command = original
        self.assertTrue(ok)
        # apt gets an `update &&` glued on, because stale lists are the other way this fails.
        self.assertEqual(
            ran[0], "sudo apt-get update && sudo apt-get install -y zstd"
        )
        self.assertEqual(ran[1], "curl -fsSL https://ollama.com/install.sh | sh")

    def test_both_tools_are_asked_for_when_both_are_missing(self) -> None:
        self.stub_missing({"zstd", "curl", "ollama"})
        ran = []
        original = setup.run_command
        setup.run_command = lambda c, o, **k: ran.append(c) or (True, "ok")
        try:
            step = setup.ollama_binary_step(platform_of("linux"), setup.Options(yes=True))
            with quiet():
                step.apply()
        finally:
            setup.run_command = original
        self.assertEqual(ran[0], "sudo apt-get update && sudo apt-get install -y curl zstd")

    def test_nothing_extra_is_installed_when_the_tools_are_there(self) -> None:
        self.stub_missing({"ollama"})
        ran = []
        original = setup.run_command
        setup.run_command = lambda c, o, **k: ran.append(c) or (True, "ok")
        try:
            step = setup.ollama_binary_step(platform_of("linux"), setup.Options(yes=True))
            with quiet():
                step.apply()
        finally:
            setup.run_command = original
        self.assertEqual(ran, ["curl -fsSL https://ollama.com/install.sh | sh"])

    def test_no_package_manager_says_what_to_install_by_hand(self) -> None:
        self.stub_missing({"zstd", "ollama"})
        step = setup.ollama_binary_step(
            platform_of("linux", package_manager=None), setup.Options(yes=True)
        )
        with quiet():
            ok, detail = step.apply()
        self.assertFalse(ok)
        self.assertIn("zstd", detail)

    def test_macos_and_windows_do_not_get_a_zstd_check(self) -> None:
        """Only the Linux path goes through a shell script with its own dependencies."""
        self.stub_missing({"zstd", "ollama"})
        for system, expected in (("macos", ["brew", "install", "ollama"]),):
            ran = []
            original = setup.run_command
            setup.run_command = lambda c, o, **k: ran.append(list(c)) or (True, "ok")
            try:
                step = setup.ollama_binary_step(platform_of(system), setup.Options(yes=True))
                with quiet():
                    step.apply()
            finally:
                setup.run_command = original
            self.assertEqual(ran, [expected])


class TestPythonInvocation(unittest.TestCase):
    """The last line setup prints is a command. It has to name the interpreter that just worked.

    The trap this closes: on ChromeOS, uv installs 3.12, the script re-execs under it, finishes —
    and then tells the learner to run `python3 run.py doctor`, where `python3` is still the
    system 3.11 that could not run the course in the first place.
    """

    def test_the_short_name_is_used_when_it_resolves_to_this_interpreter(self) -> None:
        original = setup.shutil.which
        setup.shutil.which = lambda name: sys.executable
        try:
            self.assertEqual(setup.python_invocation(), setup.python_command())
        finally:
            setup.shutil.which = original

    def test_the_full_path_is_used_when_the_short_name_is_something_else(self) -> None:
        """The uv case: `python3` exists and is the wrong Python."""
        original = setup.shutil.which
        setup.shutil.which = lambda name: "/usr/bin/python3"
        try:
            self.assertEqual(setup.python_invocation(), setup.quote_path(sys.executable))
        finally:
            setup.shutil.which = original

    def test_the_full_path_is_used_when_the_short_name_does_not_exist_at_all(self) -> None:
        original = setup.shutil.which
        setup.shutil.which = lambda name: None
        try:
            self.assertIn(sys.executable, setup.python_invocation())
        finally:
            setup.shutil.which = original

    def test_paths_are_quoted_for_the_right_shell(self) -> None:
        """shlex.quote's single quotes are not quoting in cmd.exe, and Windows has the spaces."""
        original = os.name
        try:
            setup.os.name = "posix"
            self.assertEqual(setup.quote_path("/opt/my python/bin/python3"),
                             "'/opt/my python/bin/python3'")
            setup.os.name = "nt"
            self.assertEqual(setup.quote_path(r"C:\Program Files\Python312\python.exe"),
                             '"C:\\Program Files\\Python312\\python.exe"')
            self.assertEqual(setup.quote_path(r"C:\Python312\python.exe"),
                             r"C:\Python312\python.exe")
        finally:
            setup.os.name = original


class TestHasModel(unittest.TestCase):
    """Ollama always reports a tag; some of our names carry one and some do not."""

    def test_implicit_latest_tag_matches(self) -> None:
        names = [
            "agentfix-qwen:latest",
            "hf.co/JetBrains/Mellum2-12B-A2.5B-Instruct-GGUF-Q4_K_M:latest",
            "qwen2.5-coder:1.5b",
        ]
        self.assertTrue(setup.has_model(names, "agentfix-qwen"))
        self.assertTrue(setup.has_model(names, setup.MELLUM_BASE_MODEL))
        self.assertTrue(setup.has_model(names, "qwen2.5-coder:1.5b"))

    def test_a_different_tag_is_a_different_model(self) -> None:
        self.assertFalse(setup.has_model(["qwen2.5-coder:3b"], "qwen2.5-coder:1.5b"))
        self.assertFalse(setup.has_model(["agentfix-mellum2:latest"], "agentfix-qwen"))


class TestNoDrift(unittest.TestCase):
    """The lesson ships six copies of `agentfix/`, one per step, and they drift.

    It has happened twice already — a tier that the README no longer had, and tier numbers that
    stopped matching. These two tests are cheap and catch the whole class of it.
    """

    DOCTORS = sorted(ROOT.glob("agentfix/*/*/agentfix/doctor.py"))

    def test_every_doctor_copy_agrees(self) -> None:
        self.assertEqual(len(self.DOCTORS), 6, "a lesson step gained or lost a doctor.py")
        texts = {}
        for path in self.DOCTORS:
            texts.setdefault(path.read_text(encoding="utf-8"), []).append(
                str(path.relative_to(ROOT))
            )
        if len(texts) > 1:
            groups = ["\n    ".join(paths) for paths in texts.values()]
            self.fail("doctor.py has diverged between lesson steps:\n    " +
                      "\n  --- vs ---\n    ".join(groups))

    def test_the_windows_memory_struct_matches_setup_py(self) -> None:
        """Both files read RAM through MEMORYSTATUSEX. The struct is an ABI: a field out of
        order in one of them returns plausible nonsense rather than failing."""
        def fields(source: str) -> list:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and any(
                    getattr(target, "id", "") == "_fields_" for target in node.targets
                ):
                    return [ast.unparse(element) for element in node.value.elts]
            self.fail("no _fields_ found")

        setup_fields = fields((ROOT / "setup.py").read_text(encoding="utf-8"))
        doctor_fields = fields(self.DOCTORS[0].read_text(encoding="utf-8"))
        self.assertEqual(setup_fields, doctor_fields)
        self.assertIn("('ullTotalPhys', ctypes.c_ulonglong)", setup_fields)
        self.assertIn("('ullAvailPhys', ctypes.c_ulonglong)", setup_fields)


class TestOldPythonCompatibility(unittest.TestCase):
    """setup.py has to parse under the interpreter it is about to replace.

    A definitive check needs a real 3.8 interpreter, which the test environment does not have.
    This catches the mistakes that are actually easy to make in a file this size: syntax added
    after 3.8, and standard-library calls that did not exist yet.
    """

    NEWER_THAN_3_8 = (
        "removeprefix",
        "removesuffix",
        "functools.cache",
        "graphlib",
        "zoneinfo",
        "tomllib",
        "ExceptionGroup",
        "shutil.copytree(dirs_exist_ok",
        "math.lcm",
    )

    def setUp(self) -> None:
        self.source = (ROOT / "setup.py").read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_annotations_are_postponed(self) -> None:
        """Without this, `Optional[int] | None`-style annotations would be evaluated at import."""
        self.assertIn("from __future__ import annotations", self.source)

    def test_no_syntax_newer_than_3_8(self) -> None:
        forbidden = {
            "Match": "match statements are 3.10+",
            "TryStar": "except* is 3.11+",
            "TypeAlias": "the type statement is 3.12+",
        }
        for node in ast.walk(self.tree):
            name = type(node).__name__
            if name in forbidden:
                self.fail("%s: %s" % (name, forbidden[name]))

    def test_no_standard_library_newer_than_3_8(self) -> None:
        for symbol in self.NEWER_THAN_3_8:
            with self.subTest(symbol=symbol):
                self.assertNotIn(symbol, self.source)

    def test_no_walrus_free_f_string_debugging(self) -> None:
        """f"{x=}" is 3.8, but only just — and it reads as a leftover debug print."""
        self.assertNotIn('=}"', self.source)


if __name__ == "__main__":
    unittest.main()
