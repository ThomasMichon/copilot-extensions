$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$forwardArgs = @($args)

$payloadRoot = [Environment]::GetEnvironmentVariable(
    'AGENT_MACHINES_PAYLOAD_ROOT',
    'Process'
)
if (-not $payloadRoot -or -not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
    [Console]::Error.WriteLine('[agent-machines] owning payload root is unavailable.')
    exit 126
}
$payloadRoot = (Resolve-Path -LiteralPath $payloadRoot).Path
$scriptDir = Join-Path $payloadRoot 'scripts'
$modeRunner = Join-Path $scriptDir 'installation-context\installation-context.ps1'
$runtimeResolver = Join-Path $scriptDir 'resolve-runtime.ps1'
$legacyRoot = Join-Path $env:USERPROFILE '.agent-machines' # marketplace-isolation: allow legacy compatibility root
if (
    -not (Test-Path -LiteralPath $modeRunner -PathType Leaf) -or
    -not (Test-Path -LiteralPath $runtimeResolver -PathType Leaf)
) {
    [Console]::Error.WriteLine(
        '[agent-machines] installation-context runtime resolver is unavailable.'
    )
    exit 126
}

$runtimeRoot = $legacyRoot
$context = ''
$marketplaceId = ''
$resolutionStatus = 'ready'
$resolutionReason = 'policy-default-false'
$actualMode = 'legacy'
$desiredMode = 'legacy'
$policy = Join-Path $env:USERPROFILE '.copilot-extensions\installation-mode.json'
$policyPresent = (
    (Test-Path -LiteralPath $policy) -or
    $null -ne (Get-Item -LiteralPath $policy -Force -ErrorAction SilentlyContinue)
)
$hostExe = (Get-Process -Id $PID).Path
if (-not $hostExe) {
    [Console]::Error.WriteLine('[agent-machines] PowerShell host executable is unavailable.')
    exit 126
}

    $statusArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
        'status',
        '-PayloadRoot', $payloadRoot,
        '-PluginId', 'agent-machines',
        '-LegacyRoot', $legacyRoot
    )
    if ($env:COPILOT_EXTENSIONS_CONTEXT) {
        $statusArgs += @('-Context', $env:COPILOT_EXTENSIONS_CONTEXT)
        $contextDurableHome = $env:COPILOT_EXTENSIONS_CONTEXT
        1..5 | ForEach-Object {
            $contextDurableHome = Split-Path -Parent $contextDurableHome
        }
        $statusArgs += @('-DurableHome', $contextDurableHome)
    }
    $resolutionJson = @(& $hostExe @statusArgs)
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine(
            '[agent-machines] installation context could not be resolved.'
        )
        exit 126
    }
    try {
        $resolution = ($resolutionJson -join "`n") | ConvertFrom-Json
    } catch {
        [Console]::Error.WriteLine(
            '[agent-machines] installation context returned malformed status.'
        )
        exit 126
    }
    $resolutionStatus = [string]$resolution.status
    $resolutionReason = [string]$resolution.reason
    $actualMode = [string]$resolution.actualMode
    $desiredMode = [string]$resolution.desiredMode
    $simplePolicyLegacy = $false
    if (
        -not $env:COPILOT_EXTENSIONS_CONTEXT -and
        -not $policyPresent -and
        $resolutionStatus -ceq 'provenance-blocked' -and
        [string]$resolution.policy.state -ceq 'missing' -and
        $resolution.policy.enabled -is [bool] -and
        -not $resolution.policy.enabled -and
        [string]$resolution.policy.reason -ceq 'policy-default-false' -and
        $null -eq $resolution.legacy.tombstone -and
        [string]$resolution.legacy.disposition -ceq 'active'
    ) {
        $simplePolicyLegacy = $true
    }
    elseif (
        -not $env:COPILOT_EXTENSIONS_CONTEXT -and
        $resolutionStatus -ceq 'provenance-blocked' -and
        [string]$resolution.policy.state -ceq 'valid' -and
        $resolution.policy.enabled -is [bool] -and
        -not $resolution.policy.enabled -and
        $null -eq $resolution.legacy.tombstone -and
        [string]$resolution.legacy.disposition -ceq 'active'
    ) {
        try {
            $policyDocument = Get-Content -LiteralPath $policy -Raw |
                ConvertFrom-Json
            $installationMode = @(
                $policyDocument.PSObject.Properties |
                    Where-Object { $_.Name -ceq 'installationMode' }
            )
            $marketplaces = @()
            if ($installationMode.Count -eq 1) {
                $marketplaces = @(
                    $installationMode[0].Value.PSObject.Properties |
                        Where-Object { $_.Name -ceq 'marketplaces' }
                )
            }
            $simplePolicyLegacy = (
                $marketplaces.Count -eq 0 -or
                $marketplaces[0].Value.PSObject.Properties.Count -eq 0
            )
        } catch {
            $simplePolicyLegacy = $false
        }
    }
    if (
        (
            $resolutionStatus -ceq 'ready' -and
            $actualMode -ceq 'legacy' -and
            $desiredMode -ceq 'legacy'
        ) -or
        $simplePolicyLegacy
    ) {
        if ($env:COPILOT_EXTENSIONS_CONTEXT) {
            [Console]::Error.WriteLine(
                '[agent-machines] requested installation context is not active.'
            )
            exit 126
        }
        $runtimeRoot = $legacyRoot
    }
    elseif (
        (
            $resolutionStatus -ceq 'ready' -and
            $resolutionReason -ceq 'namespaced-active'
        ) -or
        $resolutionStatus -ceq 'deactivation-required'
    ) {
        if ($actualMode -cne 'namespaced') {
            [Console]::Error.WriteLine(
                "[agent-machines] installation context blocks invocation: " +
                "status=$resolutionStatus reason=$resolutionReason."
            )
            exit 126
        }
        $runtimeRoot = [string]$resolution.runtimeRoot
        $context = [string]$resolution.context
        $marketplaceId = [string]$resolution.marketplaceId
        if (-not $runtimeRoot -or -not $context -or -not $marketplaceId) {
            [Console]::Error.WriteLine(
                '[agent-machines] active installation context is incomplete.'
            )
            exit 126
        }
    }
    else {
        [Console]::Error.WriteLine(
            "[agent-machines] installation context blocks invocation: " +
            "status=$resolutionStatus reason=$resolutionReason."
        )
        exit 126
    }

