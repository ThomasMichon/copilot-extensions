$ErrorActionPreference = 'Stop'
$pluginRoot = Split-Path -Parent $PSScriptRoot
$command = Join-Path $pluginRoot 'bin\agent-vault.cmd'
$env:AGENT_VAULT_NO_SELFPROVISION = '1'

$output = $null
$exitCode = 1
if (Test-Path -LiteralPath $command) {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $command installer-readiness 2>$null
        $exitCode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
}
if ($output) {
    $output | Write-Output
    exit $exitCode
}

if ($exitCode -eq 3) {
    '{"schema":"copilot-extensions.module-readiness","version":1,"module":"agent-vault/runtime","state":"failed","detail":"Agent Vault activation configuration could not be resolved; correct its configuration, binding or payload."}'
} else {
    '{"schema":"copilot-extensions.module-readiness","version":1,"module":"agent-vault/runtime","state":"failed","detail":"The agent-vault runtime is not installed or could not run its readiness probe. Run the declared installer update."}'
}
exit 1
