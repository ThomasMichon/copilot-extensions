@echo off
set "PYTHONUTF8=1"
set "PYTHON=%USERPROFILE%\.agent-worktrees\.venv\Scripts\python.exe"
set "PYTHONPATH=%USERPROFILE%\.agent-worktrees\lib"
"%PYTHON%" -m agent_worktrees %*
exit /b %ERRORLEVEL%
