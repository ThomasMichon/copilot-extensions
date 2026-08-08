@echo off
set "PYTHONUTF8=1"
rem Resolve the runtime slot python via the junction-free `current-version`
rem marker; the `.venv` junction is retired (marker model, #581/#285) so nothing
rem traverses a reparse point (blocked under RedirectionGuard) -- dotfiles #637
rem (never traverse) + dotfiles #1085 (global binstub marker migration). Legacy
rem `.venv` reparse-target fallback covers a host still mid-migration.
set "_ROOT=%USERPROFILE%\.agent-worktrees"
set "_VER="
if exist "%_ROOT%\current-version" set /p _VER=<"%_ROOT%\current-version"
set "_PY=%_ROOT%\versions\%_VER%\Scripts\python.exe"
if exist "%_PY%" goto :_aw_run
set "_PY=%_ROOT%\.venv\Scripts\python.exe"
for /f "tokens=2 delims=[]" %%i in ('dir /a:l "%_ROOT%" 2^>nul ^| findstr /i /c:".venv"') do set "_PY=%%i\Scripts\python.exe"
:_aw_run
"%_PY%" -m agent_worktrees %*
exit /b %ERRORLEVEL%
