# Cleaning up

None of this is required. If you plan to keep experimenting, keep everything — the models are the
slow part to get back, and `python run.py doctor` will still pass tomorrow.

But this course pulled **two** multi-gigabyte models onto your machine, possibly installed Ollama,
and may have written two environment variables into your shell profile. If you want your laptop
back exactly as it was, here is every piece of it.

**On the `colab` tier there is nothing to clean on this machine.** The models, the Ollama install
and the three repositories all lived in the Colab runtime, which is discarded when the session
ends. Delete the notebook copy from your Drive if Colab saved one there, and you are done.

### What is on your machine, and what put it there

| What | Created by | Where it lives | Size |
|---|---|---|---|
| `agentfix-mellum2` + its base model | `mellum2` tier | Ollama's model store | ~8 GB |
| `agentgraph-mellum2-thinking` + its base model | `mellum2` tier | Ollama's model store | ~8 GB |
| `agentfix-qwen3` + `qwen3:1.7b` | `qwen` tier | Ollama's model store | ~1.4 GB |
| `MELLUM_MODEL` and `AGENTGRAPH_MODEL` in your shell profile | `qwen` tier only | `~/.zshrc`, `~/.bashrc`, or the Windows user environment | — |
| `.agentfix.env` | `qwen` tier only | course root | bytes |
| `Modelfile.agentgraph-thinking` | `mellum2` tier | course root | bytes |
| `Modelfile.agentfix-qwen3`, `Modelfile.agentfix-qwen3` | `qwen` tier only | course root | bytes |
| Ollama | any tier, if setup installed it | per OS, see below | a few hundred MB |
| `agentfix-sandbox` and `agentgraph-sandbox` Docker images | only if you built them | Docker | a few hundred MB each |
| A uv-installed Python 3.12 | any tier, only where your package manager had none | `~/.local/share/uv` | ~50 MB |

The course has two kinds of agent in it: lessons 2 and 3 run a coding model, lesson 4 runs a
thinking one. On the `mellum2` tier those are two separate models, both on disk regardless of
which lessons you finished. On the `qwen` tier there is one model for all three, because qwen3
both reasons and calls tools.

Open your operating system below. Each block is the complete sequence, in the reverse of the order
setup did it, and every step says which tier created the thing it removes — skip the steps that do
not apply to you.

<details>
<summary><b>macOS</b></summary>

**1. Remove the models** — undoes `ollama pull` + `ollama create`. This is almost all of the disk
space.

```bash
ollama list                                                             # see what you have
```

```bash
# mellum2 tier — the coding model, then the thinking one
ollama rm agentfix-mellum2 hf.co/JetBrains/Mellum2-12B-A2.5B-Instruct-GGUF-Q4_K_M
ollama rm agentgraph-mellum2-thinking hf.co/JetBrains/Mellum2-12B-A2.5B-Thinking-GGUF-Q4_K_M
```

```bash
# qwen tier
ollama rm agentfix-qwen3 qwen3:1.7b
```

Remove the derived model **and** the base model it was built from. The base models are
the multi-gigabyte downloads; deleting only the derived ones frees almost nothing. The two Mellum2
checkpoints are separate 8 GB downloads that share no blobs, so removing one does not shrink the
other.

**2. Remove the model variables** — `qwen` tier only; undoes step 6 of setup.

Setup wrote one marked block into `~/.zshrc` (or `~/.bashrc`, or `~/.config/fish/config.fish`).
Open that file and delete the four lines:

```bash
# >>> agentfix setup >>>
export MELLUM_MODEL=agentfix-qwen3
export AGENTGRAPH_MODEL=agentfix-qwen3
# <<< agentfix setup <<<
```

Then, for the terminal you are standing in right now:

```bash
unset MELLUM_MODEL AGENTGRAPH_MODEL
```

On the `mellum2` tier setup removed that block instead of writing it, so there is nothing to do —
both `echo` commands printing empty lines confirms it.

**3. Delete the files setup wrote in the course root**

```bash
rm -f .agentfix.env Modelfile.agentgraph-thinking Modelfile.agentfix-qwen3 Modelfile.agentfix-qwen3
```

Do **not** delete `Modelfile` — that one ships with the course. Only the `Modelfile.*` files were
generated.

**4. Remove Ollama itself** — optional, and only if you installed it for this course. Do the models
first: uninstalling does not reliably take the model store with it.

```bash
# installed with Homebrew
brew services stop ollama
brew uninstall ollama
```

