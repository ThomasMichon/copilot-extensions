# Replaces only the canonical prerequisite helper in a copied, isolated test source.
function Ensure-Uv {
    param([string]$InstallRoot)
    if ($env:UPDATE_DRIVER_WITNESS) { throw 'Prerequisites inherited builder custody transport.' }
    $root = [IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($InstallRoot))
    $progress = [IO.File]::ReadAllText((Join-Path $root 'foundation-operation.json')) | ConvertFrom-Json
    if ($progress.state -ne 'running') { throw 'Prerequisites ran before durable admission.' }
    [IO.File]::WriteAllText((Join-Path $root 'prerequisite-started'), $progress.operation_id)
    return $env:FOUNDATION_FIXTURE_PYTHON
}

function New-SignedVenv {
    param([string]$VenvDir, [string]$VenvPython, [string]$PythonVersion, [version]$MinimumSignedBaseVersion, [string]$UvCommand, [switch]$WithoutPip)
    if ($PythonVersion -ne '3.11' -or $MinimumSignedBaseVersion -ne [version]'3.11') {
        throw 'Foundation lost its bootstrap interpreter floor.'
    }
    if (-not $WithoutPip) { throw 'UV-owned foundation must not seed pip.' }
    & $env:FOUNDATION_FIXTURE_PYTHON -I -B -m venv --copies --without-pip $VenvDir
    if ($LASTEXITCODE -ne 0) { throw 'Fixture interpreter construction failed.' }
    return [IO.File]::Exists($VenvPython)
}
