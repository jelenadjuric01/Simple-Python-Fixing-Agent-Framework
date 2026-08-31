# Now try it at a real model

Lets see how this no framework agent works with a real model and tasks.

From the terminal at the course root:

    python run.py solve tasks/workshop/01-shopcart --verbose

Then the harder one, where the bug is **not** in the file the failing test points at — which
is why `list_files` and `read_file` earn their place:

    python run.py solve tasks/workshop/02-invoice --verbose

`--verbose` prints the trace you wired up in Stage 2. Read it. You should see the model call
`run_tests`, look around, write a file, and run the tests again. That last call is the one
that ends the run, because of what you wrote in Stage 3.

If it burns all ten steps and prints `NOT SOLVED`, that is not necessarily your bug — a 12B
model does not fix every task. 

You can try running a testing suite of all three workshop tasks. 
    
    python run.py eval --suite workshop --limit 3

# How well does it actually work?

This is a discussion, not an exercise — do not run a live eval, it takes 8 minutes. The
numbers below are pre-computed and shipped with the repo.

## HumanEvalFix (20 tasks)

Running the agent over the 20-task HumanEvalFix workshop suite took **8m09s wall clock** and
**185,235 tokens** at **51 tok/s**. pass@1 is **0.60 (12/20)**, median 7 steps, max 10 (the
step-budget cap). Every one of the 8 failures spent the full 10 steps.


## Where to look

The raw numbers behind both figures are in `results/precomputed/humanevalfix.json` and
`results/precomputed/workshop.json`. Open them if you want the per-task detail —
just don't run a live eval to reproduce them; it's 8 minutes for the same numbers you already
have.

# Sandbox safety

Your agent executes model-written code. Here is what that means and what production systems do
about it.

Two boundaries, at two different layers:

- **The tool layer confines paths.** `resolve_in_root` rejects any path that would escape the
  task's working directory *before* a read or write happens — the model can ask for
  `../../etc/passwd`, but the tool refuses to touch anything outside its sandboxed root.
- **The sandbox confines execution.** When test code actually runs, it runs with no network
  access, memory/pid/CPU caps, and as a non-root user — so even code the model wrote that
  behaves badly (an infinite loop, an attempt to phone home, a fork bomb) is contained.

These are readable the path confinement lives in
`agentfix/tools/fs.py`, and the execution sandbox lives in
`agentfix/sandbox/docker_backend.py` and `Dockerfile.sandbox`, both under the guided project's
working directory `agentfix/lesson_build/task/`.

## Trying it yourself (optional — needs a running Docker daemon)

Optional, and it needs a running Docker daemon. 

```bash
docker info
python run.py docker-build
AGENTFIX_SANDBOX=docker python run.py solve tasks/workshop/01-shopcart --verbose
AGENTFIX_SANDBOX=docker python run.py eval --suite workshop --limit 3
```

