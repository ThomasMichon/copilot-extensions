@echo off
rem Windows entry for tools/run-tests: runs tools\run-tests.ps1 (Windows PowerShell 5.1).
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-tests.ps1" %*
exit /b %ERRORLEVEL%
