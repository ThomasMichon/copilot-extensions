# agent-containers

Local Docker dev-container **fleet manager**, **lease broker**, and
optional agent-bridge **`container:` namespace provider**.

Manages a persistent fleet of local dev containers (Docker Desktop WSL2
backend), brokers *advisory* exclusive leases so an effort can borrow a
container without two parallel worktrees driving the same one, and lets
agent-bridge dispatch a Copilot agent into a trusted container over OpenSSH when
agent-bridge is installed. Restricted containers can also be exposed as named
OpenSSH targets through a host-side, stdio-only provider adapter that translates
SSH session requests into the existing restricted `docker exec` boundary. No
container port, key, or SSH daemon is added. The CLI and binstub are owned by
this plugin and work standalone; without agent-bridge only bridge addressing
(`container:<name>`) is unavailable.

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
   `container:<name>` on demand. The manifest carries provider provenance; a
   missing binstub/payload makes only that namespace inactive and is diagnosed
   by `agent-bridge doctor`.

## Concepts

- **Fleet** — a named pool of long-lived dev containers built from one
  devcontainer spec. Kept warm (stopped, not destroyed) between uses.
- **Lease / borrow** — an *effort* (a logical unit of work) borrows a
  container for the duration of its work, then releases it. Leases persist
  across CLI invocations and agent dispatches; they expire on explicit
  `release` or after a TTL (default 24h). Acquisition may reclaim a lease sooner
  only when its exact same-host, same-environment holder PID is definitively
  gone. Remote, cross-environment, legacy, or otherwise indeterminate holder
  liveness keeps the TTL behavior, and active lifecycle or provider-session
  admissions block reclamation. The replacement lease records the reclaim
  reason and prior holder for audit. Enforcement is **advisory** — the resolver
  logs but does not block cross-effort dispatch. Restricted destructive
  lifecycle adds a separate provider-owned hold: while a member is being checked
  for recreate/remove, new borrows and provider launches are refused.
- **`container:` resolver** — `agent-bridge send container:<name> "..."`
  is served through agent-bridge's declarative provider registry. The
  `agent-containers` binstub implements `namespace-list`,
  `namespace-resolve`, `namespace-ensure-ready`, `namespace-recreate`, and
  `relay-profile`; bridge shells out to those commands rather than requiring
  this package in the bridge venv. `namespace-recreate` is the destructive,
  target-scoped parity seam: it removes only the identity-checked Docker
  instance of one trusted configured member, recreates the same deterministic
  name, and reports the new instance identity without exposing launch data.
  Resolution spawns `agent-containers exec --stdio <name>`, whose wrapper
  provisions a machine-local SSH key for trusted fleets and opens OpenSSH with
  `docker exec -i <name> /usr/sbin/sshd -i -e` as its `ProxyCommand`. The
  wrapper fetches the host `gh auth token` at spawn time and stages launch-only
  environment values through stdin, never argv. Because the token is fetched
  inside the wrapper, it is **never** placed in the SpawnTarget that
  agent-bridge persists to its SQLite DB, nor in any log. Restricted fleets keep
  the direct `docker exec` boundary and receive no SSH key projection. Their
  venue metadata advertises `transport: provider-exec`; a named profile can be
  published with `agent-containers ssh-profile <name> [--alias <alias>]`.
  Add `--project <project> [--label <label>]` to register the same stable
  leased provider target as a project-scoped Worktree Picker source. The provider
  publishes a validated descriptor under `~/.agent-worktrees/sources/`; the
  Picker refreshes current instance, lease assignment, readiness, and trust
  metadata through the descriptor's isolated absolute provider command before reading
  worktrees through an explicit provider-owned `ProxyCommand`. The transport
  atomically rechecks the target, instance, and assignment at connection
  admission, and an active read prevents lease release or expired-lease
  reassignment. Registered commands use an owner-private stable launcher that
  follows the active runtime in Python isolated mode. Releasing a target removes
  its Picker registrations. The initial contract is read-only:
  list, recent-message, session, and refresh operations are advertised, while
  create and lifecycle actions remain explicitly disabled. Remove a retired
  registration with `agent-containers source-remove <name> --project <project>`;
  removal does not require the target to still exist or be running.
  The resulting OpenSSH `ProxyCommand` runs `agent-containers ssh-stdio <name>`,
  which hosts SSH protocol only for that child process's stdio lifetime and
  opens no listener.
  `namespace-resolve` also returns a versioned `venue` block with a stable
  provider target id (`container:<name>`), the current Docker instance id,
  fleet/workspace identity, configured and effective trust posture, transport,
  readiness, and capability envelope. The target id is scoped to the local
  provider instance and survives deterministic container replacement; the
  instance id changes so a consumer can detect replacement (enforcement is a
  later consumer contract). `posture_verified: false` is deliberate:
  `namespace-resolve` describes the observed label/state, while
  `namespace-ensure-ready` and `exec` perform the authoritative live policy
  inspection immediately before launch. A configured/observed trust mismatch is
  reported unready and resolves to the stricter posture; unknown live profiles
  are likewise non-projecting and launch is refused. Only exact
  trusted-configured/trusted-observed posture may enter the SSH, token, or relay
  path, including Session Host preparation; missing labels are not interpreted
  as trusted. Unlabeled legacy containers remain visible in inventory as
  `security_profile: unknown` but cannot launch until their posture is
  reconciled. The legacy
  `security_profile` key remains in the block, so the new shape is a strict
  superset for in-process consumers; older CLI bridge consumers ignore the
  additive fields.
  The same trusted SSH process carries
  `-R 127.0.0.1:<container-relay>:127.0.0.1:<live-host-relay>`, so credential
  helpers connect only to container loopback and the host relay remains bound
  only to host loopback. The per-container token remains request authorization
  for Azure-token minting on the shared relay; it is no longer compensating for
  a host-network-reachable endpoint.

