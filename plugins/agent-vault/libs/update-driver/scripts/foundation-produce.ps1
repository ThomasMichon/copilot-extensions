param(
    [Parameter(Mandatory)][string]$Root,
    [Parameter(Mandatory)][string]$SourceHash
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT' -or $PSVersionTable.PSEdition -ne 'Desktop' -or $PSVersionTable.PSVersion -lt [version]'5.1') {
    throw 'Native foundation production requires stock Windows PowerShell 5.1.'
}
$plugin = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$library = [IO.Path]::GetDirectoryName($plugin)
. (Join-Path $plugin 'src\update_driver\foundation.ps1')
. (Join-Path $plugin 'src\update_driver\directory.ps1')
. (Join-Path $library 'single-instance-lease\src\single_instance_lease\operation.ps1')
. (Join-Path $library 'agent-procutil\src\agent_procutil\native_owned.ps1')
. (Join-Path $plugin 'src\update_driver\participant.ps1')
Remove-Item Env:UPDATE_DRIVER_WITNESS -ErrorAction SilentlyContinue

$script:descendantsSettled = $true
function Invoke-FoundationChild {
    param([string]$Executable, [string[]]$Arguments, [string]$Log, [int]$Timeout)
    $script:descendantsSettled = $false
    try {
        $code = Invoke-NativeOwnedProcess -FilePath $Executable -Arguments $Arguments `
            -WorkingDirectory $plugin -LogPath $Log -TimeoutMilliseconds $Timeout
        $script:descendantsSettled = $true
        return $code
    } catch {
        $errorValue = $_.Exception
        while ($null -ne $errorValue) {
            if ($errorValue -is [CopilotExtensions.NativeOwnedProcessException]) {
                $script:descendantsSettled = $errorValue.CleanupConfirmed
                break
            }
            $errorValue = $errorValue.InnerException
        }
        throw
    }
}

[CopilotExtensions.NativeFoundationState]::CheckExecutionRoot($Root, $plugin)
New-NativeExecutionDirectory -Root $Root
$waiting = [Diagnostics.Stopwatch]::StartNew()
while ($true) {
    try {
        $owned = Enter-NativeOperationLock ([IO.Path]::Combine($Root, 'execution.lock'))
        break
    } catch {
        $errorValue = $_.Exception
        while ($errorValue.InnerException) { $errorValue = $errorValue.InnerException }
        if ($errorValue -isnot [IO.IOException] -or ($errorValue.HResult -band 0xffff) -ne 33 -or
            $waiting.ElapsedMilliseconds -ge 600000) { throw }
        Start-Sleep -Milliseconds 100
    }
}
$operation = $null
$diagnosticPath = Join-Path $Root ('foundation-producer-' + [Guid]::NewGuid().ToString('N') + '.log')
$witness = $null
$witnessSettled = $true
$reason = 'source_admission_failed'
try {
    $owned.AssertOwned()
    if ([CopilotExtensions.NativeFoundationState]::SourceDigest($plugin) -cne $SourceHash) {
        throw 'Foundation source differs from its requested inventory.'
    }
    $assertion = $owned.CreateWitnessAssertion([IO.Path]::Combine($Root, 'execution.lock'))
    $prior = [CopilotExtensions.NativeFoundationState]::ReconcileLost($Root, $assertion)
    if ($prior) {
        $value = $prior | ConvertFrom-Json
        if ($value.state -eq 'completed' -and $value.source_hash -ceq $SourceHash) {
            $version = 'foundation-' + $SourceHash
            $target = Join-Path $Root 'foundation'
            $selector = Join-Path $target 'current-version'
            $hasSelector = [CopilotExtensions.NativeFoundationState]::CheckPath($selector, $false, $true)
            if ($hasSelector -and (Get-Item -LiteralPath $selector).Length -gt 128) {
                throw 'Malformed foundation selector.'
            }
            if ($hasSelector -and [IO.File]::ReadAllText($selector).Trim() -ceq $version) {
                $slot = Join-Path $target ('versions\' + $version)
                $python = Join-Path $slot 'Scripts\python.exe'
                $launcher = Join-Path $slot '.runtime-bootstrap\runtime_launcher.py'
                foreach ($path in @($python, $launcher, (Join-Path $slot '.install-complete.json'))) {
                    $null = [CopilotExtensions.NativeFoundationState]::CheckPath($path, $false, $false)
                }
                $log = Join-Path $Root ('foundation-observe-' + [Guid]::NewGuid().ToString('N') + '.log')
                $code = Invoke-FoundationChild -Executable $python -Arguments @(
                    '-I', '-S', '-B', '-X', 'utf8', $launcher, '-m', 'update_driver.foundation_inputs'
                ) -Log $log -Timeout 30000
                if ($code -eq 0) { exit 0 }
            }
        }
    }
    $target = Join-Path $Root 'foundation'
    $selector = Join-Path $target 'current-version'
    $queueAware = [CopilotExtensions.NativeFoundationState]::CheckPath($selector, $false, $true)
    foreach ($name in @('queue.sqlite3', 'queue.sqlite3-wal', 'queue.sqlite3-shm', 'queue.sqlite3-journal')) {
        if ([CopilotExtensions.NativeFoundationState]::CheckPath((Join-Path $Root $name), $false, $true)) {
            $queueAware = $true
        }
    }
    if ($queueAware) {
        $reason = 'queue_admission_unavailable'
        $null = [CopilotExtensions.NativeFoundationState]::CheckPath($selector, $false, $false)
        if ((Get-Item -LiteralPath $selector).Length -gt 128) { throw 'Malformed foundation selector.' }
        $selected = [IO.File]::ReadAllText($selector).Trim()
        if ($selected -cnotmatch '\Afoundation-[0-9a-f]{64}\z') { throw 'Malformed foundation selector.' }
        $slot = Join-Path $target ('versions\' + $selected)
        $python = Join-Path $slot 'Scripts\python.exe'
        $launcher = Join-Path $slot '.runtime-bootstrap\runtime_launcher.py'
        foreach ($path in @($python, $launcher, (Join-Path $slot '.install-complete.json'))) {
            $null = [CopilotExtensions.NativeFoundationState]::CheckPath($path, $false, $false)
        }
        $admissionLog = Join-Path $Root ('foundation-admission-' + [Guid]::NewGuid().ToString('N') + '.log')
        $witness = New-NativeParticipantWitness -Custody $owned -Root $Root -RequestId ([Guid]::NewGuid().ToString('N'))
        $witnessSettled = $false
        try {
            $env:UPDATE_DRIVER_WITNESS = $witness.Environment()
            $code = Invoke-FoundationChild -Executable $python -Arguments @(
                '-I', '-S', '-B', '-X', 'utf8', $launcher, '-m', 'update_driver.foundation_admission',
                '--root', $Root, '--source-hash', $SourceHash
            ) -Log $admissionLog -Timeout 30000
        } finally {
            Remove-Item Env:UPDATE_DRIVER_WITNESS -ErrorAction SilentlyContinue
            $witness.Dispose()
            $witnessSettled = $true
            $witness = $null
        }
        if ($code -ne 0) { throw "Queue-aware foundation admission unavailable; see $admissionLog" }
        $operation = Get-NativeFoundationProgress -Root $Root
        if (-not $operation) { throw 'Queue-aware foundation admission did not persist progress.' }
        if ([IO.File]::ReadAllText($selector).Trim() -cne $selected) {
            throw 'Selected foundation changed during queue admission.'
        }
    } else {
        $operation = Start-NativeFoundationProgress -Custody $owned -Root $Root -Plugin $plugin -ExpectedSourceHash $SourceHash
    }
    $admitted = $operation | ConvertFrom-Json
    if ($admitted.state -cne 'running' -or $admitted.source_hash -cne $SourceHash) {
        throw 'Foundation progress differs from its requested admission.'
    }
    $bootstrap = Join-Path $Root ('bootstrap\' + $admitted.operation_id)
    $null = [CopilotExtensions.NativeFoundationState]::CheckPath($bootstrap, $true, $true)
    [IO.Directory]::CreateDirectory($bootstrap) | Out-Null
    $reason = 'source_freeze_failed'
    $assertion = $owned.CreateWitnessAssertion([IO.Path]::Combine($Root, 'execution.lock'))
    $frozen = [CopilotExtensions.NativeFoundationState]::FreezeSource(
        $plugin, (Join-Path $bootstrap 'inputs'), $SourceHash, $assertion
    )
    $log = Join-Path $Root ('foundation-' + $admitted.operation_id + '.log')
    $resultPath = Join-Path $bootstrap 'prerequisites.json'
    $config = @{
        bootstrap = $bootstrap; result = $resultPath
        helper = Join-Path $frozen 'scripts\installer-engine.ps1'
        operation = $admitted.operation_id; source = $SourceHash
    } | ConvertTo-Json -Compress
    $data = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($config))
    # Only the owner emits this fixed owned-child body. No acquisition entry point or argv recipe is exposed.
    $acquire = @'
$ErrorActionPreference = 'Stop'
$c = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__CONFIG__')) | ConvertFrom-Json
Remove-Item Env:UPDATE_DRIVER_WITNESS -ErrorAction SilentlyContinue
function Write-Ok([string]$Text) { [Console]::Out.WriteLine($Text) }
function Write-Step([string]$Text) { [Console]::Out.WriteLine($Text) }
function Write-Warn([string]$Text) { [Console]::Error.WriteLine($Text) }
function Write-Fail([string]$Text) { [Console]::Error.WriteLine($Text) }
. $c.helper
$env:UV_PYTHON_INSTALL_DIR = Join-Path $c.bootstrap 'python'
$env:UV_CACHE_DIR = Join-Path $c.bootstrap 'cache'
$env:UV_NO_PROGRESS = '1'
$uv = Ensure-Uv -InstallRoot $c.bootstrap
if (-not $uv) { throw 'Canonical uv acquisition failed.' }
$venv = Join-Path $c.bootstrap 'venv'
$python = Join-Path $venv 'Scripts\python.exe'
if (-not (New-SignedVenv -VenvDir $venv -VenvPython $python -PythonVersion '3.11' -MinimumSignedBaseVersion '3.11' -UvCommand $uv -WithoutPip)) {
    throw 'Canonical bootstrap interpreter acquisition failed.'
}
$result = @{
    schema = 'copilot-extensions.foundation-prerequisites'; version = 1
    operation_id = $c.operation; source_hash = $c.source; uv = $uv; python = $python
} | ConvertTo-Json -Compress
$bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($result)
$stream = New-Object IO.FileStream($c.result, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
finally { $stream.Dispose() }
exit 0
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($acquire.Replace('__CONFIG__', $data)))
    Remove-Item Env:UPDATE_DRIVER_WITNESS -ErrorAction SilentlyContinue
    $reason = 'prerequisite_failed'
    $code = Invoke-FoundationChild -Executable (Join-Path $PSHOME 'powershell.exe') `
        -Arguments @('-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', $encoded) `
        -Log $log -Timeout 240000
    if ($code -ne 0) {
        $null = Complete-NativeFoundationProgress -Custody $owned -Root $Root -Operation $operation `
            -Plugin $plugin -ExitCode $code -Reason $reason
        exit $code
    }
    $reason = 'prerequisite_result_invalid'
    $null = [CopilotExtensions.NativeFoundationState]::CheckPath($resultPath, $false, $false)
    if ((Get-Item -LiteralPath $resultPath).Length -gt 4096) { throw 'Oversized prerequisite result.' }
    $result = [IO.File]::ReadAllText($resultPath) | ConvertFrom-Json
    $names = @($result.PSObject.Properties.Name | Sort-Object)
    if (($names -join ',') -cne 'operation_id,python,schema,source_hash,uv,version' -or
        $result.schema -cne 'copilot-extensions.foundation-prerequisites' -or $result.version -ne 1 -or
        $result.operation_id -cne $admitted.operation_id -or $result.source_hash -cne $SourceHash -or
        $result.python -cne (Join-Path $bootstrap 'venv\Scripts\python.exe')) {
        throw 'Prerequisite result differs from its owned operation.'
    }
    $null = [CopilotExtensions.NativeFoundationState]::CheckPath($result.python, $false, $false)
    $reason = 'source_changed'
    if ([CopilotExtensions.NativeFoundationState]::SourceDigest($plugin) -cne $SourceHash) {
        throw 'Foundation source changed before construction.'
    }
    $owned.AssertOwned()
    $env:UV_CACHE_DIR = Join-Path $bootstrap 'cache'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $bootstrap 'python'
    $witness = New-NativeParticipantWitness -Custody $owned -Root $Root -RequestId $admitted.operation_id
    $witnessSettled = $false
    try {
        $env:UPDATE_DRIVER_WITNESS = $witness.Environment()
        $reason = 'builder_failed'
        $code = Invoke-FoundationChild -Executable $result.python -Arguments @(
            '-I', '-B', '-X', 'utf8', (Join-Path $frozen 'scripts\foundation-build.py'),
            '--root', $Root, '--source-hash', $SourceHash, '--uv', $result.uv, '--base-python', $result.python,
            '--admitted-source', $plugin
        ) -Log $log -Timeout 300000
    } finally {
        Remove-Item Env:UPDATE_DRIVER_WITNESS -ErrorAction SilentlyContinue
        $witness.Dispose()
        $witnessSettled = $true
        $witness = $null
    }
    if ($code -eq 0) {
        $reason = 'source_changed'
        $null = Complete-NativeFoundationProgress -Custody $owned -Root $Root -Operation $operation -Plugin $plugin -ExitCode 0
    } else {
        $null = Complete-NativeFoundationProgress -Custody $owned -Root $Root -Operation $operation -Plugin $plugin -ExitCode $code -Reason $reason
    }
    exit $code
} catch {
    $message = "Foundation producer failed: $($_.Exception.Message)"
    [Console]::Error.WriteLine($message)
    [IO.File]::AppendAllText($diagnosticPath, $message + [Environment]::NewLine)
    if ($operation) {
        try {
            if ($script:descendantsSettled -and $witnessSettled) {
                $null = Complete-NativeFoundationProgress -Custody $owned -Root $Root -Operation $operation -Plugin $plugin -ExitCode 125 -Reason $reason
            } else {
                $null = Complete-NativeFoundationProgress -Custody $owned -Root $Root -Operation $operation -Plugin $plugin -Reason producer_settlement_unknown
            }
        } catch {
            $message = "Foundation outcome persistence failed: $($_.Exception.Message)"
            [Console]::Error.WriteLine($message)
            [IO.File]::AppendAllText($diagnosticPath, $message + [Environment]::NewLine)
        }
    }
    exit 125
} finally {
    Remove-Item Env:UPDATE_DRIVER_WITNESS -ErrorAction SilentlyContinue
    if ($witness) { $witness.Dispose() }
    $owned.Dispose()
}
