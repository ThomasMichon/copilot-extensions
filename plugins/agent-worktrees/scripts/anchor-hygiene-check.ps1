# Anchor hygiene check -- runs on session start via hooks.json
# Warns if the anchor repo has uncommitted changes or stash entries.
# Always exits 0 (warning only, never blocks session start).

$ErrorActionPreference = 'Stop'

$venvPython = "$env:USERPROFILE\.agent-worktrees\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { exit 0 }

$env:PYTHONPATH = ''  # package is installed in the venv (no lib/ shadow)
try {
    & $venvPython -m agent_worktrees anchor-check --quiet 2>$null
} catch {
    # Never block session start
}

exit 0
