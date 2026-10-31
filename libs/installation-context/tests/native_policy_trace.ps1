param([string]$Adapter, [string]$ArgumentsFile)

$ErrorActionPreference = 'Stop'
$global:NativePolicyTraceClock = [Diagnostics.Stopwatch]::StartNew()
$global:NativePolicyTraceSeen = @{}
[Console]::Error.WriteLine(
    ('native-policy-phase=process-entry;utc={0:o}' -f [DateTime]::UtcNow)
)
foreach ($capture in @(
    @{ Script = $Adapter; Commands = @('ConvertTo-InputPath') },
    @{
        Script = Join-Path (Split-Path -Parent $Adapter) 'shell-caller-owned.ps1'
        Commands = @('Get-Command', 'ConvertFrom-Json')
    }
)) {
    foreach ($stage in $capture.Commands) {
        $action = {
            if (-not $global:NativePolicyTraceSeen.ContainsKey($stage)) {
                $global:NativePolicyTraceSeen[$stage] = $true
                [Console]::Error.WriteLine(
                    ('native-policy-phase={0};utc={1:o};elapsed_ms={2}' -f
                        $stage, [DateTime]::UtcNow,
                        $global:NativePolicyTraceClock.ElapsedMilliseconds)
                )
            }
        }.GetNewClosure()
        $null = Set-PSBreakpoint -Script $capture.Script -Command $stage -Action $action
    }
}
[string[]]$nativeArguments = Get-Content -LiteralPath $ArgumentsFile -Raw | ConvertFrom-Json
$adapterParameters = @{ Action = $nativeArguments[0] }
for ($index = 1; $index -lt $nativeArguments.Count; $index += 2) {
    $adapterParameters[$nativeArguments[$index].TrimStart('-')] = $nativeArguments[$index + 1]
}
& $Adapter @adapterParameters
$adapterExit = $LASTEXITCODE
[Console]::Error.WriteLine(
    ('native-policy-phase=process-return;utc={0:o};elapsed_ms={1}' -f
        [DateTime]::UtcNow, $global:NativePolicyTraceClock.ElapsedMilliseconds)
)
exit $adapterExit
