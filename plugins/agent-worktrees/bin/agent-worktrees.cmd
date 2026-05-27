@echo off
setlocal

if not defined WORKTREE_PROJECT (
    echo ERROR: WORKTREE_PROJECT is not set. Use the project-specific binstub or set WORKTREE_PROJECT. >&2
    exit /b 1
)

rem Resolve runtime
set "NEW_RUNTIME=%USERPROFILE%\.agent-worktrees"

if exist "%NEW_RUNTIME%\.venv\Scripts\python.exe" (
    set "PYTHON=%NEW_RUNTIME%\.venv\Scripts\python.exe"
    set "PYTHONPATH=%NEW_RUNTIME%\lib"
) else (
    echo ERROR: Venv not found. Run the installer first. >&2
    exit /b 1
)

"%PYTHON%" -m agent_worktrees %*
exit /b %ERRORLEVEL%
