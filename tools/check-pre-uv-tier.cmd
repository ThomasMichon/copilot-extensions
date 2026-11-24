@echo off
rem Windows entry for tools/check-pre-uv-tier: runs tools\check-pre-uv-tier.ps1 (Windows PowerShell 5.1).
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0check-pre-uv-tier.ps1" %*
exit /b %ERRORLEVEL%
