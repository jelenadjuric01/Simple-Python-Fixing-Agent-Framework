# What you are about to build

In the previous lesson you read a hand-written agent and ran it: a plain `for` loop, a tool
dispatch, and a stop condition, with no framework anywhere. This lesson is **the same agent
rebuilt on a framework** — LangGraph for the graph, LangChain for the model and tool interfaces
— and it asks the only question worth asking about a framework: *which parts of my agent does it
actually write for me?*

You do not need to remember that code line by line — where this version differs from the
hand-written one, the comments say so.

The whole project is here and every file is readable, the comments included. Only one file is
yours to edit, and you will edit it in two places:

| Stage | You write | File |
|---|---|---|
| 1 | `route_after_agent` — where a model turn goes next, and the only place a run can end successfully | `agentlang/agent/graph.py` |
| 2 | the loop guard inside `tools_node` — refusing a call the model has already made | `agentlang/agent/graph.py` |

Notice what is *not* on that list. The tools, the dispatch, the message history, the retries on
a bad argument — all of that is the framework's, and you will not write a line of it. What
is left over is exactly the part the framework does not have an opinion about: **when the run
stops, and what to do about a model that has stopped making progress.**

# The three ideas, again

Loop, tools, verification — the same three ideas the hand-written agent was built out of.

The framework gives you the loop and the tools. It does not give you verification, and it does
not give you a policy for a stuck model. That asymmetry is the entire lesson — both stages here
sit on the side of the line the framework left empty.

Concretely, what LangGraph/LangChain contribute in this repo:

- **`ToolNode` runs the calls.** Dispatch, ordering, unknown tool names, argument validation,
  error recovery — one invocation per turn.
- **`add_messages` makes the history append-only by construction**, which keeps the prompt
  prefix byte-stable and the server's KV cache valid.
- **Reducers on `AgentState`** accumulate the counters, so `agent_node` returns deltas and never
  reads the old value.
- **Callbacks carry the trace.** No node contains tracing code; the tracer is handed to the
  graph once.
- **The checkpointer** snapshots state after every node, so a run can be resumed or inspected
  step by step.

And what it does not contribute, which is what you are here for:

- **The stop condition.** `is_done` believes the test suite, not the model's claim about its own
  work. No framework can supply that — it is a fact about *your* task.
- **The loop guard.** LangGraph has no hook for it at all. LangChain 1.x gives you the seam
  (`wrap_tool_call`), but "three identical calls means the model is stuck" is still your policy.
- **The step budget.** `recursion_limit` counts node executions, not model turns.

`agentlang/agent/prebuilt.py` builds the same agent again out of `create_agent` — the framework's
prebuilt loop — purely so you can read the honest comparison. It is not the path `solve` takes.

## What you will find in this repo

```text
agentlang/
├── agent/
│   ├── graph.py       ← the file you edit
│   ├── prebuilt.py
│   ├── state.py
│   └── trace.py
│
├── eval/
│   ├── runner.py
│   └── humanevalfix.py
│
├── llm/
│   ├── client.py
│   └── fake.py
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

results/
├── humanevalfix.json
├── workshop.json
└── precomputed/

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

### `agent/` — the agent itself

`graph.py` is the one to read. It is the whole agent as a LangGraph `StateGraph`: three nodes
(`agent_node`, `tools_node`, `nudge_node`) and two routers (`route_after_agent`,
`route_after_tools`). `state.py` defines `AgentState` and its reducers — including
`tests_passed`, the verdict that has to live in the state rather than on a tool if a resumed run
is to stay correct. `trace.py` records what happened, via callbacks. `prebuilt.py` is the
`create_agent` comparison described above.

### `llm/` — model interface

`client.py` builds the real OpenAI-compatible chat model used with Ollama/vLLM; `fake.py` is a
deterministic `FakeChatModel` that lets the whole test suite exercise the real wiring with no
model process running anywhere.

### `tools/` — what the model is allowed to do

`base.py` holds the shared tool plumbing and the `WorkspaceChanged` artifact, `fs.py` implements
`list_files` / `read_file` / `write_file` (and `resolve_in_root`, which refuses any path escaping
the workspace), and `tests_tool.py` runs the project's test suite. Each tool reports what it did
as a `ToolMessage` artifact, and the graph folds those artifacts into its state — which is why no
node needs a reference to a tool.

### `sandbox/` — safe execution

Where commands actually run. The default backend is a hardened subprocess; the Docker backend
adds no network, memory/pid/CPU caps, and a non-root user.

### `tasks/` — task loading

`loader.py` turns a task directory into a `Task` and yields a disposable workspace containing a
fresh copy of the buggy project. Every run starts from the pristine template.

### `eval/` — evaluation

Runs the agent over a suite of tasks and records pass rate, steps, tokens, and wall clock.
`humanevalfix.py` supports the HumanEvalFix benchmark subset.

### Top-level `agentlang` modules

* `config.py` — model and environment configuration.
* `runner.py` — the wiring: `task dir → load_task → workspace copy → tools bound to that copy → run_agent`. Short, and worth reading right after `graph.py`.
* `doctor.py` — checks the environment before you run anything.
* `cli.py` — `doctor`, `solve`, `eval`.

### `results/` — precomputed evaluation output

So you can inspect numbers without spending eight minutes reproducing them.

### Runtime configuration

`Dockerfile.sandbox` defines the isolated Docker environment for the Docker backend; `Modelfile`
configures the local Ollama model.

## Overall flow

```text
Task
  ↓
runner.py            (workspace copy, tools bound to it)
  ↓
build_graph          (nodes + routers + checkpointer + tracer)
  ↓
        ┌──────────────┐
   ───▶ │  agent_node  │  one model turn
        └──────┬───────┘
               │  route_after_agent   ← STAGE 1
      ┌────────┼─────────┐
      ▼        ▼         ▼
   tools    nudge       END
      │        │
      │  route_after_tools
      └────────┴──────────▶ back to agent_node
  ↓
AgentResult
  ↓
evaluation / results
```

`agent/graph.py` decides what happens next, `llm/` talks to the model, `tools/` gives the model
actions, `sandbox/` executes them safely. Everything else prepares tasks, wires the pieces
together, or scores the result.

