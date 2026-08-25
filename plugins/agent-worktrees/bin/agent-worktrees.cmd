@echo off
setlocal
set "PYTHONUTF8=1"
set "_ROOT=%USERPROFILE%\.agent-worktrees"
call :_resolve
if defined _PY (
  "%_PY%" -m agent_worktrees %*
  exit /b %ERRORLEVEL%
)
where pwsh >nul 2>&1
if %ERRORLEVEL%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0agent-worktrees.ps1" %*
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0agent-worktrees.ps1" %*
)
exit /b %ERRORLEVEL%
:_resolve
set "_PY="
set "_VER="
if exist "%_ROOT%\current-version" set /p _VER=<"%_ROOT%\current-version"
if defined _VER if exist "%_ROOT%\versions\%_VER%\Scripts\python.exe" set "_PY=%_ROOT%\versions\%_VER%\Scripts\python.exe"
if defined _PY goto :eof
set "_VER="
if exist "%_ROOT%\last-known-good" set /p _VER=<"%_ROOT%\last-known-good"
if defined _VER if exist "%_ROOT%\versions\%_VER%\Scripts\python.exe" set "_PY=%_ROOT%\versions\%_VER%\Scripts\python.exe"
if defined _PY goto :eof
for /f "delims=" %%f in ('dir /b /s /a-d /o-d "%_ROOT%\versions\*\.install-complete.json" 2^>nul') do if not defined _PY if exist "%%~dpfScripts\python.exe" set "_PY=%%~dpfScripts\python.exe"
goto :eof
