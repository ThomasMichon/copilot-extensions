@echo off
set "PYTHONUTF8=1"
"%USERPROFILE%\.agent-worktrees\.venv\Scripts\python.exe" -m agent_worktrees %*
exit /b %ERRORLEVEL%
