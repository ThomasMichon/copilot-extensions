# agent-containers

Local Docker dev-container **fleet manager**, **lease broker**, and
optional agent-bridge **`container:` namespace provider**.

Manages a persistent fleet of local dev containers (Docker Desktop WSL2
backend), brokers *advisory* exclusive leases so an effort can borrow a
container without two parallel worktrees driving the same one, and lets
agent-bridge dispatch a Copilot agent into a trusted container over OpenSSH when
agent-bridge is installed. Docker remains the lifecycle/bootstrap boundary and
the SSH `ProxyCommand`, so no container port is published. The CLI and binstub
are owned by this plugin and work standalone; without agent-bridge only bridge
addressing (`container:<name>`) is unavailable.

## Usage path

1. Enable the plugin, then run the cheap stamp action if the binstub is not
   already on PATH:
   - Windows: `pwsh -NoProfile -ExecutionPolicy Bypass -File <plugin>\scripts\init.ps1 stamp`
   - POSIX: `bash <plugin>/scripts/init.sh stamp`
2. First use self-provisions the runtime under `~/.agent-containers/` (or
   `%USERPROFILE%\.agent-containers\`) if the versioned venv is not built yet.
3. Define a fleet in `containers.yaml`, run `agent-containers up <fleet>`, borrow
   with `agent-containers borrow <effort>`, and release when done.
4. If agent-bridge is present, the session-start hook also drops a provider
   manifest into `~/.agent-bridge/providers.d/`; bridge then discovers
   `container:<name>` on demand.

## Concepts

- **Fleet** — a named pool of long-lived dev containers built from one
  devcontainer spec. Kept warm (stopped, not destroyed) between uses.
- **Lease / borrow** — an *effort* (a logical unit of work) borrows a
  container for the duration of its work, then releases it. Leases persist
  across CLI invocations and agent dispatches; they expire only on explicit
  `release` or after a TTL (default 24h). Enforcement is **advisory** — the
  resolver logs but does not block cross-effort dispatch.
- **`container:` resolver** — `agent-bridge send container:<name> "..."`
  is served through agent-bridge's declarative provider registry. The
  `agent-containers` binstub implements `namespace-list`,
  `namespace-resolve`, `namespace-ensure-ready`, and `relay-profile`; bridge
  shells out to those commands rather than requiring this package in the bridge
  venv. Resolution spawns `agent-containers exec --stdio <name>`, whose wrapper
  provisions a machine-local SSH key for trusted fleets and opens OpenSSH with
  `docker exec -i <name> /usr/sbin/sshd -i -e` as its `ProxyCommand`. The
  wrapper fetches the host `gh auth token` at spawn time and stages launch-only
  environment values through stdin, never argv. Because the token is fetched
  inside the wrapper, it is **never** placed in the SpawnTarget that
  agent-bridge persists to its SQLite DB, nor in any log. Restricted fleets keep
  the direct `docker exec` boundary and receive no SSH key projection.

## CLI

```
agent-containers fleet [--json]      # list fleet containers + lease status
agent-containers up <fleet>          # provision/top-up a fleet to its size
agent-containers down <fleet>        # stop (keep warm)
agent-containers start <fleet>       # start stopped containers
agent-containers rm <fleet> [--force] # remove (destructive)
agent-containers borrow <effort> [--fleet <fleet>] [--container <name>]
                                      # lease a free/specific container -> prints name
agent-containers release <target>    # release by container or effort name
agent-containers leases              # show active leases
agent-containers exec <name>         # run the ACP launch command (testing)
agent-containers config-migrate      # stamp/migrate machine-local containers.yaml
agent-containers version             # show version
```

Bridge-facing commands (`namespace-*`, `relay-profile`) are implementation
seams for agent-bridge and are not normally invoked by humans.

## Configuration

`containers.yaml` (looked up via `$AGENT_CONTAINERS_CONFIG`, `./containers.yaml`,
or `~/.agent-containers/containers.yaml`, then an optional agent-worktrees
knowledge-overlay fallback when that binstub exists and the current harness is
bound to external state). Built-in defaults target a generic VS Code dev
container (`exec_user: vscode`, `workspace_folder: /workspace`,
`image_prefixes: ["vsc-"]`); point them at a real repo in your own
`containers.yaml`. The overlay lookup is additive and fail-open; agent-worktrees
is not required for standalone use.

```yaml
exec_user: vscode
workspace_folder: /workspaces/myrepo
forward_gh_token: true
image_prefixes:
  - vsc-myrepo-                  # narrow discovery to your devcontainer image
fleets:
  myrepo:
    repo: your-org/your-repo
    devcontainer_path: D:/Src/myrepo-devcontainer
    size: 3
    code_model: clone   # Model A: repo cloned inside the container
```

### Security profiles

Fleets default to `security_profile: trusted`, preserving the existing
development-container behavior (host GitHub token forwarding, credential relay,
the default broad Copilot launch command, and OpenSSH transport).

Use `security_profile: restricted` for lower-trust agents. Restricted fleets
are image-based, receive no host GitHub token or credential relay, use an
immutable root filesystem with size-bounded tmpfs workspace/home/scratch
surfaces, drop all Linux capabilities, disable privilege escalation, apply
CPU/memory/PID ceilings, and default to `network: none`. They must provide an
explicit per-fleet `acp_command`; there is no implicit
`--allow-all-tools` fallback.

Workspace and home are explicitly executable because agent runtimes and build
tools load native helpers there; `/tmp` and `/run` remain noexec. Execution is
still bounded by the container's dropped capabilities, immutable rootfs,
network policy, and resource limits.

```yaml
fleets:
  restricted-worker:
    image: example/minimal-agent:latest
    security_profile: restricted
    workspace_folder: /workspace
    exec_user: agent
    acp_command: "cd /workspace && minimal-agent --stdio"
    network: model-only
    memory: 4g
    cpus: 2
    pids_limit: 256
    workspace_size: 2g
    home_size: 512m
    environment:
      MODEL_BASE_URL: http://model-proxy:8080/v1
      MODEL_NAME: local-model
```

The restricted defaults are transport enforcement, not prompt policy. Even if a
configuration sets `forward_gh_token: true` or `relay_enabled: true` on a
restricted fleet, both resolve false. `agent-containers fleet --json` reports
the effective `security_profile`, `network`, and `host_credentials` posture so a
dispatcher can verify the venue before launch.

Dispatch is defined only for containers with an exact fleet entry in the active
configuration. An unmanaged/discovered container is visible for inventory but
cannot inherit global launch or credential defaults. Restricted dispatch also
re-inspects the live Docker posture before every start/exec and refuses drift in
capabilities, security options, mounts, devices, namespace sharing, published
ports, network isolation, resource limits, writable surfaces, or immutable image
identity.

`environment` is an explicit **non-secret** key/value allowlist baked into the
container. Restricted configuration rejects credential-shaped names (`*_TOKEN`,
`*_SECRET`, `*_PASSWORD`, `*_API_KEY`, and related forms), and dispatch rejects
an image/runtime environment that contains one. Use this surface for model
endpoint/name/offline settings, never identity. `fleet --json` reports names
only, not values.

Restricted fleets deliberately reject the `devcontainer_path` backend because
its workspace-mount contract cannot guarantee that no host worktree is visible.
Use an image with the required harness preinstalled; repository materialization
inside the bounded workspace is owned by the higher-level workflow. Restricted
writable state is intentionally ephemeral: stopping or removing the container
clears it, so the higher-level workflow must extract or push the work before
release.

An explicit restricted network must be a user-defined Docker network created
with `--internal`; `host`, the default `bridge`, and `container:<name>` sharing
are rejected. This lets a higher-level workflow attach a narrow proxy (for
example, one model endpoint) without granting ambient host or internet reach.

## Discovery

Containers are recognised as fleet members (in priority order) by:
1. the `agent-containers.fleet` label (set by `up`),
2. a `devcontainer.local_folder` label (VS Code / devcontainer CLI), or
3. an image-name prefix from `image_prefixes`.

## Installation

The plugin owns its runtime and binstub:

- `scripts/init.ps1` (Windows) / `scripts/init.sh` (POSIX) build a versioned
  runtime under `~/.agent-containers/versions/<version>/` and publish it via
  `current-version` (Windows binstubs resolve the marker; POSIX also publishes
  `.venv` as the stable link).
- The same installers support a cheap `stamp` action that writes the
  self-provisioning binstub and payload marker (Windows also records a payload
  snapshot) without building the venv; first CLI use runs `provision`.
- `hooks.json` runs `bootstrap-check` at session start and
  `register-bridge-provider` after it. `bootstrap-check` reconciles version drift
  for provisioned runtimes; on Windows it also performs the first cheap stamp.
  If the binstub is absent elsewhere, run the stamp command above. The bridge
  provider hook writes `~/.agent-bridge/providers.d/agent-containers.json` when
  the binstub exists. If agent-bridge is missing, this registration is harmless
  and the CLI remains usable.

Docker is required for fleet operations (`fleet`, `up`, `borrow`, `exec`, etc.),
not for stamping the binstub.

## Runtime state

- `~/.agent-containers/leases.json` — lease records, guarded by an exclusive
  lock file; corrupt/unreadable state is treated as empty.
- `~/.agent-containers/relay-tokens.json` — per-container credential-relay
  secrets used by the bridge-owned relay.
- `~/.agent-containers/ssh/` — machine-local trusted-fleet SSH key, generated
  configs, and container-identity-keyed known-host records.
- `~/.agent-containers/containers.yaml` — optional machine-local config; only
  this copy is eagerly schema-stamped/migrated by `config-migrate`.
- `~/.agent-containers/deploy-manifest.json`, `current-version`, `versions/` —
  runtime deployment metadata and versioned venv slots.

## Troubleshooting

There is no `agent-containers doctor` subcommand today. Use the narrow checks the
CLI exposes:

- `agent-containers version` — proves the binstub and runtime import.
- `agent-containers fleet --json` — proves Docker is reachable and discovery
  works; Docker errors come from `docker version` / `docker ps`.
- `agent-containers leases` then `agent-containers release <container-or-effort>`
  — inspect and clear advisory leases. Forgotten leases expire after the 24h TTL.
- `agent-containers namespace-list` — checks the bridge-facing provider CLI; if
  `agent-bridge send container:<name>` cannot see containers, verify
  `~/.agent-bridge/providers.d/agent-containers.json` exists and points at the
  absolute binstub.
- `agent-containers config-migrate` — stamps/migrates only the machine-local
  `~/.agent-containers/containers.yaml`; repo/cwd configs are read-only.
