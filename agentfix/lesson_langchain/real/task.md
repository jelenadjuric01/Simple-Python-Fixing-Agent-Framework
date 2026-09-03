# Now try it at a real model

Everything you wrote so far was checked against a scripted fake model — deliberately, so the
exercises never depend on your Ollama setup. Now point the graph at the real one.

> **On the `colab` tier, this is the step you do in the browser.** Open
> `notebooks/agentfix.ipynb` in Google Colab and run it top to bottom. It installs Ollama and the
> model inside the Colab runtime and runs the same two tasks there, so the commands below and
> everything this lesson says about reading the trace still apply — only the machine underneath
> changes. Nothing on your own laptop needs to work for this.


    python run.py agentlang solve tasks/workshop/01-shopcart --verbose

And the harder one, where the bug is **not** in the file the failing test points at — which is
why `list_files` and `read_file` earn their place:

    python run.py agentlang solve tasks/workshop/02-invoice --verbose

## Read the trace

`--verbose` prints one line per step. You should see the model call `run_tests`, look around,
write a file, and run the tests again — and then a final line of prose.

Two of those lines are yours:

- The run does not end on the green test result. It ends one turn later, on the model's prose
  reply, because that is where **Stage 1** put the `is_done` check. That last turn is the only
  prose in a whole run, and it is worth reading: the explanation arrives *after* the fix was
  already verified, which tells you something about how much reasoning was involved.
- If the model gets stuck, you will see a line that no tool produced — `guarded — identical
  call #2 in a row`. That is **Stage 2**, and it is the only line in the trace that exists
  because the graph decided something rather than because a tool ran.

If it burns all ten steps and prints `NOT SOLVED`, that is not necessarily your bug. A 12B model
does not fix every task, and the next section is about exactly that.

# How well does it actually work?

This part is discussion, not an exercise — **do not run a live eval**, it takes eight minutes.
The numbers below are shipped with the repo in `results/`.

You can, however, cheaply run the three workshop tasks:

    python run.py agentlang eval --suite workshop --limit 3

## The measurement

`eval` reports **pass@1**: of N tasks, how many did the agent fix on its first and only attempt.
No retries, no best-of-k. It is a harsh number on purpose — retries hide a bad agent behind a
good sampler.

| | Workshop (3 tasks) | HumanEvalFix (20 tasks) |
|---|---|---|
| pass@1 | 1.00 (3/3) | **0.45 (9/20)** |
| steps | 8, 8, 7 | median 10, max 10 |
| tokens | — | 237,651 |
| wall clock | 1m45s | 8m15s |
| peak prompt | 1,574 tok | 3,929 tok |

The workshop suite is a smoke test — three tasks the agent is expected to pass, useful for
telling "my wiring is broken" apart from "this bug is hard". HumanEvalFix is the real
measurement: 20 independent bugs with real tests, so pass@1 over it means something.

Look at the median step count. On HumanEvalFix it is **10** — the budget cap. More than half the
runs did not finish; they were stopped. Every one of the 11 failures spent all ten steps.

## Why is this different from the no-framework agent?

Same model, same tasks, same 10-step budget, and the previous lesson's hand-written agent scored
**0.60 (12/20)** on 185,235 tokens with a median of 7 steps. This one scores 0.45 on 237,651
tokens with a median of 10. On the workshop suite the two agents take *identical* step counts
(8, 8, 7).

1. **Temperature is 0.6, not 0.** These runs are not deterministic, and a single 20-task run is a
   noisy measurement — three tasks either way is well within what re-running the same agent can
   produce. Two runs are not an A/B test.
2. **They are not the same harness.** Identical step counts on the tasks both agents solve, and
   a different distribution on the ones they do not, is what you would expect from a difference
   in *where runs give up*, not in how the model reasons.

That is the honest version, and it is the habit worth taking away: an agent's score is a property
of a specific model, prompt, budget, and stop condition measured on a specific day. Adopting a
framework changes what you have to maintain. It does not, on its own, change what the agent can
fix.

The per-task detail is in `results/humanevalfix.json` and `results/workshop.json` — open them if
you want to see which tasks failed. 

# Sandbox safety

Your agent executes model-written code. On this machine. That is worth one honest paragraph
before you run it on anything you care about.

Two boundaries, at two different layers:

- **The tool layer confines paths.** `resolve_in_root` in `agentlang/tools/fs.py` rejects any
  path that would escape the task's working directory *before* a read or write happens. The model
  can ask for `../../etc/passwd`; the tool refuses. `WriteFileTool` narrows it further — it is
  constructed with the set of files that existed in the pristine template, so the agent cannot
  create a file and then start writing to it.
- **The sandbox confines execution.** When test code actually runs, the Docker backend runs it
  with no network access, memory/pid/CPU caps, and as a non-root user — so code the model wrote
  that behaves badly (an infinite loop, an attempt to phone home, a fork bomb) is contained
  rather than trusted.

Neither of these is framework machinery, and neither changed when the agent moved to LangGraph.
Confinement is a property of the tools and the sandbox, not of the loop that calls them — which
is why this lesson's `tools/` and `sandbox/` directories are the same code as the previous
lesson's.

## Trying it yourself

Optional, and it needs a running Docker daemon.

```bash
docker info
python run.py agentlang docker-build
AGENTFIX_SANDBOX=docker python run.py agentlang solve tasks/workshop/01-shopcart --verbose
AGENTFIX_SANDBOX=docker python run.py agentlang eval --suite workshop --limit 3
```

Expect it to be slower — a container per test run is not free. That is the trade, and it is the
right one the moment the code being executed is not yours.

No **Check** on this step. Nothing here is graded: the agent either fixes the bug or it does not,
which is rather the point of the whole course.
