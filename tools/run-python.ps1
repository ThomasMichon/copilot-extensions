# Run Python for this repository through `uv run` with the uv-managed CPython
# pinned in the repository root's .python-version. uv installs that Python
# when it is missing. A system Python is never used as a fallback.
# Compatible with Windows PowerShell 5.1 and PowerShell 7.
#
# Usage: tools/run-python.ps1 [--with <requirement>...] <python arguments...>
# Example: tools/run-python.ps1 tools/changefile.py list
$ErrorActionPreference = 'Stop'

$pinFile = Join-Path (Split-Path -Parent $PSScriptRoot) '.python-version'
$pin = ''
if (Test-Path -LiteralPath $pinFile -PathType Leaf) {
    foreach ($line in Get-Content -LiteralPath $pinFile) {
        $value = ($line -replace '#.*', '').Trim()
        if ($value) { $pin = $value; break }
    }
}
if (-not $pin) {
    [Console]::Error.WriteLine("run-python: no Python version pin found in '$pinFile'.")
    exit 1
}

$uv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $uv) {
    [Console]::Error.WriteLine("run-python: uv is required to run Python in this repository, but 'uv' was not found on PATH. Install uv (https://docs.astral.sh/uv/getting-started/installation/) and retry. A system Python is never used as a fallback.")
    exit 1
}

# Leading --with/--with-editable/--with-requirements (and --isolated) options
# go to `uv run`; everything after them goes to the pinned `python`.
$all = @($args)
$options = @()
$i = 0
while ($i -lt $all.Count) {
    $arg = [string]$all[$i]
    if ($arg -in '--with', '--with-editable', '--with-requirements') {
        if ($i + 1 -ge $all.Count) { break }
        $options += $arg, $all[$i + 1]; $i += 2; continue
    }
    if ($arg -match '^--with(-editable|-requirements)?=' -or $arg -eq '--isolated') {
        $options += $arg; $i += 1; continue
    }
    break
}
$rest = @()
if ($i -lt $all.Count) { $rest = $all[$i..($all.Count - 1)] }

& $uv.Source run --no-project --managed-python --python $pin @options python @rest
exit $LASTEXITCODE
