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
> The runtime works standalone. In an agent session, invoke the exact `argv`
> from the agent-containers session command catalog; the payload-local command
> provisions on first use. Do not search `PATH` or substitute a same-named
> command from another payload. For bridge dispatch, use the exact `argv[0]`
> from the agent-bridge session command catalog and replace
> `<agent-bridge catalog argv[0]>` below with that path. Outside an agent
> session, stamp a management binstub from an explicitly chosen payload:
>
> - Windows:
>   `pwsh -NoProfile -ExecutionPolicy Bypass -File "<explicit-payload-path>\scripts\init.ps1" stamp`
> - POSIX:
>   `bash "<explicit-payload-path>/scripts/init.sh" stamp`
>
> The first call may take ~30–120s to provision (watch for `::agent-provisioning::`);
> let it finish. If it reports a provisioning failure (e.g. missing uv / network),
> surface the exact message — don't improvise a toolchain install. (Docker is a
> separate prerequisite for fleet operations, not for provisioning the runtime.)

The agent-containers runtime manages a persistent fleet of local Docker dev containers
and brokers exclusive *leases* so an effort can borrow one without two parallel
worktrees driving the same container. Trusted containers are reached over
OpenSSH, with `docker exec` used only as the local `ProxyCommand` bootstrap
(Docker Desktop WSL2 backend), and run a Copilot ACP agent addressable via
the bridge as `container:<name>` when that sibling runtime is installed.
Restricted fleets retain their direct, deny-by-construction `docker exec`
boundary and receive no SSH key. They may be published as named OpenSSH targets
through a host-side stdio adapter; this adds no target port, key, sshd, host
mount, credential relay, or network. Without agent-bridge, the fleet/lease CLI
still works; only bridge dispatch is absent.

## Provision a fleet

Define the fleet in `containers.yaml`, then:

```bash
<catalog argv[0]> up myrepo --count 3      # create/top-up to 3 warm containers
<catalog argv[0]> fleet                   # list members + lease status
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

For a lower-trust worker, use an image-based **restricted** fleet:

```yaml
fleets:
  restricted-worker:
    image: example/minimal-agent:latest
    security_profile: restricted
    workspace_folder: /workspace
    exec_user: agent
    acp_command: "cd /workspace && minimal-agent --stdio"
    network: none
    memory: 4g
    cpus: 2
    pids_limit: 256
    workspace_size: 2g
    home_size: 512m
    environment:
      MODEL_BASE_URL: http://model-proxy:8080/v1
      MODEL_NAME: local-model
```

`restricted` is a transport-enforced posture: no host GitHub token, no
credential relay, no host worktree mount, read-only rootfs, size-bounded tmpfs
workspace/home/scratch, dropped capabilities, no privilege escalation,
CPU/memory/PID ceilings, and an explicit network. It is image-only and requires
an explicit per-fleet `acp_command`; there is no implicit
`--allow-all-tools` fallback. `fleet --json` reports the inspected effective
posture. Stopping the container clears its restricted writable state; extract or
push work before release.

Workspace and home are explicitly executable for agent runtimes/native build
helpers; `/tmp` and `/run` remain noexec.

`network: none` is the restricted default. Any named network must be a
user-defined Docker network created with `--internal`; host/default-bridge/
container namespace sharing is rejected.

Only containers with an exact fleet entry may be dispatched. Inventory may
discover other devcontainers, but they never inherit global credential or launch
defaults. Restricted venues are re-inspected before start/exec; a stale image or
weakened Docker posture is refused.

Use `environment` only for explicit **non-secret** model/harness settings.
Credential-shaped names are refused at config load and again if an image
contains them; machine-readable output reports names only.

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
<catalog argv[0]> borrow my-effort    # prints the leased container name
# ... dispatch work to container:<printed-name> ...
<catalog argv[0]> release my-effort   # free it when done
```

Leases are **advisory** and persist across processes until `release` or TTL
(default 24h). `borrow` will not hand out a container already leased to another
effort; re-borrowing for the same effort is idempotent.
Use `<catalog argv[0]> leases` to inspect active leases. Release by either effort
name or container name; a missing target returns non-zero so callers can notice
cleanup drift.

