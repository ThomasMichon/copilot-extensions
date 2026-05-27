---
name: service-lifecycle
description: >
  Service and tool lifecycle — installer patterns, config layering, deploy
  manifests, drift detection, and the repo-is-source-not-runtime principle.
  Use this skill when creating installers, deploying services, managing
  config, or evaluating deployment patterns.
  Trigger phrases include:
  - 'install service'
  - 'deploy service'
  - 'uninstall'
  - 'installer'
  - 'config layering'
  - 'drift detection'
  - 'deploy manifest'
  - 'repo is source'
  - 'never edit deployed'
---

# Service Lifecycle

Standards for deploying, configuring, and managing persistent services and
installed tools from a monorepo. This skill covers the deployment model,
installer interface, config layering, and runtime provenance — the
plumbing that makes services reproducible and maintainable.

For product-level architecture (service shapes, UX patterns, frameworks),
see your repo's own architecture skill if one exists. This skill is about
the **deployment contract**, not the application design.

---

## Service vs Tool

| Aspect | Tool | Service |
|--------|------|---------|
| Lifetime | Invoked, runs, exits | Installed, stays running |
| Managed by | User invocation | OS service manager (Task Scheduler, systemd) |
| Config | CLI flags / env vars | Layered config files with drift detection |
| Install | Just run it | Dedicated installer with install/uninstall lifecycle |

Some systems span both: a vault system may have CLI tools and a background
service. The tools connect to or wrap the service; the service is the
persistent daemon.

---

## Repo Is Source, Not Runtime

The repository can move, be re-cloned, or live at a different path on each
machine. **Never register a systemd service, cron job, or system config
that points directly into the repo tree.** Instead:

- **Source of truth** lives in the repo (`services/`, `tools/`, config)
- **Installed copies** live at system paths (`/opt/`, `/etc/systemd/`,
  `%LOCALAPPDATA%/`, etc.)
- **Installers** copy files from repo to system and configure services.
  Idempotent and safe to re-run.
- **One-off tools** (manually invoked scripts, helpers, diagnostics) *can*
  run directly from the repo — they aren't registered anywhere.

**The litmus test:** if you re-clone the repo to a different path, the
installers should re-deploy cleanly, and every installed service keeps
running regardless of where (or whether) the repo is checked out.

### Never Edit Deployed Code

**Never directly edit files under the install directory** (`/opt/`,
`%LOCALAPPDATA%/`, etc.). The install path is an output, not a workspace.
All changes go through source code in the repo; the installer copies them
to the install path.

**Break-glass exception:** Emergency repairs to deployed code are
acceptable when a system is broken and you need to unstick it before a
proper deploy is possible. When this happens:

1. Get explicit operator approval
2. Capture the exact diff of what you changed
3. Immediately backport the fix to the source repo
4. Redeploy from source as soon as the system is stable

Even then, follow up with a proper deploy. Emergency edits that aren't
backported become invisible drift.

### Deploy From the Target Machine

Service deployments must be executed **on the machine that hosts the
service**, using its local repo clone:

1. **Commit and push** from the development machine
2. **Pull the latest code** on the target
3. **Run the installer** from the target machine's local repo clone

**Do NOT `scp` files directly to target machines** as a deployment
shortcut. This bypasses the installer's validation, manifest tracking,
drift detection, and config layering.

---

## Installer Interface

Every service implements a standardized installer with consistent actions
and flags across PowerShell and Bash.

### Actions

| Action | What it does |
|--------|-------------|
| `install` | Full deploy: copy code, merge+deploy config, register service, start |
| `uninstall` | Stop service, remove registration, delete installed files |
| `start` | Start the service |
| `stop` | Stop the service |
| `status` | Report: installed? running? config drifted? version? |
| `update-config` | Drift check + config sync only (no code changes) |
| `update` | Update code from repo + drift check (lighter than full reinstall) |

### Flags

| Flag | Effect |
|------|--------|
| `--remove-config` | On uninstall: also delete runtime config |
| `--remove-data` | On uninstall: also delete internal + cache data |
| `--purge` | On uninstall: remove everything (config + data) |
| `--force` | Skip drift confirmation — overwrite runtime config |

### Installer Skeleton (PowerShell)

```powershell
param(
    [Parameter(Position=0)]
    [ValidateSet('install','uninstall','start','stop','status','update-config','update')]
    [string]$Action = 'status',
    [switch]$RemoveConfig,
    [switch]$Force
)

switch ($Action) {
    'install'       { Install-Service }
    'uninstall'     { Uninstall-Service -RemoveConfig:$RemoveConfig }
    'start'         { Start-Service }
    'stop'          { Stop-Service }
    'status'        { Get-ServiceStatus }
    'update-config' { Update-ServiceConfig -Force:$Force }
    'update'        { Update-Service -Force:$Force }
}
```

### Installer Skeleton (Bash)

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/my-service"
SERVICE_USER="${USER}"   # real user, not root

case "${1:-status}" in
    install)       do_install ;;
    uninstall)     do_uninstall "$@" ;;
    start)         do_start ;;
    stop)          do_stop ;;
    status)        do_status ;;
    update-config) do_update_config "$@" ;;
    update)        do_update "$@" ;;
