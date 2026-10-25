function Get-SourceRuntimeIdentity {
    param([string]$Payload = $PluginDir, [string]$ExpectedVersion = '')
    $py = Get-BootstrapPython
    if (-not $py) { throw 'Source runtime validation requires a bootstrap Python interpreter' }
    $arguments = @('-I', '-B', (Join-Path $ScriptDir 'versioned_runtime_source.py'),
        '--payload', $Payload, '--package-version', $PackageVersion,
        '--plugin', 'agent-vault', '--format', 'json')
    if ($ExpectedVersion) { $arguments += @('--expected-runtime-version', $ExpectedVersion) }
    $nativeError = ''
    $result = Invoke-Hidden -FilePath $py -ArgList $arguments -ErrorOutput ([ref]$nativeError)
    if ($LASTEXITCODE -ne 0) {
        throw "Source runtime identity validation failed (exit ${LASTEXITCODE}): $result$nativeError"
    }
    return $result.Trim()
}

function Initialize-SourceRuntime {
    $script:PackageVersion = $SrcVersion
    $script:SourceRuntimeIdentity = $null
    $script:SourceRuntimeSnapshot = $null
    $metadata = Join-Path $PluginDir 'runtime-source.json'
    try { [IO.File]::GetAttributes($metadata) | Out-Null }
    catch [IO.FileNotFoundException] {
        if (-not (Test-Path Env:\COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN)) { return }
    }
    catch [IO.DirectoryNotFoundException] {
        if (-not (Test-Path Env:\COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN)) { return }
    }
    $script:SourceRuntimeIdentity = Get-SourceRuntimeIdentity
    $script:SrcVersion = ($SourceRuntimeIdentity | ConvertFrom-Json).runtime_version
    $script:VenvDir = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
    $script:VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
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
        foreach ($name in @('payload-dir', 'stamped-version')) {
            $path = Join-Path $InstallDir $name
            if (Test-Path -LiteralPath $path) {
                $item = Get-Item -LiteralPath $path -Force
                if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    throw "Source redirect must be an ordinary file: $path"
                }
            }
        }
    }
}

function Publish-SourceSnapshot {
    Assert-SourceRuntime
    $py = Get-BootstrapPython
    if (-not $py) { throw 'Source snapshot publication requires a bootstrap Python interpreter' }
    $arguments = @('-I', '-B', (Join-Path $ScriptDir 'versioned_runtime_source.py'),
        '--payload', $PluginDir, '--package-version', $PackageVersion,
        '--plugin', 'agent-vault', '--expected-runtime-version', $SrcVersion,
        '--expected-source-identity', $script:SourceRuntimeIdentity,
        '--snapshot-root', $InstallDir)
    $nativeError = ''
    $snapshot = Invoke-Hidden -FilePath $py -ArgList $arguments -ErrorOutput ([ref]$nativeError)
    if ($LASTEXITCODE -ne 0) {
        throw "Source snapshot publication failed (exit ${LASTEXITCODE}): $snapshot$nativeError"
    }
    Assert-SourceRuntime
    return $snapshot.Trim()
}

function Publish-SourcePayload {
    $snapshot = Publish-SourceSnapshot
    $py = Get-BootstrapPython
    if (-not $py) { throw 'Source redirect publication requires a bootstrap Python interpreter' }
    Prepare-SourceManifestContext
    Assert-SourceRuntime
    $nativeError = ''
    $result = Invoke-Hidden -FilePath $py -ArgList @(
        '-I', '-B', (Join-Path $ScriptDir 'versioned_runtime.py'),
        '--root', $InstallDir, 'publish-redirects',
        '--payload-dir', $snapshot, '--version', $SrcVersion,
        '--expected-source-identity', $script:SourceRuntimeIdentity
    ) -ErrorOutput ([ref]$nativeError)
    if ($LASTEXITCODE -ne 0) {
        throw "Source redirect publication failed (exit ${LASTEXITCODE}): $result$nativeError"
    }
    $script:SourceRuntimeSnapshot = $snapshot
}

function Get-SourceRuntimeFields {
    Assert-SourceRuntime
    $fields = @{}
    if ($script:SourceRuntimeIdentity) {
        $data = $script:SourceRuntimeIdentity | ConvertFrom-Json
        foreach ($property in $data.PSObject.Properties) { $fields[$property.Name] = $property.Value }
    }
    return $fields
}

function Prepare-SourceManifestContext {
    $script:SourceManifestOrigin = if ($env:COPILOT_PLUGIN_STAGED_FROM) {
        $env:COPILOT_PLUGIN_STAGED_FROM
    } else { $PluginDir }
    $script:SourceManifestGitInfo = @{ commit = $null; branch = $null; dirty = $false }
    if (($script:SourceManifestOrigin -replace '\\', '/') -notmatch '/\.copilot/installed-plugins/') {
        $script:SourceManifestGitInfo = Get-GitInfo (Split-Path $script:SourceManifestOrigin)
    }
}
