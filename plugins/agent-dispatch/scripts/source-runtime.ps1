function Get-SourceRuntimeIdentity {
    param([string]$Payload = $PluginDir, [string]$ExpectedVersion = '')
    $py = Get-BootstrapPython
    if (-not $py) { throw 'Source runtime validation requires a bootstrap Python interpreter' }
    $arguments = @('-I', '-B', (Join-Path $ScriptDir 'versioned_runtime_source.py'),
        '--payload', $Payload, '--package-version', $PackageVersion,
        '--plugin', 'agent-dispatch', '--format', 'json')
    if ($ExpectedVersion) { $arguments += @('--expected-runtime-version', $ExpectedVersion) }
    $nativeError = ''
    $result = Invoke-Hidden -FilePath $py -ArgList $arguments -ErrorOutput ([ref]$nativeError)
    if ($LASTEXITCODE -ne 0) {
        throw "Source runtime identity validation failed (exit ${LASTEXITCODE}): $result$nativeError"
    }
    return ("$result").Trim()
}

function Initialize-SourceRuntime {
    $script:PackageVersion = $SrcVersion
    $script:SourceRuntimeIdentity = $null
    $metadata = Join-Path $PluginDir 'runtime-source.json'
    try { [IO.File]::GetAttributes($metadata) | Out-Null }
    catch [IO.FileNotFoundException] { return }
    catch [IO.DirectoryNotFoundException] { return }
    $script:SourceRuntimeIdentity = Get-SourceRuntimeIdentity
    $script:SrcVersion = ($SourceRuntimeIdentity | ConvertFrom-Json).runtime_version
    $script:VenvDir = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
    $script:VenvPython = if ($env:OS -eq 'Windows_NT') {
        Join-Path $VenvDir 'Scripts\python.exe'
    } else { Join-Path $VenvDir 'bin/python' }
    $script:LinkDir = $VenvDir
    $script:LinkPython = $VenvPython
}

function Assert-SourceRuntime {
    $selected = Get-Variable -Name SourceRuntimeIdentity -Scope Script -ValueOnly -ErrorAction SilentlyContinue
    $payload = Get-Variable -Name PluginDir -ValueOnly -ErrorAction SilentlyContinue
    if ($selected -or ($payload -and
        (Test-Path -LiteralPath (Join-Path $payload 'runtime-source.json')))) {
        $identity = Get-SourceRuntimeIdentity -ExpectedVersion $SrcVersion
        if ($identity -cne $script:SourceRuntimeIdentity) {
            throw 'Source runtime identity changed after runtime selection; refusing publication'
        }
    }

}

function Assert-SourceSnapshot {
    param([Parameter(Mandatory)][string]$Snapshot)
    Assert-SourceRuntime
    $identity = Get-SourceRuntimeIdentity -Payload $Snapshot -ExpectedVersion $SrcVersion
    if ($identity -cne $script:SourceRuntimeIdentity) {
        throw 'Source build snapshot changed after runtime selection; refusing publication'
    }
}

function Publish-SourceSnapshot {
    Assert-SourceRuntime
    $py = Get-BootstrapPython
    if (-not $py) { throw 'Source snapshot publication requires a bootstrap Python interpreter' }
    $arguments = @('-I', '-B', (Join-Path $ScriptDir 'versioned_runtime_source.py'),
        '--payload', $PluginDir, '--package-version', $PackageVersion,
        '--plugin', 'agent-dispatch', '--expected-runtime-version', $SrcVersion,
        '--expected-source-identity', $script:SourceRuntimeIdentity,
        '--snapshot-root', $InstallDir)
    $nativeError = ''
    $snapshot = Invoke-Hidden -FilePath $py -ArgList $arguments -ErrorOutput ([ref]$nativeError)
    if ($LASTEXITCODE -ne 0) {
        throw "Source snapshot publication failed (exit ${LASTEXITCODE}): $snapshot$nativeError"
    }
    Assert-SourceRuntime
    return ("$snapshot").Trim()
}

function Publish-SourceRedirects {
    param([Parameter(Mandatory)][string]$Snapshot)
    Assert-SourceRuntime
    $py = Get-BootstrapPython
    if (-not $py) { throw 'Source redirect publication requires a bootstrap Python interpreter' }
    $arguments = @('-I', '-B', (Join-Path $ScriptDir 'versioned_runtime.py'),
        '--root', $InstallDir, 'publish-redirects', '--payload-dir', $Snapshot,
        '--version', $SrcVersion, '--expected-source-identity', $script:SourceRuntimeIdentity)
    $nativeError = ''
    $result = Invoke-Hidden -FilePath $py -ArgList $arguments -ErrorOutput ([ref]$nativeError)
    if ($LASTEXITCODE -ne 0) {
        throw "Source redirect publication failed (exit ${LASTEXITCODE}): $result$nativeError"
    }
}