If you installed the app instead: quit Ollama from the menu bar, then drag **Ollama.app** from
**Applications** to the Trash.

Either way, the leftovers neither route removes:

```bash
rm -rf ~/.ollama
sudo rm -f /usr/local/bin/ollama
```

If you set the loaded-model cap during lesson 4, undo that too:

```bash
launchctl unsetenv OLLAMA_MAX_LOADED_MODELS
```

**5. Remove the Docker sandbox images** — only the ones you built.

```bash
docker rmi agentfix-sandbox agentgraph-sandbox
```

**6. Remove a uv-installed Python 3.12** — only if setup fell back to uv because Homebrew had no
3.12. A `brew`-installed one is a normal package: `brew uninstall python@3.12`, or keep it.

```bash
uv python uninstall 3.12
uv cache clean
rm -rf ~/.local/share/uv ~/.local/bin/uv ~/.local/bin/uvx      # uv itself, if you have no other use for it
```

**7. Check it worked**

```bash
ollama list                                  # no agentfix-… or agentgraph-… entries
echo "$MELLUM_MODEL $AGENTGRAPH_MODEL"       # empty
```
</details>

<details>
<summary><b>Linux, WSL2 and ChromeOS</b></summary>

**1. Remove the models** — undoes `ollama pull` + `ollama create`. This is almost all of the disk
space.

```bash
ollama list                                                             # see what you have
```

```bash
# mellum2 tier — the coding model, then the thinking one
ollama rm agentfix-mellum2 hf.co/JetBrains/Mellum2-12B-A2.5B-Instruct-GGUF-Q4_K_M
ollama rm agentgraph-mellum2-thinking hf.co/JetBrains/Mellum2-12B-A2.5B-Thinking-GGUF-Q4_K_M
```

```bash
# qwen tier
ollama rm agentfix-qwen3 qwen3:1.7b
```

Remove the derived model **and** the base model it was built from, for both. The base models are
the multi-gigabyte downloads; deleting only the derived ones frees almost nothing.

**2. Remove the model variables** — `qwen` tier only; undoes step 6 of setup.

Setup wrote one marked block into `~/.bashrc` (or `~/.zshrc`, or `~/.config/fish/config.fish`).
Open that file and delete the four lines:

```bash
# >>> agentfix setup >>>
export MELLUM_MODEL=agentfix-qwen3
export AGENTGRAPH_MODEL=agentfix-qwen3
# <<< agentfix setup <<<
```

Then, for the terminal you are standing in right now:

```bash
unset MELLUM_MODEL AGENTGRAPH_MODEL
```

On the `mellum2` tier setup removed that block instead of writing it, so there is nothing to do.

**3. Delete the files setup wrote in the course root**

```bash
rm -f .agentfix.env Modelfile.agentgraph-thinking Modelfile.agentfix-qwen3 Modelfile.agentfix-qwen3
```

Do **not** delete `Modelfile` — that one ships with the course.

**4. Remove Ollama itself** — optional, and only if you installed it for this course. Do the models
first. The install script registers a systemd service, so stop that before deleting the binary:

```bash
sudo systemctl stop ollama
sudo systemctl disable ollama
sudo rm -f /etc/systemd/system/ollama.service
sudo systemctl daemon-reload

sudo rm -f "$(command -v ollama)"
rm -rf ~/.ollama
```

If you added `OLLAMA_MAX_LOADED_MODELS=1` with `systemctl edit ollama`, that drop-in went with the
service file above. Inside WSL2 without systemd you started the server with `ollama serve`, so
there is no service to remove — stop that process, then run the last two commands.

The installer also creates a dedicated `ollama` user with its own model store. Only if nothing else
on the machine uses them:

```bash
sudo rm -rf /usr/share/ollama
sudo userdel ollama
sudo groupdel ollama
```

**5. Remove the Docker sandbox images** — only the ones you built.

```bash
docker rmi agentfix-sandbox agentgraph-sandbox
```

**6. Remove a uv-installed Python 3.12** — only if setup fell back to uv, which it does on
Ubuntu 22.04, Debian 12 and ChromeOS. An `apt`/`dnf`/`pacman` one is a normal package: uninstall it
with the same tool, or keep it.

```bash
uv python uninstall 3.12
uv cache clean
rm -rf ~/.local/share/uv ~/.local/bin/uv ~/.local/bin/uvx      # uv itself, if you have no other use for it
```

**7. Check it worked**

```bash
ollama list                                  # no agentfix-… or agentgraph-… entries
echo "$MELLUM_MODEL $AGENTGRAPH_MODEL"       # empty
```
</details>

