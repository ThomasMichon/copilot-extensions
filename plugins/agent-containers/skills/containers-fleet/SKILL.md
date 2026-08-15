---
name: containers-fleet
description: >-
  Manage a local Docker dev-container fleet and dispatch Copilot agents into
  containers via agent-bridge. Use when asked to "set up containers", "borrow a
  container", "release a container", "container fleet", "dispatch to a
  container", "troubleshoot agent-containers", or to run work inside a local
  Docker dev container instead of a CodeSpace.
---

# Containers Fleet

> **Before you start — readiness (self-provisioning, no agent-worktrees required).**
> agent-containers owns its own binstub/runtime and works standalone. If
> `agent-containers version` is not found, stamp the binstub from the installed
> plugin payload (the first real call then self-provisions the venv):
>
> - Windows:
>   `pwsh -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.copilot\installed-plugins\copilot-extensions\agent-containers\scripts\init.ps1" stamp`
> - POSIX:
>   `bash "$(ls ~/.copilot/installed-plugins/*/agent-containers/scripts/init.sh | head -1)" stamp`
>
> The first call may take ~30–120s to provision (watch for `::agent-provisioning::`);
> let it finish. If it reports a provisioning failure (e.g. missing uv / network),
> surface the exact message — don't improvise a toolchain install. (Docker is a
> separate prerequisite for fleet operations, not for provisioning the runtime.)

`agent-containers` manages a persistent fleet of local Docker dev containers
and brokers exclusive *leases* so an effort can borrow one without two parallel
worktrees driving the same container. Containers are reached over `docker exec`
(Docker Desktop WSL2 backend) and run a Copilot ACP agent addressable via
agent-bridge as `container:<name>` when agent-bridge is installed. Without
agent-bridge, the fleet/lease CLI still works; only bridge dispatch is absent.

## Provision a fleet

Define the fleet in `containers.yaml`, then:

```bash
agent-containers up myrepo --count 3      # create/top-up to 3 warm containers
agent-containers fleet                   # list members + lease status
```

Containers are kept warm (stopped, not destroyed). `down` stops them, `start`
restarts them, `rm` removes them.

## Configuration (`containers.yaml`)

Looked up from `$AGENT_CONTAINERS_CONFIG`, `./containers.yaml`,
`~/.agent-containers/containers.yaml`, then (only when agent-worktrees is present
and the current harness is bound to external state) a knowledge-overlay
`containers.yaml`. Copy the starter example,
[`references/containers.yaml`](references/containers.yaml), and adapt. A fleet
built from a devcontainer spec, at a glance:

```yaml
fleets:
  myrepo:
    repo: your-org/your-repo
    devcontainer_path: D:/Src/myrepo-devcontainer   # dir holding .devcontainer/
    devcontainer_config: .devcontainer/docker/devcontainer.json   # if non-default
    size: 1
```

- `devcontainer_config` lets `up` build a **nested** devcontainer spec (e.g.
  a repo's local-Docker spec under `.devcontainer/docker/`) instead
  of the repo's default top-level config.
- `dotfiles` materialises a host repo at `target` and runs its `install.sh`
  (skill symlinks, instructions, etc.) — the host repo is copied in with
  `docker cp` (read-only on the host side), so `install.sh` never mutates the
  host checkout. The install step is best-effort (a failure is logged, never
  aborts `up`).
- `harness` (optional, opt-in) materialises a separate **control-plane harness**
  checkout (effort/vision state) at **`/workspaces/<basename>`** — the standard
  repo-layout convention, same as a CodeSpace — kept **distinct** from `dotfiles`
  and with **no install step** (referenced in place, not installed). Omit it
  (the default) to keep the harness **off** the container (the local
  control-plane agent owns effort updates); set it only when an in-container
  agent needs local effort-state reference. Telling the agent *how* to reference
  the effort is a control-plane **skill** concern, not this config:
  ```yaml
  harness:
    repo: /path/to/your/harness   # host checkout copied in (read-only) to /workspaces/<basename>
  ```

## Borrow / release (effort owns a container)

```bash
name=$(agent-containers borrow my-effort)   # lease a free container
# ... dispatch work to container:$name ...
agent-containers release my-effort          # free it when done
```

Leases are **advisory** and persist across processes until `release` or TTL
(default 24h). `borrow` will not hand out a container already leased to another
effort; re-borrowing for the same effort is idempotent.
Use `agent-containers leases` to inspect active leases. Release by either effort
name or container name; a missing target returns non-zero so callers can notice
cleanup drift.

## Dispatch work

```bash
agent-bridge send container:myrepo-1 "run the unit tests in packages/foo"
```

The provider manifest in `~/.agent-bridge/providers.d/agent-containers.json`
lets agent-bridge discover `container:` without importing this package into the
bridge venv. The resolver launches `agent-containers exec --stdio <name>`, which
then runs `copilot --acp --stdio --allow-all-tools` inside the container,
forwarding the host `gh auth token` as `GH_TOKEN` by environment name so the
token is not persisted in bridge state or logs.

## Troubleshooting

- `agent-containers version` — binstub/runtime health.
- `agent-containers fleet --json` — Docker reachability + fleet discovery.
- `agent-containers leases` / `agent-containers release <target>` — stale lease
  inspection and cleanup. Leases are advisory and TTL-reclaimed.
- `agent-containers namespace-list` — bridge-facing provider CLI health. If
  bridge dispatch cannot see containers, check that the provider manifest exists
  under `~/.agent-bridge/providers.d/` and points at the absolute binstub.
- `agent-containers config-migrate` — migrate/stamp only the machine-local
  `~/.agent-containers/containers.yaml`; repo/cwd configs are never rewritten.

There is no `agent-containers doctor` command today.

## Notes

- **Model A** (default): the repo is cloned *inside* the container on create.
  Model B (mount a dedicated WSL2-native standalone clone) is a future option —
  never mount a shared git worktree (branch-exclusivity + dangling-gitdir hazard).
- Discovery recognises fleet members by the `agent-containers.fleet` label, a
  `devcontainer.local_folder` label, or a configured image-name prefix.
- Runtime state (leases) lives in `~/.agent-containers/leases.json`.
