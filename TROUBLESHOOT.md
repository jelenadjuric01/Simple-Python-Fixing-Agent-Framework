# Troubleshooting

Everything that has actually gone wrong for someone taking this course, and what fixed it.
Nothing here is a bug in your work — these are environment problems, and each one has a short
answer.

Click a heading to open it.

**Three editions, three names.** Almost every command in this course starts with `python run.py`
and then names an edition: `doctor` (or `agentfix …`) for the no-framework agent, `agentlang …`
for the LangGraph one, `agentgraph …` for the thinking one. A surprising number of the problems
below are really "the right command, aimed at the wrong edition", so the `[run.py]` line printed
before every command — it names the directory and settings it chose — is the first thing to read.

## The IDE

<details>
<summary><b>Imports are underlined red — <code>agentfix</code>, <code>agentlang</code> or <code>agentgraph</code> cannot be resolved</b></summary>

The code is fine. The IDE just does not know that the lesson folder is where the package starts.

Right-click the current lesson's folder in the Project view — **Agent with no framework**,
**What about frameworks?**, or **What about thinking?** — and choose **Mark Directory as** →
**Sources Root**. The red underlines disappear immediately.

You may have to do this once per lesson folder, because each lesson carries its own copy of the
code under its own package name. It changes nothing about the code and nothing about how
`python run.py` behaves — that script never relied on the IDE's idea of the source root in the
first place.
</details>

<details>
<summary><b><code>ModuleNotFoundError: No module named 'langgraph'</code> (or <code>langchain_ollama</code>, <code>datasets</code>, anything else)</b></summary>

The dependencies in `requirements.txt` are installed by the IDE into the project interpreter, and
sometimes the IDE has not finished — or has quietly lost track of what it installed.

In order, stopping as soon as it works:

1. **Settings** → **Project: …** → **Python Interpreter**, look at the package list, and click the
   refresh button. Give it a moment to re-read the environment.
2. If the package is genuinely not in the list, install it there: the **+** button, search, install.
   Or from the terminal at the course root: `pip install -r requirements.txt`.
3. Close PyCharm and open it again. This resolves it surprisingly often — the interpreter index is
   rebuilt on startup.

Check you are on the right interpreter while you are in that dialog. If the course created a
virtual environment and the IDE is pointed at your system Python instead, every package will look
missing because, in that interpreter, it is.

Lessons 3 and 4 need `langgraph`, `langchain-core` and `langchain-ollama`; lesson 2 needs none of
them. So "lesson 2 runs but lesson 3 does not import" is this problem and not a broken agent.
</details>

<details>
<summary><b>Tests pass in the terminal but the IDE shows them failing (or the reverse)</b></summary>

Two different interpreters. The terminal uses whatever is on your `PATH`; the IDE uses the one in
**Settings** → **Project: …** → **Python Interpreter**.

Point them at the same one. If the project has a `.venv`, that is the one to choose in the IDE,
and `source .venv/bin/activate` (`.venv\Scripts\activate` on Windows) is what makes your terminal
match it.
</details>

## Windows

<details>
<summary><b>You installed Ollama or set a model variable, and the course still cannot see it</b></summary>

**Close PyCharm completely and open it again.**

This is the one that catches everyone. Windows hands a process its environment variables when the
process starts, and never updates them afterwards. `setx`, the Ollama installer, and the
**Environment Variables** dialog all write the variable for *future* processes — PyCharm, and
every terminal already open inside it, keeps the environment it was launched with. Restarting the
IDE is what picks the change up.

The same applies to a plain `cmd.exe` or PowerShell window: open a new one.

Quick check, in a **new** terminal:

```powershell
echo $env:MELLUM_MODEL        # the coding model, lessons 2-3
echo $env:AGENTGRAPH_MODEL    # the thinking model, lesson 4
ollama --version
```

If those print and `ollama` is recognized there but the course still disagrees, PyCharm is the
process that has not been restarted yet. `python run.py …` does not depend on any of it — it
reads `.agentfix.env` — so it is also the fastest way to prove the models are fine.
</details>

<details>
<summary><b><code>setup.ps1 cannot be loaded because running scripts is disabled</code></b></summary>

PowerShell's default execution policy. Run it the way the setup lesson gives it, with the bypass
on the command itself:

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

That flag applies to that one command only and changes nothing permanently on your machine.
</details>

<details>
<summary><b><code>ollama</code> is not recognized as a command, right after installing it</b></summary>

