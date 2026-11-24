# Check that the pre-uv tier (tools/pre-uv-tier.txt) compiles with Python 3.9.
# uv installs a managed Python 3.9 when it is missing. No system Python is used.
# Compatible with Windows PowerShell 5.1 and PowerShell 7.
if (-not (Get-Command uv -CommandType Application -ErrorAction SilentlyContinue)) {
    [Console]::Error.WriteLine("check-pre-uv-tier: uv is required, but 'uv' was not found on PATH. Install uv (https://docs.astral.sh/uv/getting-started/installation/) and retry.")
    exit 1
}
& uv run --no-project --managed-python --python 3.9 python (Join-Path $PSScriptRoot 'check_pre_uv_tier.py') @args
exit $LASTEXITCODE
