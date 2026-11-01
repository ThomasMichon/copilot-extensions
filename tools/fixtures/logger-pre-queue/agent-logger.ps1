$env:PYTHONUTF8 = '1'
$_root = Join-Path $env:USERPROFILE '.agent-logger'
$_resolver = Join-Path $_root 'bin\resolve-runtime.ps1'
if (-not (Test-Path -LiteralPath $_resolver) -or
    -not (Select-String -LiteralPath $_resolver -SimpleMatch '# Trace contract: normal/read-only/ready-v1' -Quiet)) {
    [Console]::Error.WriteLine('[agent-logger] read-only runtime resolver is unavailable. Re-enable/update the plugin, then retry.')
    exit 126
}
function _Resolve-Py([string]$TraceMode = 'ready') {
    $AgentRtPy = $null
    $env:AGENT_RT_ROOT = $_root
    . $_resolver -TraceMode $TraceMode
    return $AgentRtPy
}
$_py = _Resolve-Py
if ($_py) { & $_py -m agent_logger @args; exit $LASTEXITCODE }
if ($env:AGENT_LOGGER_NO_SELFPROVISION) { [Console]::Error.WriteLine('[agent-logger] runtime not provisioned (AGENT_LOGGER_NO_SELFPROVISION set).'); exit 1 }
$_snap = ''
try { $_snap = ([IO.File]::ReadAllText((Join-Path $_root 'payload-dir'))).Trim() } catch {}
$_inst = if ($_snap) { Join-Path $_snap 'scripts\install.ps1' } else { '' }
if (-not ($_inst -and (Test-Path -LiteralPath $_inst))) { [Console]::Error.WriteLine('[agent-logger] cannot self-provision: snapshot installer not found. Re-enable the plugin, then retry.'); exit 127 }
$_probe = Join-Path $_snap 'scripts\installation-context\plugin-publication-probe.ps1'
if (-not (Test-Path -LiteralPath $_probe -PathType Leaf)) {
    [Console]::Error.WriteLine('[agent-logger] Runtime publication refused: snapshot admission helper is unavailable. Re-enable/update the plugin, then retry.')
    exit 126
}
$global:LASTEXITCODE = 0
& $_probe -PayloadRoot $_snap -PluginId agent-logger -LegacyRoot $_root
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$_py = _Resolve-Py normal
if ($_py) { & $_py -m agent_logger @args; exit $LASTEXITCODE }
[Console]::Error.WriteLine('[agent-logger] runtime not provisioned -- provisioning on first use (acquires uv + builds a venv; ~30-120s). Do not kill; extend your timeout.')
[Console]::Error.WriteLine('::agent-provisioning:: plugin=agent-logger eta_seconds=120 reason=first-use')
$_pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
$_exe = if ($_pwsh) { $_pwsh.Source } else { 'powershell.exe' }
& $_exe -NoProfile -ExecutionPolicy Bypass -File $_inst provision 2>&1 | ForEach-Object { [Console]::Error.WriteLine($_) }
$_py = _Resolve-Py
if ($_py) { & $_py -m agent_logger @args; exit $LASTEXITCODE }
[Console]::Error.WriteLine('[agent-logger] provisioning did not yield a runtime. See the log above; retry, or run the snapshot installer manually.')
exit 1
