@echo off
setlocal

set "PYTHONHOME="
where pwsh >nul 2>&1
if %ERRORLEVEL%==0 (
    set "_PSHOST=pwsh"
) else (
    set "_PSHOST=powershell"
)

set "RUNTIME_DIR=%USERPROFILE%\.agent-worktrees"

rem Normal path: delegate to PowerShell wrapper
"%_PSHOST%" -NoProfile -NoLogo -ExecutionPolicy Bypass -File "%RUNTIME_DIR%\bin\launch-session.ps1" %*
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
    "%_PSHOST%" -NoProfile -NoLogo -ExecutionPolicy Bypass -File "%ANCHOR%\tools\setup\setup.ps1" -Recovery %*
    popd
    exit /b %ERRORLEVEL%
)
echo ERROR: Cannot determine anchor path for recovery. >&2
exit /b 1
