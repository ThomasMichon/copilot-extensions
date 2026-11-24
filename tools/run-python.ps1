# Run Python for this repository through `uv run` with the uv-managed CPython
# pinned in the repository root's .python-version. uv installs that Python
# when it is missing. A system Python is never used as a fallback.
# Compatible with Windows PowerShell 5.1 and PowerShell 7.
#
# Usage: tools/run-python.ps1 [--with <requirement>...] <python arguments...>
# Example: tools/run-python.ps1 tools/changefile.py list
# Leading --with requirements are prepared under the seven-day seasoning
# policy (tools/run_python_with.py), never by a plain `uv run --with`.
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$pinFile = Join-Path $root '.python-version'
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

# Leading --with options go to tools/run_python_with.py; everything after them
# goes to the pinned `python`.
$all = @($args)
$options = @()
$i = 0
while ($i -lt $all.Count) {
    $arg = [string]$all[$i]
    if ($arg -ceq '--with') {
        if ($i + 1 -ge $all.Count) { break }
        $options += $arg, [string]$all[$i + 1]; $i += 2; continue
    }
    if ($arg.StartsWith('--with=', [StringComparison]::Ordinal)) {
        $options += $arg; $i += 1; continue
    }
    break
}
$rest = @()
if ($i -lt $all.Count) { $rest = @($all[$i..($all.Count - 1)] | ForEach-Object { [string]$_ }) }
$forwarded = @('run', '--no-project', '--managed-python', '--python', $pin, 'python')
if ($options.Count -gt 0) {
    $forwarded += @((Join-Path $PSScriptRoot 'run_python_with.py')) + $options + @('--')
}
$forwarded += $rest

# Legacy native-command argument passing (Windows PowerShell 5.1, and
# PowerShell 7 in Legacy mode) drops empty arguments and strips embedded
# double quotes; encode each argument so the child receives it intact.
$nativeMode = Get-Variable PSNativeCommandArgumentPassing -ErrorAction SilentlyContinue
if ($null -eq $nativeMode -or $nativeMode.Value -eq 'Legacy') {
    # Windows PowerShell counts escaped quotes when deciding whether to wrap an argument.
    $quoteReplacement = if ($PSVersionTable.PSVersion.Major -lt 6) { '$1$1""' } else { '$1$1\"' }
    $forwarded = @($forwarded | ForEach-Object {
        if ($_ -and $_ -notmatch '[\s"]') { $_ }
        else { '"' + [regex]::Replace([regex]::Replace($_, '(\\*)"', $quoteReplacement), '(\\+)$', '$1$1') + '"' }
    })
}
# Keep a literal stop-parsing marker as data, including when arguments are splatted.
for ($index = 0; $index -lt $forwarded.Count; $index++) {
    if ($forwarded[$index] -ceq '--%') { $forwarded[$index] = New-Object Text.StringBuilder '--%' }
}

# Inherited uv and environment selectors must not redirect the interpreter or
# the working directory that relative script paths resolve against. Restore
# them afterwards so a dot-sourced or in-process caller keeps its environment.
$saved = @{}
foreach ($key in @('UV_PYTHON', 'UV_PYTHON_PREFERENCE', 'UV_MANAGED_PYTHON', 'UV_NO_MANAGED_PYTHON',
        'VIRTUAL_ENV', 'CONDA_PREFIX', 'UV_WORKING_DIR', 'UV_WORKING_DIRECTORY', 'UV_ISOLATED',
        'UV_ENV_FILE', 'UV_PROJECT', 'UV_FROZEN', 'UV_LOCKED', 'UV_NO_SYNC', 'UV_CONFIG_FILE')) {
    $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
}
$code = 1
try {
    foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, [NullString]::Value, 'Process') }
    & $uv.Source @forwarded
    $code = $LASTEXITCODE
} finally {
    foreach ($key in $saved.Keys) {
        $value = $saved[$key]
        if ($null -eq $value) { $value = [NullString]::Value }
        [Environment]::SetEnvironmentVariable($key, $value, 'Process')
    }
}
exit $code