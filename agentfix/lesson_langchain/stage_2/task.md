# Stage 2 — Refuse a call the model has already made

Open `agentlang/agent/graph.py` and find `TODO: Stage 2` inside `guard_node`.
(Your Stage 1 routing is still there and still needed — leave it alone.)

## The failure this exists to catch

Small models get stuck. Not crashed, not confused — stuck in the plainest way possible: they call
`read_file` on the same path, get the same answer, and call it again. Right now nothing stops
that. Every call the model makes is executed, however many times it asks, until the step budget
runs out and the run is scored as a failure — a failure that looks like a hard task rather than a
model going in circles.

Your Stage 1 router cannot see it. By the time routing happens the turn has already been executed,
and from the outside a repeated call is indistinguishable from a productive one: the model acted,
so the loop carries on.

## Why this one is yours

Seams for it do exist, and it is worth knowing where, because sooner or later you will go looking.
LangChain 1.x middleware gives you `wrap_tool_call`, which runs around every tool invocation;
`create_react_agent` takes a `post_model_hook` that runs after the model has proposed its calls.
LangChain even ships a `ToolCallLimitMiddleware` that blocks over-budget calls and answers each one
with a `ToolMessage`.

One of them is already in your graph, and it is worth knowing which. `post_model_hook`'s router
dispatches the calls that have **no reply yet** — so `guard_node` and `route_after_guard` are that
router, reproduced by hand, and answering a call is exactly what refuses it. You are not fighting
the framework here; you are filling in the one thing it left blank.

Because none of them is the *answer*. `ToolCallLimitMiddleware` counts calls per tool *name* and
never looks at the arguments, so it cannot tell three reads of three files from three reads of the
same file — a budget, not a repetition detector. The seams are places to put a decision: *"an
identical call means the model is stuck"* is a claim about your agent, and *"so refuse it and tell
the model why"* is a policy. No framework will make either for you, and `ToolNode` will happily
execute the same call all day.

The seams also cost something, which is why the guard's memory lives where it does.
`agentlang/agent/prebuilt.py` builds this guard on `wrap_tool_call`, and its counters live on the
middleware instance: they survive no checkpoint, and they leak into the next run. In `AgentState`
they are scoped to the run, because the state is.

## What your code decides

Your loop already iterates over the calls the model made for this turn. For each one:

- **Does this call run at all?**
- **If it does not, what does the model hear back?** Read the docstring of `guard_node` before you
  write anything — it states the one rule about answering calls that the API does not forgive, and
  breaking it produces an error one whole turn away from the code that caused it.
- **What is the *next* call compared against?** The guard needs a baseline and a count, and both
  have to survive into the next turn. They are already in the state; `route_after_tools` reads one
  of them.

<div class="hint" title="What do I have to work with?">

Everything is already in scope in that loop — the comment above the marker names it:

- `current` — this call's signature; `signature` — the previous executed call's
- `hits` — how many consecutive repeats have happened so far
- `name` — this call's tool name, and `call["id"]` its id
- `guard_observation(name, hits)` — the text to send back for a refusal
- `tracer.note("tool", name, ...)` — for recording something the *graph* decided
- `replies` — the messages this turn answers with; `runnable` — the calls that will be executed

Both `signature` and `hits` are local copies of state values, and the `return` at the end of
`guard_node` already hands them back. You do not add a state key or touch the reducers.

</div>

<div class="hint" title="How do I know it is the same call?">

`call_signature` further up already answers that: tool name plus sorted arguments, so
`{"a": 1, "b": 2}` and `{"b": 2, "a": 1}` are correctly the same call. `requested_calls` has
paired every call with its signature for you, which is what `current` is. Comparing it to
`signature` is the whole test.

</div>

<div class="hint" title="Can I just skip the call?">

Not silently. Read the docstring of `guard_node`: the API requires an answer for **every**
`tool_call_id` the model produced. Drop one and the *next* request is rejected, one turn away from
the code that caused it.

So a refused call still appends a `ToolMessage` — with the refusal text as its content, and
carrying that call's `id` and `name` — then moves on to the next call rather than falling through
to the code that queues it for execution.

</div>

<div class="hint" title="What happens to hits?">

It has to move in both directions, and it is the counter `route_after_tools` reads to abandon a
stuck model — check what it compares against. A repeat increments it. A call that is *not* a repeat
means the model is making progress, so reset it to zero and remember this call as the new baseline
for the next one.

Note that `guard_observation` escalates its wording as `hits` grows, so increment before you build
the message, not after.

</div>

<div class="hint" title="Why trace a refusal at all?">

Because it is the one line in the trace that no tool produced. Everything else you read there
happened because a tool ran; this happened because the graph decided it should not. Without the
note, a guarded run looks like a model that mysteriously stopped making calls.

</div>

<div class="hint" title="What should Tracer have as a detail?">

It should have f"guarded — identical call #{hits + 1} in a row". 
</div>

<div class="hint" title="Last resort — the shape">

Inside the loop, before the call is queued:

- if this signature equals the previous one: bump `hits`, append a `ToolMessage` carrying
  `guard_observation(name, hits)` with the call's `id` and `name`, write a `tracer.note`, and
  `continue`.
- otherwise: set `hits` to 0, set `signature` to `current`, and fall through so the call gets
  queued.

</div>

Press **Check** when you are done.
