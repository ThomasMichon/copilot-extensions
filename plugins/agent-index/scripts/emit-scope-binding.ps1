# Emit repository-scoped agent-index guidance without provisioning a runtime.
$ErrorActionPreference = 'SilentlyContinue'

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) { Write-Output '{}'; exit 0 }
$emitter = Join-Path $PSScriptRoot 'emit_scope_binding.py'
& $python.Source -E -X utf8 $emitter --cwd (Get-Location).Path
if ($LASTEXITCODE -ne 0) { Write-Output '{}' }
exit 0