## CLI

```
agent-containers fleet [--json]      # list fleet containers + lease status
agent-containers up <fleet> [--recreate] [--force-abandon] [--json]
                                      # provision/top-up; safely replace idle drift
agent-containers down <fleet> [--force-abandon] [--json]
                                      # rescue, then stop confirmed-idle members
agent-containers start <fleet>       # start stopped containers
agent-containers rm <fleet> [--force] [--force-abandon] [--json]
                                      # remove; restricted members rescue first
agent-containers borrow <effort> [--fleet <fleet>] [--container <name>]
                                      # lease a free/specific container -> prints name
agent-containers release <target>    # release by container or effort name
agent-containers leases              # show active leases
agent-containers lifecycle-clear [name]
                                      # clear only expired/dead admission records
agent-containers exec <name>         # run the ACP launch command (testing)
agent-containers ssh-profile <name> [--alias <alias>]
                                      # publish a named restricted SSH target
agent-containers ssh-profile <name> --project <project> [--label <label>]
                                      # also register a read-only Picker source
agent-containers source-remove <name> --project <project>
                                      # remove that Picker source registration
agent-containers ssh-profile <name> --json
                                      # inspect provider/profile metadata only
agent-containers config-migrate      # stamp/migrate machine-local containers.yaml
agent-containers version             # show version
```

Bridge-facing commands (`namespace-*`, `relay-profile`) and `ssh-stdio` are
implementation seams and are not normally invoked by humans.

## Configuration

`containers.yaml` (looked up via `$AGENT_CONTAINERS_CONFIG`, `./containers.yaml`,
or `~/.agent-containers/containers.yaml`, then an optional agent-worktrees
knowledge-overlay fallback when that binstub exists and the current harness is
bound to external state). Built-in defaults target a generic VS Code dev
container (`exec_user: vscode`, `workspace_folder: /workspace`,
`image_prefixes: ["vsc-"]`); point them at a real repo in your own
`containers.yaml`. The overlay lookup is additive and fail-open; agent-worktrees
is not required for standalone use.