function Resolve-AgentMachinesRuntime {
    $AgentRtPy = $null
    $env:AGENT_RT_ROOT = $runtimeRoot
    . $runtimeResolver
    return $AgentRtPy
}

function Invoke-AgentMachinesRuntime([string]$Python) {
    if ($context) { $env:COPILOT_EXTENSIONS_CONTEXT = $context }
    & $Python -m agent_machines @forwardArgs
    exit $LASTEXITCODE
}

function Invoke-AgentMachinesInstaller([string]$InstallerPath, [string]$Action) {
    $installerArgs = @(
        '-NoProfile', '-OutputFormat', 'Text',
        '-ExecutionPolicy', 'Bypass', '-File', "`"$InstallerPath`"",
        '-Action', $Action
    )
    if ($Action -ceq 'cell-provision') {
        $installerArgs += @(
            '-Context', "`"$context`"",
            '-ExpectedMarketplaceId', $marketplaceId
        )
    }
    $stdoutPath = $null
    $stderrPath = $null
    $process = $null
    $readers = @($null, $null)
    $decoders = @($null, $null)
    $characters = @($null, $null)
    $prefixes = @([Collections.Generic.List[byte]]::new(), [Collections.Generic.List[byte]]::new())
    $pending = @([Text.StringBuilder]::new(), [Text.StringBuilder]::new())
    $boms = @(
        @{ Bytes = [byte[]](0xFF, 0xFE, 0, 0); Encoding = [Text.Encoding]::UTF32 },
        @{ Bytes = [byte[]](0, 0, 0xFE, 0xFF); Encoding = [Text.UTF32Encoding]::new($true, $true) },
        @{ Bytes = [byte[]](0xEF, 0xBB, 0xBF); Encoding = [Text.Encoding]::UTF8 },
        @{ Bytes = [byte[]](0xFF, 0xFE); Encoding = [Text.Encoding]::Unicode },
        @{ Bytes = [byte[]](0xFE, 0xFF); Encoding = [Text.Encoding]::BigEndianUnicode }
    )

    function Write-InstallerBytes([int]$Index, [byte[]]$Bytes, [int]$Offset, [int]$Count, [bool]$Final) {
        $count = $decoders[$Index].GetChars($Bytes, $Offset, $Count, $characters[$Index], 0, $Final)
        if ($count -eq 0) { return }
        $text = [string]::new($characters[$Index], 0, $count)
        $boundary = $text.LastIndexOfAny([char[]]"`r`n")
        if ($boundary -ge 0) {
            $null = $pending[$Index].Append($text.Substring(0, $boundary + 1))
            [Console]::Error.Write($pending[$Index].ToString())
            $null = $pending[$Index].Clear()
        }
        $null = $pending[$Index].Append($text.Substring($boundary + 1))
    }

    function Initialize-InstallerDecoder([int]$Index, [bool]$Final) {
        $prefix = $prefixes[$Index].ToArray()
        $encoding = [Console]::OutputEncoding
        $skip = 0
        foreach ($bom in $boms) {
            $matches = $true
            for ($j = 0; $j -lt [Math]::Min($prefix.Length, $bom.Bytes.Length); $j++) {
                if ($prefix[$j] -ne $bom.Bytes[$j]) { $matches = $false; break }
            }
            if (-not $matches) { continue }
            if ($prefix.Length -lt $bom.Bytes.Length) {
                if (-not $Final) { return $false }
                continue
            }
            $encoding = $bom.Encoding
            $skip = $bom.Bytes.Length
            break
        }
        $decoders[$Index] = $encoding.GetDecoder()
        $characters[$Index] = New-Object char[] ($encoding.GetMaxCharCount(4096))
        Write-InstallerBytes $Index $prefix $skip ($prefix.Length - $skip) $false
        return $true
    }

    try {
        $stdoutPath = [IO.Path]::GetTempFileName()
        $stderrPath = [IO.Path]::GetTempFileName()
        # File redirection bypasses PS5's native-stream CLIXML/ErrorRecord parser.
        $process = Start-Process -FilePath $hostExe -ArgumentList $installerArgs `
            -NoNewWindow -PassThru `
            -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        # PS5's non-waiting Start-Process needs a retained handle to expose ExitCode.
        $null = $process.Handle
        $paths = @($stdoutPath, $stderrPath)
        # This is a transfer buffer, not an output limit. Drain both streams fairly.
        $buffer = New-Object byte[] 4096
        while ($true) {
            # Observe exit before draining so the final pass includes all child output.
            $exited = $process.HasExited
            $readCount = 0
            for ($i = 0; $i -lt $paths.Count; $i++) {
                if (-not $readers[$i] -and (Get-Item -LiteralPath $paths[$i]).Length -gt 0) {
                    $readers[$i] = [IO.File]::Open(
                        $paths[$i], [IO.FileMode]::Open, [IO.FileAccess]::Read,
                        ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
                    )
                }
                if ($readers[$i]) {
                    $count = $readers[$i].Read($buffer, 0, $buffer.Length)
                    if ($count -gt 0) {
                        $offset = 0
                        if (-not $decoders[$i]) {
                            while ($offset -lt $count -and $prefixes[$i].Count -lt 4) {
                                $prefixes[$i].Add($buffer[$offset++])
                            }
                            $null = Initialize-InstallerDecoder $i $false
                        }
                        if ($decoders[$i] -and $offset -lt $count) {
                            Write-InstallerBytes $i $buffer $offset ($count - $offset) $false
                        }
                    }
                    $readCount += $count
                }
            }
            if ($exited -and $readCount -eq 0) {
                # A growing file's temporary EOF must not flush an incomplete character.
                for ($i = 0; $i -lt $paths.Count; $i++) {
                    if (-not $decoders[$i] -and $prefixes[$i].Count -gt 0) {
                        $null = Initialize-InstallerDecoder $i $true
                    }
                    if ($decoders[$i]) { Write-InstallerBytes $i ([byte[]]@()) 0 0 $true }
                }
                foreach ($text in $pending) { [Console]::Error.Write($text.ToString()) }
                break
            }
            if ($readCount -eq 0) { Start-Sleep -Milliseconds 50 }
        }
        $process.WaitForExit()
        return $process.ExitCode
    } finally {
        if ($process) {
            try {
                if (-not $process.HasExited) {
                    # Match the installer's watchdog: do not leave a staged child behind.
                    & "$env:SystemRoot\System32\taskkill.exe" /PID $process.Id /T /F 2>&1 |
                        ForEach-Object { [Console]::Error.WriteLine($_) }
                    if ($LASTEXITCODE -ne 0 -and -not $process.HasExited) {
                        throw "taskkill failed with exit code $LASTEXITCODE"
                    }
                }
            } catch {
                [Console]::Error.WriteLine(
                    "[agent-machines] installer cleanup warning: $($_.Exception.Message)"
                )
            }
        }
        foreach ($resource in @($readers) + @($process)) {
            if ($resource) {
                try { $resource.Dispose() } catch {
                    [Console]::Error.WriteLine(
                        "[agent-machines] capture cleanup warning: $($_.Exception.Message)"
                    )
                }
            }
        }
        foreach ($path in @($stdoutPath, $stderrPath)) {
            if ($path) {
                try { Remove-Item -LiteralPath $path -Force -ErrorAction Stop } catch {
                    [Console]::Error.WriteLine(
                        "[agent-machines] could not remove capture '$path': $($_.Exception.Message)"
                    )
                }
            }
        }
    }
}

