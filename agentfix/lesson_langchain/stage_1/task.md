# Stage 1 — Decide when a run is allowed to end

Open `agentlang/agent/graph.py` and find `TODO: EXERCISE(stage-1)` inside `route_after_agent`.

The graph is already wired: `agent_node` takes a model turn, `tools_node` runs whatever the model
asked for, `nudge_node` sends it back to work. What is missing is the edge between them — the
function that looks at the turn that just happened and says where the run goes next.

## Why this edge is yours and not the framework's

LangGraph will route on whatever you return. It has no opinion about which turns mean "carry on"
and which mean "this run is over", and it cannot have one: whether an agent is *finished* is a
claim about your task, not about graphs. Everything else in this loop — dispatch, message
history, retries, the trace — the framework already wrote.

So this is the only place a run can end **successfully**, and the whole question is what the
agent is willing to believe. The model will tell you it has fixed the bug. It will say so
fluently, and sometimes without having run anything. The previous lesson's answer was to believe
the test suite instead, and `is_done` — near the top of the file, above the nodes — is where
that verdict lives. Read it before you write anything, including where the value it reads can possibly
come from.

## What the router has to be right about

- A turn that **asked for tools**. Something is about to happen that the model has not seen the
  result of yet.
- A turn of **prose** while the suite is red. The model has stopped acting, which is not the same
  thing as being done.
- A model that will not converge. Nudging is a correction, and a correction that can be repeated
  forever is not a stop condition at all.

`route_after_tools`, directly below, is the same kind of decision on the other side of the loop
and it is already written. Its docstring explains what it deliberately does *not* check, which is
worth understanding before you decide what yours checks.

<div class="hint" title="What am I choosing between, and what can I look at?">

The function returns a string, and there are exactly three answers: `"tools"` and `"nudge"` name
the nodes to go to, and `END` (imported from `langgraph.graph`) finishes the run.

Everything you need is already in scope, and the docstring lists it:

- `message.tool_calls` — what the model asked for on this turn, empty if it replied with prose
- `is_done(state)` — the verdict
- `state["step"]` — the turn just taken, and `max_steps` — the budget

No new imports, no new state keys.

</div>

<div class="hint" title="Where do I start?">

There are three situations and they are easiest to get right in the order the model produces
them: the model asked for tools, the model said something, or the model has run out of road.

Handle the tool calls first and return early. Everything after that line is then the prose case,
and you never have to think about tool calls again.

</div>

<div class="hint" title="What about a turn that asks for tools while the tests are green?">

Tool calls never end a run on their own — always execute them and loop back, so the model gets to
see the results. Deliberately skip the `is_done` check on that branch: "done" belongs on a turn
where the model had nothing more it wanted to do.

</div>

<div class="hint" title="The model says it fixed it. Is that enough?">

No, and this is the whole design. Read `is_done` further up the file: it reads
`state["tests_passed"]`, which only ever becomes true by folding a real `ExecResult` out of a tool
answer. A model that declares victory without ever running the tests must **not** end the run — it
gets nudged like any other prose on a red suite.

</div>

<div class="hint" title="Won't that nudge a stubborn model forever?">

It would, so the budget has to win. On a prose turn, check the verdict first, then the budget, and
nudge only if neither applies — a model that is out of steps stops whether or not it was making
progress. `route_after_tools` already does the same thing on the other side of the loop; read it
for the shape.

</div>

<div class="hint" title="Last resort — the four lines in words">

If the model asked for tools → `"tools"`.
Otherwise, if the verdict says the tests pass → `END`.
Otherwise, if the step budget is spent → `END`.
Otherwise → `"nudge"`.

</div>

Press **Check** when you are done.
