#Requires -Version 7.0
# agent-ssh :: emit-profile (Windows wrapper)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = (Resolve-Path (Join-Path $here '..\src')).Path
if ($env:PYTHONPATH) { $env:PYTHONPATH = "$src;$env:PYTHONPATH" } else { $env:PYTHONPATH = $src }
python -m agent_ssh emit-profile @args
exit $LASTEXITCODE
