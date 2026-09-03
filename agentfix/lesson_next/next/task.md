# Next steps

Three editions in, you have built the two decisions no framework makes for you — where a run is
allowed to end, and what to do about a model that has stopped making progress — and you have the
numbers for what reasoning bought and what it cost. This step is what to do with that.

## What the numbers point at next

From the 20-task HumanEvalFix runs the previous lesson quoted:

| Edition | pass@1 | median steps | tokens | wall clock | peak prompt |
|---|---|---|---|---|---|
| No framework, Instruct | 0.60 (12/20) | 7 | 185,235 | 8m08s | 2,998 |
| LangGraph, Instruct | 0.45 (9/20) | 10 | 237,651 | 8m15s | 3,929 |
| **LangGraph, Thinking** | **0.80 (16/20)** | **5** | **415,333** | **52m25s** | **12,599** |

The interesting number is not the pass rate — it is **12,599**. That is the peak prompt against a
16,384-token window, on a benchmark of *small, single-file* bugs. Reasoning is generated tokens,
and every earlier thought is re-sent on every later turn, so the thing that most limits this agent
is no longer the loop, the tools or the model. It is the history.

So, roughly in the order that would pay off:

### 1. Context management

Trimming, summarising, or dropping stale reasoning from the history before it is re-sent. Nothing
in this course does any of it: `add_messages` appends, and the prompt grows until the window ends
the run. This is the single change that stands between the agent you built and a task bigger than
one file.

Worth knowing before you try it: the prompt prefix being **byte-stable** is what keeps the server's
KV cache valid between turns. Rewriting old messages invalidates it, so a naive summariser can cost
more latency than the tokens it saves. The interesting designs only ever touch the *tail*.

### 2. Planning as its own phase

A thinking model plans *inside* a turn. Nothing makes it commit to a plan across turns, and nothing
notices when it has quietly abandoned one. A plan that survives turns is state — which means it is
a field on `AgentState` and a routing decision, exactly like the two you already wrote.

### 3. Reflection and self-critique

There is no pass where the model reviews its own diff before the tests do. Note what this competes
with: your stop condition already has a reviewer that cannot be talked round, and it is the test
suite. Reflection is worth adding where tests are *weak* — and worth being suspicious of where they
are strong.

### 4. Parallel tool calls

One call per turn here, on purpose: `max_concurrency=1` is what keeps a test result honest, because
two writes and a test run overlapping means the result no longer describes a known state of the
files. Doing it properly means deciding which calls are safe to overlap — reads, almost always;
anything that writes, almost never.

### 5. Multi-agent coordination

One model, one graph, no delegation. The reason to want it is context: a sub-agent gets its own
window, so a big read can happen somewhere that does not pollute the main history. The reason to be
careful is that every hand-off is a place where "done" can be claimed rather than verified — the
same problem as Stage 1, one level up.

## Where the model itself could go

- **A larger Mellum2, or a larger context window.** The cheapest experiment on this list: nothing
  in your code changes, and the peak-prompt number above tells you what it would buy.
- **The Instruct model with your thinking-era guards.** Worth one run. The idle-turn guard and the
  action guard are not reasoning-specific, and the previous lesson's 0.60 → 0.45 gap was noise, not
  a cost of the framework — making the stop condition real moved pass@1 by more than that on its
  own.
- **A different tool set.** These three editions share four tools. A patch-based edit tool instead
  of whole-file `write_file` would cut the largest single message the agent ever sends — which is
  the other half of the context problem.

## What this course deliberately left out

So you know what you have *not* seen, rather than assuming it does not exist:

- **Retrieval.** The agent finds files by listing and reading them. No index, no embeddings.
- **Human-in-the-loop.** LangGraph's checkpointer is in the graph you built and makes an interrupt
  cheap; there is no approval gate wired to it.
- **Streaming.** Every reply is awaited whole, which is why a slow turn looks like a hang.
- **Cost and rate limiting.** A local model has no bill attached, so nothing here budgets money —
  only steps and tokens.
- **Anything multi-file or repository-scale.** Every task is one small module and its test.

## If you keep going

The three implementations, if you would rather read or fork them outside the IDE:

- <https://github.com/jelenadjuric01/agentfix-workshop> — no framework
- <https://github.com/jelenadjuric01/agentfix-langchain> — LangGraph + LangChain
- <https://github.com/jelenadjuric01/agentfix-react> — the thinking edition

`python run.py agentgraph eval --suite humanevalfix` is how the numbers in the table were produced;
`--limit` makes it affordable. Change one thing, run it again, and compare against
`results/precomputed/`.

And if you take one thing from all three editions, make it the stop condition. What decides whether
an agent is trustworthy is not the framework under it or the reasoning inside it — it is that it
believes the test suite rather than the model.

The last step of the course is optional: how to take all of this back off your machine.
