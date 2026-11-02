$ErrorActionPreference = 'Stop'
$_command = '__COMMAND__'
$_root = Join-Path $env:USERPROFILE '.agent-logger'
$_payload = ''
try { $_payload = ([IO.File]::ReadAllText((Join-Path $_root 'payload-dir'))).Trim() } catch {}
$_shim = if ($_payload) { Join-Path $_payload "bin\$($_command).ps1" } else { '' }
if (-not ($_shim -and (Test-Path -LiteralPath $_shim))) {
    [Console]::Error.WriteLine("[$_command] owning payload shim not found: $_shim")
    [Console]::Error.WriteLine("[$_command] re-enable/update agent-logger, then retry.")
    exit 127
}
$env:COPILOT_PLUGIN_ROOT = $_payload
& $_shim @args
exit $LASTEXITCODE
