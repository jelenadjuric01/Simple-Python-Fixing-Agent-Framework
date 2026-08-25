# agentfix

A teaching repository for a workshop that shows developers new to agents how a coding
agent actually works, by having them build one. You write three pieces of a real agent yourself —
a tool and its JSON schema, the loop's tool dispatch, and a verification-based stop condition —
then watch it fix real bugs with a real model. The default path uses [JetBrains
Mellum2](https://huggingface.co/JetBrains/Mellum2-12B-A2.5B-Instruct-GGUF) locally through Ollama;
a smaller local Qwen model is the fallback, and browser notebook paths are available for learners
who cannot run either model comfortably on their own machine. There is no framework: the loop
itself is about 15 lines and the rest of `run_agent` is tracing and token accounting.

Every exercise test runs against a scripted fake model, so the core workshop does not depend on
real inference working. The local-model paths preserve the IDE lesson as written. The notebook
paths are different: they are untested and require a notebook-specific version of the **Build the
agent** lesson because learners edit and run the agent from the notebook environment instead of
following the IDE flow unchanged.

## Start here

This README is the single reference for the workshop. If you are taking the course in the IDE,
the wording in the setup, build, real-model, and next-step sections intentionally stays close to
the lesson text so it is easy to recognize where you are.

### Workshop path

1. [What you are about to build](#what-you-are-about-to-build)
2. [What is an agent?](#what-is-an-agent)
3. [Understand the repository](#what-will-you-find-in-this-repo-and-how-to-understand-it)
4. [Set up the model](#setting-up-one-command)
5. [Point it at a real model](#now-point-it-at-a-real-model)
6. [Learn the command-line workflow](#command-reference)
7. [Use the reference sections when you need them](#reference)
8. [Continue with next steps](#next-steps)

---

## What you are about to build

The whole agent is in front of you. Every file is readable — the comments are part of the
lesson — but only two files are yours to edit, and you will edit them one piece at a time:

| Stage | You write | File |
|---|---|---|
| 1 | the `run_tests` tool and its JSON schema | `agentfix/tools/tests_tool.py` |
| 2 | the loop's tool dispatch | `agentfix/agent/loop.py` |
| 3 | the stop condition | `agentfix/agent/loop.py` |

Three ideas, in order: an agent needs **tools**, a **loop** that feeds tool results back to
the model, and a way to know when it is **done** that does not depend on the model's opinion.

Everything else — the CLI, the sandbox, the tracer, the task loader — is already written and
locked. Read as much of it as you like. 

## What is an agent?

Loop, tools, verification.

An agent is a while-loop around a chat model that can call functions and sees the result.
Nothing more magical than that.

That definition is not a simplification for this course — it is literally what you are about
to build. The loop in this repo is about 15 lines. The rest of `run_agent` is tracing and token
accounting: bookkeeping around the loop, not the loop itself.

Keep this in your head as you go through the next few steps and into the framework lesson: at
every point where the mechanics feel like they are piling up, ask which of the three ideas —
loop, tools, verification — the code in front of you belongs to.

### What will you find in this repo and how to understand it?


If you would like to see a presentation that goes with this workshop, go to: [link](https://docs.google.com/presentation/d/1ky_-18N9A2r5ysYGqia9yVgu9yuP9o6pOVE6g6Hy0ns/edit?usp=sharing). This is optional.

```text
agentfix/
├── agent/
│   ├── loop.py
│   └── trace.py
│
├── eval/
│   ├── runner.py
│   └── humanevalfix.py
│
├── llm/
│   ├── client.py
│   ├── fake.py
│   └── types.py
│
├── sandbox/
│   ├── base.py
│   ├── subprocess_backend.py
│   └── docker_backend.py
│
├── tasks/
│   └── loader.py
│
├── tools/
│   ├── base.py
│   ├── fs.py
│   └── tests_tool.py
│
├── config.py
├── runner.py
└── cli.py

results/
└── precomputed/
    ├── humanevalfix.json
    └── workshop.json

scripts/
└── vendor_humanevalfix.py

tasks/
├── humanevalfix/
│   └── subset.json
└── workshop/
    ├── 01-shopcart/
    ├── 02-invoice/
    └── 03-parser/

Dockerfile.sandbox
Modelfile
```

#### `agent/` — Agent logic

The core of the project. `loop.py` contains the actual agent loop: it sends the conversation to the model, executes requested tools, feeds results back, and continues until the tests pass or a limit is reached. `trace.py` records what happened during a run.

#### `llm/` — Model interface

Everything related to communicating with the language model. `client.py` contains the real OpenAI-compatible client used with Ollama/vLLM, while `fake.py` provides a deterministic fake model for testing. `types.py` defines the common interfaces and data structures used by both.

#### `tools/` — Agent capabilities

Defines what the model is allowed to do. `base.py` provides the tool abstraction and registry, `fs.py` implements filesystem tools such as listing, reading, and writing files, and `tests_tool.py` lets the agent run the project's tests.

#### `sandbox/` — Safe code execution

Controls where and how commands are executed. The default backend uses hardened subprocess execution, while the Docker backend provides stronger isolation when needed.

#### `tasks/` — Task loading

`loader.py` turns a task definition into a `Task` and creates a temporary workspace containing a fresh copy of the buggy project for the agent to modify.

#### `eval/` — Evaluation

Runs the agent across collections of tasks and records metrics such as pass rate, number of steps, token usage, and execution time. `humanevalfix.py` provides support for the HumanEvalFix benchmark subset.

#### Top-level `agentfix` modules

* `config.py` — model and environment configuration.
* `runner.py` — connects a task, workspace, tools, model client, and agent loop into one complete solve operation.
* `cli.py` — command-line interface for commands such as `solve` and `eval`.

#### `tasks/` — Buggy projects / benchmark inputs

Contains the actual tasks given to the agent. `workshop/` contains small example projects such as `01-shopcart`, `02-invoice`, and `03-parser`. `humanevalfix/` contains the selected HumanEvalFix benchmark tasks.

#### `results/` — Evaluation results

Stores precomputed evaluation outputs, allowing results to be inspected or compared without rerunning the entire benchmark.

#### `scripts/` — Project utilities

Contains development/helper scripts, such as `vendor_humanevalfix.py` for preparing the HumanEvalFix task data.

#### Runtime configuration

`Dockerfile.sandbox` defines the isolated Docker environment used by the Docker sandbox backend, while `Modelfile` configures the local Ollama model used by the agent.

#### Overall flow

```text
Task
  ↓
runner.py
  ↓
temporary workspace
  ↓
ToolRegistry + LLM client
  ↓
agent/loop.py
  ↓
run_tests → read files → write fix → run_tests
  ↓
AgentResult
  ↓
evaluation / results
```

The main idea is that **`agent/loop.py` decides what happens next, `llm/` talks to the model, `tools/` gives the model actions, and `sandbox/` executes those actions safely**. Everything else primarily prepares tasks, wires those pieces together, or evaluates the result.

---

## Setting up: one command

    ./setup.sh                                            # macOS, Linux, ChromeOS, WSL2
    powershell -ExecutionPolicy Bypass -File setup.ps1    # Windows

That is the setup. It works out which model this machine can run, then brings it to the state
`python run.py doctor` calls READY: Python 3.12 if your interpreter is older, Ollama, the model,
and — the step everyone skips — the derived model that carries the 16,384-token context window
the agent needs. It shows you every command and asks before running it, and it is the same
command on macOS, Linux, WSL2 and native Windows.

Dependencies for the local IDE path come from `requirements.txt`, which the IDE installs for
you. `setup.py` does not touch your interpreter's packages; it only sets up the model.

> If `./setup.sh` says *permission denied*, run `sh setup.sh` instead.

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

You can run `python3 setup.py` directly if you already have 3.12 and only want the model half;
the shell scripts exist for the interpreter.

Each step is checked before it runs and re-checked after, and the script stops at the first
thing it cannot fix, printing the command that would. Then:

```bash
python3 run.py doctor
```

`doctor` checks the same machine independently — including measuring the loaded context window,
which is the one setting nothing else will tell you about — and prints READY.

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

---

## Now point it at a real model

Every check so far ran against a scripted fake model. That was deliberate — the exercises
must not depend on your model setup. Now use a real model.

**The `mellum2` and `qwen` tiers (local):** run the commands below from the terminal at the
course root.

**The `colab` tier:** run the equivalent commands from notebook cells after you have completed
the Colab version of **Build the agent**. The commands and expected agent behavior are
the same, but the notebook environment replaces the IDE/terminal workflow.

    python run.py solve tasks/workshop/01-shopcart --verbose

Then the harder one, where the bug is **not** in the file the failing test points at — which
is why `list_files` and `read_file` earn their place:

    python run.py solve tasks/workshop/02-invoice --verbose

`--verbose` prints the trace you wired up in Stage 2. Read it. You should see the model call
`run_tests`, look around, write a file, and run the tests again. That last call is the one
that ends the run, because of what you wrote in Stage 3.

If it burns all ten steps and prints `NOT SOLVED`, that is not necessarily your bug — real
models do not fix every task, and the smaller Qwen fallback is less reliable at multi-step tool use
than Mellum2. 

You can try running a testing suite of all three workshop tasks. 
    
    python run.py eval --suite workshop --limit 3

No Check on this step. Nothing here is graded — it either fixes the bug or it does not, which
is rather the point of the whole course.

---

## Reference

The sections below are the deeper repository reference: commands, Docker isolation,
course/repository structure, custom tasks, measured performance, platform notes, and known
limitations. In the Course View, use `python run.py ...` from the course root; `run.py` finds the
guided project's working directory for you, so you do not need to `cd` into the hidden lesson
directory.

## Command reference

All learner commands start from the terminal at the **course root**:

```bash
python run.py <command> [arguments] [flags]
```

The examples below say `python`, which is what the IDE's terminal gives you once the course's
virtual environment is active. In a bare shell on Debian, ChromeOS or a fresh Linux there is no
`python` — use `python3` there, exactly as in the setup step above.

`run.py` passes agent commands through to the AgentFix CLI and also provides helpers for running
the project's `unittest` suite and building the Docker sandbox.

### Solve one task

```bash
python run.py solve <task_dir> [--verbose] [--max-steps N]
```

Examples:

```bash
python run.py solve tasks/workshop/01-shopcart
python run.py solve tasks/workshop/01-shopcart --verbose
python run.py solve tasks/workshop/02-invoice --max-steps 15 --verbose
```

| Argument / flag | Meaning |
|---|---|
| `<task_dir>` | Required path to the task directory, for example `tasks/workshop/01-shopcart`. |
| `--verbose` | Prints the agent trace: model turns, requested tool calls, tool results, and the path the agent took toward the final verdict. |
| `--max-steps N` | Sets the maximum number of agent-loop steps allowed for this run. The default comes from the loop's `MAX_STEPS` setting. Raising it gives the model more chances to inspect, edit, and verify; lowering it gives you a stricter budget. |

At the end, `solve` prints `SOLVED` or `NOT SOLVED`, together with the task id, steps used, token
count, and duration. The command exits successfully only when the task is solved.

### Evaluate a suite

```bash
python run.py eval [--suite workshop|humanevalfix] [--limit N]
```

Examples:

```bash
python run.py eval
python run.py eval --suite workshop --limit 3
python run.py eval --suite humanevalfix --limit 10
```

| Flag | Meaning |
|---|---|
| `--suite workshop` | Runs the workshop task suite. This is the default when `--suite` is omitted. |
| `--suite humanevalfix` | Runs the vendored HumanEvalFix subset instead of the workshop suite. |
| `--limit N` | Runs at most `N` tasks from the selected suite. The default is `3`, which keeps local evaluation reasonably short. |

Use `eval` when you want aggregate behavior over several tasks rather than the detailed trace of a
single solve.

### Build the Docker sandbox helper

From the course root:

```bash
python run.py docker-build
```

This runs the repository's Docker build command in the directory that contains
`Dockerfile.sandbox`.

Everything above runs the tests directly on your machine. The Docker path is different and narrower:
it swaps out **one thing** — how `run_tests` executes the task's test suite — via the
`AGENTFIX_SANDBOX` environment variable. The agent itself, the model client, and the file tools
still run on the host either way. See `agentfix/sandbox/` for why the boundary sits there.

### Run the agent with the container sandbox

```bash
# POSIX shells (macOS, Linux, WSL2) — inline, applies to this one command
AGENTFIX_SANDBOX=docker python run.py solve tasks/workshop/01-shopcart --verbose
AGENTFIX_SANDBOX=docker python run.py eval --suite workshop --limit 3

# ...or export it once for the whole shell session
export AGENTFIX_SANDBOX=docker
python run.py solve tasks/workshop/02-invoice --verbose
unset AGENTFIX_SANDBOX          # back to the subprocess backend
```

```powershell
# Windows PowerShell — its own line, before the command
$env:AGENTFIX_SANDBOX = 'docker'
python run.py solve tasks/workshop/01-shopcart --verbose
Remove-Item Env:\AGENTFIX_SANDBOX
```

### What the container actually gives you

The default subprocess backend is *hardened* — stripped environment, resource limits, a timeout —
but it is not isolated: test code runs as your user and can reach your filesystem and the network.
The container is the real boundary:

## Measured performance

Measured on an Apple M4, 24 GB, against a local Ollama running
`hf.co/JetBrains/Mellum2-12B-A2.5B-Instruct-GGUF-Q4_K_M`. Expect roughly 3-4x slower on an older
Intel laptop.

| Metric | Result |
|---|---|
| Generation throughput | 51 tok/s (372 tokens in 7.3s) |
| Prefill throughput | ~480 tok/s (a 3,438-token prompt took ~7s before the first output token) |
| Cold model load | ~3.5s, one-time |
| GGUF size on disk | 8.07 GB |
| Loaded context window | 16,384 tokens (`ollama ps`, via the derived model) |
| Workshop suite (`01`–`03`), pass@1 | 1.00 (3/3), 44.5s wall clock, peak prompt 1,456 tok |
| HumanEvalFix (20 vendored tasks), pass@1 | 0.60 (12/20), median 7 steps, max 10, 185,235 tokens, 8m09s wall clock, peak prompt 2,998 tok |

pass@1 on HumanEvalFix was **0.50 before** the loop's stop condition was made real. The old loop
ended a run on any text-only reply, so four failures stopped at 3–5 steps of 10 with the budget
unused; now a text-only reply while the tests are red gets a nudge and another step, and every one
of the 8 remaining failures uses all 10 steps. The context-window fix landed in the same
measurement, so the two effects are not separated — note that the largest single prompt was 2,998
tokens against the old 3,072-token usable window, i.e. the longest runs really were at the edge.
Nothing was tuned to move the number.

The wall-clock figure is exactly why that eval segment is demo-only in the workshop — it does
not fit in a 90-minute session as a live activity. `results/precomputed/` ships both runs so
students can discuss the numbers without waiting for them.


---

## Next steps

### The Thinking variant

The natural next step from here is the Mellum2 **Thinking** variant: same code, one environment
variable, and it exposes visible `<think>` blocks showing the model's reasoning before it commits
to a tool call or a final answer. Nothing about the loop, the tools, or the stop condition you
built changes — only the model's own output gets richer.

### What was deliberately left out

This course, like the workshop it is ported from, is scoped to three ideas: tools, a loop, and a
verification-based stop condition. On purpose, it does not build:

- **Planning** — no phase where the model lays out a multi-step plan before acting.
- **Reflection / self-critique** — no separate pass where the model reviews its own output before
  it counts.
- **Parallel tool calls** — one tool call per turn, not several dispatched at once.
- **Multi-agent coordination** — one model, one loop, no delegation to sub-agents.
