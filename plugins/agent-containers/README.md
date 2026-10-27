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

## Legacy runtime removal

Use `scripts/init.ps1 -Action uninstall -InstallDir <root>` or
`scripts/init.sh uninstall --install-dir <root>` only for the selected legacy
runtime. The finite removal action shares install's `.install.lock`, retains
canonical command/generation/selector custody through removal, and removes the
owned deploy manifest last. It never purges the root, coordination leases,
`containers.yaml`, `leases.json`, auth/private data, logs, custom files or
`AGENT_CONTAINERS_STATE_DIR`. Managed/cell/foreign roots, raw `..`, reparse
ancestors, unproven runtime ownership and live processes refuse explicitly.
An older installer-engine without the finite entry-name API refuses; there is
no purge fallback.

The adapter passes its trusted `scripts/uninstall-profile.json` to canonical
`installer_engine_uninstall.py`; it does not own a second custody or cleanup
orchestrator. The helper and native engine must be materialized in this payload's
own `scripts/`; no ambient or sibling installation is searched.
Structural `--check` grants no deletion authority. The canonical
owner repeats ownership, inventory, live-consumer and read-only transport-guard
checks under all custody immediately before `--remove-runtime` deletion.
Unexpected outer-owner death is not qualified by that batch custody alone:
noninherited .NET whole-transaction locks and PID receipts require a native
lifetime/handle bridge. POSIX kernel-lock release is close-only, preserving any
inherited finite guardian's open-file-description custody.

SSH files alone are not a retained transport closure: Windows reconnect configs
depend on the loopback broker's interpreter, imports and Docker children.
Until that complete dependency closure has independent custody, any nonempty
`ssh` directory under the addressed root or `AGENT_CONTAINERS_HOME` refuses
uninstall before runtime removal. Preparation locks, live brokers and dormant
reconnect configurations are all preserved without stopping a transport.
Diagnostics distinguish a responsive broker endpoint from stale proxy records
and unattributed reconnect artifacts. Stale artifacts are not called busy, but
are not complete idle/dependency-closure proof either. Refusal is a safety
negative control, never positive proof of a retained active transport closure.

## Usage path

The [portable CI repair template](templates/ci-repair/README.md) ships an inert,
restricted image/fleet recipe with no repository, charter or credential baked
in. It does not enable a repair worker or supply inference/repository authority.

1. Enable the plugin, then run the cheap stamp action if the binstub is not
   already on PATH:
   - Windows: `pwsh -NoProfile -ExecutionPolicy Bypass -File <plugin>\scripts\init.ps1 stamp`
   - POSIX: `bash <plugin>/scripts/init.sh stamp`
