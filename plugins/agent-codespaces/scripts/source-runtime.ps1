function Get-SourceRuntimeIdentity {
    param(
        [string]$Payload = $PluginDir,
        [string]$ExpectedVersion = ''
    )
    $py = Get-BootstrapPython
    if (-not $py) { throw 'Source runtime validation requires a bootstrap Python interpreter' }
    $arguments = @('-I', '-B', (Join-Path $ScriptDir 'versioned_runtime_source.py'),
        '--payload', $Payload, '--package-version', $PackageVersion,
        '--plugin', 'agent-codespaces', '--format', 'json')
    if ($ExpectedVersion) { $arguments += @('--expected-runtime-version', $ExpectedVersion) }
    $nativeError = ''
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $result = @(Invoke-Hidden -FilePath $py -ArgList $arguments -ErrorOutput ([ref]$nativeError))
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    $detail = ($result | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
    if ($code -ne 0) { throw "Source runtime identity validation failed (exit ${code}): $detail$nativeError" }
    return $detail.Trim()
}

function Initialize-SourceRuntime {
    $script:PackageVersion = $SrcVersion
    $script:SourceRuntimeIdentity = $null
    $metadata = Join-Path $PluginDir 'runtime-source.json'
    try {
        [IO.File]::GetAttributes($metadata) | Out-Null
    } catch [IO.FileNotFoundException] {
        if (-not (Test-Path Env:\COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN)) { return }
    } catch [IO.DirectoryNotFoundException] {
        if (-not (Test-Path Env:\COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN)) { return }
    }
    $script:SourceRuntimeIdentity = Get-SourceRuntimeIdentity
    $script:SrcVersion = ($SourceRuntimeIdentity | ConvertFrom-Json).runtime_version
    $script:VenvDir = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
    $script:VenvPython = if ($env:OS -eq 'Windows_NT') {
        Join-Path $VenvDir 'Scripts\python.exe'
    } else {
        Join-Path $VenvDir 'bin/python'
    }
    $script:LinkDir = $VenvDir
    $script:LinkPython = $VenvPython
}

function Assert-SourceRuntime {
    if ($script:SourceRuntimeIdentity -or
        (Test-Path -LiteralPath (Join-Path $PluginDir 'runtime-source.json'))) {
        $identity = Get-SourceRuntimeIdentity -ExpectedVersion $SrcVersion
        if ($identity -ne $script:SourceRuntimeIdentity) {
            throw 'Source runtime identity changed after runtime selection; refusing publication'
        }
    }
}

function Assert-SourceSnapshot {
    param([Parameter(Mandatory)][string]$Snapshot)
    Assert-SourceRuntime
    if ($script:SourceRuntimeIdentity) {
        $identity = Get-SourceRuntimeIdentity -Payload $Snapshot -ExpectedVersion $SrcVersion
        if ($identity -ne $script:SourceRuntimeIdentity) {
            throw 'Stamped source snapshot failed identity validation; redirects were not published'
        }

    }
}

function Publish-SourceSnapshot {
    Assert-SourceRuntime
    $py = Get-BootstrapPython
    if (-not $py) { throw 'Source snapshot publication requires a bootstrap Python interpreter' }
    $arguments = @('-I', '-B', (Join-Path $ScriptDir 'versioned_runtime_source.py'),
        '--payload', $PluginDir, '--package-version', $PackageVersion,
        '--plugin', 'agent-codespaces', '--expected-runtime-version', $SrcVersion,
        '--expected-source-identity', $script:SourceRuntimeIdentity,
        '--snapshot-root', $InstallDir)
    $nativeError = ''
    $snapshot = Invoke-Hidden -FilePath $py -ArgList $arguments -ErrorOutput ([ref]$nativeError)
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "Source snapshot publication failed (exit ${code}): $snapshot$nativeError"
    }
    Assert-SourceRuntime
    return $snapshot.Trim()
}
