@echo off
setlocal

set "PYTHONHOME="

rem Runtime resolution
set "RUNTIME_DIR=%USERPROFILE%\.agent-worktrees"

rem Junction-free resolution: the active version is published by a plain-text
rem `current-version` marker -> versions\<ver>\Scripts\python.exe (nothing
rem traverses a reparse point, blocked under RedirectionGuard, dotfiles #637).
rem Fallbacks: newest installed slot, then a legacy `.venv` (junction/real dir).
set "PYTHON="
set "_VER="
if exist "%RUNTIME_DIR%\current-version" set /p _VER=<"%RUNTIME_DIR%\current-version"
if defined _VER if exist "%RUNTIME_DIR%\versions\%_VER%\Scripts\python.exe" set "PYTHON=%RUNTIME_DIR%\versions\%_VER%\Scripts\python.exe"
if not defined PYTHON if exist "%RUNTIME_DIR%\versions" for /f "delims=" %%d in ('dir /b /ad /o-n "%RUNTIME_DIR%\versions" 2^>nul') do if not defined PYTHON if exist "%RUNTIME_DIR%\versions\%%d\Scripts\python.exe" set "PYTHON=%RUNTIME_DIR%\versions\%%d\Scripts\python.exe"
if not defined PYTHON if exist "%RUNTIME_DIR%\.venv" (
    rem Legacy `.venv` (junction target or real dir), resolved without traversing.
    set "PYTHON=%RUNTIME_DIR%\.venv\Scripts\python.exe"
    for /f "tokens=2 delims=[]" %%i in ('dir /a:l "%RUNTIME_DIR%" 2^>nul ^| findstr /i /c:".venv"') do set "PYTHON=%%i\Scripts\python.exe"
)
if not defined PYTHON (
    echo ERROR: Venv not found. Run the installer first. >&2
    exit /b 1
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
