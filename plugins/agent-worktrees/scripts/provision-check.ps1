# Self-provisioning check -- runs on session start via hooks.json (dotfiles #693).
#
# "Enabling a runtime plugin" should be the whole install: on any session start,
# bring each enabled plugin's runtime into existence and keep it version-matched
# to its payload -- not only when the session was launched through the
# agent-worktrees worktree launcher. This shim runs the same version-keyed
# `reconcile-plugins` logic universally.
#
# Non-blocking by construction: the foreground does only a cheap read-only
# `--peek` (no cache side effects). A cold first provision builds a venv (slow),
# so when there is work we spawn the `--apply` worker DETACHED and return
# immediately -- session start never waits on a runtime build.
#
# Compatible with PowerShell 5.1+ and pwsh 7+.

$ErrorActionPreference = 'SilentlyContinue'

# Opt-out: honor the launcher's reconcile switch plus a provisioning-scoped one.
if ($env:WORKTREE_NO_RECONCILE -eq '1' -or $env:WORKTREE_NO_PROVISION -eq '1') { exit 0 }

$InstallDir = Join-Path $env:USERPROFILE '.agent-worktrees'
$_r = Join-Path $InstallDir 'bin\resolve-runtime.ps1'
$VenvPython = if (Test-Path -LiteralPath $_r) { . $_r; $AwPy } else { $null }
if (-not $VenvPython) { exit 0 }

$env:PYTHONPATH = ''  # package is installed in the venv (no lib/ shadow)

# Read-only preview: does anything need provisioning? (No throttle side effects.)
$peek = ''
try { $peek = & $VenvPython -m agent_worktrees reconcile-plugins --peek 2>$null } catch { exit 0 }
if (-not $peek) { exit 0 }

try { $plan = $peek | ConvertFrom-Json } catch { exit 0 }
if ($plan.action -ne 'reconcile') { exit 0 }

$services = ($plan.updates | ForEach-Object { $_.service } | Select-Object -Unique) -join ', '
Write-Host "[agent-worktrees] Provisioning runtime(s) in background: $services" -ForegroundColor DarkGray

# Background apply: execute the plan detached so the slow build never blocks the
# session. Run from HOME: Copilot can invoke hooks with the installed plugin
# payload as cwd, and a long-lived Windows child inheriting that directory blocks
# `copilot plugin update` from replacing the payload.
$logDir = Join-Path $InstallDir 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss')
$log = Join-Path $logDir "provision-$stamp.log"

try {
    Start-Process -FilePath $VenvPython `
        -ArgumentList @('-m', 'agent_worktrees', 'reconcile-plugins', '--apply') `
        -WorkingDirectory $HOME `
        -WindowStyle Hidden `
        -RedirectStandardOutput $log `
        -RedirectStandardError "$log.err" | Out-Null
} catch { }

exit 0
