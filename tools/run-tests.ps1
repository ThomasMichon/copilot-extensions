# Run the turn-key plugin test runner (tools/run-plugin-tests.py) through
# tools/run-python.ps1, so it uses the uv-managed Python pinned in .python-version.
# Compatible with Windows PowerShell 5.1 and PowerShell 7.
#
# Usage: tools/run-tests.ps1 <plugin>... | --changed | --all [runner options]
& (Join-Path $PSScriptRoot 'run-python.ps1') (Join-Path $PSScriptRoot 'run-plugin-tests.py') @args
exit $LASTEXITCODE
