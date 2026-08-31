# agentfix — three editions of one coding agent

A teaching repository for a workshop that shows developers new to agents how a coding agent
actually works — and then what changes when you put a framework and a reasoning model underneath
it. The same bug-fixing agent is built three times:

| Edition | Package | Model | Framework |
|---|---|---|---|
| Agent with no framework | `agentfix` | Mellum2 **Instruct** | none — a `for` loop |
| What about frameworks? | `agentlang` | Mellum2 **Instruct** | LangGraph + LangChain |
| What about thinking? | `agentgraph` | Mellum2 **Thinking** | LangGraph + LangChain |

You read the first one. You write the parts of the second and third that no framework can write
for you: **where a run is allowed to end**, and **what to do about a model that has stopped making
progress**. Everything else — the tools, the sandbox, the tracer, the task loader, the CLI — is
written and locked, and the comments in it are part of the lesson.

The default path uses [JetBrains Mellum2](https://huggingface.co/JetBrains) locally through
Ollama; smaller local models are the fallback, and a browser notebook path exists for learners who
cannot run either comfortably. Every graded exercise runs against a scripted **fake** model, so the
whole course can be finished offline. Running your finished agent against a real model is the
reward, not a prerequisite.

## Start here

This README is the single reference for the workshop. The wording of each lesson section stays
close to the lesson text in the IDE so it is easy to recognise where you are.

### Course map

| Lesson | Steps | You write |
|---|---|---|
| **Setting up and doctor check** | Setup, Doctor Check | — |
| **Agent with no framework** | Intro and Structure, Real Model | — (read it, run it) |
| **What about frameworks?** | Intro and Structure, Stage 1, Stage 2, Run for Real | the graph's routing, and the loop guard |
| **What about thinking?** | Stage 1, Run for Real | what counts as acting, the idle counter, the nudge choice, the routing tail |

### One command shape, three agents

Every command in the course runs from the terminal at the **course root**, through `run.py`. The
first word picks which edition it runs against:

```bash
python run.py doctor                  # Agent with no framework   (the default)
python run.py agentlang doctor        # What about frameworks?
python run.py agentgraph doctor       # What about thinking?
```

You never need to change directory, and you never need to leave the Course View — `run.py` finds
the guided project's working directory for you. The `[run.py]` line printed before every command
names the directory it chose, so which agent ran is never a guess. If it cannot find an edition's
code, open that lesson once and click its first step: the working directory is created the first
time you do.

The three ideas the whole course is about, in every edition: **tools**, a **loop** that feeds tool
results back to the model, and a way to know when it is **done** that does not depend on the
model's opinion.

---

# Lesson 1 — Setting up and doctor check

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
| `mellum2` (default) | laptops that can comfortably run Mellum2 | 16 GB+ | local Ollama, `http://localhost:11434` | reference path |
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

Use `notebooks/agentfix.ipynb` for the browser-based Google Colab path. The model, Ollama process,
repository, edits and test commands all run inside the Colab runtime rather than on the learner's
laptop.

Colab is not a drop-in replacement for the IDE setup. The exercises are the same files and the same
decisions, but the workflow is not: learners edit the repository files from the notebook
environment and run the exercise tests from notebook cells, so the IDE-specific checks,
file-navigation instructions and terminal steps in each lesson need their notebook equivalents.
Keep the lessons and their stages in the same order; only the environment changes.

## Check your setup

```bash
python run.py doctor
```

If that says it cannot find the agent's code, open the **Agent with no framework** lesson and
click its first step once, then come back.

### One doctor per agent

`doctor` runs against one edition, like every other command:

```bash
python run.py doctor                  # Agent with no framework
python run.py agentlang doctor        # What about frameworks?
python run.py agentgraph doctor       # What about thinking?
```

The first two share the model `setup` installs, so both should report READY straight away.

**`agentgraph` is worth checking now rather than three lessons from now.** That lesson runs the
*Thinking* checkpoint, and its `doctor` adds two checks the others do not have: that the model
**actually reasons**, and that it can **call a tool while doing it**. Neither failure is loud — a
model with no thinking mode does not error, it just quietly behaves like the Instruct model from
lesson 2, and every reasoning-shaped thing in the trace disappears.

If `agentgraph doctor` reports a missing model, install it and run it again:

```bash
ollama pull hf.co/JetBrains/Mellum2-12B-A2.5B-Thinking-GGUF-Q4_K_M
ollama create agentgraph-mellum2-thinking -f Modelfile
```

Run that from the **What about thinking?** lesson's directory so it picks up that lesson's own
`Modelfile` — the one carrying `PARAMETER num_ctx 16384` for the Thinking model. On the small tier
the equivalent is `qwen3:1.7b`: the smallest thing that both thinks and calls tools. The
`qwen2.5-coder:1.5b` fallback used by the other lessons has no thinking mode at all, so it cannot
stand in here.

### What `doctor` reports

It checks your Python version and free RAM, that Ollama is installed, that its server answers,
that the derived model exists, that the loaded context window is 16,384, does one warmed-up timed
generation, and runs one sandboxed test execution. It prints `[PASS]`/`[FAIL]` per check and a
final `READY <rate> tok/s`, or a remedy command for each failure.

Two lines decide your tier:

- **`ram`** — whether you can run an 8 GB model comfortably. This is the line `setup.py` uses to
  choose between the `mellum2` tier and the smaller fallback.
- **`context window`** — whether the `ollama create` step actually took effect. If it reads
  `4096` instead of `16384`, that step was skipped, and the agent will lose its own system prompt
  on long runs: Ollama's default context drops the *earliest* messages first, and the earliest
  message is the one telling the agent it is not finished until the tests pass.

If either looks wrong, fix it before continuing.

---

# Lesson 2 — Agent with no framework

Nothing to write here. Read the agent, run it, and form an opinion about which parts of it are
*this project* and which parts are plumbing a framework could own — because lesson 3 answers that
question with code.

## What is an agent?

Loop, tools, verification.

An agent is a while-loop around a chat model that can call functions and sees the result. Nothing
more magical than that.

That definition is not a simplification for this course — it is literally what is in front of you.
The loop in `agentfix/agent/loop.py` is about 15 lines. The rest of `run_agent` is tracing and
token accounting: bookkeeping around the loop, not the loop itself.

At every point where the mechanics feel like they are piling up, ask which of the three ideas —
loop, tools, verification — the code in front of you belongs to.

## What you will find in this repo

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
├── doctor.py
├── runner.py
└── cli.py

results/precomputed/     shipped eval output, so numbers can be discussed without an 8-minute wait
scripts/                 vendor_humanevalfix.py, which prepares the benchmark subset
tasks/
├── humanevalfix/subset.json
└── workshop/{01-shopcart,02-invoice,03-parser}

Dockerfile.sandbox       the isolated environment the Docker backend runs tests in
Modelfile                the derived Ollama model carrying num_ctx 16384
```

**`agent/`** — the core. `loop.py` sends the conversation to the model, executes requested tools,
feeds results back, and continues until the tests pass or a limit is reached. `trace.py` records
what happened.

**`llm/`** — talking to the model. `client.py` is the real client used with Ollama; `fake.py` is a
deterministic fake model for the tests; `types.py` holds the interfaces both share.

**`tools/`** — what the model is allowed to do. `base.py` is the tool abstraction and registry,
`fs.py` implements listing, reading and writing files, `tests_tool.py` runs the project's tests.

**`sandbox/`** — where and how commands execute. The default backend is a hardened subprocess; the
Docker backend is real isolation.

**`tasks/`** — `loader.py` turns a task definition into a `Task` and creates a temporary workspace
holding a fresh copy of the buggy project.

**`eval/`** — runs the agent across a collection of tasks and records pass rate, steps, tokens and
time. `humanevalfix.py` supports the vendored benchmark subset.

**Top level** — `config.py` (model and environment settings), `runner.py` (connects task,
workspace, tools, client and loop into one solve), `doctor.py` (environment checks), `cli.py`
(`doctor`, `solve`, `eval`).

### Overall flow

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

**`agent/loop.py` decides what happens next, `llm/` talks to the model, `tools/` gives the model
actions, `sandbox/` executes them safely.** Everything else prepares tasks, wires the pieces
together, or scores the result.

## Real Model

```bash
python run.py solve tasks/workshop/01-shopcart --verbose
```

Then the harder one, where the bug is **not** in the file the failing test points at — which is
why `list_files` and `read_file` earn their place:

```bash
python run.py solve tasks/workshop/02-invoice --verbose
```

`--verbose` prints the trace. You should see the model call `run_tests`, look around, write a
file, and run the tests again; that last call is what ends the run. If it burns all ten steps and
prints `NOT SOLVED`, that is not a bug — real models do not fix every task, and the smaller
fallback model is noticeably less reliable at multi-step tool use.

All three workshop tasks at once:

```bash
python run.py eval --suite workshop --limit 3
```

On the Colab tier, run the equivalent commands from notebook cells. The commands and the expected
agent behaviour are the same; the notebook replaces the IDE terminal.

## Sandbox safety

The agent executes model-written code on your machine. Two boundaries, at two different layers:

- **The tool layer confines paths.** `resolve_in_root` in `tools/fs.py` rejects any path that
  would escape the task's working directory *before* a read or write happens. The model can ask
  for `../../etc/passwd`; the tool refuses. The write tool narrows it further — it is constructed
  with the set of files that existed in the pristine template, so the agent cannot create a file
  and then start writing to it.
- **The sandbox confines execution.** When test code runs under the Docker backend it gets no
  network, memory/pid/CPU caps, and a non-root user, so code that behaves badly — an infinite
  loop, an attempt to phone home, a fork bomb — is contained rather than trusted.

See [What the container actually gives you](#what-the-container-actually-gives-you) for the exact
flags, and the [command reference](#command-reference) for how to switch backends.

---

# Lesson 3 — What about frameworks?

Same agent, rebuilt on LangGraph for the graph and LangChain for the model and tool interfaces.
The question the lesson exists to answer: *which parts of my agent does a framework actually write
for me?*

## What the framework gives you

- **`ToolNode` runs the calls.** Dispatch, ordering, unknown tool names, argument validation,
  error recovery — one invocation per turn.
- **`add_messages` makes the history append-only by construction**, which keeps the prompt prefix
  byte-stable and the model server's KV cache valid.
- **Reducers on `AgentState`** accumulate the counters, so `agent_node` returns deltas and never
  reads the old value.
- **Callbacks carry the trace.** No node contains tracing code; the tracer is handed to the graph
  once.
- **The checkpointer** snapshots state after every node, so a run can be resumed or inspected step
  by step.

## What it does not give you

- **The stop condition.** `is_done` believes the test suite, not the model's claim about its own
  work. No framework can supply that — it is a fact about *your* task.
- **The loop guard.** LangGraph has no hook for it at all. LangChain 1.x gives you the seam
  (`wrap_tool_call`), but "three identical calls means the model is stuck" is still your policy.
- **The step budget.** `recursion_limit` counts node executions, not model turns.

Those three are exactly what you write in this lesson.

`agentlang/agent/prebuilt.py` builds the same agent again out of `create_agent`, the framework's
prebuilt loop, purely so the comparison is readable. It is not the
path `solve` takes, and its docstring records what is still missing there: the verdict cannot
survive a checkpoint, and the guard can answer a repeated call but not abandon a stuck run.

## What is different in the tree

`tools/`, `sandbox/`, `tasks/`, `eval/` and the top-level modules are the same shape as lesson 2.
The agent is what changed:

```text
agentlang/agent/
├── graph.py       the whole agent: 3 nodes, 2 routers — the file you edit
├── state.py       AgentState and its reducers, including the tests_passed verdict
├── prebuilt.py    the same agent via create_agent, for comparison only
└── trace.py       tracing, via callbacks
```

`state.py` is worth reading before you touch `graph.py`. Making the loop's local variables an
explicit typed state is the biggest change the framework asks for, and it buys two things: any
node can read the whole state, and the state can be checkpointed. It costs one thing: a node
returns a *partial* state that LangGraph merges, so `counter += 1` is not something a node can
do — hence the reducers.

One detail that took a while to see, and it is in the docstrings: checkpointing is only as good as
what you put in the state. While the test verdict lived on the `run_tests` tool, the graph was
resumable and the *agent* was not.

## Stage 1 — where a run is allowed to end

`route_after_agent` in `agentlang/agent/graph.py`. It looks at the model turn that just happened
and returns `"tools"`, `"nudge"`, or `END`.

This is the only place a run can end **successfully**, and the framework has no opinion about it
whatsoever. The rules, in the order they have to be checked:

1. Tool calls never end a run on their own — execute them and loop back, so the model sees the
   results. This branch deliberately skips the `is_done` check: "done" belongs on a turn where the
   model had nothing more it wanted to do.
2. On a prose turn, the verdict decides. `is_done` reads `state["tests_passed"]`, which only ever
   becomes true by folding a real `ExecResult` out of a tool answer — so a model that declares
   victory without running the tests is not believed.
3. The step budget outranks the nudge, or a stubborn model never stops.
4. Otherwise, nudge it and go again.

## Stage 2 — refusing a call the model already made

The loop guard, inside `tools_node`. Small models get stuck in the plainest way possible: they
call `read_file` on the same path, get the same answer, and call it again until the budget runs
out.

Three things make it work, and all three are policy rather than plumbing:

- **What counts as the same call.** `call_signature` hashes the tool name plus its *sorted*
  arguments, so key order in the model's JSON cannot defeat the guard.
- **A refused call still gets an answer.** The API requires exactly one reply per
  `tool_call_id`; drop one and the *next* request is rejected, one turn away from the code that
  caused it. So a refusal appends a `ToolMessage` carrying the guard's text and moves on.
- **The counter moves both ways.** A repeat increments it; a call that is not a repeat resets it
  and becomes the new baseline. `route_after_tools` abandons the run once it reaches
  `MAX_GUARD_HITS`.

A guarded call also gets a `tracer.note` line — the one line in a trace that no tool produced.
Without it, a guarded run looks like a model that mysteriously stopped making calls.

## Run for Real

```bash
python run.py agentlang doctor
python run.py agentlang solve tasks/workshop/01-shopcart --verbose
python run.py agentlang solve tasks/workshop/02-invoice --verbose
python run.py agentlang eval --suite workshop --limit 3
```

Two lines in the trace are yours. The run does not end on the green test result — it ends one turn
later, on the model's prose reply, because that is where Stage 1 put the `is_done` check. And if
the model gets stuck you will see `guarded — identical call #2 in a row`.

That extra closing turn is a deliberate choice, and it is not free: measured on `01-shopcart` it
cost 6.5s of a 19.1s run. What it buys is the closing statement itself — the only prose in a run,
arriving *after* the fix was verified, which is the evidence for the claim that this agent does not
reason.

Safety is unchanged: `tools/` and `sandbox/` are the same code as lesson 2. Confinement is a
property of the tools and the sandbox, not of the loop that calls them.

---

# Lesson 4 — What about thinking?

## What thinking actually is

Same 12B/A2.5B weights, different checkpoint: this one is trained to reason inside
`<think>...</think>` before it answers. One flag on the client — `reasoning=True` — asks Ollama for
that thinking and hands it back on **its own channel**,
`AIMessage.additional_kwargs["reasoning_content"]`, instead of leaving the tags inline in
`content`. `reasoning_of` in `agentgraph/agent/trace.py` is the single line that knows where it
lives.

That channel matters more than it sounds. With the tags inline, the model's deliberation ends up in
the next prompt, in the trace, and — the expensive one — inside the "complete file contents" that
`write_file` is handed.

**There is no think step and no new node.** This is what ReAct means: the model thinks and acts in
the *same* turn. The graph from lesson 3 is unchanged in shape.

## What changed, and what did not

Not the graph. Two *decisions* about what a turn was:

- **A turn with no tool call is no longer rare.** The Instruct model acted on every turn but the
  last, so "replied without acting" meant "finishing up" and the answer was a nudge. A thinking
  model will spend an entire turn reasoning and ask for nothing — and nudging that forever is an
  unbounded loop wearing a step budget as a disguise. Hence `idle_turns` in `AgentState` and
  `MAX_IDLE_TURNS = 2` in `graph.py`: a loop guard for thinking, alongside the one for actions.
- **The action guard must ignore reasoning.** `call_signature` still hashes only the tool name and
  arguments. A model that reasons its way to the same useless call by a fresh route every time is
  still stuck, and novel thinking must not buy a repeated call another turn.

Also new: `reasoning_turns` in the state, so a run can report how many of its turns actually
thought — the number the previous edition could not produce.

## Stage 1 — reasoning is not an action

Four `TODO` markers in `agentgraph/agent/graph.py`, all one decision split four ways:

| # | Where | What it decides |
|---|---|---|
| 1 | `acted()` | what counts as a turn that *did* something — a tool call, not prose and not thought |
| 2 | `agent_node`'s returned state | keeping `idle_turns` current |
| 3 | `nudge_node` | which of the two corrections to send |
| 4 | `route_after_agent` | the answers for a turn that acted on nothing |

Two traps worth naming:

- `idle_turns` is the one key in the state with **no reducer**, because it has to *reset* — a
  reducer is handed only `(current, incoming)` and cannot tell "one more idle turn" from "that turn
  acted, start again". `agent_node` is its only writer and returns the absolute value.
- In the routing, **the verdict goes before the idle guard**. A thinking turn on a suite that is
  already green is a successful finish, not a stall. And the budget outranks the guard: a model out
  of steps stops for that reason.

The abandonment also gets a trace note, worded from what was observed — "no tool call", not "turns
of reasoning". A turn can ask for nothing without having reasoned, and a trace line claiming
deliberation that never happened is the exact failure this edition exists to fix.

## Run for Real

```bash
python run.py agentgraph doctor
python run.py agentgraph solve tasks/workshop/01-shopcart --verbose
python run.py agentgraph eval --suite workshop --limit 3
```

Every model turn now prints a `thinks` line above what it did. Read one — that text is the plan the
earlier editions never had. Two more things to watch for:

- `(NO REASONING)` now means what it says. In lesson 3 it appeared on almost every turn, because
  reasoning was read off `content`; here it prints only when the model genuinely skipped thinking.
- Reason twice with no tool call and the run ends with
  `abandoned — 2 consecutive turns with no tool call`. That is your Stage 1 guard.

`max_tokens` went from 1024 to **4096** in this edition, because one reply is now the reasoning
*plus* a complete file. Too low a cap truncates the reply and loses the tool call at the end of
it — the model appears to stop acting for no reason.

Safety is the same code again, with two names to get right — they fail at solve time, not build
time:

```bash
python run.py agentgraph docker-build          # builds agentgraph-sandbox:latest
AGENTGRAPH_SANDBOX=docker python run.py agentgraph solve tasks/workshop/01-shopcart --verbose
```

---

# Reference

## Command reference

```bash
python run.py [edition] <command> [arguments] [flags]
```

`edition` is `agentfix` (the default, so it can be omitted), `agentlang`, or `agentgraph` — as the
first word, as `--agentlang`, or set once via `AGENT_EDITION` in `.agentfix.env`.

The examples say `python`, which is what the IDE terminal gives you once the course's virtual
environment is active. In a bare shell on Debian, ChromeOS or a fresh Linux there is no `python` —
use `python3`.

| Command | What it does |
|---|---|
| `doctor` | Checks this machine is ready for that edition. |
| `solve <task_dir> [--verbose] [--max-steps N]` | Runs the agent on one task. |
| `eval [--suite workshop\|humanevalfix] [--limit N]` | Runs the agent over a suite. |
| `unittest <module> [-v]` | Runs that edition's test suite. |
| `docker-build` | Builds that edition's sandbox image. |

### Solve one task

```bash
python run.py solve tasks/workshop/01-shopcart
python run.py agentlang solve tasks/workshop/01-shopcart --verbose
python run.py agentgraph solve tasks/workshop/02-invoice --max-steps 15 --verbose
```

| Argument / flag | Meaning |
|---|---|
| `<task_dir>` | Required path to the task directory, e.g. `tasks/workshop/01-shopcart`. |
| `--verbose` | Prints the trace: model turns, requested tool calls, tool results, guard decisions, and (in `agentgraph`) the model's reasoning. |
| `--max-steps N` | Model turns allowed for this run. Default is the edition's `MAX_STEPS`, which is 10. |

`solve` prints `SOLVED` or `NOT SOLVED` with the task id, steps used, token count and duration,
and exits non-zero unless the task was solved — so `solve … && echo ok` behaves sensibly.

### Evaluate a suite

```bash
python run.py eval
python run.py agentlang eval --suite workshop --limit 3
python run.py agentgraph eval --suite humanevalfix --limit 10
```

| Flag | Meaning |
|---|---|
| `--suite workshop` | The three workshop tasks. The default. |
| `--suite humanevalfix` | The vendored HumanEvalFix subset. |
| `--limit N` | At most `N` tasks. Default 3, because local evaluation is slow. |

`eval` writes its output to that edition's `results/`, and every edition ships a precomputed run
so the numbers can be discussed without waiting.

### Run the tests

```bash
python run.py agentlang unittest tests.test_task -v
python run.py agentgraph unittest tests.test_task -v
```

Every exercise test drives the real graph against the real tools in a real temporary directory —
only the model is scripted. Nothing in the suite needs Ollama running.

### The container sandbox

The default subprocess backend is *hardened* — stripped environment, resource limits, a timeout —
but it is not isolated: test code runs as your user and can reach your filesystem and the network.
Switching backends swaps exactly **one** thing: how `run_tests` executes the task's suite. The
agent, the model client and the file tools still run on the host either way.

The environment variable and the image tag are per edition:

| Edition | Variable | Image |
|---|---|---|
| `agentfix` | `AGENTFIX_SANDBOX=docker` | `agentfix-sandbox:latest` |
| `agentlang` | `AGENTFIX_SANDBOX=docker` | `agentfix-sandbox:latest` |
| `agentgraph` | `AGENTGRAPH_SANDBOX=docker` | `agentgraph-sandbox:latest` |

```bash
# POSIX shells (macOS, Linux, WSL2)
docker info
python run.py agentlang docker-build
AGENTFIX_SANDBOX=docker python run.py agentlang solve tasks/workshop/01-shopcart --verbose

export AGENTFIX_SANDBOX=docker      # ...or for the whole session
unset AGENTFIX_SANDBOX              # back to the subprocess backend
```

```powershell
# Windows PowerShell — its own line, before the command
$env:AGENTFIX_SANDBOX = 'docker'
python run.py agentlang solve tasks/workshop/01-shopcart --verbose
Remove-Item Env:\AGENTFIX_SANDBOX
```

### What the container actually gives you

Every flag below is in `sandbox/docker_backend.py`, with the reasoning next to it:

| Flag | Why |
|---|---|
| `--network none` | No network at all — the real difference from the subprocess backend. |
| `--memory 512m`, `--pids-limit 128`, `--cpus 1` | A runaway test cannot take the machine down. |
| `--user runner` | Not root, even inside the container. |
| `--cap-drop ALL`, `--security-opt no-new-privileges` | Every Linux capability dropped, and no regaining them via setuid. |
| `--read-only` plus `--tmpfs /tmp` | The container filesystem is immutable; scratch space is in memory and discarded. |
| `--volume <workspace>:/work:ro` | The workspace is mounted **read-only** — the file tools write on the host, so the container never needs to. |
| `--rm` | No state survives a run. |

The image installs nothing: `Dockerfile.sandbox` is `python:3.12-slim`, a non-root user, and a
workdir. Tests run with `python -m unittest discover -q`, which is in the standard library — so
there is no dependency to pin and no drift between the two backends. That matters more than it
sounds: both backends are the same oracle for the agent's fixes, and an oracle that changes with an
environment variable is not one.

## Measured performance

Throughput measured on an Apple M4, 24 GB, against a local Ollama running the Q4_K_M GGUF. Expect
roughly 3–4× slower on an older Intel laptop.

| Metric | Result |
|---|---|
| Generation throughput | 51 tok/s (372 tokens in 7.3s) |
| Prefill throughput | ~480 tok/s (a 3,438-token prompt took ~7s before the first output token) |
| Cold model load | ~3.5s, one-time |
| GGUF size on disk | 8.07 GB |
| Loaded context window | 16,384 tokens (`ollama ps`, via the derived model) |

### Three editions, the same 20 HumanEvalFix tasks, the same 10-step budget

| Edition | pass@1 | median steps | tokens | wall clock | peak prompt |
|---|---|---|---|---|---|
| No framework, Instruct | 0.60 (12/20) | 7 | 185,235 | 8m08s | 2,998 |
| LangGraph, Instruct | 0.45 (9/20) | 10 | 237,651 | 8m15s | 3,929 |
| **LangGraph, Thinking** | **0.80 (16/20)** | **5** | **415,333** | **52m25s** | **12,599** |

Workshop suite (`01`–`03`), pass@1 1.00 in all three editions: 43.6s / 1m45s / 7m17s wall clock,
peak prompt 1,456 / 1,574 / 6,163 tokens.

**Thinking is the largest single move in the course.** It did not just solve more, it solved in
*fewer* turns — fourteen of the sixteen successes took exactly five steps: run the tests, look,
write, verify. Every one of the 23 thinking runs reasoned on every turn (`reasoning_turns` equals
`steps_used` throughout), which closes the gap the earlier editions could only point at. Two of its
four failures ended at 6 steps rather than 10, stopped by a guard rather than by the budget — a
stuck thinking model is abandoned instead of nudged until the money runs out.

**And it is expensive.** 1.75× the tokens for 6× the wall clock, and a peak prompt of 12,599
against a 16,384-token window: reasoning is sent back with every subsequent request, so the history
grows much faster than before. This agent is three-quarters of the way to overflowing its context
on a benchmark of *small* bugs.

**Do not read the 0.60 → 0.45 step as a cost of the framework.** Temperature is 0.6 in all three,
so a single 20-task run is a noisy measurement, and the two Instruct editions take identical step
counts (8, 8, 7) on the tasks they both solve. For scale: in the no-framework edition, making the
stop condition real moved pass@1 from **0.50 to 0.60** on its own — larger than the gap between the
first two rows. What moves the number is the prompt, the budget and the stop condition, not the
plumbing.

The wall-clock figures are why the HumanEvalFix eval is demo-only in a 90-minute session. Every
edition ships its run under `results/`, so the numbers can be discussed without waiting for them.

## Repository layout

```text
agentfix/                        the course section
├── lesson_setup/                Setup, Doctor Check
├── lesson_build/                Agent with no framework   → package agentfix
├── lesson_langchain/            What about frameworks?    → package agentlang
└── lesson_react/                What about thinking?      → package agentgraph

run.py                           runs any edition from the course root
setup.sh / setup.ps1 / setup.py  one-command model setup
requirements.txt                 langgraph, langchain, langchain-ollama, datasets
Modelfile                        the derived Ollama model for the default tier
notebooks/agentfix.ipynb         the Colab path
```

Each lesson step keeps a complete, self-contained copy of its edition's code, which is how the
JetBrains Academy framework-lesson format works: the step you have open is the project you are
running. `run.py` resolves that for you — it probes the plugin-managed working directory first, so
you run *your* work rather than the reference copy, and falls back to the authoring copy when there
isn't one.

## Known limitations

- **One tool call at a time.** `max_concurrency=1` is not a performance setting; it is what keeps
  the test result honest. Tool calls in one turn would otherwise execute in parallel, and a turn
  that wrote a file and ran the tests together could measure the file as it was *before* the write.
- **No context management.** Nothing trims or summarises old turns. It is the clearest gap in the
  numbers above, and the first thing worth building next.
- **The notebook path is untested** and needs a notebook-specific version of the lesson flow,
  because learners edit and run the agent from the notebook environment rather than the IDE.
- **`agentlang/agent/prebuilt.py` refuses a checkpointer** on purpose: the framework's prebuilt
  agent carries its own state schema with nowhere to keep the verdict, so a resumed run recomputes
  it from message artifacts the serialiser has already turned back into plain dicts, and a solved
  task comes back unsolved.

## Next steps

Roughly in order of what would pay off next on these numbers:

- **Context management** — trimming or summarising old turns, or dropping stale reasoning from the
  history. What stands between this agent and a task bigger than a one-file bug.
- **Planning as its own phase** — the model plans inside a turn now; nothing makes it commit to a
  plan across turns or notice when it has abandoned one.
- **Reflection / self-critique** — no separate pass where the model reviews its own diff before the
  tests do.
- **Parallel tool calls** — done properly, which means knowing which calls are safe to overlap.
- **Multi-agent coordination** — one model, one graph, no delegation.

None of them is magic, and each is a fair amount of work. If you take one thing from the whole
course, make it the stop condition: three editions in, the thing that decides whether an agent is
trustworthy is still that it believes the test suite rather than the model.

## License

MIT — see [LICENSE](LICENSE).
