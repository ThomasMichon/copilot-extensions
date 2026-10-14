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

This package's own `pyproject.toml` declares a plain versioned `agent-dispatch`
dependency with no `[tool.uv.sources]` escape (a local dev/CI install supplies
`plugins/agent-dispatch` as an explicit extra source instead -- see
`tools/run-standalone-tests.py`'s `prepare()`), so release materialization
(`tools/materialize_main.py`) has nothing to vendor for this package today.
`tools/materialize_main.py` and its trusted-updater parity copy in
`worktree-manager` still carry a narrow, well-tested exception recognizing
exactly this standalone-consumer/`agent-dispatch` relationship if a future
revision reintroduces an editable pointer at `../plugins/agent-dispatch` --
canonical source identity, symlink and destination safeguards remain required
either way; it does not permit arbitrary plugin-to-plugin copying.

## Install and run

The stable, public install command (never changes across future repo
reorganizations -- see
[`docs/patterns/standalone-service-layout.md`](../../docs/patterns/standalone-service-layout.md)'s
"public-install-surface indirection"):

```bash
# Linux / WSL / macOS:
curl -fsSL https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/install/dispatch/bootstrap.sh | bash -s -- install
```

```powershell
# Windows:
iex (irm https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/install/dispatch/bootstrap.ps1)
```

The `install/dispatch/bootstrap.{sh,ps1}` proxy does nothing but resolve and
exec this package's own `bootstrap.{sh,ps1}` (below), which fetches this
repository (git clone/fetch when available, else a
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

`Dockerfile` builds a container image that runs the coordinator directly
(`python -m agent_dispatch serve` as PID 1) -- no systemd unit, no Scheduled
Task, no self-update loop (disabled; an image rebuild+redeploy is how this
service updates in a container). Build from the **repository root**, not
this directory -- the image needs `plugins/agent-dispatch` plus the handful
of shared `libs/*` it depends on, in their real relative positions:

```bash
docker build -f services/dispatch/Dockerfile -t agent-dispatch-service .
```

Run it with a durable volume for its task database and rendezvous file:

```bash
docker run -d --name agent-dispatch \
  -v agent-dispatch-data:/home/dispatch/.agent-dispatch \
  agent-dispatch-service
```

By default the coordinator binds **loopback-only** inside the container
(`AGENT_DISPATCH_HOST` is not overridden, so it keeps the coordinator's own
safe default) -- reachable only from inside that exact container, which is
enough to `docker exec` the CLI against it but not for a sibling container.
To reach it from another container or a published port, set **both**
`AGENT_DISPATCH_HOST=0.0.0.0` and `AGENT_DISPATCH_TOKEN=<bearer-token>`:
the coordinator itself refuses an unauthenticated `0.0.0.0` bind ("the
task-control API must not be exposed on the LAN unauthenticated"), and this
image does not work around that guard. Per this effort's own deployment
model (see the owning effort doc), a real fleet deployment reaches the
coordinator only through the Gateway or a private container network, never
a directly published public port:

```bash
docker run -d --name agent-dispatch \
  -e AGENT_DISPATCH_HOST=0.0.0.0 -e AGENT_DISPATCH_TOKEN=<token> \
  -e AGENT_DISPATCH_PORT=<port> \
  -v agent-dispatch-data:/home/dispatch/.agent-dispatch \
  agent-dispatch-service
```

`AGENT_DISPATCH_PORT` is intentionally left unset by default -- the
coordinator binds an OS-assigned dynamic port (the same Stage C behavior
every plugin-delivered/standalone host already uses), advertised via its
own rendezvous file. Pin a stable port explicitly when a deployment needs
one to publish or for another container to dial by a fixed address.

Validated locally: a clean build, a real authenticated `health`/`create`
round-trip via the `agent-dispatch` CLI (`docker exec`), and a `docker
restart` with the previously created task still present afterward (the
task database under the named volume survives a container recreate,
mirroring the restart-persistence validation this effort's parent
`harness-gateway-fleet` dotfiles-private effort already applied to its own
Gateway container).

If your network routes package installs through an approved internal feed
instead of public PyPI (e.g. company policy on a managed developer
machine), override the index at build time without changing the
committed default:

```bash
docker build -f services/dispatch/Dockerfile \
  --build-arg PYTHON_INDEX_URL=<your-approved-feed-index-url> \
  -t agent-dispatch-service .
```


## Scope

See [`efforts/active/agent-dispatch-standalone-container/README.md`](../../efforts/active/agent-dispatch-standalone-container/README.md)
for the full plan, validation requirements, and relationship to the
similarly-shaped `agent-index-standalone-service` and
`machine-fleet-routing-foundation` efforts.
