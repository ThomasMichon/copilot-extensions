# emit-mesh-pointer -- agent-ssh sessionStart hook.
#
# Emits a SUCCINCT pointer as {"additionalContext": "..."} telling the agent that
# the calling repo declares an SSH machine mesh (machines.yaml) and to run
# the payload command catalog's `mesh-status` action for the full per-host table
# -- rather than dumping the whole mesh into every session. cwd-gated: fires only
# when the current git repo has a machines.yaml at its root; otherwise emits {}
# so a globally-loaded plugin never leaks one repo's mesh into an unrelated repo
# (repo-specific by the gate).
#
# Presence-gated only -- it does NOT parse machines.yaml (that keeps sessionStart
# fast and robust; all rendering is deferred to the payload-local command,
# invoked on demand). PowerShell 5.1+ / 7+.

$ErrorActionPreference = 'SilentlyContinue'

function Emit-Empty { Write-Output '{}'; exit 0 }

# Resolve the current repo root (cwd-gated). No git repo / no machines.yaml -> {}.
$root = (& git rev-parse --show-toplevel 2>$null | Select-Object -First 1)
if (-not $root) { Emit-Empty }
if (-not (Test-Path -LiteralPath (Join-Path $root 'machines.yaml'))) { Emit-Empty }

$md = @'
## SSH machine mesh available for this repo

This repo declares an SSH machine mesh in ``machines.yaml``. For the per-host
**role + reachability + aliases**, append **``mesh-status``** to the exact
``argv`` in the agent-ssh session command catalog (``--summary`` for one line,
``--json`` for structured). Reach a host interactively with ``ssh <alias>``
(the aliases are listed by that action).
Reachability is **dtssh** -- live only while the target is powered on and logged
in; ``ssh.ready`` is the operator's declared state, so append ``verify
<alias>`` to the same catalog ``argv`` to probe a host live.
'@

Write-Output (@{ additionalContext = $md } | ConvertTo-Json -Compress -Depth 3)
exit 0
