#!/usr/bin/env bash
# A clean Linux machine, in a container. This is the closest of the three to a real
# learner's machine, because it *is* one: debian:bookworm is the same base as the Linux
# container on a Chromebook, and it ships python3.11 with no python3.12 candidate in apt.
#
#   clean_room/linux.sh                      # 3.4 GB "Chromebook" -> expects the colab verdict
#   clean_room/linux.sh --ram 12             # 12 GB -> qwen tier, exercises the uv fallback
#   clean_room/linux.sh --ram 32 --tier qwen # skip the 8 GB pull but keep the mellum2-class RAM
#   clean_room/linux.sh --shell              # just drop me in the container
#   clean_room/linux.sh --virgin             # no cached models: re-download everything
#   clean_room/linux.sh --image ubuntu:24.04  # a distro where apt DOES have python3.12
#
# Anything after `--` is passed straight to setup.py.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAM_GB=3.4
IMAGE=debian:bookworm
TIER=""
MODE=setup
KEEP_MODELS=1
PASSTHROUGH=()

while [ $# -gt 0 ]; do
  case "$1" in
    --ram)     RAM_GB="$2"; shift 2 ;;
    --tier)    TIER="$2"; shift 2 ;;
    --image)   IMAGE="$2"; shift 2 ;;
    --shell)   MODE=shell; shift ;;
    --virgin)  KEEP_MODELS=0; shift ;;
    --)        shift; PASSTHROUGH=("$@"); break ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)         echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

docker info >/dev/null 2>&1 || {
  echo "Docker is not running. Start Docker Desktop (open -a Docker) and try again." >&2
  exit 1
}

# The container sees the HOST's /proc/meminfo, so --memory would not change the tier setup
# picks: setup.py reads MemTotal. Bind-mounting a file over /proc/meminfo is what actually
# simulates a small machine.
MEMINFO="$(mktemp -t meminfo)"
trap 'rm -f "$MEMINFO"' EXIT
KB=$(awk -v gb="$RAM_GB" 'BEGIN { printf "%d", gb * 1024 * 1024 }')
{
  printf 'MemTotal:       %8d kB\n' "$KB"
  printf 'MemFree:        %8d kB\n' $((KB / 3))
  printf 'MemAvailable:   %8d kB\n' $((KB / 2))
} > "$MEMINFO"

# -it only when there really is a terminal, so this script also works from CI or a pipe.
# A plain string, not an array: macOS still ships bash 3.2, where expanding an empty array
# under `set -u` is an error.
TTY=""
if [ -t 0 ] && [ -t 1 ]; then TTY="-it"; fi

MOUNTS=(-v "$ROOT":/course -w /course -v "$MEMINFO":/proc/meminfo:ro)
if [ "$KEEP_MODELS" = 1 ]; then
  # A named volume so a re-run does not re-download the model. --virgin skips it.
  MOUNTS+=(-v agentfix-clean-room-ollama:/root/.ollama)
fi

# Nothing is installed. Not python3, not curl, not zstd. `./setup.sh` is supposed to cope with a
# machine that has no Python at all — that is the whole reason it is a shell script — and
# handing it a python3 would hide the one thing most worth testing. Only apt-get update, because
# nothing can be installed without it.
PREP='apt-get update -qq >/dev/null 2>&1
echo "== clean room: $(. /etc/os-release; echo "$PRETTY_NAME"), python3: $(command -v python3 >/dev/null 2>&1 && python3 -V 2>&1 || echo NONE), $(awk "/MemTotal/ {printf \"%.1f GB RAM\", \$2/1048576}" /proc/meminfo) =="
echo "== apt candidate for python3.12: $(apt-cache policy python3.12 2>/dev/null | sed -n 2p | sed "s/^ *//" || echo none) =="
echo'

if [ "$MODE" = shell ]; then
  [ -z "$TTY" ] && { echo "--shell needs a terminal" >&2; exit 2; }
  RUN="$PREP
echo 'You are root in a clean Debian. Try: ./setup.sh'
exec bash"
else
  ARGS=""
  [ -n "$TIER" ] && ARGS="--tier $TIER"
  RUN="$PREP
./setup.sh $ARGS ${PASSTHROUGH[*]:-}
status=\$?
echo
echo \"== setup.sh exited \$status ==\""
fi

exec docker run --rm $TTY "${MOUNTS[@]}" "$IMAGE" bash -lc "$RUN"
