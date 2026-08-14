@echo off
setlocal
set "PYTHONUTF8=1"
rem Resolve the runtime slot python SOLELY via the junction-free `current-version`
rem marker; the `.venv` junction is retired (marker model, #581/#1085/#1106) so
rem nothing parses/traverses a reparse point (blocked under RedirectionGuard,
rem WinError 448/3, and prone to drift). dotfiles #637 / #1085 / #1106. Fallback:
rem newest installed slot only. SELF-PROVISIONING (#1393): if no slot exists,
rem provision on first use via the lean `install.ps1 provision` then dispatch.
rem Opt out with AGENT_WORKTREES_NO_SELFPROVISION=1.
set "_ROOT=%USERPROFILE%\.agent-worktrees"
call :_resolve
if defined _PY goto :_aw_run
if defined AGENT_WORKTREES_NO_SELFPROVISION (python -m agent_worktrees %* & exit /b %ERRORLEVEL%)
set "_SNAP="
if exist "%_ROOT%\payload-dir" set /p _SNAP=<"%_ROOT%\payload-dir"
set "_INST=%_SNAP%\scripts\install.ps1"
if not exist "%_INST%" goto :_noinst
echo [agent-worktrees] runtime not provisioned -- provisioning on first use ^(~30-120s^). Do not kill.>&2
where pwsh >nul 2>&1
if %ERRORLEVEL%==0 (pwsh -NoProfile -ExecutionPolicy Bypass -File "%_INST%" provision 1>&2) else (powershell -NoProfile -ExecutionPolicy Bypass -File "%_INST%" provision 1>&2)
call :_resolve
if not defined _PY goto :_failprov
:_aw_run
"%_PY%" -m agent_worktrees %*
exit /b %ERRORLEVEL%
:_noinst
echo [agent-worktrees] cannot self-provision: installer not found.>&2
exit /b 127
:_failprov
echo [agent-worktrees] provisioning did not yield a runtime.>&2
exit /b 1
:_resolve
set "_PY="
set "_VER="
if exist "%_ROOT%\current-version" set /p _VER=<"%_ROOT%\current-version"
if defined _VER if exist "%_ROOT%\versions\%_VER%\Scripts\python.exe" set "_PY=%_ROOT%\versions\%_VER%\Scripts\python.exe"
if not defined _PY for /f "delims=" %%d in ('dir /b /ad /o-n "%_ROOT%\versions" 2^>nul') do if not defined _PY if exist "%_ROOT%\versions\%%d\Scripts\python.exe" set "_PY=%_ROOT%\versions\%%d\Scripts\python.exe"
goto :eof
