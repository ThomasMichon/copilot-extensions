@echo off
rem Windows entry for tools/run-python: runs tools\run-python.ps1 (Windows PowerShell 5.1).
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-python.ps1" %*
exit /b %ERRORLEVEL%