## Dispatch work

```bash
<agent-bridge catalog argv[0]> send container:myrepo-1 "run the unit tests in packages/foo"
```

The provider manifest in `~/.agent-bridge/providers.d/agent-containers.json`
lets agent-bridge discover `container:` without importing this package into the
bridge venv. The resolver launches the `exec --stdio <name>` action through its registered
management entry point, which
then reaches a trusted container through OpenSSH and runs
`copilot --acp --stdio --allow-all-tools`, staging the host `gh auth token`
through stdin so the token is not persisted in bridge state, argv, or logs.
That SSH process also carries the credential relay over an explicit loopback
reverse forward (`-R 127.0.0.1:<container-port>:127.0.0.1:<live-host-port>`).
The launch fails rather than degrading when agent-bridge has not published a
healthy relay or the far-side port cannot bind. Set `relay.enabled: false` only
for a trusted fleet that intentionally needs no host credential path.

Those are the **trusted-profile** defaults. A restricted fleet launches only its
explicit `acp_command` and forwards neither host credential path.

For a named restricted OpenSSH target:

```bash
<catalog argv[0]> ssh-profile restricted-worker-1 --alias sandbox
ssh sandbox "command"
```

`ssh-profile` fails unless the configured and observed profile is restricted,
the container is running, the full live policy passes, and no destructive
lifecycle hold is active. It delegates fragment rendering to agent-ssh and
persists the normalized provider registry under the agent-containers state
directory. The `ssh-stdio` ProxyCommand is an internal one-connection protocol
adapter: it holds lifecycle admission for the whole SSH connection and always
uses the fleet-owned `exec_user`, never the SSH username. Use
`ssh-profile <name> --json` for read-only metadata inspection.

To expose the target as a project-scoped, read-only Worktree Picker source:

```bash
<catalog argv[0]> ssh-profile restricted-worker-1 \
  --alias sandbox \
  --project <project> \
  --label "Restricted target"
```

This writes a provider-owned descriptor under
`~/.agent-worktrees/sources/agent-containers.json`. The Picker validates the
descriptor, re-resolves live instance/lease/readiness/trust metadata through
the provider command, and reads worktrees, recent messages, and sessions
through the descriptor's isolated absolute provider-owned connection command. The
transport rechecks target, instance, and lease assignment during admission,
and active reads prevent lease release or expired-lease reassignment.
The registered command follows the active versioned runtime through an
owner-private isolated launcher, and releasing the target removes its Picker
registrations. Registration requires an active provider lease. Create,
open/resume, and lifecycle operations remain disabled until a later capability
contract explicitly enables them.

Remove a retired registration without resolving or starting the target:

```bash
<catalog argv[0]> source-remove restricted-worker-1 --project <project>
```
The adapter is single-channel and intended for shell/exec use, not SFTP or VS
Code Remote-SSH. PTY and targeted disconnect cleanup require compatible
util-linux `script` and `setsid` helpers in the restricted image.

## Troubleshooting

- `<catalog argv[0]> version` — payload/runtime health.
- `<catalog argv[0]> fleet --json` — Docker reachability + fleet discovery.
- `<catalog argv[0]> leases` / `<catalog argv[0]> release <target>` — stale lease
  inspection and cleanup. Leases are advisory and TTL-reclaimed.
- `<catalog argv[0]> namespace-list` — bridge-facing provider CLI health. If
  bridge dispatch cannot see containers, check that the provider manifest exists
  under `~/.agent-bridge/providers.d/` and points at the absolute binstub.
- `<catalog argv[0]> config-migrate` — migrate/stamp only the machine-local
  `~/.agent-containers/containers.yaml`; repo/cwd configs are never rewritten.

There is no `doctor` action today.

## Notes

- **Model A** (default): the repo is cloned *inside* the container on create.
  Model B (mount a dedicated WSL2-native standalone clone) is a future option —
  never mount a shared git worktree (branch-exclusivity + dangling-gitdir hazard).
- Discovery recognises fleet members by the `agent-containers.fleet` label, a
  `devcontainer.local_folder` label, or a configured image-name prefix.
- Runtime state (leases) lives in `~/.agent-containers/leases.json`.
