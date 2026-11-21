@echo off
setlocal EnableExtensions DisableDelayedExpansion
for %%I in ("%~dp0.") do set "SLOT=%%~fI"
set "UV_PROJECT="
set "UV_PROJECT_ENVIRONMENT="
set "UV_NO_SYNC="
set "UV_NO_PROJECT="
set "UV_WORKING_DIR="
set "UV_FROZEN="
set "PYTHONPATH="
set "PYTHONHOME="
set "VIRTUAL_ENV="
set "UV_PYTHON="
set "UV_PYTHON_PREFERENCE="
set "UV_MANAGED_PYTHON="
set "UV_NO_MANAGED_PYTHON="
if not defined UV_CACHE_DIR for %%I in ("%SLOT%\..\..") do set "UV_CACHE_DIR=%%~fI\cache\uv"
for %%F in (uv.lock .venv\pyvenv.cfg .venv\Scripts\python.exe) do (
    if not exist "%SLOT%\%%F" (
        >&2 echo Application is not staged; run the installer with --stage: "%SLOT%\install.cmd"
        exit /b 1
    )
)
uv run --locked --no-dev --no-editable --link-mode hardlink --managed-python --no-sync --no-python-downloads --project "%SLOT%" python -I -X utf8 -B "%SLOT%\worktree-manager.py" %*
exit /b %errorlevel%
