function Get-SourceRuntimeIdentity {
    param([string]$Payload = $PluginDir, [string]$ExpectedVersion = '')
    $py = Get-BootstrapPython -ExcludeRuntimeRoot
    if (-not $py) { throw 'Source runtime validation requires a bootstrap Python interpreter' }
    $arguments = @('-I', '-B', (Join-Path $ScriptDir 'versioned_runtime_source.py'),
        '--payload', $Payload, '--package-version', $PackageVersion,
        '--plugin', 'agent-worktrees', '--format', 'json')
    if ($ExpectedVersion) { $arguments += @('--expected-runtime-version', $ExpectedVersion) }
    $nativeError = ''
    $detail = Invoke-Hidden -FilePath $py -ArgList $arguments -ErrorOutput ([ref]$nativeError)
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "Source runtime identity validation failed (exit ${code}): $detail$nativeError" }
    return $detail.Trim()
}

function Initialize-SourceRuntime {
    $script:PackageVersion = $SrcVersion
    $script:SourceRuntimeIdentity = $null
    $script:SourceRuntimeManifest = $null
    try {
        [IO.File]::GetAttributes((Join-Path $PluginDir 'runtime-source.json')) | Out-Null
    } catch [IO.FileNotFoundException] {
        return
    } catch [IO.DirectoryNotFoundException] {
        return
    }
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
    param([string]$Payload = $PluginDir)
    $metadataPresent = $true
    try {
        [IO.File]::GetAttributes((Join-Path $Payload 'runtime-source.json')) | Out-Null
    } catch [IO.FileNotFoundException] {
        $metadataPresent = $false
    } catch [IO.DirectoryNotFoundException] {
        $metadataPresent = $false
    }
    if ($SourceRuntimeIdentity -or $metadataPresent) {
        $identity = Get-SourceRuntimeIdentity -Payload $Payload -ExpectedVersion $SrcVersion
        if ($identity -cne $SourceRuntimeIdentity) {
            throw 'Source runtime identity changed after runtime selection; refusing publication'
        }
        foreach ($name in @('payload-dir', 'stamped-version')) {
            $path = Join-Path $InstallDir $name
            try {
                $attributes = [IO.File]::GetAttributes($path)
            } catch [IO.FileNotFoundException] {
                continue
            } catch [IO.DirectoryNotFoundException] {
                continue
            }
            if ($attributes -band ([IO.FileAttributes]::Directory -bor [IO.FileAttributes]::ReparsePoint)) {
                throw "Source redirect must be an ordinary file: $path"
            }
        }
    }
}

function Publish-SourceSnapshot {
    Assert-SourceRuntime
    $py = Get-BootstrapPython -ExcludeRuntimeRoot
    if (-not $py) { throw 'Source snapshot publication requires a bootstrap Python interpreter' }
    $arguments = @('-I', '-B', (Join-Path $ScriptDir 'versioned_runtime_source.py'),
        '--payload', $PluginDir, '--package-version', $PackageVersion,
        '--plugin', 'agent-worktrees', '--expected-runtime-version', $SrcVersion,
        '--expected-source-identity', $SourceRuntimeIdentity,
        '--snapshot-root', $InstallDir)
    $nativeError = ''
    $snapshot = Invoke-Hidden -FilePath $py -ArgList $arguments -ErrorOutput ([ref]$nativeError)
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "Source snapshot publication failed (exit ${code}): $snapshot$nativeError"
    }
    Assert-SourceRuntime
    Assert-SourceRuntime -Payload $snapshot.Trim()
    return $snapshot.Trim()
}

function Publish-SourceRedirects {
    param([Parameter(Mandatory)][string]$Snapshot)
    Assert-SourceRuntime
    Assert-SourceRuntime -Payload $Snapshot
    $py = Get-BootstrapPython -ExcludeRuntimeRoot
    if (-not $py) { throw 'Source redirect publication requires a bootstrap Python interpreter' }
    $nativeError = ''
    [void](Invoke-Hidden -FilePath $py -ArgList @(
        '-I', '-B', (Join-Path $ScriptDir 'versioned_runtime.py'), '--root', $InstallDir,
        'publish-redirects', '--payload-dir', $Snapshot, '--version', $SrcVersion,
        '--expected-source-identity', $SourceRuntimeIdentity
    ) -ErrorOutput ([ref]$nativeError))
    if ($LASTEXITCODE -ne 0) {
        throw "Source redirect publication failed (exit ${LASTEXITCODE}): $nativeError"
    }
}
