# Clean rooms

Three ways to run `setup.py` on a machine that has never seen it. They are not three of the
same thing, and the difference matters when you read the results:

| | What it actually is | Fidelity | Cost |
|---|---|---|---|
| `linux.sh` | a real Docker container | **high** — it *is* a fresh Debian, and Debian is what ChromeOS's Linux container runs | seconds |
| `macos.sh` | this Mac with a scrubbed environment (`--lite`), or a real throwaway VM (`--vm`) | medium / high | seconds / ~50 GB |
| `windows.ps1` | a script you run *inside* a Windows you provide | high | a VM, or a free CI run |

Windows and macOS cannot be containerised from a Mac: Windows containers need a Windows host,
and macOS VMs need Apple hardware plus a 50 GB image. So only Linux is a one-command clean room.
The other two are honest about what they are.

## Linux — the real thing

```bash
clean_room/linux.sh                       # 3.4 GB "Chromebook" → expect the Colab verdict
clean_room/linux.sh --ram 12              # 12 GB → qwen tier, and the uv fallback has to work
clean_room/linux.sh --ram 32              # mellum2 tier (8 GB pull — maybe add: -- --dry-run)
clean_room/linux.sh --image ubuntu:24.04  # apt HAS python3.12
clean_room/linux.sh --image ubuntu:22.04  # apt has none: the uv path
clean_room/linux.sh --image debian:13     # python3 is already 3.13: nothing to install
clean_room/linux.sh --shell               # poke around by hand
clean_room/linux.sh --virgin              # no cached models; download everything again
clean_room/linux.sh -- --dry-run          # anything after -- goes to setup.py
```

Needs Docker running (`open -a Docker`). The container installs **nothing** first — no
`python3`, no `curl`, no `zstd`. That is the point: `./setup.sh` is a shell script precisely so
that a machine with no Python at all can be set up, and handing it a `python3` would hide the one
thing most worth testing. Leaving `curl` and `zstd` out is likewise how their absence was found.

**It writes into your working copy.** The repo is bind-mounted, so a real (non-dry) run leaves
`.agentfix.env` and `Modelfile.agentfix-qwen` behind in the course root, created by root. Both
are gitignored, and `rm -f .agentfix.env Modelfile.agentfix-qwen` clears them.

RAM is faked by bind-mounting a file over `/proc/meminfo`, because that is what `setup.py`
reads. `docker run --memory=3g` would **not** work: inside a container `/proc/meminfo` still
reports the host's memory.

What to look for:

* at 3.4 GB — `no local model … this machine gets the browser path`, exit 0
* at 12 GB on Debian — `apt-get did not produce a Python 3.12 — trying the portable uv installer
  instead`, then the whole run under `/root/.local/share/uv/python/…/python3.12`
* on `ubuntu:24.04` — `apt-get install python3.12` succeeds and uv is never touched
* on `ubuntu:22.04` — no `python3.12` package, `python3` is 3.10, so uv carries it (the most
  common "normal Linux" case)

## macOS

```bash
clean_room/macos.sh                    # lite: scrubbed environment, dry run (default)
clean_room/macos.sh --for-real         # let it install (into a temp HOME, deleted on exit)
clean_room/macos.sh --use-host-ollama  # don't hide the Ollama already running here
clean_room/macos.sh --vm               # a real disposable macOS VM via tart
```

**Lite mode** runs `setup.py` (not `setup.sh`, so it tests the model half) through `env -i`
under `/usr/bin/python3` — the Command Line Tools
interpreter, 3.9.x — with `HOME` in a temp directory and `PATH` cut back to `/usr/bin:/bin`. So
the process sees no Homebrew, no uv, no shell profile and (by default) no Ollama, which is a fair
imitation of a fresh Mac and costs nothing. Expect `python: 3.9.6 — the course needs 3.12 or
newer` and the uv fallback in the plan.

Its two blind spots: it cannot test that `brew install ollama` works, and it cannot hide
`/Applications`, so if you have Ollama.app the plan says `open -a Ollama` where a clean Mac
would say `ollama serve &`.

**VM mode** needs Apple Silicon and [tart](https://tart.run) (`brew install cirruslabs/cli/tart`).
It asks before downloading ~50 GB. The repo is shared into the VM at
`/Volumes/My Shared Files/course`; log in as `admin` / `admin`. Check `brew --version` inside
before drawing conclusions — if the image ships Homebrew, it is not a no-package-manager test.
Throw it away with `tart delete agentfix-clean-room`.

## Windows

Two routes, and neither runs from your Mac.

**Free CI, no VM** — a brand new Windows per run:

```bash
cp clean_room/github-windows.yml .github/workflows/clean-room-windows.yml
git add .github/workflows/clean-room-windows.yml && git commit -m "chore: windows clean room" && git push
# then: GitHub → Actions → "clean room: Windows" → Run workflow
```

Note the runners have **no winget** (they are Server images), so this exercises the uv fallback,
not the winget path — which is itself worth testing, since locked-down corporate Windows behaves
the same way. Tick `install_ollama` to have it fetch Ollama's silent installer so the model steps
run for real; keep the tier on `qwen`.

**A real Windows** — Windows Sandbox (Win 11 Pro, resets on close), or a Windows 11 ARM64 VM in
UTM or Parallels. Snapshot it first. Then, inside that machine at the course root:

```powershell
powershell -ExecutionPolicy Bypass -File clean_room\windows.ps1 -OldPython   # install 3.11 first
# open a NEW PowerShell so PATH updates, then:
powershell -ExecutionPolicy Bypass -File clean_room\windows.ps1 -Tier qwen
```

It prints what the machine has (including the RAM figure `setup.py` reads through
`GlobalMemoryStatusEx`), runs setup, then tells you the three Windows-only things to verify by
hand: the `setx`-written `MELLUM_MODEL`, `.agentfix.env`, and `ollama ps` showing CONTEXT 16384.

`windows.ps1` has been parsed with the real PowerShell parser, but it has **not** been executed
on Windows — `Get-CimInstance` and friends only exist there. Treat the first run as part of the
test.

## What none of these cover

The IDE. Every clean room here runs `setup.py` from a terminal; none of them reproduce
JetBrains Academy provisioning an interpreter and installing `requirements.txt`. If a learner's
problem is "the IDE's Python is not the one setup configured", these will not show it.
