# Getting Started with Agent Worktrees

Agent Worktrees gives each Copilot CLI session its own isolated git
worktree — no branch conflicts, no stale state, no stepping on other
sessions. This guide covers what you need to get started.

## How It Works

```
Your repo (anchor)          Worktrees (ephemeral)
D:\Src\my-project\          D:\Src\my-project.worktrees\
├── .git/                       ├── feature-abc-20260527\
├── src/                        │   ├── .git  (file → anchor/.git)
└── ...                         │   ├── src/
                                │   └── ...
                                └── bugfix-xyz-20260528\
                                    └── ...
```

Each worktree is a full working copy sharing the same `.git` database.
Worktrees live in a `<anchor>.worktrees` sibling folder next to the repo —
the same layout the Copilot CLI's native `/worktree` command uses, so
worktrees from either tool are mutually discoverable.
Sessions can run in parallel without conflicts. When done, the worktree
is merged and cleaned up automatically.

## Prerequisites

- **Python 3.10+** on PATH
- **Git 2.15+** (worktree support)
- **Copilot CLI** installed (`copilot` or `gh copilot`)

## Quick Start

### 1. Install the plugin

```bash
# Register the marketplace (one-time)
copilot plugin marketplace add ThomasMichon/copilot-extensions

# Install from marketplace
copilot plugin install agent-worktrees@copilot-extensions
```

### 2. Bootstrap the runtime

Start a Copilot CLI session and ask: *"set up agent-worktrees"*

Or run the init script directly:

```powershell
# Windows
$pluginDir = (Get-ChildItem -Recurse "$env:USERPROFILE\.copilot\installed-plugins" -Filter plugin.json |
    Where-Object { (Get-Content $_.FullName -Raw) -match 'agent-worktrees' } |
    Select-Object -First 1).DirectoryName
powershell -NoProfile -ExecutionPolicy Bypass -File "$pluginDir\scripts\init.ps1"
```

```bash
# Linux/macOS
plugin_dir=$(find ~/.copilot/installed-plugins -name plugin.json \
    -exec grep -l agent-worktrees {} \; | head -1 | xargs dirname)
bash "$plugin_dir/scripts/init.sh"
```

### 3. Register your first project

```bash
cd /path/to/your/repo
agent-worktrees register my-project
```

### 4. Launch a session

```bash
my-project    # opens the worktree picker
```

## Choosing an Anchor Repo

The **anchor repo** is the main checkout that worktrees branch from.
Any git repo works, but some patterns work better than others.

### Good anchor repos

- **A personal "control" repo** (like a dotfiles repo) — keeps worktree
  config, custom instructions, and session state together. Works well as
  a hub for managing multiple projects.

- **Your main project repo** — the most common case. Clone it once,
  register it, and all sessions branch from the same checkout.

- **A monorepo** — agent-worktrees handles large repos fine. Each
  worktree gets the full tree but shares the `.git` database, so disk
  usage stays reasonable.

### Requirements for anchor repos

- Must be a **git repository** (not a bare clone)
- Should have a **remote** configured (`origin`) for push/pull
- The **default branch** should be up to date — worktrees branch from it
- Avoid repos with **uncommitted changes** in the anchor — worktrees
  inherit the index state at creation time

### Multiple projects

You can register multiple repos on the same machine. Each gets its own
config directory (`~/.{project-name}/`) and binstub. The shared runtime
(`~/.agent-worktrees/`) is installed once.

```bash
agent-worktrees register my-app --repo-dir ~/src/my-app
agent-worktrees register dotfiles --repo-dir ~/src/dotfiles
```

## Setup Scripts

When a worktree session starts, agent-worktrees runs a **setup script**
that prepares the environment and launches Copilot. There are three
levels:

### 1. Built-in default (no setup needed)

If your repo has no setup script, agent-worktrees uses a minimal default
that shows project status and launches Copilot. This works out of the
box for simple repos.

### 2. Repo-specific setup script (recommended)

Create `tools/setup/setup.ps1` (Windows) and/or `tools/setup/setup.sh`
(Linux) in your repo. These run automatically — no config changes needed.

Use a setup script when you want to:
- Install dependencies (`npm ci`, `pip install`, etc.)
- Set environment variables
- Run codegen or build steps
- Display a project-specific welcome banner

Ask Copilot to *"create a setup script"* (see the `create-setup-script`
skill) for guided generation.

### 3. Config-driven launch (advanced)

For full control, add a `launch:` block to your project config
(`~/.{project}/config.yaml`):

```yaml
repos:
  my-project:
    anchor: /path/to/repo
    launch:
      windows: ["pwsh.exe", "-NoProfile", "-File", "path/to/my-setup.ps1"]
      linux: ["bash", "path/to/my-setup.sh"]
```

This overrides both the repo convention and the built-in default.

## Session Lifecycle

```
my-project                      # launch picker
  → Create new worktree         # branch from default branch
  → Run setup script            # install deps, set env
  → Copilot CLI session         # your work happens here
  → Post-exit checks            # detect completion markers
  → Finalize (if pushed)       # validate → clean up worktree
```

### Completion

Worktree completion is a two-step process:

1. **Push changes** — `agent-worktrees push-changes --title "desc"`
   squashes commits, rebases onto the default branch, and pushes to origin.
2. **Finalize** — `agent-worktrees finalize` validates the content is on
   upstream, then removes the worktree directory and branch.

Use the `worktree` skill during a Copilot session for guided sign-off.

## Next Steps

- **Customize sessions** -- create a setup script for your repo
- **Add custom instructions** -- put an `AGENTS.md` in your repo root
  for Copilot CLI context
- **Manage services** -- if your repo has services with `service.yaml`
  manifests, agent-worktrees can discover and deploy them
- **Multiple machines** -- add a `machines.yaml` to your repo for
  per-machine configuration
- **Architecture** -- see [Architecture](architecture.md) for internals
- **CLI reference** -- see [CLI Reference](cli-reference.md) for commands
