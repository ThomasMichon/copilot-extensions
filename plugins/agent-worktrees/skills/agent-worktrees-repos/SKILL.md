---
name: agent-worktrees-repos
description: >
  Manage the repos registry — catalog of known repositories, source roots,
  and local checkout paths. Use when asked to find, add, clone, or list
  repos, or when setting up source directories for a platform. Trigger
  phrases include:
  - 'find repo'
  - 'where is repo'
  - 'clone repo'
  - 'register repo'
  - 'list repos'
  - 'source root'
  - 'srcroot'
  - 'repos registry'
  - 'add repo'
  - 'remove repo'
---

# Agent Worktrees Repos Registry

Manage the repos registry at `~/.agent-worktrees/repos.yaml` — a catalog
of known repositories across platforms.

## Two-Tier Model

- **project** repos get full agent-worktrees management: binstubs,
  worktrees, terminal profiles, and (future) ACP bridge dispatch.
  These are also tracked in `projects.yaml`.
- **repo** entries are tracked locations only — used for path lookup,
  clone resolution, and cross-platform path mapping.

## CLI Commands

All commands are accessed via `agent-worktrees repos <subcommand>`:

```
repos list [--type project|repo] [--json]
repos find <name>
repos add <name> <path> [--type project|repo] [--remote URL]
repos remove <name>
repos clone <remote> [--name N] [--target PATH]
repos srcroot [--set PATH] [--platform windows|wsl|linux]
```

## Common Workflows

### Set up source roots

Before cloning repos, set the default source directory per platform:

```bash
agent-worktrees repos srcroot --set D:\Src --platform windows
agent-worktrees repos srcroot --set ~/src --platform wsl
agent-worktrees repos srcroot --set ~/src --platform linux
```

### Register an existing repo

```bash
agent-worktrees repos add my-project D:\Src\my-project --type project --remote https://github.com/org/my-project.git
```

### Find where a repo is checked out

```bash
agent-worktrees repos find my-project
# → D:\Src\my-project
```

If the repo has no local path but has a remote, suggest cloning it.

### Clone a new repo

```bash
agent-worktrees repos clone https://github.com/org/new-repo.git
# Clones to {srcroot}/new-repo and registers it
```

### List all known repos

```bash
agent-worktrees repos list
agent-worktrees repos list --type project   # only agent-worktrees managed repos
```

## Data File

The registry lives at `~/.agent-worktrees/repos.yaml`:

```yaml
srcroot:
  windows: D:\Src
  wsl: ~/src

repos:
  dotfiles:
    type: project
    remote: "https://github.com/user/dotfiles.git"
    windows: D:\Src\dotfiles
    wsl: ~/src/dotfiles

  some-utility:
    type: repo
    remote: "https://github.com/org/some-utility.git"
    windows: D:\Src\some-utility
```

## Integration Points

- **Adopt flow**: reads `srcroot` to suggest clone locations for WSL
- **WSL provision**: uses `srcroot.wsl` for clone targets
- **ACP bridge** (future): queries the registry to find local checkouts
- **`projects.yaml`**: remains the authoritative registry for adopted
  projects; `repos.yaml` is the broader catalog
