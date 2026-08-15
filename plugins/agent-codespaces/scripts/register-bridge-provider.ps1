# register-bridge-provider -- drop this plugin's agent-bridge namespace-provider
# manifest into the providers.d registry so agent-bridge discovers it
# DECLARATIVELY (no imperative "bridge register" call). Windows twin of the .sh;
# see it for the contract.
#
# Generic + self-locating: byte-identical across provider plugins. Safe +
# best-effort: exit 0 (never block/raise) if anything is missing.
$ErrorActionPreference = 'SilentlyContinue'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir = Split-Path -Parent $ScriptDir

try {
    $name = (Get-Content (Join-Path $PluginDir 'plugin.json') -Raw | ConvertFrom-Json).name
} catch { $name = $null }
if (-not $name) { $name = Split-Path -Leaf $PluginDir }
if (-not $name) { exit 0 }

$template = Join-Path $PluginDir 'references\bridge-provider.json'
if (-not (Test-Path $template)) { exit 0 }

# Binstub location is a fixed agent-* runtime convention
# (%USERPROFILE%\.local\bin\<name>.cmd -- a .cmd is directly runnable by the
# daemon's subprocess call, a .ps1 is not).
$binstub = Join-Path $env:USERPROFILE ".local\bin\$name.cmd"
if (-not (Test-Path $binstub)) { exit 0 }

if ($env:AGENT_BRIDGE_PROVIDERS_DIR) {
    $dir = $env:AGENT_BRIDGE_PROVIDERS_DIR
} elseif ($env:AGENT_BRIDGE_CONFIG_DIR) {
    $dir = Join-Path $env:AGENT_BRIDGE_CONFIG_DIR 'providers.d'
} else {
    $dir = Join-Path $env:USERPROFILE '.agent-bridge\providers.d'
}
try { New-Item -ItemType Directory -Force -Path $dir | Out-Null } catch { exit 0 }

try {
    $data = Get-Content $template -Raw | ConvertFrom-Json
} catch { exit 0 }
$data | Add-Member -NotePropertyName command -NotePropertyValue @($binstub) -Force

$payload = ($data | ConvertTo-Json -Depth 10)
$out = Join-Path $dir "$name.json"

try {
    $existing = if (Test-Path $out) { [System.IO.File]::ReadAllText($out) } else { $null }
    if ($existing -ne $payload) {
        # WriteAllText -> UTF-8 without BOM (json.loads-safe).
        [System.IO.File]::WriteAllText($out, $payload)
    }
} catch { exit 0 }
exit 0
