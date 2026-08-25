# Next steps

## The Thinking variant

The natural next step from here is the Mellum2 **Thinking** variant: same code, one environment
variable, and it exposes visible `<think>` blocks showing the model's reasoning before it commits
to a tool call or a final answer. Nothing about the loop, the tools, or the stop condition you
built changes — only the model's own output gets richer.

## What was deliberately left out

This course, like the workshop it is ported from, is scoped to three ideas: tools, a loop, and a
verification-based stop condition. On purpose, it does not build:

- **Planning** — no phase where the model lays out a multi-step plan before acting.
- **Reflection / self-critique** — no separate pass where the model reviews its own output before
  it counts.
- **Parallel tool calls** — one tool call per turn, not several dispatched at once.
- **Multi-agent coordination** — one model, one loop, no delegation to sub-agents.