`AGENT_CONTAINERS_STATE_DIR` may relocate mutable lease/admission/rescue state
without relocating either platform's runtime installation. When Windows and
WSL intentionally operate the same Docker provider, both installations must set
it to the same filesystem-visible directory (or designate only one environment
as the restricted lifecycle owner). Separate state roots cannot make host-side
admission atomic across environments. Shared records from the peer environment
are therefore preserved fail-closed until their bounded heartbeat expires.

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

The trusted credential relay is a required launch dependency by default. The
wrapper reads agent-bridge's published live relay port, verifies its `ping/pong`
protocol identity, and refuses launch if the SSH reverse forward cannot bind.
Start agent-bridge before dispatch, or explicitly set `relay.enabled: false`
when a trusted fleet intentionally does not need host credentials.

Use `security_profile: restricted` for lower-trust agents. Restricted fleets
are image-based, receive no host GitHub token or credential relay, use an
immutable root filesystem with size-bounded tmpfs workspace/home/scratch
surfaces, drop all Linux capabilities, disable privilege escalation, apply
CPU/memory/PID ceilings, and default to `network: none`. They must provide an
explicit per-fleet `acp_command`; there is no implicit
`--allow-all-tools` fallback.

The primary threat is a fallible worker issuing a mistaken/destructive command,
including one suggested by prompt injection—not an omnipotent hostile tenant.
Containment therefore prioritizes disposable local state, no host filesystem or
credential projection, active-session-safe lifecycle, and narrow egress. A
typical online restricted fleet reaches only its model endpoint, repository
forge, and a controlled basic-search interceptor; arbitrary internet remains
absent. Rescue still treats received bytes as untrusted so command mistakes,
special files, and path races cannot escape the evidence allowlist.

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

The restricted launch command is a separate API that accepts only container,
user, and ACP command. It has no parameters for a host token, credential relay,
SSH projection, mount, network, or gateway, so the restricted path cannot gain
those capabilities through an accidental caller flag.

The optional restricted SSH profile does not change that authority boundary.
The host-side adapter accepts one SSH session channel, ignores the client
username for Docker-user selection, holds provider lifecycle admission for the
full connection, and re-inspects the live restricted posture after admission.
It disables OpenSSH connection multiplexing and credential authentication in
the emitted profile. This slice supports ordinary shell/exec use, not
multi-channel consumers such as SFTP or VS Code Remote-SSH. PTY requests use
the target's util-linux `script` and `setsid` helpers with the initial terminal
dimensions; a target without compatible helpers fails explicitly.

Venue capability booleans report configured launch authority, not inferred
runtime access: `host_credentials` and `credential_relay` come from the effective
fleet credential policy; `session_host` is trusted-only; and
`ssh_profile` identifies an exact restricted provider-exec target; `ready`
separately reports whether it can be entered now. `container_local_workspace`
means the target has a concrete container workspace, not that it is safe to
mount host files. Unknown future capability keys must be treated as unavailable
by consumers.

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

### Restricted replacement and session evidence

`up --recreate`, `down`, and `rm` reconcile restricted members independently.
Before stopping or destroying one member, the provider records an exclusive,
heartbeated lifecycle hold, checks the advisory lease and provider launch
admission, and probes Copilot's own `inuse.<pid>.lock` markers inside the
still-running container. Marker PIDs are checked through `/proc/<pid>` rather
than signal permissions, with a process/cmdline backstop so an unexpected marker
layout becomes unknown instead of idle. A live marker or an
unavailable/unparseable probe defers that member. Recreate/remove may unpause a
member for inspection; `down` defers paused, restarting, removing, and unknown
states. Already-stopped members are reported unchanged with explicit evidence
loss status. Other confirmed-idle members may continue through rescue and
replacement.

The fleet named on the command is the safety authority. A deterministic-name
member carrying an explicit foreign fleet label is reported as drift and
deferred; its foreign/trusted configuration can never downgrade a requested
restricted remove into the trusted direct-removal path.

The rescue is one-way evidence capture, not persistence or restore. The
provider streams only these members from UUID-named Copilot session-state
directories into host-owned state:

