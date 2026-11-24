@echo off
rem Windows entry for tools/run-tests: runs tools\run-plugin-tests.py through
rem tools\run-python.cmd, so it uses the uv-managed Python pinned in .python-version.
call "%~dp0run-python.cmd" "%~dp0run-plugin-tests.py" %*
exit /b %ERRORLEVEL%