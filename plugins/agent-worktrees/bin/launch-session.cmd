@echo off
setlocal

set "PYTHONHOME="

rem Runtime resolution
set "RUNTIME_DIR=%USERPROFILE%\.agent-worktrees"

rem Junction-free resolution (marker-only): the active version is published by a
rem plain-text `current-version` marker -> versions\<ver>\Scripts\python.exe
rem (nothing traverses a reparse point, blocked under RedirectionGuard, dotfiles
rem #637, and prone to drift). Fallback: newest installed slot only -- the
rem `.venv` link is retired (#1106).
set "PYTHON="
set "_VER="
if exist "%RUNTIME_DIR%\current-version" set /p _VER=<"%RUNTIME_DIR%\current-version"
if defined _VER if exist "%RUNTIME_DIR%\versions\%_VER%\.install-complete.json" if exist "%RUNTIME_DIR%\versions\%_VER%\Scripts\python.exe" set "PYTHON=%RUNTIME_DIR%\versions\%_VER%\Scripts\python.exe"
if not defined PYTHON (
    pwsh.exe -NoProfile -NoLogo -File "%RUNTIME_DIR%\bin\launch-session.ps1" %*
    exit /b %ERRORLEVEL%
)

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
