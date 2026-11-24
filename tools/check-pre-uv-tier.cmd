@echo off
rem Windows entry for tools/check-pre-uv-tier: compiles the pre-uv tier
rem (tools\pre-uv-tier.txt) with a uv-managed Python 3.9. No system Python is used.
setlocal
where uv >nul 2>&1
if errorlevel 1 (
    echo check-pre-uv-tier: uv is required, but 'uv' was not found on PATH. Install uv ^(https://docs.astral.sh/uv/getting-started/installation/^) and retry. 1>&2
    exit /b 1
)
for %%k in (UV_PYTHON UV_PYTHON_PREFERENCE UV_MANAGED_PYTHON UV_NO_MANAGED_PYTHON VIRTUAL_ENV CONDA_PREFIX UV_WORKING_DIR UV_WORKING_DIRECTORY UV_ISOLATED UV_ENV_FILE UV_PROJECT UV_FROZEN UV_LOCKED UV_NO_SYNC) do set "%%k="
uv run --no-project --managed-python --python 3.9 python "%~dp0check_pre_uv_tier.py" %*
exit /b %ERRORLEVEL%