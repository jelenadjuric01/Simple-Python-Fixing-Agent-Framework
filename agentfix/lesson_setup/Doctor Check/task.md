# Check your setup

## One command

Every command in this course runs the same way: **open the terminal, stay where it opens — the
course root — and use `run.py`.**

```bash
python run.py doctor
```

You never need to change directory, and you never need to leave the Course View.

If that says it cannot find the agent's code, open the next lesson — **Build the agent** — and
click its first step once, then come back. The guided project's working directory is created the
first time you open it. You will run `doctor` again there anyway, so you can also just read on
and do it then.

## What `doctor` reports

`doctor` checks your Python version and free RAM, that Ollama is installed, that its server
answers, that the derived model exists, that the loaded context window is 16384, does one
warmed-up timed generation, and runs one sandboxed test execution. It prints `[PASS]`/`[FAIL]`
per check and a final `READY <rate> tok/s`, or a remedy command for each failure.

Two lines decide your tier: `ram` and `context window`.

- **`ram`** tells you, in plain numbers, whether you can run an 8 GB model comfortably. This is
  the line `setup.py` uses to decide whether you get the `mellum2` tier or fall back
  to `qwen`.
- **`context window`** tells you whether the `ollama create` step actually took effect. If it
  reads `context window: 4096` instead of `16384`, the `ollama create` step was skipped — and
  the agent will lose its own system prompt on long runs, because Ollama's default context
  drops the *earliest* messages first once the conversation grows past 4,096 tokens. That
  earliest message is the one telling the agent it is not finished until the tests pass.

If either of those lines looks wrong, go back to the previous step before continuing.