$python = Resolve-AgentMachinesRuntime
if ($python) { Invoke-AgentMachinesRuntime $python }
if ($env:AGENT_MACHINES_NO_SELFPROVISION) {
    [Console]::Error.WriteLine(
        '[agent-machines] runtime not provisioned ' +
        '(AGENT_MACHINES_NO_SELFPROVISION set).'
    )
    exit 1
}

$installer = Join-Path $scriptDir 'init.ps1'
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    [Console]::Error.WriteLine(
        "[agent-machines] payload installer not found: $installer"
    )
    exit 127
}
[Console]::Error.WriteLine(
    '[agent-machines] runtime not provisioned -- provisioning from the owning payload.'
)
[Console]::Error.WriteLine(
    '::agent-provisioning:: plugin=agent-machines eta_seconds=120 reason=first-use'
)
if ($actualMode -ceq 'namespaced') {
    if (
        $resolutionStatus -cne 'ready' -or
        $resolutionReason -cne 'namespaced-active'
    ) {
        [Console]::Error.WriteLine(
            '[agent-machines] deactivation-pending installation cannot ' +
            'provision a new runtime.'
        )
        exit 126
    }
    $provisionStatus = Invoke-AgentMachinesInstaller $installer 'cell-provision'
    if ($provisionStatus -ne 0) { exit $provisionStatus }
    $python = Resolve-AgentMachinesRuntime
    if ($python) { Invoke-AgentMachinesRuntime $python }
    [Console]::Error.WriteLine(
        '[agent-machines] provisioning completed without a resolvable runtime.'
    )
    exit 1
}