Same cause as the variables above: the installer added Ollama to `PATH`, but only for processes
started afterwards. Open a new terminal — and if you are running it from inside the IDE, restart
the IDE.
</details>

## Setup, disk and the models

<details>
<summary><b><code>./setup.sh: permission denied</code></b></summary>

The file is not marked executable on your machine. Run it through the shell instead:

```bash
sh setup.sh
```

Or make it executable once: `chmod +x setup.sh`.
</details>

<details>
<summary><b>Setup wants ~18 GB and you do not have it, or a pull dies part-way through</b></summary>

Each tier installs **two** models — a coding one for lessons 2 and 3, a thinking one for lesson 4 —
so the default tier downloads about 16 GB of weights. Ollama does not check free space up front; it
writes the blob, then the manifest, and fails somewhere in between. Setup prints the estimate and
your free space before it starts, which is the number to read.

Either free the space, or take the small tier, which needs about 3 GB in total:

```bash
./setup.sh --tier qwen
```

A pull that died leaves a partial blob behind. Re-running the same `ollama pull` resumes it — it
does not start over.
</details>

<details>
<summary><b><code>doctor</code> says <code>context window: 4096</code> instead of <code>16384</code></b></summary>

The `ollama create` step was skipped, so you are talking to a base model rather than a derived one.
Per edition, from the course root:

```bash
ollama create agentfix-mellum2 -f Modelfile                                   # lessons 2-3
ollama create agentgraph-mellum2-thinking -f Modelfile.agentgraph-thinking    # lesson 4
```

On the `qwen` tier there is one name, `agentfix-qwen3`, with the matching `Modelfile` setup wrote
into the course root — that tier's single model serves every lesson. Re-running `./setup.sh` does
all of this for you.

Do not skip it. At 4,096 tokens Ollama drops the *earliest* messages once the conversation grows
past the limit — and the earliest message is the system prompt telling the agent it is not done
until the tests pass. The symptom is an agent that seems to forget the task halfway through, which
looks like a stupid model rather than a misconfigured one.
</details>

<details>
<summary><b>Connection refused on <code>localhost:11434</code>, or <code>doctor</code> says the server does not answer</b></summary>

Ollama is installed but the server is not running.

```bash
brew services start ollama       # macOS, if installed with Homebrew
open -a Ollama                   # macOS, if you installed the app
sudo systemctl start ollama      # Linux
ollama serve                     # anywhere, including WSL2 without systemd
```

On macOS, the Homebrew service and the tray app are the same server on the same port — use one,
not both at once.
</details>

<details>
<summary><b>Everything was fine, and then lesson 4 made the machine crawl</b></summary>

Two 8 GB models resident at once, which only happens on the `mellum2` tier — the `qwen` tier has
a single model and cannot hit this. The lessons use one model at a time, but Ollama keeps the last
one loaded for **five minutes** after the final request, so moving straight from lesson 3 to
lesson 4 on a 16 GB laptop can put both in memory together. That is the one case where the pair
costs RAM rather than only disk.

```bash
ollama ps                        # what is loaded right now
ollama stop agentfix-mellum2     # or whichever one you are done with
```

Permanently, in the **server's** own environment: `OLLAMA_MAX_LOADED_MODELS=1`. `setup.py` sets it
for a server it starts itself; the macOS menu-bar app needs
`launchctl setenv OLLAMA_MAX_LOADED_MODELS 1` and a restart of Ollama, and a systemd install needs
it in `systemctl edit ollama`.
</details>

<details>
<summary><b>The model is painfully slow, or your machine runs out of memory</b></summary>

You are on the wrong tier for this machine. Mellum2 is an 8 GB model and wants 16 GB of RAM to be
comfortable. Drop to the smaller pair:

```bash
./setup.sh --tier qwen
```

Below 8 GB, use the `colab` tier instead — see the setup lesson. The small models are less reliable
at multi-step tool use than Mellum2, so expect more steps or an occasional task they cannot fix.
They are enough to watch the loop work.
</details>

<details>
<summary><b>On the <code>colab</code> tier, <code>python run.py doctor</code> fails</b></summary>

Expected. Nothing is broken.

On that tier there is no Ollama, no model and no server on your laptop by design — they live in the
Colab runtime. Skip `doctor` locally. `notebooks/agentfix.ipynb` runs the same checks inside Colab,
for all three editions, and those are the ones that have to pass. Every exercise still runs on your
own machine, because the exercise tests use a scripted fake model and need no model at all.
</details>

