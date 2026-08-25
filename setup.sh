#!/bin/sh
# Workshop setup for macOS, Linux, WSL2 and ChromeOS. One command:
#
#     ./setup.sh
#
# This script exists to solve exactly one problem — getting a Python 3.12 onto the machine —
# and then it hands over to setup.py, which does the model work. That split is deliberate:
#
#   * A Python script cannot install the interpreter it needs in order to run. This one can,
#     which is why a machine with no Python at all still works.
#   * Everything after Python is decisions and state (which tier, is the model derived, what
#     goes in .agentfix.env). That belongs in one tested implementation, not in one copy per
#     operating system. See setup.py.
#
# Plain POSIX sh, not bash: /bin/sh on Debian is dash, and this has to run before anything is
# installed. No pipefail, no arrays, no [[ ]].
#
#     ./setup.sh                  # ask before each change
#     ./setup.sh --yes            # assume yes (pre-session run)
#     ./setup.sh --dry-run        # plan only; changes nothing
#     ./setup.sh --tier qwen      # anything else is passed through to setup.py
set -eu

ROOT=$(cd "$(dirname "$0")" && pwd)
PYTHON_SERIES=3.12
UV_URL=https://astral.sh/uv/install.sh
PYTHON_ORG=https://www.python.org/downloads/

have() { command -v "$1" >/dev/null 2>&1; }

# Where the interpreter we settle on ends up. A global, not a return value on stdout: these
# functions also print progress and run installers, and `X=$(find_python)` would swallow all of
# that into the variable instead of showing it to you.
FOUND_PYTHON=""
say()  { printf '%s\n' "$*"; }

# --yes and --dry-run are read here as well as passed on: this script has its own prompt to
# skip, and it must not install anything during a dry run.
ASSUME_YES=0
DRY_RUN=0
for argument in "$@"; do
  case "$argument" in
    --yes) ASSUME_YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
  esac
done

confirm() {
  [ "$ASSUME_YES" = 1 ] && return 0
  [ -t 0 ] || { say "     (not a terminal, so nothing is assumed — re-run with --yes)"; return 1; }
  printf '     %s [Y/n] ' "$1"
  read -r reply || return 1
  case "$reply" in ""|y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

run() {
  say "     \$ $*"
  confirm "run it?" || { say "declined — nothing was changed"; exit 1; }
  "$@"
}

# Same, but a non-zero exit is a normal answer rather than the end of the script: used where
# there is another rung on the ladder to try.
try_run() {
  say "     \$ $*"
  confirm "run it?" || return 1
  "$@" || return 1
}

# Already root, or able to become it, or neither. Containers and minimal Debian installs are
# root with no sudo binary at all, so `sudo` cannot be hardcoded.
if [ "$(id -u)" = 0 ]; then
  SUDO=""
elif have sudo; then
  SUDO="sudo"
else
  SUDO=""
  say "note: not root and no sudo here — a package install may fail with a permission error."
fi

case "$(uname -s)" in
  Darwin) SYSTEM=macos ;;
  Linux)  SYSTEM=linux ;;
  *)      SYSTEM=$(uname -s) ;;
esac

MANAGER=""
if [ "$SYSTEM" = macos ]; then
  have brew && MANAGER=brew
else
  for candidate in apt-get dnf pacman; do
    if have "$candidate"; then MANAGER=$candidate; break; fi
  done
fi

say "agentfix setup"
say "  machine: $SYSTEM, package manager: ${MANAGER:-none found}"

# --- is there already a new enough Python? ----------------------------------------------------

