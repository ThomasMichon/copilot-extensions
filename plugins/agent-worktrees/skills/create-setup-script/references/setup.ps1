# setup.ps1 -- worktree session setup script (reference example)
#
# Full PowerShell example for a `launch`/setup script invoked by agent-worktrees.
# Conventions (see the create-setup-script SKILL.md):
#   - Accept -Machine, -Recovery, and remaining args as -CopilotArgs.
#   - Detect ACP mode (`--acp` present) and skip banners / heavy deps for speed.
#   - Launching Copilot MUST be the last step.
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
