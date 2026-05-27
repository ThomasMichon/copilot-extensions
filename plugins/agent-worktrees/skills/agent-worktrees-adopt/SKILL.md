---
name: agent-worktrees-adopt
description: >
  Adopt the current repository for worktree-managed sessions — detect repo
  layout, sweep for machines.yaml and service definitions, assign machine
  roles, generate per-project config and binstubs. Run from inside a repo
  after agent-worktrees-init. Trigger phrases include:
  - 'adopt this repo'
  - 'adopt repo'
  - 'register project'
  - 'agent-worktrees adopt'
  - 'set up worktree sessions for this repo'
  - 'configure agent-worktrees for this project'
---

# Agent Worktrees Adopt

Register the **current repository** as a worktree-managed project. This
creates per-project config and binstubs so the repo can launch isolated
Copilot CLI sessions via git worktrees.

**Prerequisite:** the agent-worktrees runtime must be installed first
(see the `agent-worktrees-init` skill).

## What It Creates

```
~/.{repo-name}/
├── config.yaml           ← project config (repos, launch commands, profiles)
└── worktrees/            ← per-worktree tracking YAML (populated at runtime)

~/.local/bin/
└── {repo-name}[.cmd]    ← project binstub (launches worktree picker)
```

## Adoption Flow

### 1. Detect repo identity

From the current working directory:

```bash
repo_root=$(git rev-parse --show-toplevel)
repo_name=$(basename "$repo_root")
remote_url=$(git remote get-url origin 2>/dev/null || echo "")
default_branch=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||')
```

If `default_branch` detection fails, check for `master` or `main` branches
and ask the user which is the default.

### 2. Sweep for machines.yaml

Look for a machine registry at conventional locations:

```
{repo_root}/machines.yaml
{repo_root}/config/machines.yaml
{repo_root}/.github/machines.yaml
```

If found, parse it and present the machine list. Ask the user:
- **Which machine is this?** (match by hostname prefix if possible,
  otherwise present a picker)
- The selected machine determines the `machine:` and `platform:` fields
  in config.yaml

If no machines.yaml exists, ask the user for:
- A machine name (default: hostname)
- The platform (auto-detect from OS)

### 3. Sweep for services

Look for service definitions at conventional paths:

```
services/*/service.yaml
tools/*/service.yaml
{machine}/services/*/service.yaml
```

Report what was found — this tells the user what services are available
for deployment. No action needed during adopt; services are managed
separately via `agent-worktrees services`.

### 4. Detect launch command convention

Check for common setup script patterns:

```
tools/setup/setup.ps1    → launch: ["pwsh", "-NoProfile", "-File", "{work_dir}/tools/setup/setup.ps1", "-Machine", "{machine}"]
tools/setup/setup.sh     → launch: ["bash", "{work_dir}/tools/setup/setup.sh", "--machine", "{machine}"]
.devcontainer/           → (note: devcontainer-based, different flow)
```

If no setup script is found, use a generic launch that just starts
Copilot CLI directly:

```yaml
launch:
  windows: ["copilot"]
  linux: ["copilot"]
```

Ask the user to confirm or customize the launch command.

### 5. Choose worktree root

Default convention: sibling `.worktrees/{repo-name}/` directory next to
the anchor repo.

```
{parent-of-repo}/.worktrees/{repo-name}/
```

Ask the user to confirm or customize.

### 6. Generate config.yaml

Write `~/.{repo-name}/config.yaml`:

```yaml
repo_name: {repo-name}
machine: {machine-name}
platform: {windows|linux}

repos:
  {repo-name}:
    anchor: {repo-root}
    worktree_root: {worktree-root}
    default_branch: {default-branch}
    remote: origin
    launch:
      windows: [...]
      linux: [...]
```

### 7. Create project binstub

**Windows (`{repo-name}.cmd` in `~/.local/bin/`):**
```bat
@echo off
set "WORKTREE_PROJECT={repo-name}"
"%USERPROFILE%\.agent-worktrees\bin\launch-session.cmd" %*
```

**Linux (`{repo-name}` in `~/.local/bin/`):**
```bash
#!/usr/bin/env bash
export WORKTREE_PROJECT="{repo-name}"
exec "$HOME/.agent-worktrees/bin/launch-session.sh" "$@"
```

### 8. Create worktree tracking directory

```bash
mkdir -p ~/.{repo-name}/worktrees
```

### 9. Verify

```
{repo-name}          # should launch the worktree picker
agent-worktrees status   # should show the adopted repo
```

## Terminal Integration (Optional)

If the repo contains terminal profile templates:

- **Windows Terminal fragment** at `terminal/{repo-name}.json` →
  deploy to `%LOCALAPPDATA%\Microsoft\Windows Terminal\Fragments\{RepoName}\`
- **Tabby profile** at `terminal/tabby-{repo-name}.yaml` →
  merge into `~/.config/tabby/config.yaml`

Ask the user whether to deploy terminal profiles if found.

## Multiple Repos

Each repo gets its own `~/.{repo-name}/` directory and binstub. The
shared runtime at `~/.agent-worktrees/` serves all adopted projects.
Run `agent-worktrees-adopt` once per repo.
