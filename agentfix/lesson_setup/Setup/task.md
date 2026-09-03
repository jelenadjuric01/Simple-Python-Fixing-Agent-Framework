# Setting up: one command

    ./setup.sh                                            # macOS, Linux, ChromeOS, WSL2
    powershell -ExecutionPolicy Bypass -File setup.ps1    # Windows

If `./setup.sh` says *permission denied*, run `sh setup.sh` instead.

That is the setup. It works out which models this machine can run, then brings it to the state
`python run.py doctor` calls READY: Python 3.12 if your interpreter is older, Ollama, both
models — the coding one the first two lessons use and the *thinking* one the third needs — and,
the step everyone skips, the derived model per checkpoint that carries the 16,384-token context
window the agent needs. It shows you every command and asks before running it, and it is the same
command on macOS, Linux, WSL2 and native Windows.

Dependencies for the local IDE path come from `requirements.txt`, which the IDE installs for
you. `setup.py` does not touch your interpreter's packages; it only sets up the model.


### Which tier are you?

| Tier | Best for | RAM | Disk | Models | Status |
|---|---|---:|---:|---|---|
| `mellum2` (default) | laptops that can comfortably run Mellum2 | 16 GB+ | ~18 GB | `agentfix-mellum2` (Instruct, 8 GB) + `agentgraph-mellum2-thinking` (Thinking, 8 GB) | reference path |
| `qwen` | laptops that cannot hold an 8 GB model | 8–16 GB | ~4 GB | `agentfix-qwen` (`qwen2.5-coder:1.5b`) + `agentgraph-qwen3` (`qwen3:1.7b`) | local fallback |
| `colab` | Chromebooks, thin laptops, anyone who prefers a browser | under 8 GB, or any | — | Google Colab notebook — `notebooks/agentfix.ipynb` | **tested** |

**Two models per tier, and the RAM number is still about one of them.** The course has two kinds
of agent in it: the first two editions run a *coding* model, and the third runs a *thinking* one.
So every tier installs a pair, and setup pulls both in the same run — nobody should meet an 8 GB
download in the middle of the lesson that needs it.

What that pair costs is **disk** (about 18 GB on the default tier), not RAM. The lessons run one
after the other, so only one model is ever needed at a time, and 16 GB is the line for holding
*one* 8 GB model plus the OS. The exception is worth knowing: Ollama keeps the last model loaded
for five minutes after the final request, so moving straight from lesson 3 to lesson 4 can leave
both resident at once. One command fixes it —

```bash
ollama stop agentfix-mellum2        # before you start the thinking lesson
```

— or cap it permanently in the **server's** environment with `OLLAMA_MAX_LOADED_MODELS=1`
(`setup.py` does that for a server it starts itself; the macOS menu-bar app needs
`launchctl setenv OLLAMA_MAX_LOADED_MODELS 1` and a restart).

`./setup.sh` reads this machine's RAM and chooses: `mellum2` at 16 GB or more, `qwen` from
8 GB up, and below 8 GB it says so and sends you to Colab rather than installing a model that
cannot fit. It also prints how much free disk the tier's two pulls need against how much you
have, before starting, because Ollama fails part-way through a pull rather than up front. That
RAM floor is not theoretical — a 3.4 GB Chromebook is a real machine a learner brought to this
workshop, and the IDE plus an Ollama server plus a 16,384-token context does not fit in it.
Override any of it whenever you want:

```bash
./setup.sh --tier qwen      # force the small model, even under the floor
./setup.sh --tier colab     # print the notebook pointer and exit
./setup.sh --dry-run        # print the plan and change nothing
./setup.sh --yes            # assume yes at every prompt — for a pre-session run
./setup.sh --no-shell-env   # do not touch your shell profile
```

Every flag is passed straight through to `setup.py`, so the same ones work on Windows after
`-File setup.ps1 --`.
### What setup does

`setup.sh` / `setup.ps1` handles step 1, which is the only per-OS part. `setup.py` does the rest,
and is the same code on every platform.