<details>
<summary><b>Windows — WSL2</b></summary>

Everything from this course lives inside the Ubuntu installation, so:

**1. Follow the Linux, WSL2 and ChromeOS block above**, inside the Ubuntu shell.

**2. Undo the WSL2 memory setting** — only if you raised it during setup. In PowerShell, edit
`%UserProfile%\.wslconfig` and remove the lines you added:

```ini
[wsl2]
memory=16GB
```

Then `wsl --shutdown`.

**3. Or remove the whole Ubuntu installation** — the blunt version, which takes the models, Ollama
and the clones with it in one command. Only if you installed Ubuntu for this course and have
nothing else in it:

```powershell
wsl --unregister Ubuntu
```

That is irreversible and deletes everything in that Linux filesystem. Copy out anything you want to
keep first.
</details>

<details>
<summary><b>Windows — native PowerShell</b></summary>

**1. Remove the models** — undoes `ollama pull` + `ollama create`. This is almost all of the disk
space.

```powershell
ollama list                                                             # see what you have
```

```powershell
# mellum2 tier — the coding model, then the thinking one
ollama rm agentfix-mellum2 hf.co/JetBrains/Mellum2-12B-A2.5B-Instruct-GGUF-Q4_K_M
ollama rm agentgraph-mellum2-thinking hf.co/JetBrains/Mellum2-12B-A2.5B-Thinking-GGUF-Q4_K_M
```

```powershell
# qwen tier
ollama rm agentfix-qwen3 qwen3:1.7b
```

Remove the derived model **and** the base model it was built from, for both.

**2. Remove the model variables** — `qwen` tier only; undoes step 6 of setup, which used `setx`.

```powershell
reg delete HKCU\Environment /F /V MELLUM_MODEL
reg delete HKCU\Environment /F /V AGENTGRAPH_MODEL
```

Use `reg delete`, not `setx MELLUM_MODEL ""` — `setx` with an empty value leaves the variable
*present but empty*, which reads as a model named `""` and fails in a much more confusing way than
a missing variable does.

By hand instead: **Settings** → **System** → **About** → **Advanced system settings** →
**Environment variables**, then delete both from the top (user) list.

Either way, already-running programs keep the old values until they restart — the same rule that
made you restart PyCharm during setup. For the PowerShell window you are in:

```powershell
Remove-Item Env:\MELLUM_MODEL, Env:\AGENTGRAPH_MODEL -ErrorAction Ignore
```

**3. Delete the files setup wrote in the course root**

```powershell
Remove-Item -Force -ErrorAction Ignore .agentfix.env, Modelfile.agentgraph-thinking, Modelfile.agentfix-qwen3, Modelfile.agentfix-qwen3
```

Do **not** delete `Modelfile` — that one ships with the course.

**4. Remove Ollama itself** — optional, and only if you installed it for this course. Do the models
first: the uninstaller does not take the model store with it.

**Settings** → **Apps** → **Installed apps** → **Ollama** → **Uninstall**, or from a terminal if
you installed it with winget:

```powershell
winget uninstall -e --id Ollama.Ollama
```

Then the directories it leaves behind:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.ollama"
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Ollama"
```

**5. Remove the Docker sandbox images** — only the ones you built.

```powershell
docker rmi agentfix-sandbox agentgraph-sandbox
```

**6. Remove a uv-installed Python 3.12** — only if setup fell back to uv. A `winget`-installed one
is a normal package: `winget uninstall -e --id Python.Python.3.12`, or keep it.

```powershell
uv python uninstall 3.12
uv cache clean
Remove-Item -Recurse -Force "$env:USERPROFILE\.local\bin\uv.exe", "$env:APPDATA\uv"
```

**7. Check it worked**

```powershell
ollama list                                       # no agentfix-… or agentgraph-… entries
echo "$env:MELLUM_MODEL $env:AGENTGRAPH_MODEL"    # empty
```
</details>

### Any OS — the course project itself

The virtual environment lives inside the project folder, so deleting the project removes the
packages with it. Nothing was installed into your system Python.

In the IDE: **File** → **Close Project**, remove it from the Welcome screen's recent list, and
delete the folder if you want it gone from disk. That takes all three lessons' working directories
with it, including any run results under `results/`.

`python run.py doctor` will fail once you have done any of this, which is the point — you removed
what it checks for. Everything you built still works, and every exercise test still passes, because
they run against a scripted fake model and never needed a model at all.
