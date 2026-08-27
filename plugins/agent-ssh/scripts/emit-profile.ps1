#Requires -Version 7.0
# agent-ssh :: emit-profile (Windows wrapper)
# Delegates to this payload's generated command, which resolves the interpreter
# the uniform marker-only way and self-provisions on first use without selecting
# a same-named command through global PATH. Falls back to a source-tree python
# only for a raw checkout with no payload command.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$payloadCommand = Join-Path (Split-Path -Parent $here) 'bin\agent-ssh.ps1'
if (Test-Path -LiteralPath $payloadCommand) { & $payloadCommand emit-profile @args; exit $LASTEXITCODE }
$src = (Resolve-Path (Join-Path $here '..\src')).Path
if ($env:PYTHONPATH) { $env:PYTHONPATH = "$src;$env:PYTHONPATH" } else { $env:PYTHONPATH = $src }
python -m agent_ssh emit-profile @args  # runtime-resolution: allow bootstrap: raw source checkout, no installed binstub
exit $LASTEXITCODE
