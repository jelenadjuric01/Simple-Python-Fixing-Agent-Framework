#!/usr/bin/env python3
"""Run either agent from the course root, so you never have to go looking for its directory.

    python run.py doctor                                  # the no-framework agent
    python run.py agentlang doctor                        # the LangGraph agent
    python run.py agentlang solve tasks/workshop/01-shopcart --verbose
    python run.py unittest tests.test_task -v
    python run.py agentlang docker-build

Why this exists. The guided project is a JetBrains Academy *framework lesson*, and those keep all
of a lesson's code in one plugin-managed working directory. That directory is real, but it is not
declared course content, so the Course View does not show it: you would have to switch to the
Project Files view and walk down four levels to get a terminal in the right place, on every
single task. This script finds that directory for you and runs the command there, so the terminal
you already have at the course root is enough.

It shells out rather than importing, deliberately. The course root contains a directory called
`agentfix/` (the course *section*), and Python would happily treat that as a namespace package
and import it instead of the real one. Running a subprocess with the working directory set means
the package name always resolves to the package, never to the section folder.

Two lessons, two agents, two package names. `lesson_build` builds the agent with no framework and
calls its package `agentfix`; `lesson_langchain` rebuilds it on LangGraph and calls its package
`agentlang`. They are separate implementations with separate code, so every command — solve,
eval, doctor, docker-build — has to run against one or the other.

Which one? Nothing on disk tells this script which lesson you are reading, so it is chosen
explicitly, in this order:

  1. the first word of the command   `python run.py agentlang solve ...`
  2. a flag, if you prefer it        `python run.py --agentlang solve ...`
  3. AGENT_EDITION in .agentfix.env  set it once while working through a lesson
  4. the default, `agentfix`

The `[run.py]` line printed before every command names the directory it chose, so which agent
ran is never a guess.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Written by `python setup.py`: the model the chosen tier installed. It lives in a file because a
# child process cannot set its parent shell's environment, so `setup.py` has no way to hand the
# variable back to the terminal you are standing in. Reading it here means the choice survives a
# new terminal without anyone having to remember an `export` line.
#
# Shared by both editions, and deliberately still called `.agentfix.env` — `setup.py` writes it,
# and the tier it records is a property of the machine, not of which lesson you are running.
# `AGENT_EDITION` is also read from here, which is what makes step 3 above work.
ENV_FILE = ROOT / ".agentfix.env"

EDITION_KEY = "AGENT_EDITION"

# One image serves both editions: `docker_backend.py` in each looks for `agentfix-sandbox:latest`,
# and the Dockerfile only ever installs a Python and a working directory.
SANDBOX_IMAGE = "agentfix-sandbox"


@dataclass(frozen=True)
class Edition:
    """One of the two agents: where its code lives, and what its package is called."""

    name: str  # what you type: `python run.py <name> <command>` or `--<name>`
    package: str  # the importable package, for `python -m <package>.cli`
    lesson: str  # the lesson it belongs to, named in errors
    workdirs: tuple[Path, ...]  # probed in order; the first one holding the package wins
    tests_workdir: Path | None  # where `unittest` runs, if this edition ships a suite

    def find_workdir(self) -> Path:
        """The directory this edition's code actually lives in."""
        for candidate in self.workdirs:
            if (candidate / self.package / "cli.py").is_file():
                return candidate
        searched = "\n  ".join(str(p) for p in self.workdirs)
        raise SystemExit(
            f"run.py could not find the {self.package} code. Looked in:\n  {searched}\n\n"
            f"If you are a learner: open the '{self.lesson}' lesson once — the working directory\n"
            "is created when you first open one of its steps. Then try again."
        )


AGENTFIX = Edition(
    name="agentfix",
    package="agentfix",
    lesson="Agent with no Framework",
    # `run_for_real` is the finished no-framework agent, so it is the copy worth running: every
    # earlier step in the lesson is the same code with pieces taken out. `task/` is the
    # plugin-managed directory a learner's project materialises, and it does not exist in the
    # authoring repo — probed first so a learner runs their own work rather than the solution.
    workdirs=(
        ROOT / "agentfix" / "lesson_build" / "task",
        ROOT / "agentfix" / "lesson_build" / "run_for_real",
    ),
    # Both steps of this lesson are theory tasks and neither ships a suite. See TESTS_FALLBACK.
    tests_workdir=None,
)

AGENTLANG = Edition(
    name="agentlang",
    package="agentlang",
    lesson="What about frameworks?",
    workdirs=(
        ROOT / "agentfix" / "lesson_langchain" / "task",
        ROOT / "agentfix" / "lesson_langchain" / "real",
    ),
    # `stage_2` is where the project is complete, so that is where the whole suite lives —
    # the graph, its state, the stop condition, the budget and guard, the wiring and the oracle.
    tests_workdir=ROOT / "agentfix" / "lesson_langchain" / "stage_2",
)

EDITIONS: dict[str, Edition] = {AGENTFIX.name: AGENTFIX, AGENTLANG.name: AGENTLANG}

