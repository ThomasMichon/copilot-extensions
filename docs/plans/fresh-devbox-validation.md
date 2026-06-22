# Fresh Dev Box Validation — copilot-extensions team rollout

Prove a teammate can go from a clean Windows dev box + an empty
**`my-control-harness`** repo to *"send a prompt to my CodeSpace through the
bridge and get work done."* Run this **as the teammate would** — do **not** rely
on an existing git checkout, binstubs, or runtimes. Capture every deviation
from the written docs; each becomes a required fix.

> End-state requirement: all three modules installed from the **marketplace**
> and running from **local install paths** (`~/.agent-*`, `~/.local/bin`).

## Environment assumptions
- Fresh Windows 11 dev box; nothing from copilot-extensions installed.
- `my-control-harness` = the teammate's own control repo (start from an empty
  git repo with a README; this run adds `machines.yaml`, `acp-agents.json`,
  `codespaces.yaml`). It also serves as the CodeSpaces dotfiles repo.
- Installed & authed: `copilot`, `git`, `gh` (`gh auth login`), Python 3.10+.
- Access to your team's shared CodeSpaces repo (set in `codespaces.yaml`) and
  read access to `ThomasMichon/copilot-extensions`.

## Phase 0 — Baseline
```powershell
copilot --version; git --version; gh auth status; python --version
Get-ChildItem $env:USERPROFILE\.local\bin -EA SilentlyContinue
Get-ChildItem $env:USERPROFILE\.copilot\installed-plugins -EA SilentlyContinue
```
Expect: no `agent-*` binstubs, no `installed-plugins\copilot-extensions`.

## Phase 1 — Plugin install (all three from the marketplace)
```powershell
copilot plugin marketplace add ThomasMichon/copilot-extensions
copilot plugin install agent-worktrees@copilot-extensions
copilot plugin install agent-bridge@copilot-extensions
copilot plugin install agent-codespaces@copilot-extensions
Get-ChildItem $env:USERPROFILE\.copilot\installed-plugins\copilot-extensions
```
**GATE:** all three plugin dirs present, each with `scripts/`, `src/`, `libs/`.

## Phase 2 — Runtime bootstrap (into local install paths)
2a. agent-worktrees — start a fresh `copilot` session (the sessionStart hook
nudges "set up agent-worktrees"), or run init directly:
```powershell
$aw = (Get-ChildItem -Recurse "$env:USERPROFILE\.copilot\installed-plugins" -Filter plugin.json |
  ? { (Get-Content $_.FullName -Raw) -match '"agent-worktrees"' } | select -First 1).DirectoryName
pwsh -NoProfile -ExecutionPolicy Bypass -File "$aw\scripts\init.ps1"
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
agent-worktrees --version
```
2b. agent-codespaces — its own runtime/binstub (`~/.agent-codespaces`):
```powershell
$ac = (Get-ChildItem -Recurse "$env:USERPROFILE\.copilot\installed-plugins" -Filter plugin.json |
  ? { (Get-Content $_.FullName -Raw) -match '"agent-codespaces"' } | select -First 1).DirectoryName
pwsh -NoProfile -ExecutionPolicy Bypass -File "$ac\scripts\init.ps1"
agent-codespaces version
Get-Content $env:USERPROFILE\.local\bin\agent-codespaces.cmd   # should point to ~/.agent-codespaces
```
2c. agent-bridge — installs the service AND imports agent_codespaces into its
venv for the relay/resolver:
```powershell
$ab = (Get-ChildItem -Recurse "$env:USERPROFILE\.copilot\installed-plugins" -Filter plugin.json |
  ? { (Get-Content $_.FullName -Raw) -match '"agent-bridge"' } | select -First 1).DirectoryName
pwsh -NoProfile -ExecutionPolicy Bypass -File "$ab\scripts\install.ps1" install
agent-bridge version; agent-bridge status
```
**CRITICAL CHECK:** the installer output must show `Sibling plugin:
agent-codespaces [OK]` (loud WARN if not). Then confirm the relay import:
```powershell
& "$env:USERPROFILE\.agent-bridge\venv\Scripts\python.exe" -c "import agent_codespaces; print('relay import OK')"
```
**GATE:** `agent_codespaces` imports from the **bridge** venv, and the
`agent-codespaces` binstub still points at `~/.agent-codespaces` (not clobbered).
Record the bridge port from `agent-bridge status` (9280 on Windows).

## Phase 3 — Adopt the control-harness repo
```powershell
cd <my-control-harness>
agent-worktrees register my-control-harness
agent-worktrees status
```
Create topology files (templates: `plugins/agent-bridge/docs/machine-config.md`):
- `machines.yaml` — this dev box (minimal/local is fine to start).
- `acp-agents.json` — a `local` agent with `project: my-control-harness`.
- `codespaces.yaml` — `defaults` (machine_type, location, `ssh_user: vscode`,
  `workspace_folder: /workspaces/<your-repo>`) + `credentials` sources
  (`git-credential`, `gh-auth`) + your team's shared CodeSpaces repo entry.
```powershell
agent-bridge config adopt --repo <my-control-harness> --profile my-control-harness
agent-bridge config validate
agent-bridge stop; agent-bridge start
agent-bridge machines; agent-bridge agents
cd <my-control-harness>; agent-codespaces config adopt; agent-codespaces config validate
```

## Phase 4 — Local bridge smoke test (no CodeSpace yet)
```powershell
agent-bridge send local "Print the current working directory and git branch."
agent-bridge sessions
agent-bridge end <session-id>
```
**GATE:** a local agent responds (bridge + worktree spawning work).

## Phase 5 — CodeSpace through the bridge (the real goal)
```powershell
gh codespace list
# create if needed (derive flags from `agent-codespaces create -h`):
agent-codespaces create <args>
agent-codespaces bridge register
agent-codespaces bridge status
agent-bridge agents            # expect codespace:<name> / cs-<name>
agent-bridge send "codespace:<name>" "Run: pwd && git -C /workspaces/<your-repo> rev-parse --abbrev-ref HEAD && gh auth status"
```
**GATE 1 (connectivity):** the CodeSpace agent responds (allow ~120–180s for a
Shutdown CodeSpace to auto-start).
**GATE 2 (relay auth):** `gh auth status` succeeds and a git fetch against ADO
works **inside** the CodeSpace — proving the credential relay (port 9857)
forwarded credentials. If echo works but auth fails, suspect the sibling
install or `auth.hooks`.
```powershell
agent-bridge send <session-id> "Make a trivial change, commit it, report the result."
agent-bridge end <session-id>
```

## Phase 6 — Teardown
```powershell
agent-bridge sessions; agent-bridge end <each>
agent-codespaces bridge unregister
agent-worktrees worktrees cleanup
```

## Capture per phase
- Command, exit code, and whether it matched the docs.
- Every deviation/supplement needed beyond the written docs.
- Any silent no-op (esp. the agent-codespaces sibling install + relay).
- Actual ports: `agent-bridge status` (9280/9281) and relay (9857).

## Pass criteria
Teammate reaches Phase 5 GATE 2 using only the published docs (README + linked
guides), with all modules installed from the marketplace and running from local
install paths.