esac
```

---

## Config Layer Model

All configs use **YAML** format. Three layers, merged at deploy time:

### 1. Default Config (`config/default.yaml`)

- Lives in the repo alongside the service source
- Environment-neutral — works on any machine without modification
- **Never contains secrets, personal information, or environment paths**
- Defines all config keys with safe, generic defaults

### 2. Preferred Config (`config/{machine}.yaml`)

- Per-machine overrides — only keys that differ from default
- May contain machine-specific paths, ports, tuning parameters
- **Still no raw secrets** — use vault references if credentials are needed

### 3. Runtime Config (at install location)

- The actual config file the running service reads
- Created by deep-merging default + preferred at install/deploy time
- May be edited directly on the machine (triggering drift)

### Config Merge Rule

```
runtime_config = deep_merge(default.yaml, {machine}.yaml)
```

Deep merge: machine-specific keys override defaults at any depth. Lists
are replaced wholesale (not appended). Missing machine file = just use
defaults.

### Drift Detection

On `install` or `update-config` actions:

1. Build "preferred" config = deep_merge(default.yaml, {machine}.yaml)
2. Read runtime config from install location
3. Compare (normalizing key order, ignoring comments)
4. **If identical** → proceed silently
5. **If runtime has changes not in preferred** → drift detected:
   - Show a unified diff (preferred vs runtime)
   - Ask the user:
     - **Pull**: copy runtime config back into `config/{machine}.yaml`
       in the repo (preserves on-machine edits)
     - **Push**: overwrite runtime with preferred (discards on-machine edits)
     - **Skip**: leave config as-is, continue with other actions
6. **If runtime doesn't exist** → first deploy, push without asking

---

## Deploy Manifest

Each deployment writes a `deploy-manifest.json` to the install directory
as the **final step** of a successful install or update. This provides
runtime provenance.

```json
{
  "schema_version": 1,
  "service": "my-service",
  "environment": "my-machine-windows",
  "commit": "abc1234...",
  "branch": "main",
  "dirty": false,
  "dirty_files": [],
  "deployed_at": "2026-04-13T07:25:00Z",
  "deployed_by": "my-machine",
  "source_paths": ["services/my-service/"],
  "installer_path": "services/my-service/install.ps1"
}
```

### Staleness Check

```
git log <deployed-commit>..HEAD -- <source_paths...>
```

If empty → deployment is current for this service's source paths, even if
HEAD has advanced for unrelated code.

Always write the manifest **after** all deployment steps succeed. Never
write it before the service is fully deployed and running.

---

## Machine State Reproducibility

Every persistent change to a machine — system settings, scheduled tasks,
firewall rules, service registrations, config files outside the repo —
must be **reproducible from the repo alone**.

Requirements:

1. **Capture in a restore/setup script.** New modifications go into the
   appropriate section of a machine restore script, or into a dedicated
   installer if the scope warrants it.
2. **Idempotent by default.** Restore and setup scripts check state before
   acting. Running them twice produces the same result as running once.
3. **Revert when practical.** For changes that are hard to undo manually,
   prefer scripts that can both *install* and *uninstall*.

**The litmus test:** if the machine's OS is wiped and reinstalled, can you
get back to the current state by running restore scripts + documented
manual steps?

---

## Python Dependency Management

For Python-based services, use **`uv`** for all dependency operations —
never bare `pip` or `pip install`:

```bash
# Dev/test (repo root)
uv sync --extra dev

# Service venv (in installers)
uv venv "$INSTALL_DIR/.venv"
uv pip install --python "$INSTALL_DIR/.venv/bin/python" -r requirements.txt
```

### The Venv Trap

**Never run an installer from the venv it's trying to update.** This is a
recurring failure mode: the installer runs from the service's deployed
`.venv`, then tries to recreate or overwrite that same venv — and fails
because the Python interpreter is locked by the running process.

**The fix:** Installers must use the **system Python** or a **separate dev
venv** to bootstrap the target service venv — never the target venv
itself.

```bash
# ✅ Correct — create venv with system uv, not from within the target venv
uv venv "$INSTALL_DIR/.venv" --python python3
uv pip install --python "$INSTALL_DIR/.venv/bin/python" -r requirements.txt

# ❌ Wrong — running from the venv we're about to overwrite
source "$INSTALL_DIR/.venv/bin/activate"
uv pip install -r requirements.txt   # locks files we need to replace
```

This applies equally to Windows installers — don't activate or invoke the
target service's venv to install into itself.

---

## Elevation Patterns (Summary)

### Linux / WSL

Use `sudo -A` for individual privileged commands. Never run the entire
installer under `sudo` — it causes `$USER` and environment misresolution.

### Windows

Decision hierarchy:
1. **Don't elevate** — most operations don't need admin
2. **Gate-and-bail** — script checks for elevation, exits with clear error
   if missing. User re-runs from elevated terminal.
3. **Elevate-and-capture** — for ad-hoc elevated commands, write work to a
   temp script, launch with `Start-Process -Verb RunAs`, capture output
   via a pre-resolved file path.
4. **Scheduled task** — for recurring or boot-time elevation needs

See your repo's elevation skill (if available) for platform-specific
details and pitfalls.
