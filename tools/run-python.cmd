@echo off
rem Windows entry for tools/run-python. Runs Python through `uv run` with the
rem uv-managed CPython pinned in the repository root's .python-version; uv
rem installs it when it is missing. A system Python is never used.
rem Arguments go to uv unchanged, so cmd.exe quoting reaches Python intact.
rem Leading --with requirements are prepared by tools\run_python_with.py under
rem the seven-day seasoning policy.
setlocal
set "pin="
if exist "%~dp0..\.python-version" (
    for /f "usebackq eol=# tokens=1" %%v in ("%~dp0..\.python-version") do if not defined pin set "pin=%%v"
)
if not defined pin (
    echo run-python: no Python version pin found in '%~dp0..\.python-version'. 1>&2
    exit /b 1
)
where uv >nul 2>&1
if errorlevel 1 (
    echo run-python: uv is required to run Python in this repository, but 'uv' was not found on PATH. Install uv ^(https://docs.astral.sh/uv/getting-started/installation/^) and retry. A system Python is never used as a fallback. 1>&2
    exit /b 1
)
rem Inherited selectors must not redirect the interpreter or working directory.
for %%k in (UV_PYTHON UV_PYTHON_PREFERENCE UV_MANAGED_PYTHON UV_NO_MANAGED_PYTHON VIRTUAL_ENV CONDA_PREFIX UV_WORKING_DIR UV_WORKING_DIRECTORY UV_ISOLATED UV_ENV_FILE UV_PROJECT UV_FROZEN UV_LOCKED UV_NO_SYNC UV_CONFIG_FILE) do set "%%k="
uv run --no-project --managed-python --python %pin% python "%~dp0run_python_with.py" %*
exit /b %ERRORLEVEL%