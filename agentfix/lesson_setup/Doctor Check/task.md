# Check your setup


Every command in this course runs the same way: **open the terminal, stay where it opens — the
course root — and use `run.py`.**

```bash
python run.py doctor
```

You never need to change directory, and you never need to leave the Course View.


> **On the `colab` tier, skip this command.** It will fail, and that is fine. `doctor` looks for
> Ollama, a derived model, and a server answering on `localhost:11434` — on this tier none of
> those live on your laptop, they live in the Colab runtime. Nothing is broken and there is
> nothing to fix. Read the rest of this lesson so you know what `doctor` reports, then continue;
> `notebooks/agentfix.ipynb` runs the check for you in Colab when you get to **Now point it at a
> real model**. Everything else in the course — every lesson, every exercise, every exercise test
> — you do on your own machine exactly as written, because the exercise tests use a scripted fake
> model and need no model at all.

If that says it cannot find the agent's code, open the next lesson — **Agent with no framework** — and
click its first step once, then come back. The guided project's working directory is created the
first time you open it. You will run `doctor` again there anyway, so you can also just read on
and do it then.

## One doctor per agent

This course contains three agents, and every command — `doctor` included — runs against one of
them. The first word chooses which:

```bash
python run.py doctor                  # Agent with no Framework  (the default)
python run.py agentlang doctor        # What about frameworks?
python run.py agentgraph doctor       # What about thinking?
```

The `[run.py]` line printed before every command names the directory it chose, so which agent ran
is never a guess. If it says it cannot find that agent's code, open the lesson once and click its
first step — the working directory is created the first time you do.

## What `doctor` reports

`doctor` checks your Python version and free RAM, that Ollama is installed, that its server
answers, that the derived model exists, that the loaded context window is 16384, does one
warmed-up timed generation, and runs one sandboxed test execution. It prints `[PASS]`/`[FAIL]`
per check and a final `READY <rate> tok/s`, or a remedy command for each failure.

Two lines decide your tier: `ram` and `context window`.

- **`ram`** tells you, in plain numbers, whether you can run **one** 8 GB model comfortably.
  This is the line `setup.py` uses to decide whether you get the `mellum2` tier or fall back
  to `qwen`. Your tier installs two models — a coding one and a thinking one — but the lessons
  use them one at a time, so the second is a disk cost, not a second 8 GB of memory. The one
  case where it *is*: Ollama keeps the previous lesson's model loaded for five minutes, so
  `ollama stop agentfix-mellum2` before you start the thinking lesson on a 16 GB machine.
- **`context window`** tells you whether the `ollama create` step actually took effect. If it
  reads `context window: 4096` instead of `16384`, the `ollama create` step was skipped — and
  the agent will lose its own system prompt on long runs, because Ollama's default context
  drops the *earliest* messages first once the conversation grows past 4,096 tokens. That
  earliest message is the one telling the agent it is not finished until the tests pass.

If either of those lines looks wrong, go back to the previous step before continuing.

## When something is broken rather than unclear

`TROUBLESHOOT.md` in the course root is every environment failure that has actually happened to
someone taking this course, with the command that fixed it — red imports in the IDE, a Windows
variable the IDE cannot see yet, `context window: 4096`, a machine that started swapping in
lesson 4, a lesson 4 trace with no reasoning in it. Check there before assuming your own code is
at fault.