1. Gets a Python 3.12. Your package manager first (`brew`, `apt`, `dnf`, `pacman`, `winget`) —
   and this genuinely differs by distro: Ubuntu 24.04 has a `python3.12` package, Ubuntu 22.04
   and Debian have none, while Debian 13 and Fedora already ship something newer than 3.12 as
   their default `python3`. Where the package manager cannot produce one, it falls back to
   [uv](https://docs.astral.sh/uv/) and fetches a real CPython with `uv python install 3.12`.
   That is uv as an interpreter installer and nothing more: `requirements.txt` is still pip's
   job, in a virtual environment, installed by the IDE.
2. Installs Ollama, if `ollama` is not already on your PATH.
3. Starts the Ollama server and waits until it answers on `localhost:11434`.
4. Pulls the tier's **two** base models: the Instruct checkpoint the first two lessons run on,
   then the Thinking checkpoint the third one is about.
5. Derives one model per checkpoint with `PARAMETER num_ctx 16384` — `agentfix-mellum2` and
   `agentgraph-mellum2-thinking` (or `agentfix-qwen` and `agentgraph-qwen3`). Only `Modelfile`
   is committed to the repo; the other three are written into the course root as
   `Modelfile.agentgraph-thinking`, `Modelfile.agentfix-qwen`, `Modelfile.agentgraph-qwen3`,
   because `ollama create` reads them from the directory it runs in.
6. Records the model choice: `.agentfix.env`, which `run.py` reads, plus the same variables in
   your user environment for terminals you open later — `setx` on Windows, one marked block in
   your shell profile on macOS and Linux. `--no-shell-env` skips that half.

   Two variables, because the two models are different kinds: **`MELLUM_MODEL`** for the
   `agentfix` and `agentlang` lessons, **`AGENTGRAPH_MODEL`** for the thinking one. One variable
   could not do both jobs — on the small tier `MELLUM_MODEL=agentfix-qwen` names a model with no
   thinking mode, and pointing lesson 4 at it produces a working agent with no reasoning in it
   and no error anywhere. The mellum2 tier needs neither variable (both derived names already
   are those editions' defaults) and setup *removes* them, in case an earlier `--tier qwen` run
   left them behind.


#### Do not skip the derived model

Why `ollama create` with a `Modelfile` instead of setting a server environment variable? Because
the environment-variable route only works if you can get the variable into the **server
process's** environment, which is a different command on every platform (your own terminal,
`launchctl` on macOS, `systemctl edit` on Linux, `setx` on Windows) — and if you get it into the
wrong process, Ollama silently reports its default 4,096-token context window instead of telling
you it ignored you. At 4,096 tokens a long run quietly loses its own history mid-task, which
looks like a stupid model rather than a misconfigured one. The `Modelfile` route
(`PARAMETER num_ctx 16384`) is one command, identical on every platform, and it survives
whichever endpoint the client talks to. That is why this course derives the model.

<details>
<summary><b>If <code>setup.py</code> could not finish — the same steps by hand</b></summary>

`setup.py` prints the command it was about to run whenever it stops, so the fastest path is
usually to run that one command yourself and start it again. The full sequence, per platform:

**macOS**

```bash
brew install ollama
brew services start ollama       # or: open -a Ollama, if you installed the app instead
```

Homebrew's `ollama` formula and the Ollama app are the same server on `localhost:11434` — use
either, but not both at once. Without Homebrew, install from
[ollama.com/download](https://ollama.com/download).

**Linux and WSL2**

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl start ollama      # no systemd (common in WSL2): ollama serve &
```

The install script registers a systemd service, so the server is usually already listening;
`systemctl status ollama` tells you. A GPU is not required — CPU inference works, it is just
slower than the numbers below.

**Windows — WSL2 (recommended)**

In PowerShell, once:

```powershell
wsl --install -d Ubuntu
```

Then follow the Linux steps inside the Ubuntu shell and do everything else — `ollama`, the
exercises — inside WSL2. Keep the clone on the Linux filesystem (`~/agentfix-workshop`, not
`/mnt/c/...`); test discovery across the `/mnt/c` bridge is slow enough to be annoying.

WSL2 gets a fraction of your total RAM by default (50%, capped at 8 GB on older builds), and
that fraction — not your machine's spec sheet — is what has to hold an 8 GB model, so it is also
the number `setup.py` picks the tier from. If `free -g` inside WSL2 shows less than 16 GB, raise
it in `%UserProfile%\.wslconfig`:

```ini
[wsl2]
memory=16GB
```

then `wsl --shutdown` in PowerShell, reopen the shell, and run `./setup.sh` again.

**Windows — native PowerShell** (works for the exercises; sandbox untested)

```powershell
winget install -e --id Ollama.Ollama
ollama serve                     # or start the Ollama tray app
```

**Python 3.12 where `apt` has no candidate for it** (Ubuntu 22.04, Debian, ChromeOS):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12
uv python find 3.12                 # prints the path setup.py would re-execute itself with
```

To give the course an environment with pip in it, built on that interpreter:

```bash
uv venv --python 3.12 --seed .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**The models, on every platform.** Two per tier: one that codes, one that thinks.

Mellum2 — the default tier. `Modelfile` is in the repo; the Thinking one is generated, and every
generated `Modelfile.*` goes in the **course root**, because native Windows has no `/tmp` and
`ollama create` reads the file from the directory it runs in:

```bash
ollama pull hf.co/JetBrains/Mellum2-12B-A2.5B-Instruct-GGUF-Q4_K_M
ollama create agentfix-mellum2 -f Modelfile

ollama pull hf.co/JetBrains/Mellum2-12B-A2.5B-Thinking-GGUF-Q4_K_M
printf 'FROM hf.co/JetBrains/Mellum2-12B-A2.5B-Thinking-GGUF-Q4_K_M\nPARAMETER num_ctx 16384\n' \
  > Modelfile.agentgraph-thinking
ollama create agentgraph-mellum2-thinking -f Modelfile.agentgraph-thinking
```

Qwen — the fallback, and it takes two *different* models. `qwen2.5-coder:1.5b` has no thinking
mode at all, so the thinking lesson gets `qwen3:1.7b`, the smallest thing that both reasons and
calls tools:

```bash
ollama pull qwen2.5-coder:1.5b
printf 'FROM qwen2.5-coder:1.5b\nPARAMETER num_ctx 16384\n' > Modelfile.agentfix-qwen
ollama create agentfix-qwen -f Modelfile.agentfix-qwen
export MELLUM_MODEL=agentfix-qwen

ollama pull qwen3:1.7b
printf 'FROM qwen3:1.7b\nPARAMETER num_ctx 16384\n' > Modelfile.agentgraph-qwen3
ollama create agentgraph-qwen3 -f Modelfile.agentgraph-qwen3
export AGENTGRAPH_MODEL=agentgraph-qwen3
```

```powershell
ollama pull qwen2.5-coder:1.5b
Set-Content Modelfile.agentfix-qwen @('FROM qwen2.5-coder:1.5b', 'PARAMETER num_ctx 16384')
ollama create agentfix-qwen -f Modelfile.agentfix-qwen
$env:MELLUM_MODEL = 'agentfix-qwen'

ollama pull qwen3:1.7b
Set-Content Modelfile.agentgraph-qwen3 @('FROM qwen3:1.7b', 'PARAMETER num_ctx 16384')
ollama create agentgraph-qwen3 -f Modelfile.agentgraph-qwen3
$env:AGENTGRAPH_MODEL = 'agentgraph-qwen3'
```

Those `export` lines last for that one terminal session — `unset MELLUM_MODEL` in a POSIX
shell, `Remove-Item Env:\MELLUM_MODEL` in PowerShell, `set MELLUM_MODEL=` in `cmd.exe` — which
is exactly the thing `setup.py` writes `.agentfix.env` to avoid. Qwen is smaller and faster, but
noticeably less reliable at multi-step tool use than Mellum2: expect more steps, or a task it
cannot fix. Good enough to see the loop work; not the demo model.
</details>

### The Colab tier

`notebooks/agentfix.ipynb` is the browser path, and it does **one thing**: it runs the three
finished agents against a real model. Ollama, both models and all three repositories live in the
Colab runtime rather than on your laptop. This path has been tested end to end.

#### What the Colab tier does and does not replace

It is not a course substitute. Every lesson still happens here, in the IDE, on your own machine:
you read the code, and you write the parts no framework writes for you — the graph's routing and
the loop guard in lesson 3, and what counts as acting, the idle counter, the nudge and the routing
tail in lesson 4. Those are graded by tests that run against a scripted **fake** model, so they
need no model, no Ollama and no network, and they pass on any machine.

The notebook covers only the step that genuinely needs a real model: `doctor`, `solve` and `eval`
for each edition, in the same order as the lessons, with the small `qwen` pair standing in for the
Mellum2 pair.

So, on this tier:

1. Do the lessons and their exercises here, as normal.
2. **Skip `python run.py doctor` on this machine** — there is no model here to check, and it will
   fail. That is expected on this tier, not a broken setup.
3. When a lesson says "run it for real", open the notebook and run it top to bottom.

The notebook checks out the reference solution of each exercise file so the agents are guaranteed
to run. That cannot touch your work: it happens in the Colab runtime, on its own clones, and the
runtime is discarded when the session ends.
