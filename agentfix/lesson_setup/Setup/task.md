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

#### If setup could not finish — the same steps by hand

`setup.py` prints the command it was about to run whenever it stops, so the fastest path is
usually to run that one command yourself and start the script again.

If you would rather do the whole thing by hand, open your operating system below. Each block is
the complete sequence, in the order `setup.py` does it, and nothing in it depends on the other
blocks. Every tier is **two** models — a coding one for the `agentfix` and `agentlang` lessons and
a thinking one for `agentgraph` — so steps 4 and 5 happen twice.

<details>
<summary><b>macOS</b></summary>

**1. Python 3.12 or newer**

```bash
brew install python@3.12
```

No Homebrew? Install [uv](https://docs.astral.sh/uv/) and let it fetch a real CPython:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12
uv python find 3.12                 # the path setup.py would re-execute itself with
```

**2. Install Ollama**

```bash
brew install ollama
```

Without Homebrew, download the app from [ollama.com/download](https://ollama.com/download).

**3. Start the server**

```bash
brew services start ollama          # Homebrew install
open -a Ollama                      # the app instead
```

The formula and the app are the same server on `localhost:11434` — use either, but not both at
once. `curl http://localhost:11434/api/version` confirms it is up.

**4. Pull and derive the coding model** — for the `agentfix` and `agentlang` lessons.

Mellum2 Instruct, the default tier:

```bash
ollama pull hf.co/JetBrains/Mellum2-12B-A2.5B-Instruct-GGUF-Q4_K_M
ollama create agentfix-mellum2 -f Modelfile
```

Or Qwen, the fallback for machines that cannot hold an 8 GB model:

```bash
ollama pull qwen2.5-coder:1.5b
printf 'FROM qwen2.5-coder:1.5b\nPARAMETER num_ctx 16384\n' > Modelfile.agentfix-qwen
ollama create agentfix-qwen -f Modelfile.agentfix-qwen
```

Do not stop after the `pull`. The `create` is what carries the 16,384-token context window.

**5. Pull and derive the thinking model** — for the `agentgraph` lesson.

Mellum2 Thinking, the default tier. Same weights as the Instruct model above, trained to reason
before it answers, and a separate 8 GB download:

```bash
ollama pull hf.co/JetBrains/Mellum2-12B-A2.5B-Thinking-GGUF-Q4_K_M
printf 'FROM hf.co/JetBrains/Mellum2-12B-A2.5B-Thinking-GGUF-Q4_K_M\nPARAMETER num_ctx 16384\n' \
  > Modelfile.agentgraph-thinking
ollama create agentgraph-mellum2-thinking -f Modelfile.agentgraph-thinking
```

Or, on the small tier, `qwen3:1.7b` — **not** the `qwen2.5-coder` from step 4, which has no
thinking mode at all:

```bash
ollama pull qwen3:1.7b
printf 'FROM qwen3:1.7b\nPARAMETER num_ctx 16384\n' > Modelfile.agentgraph-qwen3
ollama create agentgraph-qwen3 -f Modelfile.agentgraph-qwen3
```

**6. Only on the Qwen tier — say which models to use**

```bash
printf 'MELLUM_MODEL=agentfix-qwen\nAGENTGRAPH_MODEL=agentgraph-qwen3\n' > .agentfix.env
export MELLUM_MODEL=agentfix-qwen AGENTGRAPH_MODEL=agentgraph-qwen3   # this terminal only
```

`.agentfix.env` in the course root is what `run.py` reads, which is why setup writes it: an
`export` line is gone the moment you close the terminal. Two variables, because the two models are
different kinds — `MELLUM_MODEL` for the coding lessons, `AGENTGRAPH_MODEL` for the thinking one.
The default Mellum2 tier needs neither: both derived names above already are those lessons'
defaults.

**7. Check it**

```bash
python run.py doctor                # the coding model
python run.py agentgraph doctor     # the thinking model
```

On a 16 GB machine, `ollama stop agentfix-mellum2` between those two. Ollama keeps the last model
loaded for five minutes, and two 8 GB models at once is what makes a correctly set-up laptop start
swapping. Permanently: `launchctl setenv OLLAMA_MAX_LOADED_MODELS 1`, then restart Ollama.
</details>

<details>
<summary><b>Linux, WSL2 and ChromeOS</b></summary>

**1. Python 3.12 or newer**

Ask your package manager for the exact series first, and fall back to its default `python3` —
which distro needs which genuinely differs:

```bash
sudo apt-get update && sudo apt-get install -y python3.12    # Ubuntu 24.04
sudo apt-get install -y python3                              # Debian 13: already newer than 3.12
sudo dnf install -y python3.12                               # Fedora: python3 is also fine
sudo pacman -S --needed --noconfirm python                   # Arch is rolling; no 3.12 package
```

Ubuntu 22.04 and Debian 12 have no `python3.12` package **and** a `python3` that is too old. There,
use uv, which fetches a real CPython:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12
uv python find 3.12                 # the path setup.py would re-execute itself with
```

That is uv as an interpreter installer and nothing more. To turn that interpreter into an
environment with pip in it:

```bash
uv venv --python 3.12 --seed .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**2. Install Ollama**

```bash
sudo apt-get install -y curl zstd   # the install script needs both; a fresh Debian has neither
curl -fsSL https://ollama.com/install.sh | sh
```

**3. Start the server**

```bash
sudo systemctl start ollama         # the install script registers this service
ollama serve &                      # no systemd — common inside WSL2
```

`systemctl status ollama` usually shows it already listening. A GPU is not required; CPU inference
works, it is just slower.

**4. Pull and derive the coding model** — for the `agentfix` and `agentlang` lessons.

Mellum2 Instruct, the default tier:

```bash
ollama pull hf.co/JetBrains/Mellum2-12B-A2.5B-Instruct-GGUF-Q4_K_M
ollama create agentfix-mellum2 -f Modelfile
```

Or Qwen, the fallback:

```bash
ollama pull qwen2.5-coder:1.5b
printf 'FROM qwen2.5-coder:1.5b\nPARAMETER num_ctx 16384\n' > Modelfile.agentfix-qwen
ollama create agentfix-qwen -f Modelfile.agentfix-qwen
```

Do not stop after the `pull`. The `create` is what carries the 16,384-token context window.

**5. Pull and derive the thinking model** — for the `agentgraph` lesson.

Mellum2 Thinking, the default tier. Same weights as the Instruct model above, trained to reason
before it answers, and a separate 8 GB download:

```bash
ollama pull hf.co/JetBrains/Mellum2-12B-A2.5B-Thinking-GGUF-Q4_K_M
printf 'FROM hf.co/JetBrains/Mellum2-12B-A2.5B-Thinking-GGUF-Q4_K_M\nPARAMETER num_ctx 16384\n' \
  > Modelfile.agentgraph-thinking
ollama create agentgraph-mellum2-thinking -f Modelfile.agentgraph-thinking
```

Or, on the small tier, `qwen3:1.7b` — **not** the `qwen2.5-coder` from step 4, which has no
thinking mode at all:

```bash
ollama pull qwen3:1.7b
printf 'FROM qwen3:1.7b\nPARAMETER num_ctx 16384\n' > Modelfile.agentgraph-qwen3
ollama create agentgraph-qwen3 -f Modelfile.agentgraph-qwen3
```

**6. Only on the Qwen tier — say which models to use**

```bash
printf 'MELLUM_MODEL=agentfix-qwen\nAGENTGRAPH_MODEL=agentgraph-qwen3\n' > .agentfix.env
export MELLUM_MODEL=agentfix-qwen AGENTGRAPH_MODEL=agentgraph-qwen3   # this terminal only
```

`.agentfix.env` in the course root is what `run.py` reads, which is why setup writes it: an
`export` line is gone the moment you close the terminal. Two variables, because the two models are
different kinds — `MELLUM_MODEL` for the coding lessons, `AGENTGRAPH_MODEL` for the thinking one.
The default Mellum2 tier needs neither.

**7. Check it**

```bash
python run.py doctor                # the coding model
python run.py agentgraph doctor     # the thinking model
```

On a 16 GB machine, `ollama stop agentfix-mellum2` between those two. Ollama keeps the last model
loaded for five minutes, and two 8 GB models at once is what makes a correctly set-up laptop start
swapping. Permanently, in the server's own environment: `OLLAMA_MAX_LOADED_MODELS=1`, via
`sudo systemctl edit ollama`.

**WSL2 only — give it enough RAM**

WSL2 gets a fraction of your total RAM by default (50%, capped at 8 GB on older builds), and that
fraction — not your machine's spec sheet — is what has to hold an 8 GB model. It is also the number
`setup.py` picks the tier from. If `free -g` shows less than 16 GB, raise it from PowerShell in
`%UserProfile%\.wslconfig`:

```ini
[wsl2]
memory=16GB
```

Then `wsl --shutdown`, reopen the Ubuntu shell, and run `./setup.sh` again.
</details>

<details>
<summary><b>Windows — WSL2 (recommended)</b></summary>

WSL2 is the recommended Windows path: everything below the first command is plain Linux, and the
sandbox that runs the agent's tests works properly there.

**1. Install Ubuntu**, once, in PowerShell:

```powershell
wsl --install -d Ubuntu
```

**2. Do everything else inside the Ubuntu shell** — Ollama, both models, the exercises — following
the **Linux, WSL2 and ChromeOS** block above, including its RAM note at the end.

Keep the course on the Linux filesystem (`~/agentfix-workshop`, not `/mnt/c/...`). Test discovery
across the `/mnt/c` bridge is slow enough to be annoying.
</details>

<details>
<summary><b>Windows — native PowerShell</b></summary>

Works for setup and the exercises. The sandbox that runs the agent's tests is untested here — use
WSL2 if that matters to you.

**1. Python 3.12 or newer**

```powershell
winget install -e --id Python.Python.3.12
```

**2. Install Ollama**

```powershell
winget install -e --id Ollama.Ollama
```

**3. Start the server**

```powershell
ollama serve
```

Or start the Ollama tray app, which is the same server. If PowerShell says `ollama` is not
recognized straight after installing, open a **new** terminal — the installer's `PATH` change only
reaches processes started afterwards.

**4. Pull and derive the coding model** — for the `agentfix` and `agentlang` lessons.

Mellum2 Instruct, the default tier:

```powershell
ollama pull hf.co/JetBrains/Mellum2-12B-A2.5B-Instruct-GGUF-Q4_K_M
ollama create agentfix-mellum2 -f Modelfile
```

Or Qwen, the fallback. Every generated Modelfile goes in the course root, because native Windows
has no `/tmp`:

```powershell
ollama pull qwen2.5-coder:1.5b
Set-Content Modelfile.agentfix-qwen @('FROM qwen2.5-coder:1.5b', 'PARAMETER num_ctx 16384')
ollama create agentfix-qwen -f Modelfile.agentfix-qwen
```

Do not stop after the `pull`. The `create` is what carries the 16,384-token context window.

**5. Pull and derive the thinking model** — for the `agentgraph` lesson.

Mellum2 Thinking, the default tier. Same weights as the Instruct model above, trained to reason
before it answers, and a separate 8 GB download:

```powershell
ollama pull hf.co/JetBrains/Mellum2-12B-A2.5B-Thinking-GGUF-Q4_K_M
Set-Content Modelfile.agentgraph-thinking @(
  'FROM hf.co/JetBrains/Mellum2-12B-A2.5B-Thinking-GGUF-Q4_K_M', 'PARAMETER num_ctx 16384')
ollama create agentgraph-mellum2-thinking -f Modelfile.agentgraph-thinking
```

Or, on the small tier, `qwen3:1.7b` — **not** the `qwen2.5-coder` from step 4, which has no
thinking mode at all:

```powershell
ollama pull qwen3:1.7b
Set-Content Modelfile.agentgraph-qwen3 @('FROM qwen3:1.7b', 'PARAMETER num_ctx 16384')
ollama create agentgraph-qwen3 -f Modelfile.agentgraph-qwen3
```

**6. Only on the Qwen tier — say which models to use**

```powershell
Set-Content .agentfix.env @('MELLUM_MODEL=agentfix-qwen', 'AGENTGRAPH_MODEL=agentgraph-qwen3')

$env:MELLUM_MODEL = 'agentfix-qwen'                        # this terminal only
$env:AGENTGRAPH_MODEL = 'agentgraph-qwen3'
setx MELLUM_MODEL agentfix-qwen                            # future terminals, user-wide
setx AGENTGRAPH_MODEL agentgraph-qwen3
```

`.agentfix.env` in the course root is what `run.py` reads, which is why setup writes it. Two
variables, because the two models are different kinds — `MELLUM_MODEL` for the coding lessons,
`AGENTGRAPH_MODEL` for the thinking one. `$env:` lasts for that one session
(`Remove-Item Env:\MELLUM_MODEL` clears it; in `cmd.exe` the equivalents are
`set MELLUM_MODEL=agentfix-qwen` and `set MELLUM_MODEL=`). `setx` persists, but **only for
processes started afterwards** — close and reopen PyCharm and your terminals, or they will keep the
environment they were launched with. The default Mellum2 tier needs none of this.

**7. Check it**

```powershell
python run.py doctor                # the coding model
python run.py agentgraph doctor     # the thinking model
```

On a 16 GB machine, `ollama stop agentfix-mellum2` between those two: Ollama keeps the last model
loaded for five minutes, and two 8 GB models at once is what makes a correctly set-up laptop start
swapping.
</details>

Qwen is smaller and faster than Mellum2, but noticeably less reliable at multi-step tool use:
expect more steps, or a task it cannot fix. Good enough to see the loop work; not the demo model.

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