## Running the agents

<details>
<summary><b><code>run.py could not find the agent's code</code></b></summary>

Each lesson's working directory is created the first time you open that lesson. Open the lesson the
error names and click its first step once, then run the command again.

The error is per edition, so `python run.py doctor` working while
`python run.py agentgraph doctor` does not is this, and not a broken install.
</details>

<details>
<summary><b>You ran a command and the wrong edition answered</b></summary>

The edition is the first word of the command, and there are four ways it gets chosen: the first
word, a `--agentgraph`-style flag, `AGENT_EDITION` in `.agentfix.env`, and otherwise the default
`agentfix`. If you set `AGENT_EDITION` once while working through a lesson and forgot, everything
afterwards runs against that edition.

```bash
python run.py agentgraph doctor    # explicit always wins
```

The `[run.py]` line printed before every command names the directory it chose — that line is the
answer, not a guess.
</details>

<details>
<summary><b>Lesson 4 runs, but there is no reasoning anywhere in the trace</b></summary>

You are pointed at a model that cannot think, and this failure is silent by design of the model,
not of the course: the agent still completes runs, and behaves exactly like lesson 3's Act-only
agent.

```bash
python run.py agentgraph doctor
```

Its `reasoning` and `tool calling` checks exist for precisely this. What usually causes it:

- `MELLUM_MODEL` set by hand to a coding model. Lesson 4 reads **`AGENTGRAPH_MODEL`** first and
  only falls back to `MELLUM_MODEL`, so an override left over from an earlier tier can capture it.
- The `reasoning` check failing with *"the reasoning arrived INLINE"* is a different thing: the
  model does think, but the client asked for it the wrong way. That is a `reasoning=True` /
  native-API question, not a model choice — see the lesson's `llm/client.py`.
</details>

<details>
<summary><b>The agent prints <code>NOT SOLVED</code></b></summary>

Not necessarily your bug. Real models do not fix every task, and the smaller fallbacks are
noticeably less reliable at multi-step tool use than Mellum2.

Read the `--verbose` trace before assuming your code is wrong. You are looking for the shape of a
working loop: the model calls `run_tests`, looks around with `list_files` / `read_file`, writes a
file, and runs the tests again. If that shape is there and the run simply ran out of steps, your
implementation is doing its job — run it again, or move on.

If instead the run ends while the tests are still red, or ends the moment the model says it is
done, that points at your stop condition rather than at the model.
</details>

<details>
<summary><b>The run ends with <code>abandoned — 2 consecutive turns with no tool call</code></b></summary>

That is your lesson 4 guard doing its job, not an error. A thinking model can spend a whole turn
reasoning and ask for nothing; nudging that forever is an unbounded loop wearing a step budget as
a disguise, so two idle turns in a row ends the run.

If it fires on *every* task, the nudge is probably not reaching the model — check what your Stage 1
code appends to the history before it routes back.
</details>

<details>
<summary><b>An exercise test fails and you cannot see why</b></summary>

Every graded test runs against a scripted **fake** model, so the failure is deterministic and has
nothing to do with Ollama, your tier, or the network. Run the one test on its own:

```bash
python run.py agentlang unittest tests.test_task -v
```

The fake's scripts live in the edition's `llm/fake.py`, and reading the one the failing test uses
is usually faster than re-reading your own code: it tells you exactly which turn the test is
simulating.
</details>

<details>
<summary><b>The Docker sandbox does not run</b></summary>

Every edition falls back to the subprocess backend when Docker is unavailable, so the course still
works. For the container version you need a running daemon **and** a built image:

```bash
docker info
python run.py docker-build              # lessons 2-3: agentfix-sandbox
python run.py agentgraph docker-build   # lesson 4: agentgraph-sandbox
```

The image name changed along with the package in lesson 4, so building one does not give you the
other — and the failure shows up at solve time as "Unable to find image", not at build time.
Selecting it is `AGENTFIX_SANDBOX=docker` for lessons 2–3, `AGENTGRAPH_SANDBOX=docker` for
lesson 4.
</details>

## Nothing here matches

Run the doctor for the edition you are on and read it top to bottom:

```bash
python run.py doctor
python run.py agentlang doctor
python run.py agentgraph doctor
```

Each prints `[PASS]`/`[FAIL]` per check and a remedy command for every failure, which is faster
than guessing. The setup lesson explains what each line means.
