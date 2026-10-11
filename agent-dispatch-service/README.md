# agent-dispatch-service

The **standalone, Copilot-independent distribution** of the `agent-dispatch`
coordinator daemon -- installable and runnable with no Copilot CLI and no
marketplace registration, the same out-of-plugin shape `worktree-manager`
already proves for `agent-worktrees`.

This package does not reimplement the coordinator. All of its functionality
(CLI, `serve` command, versioned-venv + binstub + systemd-unit/Scheduled-Task
installer, MCP tools) already lives in the `agent_dispatch` package the
Copilot plugin ships; `agent-dispatch-service` is a thin dependency wrapper
around it plus the two bootstrap scripts below, so a bare machine with
neither Copilot nor `git`/`uv` preinstalled can still get a running
coordinator.

## Install and run

```bash
# Linux / WSL / macOS:
curl -fsSL https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/agent-dispatch-service/bootstrap.sh | bash -s -- install
```

```powershell
# Windows:
iex (irm https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/agent-dispatch-service/bootstrap.ps1)
```

Both scripts fetch this repository (git clone/fetch when available, else a
GitHub tarball so a bare machine without `git` still bootstraps), then exec
straight into the existing `plugins/agent-dispatch/scripts/install.{sh,ps1}`
-- the identical installer every plugin-delivered host already runs. Every
installer action and flag passes straight through:

```bash
bash bootstrap.sh install        # venv + binstub + coordinator service
bash bootstrap.sh update         # idempotent refresh (downgrade-guarded)
bash bootstrap.sh status
bash bootstrap.sh start | stop
bash bootstrap.sh uninstall --purge
bash bootstrap.sh install --no-service --install-dir /opt/agent-dispatch
```

The installed runtime is byte-for-byte what a plugin-delivered host gets:
the same `~/.agent-dispatch` venv/DB/config layout, the same
`AGENT_DISPATCH_*` environment contract (`AGENT_DISPATCH_HOST`/`PORT`/`DB`/
`TOKEN`/`CONTROL_TOKEN`/`ENFORCE_REGISTERED_REPOS`), and the same systemd
user unit / Windows Scheduled Task. A host can move between plugin-delivered
and standalone installation without any data migration.

Relocate the staging root with `$AGENT_DISPATCH_SERVICE_ROOT` /
`$env:AGENT_DISPATCH_SERVICE_ROOT`, and the git source with
`$AGENT_DISPATCH_SERVICE_REPO` / `$AGENT_DISPATCH_SERVICE_REF` (and their
`$env:` equivalents on Windows) to track a fork or canary branch.

## Container image

A reusable container image is planned (Phase 2 of
[the owning effort](../efforts/active/agent-dispatch-standalone-container/README.md))
but not yet part of this package.

## Scope

See [`efforts/active/agent-dispatch-standalone-container/README.md`](../efforts/active/agent-dispatch-standalone-container/README.md)
for the full plan, validation requirements, and relationship to the
similarly-shaped `agent-index-standalone-service` and
`machine-fleet-routing-foundation` efforts.
