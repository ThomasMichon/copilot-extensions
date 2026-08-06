# Session-start version drift check; first install remains explicit.
$ErrorActionPreference = 'SilentlyContinue'
$InstallDir = Join-Path $env:USERPROFILE '.agent-leases'
$ManifestPath = Join-Path $InstallDir 'deploy-manifest.json'
$Binstub = Join-Path $env:USERPROFILE '.local\bin\agent-leases.cmd'
if (-not (Test-Path $ManifestPath)) { exit 0 }
$Manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
$PluginDir = "" + $Manifest.source.path
if (-not $PluginDir -or -not (Test-Path $PluginDir)) { exit 0 }
$Deployed = "" + $Manifest.source.version
$VersionLine = Select-String -Path (Join-Path $PluginDir 'pyproject.toml') -Pattern '^\s*version\s*=' | Select-Object -First 1
$Current = ($VersionLine.Line -replace '.*=\s*"([^"]+)".*', '$1')
if ((Test-Path $Binstub) -and $Deployed -eq $Current) { exit 0 }
$Installer = Join-Path $PluginDir 'scripts\install.ps1'
if (-not (Test-Path $Installer)) { exit 0 }
$PowerShell = Get-Command pwsh -ErrorAction SilentlyContinue
$Executable = if ($PowerShell) { $PowerShell.Source } else { 'powershell.exe' }
Start-Process -FilePath $Executable -WindowStyle Hidden -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $Installer, 'update'
) | Out-Null
