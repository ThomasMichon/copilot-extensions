# agent-ssh

The **SSH connectivity layer** for the agent fabric. It provides a standalone
`agent-ssh` CLI that renders machine-name SSH profiles, verifies reachability,
and defines the transport-provider contract used by direct and tunnel
transports.

The payload carries canonical installer-engine Python helpers for the shared
installation driver's fixed legacy `stamp`/`provision` recipe. Those files are
maintained by `tools/sync-installer-engine.py`, not a separate SSH copier.
The legacy session bootstrap packages that same standalone foundation through
the canonical dependency materializer. First stamp and manifest-selected drift
submit fixed native actions before holding any outer provisioning lock. The
15-second hook returns exactly `{}`; stderr identifies its launched supervisor
and log, not durable acceptance. The ordinary request receipt and result in that
log remain authoritative. Healthy and namespaced hooks remain no-ops; companion
registration and session guidance keep their independent hooks.

Managed publication, disabled self-provisioning, pinned reads, nested owners,
unavailable sources and unsupported retained/custom sources refuse before queue
or installation writes. Drift never replaces the manifest's selected source
with the current hook payload. Native service-less actions/options and
transaction custody are unchanged. Checkout stamping retains the foundation's
complete local dependency graph so later snapshot provisioning stays
self-contained; this is packaging, not adoption of the snapshot publisher.
The generated legacy primary and payload commands also use this packaged driver.
Their fixed SSH-only `stamp-provision` request holds the same queue custody
across stamping and provisioning from the exact selected snapshot, including
caller loss between those steps. The primary delegates to its own tagged
snapshot dispatcher rather than today's installed payload. Healthy commands
still dispatch directly with their original arguments and exit outcomes;
cold custom roots/cells and missing/skewed support refuse without an outer
provision lock, status writes or a foreground fallback. Explicit installers and
network-backed retained-source qualification under #5379 remain separate.

The installer's `uninstall` action serializes with install/update. It removes
only explicitly named runtime code and selectors, retaining configuration,
authentication, forward keepers, host/workspace settings, warning state, unknown
entries, transaction locks and `.install-stage` coordination. It requires a
matching deployment owner and an installer-engine with entry-list removal.
Linked roots and configured SSH reconnect closures refuse before removal;
unowned binstubs remain untouched. The canonical uninstall engine holds runtime, snapshot and
redirect-publication custody throughout finite native cleanup. Persistent
lease registries remain in place and do not imply contention; actual held
leases refuse admission. These refusals do not disconnect SSH or stop keepers.
Normal self-staging reap
removes dead-owner stages on later invocations. Its preview takes no transaction lock and removes no contents;
normal transient payload self-staging still applies.

## What you can do with only this plugin

`agent-ssh` does not require a harness, daemon, or sibling plugin. From a
registry plus a transport `module.yaml` it writes one managed SSH fragment:

```powershell
agent-ssh emit-profile registry.yaml --module transports\direct\module.yaml
agent-ssh gateway-profile selected-gateway.json --json
agent-ssh gateway-provider --policy policy.json --directory admitted.json --machine worker-a --environment linux --gateway chosen-gateway --identity-file <absolute-key> --public-key-file <absolute-public-key> --json
agent-ssh gateway-authorization --policy policy.json --directory admitted.json --machine worker-a --environment linux --public-key-file <absolute-public-key> --state-dir <dedicated-absolute-directory> --json
agent-ssh doctor
agent-ssh verify --timeout 8 my-machine
agent-ssh explore my-machine --json
agent-ssh mesh-status
agent-ssh session-snapshot --json
agent-ssh fleet-targets registry.yaml --module transports\direct\module.yaml --provider-instance lab-ssh --target my-machine
agent-ssh refresh-mesh --json
agent-ssh copilot-config set my-machine --workspace /workspaces/repo
agent-ssh copilot my-machine
agent-ssh copilot my-machine --detach --seed-file task.md
agent-ssh copilot my-machine --stop
```

The profile core manages SSH aliases. Once `ssh <name>` works, sibling plugins such
as agent-bridge or agent-codespaces can use that OpenSSH surface, but agent-ssh
does not import their runtimes or require them to be installed.

