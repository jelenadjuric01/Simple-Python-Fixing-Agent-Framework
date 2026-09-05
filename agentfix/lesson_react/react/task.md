# Stage 1 — A model that thinks before it acts

## What "thinking" actually is

Same 12B weights as the model you have been running, different checkpoint: this one is trained
to reason inside `<think>...</think>` before it answers. One flag on the client —
`reasoning=True` — asks Ollama for that thinking and hands it back on **its own channel**,
`AIMessage.additional_kwargs["reasoning_content"]`, instead of leaving the tags inline in
`content`. `reasoning_of` in `agentgraph/agent/trace.py` is the single line that knows where it
lives.

That channel matters more than it sounds. With the tags inline, the model's deliberation ends up
inside the next prompt, inside the trace, and — the expensive one — inside the "complete file
contents" that `write_file` is handed. Separating the two is the difference between reasoning you
can read and reasoning that quietly corrupts your outputs.

**There is no think step, and no new node.** This is what ReAct actually means: the model thinks
and acts in the *same* turn. The graph you built in the previous lesson is unchanged in shape.

## Why it is worth a lesson

The previous edition's honest summary was that its agent did not reason: seven tool-calling turns
carrying no deliberation, and one closing explanation *after* the fix was already verified. It
worked by trying things. A thinking model plans before it acts, and on this workshop's tasks that
is worth a large jump in pass@1 — you will see the numbers in the next step.

It also introduces a failure mode the Instruct model essentially never had.

## What changed, and what did not

Not the graph. Two *decisions* about what a turn was:

- **A turn with no tool call is no longer rare.** The old model acted on every turn but the last,
  so "the model replied without acting" meant "it is finishing up" and the answer was a nudge. A
  thinking model will happily spend an entire turn reasoning and ask for nothing — and nudging
  that forever is an unbounded loop wearing a step budget as a disguise. Hence `idle_turns` in
  `AgentState` and `MAX_IDLE_TURNS` in `graph.py`: a loop guard for thinking, alongside the one
  for actions you already wrote.
- **The action guard must ignore reasoning.** Read `call_signature` — it deliberately hashes only
  the tool name and its arguments. A model that reasons its way to the same useless call by a
  fresh route every time is still stuck, and novel thinking must not buy a repeated call another
  turn. That one is already written; read it and make sure you see why.

Also new, and free: `reasoning_turns` in the state, so a run can report how many of its turns
actually thought — the number the previous workshop could not produce.

## Your job

Open `agentgraph/agent/graph.py`. There are four `TODO` markers.

| # | Where | What it decides |
|---|---|---|
| 1 | `acted()` | what counts as a turn that *did* something |
| 2 | `agent_node`'s returned state | keeping `idle_turns` current |
| 3 | `nudge_node` | which of the two corrections to send |


They are one decision split four ways: **reasoning is not an action**, and an agent has to be
able to tell the difference.

<div class="hint" title="1 — what counts as acting?">

One line. The question is whether this turn asked for anything to *happen*, and there is exactly
one thing in an `AIMessage` that does that.

Two traps, both from the docstring: prose is not an action either, and a turn that reasoned
**and** called a tool very much did act. Do not let the reasoning enter into it at all.

</div>

<div class="hint" title="2 — why can't I just add one?">

Read the comment on `idle_turns` in `agentgraph/agent/state.py`. It is the only key in the state
with no reducer, and the reason is that it has to **reset**: a reducer is handed only
`(current, incoming)`, so it cannot tell "one more idle turn" from "that turn acted, start
again".

So unlike every other key in that returned dict, this one is not a delta — it is the absolute
value. `agent_node` is its only writer, and it is the node that knows which of the two just
happened. Your answer from TODO 1 is what tells it.

</div>

<div class="hint" title="3 — which nudge?">

Both texts already exist near the top of the file. They differ because a correction that
misdiagnoses the turn is one the model can reasonably ignore: "read the failure and write a fix"
is the wrong thing to say to a model that just spent 400 tokens reasoning about the failure.

`reasoning_of(message)` tells you which turn you are looking at — and note that it is
`reasoning_of` here, not `acted`. By the time `nudge_node` runs, the turn is known not to have
acted; the open question is whether it *thought*.

</div>



Press **Check** when you are done.
