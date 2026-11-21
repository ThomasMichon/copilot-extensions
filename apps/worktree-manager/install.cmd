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
if exist "%SLOT%\uv.lock" goto source_lock_ready
uv lock --managed-python --project "%SLOT%"
if errorlevel 1 exit /b %errorlevel%
:source_lock_ready
uv run --locked --no-dev --no-editable --link-mode hardlink --managed-python --project "%SLOT%" python -I -X utf8 -B "%SLOT%\install.py" %*
exit /b %errorlevel%