if (-not (Test-Path -LiteralPath $runtimeRoot)) {
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
}
$lockPath = Join-Path $runtimeRoot '.provision.lock'
$lock = $null
while (-not $lock) {
    try {
        $lock = [IO.File]::Open(
            $lockPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
    } catch {
        Start-Sleep -Milliseconds 200
    }
}

try {
    $python = Resolve-AgentMachinesRuntime
    if ($python) { Invoke-AgentMachinesRuntime $python }

    $provisionStatus = Invoke-AgentMachinesInstaller $installer 'stamp'
    if ($provisionStatus -ne 0) { exit $provisionStatus }
    $snapshot = ''
    try {
        $snapshot = ([IO.File]::ReadAllText(
            (Join-Path $legacyRoot 'payload-dir')
        )).Trim()
    } catch {}
    $snapshotInstaller = if ($snapshot) {
        Join-Path $snapshot 'scripts\init.ps1'
    } else {
        ''
    }
    if (
        -not $snapshotInstaller -or
        -not (Test-Path -LiteralPath $snapshotInstaller -PathType Leaf)
    ) {
        [Console]::Error.WriteLine(
            "[agent-machines] stamped snapshot installer not found: " +
            $snapshotInstaller
        )
        exit 127
    }
    $provisionStatus = Invoke-AgentMachinesInstaller $snapshotInstaller 'provision'
    if ($provisionStatus -ne 0) { exit $provisionStatus }

    $python = Resolve-AgentMachinesRuntime
    if ($python) { Invoke-AgentMachinesRuntime $python }
} finally {
    if ($lock) { $lock.Dispose() }
}
[Console]::Error.WriteLine(
    '[agent-machines] provisioning completed without a resolvable runtime.'
)
exit 1