# What you get when you name nothing, so every command that worked before this file learned
# about a second edition still works unchanged.
DEFAULT_EDITION = AGENTFIX.name

# `unittest` under an edition that ships no suite runs this one instead, rather than failing with
# `No module named 'tests'`. The printed `[run.py]` line names the directory, so the substitution
# is visible rather than silent.
TESTS_FALLBACK = AGENTLANG

USAGE = """usage: python run.py [edition] <command> [args...]

  python run.py doctor
  python run.py solve tasks/workshop/01-shopcart --verbose
  python run.py eval workshop
  python run.py unittest tests.test_task -v
  python run.py docker-build

Any other command is passed straight through to `python -m <package>.cli`.

editions — pick one as the first word, or as --<name>, or set {key} in {env}:
{editions}
default: {default}"""


def usage() -> str:
    listed = "\n".join(
        f"  {e.name:<10} {e.lesson:<26} python -m {e.package}.cli" for e in EDITIONS.values()
    )
    return USAGE.format(
        key=EDITION_KEY, env=ENV_FILE.name, editions=listed, default=DEFAULT_EDITION
    )


def env_from_file() -> dict[str, str]:
    """Settings from `.agentfix.env`, minus anything already set for real.

    A real environment variable always wins over the file: the file is the default that setup
    chose, not an override of what you asked for in this shell.
    """
    if not ENV_FILE.is_file():
        return {}
    try:
        text = ENV_FILE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        # UnicodeDecodeError is a ValueError, not an OSError — a file with a stray non-UTF-8
        # byte in it must not take down every command that goes through run.py.
        print(f"[run.py] ignoring {ENV_FILE.name}: {error}", file=sys.stderr)
        return {}

    overrides: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key and key not in os.environ:
            overrides[key] = value
    return overrides


def pick_edition(argv: list[str], overrides: dict[str, str]) -> tuple[Edition, list[str]]:
    """Resolve which agent to run, and hand back the arguments with the choice removed.

    Only a name that is actually registered counts, so `run.py solve ...` keeps working and a
    typo stays an unknown *command* for the CLI to report rather than becoming a silently
    different agent.
    """
    if argv and argv[0] in EDITIONS:
        return EDITIONS[argv[0]], argv[1:]

    if argv and argv[0].startswith("--") and argv[0][2:] in EDITIONS:
        return EDITIONS[argv[0][2:]], argv[1:]

    # A real environment variable beats the file, same rule as every other setting.
    chosen = os.environ.get(EDITION_KEY) or overrides.get(EDITION_KEY)
    if chosen:
        if chosen not in EDITIONS:
            known = ", ".join(EDITIONS)
            raise SystemExit(f"{EDITION_KEY}={chosen!r} is not an edition. Known: {known}")
        return EDITIONS[chosen], argv

    return EDITIONS[DEFAULT_EDITION], argv


def tests_workdir_for(edition: Edition) -> Path:
    """Where `unittest` should run for this edition."""
    if edition.tests_workdir is not None:
        return edition.tests_workdir
    if TESTS_FALLBACK.tests_workdir is None:  # pragma: no cover - guards a future edit
        raise SystemExit("run.py has no edition that ships a test suite.")
    print(
        f"[run.py] {edition.package} ships no tests; running the {TESTS_FALLBACK.package} suite",
        file=sys.stderr,
    )
    return TESTS_FALLBACK.tests_workdir


def build_command(edition: Edition, argv: list[str]) -> tuple[list[str], Path]:
    """The subprocess to run, and the directory to run it in."""
    # `unittest` is resolved before the agent's code is located: the suite lives with the
    # finished project, which is not necessarily the copy this edition would solve tasks from.
    if argv[0] == "unittest":
        workdir = tests_workdir_for(edition)
        if not (workdir / "tests").is_dir():
            raise SystemExit(f"run.py could not find a tests/ directory in:\n  {workdir}")
        return [sys.executable, "-m", "unittest", *argv[1:]], workdir

    workdir = edition.find_workdir()

    if argv[0] == "docker-build":
        # Built from the edition's own directory rather than a path spelled out up here, so
        # renaming or reordering tasks cannot leave this pointing at a directory that is gone.
        if not (workdir / "Dockerfile.sandbox").is_file():
            raise SystemExit(f"run.py could not find Dockerfile.sandbox in:\n  {workdir}")
        return ["docker", "build", "-t", SANDBOX_IMAGE, "-f", "Dockerfile.sandbox", "."], workdir

    # Everything else — doctor, solve, eval, --version — is the edition's own CLI.
    return [sys.executable, "-m", f"{edition.package}.cli", *argv], workdir


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(usage())
        return 0

    overrides = env_from_file()
    edition, argv = pick_edition(argv, overrides)
    if not argv:
        print(usage())
        return 0

    command, workdir = build_command(edition, argv)
    settings = "".join(f"{key}={value} " for key, value in sorted(overrides.items()))

    print(
        f"[run.py] {edition.name}: {workdir.relative_to(ROOT)} $ "
        f"{settings}{' '.join(command)}\n",
        flush=True,
    )
    return subprocess.call(command, cwd=workdir, env={**os.environ, **overrides})


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
