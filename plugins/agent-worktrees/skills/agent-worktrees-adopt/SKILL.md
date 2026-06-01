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

### 2. Sweep for machines.yaml (optional)

Look for a machine registry at conventional locations:

```
{repo_root}/machines.yaml
{repo_root}/config/machines.yaml
{repo_root}/.github/machines.yaml
```

This file is **optional**. Most repos will not have one. It is an advanced
feature for repos that manage multi-machine deployments or need machine-
specific custom instructions.

If found, parse it and present the machine list. Ask the user:
- **Which machine is this?** (match by hostname prefix if possible,
  otherwise present a picker)
- The selected machine determines the `machine:` and `platform:` fields
  in config.yaml, and deploys `machine.instructions.md` for Copilot

If no machines.yaml exists, auto-detect:
- Machine name from hostname (lowercase)
- Platform from OS detection

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

After adoption, the project binstub is at `~/.local/bin/{repo-name}[.cmd]`.
If `~/.local/bin` was just added to PATH by a prior init step in this same
session, the current shell may not see it yet. Resolve by either:

- Updating PATH in the current session:
  `$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"` (Windows) or
  `export PATH="$HOME/.local/bin:$PATH"` (Linux)
- Invoking the binstub by full path for verification

```
{repo-name}          # should launch the worktree picker
agent-worktrees --version   # confirm deployed version
agent-worktrees status   # should show the adopted repo
```

Report the `--version` output to the user after adoption so they can
confirm the deployed version matches expectations.

## Terminal Integration (Optional)

If the repo contains terminal profile templates:

- **Windows Terminal fragment** at `terminal/{repo-name}.json` →
  deploy to `%LOCALAPPDATA%\Microsoft\Windows Terminal\Fragments\{RepoName}\`
- **Tabby profile** at `terminal/tabby-{repo-name}.yaml` →
  merge into `~/.config/tabby/config.yaml`

Ask the user whether to deploy terminal profiles if found.

### WSL Terminal Profiles

The `(WSL)` Windows Terminal profile is generated by the installer when
the project's `projects.yaml` entry contains WSL metadata
(`wsl.state` and `wsl.distro`).  The installer does **not** probe WSL
at install/update time -- WSL support is recorded during adoption.

When adopting on Windows:
- Ask the user whether they want **WSL support** for this project.
- If the user agrees, ask for the WSL distro name (default: check
  `wsl.exe -l -q` interactively during the adopt conversation, or let
  the user specify it).
- Record WSL metadata in the registry via `register_project()`:
  ```python
  register_project(project, repo_dir=...,
      wsl_state="adopted", wsl_distro="Ubuntu", wsl_path="~/src/my-project")
  ```
  Or from PowerShell, ensure the project entry in `projects.yaml`
  contains:
  ```yaml
  wsl:
    state: adopted
    distro: Ubuntu
    path: ~/src/my-project
  ```
- The next `install` or `update` will use this stored config to
  generate the `(WSL)` terminal profile and shortcut -- no `wsl.exe`
  probing required.
- If the user declines WSL support, do not record any `wsl` metadata.
  The installer will skip WSL profile generation.

**Full WSL setup** is done from within WSL itself:
1. Install the copilot-extensions plugin in WSL (requires Copilot CLI)
2. Run the agent-worktrees installer:
   `agent-worktrees install --project-name {project}`
3. Run adoption from WSL -- this automatically records
   `wsl.state: adopted` and `wsl.distro` in the registry.

When adopting inside WSL:
- The `wsl.state` and `wsl.distro` metadata is recorded in
  `projects.yaml` automatically (from `$WSL_DISTRO_NAME`).
- The Windows-side installer reads this metadata on the next `update`
  and generates the `(WSL)` terminal profile accordingly.

## Multiple Repos

Each repo gets its own `~/.{repo-name}/` directory and binstub. The
shared runtime at `~/.agent-worktrees/` serves all adopted projects.
Run `agent-worktrees-adopt` once per repo.
