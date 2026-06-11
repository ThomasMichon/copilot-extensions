@echo off
set "PYTHONUTF8=1"
"%USERPROFILE%\.agent-worktrees\.venv\Scripts\agent-worktrees.exe" %*
exit /b %ERRORLEVEL%