MAJOR=${PYTHON_SERIES%%.*}
MINOR=${PYTHON_SERIES#*.}

# The interpreter answers for itself rather than us parsing `python3 -V`: version strings have
# suffixes ("3.12.0rc1", "3.11.2+") that string comparison gets wrong.
new_enough() {
  "$1" -c "import sys; sys.exit(0 if sys.version_info[:2] >= ($MAJOR, $MINOR) else 1)" \
    2>/dev/null
}

find_python() {
  # Newest first, then the generic names. `python` last: on Debian it is usually absent, and
  # where it exists it is often the oldest thing on the machine.
  for candidate in python3.14 python3.13 python3.12 python3 python; do
    if have "$candidate" && new_enough "$candidate"; then
      FOUND_PYTHON=$(command -v "$candidate")
      return 0
    fi
  done
  # uv keeps its interpreters outside PATH, so ask uv directly rather than looking for them.
  for uv in uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if have "$uv" || [ -x "$uv" ]; then
      found=$("$uv" python find "$PYTHON_SERIES" 2>/dev/null || true)
      if [ -n "$found" ] && [ -x "$found" ] && new_enough "$found"; then
        FOUND_PYTHON=$found
        return 0
      fi
    fi
  done
  return 1
}

# Two rungs, because which one works depends on the distro and neither covers both cases:
#
#   `python3.12` exists on Ubuntu 24.04 but on no Debian and not on Ubuntu 22.04.
#   plain `python3` is already 3.13 on Debian 13 and 3.14 on Fedora, but only 3.10 on
#   Ubuntu 22.04 and 3.11 on Debian 12.
#
# So: ask for the exact series, and if the distro has no such package, ask for its default and
# let the version check decide. Whatever is left over goes to uv.
install_with_manager() {
  case "$MANAGER" in
    brew)    run brew install "python@$PYTHON_SERIES" ;;
    apt-get) try_run sh -c "${SUDO:+$SUDO }apt-get update && ${SUDO:+$SUDO }apt-get install -y python$PYTHON_SERIES" \
               || run sh -c "${SUDO:+$SUDO }apt-get install -y python3" ;;
    dnf)     try_run ${SUDO:+$SUDO} dnf install -y "python$PYTHON_SERIES" \
               || run ${SUDO:+$SUDO} dnf install -y python3 ;;
    # Arch is rolling: there is no python3.12 package, `python` is the current series.
    pacman)  run ${SUDO:+$SUDO} pacman -S --needed --noconfirm python ;;
    *)       return 1 ;;
  esac
}

ensure_curl() {
  have curl && return 0
  say "     the uv installer needs curl, which this machine does not have yet:"
  case "$MANAGER" in
    apt-get) run sh -c "${SUDO:+$SUDO }apt-get update && ${SUDO:+$SUDO }apt-get install -y curl" ;;
    dnf)     run ${SUDO:+$SUDO} dnf install -y curl ;;
    pacman)  run ${SUDO:+$SUDO} pacman -S --needed --noconfirm curl ;;
    *)       say "     install curl first, then run this script again"; return 1 ;;
  esac
  have curl
}

install_with_uv() {
  ensure_curl || return 1
  uv=""
  for candidate in uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if have "$candidate"; then uv=$(command -v "$candidate"); break; fi
    if [ -x "$candidate" ]; then uv=$candidate; break; fi
  done

  if [ -z "$uv" ]; then
    say "     installing uv, to fetch a real CPython:"
    # Downloaded to a file and then run, not piped into sh: a pipeline reports the *shell's*
    # exit status, so a failed download would look like a successful install.
    script=$(mktemp)
    run curl -LsSf "$UV_URL" -o "$script"
    run sh "$script"
    rm -f "$script"
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
      [ -x "$candidate" ] && uv=$candidate && break
    done
    have uv && uv=$(command -v uv)
  fi

  [ -n "$uv" ] || { say "     uv installed but cannot be found — open a new terminal and retry"; return 1; }
  run "$uv" python install "$PYTHON_SERIES"
  # `uv python find` rather than PATH: uv installs into ~/.local/bin, which the current shell
  # has usually not picked up yet.
  FOUND_PYTHON=$("$uv" python find "$PYTHON_SERIES" 2>/dev/null || true)
  [ -n "$FOUND_PYTHON" ] && [ -x "$FOUND_PYTHON" ]
}

find_python || true

if [ -z "$FOUND_PYTHON" ]; then
  say ""
  say "[todo] python: nothing here is $PYTHON_SERIES or newer"
  if [ "$DRY_RUN" = 1 ]; then
    say "       would install Python $PYTHON_SERIES with ${MANAGER:-uv}, then run setup.py under it"
    say ""
    say "READY (dry run — nothing was changed)"
    exit 0
  fi

  if [ -n "$MANAGER" ] && install_with_manager; then
    find_python || true
  fi

  if [ -z "$FOUND_PYTHON" ]; then
    if [ -n "$MANAGER" ]; then
      say "     ${MANAGER} did not produce a Python $PYTHON_SERIES — trying the portable uv"
      say "     installer instead."
    fi
    install_with_uv || true
  fi

  if [ -z "$FOUND_PYTHON" ] || ! new_enough "$FOUND_PYTHON"; then
    say ""
    say "Could not get Python $PYTHON_SERIES onto this machine."
    say "Install it from $PYTHON_ORG, then run: ./setup.sh"
    exit 1
  fi
  say "[ok]   python: $("$FOUND_PYTHON" -V 2>&1) at $FOUND_PYTHON"
fi

# Everything from here — the tier, the models, the context window, .agentfix.env — is the same
# on every operating system, so it lives in one place.
exec "$FOUND_PYTHON" "$ROOT/setup.py" --bootstrapped "$@"
