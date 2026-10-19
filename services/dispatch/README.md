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


## Pinned central authority with optional local-only work

Fleet mode is explicit: set `AGENT_DISPATCH_TASK_AUTHORITY=central` on fleet
clients and supervisors. The central `services/dispatch` instance keeps its own
durable database and ordinary server authentication; its serving environment
does not opt into this client mode. Use a private authenticated endpoint, not
an unauthenticated public listener.

Configure each participating machine with:

| Setting | Meaning |
|---------|---------|
| `AGENT_DISPATCH_TASK_AUTHORITY=central` | Default all ordinary task clients and supervisor reconnects to the central writer, regardless of local health. |
| `AGENT_DISPATCH_SHARED_URL` | The pinned central endpoint, discovered/provided by its owning deployment. |
| `AGENT_DISPATCH_SHARED_TOKEN` or `AGENT_DISPATCH_SHARED_TOKEN_COMMAND` | Central service authentication, independent of the local server's bearer. Central mode refuses missing credentials instead of borrowing local authority. |
| `AGENT_DISPATCH_SHARED_CONTROL_TOKEN` or its `_COMMAND` variant | Separate central control authority when managed producer/grant operations require it. |
| `AGENT_DISPATCH_FEDERATION_ROLE=peer` | Presence-only federation for a machine retaining its own coordinator. `satellite` is also supported under its existing enrollment gate. |
| `AGENT_DISPATCH_FEDERATION_INSTANCE` | The machine/environment's stable, uniquely resolved directory identity. |

A pure client need not start a local coordinator. If one is retained, central
mode requires its role to be `peer` or `satellite`, never `coordinator` or
`standby`. Both server admission and the federation runner reject lease-eligible
roles in this mode. Directory awareness does not replicate a task database or
grant a local database permission to become the central writer.
Strict local-only mode needs no central service or federation, but if it does
participate in federation its role is likewise presence-only, not a candidate
fleet writer. Unconfigured legacy federation/election behavior is unchanged.

Central and local-only work have **explicit separate targets**, not a combined
list. Ordinary `create`, `list`, `show` and lifecycle operations use the central
writer in central mode; `--shared` also names that target explicitly. Use
`--local` for local-only work. It resolves the local rendezvous, ignores a
remote `AGENT_DISPATCH_URL` override, and never switches to a shared or SSH peer
when the local service is unavailable. Conversely, a central outage never
starts/promotes a local queue or redirects a central task mutation there.
Transport errors do not prove a write was unaccepted: reconcile an uncertain
outcome from that same central authority, never by replaying it locally.
Explicit `--url` remains an operator-selected endpoint; `--local` cannot be
combined with it or `--shared`.

The supervisor singleton remains one owner per machine/environment, not one per
authority. An explicit `supervise register --ensure` accepts an existing owner
only when its current PID-bound status receipt names the same target.
`authority conflict` or `authority unavailable` means the registration was
saved but that target is **not ensured**; reconcile the existing owner's
configuration explicitly. The command does not start a second supervisor,
terminate the existing one or move its registrations. Target receipts contain
only a digest of credential-free target flags and the configured central
endpoint, not bearer/control credentials. Local rendezvous discovery remains
dynamic; changing it does not create a different supervisor target.

Explicit `inspect-task --machine` reads from the selected writer rather than
implicitly inspecting that machine's independent queue over SSH. Remote MCP
emitter side-loads refuse explicit authority before executing on an SSH owner:
the remote login's default is not proof of the same writer, and coordinator
credentials are not forwarded. Invoke the side-load on its owner with the same
explicit writer instead. Legacy peer browsing/side-loading is unchanged.

Central task reads are on-demand references to the central service, not local
task rows or an offline write replica. Keep the selected authority with any
saved task reference; a bare task ID is not permission to move its state to
another database. Existing local tasks remain local and are accessed with
`--local`; selecting central mode does not import or migrate them. No ownership
transfer operation is introduced: any future transfer must be acknowledged and
fenced at the durable owner before the destination activates it.

Named-authority `create`, `claim` and `show` output includes `task_authority`
and a durable `claim_ref`: `dispatch-central-task:<id>` or
`dispatch-local-task:<id>`. The existing claim-provider registry carries the
corresponding fixed `--shared`/`--local` callback prefix, so changing a caller's
default mode cannot redirect the reference. No second task store is created.
When explicit mode is configured, the old unqualified `dispatch-task:` callback
fails unavailable rather than interpreting a foreign 404 as proof that the
original resource disappeared. Reconcile old references at their original
writer before explicitly recording the appropriate qualified reference; mode
selection never rewrites claims or transfers task ownership.

In explicit authority mode, inbox machine filters apply to that selected
authority, not a peer's independent database. `list --target-machine` filters
placement without changing writers. The legacy SSH create-and-embody path that
creates a task in another machine's queue is refused with explicit authority:
enqueue using `--target-machine` and let the selected central authority's
supervisor acquire it instead. Spawned worker monikers and producer re-exec
arguments retain explicit local/central routing. Installer-owned local
start/stop commands remain local even when ordinary clients default central.
Existing SSH fleet bodies retain that selector on commands relayed through
their origin host as well, so an SSH login's different default cannot retarget
an explicitly local-only task. Central credentials stay on the origin.

This is an opt-in routing/authority contract, not a host deployment action.
Apply configuration and restart affected long-lived clients through their
existing installer/service lifecycle when rollout is authorized. Leaving
`AGENT_DISPATCH_TASK_AUTHORITY` unset preserves existing standalone discovery,
legacy shared fallback and SSH behavior. Restricted task-only credentials keep
their separately pinned task endpoint and reject `--local` overrides.

## Scope

See [`efforts/active/agent-dispatch-standalone-container/README.md`](../../efforts/active/agent-dispatch-standalone-container/README.md)
for the full plan, validation requirements, and relationship to the
similarly-shaped `agent-index-standalone-service` and
`machine-fleet-routing-foundation` efforts.
