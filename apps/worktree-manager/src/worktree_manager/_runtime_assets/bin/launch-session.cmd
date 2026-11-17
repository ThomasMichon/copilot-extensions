@echo off
setlocal

set "PYTHONHOME="
set "_PSHOST="
for /f "delims=" %%I in ('"%SystemRoot%\System32\where.exe" pwsh 2^>nul') do if not defined _PSHOST set "_PSHOST=%%I"
if not defined _PSHOST set "_PSHOST=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

rem Normal path: delegate to sibling PowerShell wrapper
"%_PSHOST%" -NoProfile -NoLogo -ExecutionPolicy Bypass -File "%~dp0launch-session.ps1" %*
exit /b %ERRORLEVEL%
