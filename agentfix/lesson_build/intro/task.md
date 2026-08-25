# What you are about to see

The whole agent is in front of you. Every file is readable — the comments are part of the
lesson.

## If you want to come back to everything in this file, see [README.md] in the additional files.

# What is an agent?

Loop, tools, verification.

An agent is a while-loop around a chat model that can call functions and sees the result.
Nothing more magical than that.

That definition is not a simplification for this course — it is literally what you are about
to build. The loop in this repo is about 15 lines. The rest of `run_agent` is tracing and token
accounting: bookkeeping around the loop, not the loop itself.

Keep this in your head as you go through the next few steps and into the framework lesson: at
every point where the mechanics feel like they are piling up, ask which of the three ideas —
loop, tools, verification — the code in front of you belongs to.

## What will you find in this repo and how to understand it?


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
├── doctor.py
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

### `agent/` — Agent logic

The core of the project. `loop.py` contains the actual agent loop: it sends the conversation to the model, executes requested tools, feeds results back, and continues until the tests pass or a limit is reached. `trace.py` records what happened during a run.

### `llm/` — Model interface

Everything related to communicating with the language model. `client.py` contains the real OpenAI-compatible client used with Ollama/vLLM, while `fake.py` provides a deterministic fake model for testing. `types.py` defines the common interfaces and data structures used by both.

### `tools/` — Agent capabilities

Defines what the model is allowed to do. `base.py` provides the tool abstraction and registry, `fs.py` implements filesystem tools such as listing, reading, and writing files, and `tests_tool.py` lets the agent run the project's tests.

### `sandbox/` — Safe code execution

Controls where and how commands are executed. The default backend uses hardened subprocess execution, while the Docker backend provides stronger isolation when needed.

### `tasks/` — Task loading

`loader.py` turns a task definition into a `Task` and creates a temporary workspace containing a fresh copy of the buggy project for the agent to modify.

### `eval/` — Evaluation

Runs the agent across collections of tasks and records metrics such as pass rate, number of steps, token usage, and execution time. `humanevalfix.py` provides support for the HumanEvalFix benchmark subset.

### Top-level `agentfix` modules

* `config.py` — model and environment configuration.
* `runner.py` — connects a task, workspace, tools, model client, and agent loop into one complete solve operation.
* `doctor.py` — checks that the environment is correctly configured before running.
* `cli.py` — command-line interface for commands such as `doctor`, `solve`, and `eval`.

### `tasks/` — Buggy projects / benchmark inputs

Contains the actual tasks given to the agent. `workshop/` contains small example projects such as `01-shopcart`, `02-invoice`, and `03-parser`. `humanevalfix/` contains the selected HumanEvalFix benchmark tasks.

### `results/` — Evaluation results

Stores precomputed evaluation outputs, allowing results to be inspected or compared without rerunning the entire benchmark.

### `scripts/` — Project utilities

Contains development/helper scripts, such as `vendor_humanevalfix.py` for preparing the HumanEvalFix task data.

### Runtime configuration

`Dockerfile.sandbox` defines the isolated Docker environment used by the Docker sandbox backend, while `Modelfile` configures the local Ollama model used by the agent.

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

The main idea is that **`agent/loop.py` decides what happens next, `llm/` talks to the model, `tools/` gives the model actions, and `sandbox/` executes those actions safely**. Everything else primarily prepares tasks, wires those pieces together, or evaluates the result.