2. First use self-provisions a standalone runtime under `~/.agent-containers/`
   (or `%USERPROFILE%\.agent-containers\`) if the versioned venv is not built
   yet and the selected installation admits plugin publication.
3. Define a fleet in `containers.yaml`, run `agent-containers up <fleet>`, borrow
   with `agent-containers borrow <effort>`, and release when done.
4. If agent-bridge is present, the session-start hook also drops a provider
   manifest into `~/.agent-bridge/providers.d/`; bridge then discovers
   `container:<name>` on demand. The manifest carries provider provenance; a
   missing binstub/payload makes only that namespace inactive and is diagnosed
   by `agent-bridge doctor`.

Payload commands and stamped global stubs check the selected installation's
runtime-deployment policy before cold provisioning creates a root, lock, status
or boot trace, and refresh that decision under the provisioning lock. Managed,
malformed and unsupported policies refuse first-use publication with an explicit
installation-management diagnostic. A healthy current/LKG runtime still starts
without provisioning or runtime repair; existing ready-runtime boot tracing is
unchanged. Standalone
provisioning retains the exact selected root, context, installer exit code and
stderr; `AGENT_CONTAINERS_NO_SELFPROVISION` remains an opt-out.

On Windows, initial installation `status` and selected-context `validate`
reads use the payload's canonical SDK in a fresh isolated, no-site Python
process only after the [independent bootstrap selector](../../docs/install-contract.md#independent-windows-evaluator-bootstrap)
admits a registered signed CPython base. PATH and the addressed runtime do not
establish admission. Unavailable optional support selects native PowerShell
before evaluation; selected interpreter/SDK failures are terminal. The file
guard is released before ordinary package-site/runtime resolution. Publication
and under-lock admission retain their ordering. Legacy PowerShell argument
passing uses the same encoded-argv module entry as Codespaces, preserving empty,
numeric and quoted arguments without disabling ordinary package-site loading.

Global stubs retain the exact publisher, canonical install root and selected
context rather than searching other marketplaces or ambient commands. Equivalent
root spellings bind to the same installation. Fresh direct provisioning publishes
its original publisher binding before emitting the command; no prior stamp is
required. Stamping reads
the shared installation decision; only a positively identified legacy binding
can use the resolver-only fallback. A cell-bound command restores its retained
context even in an ordinary shell and revalidates activation/generation through
the gate. If its source pointer or retained gate is unavailable, it refuses
rather than running that cell as legacy. A healthy known-legacy runtime can
still use its co-deployed readiness resolver; cold provisioning and
explicit-context fallback refuse.
The POSIX installer's whole-action gate uses native `flock` or an existing
Python's `fcntl.flock` on the same persistent install-lock descriptor, including
legacy removal. Missing both refuses without provisioning. Old install PID or
reaper receipts are retained and require explicit settlement, never automatic
reclamation by the new gate.
First-use provisioning's separate stale PID-link recovery serializes reapers
and only cleans up its own mutex. An unknown/abandoned reaper mutex is not
deleted: after 30 one-second retries the command refuses with exit 126 and an
explicit repair diagnostic. Live provisioning owners retain their separate wait.

The Windows installer declares its `init.ps1` human-output boundary for the
existing updater's timed capture. Direct invocation retains its original
encoding; requested capture uses the packaged UTF-8 text-writer initializer
without changing native-command decoding or pipeline stdin. Docker-version
diagnostics are drained on both streams and preserved on stderr; POSIX no
longer suppresses those diagnostics. On both platforms a failing Docker probe
warns with its exit code but does not prevent CLI installation.

## Dispatch-only restricted workers

Managed agent launch checks repository hosting eligibility inside the actual
guest checkout, using the selected physical container instance. This includes
ACP, trusted Session Host preparation, attached launch, and restricted task
receivers. Guest ACP commands recheck at execution; an explicit prepared
worktree is checked instead of its anchor. Required receivers must support
`agent-worktrees agent-policy check --path <checkout> --operation launch --json`;
unsupported receivers refuse without a weaker launch fallback.

`dispatch-check-hosting <name> --expected-container-id <id>` is the read-only
preflight for restricted supervisors, before binding or credential projection.
Source allocation and inspection remain available. A genuine live attachment
preserves custody; if a detached rejoin instead needs a replacement, the
receiving Worktrees launch boundary checks fresh policy before creating it.
The shared [repository-policy library](../../libs/repository-policy/README.md)
owns configuration and precedence.

An image-backed restricted fleet can opt into `dispatch_only: true`. This is a
creation-time policy, not an advisory borrow: ordinary `exec`, provider SSH and
unqualified bridge launches are refused. Existing ordinary restricted fleets
remain unchanged. Recreate an old member when changing this policy.

The trusted host calls `dispatch-prepare <name> --expected-container-id <full-id>`
with a bounded JSON object on stdin containing `task_id`, `worker_id`,
`generation`, `coordinator_epoch` and `coordinator_url`. The provider retains a
nonsecret binding and returns its explicit `spawn_command`; a different task,
generation, epoch, worker or endpoint cannot reuse that physical container.
Releasing a borrow or ending a session does not clear this fence: a different
incarnation needs a new physical container. One provider worker is admitted at
a time. Admission, lifecycle and projection writers share a kernel-owned
host lock that is never stolen based on file age. Task execution additionally
holds a guest `flock` on a stable root-owned file for the worker's lifetime:
terminating only the host wrapper does not admit a duplicate surviving worker.

Preparation also returns an explicit, nonsecret `agent` address:
`container:dispatch/<full-physical-id>/<birth-binding-id>/<name>`. Only this
prepared address participates in namespace readiness and resolution for a
dispatch-only member; ordinary `container:<name>` remains refused. Every
resolution rechecks the retained birth binding, exact physical member, live
restricted posture and lifecycle hold without changing task ownership.
Its `instance_pinned_exec` capability is backed by the expected physical-ID
and birth-binding fences in the returned execution command; ordinary
name-based wrappers do not advertise that guarantee.
The separate internal [original creation prerequisites](docs/creation-custody.md)
retain an explicit new birth and original association, with an exact-slot
retention primitive; they do not enable retained launches or upgrade existing bindings.
Use Bridge's protocol-36 `create <agent> --creation-key <key>
--provider-instance` to establish a replay-safe native session without
inventing a worktree or sending a first prompt. A missing binding or replaced
member refuses; neither resolution nor a new borrow clears the incarnation.
Destructive lifecycle probes that guest lock under its provider hold, both
before rescue and immediately before stop/removal/recreation; an active or
unreadable lock defers the action even without host admission or Copilot markers.
Before the destructive action, the provider closes guest admission under that
lock. Root-owned admission generations fence delayed Docker exec commands even
after their host wrapper dies; a later admitted host launch gets a new nonce
and cannot revive old launches or delayed helpers. Closure failure defers the
action. This does not change task ownership, token authority or lease duration.
The opt-in `test_dispatch_admission_gate.py` acceptance proof uses
`CE_TEST_DISPATCH_DOCKER=1` with a cached `node:22-bookworm-slim` image.
Its disposable guest is offline, read-only and capability-dropped; it verifies
host-wrapper death before guest lock acquisition, delayed-launch refusal,
monotonic helper fencing and a fresh admitted launch without changing deadlines.
Lock-protocol cutover waits for an existing legacy lock file to be released;
it never steals or deletes that file. The new `leases.lock` is a private
directory with its kernel lock inside, so old O_EXCL/unlink writers cannot
enter or unlink the persistent lock after cutover. Live legacy admission and
lifecycle writers must finish before new mutations are admitted. A crashed,
unidentifiable legacy lock file requires verified operator quiescence rather
than automatic age-based removal; no active wrapper is forcibly terminated.
Legacy records from another environment remain blocking even after heartbeat
expiry: cutover prevents their old writers from refreshing that heartbeat.
Quiesce them in their owning environment and run `lifecycle-clear` there.
Its cleanup-only transaction removes only locally verified-dead legacy records
that are also no longer live. An uncertain destructive hold remains live until
its fixed expiry even after its wrapper exits. Cleanup preserves foreign and
live records, so each environment can clean up
without opening normal admission prematurely. Expiry alone never proves
foreign legacy quiescence.
The provider's creation-time binding identity is pinned in both an
immutable Docker label and owner-private state. Missing, corrupt or mismatched
birth state is not an unused slot: preparation refuses it and requires
recreation instead of letting a surviving old worker consume a new task grant.

After establishing the worker's native session and obtaining its grant from the
coordinator, the trusted host calls
`dispatch-project <name> --expected-container-id <full-id>` with `binding_id`,
`owner_session_id`, `task_token` and `expires_at` on stdin. The native session
is write-once, including after an interrupted delivery. Only the credential
travels over stdin to a root-owned atomic file at
`/run/agent-dispatch/task-token`; the nonroot worker can read but not overwrite
it. Neither Docker configuration, retained binding metadata, launch argv nor
command output stores the token. Rotation preserves the same incarnation.
The host persists a monotonic attempted-expiry fence before delivery, separately
from acknowledged expiry. An interrupted publication cannot permit an older
grant to replace a potentially delivered newer one; equal-expiry retries remain
admissible without claiming a failed delivery succeeded.
Publication also holds a separate guest kernel lock and checks a root-owned
monotonic expiry fence before replacing the token. A guest helper surviving its
Docker client's timeout cannot overwrite a newer publication when it resumes;
contention fails explicitly so the trusted supervisor can retry.

The explicit launch projects only the pinned task/worker/endpoint and token-file
path, using an immutable absolute Bash executable outside writable mounts and
without shell startup files. The image must provide Bash, Node and `flock` outside its
writable surfaces, plus its own worker tools. No host signing key, shared bearer
or credential relay is projected. This channel does not issue grants, extend
task leases, schedule renewal, supply inference credentials or open a network:
the supervisor and coordinator still own those decisions. Automatic dispatch
placement and supervisor renewal are not yet integrated.

## Concepts

### Externally managed trusted members

An external deployment manager may own a trusted fleet's lifecycle while
agent-containers supplies sessions against its already running members:

```yaml
fleets:
  pilot:
    lifecycle_owner: external
    security_profile: trusted
    exec_user: vscode
    workspace_folder: /workspaces/project
```

The manager supplies `agent-containers.fleet=pilot`,
`agent-containers.security-profile=trusted` and
`agent-containers.lifecycle-owner=external` on each member. Configuration and
the live owner label must agree before trusted session use. Existing physical
instance, repository, lease, authentication and session checks still apply.
This mode does not enroll a manager, select a remote Docker endpoint, issue
credentials or establish remote-generation/auth-renewal parity.

`up` (including recreation), fleet start/down/removal, member start/stop/
unpause/removal and implicit start refuse external ownership. Missing or
stopped members require the selected deployment manager; no direct-Docker
fallback is offered. A live external owner label independently prevents
lifecycle mutation after configuration is changed or removed. Lifecycle
commands inspect ownership immediately before their effect and address the
observed full container ID, not a replacement sharing the same name.
Unlabeled members inherit external ownership by the configured fleet-name
prefix, including nonnumeric suffixes. An explicit managed fleet label still
distinguishes overlapping prefixes; an external fleet's numeric slot remains
protected even when labeled as another fleet.

The default `lifecycle_owner: agent-containers` preserves managed fleets.
Restricted fleets cannot select external lifecycle ownership. This is an
ownership contract, not a new security boundary or permission to bypass a
manager through a raw Docker command.

### Optional restricted SSH runtime

New installations use the **base** dependency profile: trusted OpenSSH and direct
restricted `exec` work without Paramiko. Only the optional restricted
`ssh-stdio` / `ssh-profile` bridge needs **provider-exec**. Select it explicitly
with the owning payload's installer:

```powershell
& <payload>\scripts\init.ps1 provision -InstallDir <resolved-runtime-root> -Profile provider-exec
```

```bash
bash <payload>/scripts/init.sh provision --install-dir <resolved-runtime-root> --profile provider-exec
```

Use the exact payload and installation root selected by this installation's
command/context, not another marketplace's payload or an ambient global command.
This is an explicit profile-provisioning operation, not a replacement for the
suite's ordinary unified update flow.

The installation's `runtime-profile.json` records durable desired state;
subsequent updates retain it. Existing installations without this record preserve
provider-exec when their selected runtime already contains Paramiko. Fresh
installs do not infer dependencies from whichever repository/config happens to
be the installer's working directory.

Both profiles include the declared local repository-policy package in their
explicit dependency targets, input fingerprint and installed-module health check.
It resolves from the selected payload/source closure rather than relying on a
package registry to provide that first-party library.

Each package version/profile has its own immutable `versions/<version>+<profile>`
slot. Switching with `-Profile base` / `--profile base` leaves older slots intact
and reuses an already-completed matching slot. Even `--force` never rewrites a
completed slot. A failed requested profile does **not** fall back silently: the
old active runtime remains selected and the saved intent is retried next time.
The deploy manifest records package version separately from runtime slot/profile.
Prepared-source suite updates preserve that distinction through the
[owned source-target planning contract](docs/runtime-source-target.md).
The payload ships the canonical lifecycle helper through `installerLifecycle`;
this does not change its existing dispatcher or publication policy.
Profile changes start no service, SSH listener, Docker container or fleet.
Helper packages are installed non-editably into that slot; health checks reject
code resolved from a mutable source checkout outside it.
Every selected profile uses the canonical seven-day dependency resolver against
the configured feed; unavailable UV or a selected dependency fails closed
without replacing the active slot. Windows keeps signed stdlib interpreter
copies without pip/setuptools seeding; unrestricted pip fallback is unsupported.
The staged profile coordinator runs in Python isolated mode and loads its
runtime helper from the same payload directory explicitly, so safe-path
settings and ambient Python modules cannot redirect that import.

Newly constructed slots also publish a versioned `executionContract` inside the
slot's own `runtime-profile.json`. The installer-owned
[`runtime_profile.select_execution`](scripts/runtime_profile.py) is a read-only
internal selection API: it uses the canonical completed-runtime selector, checks
the slot/profile identity and completion hash, and returns that exact immutable
interpreter with its declaration. It does not provision, activate, acquire a
retention lease, start a provider, or inspect a guest. A later mutable payload
shim is **not** that selected execution; final execution and continuing retention
must use the actual runtime owners.

Declaration schema `copilot-extensions.agent-containers.provider-execution`,
version `1`, contains a strict boolean `retainedCreation`. Both dependency
profiles currently publish `false`: Paramiko, readiness and instance-pinned
execution do not implement Bridge creation custody. Missing, malformed,
duplicate-key or unsupported declarations refuse this selection API without
effects. Previously completed profiles without the declaration remain reusable
for ordinary installation byte-for-byte; they are never retroactively stamped
as supporting a new contract. Re-select after a runtime change or rollback.
This is provider-first metadata, not an enabled retained-creation caller,
provider/guest admission, launch-lifetime retention or native qualification.

With an explicit installation-cell context, configuration loading validates the
Containers owner before using config overrides. Its optional knowledge-repo
fallback invokes only that cell's Agent Worktrees through the packaged native
peer boundary, never an ambient command. An absent peer is optional only after
owner validation; invalid receipts, blocked governance, failed probes, and
unbound required knowledge roots produce an error rather than default fleet
configuration. Without explicit context, legacy precedence and fallback remain
unchanged.

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
  this package in the bridge venv. Inventory uses one `docker ps` query without
  a separate version preflight; missing CLI, daemon and query failures remain
  errors, not successful empty inventories. `namespace-recreate` is the destructive,
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
  list, recent-message, session, refresh, and explicit claim-inspection operations
  are advertised, while
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

## Plugin preparation

Trusted container launches share the `plugin-resolve` install/copy/register
barrier with CodeSpaces. Bridge selects related-repository plugins and retains
that selection independently of the provider's legacy launch argv. Its headed
CLI handoff carries explicit `--stage-plugin` and `--plugin-root` inputs;
`--stage-plugin-disabled SOURCE` prepares the same payload but preserves its
disabled selection. Both staging flags are repeatable; plain source values
remain unchanged, including paths containing spaces or `=false`. Retained
provider metadata carries each selection as a `{source, enable}` row while
accepting older plain-source rows as enabled.
Standalone container CLI use does not depend on importing bridge's runtime.
All required remote installs and local copies complete before the execution
repository's `.github/copilot/settings.local.json` is merged and the child
starts. The target repository's own enabled remote plugins join that same
installation barrier. ACP also receives each effective directory explicitly; CLI discovers
the registered identities from repository settings, without duplicate external
directory entries. Explicit worktree mode targets that worktree's repository,
not the fleet's default checkout. Attached and detached native CLI attempts run
the idempotent plugin barrier before the remote reuse/create decision, even if
the host live-session registration remains fresh. Preparation failure blocks
creation; a genuine rejoin still reuses the running child without changing its
active bindings or replaying its seed. Guaranteed surviving Session Host
reattachment skips preparation. Restricted fleets remain outside host plugin
projection.
Attached CLI launch binds its child login shell to the exact checkout selected
for preparation, after login profiles run. A worktree resolver's returned path
wins over conventional workspace metadata; failure to enter it prevents launch.

See [`plugin-resolve`](../../libs/plugin-resolve/README.md#trusted-venue-preparation)
for settings ownership, isolated installation cache, retained snapshots and
selected-asset handling. Manifest companions outside the eligible selection are
diagnostics for caller-owned execution routing, not permission to propagate
additional plugins.

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
agent-containers rescue-capture <fleet> [--json]
                                      # capture running restricted members' session
                                      # evidence non-destructively (no stop/remove)
agent-containers borrow <effort> [--fleet <fleet>] [--container <name>]
                                      # lease a free/specific container -> prints name
agent-containers release <target>    # release by container or effort name
agent-containers leases              # show active leases
agent-containers lifecycle-clear [name]
                                      # clear only expired/dead admission records
agent-containers exec <name>         # run the ACP launch command (testing)
agent-containers copilot <name>      # attach a trusted-container CLI session
                                      # (--ttl-seconds applies here only)
agent-containers copilot <name> --detach [--seed-file task.md]
                                      # start/rejoin an observable background CLI session
agent-containers copilot <name> --stop
                                      # stop that detached session and release its keeper hold
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

Detached CLI-mode sessions are trusted-profile only. `--detach` reserves the
host bridge's CLI-mode slot under `<identity>@<container>`, provisions
agent-bridge registration credentials inside the container, starts a small
host-side forward keeper for the bridge and credential-relay reverse forwards,
runs `agent-worktrees embody --json` in the container workspace without a PTY,
and prints a JSON handle with `session_id`, `scope_id`, and ready-made
`status`/`observe`/`nudge`/`attach`/`stop` commands. `--stop` kills the
container tmux session, verifies it is gone, releases that session's keeper
hold, stops the shared per-container keeper only when no other session still
holds it, and deregisters the exact live-session row. `--ttl-seconds` applies
only to attached mode; detached sessions use their fixed reservation lease.
Extra Copilot CLI flags can be repeated with
`--copilot-arg`; use `--seed-file -` for long or multi-line prompts. A new
session starts on the caller's own model, reasoning effort, and context tier
(from `~/.copilot/settings.json`); an explicit `--copilot-arg=--model=...`
wins and `AGENT_CODESPACES_MODEL_PROPAGATE=0` opts out.
`--ref-file PATH` (repeatable, `--detach` only) copies an operator file outside
the checkout to `~/.agent-bridge/refs/<batch>/` in the container and names it to
the worker (seed for a new session, a message on rejoin).

## Configuration

`containers.yaml` (looked up via `$AGENT_CONTAINERS_CONFIG`, then
`<repo>/.copilot-extensions/agent-containers/containers.yaml` -- the
canonical in-repo location, matching every other plugin's own
`.copilot-extensions/<plugin-id>/...` convention, see
[`docs/configuration.md`](../../docs/configuration.md) -- then the legacy
bare `./containers.yaml`, which remains readable, then
`~/.agent-containers/containers.yaml`, then an optional agent-worktrees
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
copilot_cli_account: inference-user   # intended host Copilot login; no gh fallback
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

### Separating Copilot CLI inference identity from git/PR identity

Trusted launches default to `copilot_auth_mode: host-copilot`. This mode
requires the intended host Copilot account and its current token, independently
of the in-container `git`/`gh` identity. An unset account or missing source
refuses the launch, even if `GH_TOKEN`, `GITHUB_TOKEN`, ambient inference
credentials or an existing container login are available. No arbitrary account
is selected to repair an unset policy. Set
`copilot_cli_account` (globally or per-fleet) to the GitHub account whose
own Copilot CLI login on THIS HOST should back `COPILOT_GITHUB_TOKEN`,
independent of whatever GH_TOKEN carries:

```yaml
schema_version: 2
copilot_auth_mode: host-copilot       # default; no repository-token fallback
copilot_cli_account: inference-user   # global default for every trusted fleet
fleets:
  myrepo:
    repo: your-org/your-repo
    devcontainer_path: D:/Src/myrepo-devcontainer
    forward_gh_token: true   # still true: git/PR identity stays GH_TOKEN
    # copilot_cli_account: some-other-account   # per-fleet override, if needed
```

This reads the configured account's **live** Copilot CLI login from this
host's own `~/.copilot/config.json` (`authTokens`/`loggedInUsers` --
completely separate storage from `gh`'s own OAuth store, so `gh auth
status` is unaffected) at every launch -- never the vault, never minted or
refreshed by agent-containers itself. The operator keeps it current simply
by running `copilot login` as that account on this host; agent-containers
only ever reads whatever is currently there, and fails closed (refuses to
launch, naming the exact fallback command below) if that account has no
live login yet. An unset `copilot_cli_account` is an admission failure in the
default host-Copilot mode, not a request to use repository credentials.
Restricted fleets never receive this (or any) host credential,
same hard boundary as `forward_gh_token`.

Compatibility is explicit: `copilot_auth_mode: legacy` (global or per-fleet)
opts into the old native/container/repository authentication behavior. It does
not promise host-Copilot identity or inference-token isolation and is never an
implicit fallback from a failed host-Copilot launch. A per-fleet mode overrides
the global mode; an invalid mode refuses configuration. Restricted inference
does not use either trusted authentication mode and remains unchanged.

Existing version-1/unversioned configurations are migrated compatibly to an
explicit mode: already selected Copilot accounts retain `host-copilot`; other
old configurations retain `legacy` with a visible warning. The loader migrates
in memory only; the existing authorized installer migration stamps the
machine-local copy, never a repository copy. This does not make legacy the
default of a new version-2 configuration or permit fallback from host mode.
To adopt the new isolation contract deliberately, set `schema_version: 2`,
`copilot_auth_mode: host-copilot`, and the intended `copilot_cli_account`.

For an explicit `copilot_cli_account`, trusted ACP, Session Host child, and
attached/detached CLI launches re-read that existing host source for each launch. Only
the native Copilot process receives the selected inference token: a private,
one-use launch file is consumed by a narrow executable wrapper, not sourced
into the Session Host, `embody`, tmux, container, or session environment.
Shared launch inputs and companion authentication use private mode-0700
directories under `/dev/shm/agent-containers-<user>/launch`, even when HOME
is disk-backed. The writer verifies tmpfs, ownership, permissions and
non-symlink ancestors before reading credential input; an unavailable or
unsafe RAM destination refuses the launch without a disk fallback. Final
files are created with mode 0600 and exclusive, no-clobber writes. Companion
authentication must share the validated RAM-backed launch owner directory.
Promotion into a companion auth file is an exclusive same-filesystem link,
followed by removing its staging name; an existing destination refuses rather
than being replaced. Cleanup and abandoned-input reaping validate both storage
roots, including filesystem, ownership, mode and non-symlink ancestry, before
removal. Missing roots remain an idempotent cleanup case.
The credential-free executable wrapper and nonsecret lifecycle receipts live
in a separate private `/tmp/agent-containers-<user>/launch` directory because
Linux commonly mounts `/dev/shm` with `noexec`. Wrapper content contains only
the RAM credential path and variable name, never credential bytes. Cleanup
and the abandoned-input reaper cover both directories together.
Executable scopes are created explicitly with mode 0700 rather than relying on
umask, so inherited default ACLs cannot widen their permissions.
Existing launch inputs in the former home-directory location are not migrated
or swept by the new RAM-only reaper.
Successful CLI startup must confirm the native child consumed its file before
cleanup removes the executable wrapper; an `embody` return alone is not proof
of native startup. Explicit create-or-resume receipts distinguish a new native
child from rejoining a running session: a rejoin cleans unused launch inputs
without claiming the existing process's authentication was refreshed.
The attended CLI writes that nonsecret result before attaching.
SSH failure, interruption, or a missing receipt does not prove that a detached
child was never created. Those ambiguous outcomes retain the scoped launch
inputs for the bounded abandoned-input reaper; only confirmed consumption,
an explicit resume receipt, or failure before submitting the remote launch
allows immediate cleanup. Receipt handling is independent of the SSH exit code.
Session Host authentication lives in the existing launch handle's companion
directory, so the original `session-host-cleanup --remote-env` call removes it
even when an older bridge knows no additional authentication response fields.
Consumption has its own explicit receipt; token-file absence alone is not
success. Abandoned UUID-scoped inputs and companion directories are reaped
after a conservative one-hour staging lease, while fresh inputs and matching
active PID/start-time bindings are preserved. Unknown names and symlinked
scopes are not swept.
Copilot receives `--secret-env-vars=COPILOT_GITHUB_TOKEN`, which strips that
value from its normal shell and MCP child environments. Existing `GH_TOKEN`,
`GITHUB_TOKEN`, Git credential helpers, and relay identities are independent
and remain available to downstream tools.

The target's native `copilot --help` must confirm `--secret-env-vars` support
before staging authentication. Missing source tokens or unsupported CLI
capabilities refuse the launch; they never fall back to the Git identity.
ACP commands must directly execute PATH-resolved `copilot` (including the
target-preference launcher template), optionally after one `cd ... &&` and/or
`exec`, not an absolute executable override, custom command or shell pipeline.
Attached/detached launches
require an agent-worktrees runtime accepting `--copilot-process-launcher`;
an older runtime rejects that option before launching. Direct `copilot`
templates and normalized setup are supported; custom executable templates and
configured `copilot_path` overrides refuse rather than bypass isolation.
Caller-supplied `--secret-env-vars` overrides or `--` separators are likewise
refused for these authenticated launches.

This is accidental-inheritance isolation in a **trusted** venue, not a hard
credential-custody boundary: trusted agents can access their inference
credential. It neither changes the existing source parser nor proves a
vendor storage/expiry/refresh schema. Synthetic launch tests establish
per-launch source rereads, child argv/environment isolation and fail-closed
behavior, not live OAuth or inference success. Restricted native-proxy
launches remain separate and never receive a host inference token.

**Worst case: no live login exists anywhere for the configured account yet.**
Run the device-code flow INSIDE a running container of the target fleet
instead of on the bare host (useful when the host itself can't run an
interactive OAuth flow, or you'd rather not touch the host's own default
login):

```bash
agent-containers copilot-login myrepo-1 --account inference-user
```

This execs into the container, runs `copilot login --device-code`
interactively (follow the printed URL/code in your own browser), then reads
the resulting token back out of the container's own config.json and
imports it into the **host's** `~/.copilot/config.json` -- so every future
launch of this (or any other) fleet configured with that same
`copilot_cli_account` finds it the normal way, with no repeat in-container
login. This command is manual/on-demand only; it is never triggered
automatically by any launch path.

Imports use owner-only temporary files, file fsync, atomic replacement, and
best-effort containing-directory fsync where supported.
Owner permissions are restored before writing even when the process umask
masks owner bits; Windows and ACL-backed filesystems retain their existing
best-effort mode enforcement. Concurrent
`copilot-login` imports to the same config serialize their read/merge/write
transactions through `.config.json.copilot-login.lock` beside the config.
The OS releases the lock when its holder exits; keep the lock file in place.
Contention waits at most ten seconds before failing explicitly. This is a
cooperative import lock, not synchronization with unrelated Copilot CLI writes.
Unreadable existing configs are not treated as missing or overwritten.
If restoring or removing the container's temporary login config fails, the
command warns that credentials may remain and names the affected container.

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
`--allow-all --experimental` fallback.

Trusted SSH Session Host fleets may opt into the
[target-local preference launcher](../agent-bridge/docs/session-preferences.md#target-local-container-launcher)
by placing `{{target_preference_launcher}}` at the final direct execution point
of their operator-owned `acp_command`. Authentication/proxy/workspace setup stays
before the token. The provider advertises wrapper-authority v1 only for that
explicit template and binds preparation to the discovered container instance
and selected execution user. Appended caller flags cannot enable it.
This does not change the public caller-settings default, expand a sandbox,
provision target settings, or support the restricted provider-exec route.

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

### Opt-in native inference for restricted ACP

An image-backed restricted fleet may opt into a host-controlled native
inference broker without receiving a host credential:

```yaml
fleets:
  inference-worker:
    image: example/copilot-agent:pinned
    security_profile: restricted
    workspace_folder: /workspace
    exec_user: agent
    acp_command: "cd /workspace && copilot --acp --stdio --allow-all"
    network: inference-only
    native_inference:
      endpoint: http://inference-broker:8080
      reference: openshell:resolve:env:EXAMPLE_INFERENCE
```

The network must pass the existing Docker-internal, single-network policy.
The endpoint is an HTTP(S) origin with a single-label broker name, not an
arbitrary URL, localhost, IP address, credential-returning relay, or host
gateway. The reference is a public identifier in the exact
`openshell:resolve:env:NAME` form, never a GitHub or model-session credential.
Both fields are required; unknown fields and generic environment overrides
are rejected. Changing either field changes the restricted policy fingerprint.

Only the ACP exec process receives `COPILOT_API_URL` and
`COPILOT_GITHUB_TOKEN` containing this reference. The provider rereads the
profile at launch; it does not place it in the bridge spawn spec or bake it
into the image/container environment. The generic sensitive-environment guard,
restricted SSH adapter, host-token prohibition, and relay prohibition are
unchanged. Trusted and restricted-local launches remain unchanged when this
profile is absent.

This is a launch seam, not a broker or a live-service authorization. The
operator supplies and secures the broker, owns upstream authentication,
enforces actual wire-model/route admission, cancellation, budgets and revocation,
and keeps upstream credentials out of responses and logs. A public reference
is not caller authentication: use an exclusively admitted network/assignment.
Parent and child model selection still belongs to ACP/runtime configuration;
the broker must enforce the finite wire-model allowlist independently.
Pin and test the CLI against the broker before enabling live inference.

### Host-backed persistence and systemd for trusted, image-backed fleets

An `image:`-backed `trusted` fleet otherwise has no persistence mechanism at
all (`code_model: mount` remains unimplemented) and no capability for a
containerized `systemd --user` instance (its own maintenance timers need
`CAP_SYS_ADMIN` + a writable `/sys/fs/cgroup`, neither granted by `trusted`'s
defaults). Two opt-in fields close that gap -- both are rejected on a
`restricted` fleet, or alongside `devcontainer_path` (`validate_restricted()`
raises), since a host bind-mount and `CAP_SYS_ADMIN` both defeat the
restricted containment contract, and a devcontainer-backed fleet's launch
path never consumes these fields at all:

```yaml
fleets:
  self-maintaining-worker:
    image: example/agent:latest
    security_profile: trusted
    workspace_folder: /workspace/myrepo
    exec_user: node
    size: 2
    host_workspace_path: /srv/agent-containers/workspaces/self-maintaining-worker
    host_home_path: /srv/agent-containers/home/self-maintaining-worker
    home_folder: /home/node        # the image's own HOME for exec_user -- not
                                    # probed automatically for trusted fleets,
                                    # so it must be declared alongside
                                    # host_home_path (both or neither -- an
                                    # incomplete pair is rejected)
    systemd_capable: true
```

`host_workspace_path`/`host_home_path` are **parent** directories -- each
fleet member mounts its own subdirectory, keyed by its unique container name
(e.g. `self-maintaining-worker-1`, `self-maintaining-worker-2` for `size: 2`
above), onto `workspace_folder`/`home_folder` respectively, so members of a
multi-container fleet never collide on the same host path. Each member
subdirectory is created and `chown`'d to `exec_user`'s real uid/gid (resolved
the same way `restricted` already does) before it is ever mounted -- a
missing bind-mount source would otherwise be auto-created by Docker owned by
the daemon (root), leaving a non-root `exec_user` unable to write to its own
"persistent" workspace/home on first launch. `chown` is POSIX-only; a
Windows Docker Desktop host only gets the directory created (see the
platform note on `_ensure_owned_dir`).
`systemd_capable: true` (a real boolean only -- a quoted `"false"` or other
non-boolean value is rejected rather than coerced) adds `--cap-add
SYS_ADMIN`, writable/executable `/run` + `/run/lock` tmpfs mounts, an
allocated tty, `container=docker`, and **`--user root`** (PID 1 -- systemd
itself -- must boot as root regardless of the image's own default `USER`;
`exec_user` only governs LATER `docker exec` calls for actual work, never
the entrypoint process -- systemd drops to per-service users for real
workloads via its own unit files, exactly like a normal machine's init),
then launches the container via a wrapper that remounts `/sys/fs/cgroup`
read-write before `exec`'ing `/lib/systemd/systemd` as PID 1 (in place of the
historical `sleep infinity` placeholder) -- Docker's default `/sys/fs/cgroup`
mount is read-only even under `trusted`, and a raw host bind-mount of it does
NOT work (a cgroup-namespace path mismatch produces "No such file or
directory"); an in-container remount at launch is the fix that actually
works. **The image itself must provide `systemd`, `systemd-sysv`, and
`dbus-user-session`** -- this plugin only wires the launch, it does not
install systemd into the image. Once running, `loginctl enable-linger <user>`
plus that user's own `systemctl --user ...` registers and runs real
`.timer`/`.service` units exactly as on a normal machine.

The per-member uid/gid used to `chown` `host_workspace_path`/`host_home_path`
subdirectories is resolved via a trusted-specific probe (not the
restricted-only `_image_user`, which requires real `memory`/`cpus`/
`pids_limit` values and rejects a root `exec_user` -- both wrong for
`trusted`'s normal unset defaults and its historical allowance of a root
exec_user). The resolved member subdirectory is also validated to stay
beneath the configured parent path -- `name` comes from the operator-
configurable `name_prefix`, so a prefix containing `../` is rejected before
anything is created or `chown`'d.

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

**Profile migration (`restricted` -> `trusted`):** when a fleet's
`containers.yaml` entry relaxes its `security_profile` while a member built
under the old `restricted` profile is still live, `up --recreate` and `rm`
both detect that drift and recreate/remove the member -- routed through the
exact same rescue/liveness/lease pipeline above (a restricted-built member
never gets a lighter-weight path just because the fleet's current config
moved on), with only the "does it still match the CURRENT fleet's
conformance policy" check skipped, since that check assumes the container is
staying restricted. Without `--recreate`, `up` raises instead of silently
leaving the old member untouched.

`rescue-capture` runs the same admission/lease/liveness gating as `down`/`rm`
against every running restricted member of the named fleet, but performs no
stop or remove afterward -- the container keeps running untouched. This is
for a fleet that is deliberately never recycled (an always-on service), where
`down`/`rm`'s rescue-on-destruction would otherwise never fire: it lets an
operator (or a periodic timer) shuttle a live member's session evidence out on
a schedule instead. A member with an active/unknown session, an active lease,
or a paused state is deferred, same as `down`/`rm` -- capture-only never
unpauses a container just to probe it. Unlike `down` (which reports an
already-stopped member as unchanged) or `rm` (which follows the
stopped-instance evidence/removal path), a non-`running` member is deferred
immediately with "nothing to capture": capturing is only ever a live,
in-place operation, never a fallback onto stale stopped-instance evidence.
`--json` reports per-member `captured`/`deferred` results the same shape as
`down`'s.

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

`up`, `down`, `rm`, and `rescue-capture` accept `--json`; their result includes
created/stopped/removed/captured, unchanged, rescued, abandoned, and deferred
members. Any deferred member returns the established busy exit code `75`.
Restricted `exec` blocked by a lifecycle hold uses the same exit code.
Docker command timeouts are normalized into per-member deferred results:
liveness timeouts become unknown, while stop/remove/confirmation timeouts leave
the hold fail-closed and do not abort reconciliation of sibling members.
Typed rescue/generation/pin failures follow the same per-member rule across
`up`, `down`, and `rm`. `agent-codespaces`' peer non-destructive capture verb
(`sync-sessions`, session-rescue-parity Phase 3) uses the same busy exit
code `75` and a `captured`/`rescued`-equivalent `ok`+`deferred` result shape,
scaled down to one target instead of a fleet -- see that plugin's own
README for the exact field names.

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

## Native launch-storage tests

Run `tools/run-plugin-tests.py agent-containers --test-file tests/test_launch_storage.py`
from the repository root. These Linux regressions inspect the actual filesystem:
`/tmp` is not necessarily disk-backed. Disk-only refusal cases explicitly skip
when their temporary directory is on tmpfs; complete disk-refusal qualification
also needs a non-tmpfs run. RAM, symlink and permission witnesses remain active
on the tmpfs layout. A prerequisite skip is not a passing disk witness.

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

Runtime-home and broker-endpoint paths are normalized lexically against the
selecting process's cwd before instance reservation or admission. Broker child
arguments remain absolute when its cwd changes to HOME. This does not resolve
filesystem links: redirected runtime roots still fail custody checks.
Keeper children inherit the home belonging to their selected store, even if
the launcher's cwd or ambient home override changes afterward.

The localhost SSH broker stops intake and settles accepted connections, its
native Docker CLI children, and every control/stream worker before releasing
daemon admission, including interrupted or failed startup. Windows children
use a strict, retained Job that confirms descendant retirement even after the
CLI leader exits. POSIX children retain the broker's existing process group
and test/lease containment: cleanup reaps the exact direct child and waits for
both pipe pumps. It does not adopt or signal a guessed descendant group; an
inherited pipe held by a descendant keeps admission live until that pipe closes.
Cleanup retries interrupted retirement rather than declaring unfinished work
complete. Endpoint removal still preserves another process's replacement.

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
