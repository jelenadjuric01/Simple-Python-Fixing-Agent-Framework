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
`agentlang/tools/fs.py`, and the execution sandbox lives in
`agentlang/sandbox/docker_backend.py` and `Dockerfile.sandbox`, both under the guided project's
working directory `agentfix/lesson_build/task/`.

## Trying it yourself (optional — needs a running Docker daemon)

Optional, and it needs a running Docker daemon. 

```bash
docker info
python run.py docker-build
AGENTFIX_SANDBOX=docker python run.py solve tasks/workshop/01-shopcart --verbose
AGENTFIX_SANDBOX=docker python run.py eval --suite workshop --limit 3
```

