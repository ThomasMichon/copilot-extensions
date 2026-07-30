@echo off
set "PYTHONUTF8=1"
rem Resolve the .venv reparse target and launch the slot python directly, never
rem traversing the junction (blocked under RedirectionGuard) -- dotfiles #637.
set "_PY=%USERPROFILE%\.agent-worktrees\.venv\Scripts\python.exe"
for /f "tokens=2 delims=[]" %%i in ('dir /a:l "%USERPROFILE%\.agent-worktrees" 2^>nul ^| findstr /i /c:".venv"') do set "_PY=%%i\Scripts\python.exe"
"%_PY%" -m agent_worktrees %*
exit /b %ERRORLEVEL%
