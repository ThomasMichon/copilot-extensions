$env:PYTHONUTF8 = '1'
& "$env:USERPROFILE\.agent-worktrees\.venv\Scripts\python.exe" -m agent_worktrees @args
exit $LASTEXITCODE
