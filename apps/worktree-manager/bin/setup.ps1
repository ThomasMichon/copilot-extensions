$ErrorActionPreference = 'Stop'
& (Join-Path (Split-Path -Parent $PSScriptRoot) 'install.ps1') @args
exit $LASTEXITCODE