- `events.jsonl`
- `workspace.yaml`, `origin.json`, and `context.json` when present
- `agent-worktrees.json` when it is bounded schema-v1 JSON for the enclosing
  session ID
- `checkpoints/index.md` when present

`files/`, rewind snapshots, research, unknown session members, workspaces,
source roots, settings, databases, credentials, and arbitrary home files are
never copied. The reciprocal sidecar remains inert evidence and does not make a
restored worktree relation authoritative. Each allowlisted member is opened by
a host-supplied helper executed with the image's Node interpreter. The
interpreter is resolved to an absolute path
whose canonical target is outside the actual inspected tmpfs/mount/home
surfaces; helper launch fails unless Docker reports `ReadonlyRootfs: true`.
Bash liveness probes use the same
absolute-candidate rule plus `--noprofile --norc`; both helper paths override
startup/preload/runtime option variables with a sanitized environment rather
than inheriting `BASH_ENV`, `ENV`, `LD_PRELOAD`, `NODE_OPTIONS`, or container
`PATH`. The helper uses no-follow directory
descriptors anchored beneath the restricted home, fstats and streams that same
descriptor, and is syntax/execution tested. FIFO, device, socket, symlink, and
oversize members are excluded as explicit partial evidence instead of blocking
all valid members. Inventory is NUL-framed and never descends into `files/`,
rewind snapshots, or research; its byte ceiling is enforced while streaming,
terminating the helper immediately on overflow. Helper diagnostics are drained
concurrently into a separate bounded buffer so stderr cannot deadlock the
stream; diagnostic overflow likewise terminates the helper. Host-received bytes
are SHA-256 hashed,
re-verified, fsynced, and published as part of one atomic capture with path-free
status metadata. A missing session-state root is recorded as verified-partial,
distinct from a present but empty root.
The replacement starts with the same bounded tmpfs/no-bind/no-mount policy and
receives no rescued bytes.

An ordinary rescue failure defers stop/removal and leaves the old container
running. A member that is already stopped has already lost its tmpfs evidence,
so remove/recreate likewise defers unless `--force-abandon` records that loss.
The flag never overrides active or unknown liveness. Immediately before the
lifecycle action, the provider proves it still owns the hold and probes
liveness again. Current and failed-attempt-with-verified-fallback status is
available in `fleet --json` under `lifecycle_hold` and `rescue`.
The complete rescue operation has a wall-clock deadline; a hung member stream
is terminated and replacement defers. Deploy holds also carry a non-extendable
maximum lifetime, reserving bounded time for Docker stop/remove, state
confirmation, and hold cleanup after the rescue deadline. Heartbeat cannot keep
a wedged operation alive forever, and the hold is re-verified after action
confirmation before release.
After a successful `down`, `rm` accepts the verified capture for that same
container instance rather than demanding telemetry abandonment again. A
capture being consumed by an active lifecycle operation is pinned against
global retention until stop/remove is confirmed or the operation safely
aborts, and its archive/pin are verified immediately before destruction.
Captures, status, pins, and stopped-instance reuse bind to both the immutable
Docker container ID and its authoritative `State.StartedAt` execution
generation; restarting the same container ID creates a new generation that
cannot reuse evidence from the prior run.

`up`, `down`, and `rm` accept `--json`; their result includes created/stopped/
removed, unchanged, rescued, abandoned, and deferred members. Any deferred
member returns the established busy exit code `75`. Restricted `exec` blocked by
a lifecycle hold uses the same exit code.
Docker command timeouts are normalized into per-member deferred results:
liveness timeouts become unknown, while stop/remove/confirmation timeouts leave
the hold fail-closed and do not abort reconciliation of sibling members.
Typed rescue/generation/pin failures follow the same per-member rule across
`up`, `down`, and `rm`.

