@echo off
set "PYTHONUTF8=1"
rem Resolve the runtime slot python SOLELY via the junction-free `current-version`
rem marker; the `.venv` junction is retired (marker model, #581/#1085/#1106) so
rem nothing parses/traverses a reparse point (blocked under RedirectionGuard,
rem WinError 448/3, and prone to drift). dotfiles #637 / #1085 / #1106. Fallback:
rem newest installed slot only.
set "_ROOT=%USERPROFILE%\.agent-worktrees"
set "_VER="
if exist "%_ROOT%\current-version" set /p _VER=<"%_ROOT%\current-version"
set "_PY=%_ROOT%\versions\%_VER%\Scripts\python.exe"
if exist "%_PY%" goto :_aw_run
set "_PY="
for /f "delims=" %%d in ('dir /b /ad /o-n "%_ROOT%\versions" 2^>nul') do if not defined _PY if exist "%_ROOT%\versions\%%d\Scripts\python.exe" set "_PY=%_ROOT%\versions\%%d\Scripts\python.exe"
:_aw_run
"%_PY%" -m agent_worktrees %*
exit /b %ERRORLEVEL%
