# Now try it at a real thinking model

Same three commands as the last lesson, one word different — `agentgraph` instead of `agentlang`:

    python run.py agentgraph doctor
    python run.py agentgraph solve tasks/workshop/01-shopcart --verbose
    python run.py agentgraph solve tasks/workshop/02-invoice --verbose

Run `doctor` again if you skipped it in the previous step — its reasoning and tool-calling checks
are the ones that matter now that a real model is answering.

This is a different checkpoint from the last lesson — the Thinking one — and `./setup.sh`
installed it in the same run as the coding model, so there is normally nothing to pull here. If
`doctor` says the model is missing, that pull is:

    ollama pull hf.co/JetBrains/Mellum2-12B-A2.5B-Thinking-GGUF-Q4_K_M
    ollama create agentgraph-mellum2-thinking -f Modelfile

One more thing, if this laptop has 16 GB: Ollama keeps the *previous* lesson's model loaded for
five minutes, and two 8 GB models at once is what makes a correctly set-up machine start
swapping. `ollama stop agentfix-mellum2` before the first command above, and it never comes up.

## What is different in the trace

Every model turn now prints a `thinks` line above what it did. Read one — that text is the plan
the last edition never had.

Watch for two things you wrote:

- `(NO REASONING)` now means what it says. In the previous edition it appeared on almost every
  turn, because reasoning was read off `content`; here it prints only when the model genuinely
  skipped thinking.
- If the model reasons and asks for nothing twice in a row, the run ends with
  `abandoned — 2 consecutive turns with no tool call`. That is your Stage 1 guard. It is the only
  line in the trace no tool produced.

# How well does it actually work?

Discussion, not an exercise — the shipped HumanEvalFix run took **52 minutes**. Do not reproduce
it. The workshop suite is affordable if you want something live:

    python run.py agentgraph eval --suite workshop --limit 3

## Three editions, same 20 HumanEvalFix tasks, same 10-step budget

| | pass@1 | median steps | tokens | wall clock | peak prompt |
|---|---|---|---|---|---|
| No framework, Instruct | 0.60 (12/20) | 7 | 185,235 | 8m08s | 2,998 |
| LangGraph, Instruct | 0.45 (9/20) | 10 | 237,651 | 8m15s | 3,929 |
| **LangGraph, Thinking** | **0.80 (16/20)** | **5** | **415,333** | **52m25s** | **12,599** |

Read the middle two columns together. Thinking did not just solve more — it solved them in
*fewer* turns. Fourteen of the sixteen successes took exactly five steps: run the tests, look,
write, verify. That is what planning before acting buys, and it is the largest single move in
this whole course.

Now read the last three columns, because they are the bill. **1.75× the tokens for 6× the wall
clock**, and a peak prompt of 12,599 against a 16,384-token context window — the reasoning is
sent back with every subsequent request, so the history grows much faster than before. This agent
is three-quarters of the way to overflowing its context on a 20-task benchmark of *small* bugs.

Two more things in the data worth knowing:

- **Every single turn reasoned** — `reasoning_turns` equals `steps_used` on all 23 runs. The
  previous workshop's honest complaint was that 0 of 7 turns carried reasoning. That gap is now
  closed and measured.
- **Two of the four failures ended at 6 steps, not 10.** They were stopped by a guard rather than
  by the budget. A stuck thinking model is now abandoned instead of being nudged until the money
  runs out — which is worth more than it looks: those runs cost half of what they used to.

Per-task detail is in `results/humanevalfix.json` and `results/workshop.json`.

# Safety

Unchanged, and that is the point — `tools/` and `sandbox/` are the same code as the last two
lessons. Path confinement is a property of the tools, execution confinement a property of the
sandbox, and neither has anything to do with whether the model reasons.

Two names differ in this edition, and getting them wrong fails at solve time rather than build
time:

```bash
python run.py agentgraph docker-build          # builds agentgraph-sandbox:latest
AGENTGRAPH_SANDBOX=docker python run.py agentgraph solve tasks/workshop/01-shopcart --verbose
```

One thing did get riskier, and it is not the sandbox. `max_tokens` went from 1024 to **4096**,
because a single reply is now the reasoning *plus* a complete file. Too low a cap truncates the
reply and loses the tool call at the end of it — the model appears to stop acting for no reason.
Too high, on a small context window, is the peak-prompt number above.

# Where to go from here

The course stops at three ideas — tools, a loop, and a verification-based stop condition — plus
the thinking guard you just added. Deliberately not built, roughly in order of what would pay off
next on these numbers:

- **Context management.** The clearest gap in the table above. Trimming or summarising old turns,
  or dropping stale reasoning from the history, is what stands between this agent and a task
  bigger than a one-file bug.
- **Planning as its own phase.** The model plans inside a turn now; nothing makes it commit to a
  plan across turns or notice when it has abandoned one.
- **Reflection / self-critique.** No separate pass where the model reviews its own diff before the
  tests do.
- **Parallel tool calls.** One at a time here, on purpose — `max_concurrency=1` is what keeps the
  test result honest. Doing it properly means knowing which calls are safe to overlap.
- **Multi-agent coordination.** One model, one graph, no delegation.

Each of those is a fair amount of work and none of them is magic. If you take one thing from the
whole course, make it the stop condition: three editions in, the thing that decides whether an
agent is trustworthy is still that it believes the test suite rather than the model.

No **Check** on this step — nothing here is graded.
