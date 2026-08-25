#!/usr/bin/env bash
# A clean Mac. Two modes, because macOS cannot be containerised.
#
#   clean_room/macos.sh                 # lite: this Mac, with a scrubbed environment (default)
#   clean_room/macos.sh --for-real      # lite, but let it actually install things
#   clean_room/macos.sh --use-host-ollama  # do NOT hide the Ollama already running here
#   clean_room/macos.sh --vm            # a real throwaway macOS VM via tart (50 GB download)
#
# LITE MODE is not a virtual machine. It runs setup.py through `env -i`, so the process sees
# no Homebrew, no uv, no ollama and no shell profile — and it runs under /usr/bin/python3,
# which on macOS is the Command Line Tools interpreter (3.9.x), i.e. exactly the "wrong Python"
# a learner with a clean Mac has. That covers the two things that actually differ on a fresh
# Mac (no package manager, old interpreter) in about a second and with nothing to uninstall.
#
# What lite mode CANNOT tell you: whether `brew install ollama` works, or whether the Ollama app
# starts. It also cannot hide /Applications, so if you have Ollama.app installed, the plan will
# say `open -a Ollama` where a clean Mac would say `ollama serve &`. For those, use --vm.
#
# In lite mode nothing touches your real setup: HOME points at a temp directory, so a uv
# install lands there and is deleted with it. It defaults to --dry-run anyway.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE=lite
FOR_REAL=0
HIDE_OLLAMA=1

while [ $# -gt 0 ]; do
  case "$1" in
    --vm)       MODE=vm; shift ;;
    --lite)     MODE=lite; shift ;;
    --for-real) FOR_REAL=1; shift ;;
    --use-host-ollama) HIDE_OLLAMA=0; shift ;;
    -h|--help)  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)          echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

[ "$(uname -s)" = "Darwin" ] || { echo "This one only makes sense on a Mac." >&2; exit 1; }

if [ "$MODE" = lite ]; then
  TMPHOME="$(mktemp -d -t agentfix-clean-home)"
  trap 'rm -rf "$TMPHOME"' EXIT

  echo "== clean room (lite): $(sw_vers -productName) $(sw_vers -productVersion), $(uname -m) =="
  echo "== interpreter: $(/usr/bin/python3 -V 2>&1) at /usr/bin/python3 =="
  echo "== HOME -> $TMPHOME, PATH -> /usr/bin:/bin:/usr/sbin:/sbin (no brew, no uv, no ollama) =="
  echo

  # A clean Mac has no Ollama listening. This Mac probably does — and setup.py would find it
  # over the loopback and report the server and the models as already done, which is the one
  # part of a clean-machine run this mode would otherwise get wrong. Point it at a dead port.
  ARGS="--dry-run"
  if [ "$HIDE_OLLAMA" = 1 ]; then
    ARGS="$ARGS --base-url http://127.0.0.1:11435/v1"
    echo "== hiding any Ollama on this Mac by pointing at port 11435 (--use-host-ollama keeps it) =="
    echo
  fi
  if [ "$FOR_REAL" = 1 ]; then
    ARGS="${ARGS#--dry-run}"
    echo "!! --for-real: it may install uv and a CPython into $TMPHOME (thrown away on exit)."
    echo "!! It cannot install Ollama, because there is no Homebrew in this environment."
    echo
  fi

  # env -i wipes the environment: no HOMEBREW_*, no PATH additions, no SHELL, no MELLUM_MODEL.
  # TERM is kept only so prompts render.
  set +e
  env -i \
    HOME="$TMPHOME" \
    PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    TERM="${TERM:-xterm}" \
    /usr/bin/python3 "$ROOT/setup.py" $ARGS
  status=$?
  set -e
  echo
  echo "== setup.py exited $status =="
  exit $status
fi

# ---------------------------------------------------------------------------------------------
# VM mode: a real, disposable macOS install.
# ---------------------------------------------------------------------------------------------
VM_NAME=agentfix-clean-room
IMAGE=ghcr.io/cirruslabs/macos-sequoia-base:latest

[ "$(uname -m)" = "arm64" ] || {
  echo "macOS VMs need Apple Silicon. On Intel, use the Linux clean room or a spare Mac." >&2
  exit 1
}

if ! command -v tart >/dev/null 2>&1; then
  echo "tart is not installed. It is the tool that runs macOS VMs on Apple Silicon:"
  echo "    brew install cirruslabs/cli/tart"
  exit 1
fi

if ! tart list --format json 2>/dev/null | grep -q "\"$VM_NAME\""; then
  echo "About to download $IMAGE."
  echo "That is a full macOS install: ~50 GB on disk, and a long download."
  printf "Continue? [y/N] "
  read -r reply
  case "$reply" in
    y|Y|yes) ;;
    *) echo "Stopped. Nothing downloaded."; exit 0 ;;
  esac
  tart clone "$IMAGE" "$VM_NAME"
fi

echo "Starting $VM_NAME with this repo shared into it."
echo "Log in as admin / admin. Inside the VM, the course is at:"
echo "    /Volumes/My Shared Files/course"
echo
echo "Then, in the VM's Terminal:"
echo "    cd '/Volumes/My Shared Files/course'"
echo "    python3 setup.py                 # or: python3 setup.py --tier qwen"
echo
echo "Check what the image already has before you trust the result — if Homebrew is"
echo "preinstalled, this is not a no-package-manager test:"
echo "    brew --version ; python3 -V ; command -v ollama"
echo
echo "To throw the machine away afterwards:  tart delete $VM_NAME"
echo
exec tart run "$VM_NAME" --dir="course:$ROOT"
