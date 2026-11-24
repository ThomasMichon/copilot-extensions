function Get-DispatchPreparedBootstrapPython {
    param([string]$Root, [string]$ScriptsRoot)
    $bootstrapDirectory = $ScriptsRoot
    $provider = Join-Path $ScriptsRoot 'bootstrap-uv.ps1'
    $request = Join-Path $ScriptsRoot 'installer-instance-python-version.txt'
    $installer = Join-Path $ScriptsRoot 'install.ps1'
    # Only the canonical unmaterialized source reference may use repository support.
    if (-not (Get-Item -LiteralPath $provider -Force -ErrorAction SilentlyContinue) -and
        -not (Get-Item -LiteralPath $request -Force -ErrorAction SilentlyContinue) -and
        [IO.File]::Exists($installer) -and
        ([IO.File]::ReadAllLines($installer) -ccontains
            ". (Join-Path `$PSScriptRoot '..\..\..\libs\installer-engine\installer-engine.ps1')")) {
        $bootstrapDirectory = Join-Path $ScriptsRoot '..\..\..\libs\installer-engine'
    }
    try {
        $python = Resolve-NativeBootstrapPython -Root $Root -BootstrapDirectory $bootstrapDirectory
        if ($python) { return $python }
    } catch { }
    throw 'prepared uv-managed bootstrap Python is unavailable; use explicit prerequisite setup. No installation was attempted.'
}