On Windows, `verify` and `refresh-mesh` resolve the selected client's profile and
use the shared owned SSH launcher, including its jump/proxy chain. The transport
wait is finite, and timeout/cancellation awaits the existing cleanup owner.
Configuration and cleanup errors remain explicit rather than being classified
as an unreachable target. An unpaired older shared library refuses before
transport instead of falling back to the legacy launcher.
Windows probe timeouts must be positive integers; zero does not request an
unbounded subprocess.
POSIX probe behavior is unchanged.
See [native qualification](../../TESTING.md#optional-owned-windows-jump-probes);
source changes do not silently replace an installed runtime.

### Session topology and profile evidence

`session-snapshot` captures fresh declared aliases and this client's managed
fragment coverage without running OpenSSH, evaluating `Match exec`, probing a
host, emitting profiles, persisting warnings, or provisioning a runtime.
`--path` and `--config-d` select explicit topology and fragment inputs. Without
`--path`, existing caller/repository discovery wins; only when no caller source
is selected may an available Worktrees peer resolve an external state root.
An invalid selected source does not fall through to a different topology.
In an installation cell the optional peer is resolved in that same cell, never
through ambient `PATH`; absent, unbound and refused peers are not an empty mesh.
Worktrees remains optional for standalone use.
Its bounded lookup uses the existing caller-lifetime process owner, including
stream limits and descendant cleanup on timeout or snapshot-caller death.

Coverage distinguishes generated, unattributed, missing, refused and
indeterminate fragments. It does not inspect the effective OpenSSH include
chain or claim route/connectivity health: both remain `unobserved`, and
`ssh.ready` remains declared state. A generated fragment can exist without
being selected by OpenSSH.

The output-free session-start writer combines the attributable command catalog
with a bounded snapshot in the exact session's guidance file. It uses the
payload's existing runtime with self-provisioning disabled; an absent, older
or refused runtime produces an explicit unavailable status. Resume replaces
the prior snapshot. Contributor timeouts retire the owned process group on
POSIX or strict, non-breakaway Job on Windows, including nested runtimes.
The Windows writer loads the process primitive from this payload's shipped
`agent-procutil` dependency (or the canonical in-repository development source),
not an ambient Python package. It does not install that dependency at launch.
If the combined 4 KiB budget is exceeded, a fitting command
catalog is preserved and snapshot omission is explicit. Run the catalog's
`session-snapshot --json` command for the full current report; use the
`agent-ssh` skill for diagnosis and route repair, not automatic launch-time repair.

### Gateway transport

The supported [Gateway transport](docs/gateway-transport.md) projects explicitly
approved peer names, finite reverse-listener ports and public host pins through
an existing Gateway SSH alias. It retains source-independent local inputs and
uses the same profile writer; it does not enroll peers, fetch keys, open a
connection or start another controller.

The optional `fleet` extra adds the
[Fleet policy/public-directory provider](../../fleet-manager/docs/gateway-ssh-provider.md).
`gateway-provider` previews independent-direction filtering; `--apply` uses
the same writer and refuses superseded or equal-revision/changed directories.
Missing SDK support is actionable, not a fallback to unfiltered peer routes.
Ordinary standalone profile commands remain SDK-free.

The same optional extra supplies `gateway-authorization`: a read-only preview,
or explicit `--apply` of a dedicated target-local `AuthorizedKeysFile` for a
separately owned sshd. It grants only source-outbound/target-inbound logins
selected by the same public directory and policy; forwarding remains disabled.
It does not edit the user's ordinary key file, enroll a Gateway, fetch a secret,
start a listener, or create a reconciliation loop. See the
[native authorization contract](docs/gateway-transport.md#target-local-login-authorization).
On Windows the directory remains current-account-owned with its protected
user/SYSTEM DACL. Files may retain the OS's privileged default owner under an
elevated token, but their closed user/SYSTEM grants are unchanged and still
verified; foreign owners or extra grants are not adopted or repaired.

The installed `agent_ssh.gateway_sshd` module is the native foreground listener
entrypoint for an existing target-local lifecycle owner. It verifies the real
login principal and admitted host pin, consumes that dedicated authorization
file, and keeps its native child within the owner's process lifetime. It is
not a new supervisor or complete Gateway setup installer. See
[native listener integration](docs/gateway-transport.md#native-listener-integration).

For an already authenticated
[Fleet SSH service directory notification](../../services/ssh/README.md),
`agent_ssh.gateway_directory.apply_update` is the fixed local consumer selected
by the machine's existing Fleet setup/lifetime owner. It verifies the local
policy/controller and local public key, then uses the same profile builder and
target-local authorization writer. A later admitted peer therefore gains both
its strict-pinned route and the appropriate login-key grant without a service
restart. Each writer retains its existing monotonic revision and conflict
checks; a partial write fails visibly and does not acknowledge convergence.
No new operator command, listener, private-key transfer or background owner is
introduced. Profile-only emission is not a complete native authorization update.

`fleet-targets` is the read-only static-machine fleet driver. It uses the SAME
normalized registry and matching transport recipe as `emit-profile`, validates
both through the emitter, and returns an `agent-fleet.driver-snapshot` schema-v1
record for only the explicit `--target` selections. Target IDs preserve canonical
registry spelling; selection is case-insensitive and duplicate/unknown names
fail. `--provider-instance` explicitly qualifies this driver's identity, while
the source revision hashes the exact registry/module snapshot. It is metadata,
not an enrollment credential or authority.

This command never probes SSH, writes a profile, executes a transport, starts a
tunnel, enrolls a connector or discovers every host automatically. `configured`
and capability `ssh` describe a declared route, NOT verified connectivity or
service readiness. Creation, deletion, leases and bootstrap are not offered.
Only names/capabilities and a source digest are emitted: proxy recipes, private
keys, file paths, host addresses and credentials stay local with their owner.

`copilot <ssh-target>` attaches a real interactive Copilot CLI session in this
terminal through the SSH target, matching the venue contract of
`agent-codespaces copilot <name>` and `agent-containers copilot <name>`.
Re-running the command re-attaches the same remote tmux-backed anchor session;
if another process already holds the bridge reverse-forward route, the attached
command reuses that route instead of trying to bind the same remote port again.
`--detach` starts or rejoins the session in the background without taking over
the caller terminal and prints a JSON handle with `status`, `observe`, `nudge`,
`attach`, and `stop` commands; `--stop` verifies the detached session is gone and
releases its bridge forward. The remote machine must already have `bash`, `tmux`,
Python 3.10 or newer, `copilot`, `agent-worktrees`, and the `agent-bridge` Copilot plugin installed.
Windows SSH targets are not supported yet; run the orchestrator on that machine
and use local `agent-worktrees embody` there.

The remote checkout is resolved fail-closed: explicit `--workspace` first, then
the agent-ssh-owned host config written by
`agent-ssh copilot-config set <ssh-target> --workspace /path/to/checkout`
(`~/.agent-ssh/copilot-hosts.json`). The command does not guess by scanning the
remote machine; if neither source is present, it fails before launch with
instructions to pass or configure the workspace. `--ttl-seconds` applies only
to attached mode; detached launches use their short fixed launch reservation
and release it after registration. A new detached session starts on the caller's
own model, reasoning effort, and context tier (from `~/.copilot/settings.json`);
an explicit
`--copilot-arg=--model=...` wins and `AGENT_CODESPACES_MODEL_PROPAGATE=0` opts
out.
`--ref-file PATH` (repeatable) copies an operator file outside the checkout to
`~/.agent-bridge/refs/<batch>/` and names it to the worker (seed for a new
session, a message on rejoin).

Both attached and detached launches use the existing shared native launch
owner. Before starting Copilot, the SSH channel streams repository-scoped
plugin preparation into the actual anchor checkout. Repeatable
`--stage-plugin <name@marketplace>`, `--stage-plugin-disabled`, and
`--plugin-root` inputs preserve controller selection, target-native sources and
explicit target disables. Local sources are reflected, Git origins remain Git
declarations, and registered target-owned absolute sources rebind to the
receiver checkout. Plugin registration writes repository `settings.local.json`,
not user-global enablement. CLI loads the merged repository settings without
repeating those identities as `--plugin-dir`.

The host Bridge must be reachable for either launch mode. Seeds are never
typed into a pane: the owner requires a native deferred-seed receipt, then
admits the seed through Bridge's complete prepared-plugin readiness gate.
Attached mode only attaches the existing tmux session after that admission.
A rejoin does not replay the original launch task. An unknown admission keeps
the native session, forward hold and separate durable delivery journal; repeat
the same command to reconcile the original operation before submitting new
work. Keeper replacement does not discard that journal. Older owners without
the receipt and installed-only payloads without native marketplace origins
fail explicitly, without an ACP fallback or plugin reload.

On POSIX, the delivery directory is made owner-only before every journal
access, including existing directories. A verified `--stop` archives the exact
pending operation under its private `retired` directory before removing its
active entry; failed stop or changed ownership retains the journal. A later
launch can start fresh without losing the stopped operation's evidence.
Before terminating a pending launch, stop captures its original hold and
timestamp. After verified termination it conditionally releases that hold
before retiring the journal, so startup grace cannot carry an orphan hold
into an immediate fresh attachment. Unrelated or subsequently refreshed holds
are preserved; hold-cleanup failure leaves the active journal recoverable.
Recovery updates and clears compare the complete original entry under the
journal lock, so a concurrent retry cannot overwrite or erase a newer operation.
The full expected-repository fence runs before recovery as well as preparation.
The forward stays tied to the live launcher throughout preparation, then
transfers to mux liveness; an attached client subsequently transfers its hold
to that client's PID and releases the refreshed hold when attach exits.
The delivery journal retains that hold's identity: recovery reacquires the
same hold instead of creating an additional attached hold, including across
repeated unknown admissions. The eventual attached exit releases the original
hold while preserving unrelated sessions. A journal without its original hold
identity is refused rather than guessing which hold to retire.
An existing, probe-confirmed live mux hold stays mux-backed during rejoin, so
a failed reservation or preparation cannot leave it tied to an exiting caller.
If the old mux probe is indeterminate, acquisition may temporarily use the
launcher PID; a pre-handoff failure restores the exact original mux hold and
its confirmation history under lock, only while the caller still owns that
transition. A changed owner is never overwritten.
Stop refuses a scope that still has a launcher-PID hold rather than treating
that liveness token as a tmux target or releasing native ownership.
Launch, recovery and stop also share a controller-local, cross-process lock
for the resolved native scope. A competing operation fails promptly without
changing holds or reservations, including while attached preparation is active.
The lock covers final cleanup and journal retirement but not the interactive
attach lifetime; unrelated scopes and the keeper's monitoring lock stay independent.
Graceful Ctrl+C is deferred from before receiver-thread startup until the
bounded receiver returns, then the previous signal handler is restored and
the interruption propagates. POSIX preparation transports start in a separate
session so the terminal's signal group cannot independently kill SSH while
the caller is waiting for receiver cleanup. This is not a forced-process-kill
or lost-host recovery guarantee.

`namespace-target-repo <ssh-target>` is the read-only query Bridge uses to
select related plugins from the actual remote workspace's configured Git
remote. The native owner's `get git-remote` query supplies the remote name
(default `origin`), including upstream-only checkouts; an owner that lacks this
query fails explicitly rather than silently selecting no related plugins.
Filesystem origins use distinct, case-sensitive keys, not GitHub slug keys.
The query does not infer a repository from the SSH alias. Bridge forwards that selection and
the controller's current checkout before its fallback roots, and fences the
launch against a changed repository. SSH CLI remains anchor-only:
`--worktree-id` is not silently redirected to an anchor.
Machine-only related entries are matched through the registered project's
full Git remote, never a basename guess; the full remote coordinate still
fences launch, and ambiguous project mappings are refused.
An explicit expected-repository fence also applies to stop. The native anchor
directory is resolved on the target before checkout comparison, so equivalent
symlink/trailing-slash spellings retain the canonical execution root without
accepting a genuinely different anchor.
The original reservation includes the checkout-probe and preparation budgets,
including both transport grace periods, in addition to upload, native launch
and registration. This lengthens only the ownership lease, not any execution
timeout or test cap.

Repository discovery uses one bounded receiver, not the launch-time native
anchor resolution or a second checkout probe. Its 60-second receiver budget
and 20-second transport/cleanup allowance fit within Bridge's unchanged
90-second discovery deadline. Launch still independently resolves and fences
the actual native anchor before reservation and preparation. Network remote
keys reuse the same parser as Bridge's plugin activation, including userless
SCP notation and URL path decoding; filesystem keys remain type-distinct.

`restore-host` exposes transport-owned host setup to declarative orchestrators
without requiring them to know installed payload paths:

```powershell
agent-ssh restore-host --transport dtssh --alias example-host --port 2222 --dry-run
agent-ssh restore-host --transport dtssh --alias example-host --port 2222 --apply
```

The default/dry-run path executes the dtssh host status contract without
mutation. `--apply` invokes the transport's idempotent installer with
interactive login disabled; authentication remains an external prerequisite
and is never captured. When `--apply` is invoked from an SSH session on
Windows, it brokers the updater through WMI so stopping the serving sshd cannot
reap the updater with the SSH session. That mode returns
`verification_required: true` and does not claim `applied` until the caller
reconnects and runs the dry-run/status contract. Use `--json` for structured
command, output, and result data.

On Windows, the installer preserves the dtssh server host identity across
updates before restarting the host. It automatically uses an available
`OneDriveCommercial` folder for an alias-scoped durable backup, otherwise it
uses a separate local backup outside dtssh's state directory. The
`AGENT_SSH_DTSSH_HOST_KEY_BACKUP_ROOT` environment variable or the install
script's `-HostKeyBackupRoot` parameter selects an explicit location. Partial,
corrupt, or conflicting identities fail closed instead of silently rotating a
key that clients have pinned.

The dtssh launcher now also persists a local dispatch-companion config under
`%LOCALAPPDATA%\agent-ssh-dtssh`, and the plugin's session-start hook publishes
an attributed `agent-dispatch` companion declaration. On a configured Windows
host, the already-durable interactive-session dispatch coordinator can
re-launch the watchdog even when the historical Startup-folder shortcut did not
fire for that logon or the launcher died later in the session.
Before launching that new daemon root, the companion removes only the inherited
Win32 OpenSSH `C28FC6F98A2C44ABBBD89D6A3037D0D9_POSIX_FD_STATE` execution-descriptor
carrier from its process environment. Those descriptors belong to an existing
SSH process chain, not a fresh sshd. All other variables remain inherited;
stop/health calls and ordinary SSH descendant/re-exec chains are unchanged.

`mesh-status [--json]` is a read-only view of a calling repository's
`machines.yaml`. In addition to SSH readiness and environments, it shows the
optional static machine metadata shared with agent-worktrees and agent-bridge:
`role` is a stable terse classification, `description` explains the machine's
purpose, and `capabilities` is an ordered list of broad discovery hints. These
fields describe topology, not live machine state.

Both `mesh-status` and `refresh-mesh` resolve "the calling repository" from the
process's current working directory. Within the Git toplevel they search, in
order, `machines.yaml`, `.agent-worktrees/machines.yaml`,
`config/machines.yaml`, and `.github/machines.yaml`, matching Bridge's existing
conventional-location priority. They never borrow topology from outside that
Git repository or from a nearer subdirectory. Without Git, parent walking
remains available but stops at a `.git` directory or worktree file.
Git source lookup is bounded to ten seconds and ignores inherited `GIT_*`
overrides, so another checkout cannot redirect the calling-repository scope.
Running either through agent-worktrees' `<repo> ssh …` front door (e.g.
`<repo> ssh mesh-status`) pins this to the named `<repo>`'s own checkout
regardless of the invoking shell's actual cwd -- agent-worktrees resolves
`<repo>` from its own registry and launches `agent-ssh` already chdir'd there,
rather than `agent-ssh` resolving the name itself. An unresolvable `<repo>`
fails that routed invocation closed instead of silently falling back to the
caller's cwd.

An explicit `--path` is authoritative: a missing, unreadable, malformed or
non-file source fails rather than selecting another source or reporting an
empty mesh. The selected conventional source has the same validation behavior;
invalid higher-priority input never falls through to a lower-priority file.
An absent conventional source and an explicitly empty `machines: {}` remain
successful no-ops. Refresh validates the source before transport discovery,
profile publication or reachability probes. This source-discovery behavior
does not yet add direct-profile refresh or knowledge-repository resolution.

`refresh-mesh [--path machines.yaml] [--config-d dir] [--timeout N] [--json]`
reconciles this machine's *outbound* reach into the mesh: it re-runs the
transport's own discovery (e.g. `dtssh discover` for the dtssh transport) to
capture live tunnel/host ids, re-renders this machine's managed
`config.d` fragment from that live state, and probes reachability of every
declared alias *other than this machine's own* -- an alias whose declared
`hostname` (or, when `hostname` is omitted, its `machines.yaml` key) matches
this machine's own raw hostname is reported reachable without an outbound SSH
dial, since a machine never needs a network hop through its own relay/tunnel
to reach itself. It never trusts a previously cached id -- transports such as
dtssh rotate tunnel ids on every host restart, and nothing else re-validates a
peer's cached id once it goes stale. `agent-machines`' hourly `watchdog`
self-update tier calls this on every opted-in machine, so a stale cached id
self-heals within about an hour instead of silently breaking inbound SSH until
an operator notices and re-runs discovery by hand.

The fragment's registry provenance is retained in `.agent-ssh-sources` beside
`config.d`, outside its OpenSSH Include glob. Content-addressed snapshots reuse
unchanged discovery evidence and keep an existing profile's source valid while
a new fragment is atomically published. Registry-retention failures are reported
without replacing the existing profile; `doctor` remains report-only.

The repository-gated mesh pointer also names the maintenance fallback for a
machine that remains unreachable after bounded diagnosis. Repeatable state
belongs in agent-machines requirement packages or another declared auto-update
owner; residual local execution becomes a machine-scoped maintenance issue in
an explicitly identified user repository. The target drains that queue with
the optional `agent-machines:performing-machine-maintenance` skill when that
plugin is active. The emitted rule remains self-contained when it is absent:
maintenance becomes inspection-only and mutation stops until an equivalent
trusted workflow is available. agent-ssh reports the routing boundary but does
not own the queue or execute issue instructions.

## Minimal setup

When the plugin is enabled, its skills and hook are available immediately. The
runtime can be stamped cheaply, then self-provisions on first `agent-ssh` use:

```powershell
# Windows, from this plugin directory (checkout or installed payload)
pwsh -File .\scripts\install.ps1 stamp

# POSIX / WSL
bash ./scripts/install.sh stamp
```

Use `install` instead of `stamp` to build the runtime eagerly. The installer
deploys a binstub under the user's local bin directory and a version-gated
session-start reconcile hook under `~/.agent-ssh/bin`; the first self-provisioning
call prints a `::agent-provisioning::` line and fails loud if the runtime cannot
be built.

## The split (core + in-box vs. external transports)

| Piece | Home | Owns |
|---|---|---|
| **agent-ssh core + contract** (this plugin) | copilot-extensions (public) | SSH-profile creation/validation, `~/.ssh/config.d` coexistence, `verify`, `explore`, and the `module.yaml`/registry-record contract schemas. |
| **In-box transports** (`transports/<module>/`) | this plugin (public) | **Self-contained** transports with **no non-public provider/multi-machine system config** — today `direct` (plain SSH), `dtssh` (real-user reach over public Microsoft Dev Tunnels; operator identity injected at deploy), and `wsl` (local-to-WSL reach via the `wsl.exe` interop stdio pipe — needs no ProxyJump/localhostForwarding). |
| **External provider transports** | provider-owned marketplaces | Transports needing **multi-machine system/provider config or credentials** — e.g. the Cloudflare transport (Access org / SSO / multi-machine system hostnames), which ships as its own plugin and registers against this contract. |

**Where a transport lives is decided by one axis: does it carry non-public
provider/multi-machine system config?** If not, it ships **in-box** under `transports/`
(direct, dtssh, wsl). If it does, it stays an **external plugin** in its audience's
marketplace and keeps its hostnames, identifiers, and secrets out of this public
core (cloudflare). Either way it plugs in through the same `module.yaml` contract.

The WSL registry emitter uses `machine_transport.normalize_machine_entries`
to select an independent logical WSL machine. Legacy Windows/WSL topology
projects to `<base>` and `<base>-wsl`; explicit independent declarations keep
their keys and `physical_host` association. `--machine` accepts the logical
WSL key/profile or an exact native/physical identity whose WSL sibling is unique.
Automatic hostname selection is exact, never a prefix/suffix guess. Duplicate
same-platform profiles, alias collisions, and multiple eligible WSL siblings
are refused. The emitted SSH `Host` remains the configured profile alias (or
logical key when absent), and deployment flags retain their existing user,
port, distro, and identity-file behavior. Physical association is UI metadata,
not an environment component in downstream peer addresses.

## The deliverable: name-keyed SSH profiles

Consumers reach a machine by `ssh <name>`. Each transport contributes only its
own `Host <name>` blocks to a managed `~/.ssh/config.d/50-agent-ssh-<module>.conf`
fragment, so multiple transports coexist on one client, dispatched per machine
by the registry `transport:` key. No transport owns the whole config.

New fragments carry schema-v1 source identity for the absolute registry and
transport-module files that produced them. Keep those source files durable:
operational commands audit each managed fragment against the current sources,
withdraw confirmed-stale aliases from `verify`/`explore`, and emit bounded
warnings without probing the host. Legacy fragments remain usable with a
`legacy-unattributed` advisory until `emit-profile` rewrites them.

`agent-ssh doctor [--json]` reports every managed-fragment finding and the exact
file to re-emit or remove. It scans only `50-agent-ssh-*.conf`; unrelated
OpenSSH drop-ins are never parsed or changed. Cleanup is intentionally
report-only because agent-ssh does not have a receipt ledger that could prove
safe deletion.

## Layout

```
plugins/agent-ssh/
  plugin.json
  pyproject.toml
  src/agent_ssh/
  skills/
    agent-ssh/SKILL.md              # profile awareness/consumption
    setting-up-ssh-host/            # dtssh transport runbook (host)
    setting-up-ssh-client/          #                         (client)
    sharing-ssh-keys/               #                         (key hygiene)
    troubleshooting-devtunnel-ssh/  #                         (diagnosis)
  core/ssh_profile.py               # compatibility wrapper for the packaged core
  contract/
    module.schema.json              # transport-provider contract (the recipe shape)
    registry-record.schema.json     # normalized machine record
    examples/{direct,cloudflare}.module.yaml   # contract exemplars (rendering tests)
  transports/                       # first-party in-box transports (real, not exemplars)
    direct/module.yaml              # plain SSH (no recipe, no installer)
    dtssh/                          # module.yaml + scripts/ (install-host/client + launcher)
                                    #   + deploy/emit-registry + schema/ + examples/
    wsl/                            # local WSL interop transport + deploy/emit-registry
  scripts/{install,init,emit-profile,verify}.{sh,ps1}
  docs/transport-provider-contract.md
```

## Usage path

- **Core CLI / contract:** `skills/agent-ssh/SKILL.md` and
  `docs/transport-provider-contract.md`.
- **dtssh host/client setup:** `skills/setting-up-ssh-host/SKILL.md` and
  `skills/setting-up-ssh-client/SKILL.md`.
- **Key handling:** `skills/sharing-ssh-keys/SKILL.md`.
- **Failures:** start with `skills/troubleshooting-devtunnel-ssh/SKILL.md`.

## Writing a transport

Ship a `module.yaml` conforming to `contract/module.schema.json`. Provide a
`proxy_command` template (or omit it for plain SSH). The core does the rest:
renders `Host` blocks, manages the `Include`, writes your namespaced fragment,
and verifies reachability. If your transport is self-contained (no non-public
config), add it in-box under `transports/<module>/`; otherwise ship it as its own
provider plugin. See `docs/transport-provider-contract.md`.
