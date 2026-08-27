@echo off
setlocal
set "PYTHONUTF8=1"
where pwsh >nul 2>&1
if %ERRORLEVEL%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0agent-worktrees.ps1" %*
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0agent-worktrees.ps1" %*
)
exit /b %ERRORLEVEL%
