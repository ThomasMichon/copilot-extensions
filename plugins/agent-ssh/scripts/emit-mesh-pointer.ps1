# Read current topology/profile data only through this payload's existing runtime.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$command = Join-Path $root 'bin\agent-ssh.ps1'
$hostExe = (Get-Process -Id $PID).Path
$env:AGENT_SSH_NO_SELFPROVISION = '1'
try {
    $result = @(& $hostExe -NoProfile -File $command session-snapshot --guidance 2>$null)
    if ($LASTEXITCODE -eq 0 -and $result) {
        $result -join "`n"
        exit 0
    }
} catch {
    # Windows PowerShell can promote native stderr to a terminating error.
}
@{ additionalContext = '## SSH snapshot unavailable

The selected runtime is unavailable, refused or does not support session-snapshot. No provisioning or probe was attempted. Use the agent-ssh skill for inspection and bounded route repair.' } | ConvertTo-Json -Compress
exit 0
