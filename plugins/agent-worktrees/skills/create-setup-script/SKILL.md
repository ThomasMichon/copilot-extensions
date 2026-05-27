---
name: create-setup-script
description: >
  Create a session setup script for a repo that doesn't have one yet.
  Generates a setup.ps1 and/or setup.sh in the repo's tools/setup/
  directory (or a local override in ~/.agent-worktrees/) that runs at
  the start of each worktree session. Trigger phrases include:
  - 'create setup script'
  - 'add setup script'
  - 'repo setup script'
  - 'session setup'
  - 'customize session launch'
  - 'setup.ps1'
  - 'setup.sh'
---

# Create Setup Script

Generate a session setup script for a worktree-managed repo. The script
runs at the start of each Copilot CLI session, before the agent launches.

## When to Use

When the user wants to customize what happens when a worktree session
starts — installing dependencies, setting env vars, running codegen,
displaying project status, or any other pre-session tasks.

## Script Locations

There are two places a setup script can live:

### 1. In-repo (recommended for shared repos)

```
{repo}/tools/setup/setup.ps1    # Windows
{repo}/tools/setup/setup.sh     # Linux/WSL/macOS
```

Checked into the repo so all contributors get the same session setup.
This is the standard convention — agent-worktrees checks here first.

### 2. Local override (for personal repos or per-machine customization)

Update the project config (`~/.{project}/config.yaml`) to use a
`launch:` block pointing at a local script:

```yaml
repos:
  my-project:
    anchor: /path/to/repo
    worktree_root: /path/to/.worktrees/my-project
    launch:
      windows: ["pwsh.exe", "-NoProfile", "-File", "C:/Users/me/scripts/my-setup.ps1"]
      linux: ["bash", "/home/me/scripts/my-setup.sh"]
```

## Script Contract

The setup script receives these from the launcher:

### Environment variables (set by launch-session)

| Variable | Description |
|----------|-------------|
| `WORKTREE_PROJECT` | Project name (e.g., `my-app`) |
| `WORKTREE_ID` | Current worktree identifier |
| `WORKTREE_MACHINE` | Machine name (passed via `-Machine` / `--machine`) |
| `WORKTREE_SETUP_LOG` | Path to the setup log file |

### Arguments

| Argument | Description |
|----------|-------------|
| `-Machine` / `--machine` | Machine name from config |
| `-Recovery` / `--recovery` | Recovery mode (launch in anchor, not worktree) |
| Remaining args | Passed through to `copilot` CLI |

### Responsibilities

The setup script **must** launch the Copilot CLI as its final action.
Everything before that is pre-session setup:

```powershell
# setup.ps1 — example
param(
    [string]$Machine = $env:COMPUTERNAME,
    [switch]$Recovery,
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$CopilotArgs
)

# 1. Environment setup
$env:MY_API_KEY = "..."

# 2. Dependencies
if (-not (Test-Path node_modules)) { npm ci --quiet }

# 3. Codegen / build
# npm run build

# 4. Welcome banner
Write-Host "Ready: $env:WORKTREE_PROJECT on $Machine"

# 5. Launch Copilot (REQUIRED — must be last)
copilot @CopilotArgs
```

```bash
#!/usr/bin/env bash
# setup.sh — example
MACHINE="${HOSTNAME}"
COPILOT_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --machine)  MACHINE="$2"; shift 2 ;;
        --recovery) shift ;;
        *)          COPILOT_ARGS+=("$1"); shift ;;
    esac
done

# 1. Environment
export MY_API_KEY="..."

# 2. Dependencies
[[ -d node_modules ]] || npm ci --quiet

# 3. Launch Copilot (REQUIRED — must be last)
exec copilot "${COPILOT_ARGS[@]}"
```

## Generation Flow

1. **Ask the user** what the script should do:
   - Install dependencies? (npm, pip, cargo, etc.)
   - Set environment variables?
   - Run build/codegen steps?
   - Display project status?
   - Platform targets? (Windows only, Linux only, both)

2. **Detect repo characteristics** to suggest useful steps:
   - `package.json` → `npm ci`
   - `pyproject.toml` → `pip install -e .` or `uv sync`
   - `Cargo.toml` → `cargo build`
   - `go.mod` → `go mod download`

3. **Generate the script(s)** in `tools/setup/` (or wherever the user
   wants them).

4. **Update config** if the user chose a non-standard location — add
   a `launch:` block to `~/.{project}/config.yaml`.

5. **Test** by running the script directly to verify it works before
   committing.

## If No Setup Script Exists

When a repo has no setup script and no `launch:` config, agent-worktrees
falls back to a built-in default script (`~/.agent-worktrees/scripts/
default-setup.{ps1,sh}`) that displays basic project info and launches
Copilot. The default is functional but minimal — creating a custom setup
script unlocks the full pre-session workflow.
