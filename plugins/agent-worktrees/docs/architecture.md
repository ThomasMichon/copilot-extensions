# Agent Worktrees -- Architecture

## Two-Layer Design

```
Plugin layer (Copilot CLI)              Runtime layer (Python CLI)
  plugin.json                             ~/.agent-worktrees/
  hooks.json  -- sessionStart hook          .venv/           Python venv
  skills/     -- skills loaded                lib/agent_worktrees/  Python package
                 into every session           bin/             launch-session, bootstrap-check
                                              projects.yaml    registry of adopted repos
                                              repos.yaml       repos registry + source roots

                                            ~/.{project}/      per-project config + state
                                              config.yaml      repos, machine, launch commands
                                              worktrees/       per-worktree tracking YAML

                                            ~/.local/bin/
                                              {project}        binstub (Windows: .cmd)
                                              agent-worktrees  CLI tool
```

The **plugin** installs via `copilot plugin install` and provides skills
and hooks to every Copilot CLI session. The **runtime** installs via
init scripts (`init.ps1`/`init.sh`) and provides the `agent-worktrees`
CLI, session launchers, and per-project binstubs.

## Installed Layout

After full installation and project registration:

```
~/.agent-worktrees/                 # Shared runtime (one per machine)
  .venv/                            #   Python virtual environment
  lib/agent_worktrees/              #   Python package
  bin/                              #   Shell wrappers
    launch-session.{ps1,cmd,sh}     #     Session launcher
    bootstrap-check.{ps1,sh}        #     Session-start health check
  projects.yaml                     #   Registry of adopted projects
  repos.yaml                        #   Repos catalog + source roots
  deploy-manifest.json              #   Provenance (commit, timestamp)

~/.{project}/                       # Per-project config + state
  config.yaml                       #   Machine, repos, launch commands
  worktrees/                        #   Per-worktree tracking
    {worktree-id}.yaml              #     status: active|complete|finalized|orphaned

~/.local/bin/                       # Binstubs on PATH
  agent-worktrees{.cmd}             #   CLI tool
  {project}{.cmd}                   #   Project launcher (one per registered repo)
  cleanup-worktrees{.cmd}           #   Bulk worktree cleanup
  mark-worktree-complete{.cmd}      #   Mark worktree done / set title
```

## Session Lifecycle

```
{project}                         # launch binstub
  |
  v
launch-session.{ps1,sh}           # pre-flight update, venv activation
  |
  v
agent-worktrees resolve           # picker UI, worktree creation
  |                                 emits JSON launch plan, exits
  v
Setup script runs                  # tools/setup/setup.{ps1,sh} or config-driven
  |
  v
Copilot CLI session                # your work happens here
  |
  v
Post-exit checks                   # detect completion markers
  |
  +-- status: complete --> finalize (squash, rebase, ff-merge, push, cleanup)
  +-- status: active   --> preserve worktree for later resume
```

### Finalization Flow

When a session is marked complete (via the `worktree` skill or
`mark-worktree-complete`), the finalization flow:

1. Acquires a local lock
2. Fetches from origin
3. Squashes commits on the worktree branch
4. Rebases onto `origin/{default_branch}`
5. Fast-forward merges into local `{default_branch}`
6. Pushes to origin (with retry on rejection)
7. Removes the worktree directory and branch
8. Updates tracking YAML to `status: finalized`

On any failure, the worktree is preserved and marked `status: orphaned`.

### Recovery Mode

```bash
my-project -Recovery    # Windows
my-project recovery     # Linux/WSL
```

Skips vault credential loading for debugging broken bootstrap
infrastructure.

## Terminal Integration

| File | Platform | Description |
|------|----------|-------------|
| `psmux.conf` | Windows | psmux multiplexer config |
| `tmux.conf` | Linux/WSL | tmux session config |
| `tabby-template.yaml` | Linux | Tabby terminal profile template |

The Windows installer generates **Windows Terminal fragments** at
`%LOCALAPPDATA%\Microsoft\Windows Terminal\Fragments\AgentWorktrees\`
with profiles for each registered project (local + remote SSH machines).

## Multiple Projects

Register multiple repos on the same machine. Each gets its own config
directory (`~/.{project}/`) and binstub. The shared runtime is installed
once:

```bash
agent-worktrees register my-app --repo-dir ~/src/my-app
agent-worktrees register dotfiles --repo-dir ~/src/dotfiles
```

## Update Mechanisms

### Pre-Flight Auto-Update

The `launch-session` wrapper checks for new commits on each session
launch. If the anchor repo has changes affecting the worktree manager,
the launcher re-runs the installer automatically.

Skip with `--no-update` or `WORKTREE_NO_UPDATE=1`.

### Plugin Marketplace Update

```bash
copilot plugin update agent-worktrees@copilot-extensions
```

Or use the built-in update command:

```bash
agent-worktrees update
```

### Version Checking

All three version sources must agree:

| File | Purpose |
|------|---------|
| `plugin.json` | Marketplace version detection |
| `pyproject.toml` | Runtime `--version` output |
| `.github/plugin/marketplace.json` | GitHub-hosted marketplace catalog |

See [CONTRIBUTING.md](../../CONTRIBUTING.md) for versioning details.
