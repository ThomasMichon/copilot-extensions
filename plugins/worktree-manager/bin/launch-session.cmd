@echo off
setlocal

set "PYTHONHOME="

rem Dual-layout resolution: prefer ~/.worktree-manager/, fall back to legacy
set "NEW_RUNTIME=%USERPROFILE%\.worktree-manager"
set "LEGACY_RUNTIME=%USERPROFILE%\.aperture-labs"

if exist "%NEW_RUNTIME%\.venv\Scripts\python.exe" (
    set "RUNTIME_DIR=%NEW_RUNTIME%"
) else if exist "%LEGACY_RUNTIME%\.venv\Scripts\python.exe" (
    set "RUNTIME_DIR=%LEGACY_RUNTIME%"
) else (
    echo ERROR: Venv not found. Run the installer first. >&2
    exit /b 1
)

set "PYTHON=%RUNTIME_DIR%\.venv\Scripts\python.exe"

rem Recovery escape hatch: if Python is broken, fall back to native
if /i "%~1"=="recovery" if not exist "%PYTHON%" goto :native_recovery
if /i "%~1"=="-Recovery" if not exist "%PYTHON%" goto :native_recovery
if /i "%~1"=="--recovery" if not exist "%PYTHON%" goto :native_recovery

rem Normal path: delegate to PowerShell wrapper
pwsh.exe -NoProfile -NoLogo -File "%RUNTIME_DIR%\bin\launch-session.ps1" %*
exit /b %ERRORLEVEL%

:native_recovery
rem Minimal fallback: launch Copilot directly in the anchor repo
rem Requires WORKTREE_PROJECT to be set
if not defined WORKTREE_PROJECT (
    echo ERROR: WORKTREE_PROJECT is not set. Set it or use the project binstub. >&2
    exit /b 1
)
set "CONFIG=%USERPROFILE%\.%WORKTREE_PROJECT%\config.yaml"
if not exist "%CONFIG%" (
    echo ERROR: Cannot find config for recovery. >&2
    exit /b 1
)
for /f "tokens=2 delims= " %%A in ('findstr /r "^    anchor:" "%CONFIG%"') do set "ANCHOR=%%A"
if defined ANCHOR (
    pushd "%ANCHOR%"
    pwsh.exe -NoProfile -NoLogo -File "%ANCHOR%\tools\setup\setup.ps1" -Recovery %*
    popd
    exit /b %ERRORLEVEL%
)
echo ERROR: Cannot determine anchor path for recovery. >&2
exit /b 1