Deploy holds expire after 15 minutes without heartbeat; session admissions
expire after 5 minutes without heartbeat. This bounds PID-reuse failures.
Windows and WSL cannot inspect each other's PIDs, so a fresh record written by
the other environment stays fail-closed until its heartbeat expires.
`lifecycle-clear` removes only records proven dead/expired (or corrupt files
older than the bound); it never clears a fresh unknown owner.
Cleanup is deliberately fail-silent: if the hold/admission record becomes
unreadable while an operation exits, cleanup leaves it fail-closed for
TTL/`lifecycle-clear`, logs the condition, and preserves the operation's
original result or exception.

Host capture limits are optional top-level `containers.yaml` settings, expressed
as byte counts:

```yaml
rescue:
  max_member_bytes: 67108864
  max_capture_bytes: 268435456
  max_total_bytes: 1073741824
  retain_per_container: 3
  operation_timeout_seconds: 600
```

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
  lock file and kept backward-compatible across runtime versions;
  corrupt/unreadable state is treated as empty.
- `~/.agent-containers/lease-details.json` — optional environment and reclaim
  audit metadata keyed to the exact core lease identity. Missing, stale, or
  unreadable details make early PID-based reclaim indeterminate without hiding
  the active lease from older runtimes.
- `~/.agent-containers/deploy-holds.json` and `session-admissions.json` —
  short-lived, heartbeated provider admission records sharing the lease lock
  discipline across Windows/WSL access to the same Docker provider.
- `~/.agent-containers/rescues/` — bounded, host-owned, atomically published
  restricted session-evidence captures. Captured bytes are untrusted evidence
  and are never restored or executed by the provider.
- `~/.agent-containers/relay-tokens.json` — per-container credential-relay
  secrets used by the bridge-owned relay.
- `~/.agent-containers/ssh/` — machine-local trusted-fleet SSH key, generated
  configs, and container-identity-keyed known-host records.
- `~/.agent-containers/containers.yaml` — optional machine-local config; only
  this copy is eagerly schema-stamped/migrated by `config-migrate`.
- `~/.agent-containers/deploy-manifest.json`, `current-version`, `versions/` —
  runtime deployment metadata and versioned venv slots.

Mutable coordination state is owner-only: the state directory is enforced as
`0700` and leases, admissions, holds, pins, rescue status/metadata, and relay
token files are atomically published from owner-only temporary files as `0600`
on POSIX. Windows applies the corresponding mode operations best-effort while
relying on the user's filesystem ACL. WSL DrvFS/9p and other detected
ACL-backed shared filesystems use the same best-effort model when POSIX mode
bits are not authoritative; native POSIX filesystems continue to enforce and
verify exact modes. Atomic JSON replacement fsyncs the containing directory
best-effort so coordination and relay-token publication is crash-durable where
the backing filesystem supports it. A relocated state directory becomes the
relay-token home; an existing legacy token store is repaired and reused without
rewriting its contents. Lifecycle pin files are published complete via
owner-private temporary files and atomic no-clobber linking/rename; malformed
crash remnants fail closed only for a bounded interval before retention reclaims
them.

## Troubleshooting

There is no `agent-containers doctor` subcommand today. Use the narrow checks the
CLI exposes:

- `agent-containers version` — proves the binstub and runtime import.
- `agent-containers fleet --json` — proves Docker is reachable and discovery
  works, and reports restricted lifecycle holds plus the latest rescue attempt
  without exposing host paths or transcript content; Docker errors come from
  `docker version` / `docker ps`.
- `agent-containers leases` then `agent-containers release <container-or-effort>`
  — inspect and clear advisory leases. Forgotten leases expire after the 24h TTL.
- `agent-containers lifecycle-clear [name]` — safely clear only expired/dead
  lifecycle records; fresh or cross-environment-unknown records remain blocked.
- `agent-containers namespace-list` — checks the bridge-facing provider CLI; if
  `agent-bridge send container:<name>` cannot see containers, verify
  `~/.agent-bridge/providers.d/agent-containers.json` exists and points at the
  absolute binstub.
- `agent-containers config-migrate` — stamps/migrates only the machine-local
  `~/.agent-containers/containers.yaml`; repo/cwd configs are read-only.
