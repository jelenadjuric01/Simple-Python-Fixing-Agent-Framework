# Setting up: one command

    ./setup.sh                                            # macOS, Linux, ChromeOS, WSL2
    powershell -ExecutionPolicy Bypass -File setup.ps1    # Windows

If `./setup.sh` says *permission denied*, run `sh setup.sh` instead.

That is the setup. It works out which model this machine can run, then brings it to the state
`python run.py doctor` calls READY: Python 3.12 if your interpreter is older, Ollama, the model,
and — the step everyone skips — the derived model that carries the 16,384-token context window
the agent needs. It shows you every command and asks before running it, and it is the same
command on macOS, Linux, WSL2 and native Windows.

Dependencies for the local IDE path come from `requirements.txt`, which the IDE installs for
you. `setup.py` does not touch your interpreter's packages; it only sets up the model.


### Which tier are you?

| Tier | Best for | RAM on learner machine | Model environment | Status |
|---|---|---:|---|---|
| `mellum2` (default) | laptops that can comfortably run Mellum2 | 16 GB+ | local Ollama, `http://localhost:11434/v1` | reference path |
| `qwen` | laptops that cannot hold the 8 GB Mellum2 model | 8–16 GB | `qwen2.5-coder:1.5b` locally through Ollama | local fallback |
| `colab` | Chromebooks, thin laptops, anyone who prefers a browser | under 8 GB, or any | Google Colab notebook — `notebooks/agentfix.ipynb` | **tested** |

`./setup.sh` reads this machine's RAM and chooses: `mellum2` at 16 GB or more, `qwen` from
8 GB up, and below 8 GB it says so and sends you to Colab rather than installing a model that
cannot fit. That floor is not theoretical — a 3.4 GB Chromebook is a real machine a learner
brought to this workshop, and the IDE plus an Ollama server plus a 16,384-token context does not
fit in it. Override any of it whenever you want:

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
4. Pulls the tier's base model.
5. Derives `agentfix-mellum2` (or `agentfix-qwen`) from it with `PARAMETER num_ctx 16384`.
6. Records the model choice: `.agentfix.env`, which `run.py` reads, plus `MELLUM_MODEL` in your
   user environment for terminals you open later — `setx` on Windows, a marked block in your
   shell profile on macOS and Linux. `--no-shell-env` skips that half, and the mellum2 tier
   removes both, because `agentfix-mellum2` is already the default.


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

**The models, on every platform.** Mellum2:

```bash
ollama pull hf.co/JetBrains/Mellum2-12B-A2.5B-Instruct-GGUF-Q4_K_M
ollama create agentfix-mellum2 -f Modelfile
```

Qwen — the fallback. `Modelfile.agentfix-qwen` goes in the course root, because native Windows
has no `/tmp`:

```bash
ollama pull qwen2.5-coder:1.5b
printf 'FROM qwen2.5-coder:1.5b\nPARAMETER num_ctx 16384\n' > Modelfile.agentfix-qwen
ollama create agentfix-qwen -f Modelfile.agentfix-qwen
export MELLUM_MODEL=agentfix-qwen
```

```powershell
ollama pull qwen2.5-coder:1.5b
Set-Content Modelfile.agentfix-qwen @('FROM qwen2.5-coder:1.5b', 'PARAMETER num_ctx 16384')
ollama create agentfix-qwen -f Modelfile.agentfix-qwen
$env:MELLUM_MODEL = 'agentfix-qwen'
```

The `MELLUM_MODEL` line lasts for that one terminal session — `unset MELLUM_MODEL` in a POSIX
shell, `Remove-Item Env:\MELLUM_MODEL` in PowerShell, `set MELLUM_MODEL=` in `cmd.exe` — which
is exactly the thing `setup.py` writes `.agentfix.env` to avoid. Qwen is smaller and faster, but
noticeably less reliable at multi-step tool use than Mellum2: expect more steps, or a task it
cannot fix. Good enough to see the loop work; not the demo model.
</details>

### The Colab tier

Use `notebooks/agentfix.ipynb` for the browser-based Google Colab path. The model, Ollama
process, repository, edits, and test commands run inside the Colab runtime rather than on the
learner's laptop. This path has been tested end to end.

#### How the Colab tier changes the Build the agent lesson

Google Colab is not a drop-in replacement for the IDE setup. Learners still build the same three
pieces of the agent in the same files:

1. the `run_tests` tool and its JSON schema,
2. the loop's tool dispatch,
3. the verification-based stop condition.

What changes is the workflow. Instead of following the IDE lesson literally, Colab users edit the
repository files from the notebook environment and run the exercise tests from notebook cells.
IDE-specific checks, file-navigation instructions, and terminal steps in **Build the agent** need
their Colab equivalents.

Keep the three stages in the same order; only the environment and lesson instructions change.
