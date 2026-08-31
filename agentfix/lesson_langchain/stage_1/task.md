# Stage 1 — Decide when a run is allowed to end

**Imports underlined red?** Right-click the lesson directory and choose **Mark Directory as** → **Sources Root**. That is all it needs — the code itself is fine.

Open `agentlang/agent/graph.py` and find `TODO: EXERCISE(stage-1)` inside `route_after_agent`.

The graph is already wired: `agent_node` takes a model turn, `tools_node` runs whatever the
model asked for, `nudge_node` sends it back to work. What is missing is the edge between them —
the function that looks at the turn that just happened and says where the run goes next.

Return one of three things: `"tools"`, `"nudge"`, or `END`.

This is the only place a run can end **successfully**, and the framework has no opinion about
it whatsoever. LangGraph will happily route on whatever you return; whether the agent is honest
about being finished is entirely this function.

What you have to work with:

- `message.tool_calls` — what the model asked for on this turn (empty if it replied with prose)
- `is_done(state)` — the verdict
- `state["step"]` — the turn just taken, and `max_steps` — the budget

<div class="hint" title="Where do I start?">

Three cases, and they are easiest to get right in the order the model produces them: the model
asked for tools, the model said something, or the model has run out of road.

Handle the tool calls first and return early. Everything after that line is the prose case.

</div>

<div class="hint" title="What about a turn that asks for tools while the tests are green?">

Tool calls never end a run on their own — always execute them and loop back, so the model gets
to see the results. Deliberately skip the `is_done` check on that branch: "done" belongs on a
turn where the model had nothing more it wanted to do.

</div>

<div class="hint" title="The model says it fixed it. Is that enough?">

No, and this is the whole design. Read `is_done` a few lines up: it reads `state["tests_passed"]`,
which only ever becomes true by folding a real `ExecResult` out of a tool answer. A model that
declares victory without ever running the tests must **not** end the run — it gets nudged like
any other prose on a red suite.

</div>

<div class="hint" title="Won't that nudge a stubborn model forever?">

It would, so the budget has to win. On a prose turn, check the verdict first, then the budget,
and nudge only if neither applies — a model that is out of steps stops whether or not it was
making progress. `route_after_tools` already does the same thing on the other side of the loop;
read it for the shape.

</div>

<div class="hint" title="Last resort — the four lines in words">

If the model asked for tools → `"tools"`.
Otherwise, if the verdict says the tests pass → `END`.
Otherwise, if the step budget is spent → `END`.
Otherwise → `"nudge"`.

</div>

Press **Check** when you are done.
