#Requires -Version 7.0
# agent-ssh :: emit-profile (Windows wrapper)
# Delegates to the installed binstub, which resolves the interpreter the uniform
# marker-only way (uniform-runtime-resolution, #765) and self-provisions on first
# use. Falls back to a source-tree python only for a raw checkout with no binstub.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$binstub = Join-Path $env:USERPROFILE '.local\bin\agent-ssh.ps1'
if (Test-Path -LiteralPath $binstub) { & $binstub emit-profile @args; exit $LASTEXITCODE }
$src = (Resolve-Path (Join-Path $here '..\src')).Path
if ($env:PYTHONPATH) { $env:PYTHONPATH = "$src;$env:PYTHONPATH" } else { $env:PYTHONPATH = $src }
python -m agent_ssh emit-profile @args  # runtime-resolution: allow bootstrap: raw source checkout, no installed binstub
exit $LASTEXITCODE
