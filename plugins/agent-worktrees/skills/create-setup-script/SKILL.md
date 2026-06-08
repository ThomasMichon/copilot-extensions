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
Everything before that is pre-session setup.

### ACP Compatibility (CRITICAL)

Setup scripts are invoked both for **interactive** worktree sessions
(user at a terminal) and for **ACP stdio** sessions (agent-bridge
spawning a headless agent). The script must handle both modes:

1. **Pass through all remaining args** -- `--acp`, `--stdio`, and any
   other flags must reach the `copilot` CLI unchanged. Never filter or
   drop arguments you don't recognize.

2. **No stdout pollution in ACP mode** -- when `--acp` is in the args,
   the Copilot CLI communicates via JSON-RPC on stdin/stdout. Any
   `Write-Host`, `echo`, or banner output to stdout will corrupt the
   protocol and crash the session. Either:
   - Detect ACP mode and skip all output: `if ($CopilotArgs -contains '--acp') { ... }`
   - Or always write banners to stderr instead of stdout

3. **No interactive prompts** -- the script must never block waiting
   for user input. Use defaults or fail fast.

4. **ASCII-only output** -- emoji and Unicode symbols in stdio cause
   encoding failures when output is piped between processes. Use
   `[OK]`, `[>]`, `[!]` etc.

5. **CLI wrappers** -- if the repo uses a wrapper around `copilot`
   (e.g., for auth, MCP injection, or plugin loading), **test it in
   ACP mode before relying on it**. Wrappers may inject flags that
   conflict with `--acp` (e.g., `--session-id`), emit startup banners
   to stdout, or otherwise break the JSON-RPC transport. If the wrapper
   is not ACP-safe, call `copilot` directly in ACP mode and use
   `.copilot/mcp.json` to register any MCP servers the wrapper would
   normally provide. The setup script can detect ACP mode and switch
   launch commands accordingly:
   ```powershell
   if ($IsAcp) {
       copilot @CopilotArgs          # direct -- wrapper not ACP-safe
   } else {
       my-wrapper copilot @CopilotArgs  # wrapper adds value interactively
   }
   ```

**ACP-compatible example:**

```powershell
# setup.ps1 -- ACP-compatible
param(
    [string]$Machine = $env:COMPUTERNAME,
    [switch]$Recovery,
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$CopilotArgs
)

$IsAcp = $CopilotArgs -contains '--acp'

# Pre-session setup (skip banners in ACP mode)
if (-not $IsAcp) {
    Write-Host "[>] Ready: $env:WORKTREE_PROJECT on $Machine"
}

# Launch Copilot (REQUIRED -- must be last)
copilot @CopilotArgs
```

## Full Examples

### PowerShell (setup.ps1)

```powershell
# setup.ps1
param(
    [string]$Machine = $env:COMPUTERNAME,
    [switch]$Recovery,
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$CopilotArgs
)

$IsAcp = $CopilotArgs -contains '--acp'

# 1. Environment setup
$env:MY_API_KEY = "..."

# 2. Dependencies (skip in ACP mode for speed)
if (-not $IsAcp) {
    if (-not (Test-Path node_modules)) { npm ci --quiet }
}

# 3. Welcome banner (skip in ACP mode)
if (-not $IsAcp) {
    Write-Host "[>] Ready: $env:WORKTREE_PROJECT on $Machine"
    if ($Recovery) { Write-Host "[!] RECOVERY MODE" }
}

# 4. Launch Copilot (REQUIRED -- must be last)
copilot @CopilotArgs
```

### Bash (setup.sh)

```bash
#!/usr/bin/env bash
# setup.sh
MACHINE="${HOSTNAME}"
IS_ACP=false
COPILOT_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --machine)  MACHINE="$2"; shift 2 ;;
        --recovery) shift ;;
        --acp)      IS_ACP=true; COPILOT_ARGS+=("$1"); shift ;;
        *)          COPILOT_ARGS+=("$1"); shift ;;
    esac
done

# 1. Environment
export MY_API_KEY="..."

# 2. Dependencies (skip in ACP mode)
if [[ "$IS_ACP" != "true" ]]; then
    [[ -d node_modules ]] || npm ci --quiet
fi

# 3. Banner (skip in ACP mode)
if [[ "$IS_ACP" != "true" ]]; then
    echo "[>] Ready: ${WORKTREE_PROJECT:-unknown} on $MACHINE"
fi

# 4. Launch Copilot (REQUIRED -- must be last)
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
