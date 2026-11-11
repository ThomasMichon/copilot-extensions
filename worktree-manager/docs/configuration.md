# Worktree Manager configuration

The Manager's user-owned configuration lives at
`~/.worktree-manager/config.toml`. It contains control-plane preferences and
provider settings that must not be committed to a project. The existing
`[source]` update-source, AHP provider and restart-recovery tables coexist in this file;
`worktree-manager source set/reset` preserves all non-source tables.

## Knowledge repository choices

The contributed **Repositories** section keeps hosted creation optional and
separate from checkout acquisition, local registration and binding. A capable
executing native owner contributes personal-private GitHub creation and exact
receipt recovery; a newer payload alone cannot enable them. Creation and
protection completion use the existing remote-effect preview with default Cancel.
Read-only inspection never turns an unknown creation into repair authority.
Partial receipts remain visible and refresh the originating scope without retry.
Periodic catalog polling pauses while an editor, preview or result is open;
command completion still refreshes its originating scope. Apply continues to
recheck the current source.

The [native repository contract](../../plugins/agent-worktrees/docs/repositories-configuration.md#create-a-personal-private-knowledge-repository)
owns effects, account selection, safeguards, recovery and compatibility.
Neither remote completion nor an acquired checkout establishes New readiness;
registration and binding still require their own confirmations.

## Repository authentication inspection

The standing **Authentication** section is a read-only view of the selected
project's registered repository, not a credential store or a whole-harness
readiness verdict. The native repository owner supplies credential availability,
verified identity and exact repository-metadata access as separate rows and
detail cards. Cards retain the target, registry revision, observation time,
failure stage and unobserved capabilities. Git/GCM transport, write permission,
CodeSpaces, MCP and Copilot are not verified by this API observation.

Personal GitHub sign-in is not an upfront setup or first-run prerequisite.
Defer any such prompt until requested work touches a repository that requires
that account. Installing public tooling, the host alone or an unavailable
optional observation does not establish that need; independently required
target-specific credentials remain with their own provider.

The section uses the existing version-1 Configuration list/card shell. Opening
Profiles or another section does not run its authentication read. Visiting or
refreshing Authentication performs one bounded native inspection; retained
observations are visibly stale during refresh, failures stay visible and
retryable, and a background completion never changes the selected section.
There is no executable sign-in action, account switch, credential write,
installation, knowledge binding or first-task admission.

Manager first qualifies the selected engine command against the existing
installed-runtime resolver and reads its optional support declaration from that
exact immutable slot. A newer marketplace payload is not support evidence.
Missing, invalid or older declarations, a foreign explicit engine command and
provider rollback make only this section **unsupported**, without invoking
new syntax or disabling repository preferences. There is no equivalent legacy
authentication read and no probe/replay downgrade. The native descriptor's
`configuration: 1` advertises its provider-owned list projection; older consumers
ignore this additive field. Older Managers do not discover a new payload pivot.
The [native contract](../../plugins/agent-worktrees/docs/repository-authentication.md)
owns provider scope, response meaning and compatibility details.

The generic list transport deliberately removes parent-plugin identity.
This native adapter carries the selected installation receipt separately and
validates it through the existing Manager context-restoration owner before
runtime resolution or invocation. It preserves Manager's engine precedence:
an in-process choice wins, while an explicit environment command takes priority
over an inherited engine handoff. A rejected context never falls through to a
different legacy installation.
An explicitly supplied malformed engine command, including JSON `null`, is a
read error before runtime selection rather than permission to use a default.

## Installation and Updates

Configuration includes a standing **Installation & Updates** list. It combines
Manager's catalog and the locally available canonical marketplace catalog with
the selected engine's installation inventory; it never copies a plugin roster
into the UI. Each row has contextual details and, where supported, **Update
selected component only**. The confirmation names the component and local
machine scope. Browsing and refreshing are read-only and run off the UI thread:
they do not install prerequisites, start services/workers, request credentials
or enroll automatic maintenance.

The **Worktree Manager** row also offers **Inspect Manager setup (read-only)**.
Only explicit activation runs the existing native doctor text report, off the
UI thread. The result retains its output and success/failure status; closing it
returns to Configuration and refreshes the originating view. It never requests
daemon repair, setup, an update or authentication. The report covers Manager's
existing prerequisite, core, daemon, catalog and registration observations;
command completion is not proof of every application's health or first-task
readiness. Other components retain their own diagnostic owners, and the
standalone observation-only `doctor --json` behavior is unchanged.
Read-only health probes use existing control credentials without creating or
replacing them; unavailable credential observations remain explicit findings,
not proof that an endpoint is healthy. Inactive services do not acquire a new
credential prerequisite. Captured Windows Git and PowerShell probes stay
windowless.
Captured command stdout and stderr are decoded as UTF-8 even when the
Manager parent's Windows locale is ANSI. Synchronous commands receive an
explicit UTF-8 Python stream setting; invalid bytes from other commands remain
visible as replacement characters without losing the completed command's
output or exit status.
Report mode neither creates a cutover lock nor initializes an existing empty
one. It observes an existing lock without writing it and reports a busy cutover
without running a census; explicitly requested repair retains its writer lock.

### Automatic update controls

The same workspace has two independently confirmed local controls:

| Row | Existing policy and scope |
|-----|---------------------------|
| **Automatic software updates** | The shared default-installation bootstrap KillSwitch. Allowing updates clears its named central/legacy switch files through the canonical controller; an explicit state-file override remains isolated. The confirmation shows affected paths. This control is not applicable to an independent installation cell. |
| **Launch-time maintenance** | Manager's existing `[maintenance] automatic` boolean, default `true`. This also permits automatic housekeeping, repair and monitor startup, so it remains separate from the software-update switch. Changes preserve unrelated TOML and refuse stale views. |

```toml
[maintenance]
automatic = false
```

Enabled means permission from that control, not a running worker. Details show
effective automatic-update admission and independent blockers. Allowing one
control never clears the other, starts an update, enrolls recurring maintenance
or stops work already admitted. Missing/unreadable/invalid controller state is
visible; an invalid legacy switch retains its documented fail-open reader
semantics rather than being presented as a reliable pause.
The canonical controller comes from the running Manager's runtime resources;
retained-payload scripts remain the fallback for older source layouts.

Confirmations compare the exact viewed bytes inside a bounded writer lock.
The switch's canonical Bash, PowerShell and optional Machines CLI writers
share the same retained sibling lock file. Manager probes the selected
controller's read-only conditional-mutation capability before sending an
expected revision; an older controller refuses this UI operation rather than
receiving an unconditional clear. Upgrade all switch writers before relying
on concurrent edits; an older installed writer does not participate in this
serialization. A new audit hold is never equivalent to the previously
confirmed hold merely because both have `active = true`.

The maintenance control and both source-selection commands share a separate
bounded per-configuration lock from read through atomic publication. A stale
maintenance confirmation refuses after waiting; source edits preserve a
maintenance change published before they acquire the lock. The retained
`.config.toml.lock` is synchronization state, not another preference or a
stale PID lease; it is not removed after each write.

Automatic software updates after Manager startup and before worktree
creation/resume use the canonical `<repo> update --auto` path. Normal launches
submit without blocking first paint; explicit blocking mode waits on the same
owner. `--no-update` and non-update reconciliation retain their separate
meaning. There is no competing stage/apply installer.

Plain user or agent `<repo> update` calls, including explicit component updates,
do not consult these automation vetoes and need no force flag. They retain
normal installation/re-entrancy, source, privilege and interruption checks.
The execution owner re-reads applicable automatic policy before a pending
automatic request starts: a veto retains a durable exit-3 result with no product
runner, not a successful skip. Queued work is not replayed merely by enabling
the setting. Queued automatic `--no-update` reconciliation still rechecks the
broader maintenance veto, but does not consult the software-only hold.
Borrowed Manager execution restores the originating request's exact switch
selection, including the default selection, rather than inheriting another
request's worker override. See the [execution contract](../../libs/update-driver/README.md#automatic-update-killswitch-and-explicitmanual-admission)
for worker/version-skew and installation-scope behavior.

### Maintained components and explicit updates

The table distinguishes delivered **Payload**, **Selected runtime**, **Observed
running**, enablement and durable update activity. A deployment marker is not
running-service proof. Manager's running version describes the querying process,
not its independently running mux daemon. Unknown, missing, payload-only and
machine-policy outcomes remain explicit in details. Unavailable catalog or
engine metadata is a finding, not an empty healthy installation.

The optional Machines-owned **Application Readiness** section complements this
version inventory with declared required-module observations and explicitly
confirmed repair. Its [native owner](../../plugins/agent-machines/README.md#application-readiness-in-configuration)
retains selection, installers, plan validation, post-readiness and restart
obligations. Manager does not infer authentication or first-task admission from
these rows, and visiting the section does not install a missing Machines runtime.

Updating submits a durable request through the existing installation driver.
Acceptance is not completion: return to the list and refresh to see queued,
running, succeeded, failed, blocked or interrupted owner state, attributable
progress and the actual resulting versions. Closing the result or Manager
does not cancel service-owned work. A failed worker launch may leave an
accepted request; inspect its receipt before retrying. A pending request with
unconfirmed execution evidence appears blocked and requires recovery, not
replay. The same queue retains exclusive execution, deduplication and recovery;
the page adds no queue or scheduler. A busy owner queues through that same driver.

Plugin requests enter the selected engine's backend and retain its installation
identity, even through the Manager CLI; Manager self-update uses the separate
Manager backend. The workspace transports the selected engine's validated
installation-cell receipt as well as its exact command. Contributed-command
environment isolation therefore cannot silently redirect these reads or updates
to legacy registries. Catalog failures appear in the standing warning banner
while available rows remain inspectable.

The public update seam is `worktree-manager update --target <plugin>` or
`--target worktree-manager` for Manager alone. Add `--detach --json` for an
acceptance receipt and use `update --request-status <id> --json` for owner state.
Normal untargeted `update` retains its complete suite behavior. Component scope
cannot be combined with force/reinstallation, repair or module-selection flags.
The suite-only engine bootstrap acquisition budget is likewise inapplicable:
component targeting never bootstraps or repairs a missing engine.
Plugin scope does not bootstrap/repair the engine, update Manager, sync anchors,
prune unrelated contributions or run the suite's finishing integration.

The selected engine advertises targeted-update contract generation 1 through
`update --inventory --json`. Older owners leave plugin actions unavailable;
upgrade through the existing suite command, then refresh. A legacy engine
installer without scoped finishing support similarly requires a suite update
before targeting the engine itself. Missing/disabled prerequisites use explicit
setup/enablement, not implicit broad installation. Configured runtime source
policies that the current executor cannot realize remain blocked, never silently
reset to marketplace delivery. Manager self-update retains its independent
configured source, versioned publication and mux cutover owner.

## Pane boundary ownership

Tracked launches copy their OS pane shells into the engine's launch-owned state,
not the Manager's version directory. Each boundary selects the current completed
generation within the originally selected Manager root, with a finite child
deadline and no provisioning or service-start fallback. The internal
`worktree_manager.pane_lifecycle` contract uses that root, platform and configured
source/ref to distinguish its installation identity from its replaceable version.
Changing source selection mid-session therefore leaves a previous end notification
unconfirmed rather than applying it under a different selection.

The registry owner synchronously fences native session plus CLI-pane identity
and retains an affirmative observed child exit. A handoff retains both live CLI
panes until their individual exits; one failed pane cannot retire the other.
Corrupt starts refuse without replacing state, and exit fences protect tombstones
as well as live entries. Admitted changes notify an existing status monitor
through bounded local IPC without starting it. The shell's successful process
spawn alone is not that admission. Missing handlers remain quiet and uncertain.
See [the engine's lifecycle architecture](../../plugins/agent-worktrees/docs/architecture.md#launch-owned-pane-assets-and-current-boundary-handlers)
for storage, compatibility and the remaining setup-waiter limitations.

## Restart recovery requests

Both new behaviors default off. Existing bounded, report-only evidence sampling
is independent of these preferences.

```toml
[restart_recovery]
shutdown_notifications = false
automatic_restore = false
```

These are strictly boolean local requests, not capability attestations or
authorization. Unknown keys and invalid values visibly block recovery rather
than silently enabling a default. Source updates preserve this table. No
command here writes enrollment, installs a host startup service, changes a
system policy, or resumes a conversation.

`shutdown_notifications = true` requests the native Linux logind transport in
the existing active mux daemon. It acquires a delay inhibitor before subscribing
to shutdown signals; denied access is a blocker, never a reason to elevate.
The connection and FD exist only while the daemon holds its active lease and
admits loop mutations. Passive generations do not connect. WSL teardown, Windows,
macOS and containers are not qualified by Linux logind support.

The transport reports `qualified: false`: the private D-Bus/FD-transfer fixture
services a shutdown signal within its delay budget while ordinary daemon work
blocks, but does not qualify actual host shutdown/startup or hard real-time
OS/filesystem bounds. `automatic_restore = true`
currently reports a blocker because current-owner admission and acknowledged
exact-conversation restart Resume are not qualified; it never launches.
An explicit request lets the active daemon durably freeze only qualified
previous-boot candidate identities before ordinary reconciliation. This uses
private local attempt state, not host startup enrollment. Frozen records require
the historical owner's existing worktree incarnation and head revision; missing
legacy proof is not inferred or backfilled. Neither freezing nor enabling this
preference admits a task, launches a conversation, or acknowledges a seed.
With this request enabled, the active ordinary daemon loop additionally reads
at most one unresolved attempt's exact historical owner receipt per twenty
seconds. It durably retains monotonic outcomes without changing frozen evidence
or resetting uncertainty. Owner errors and contradictory receipts remain
visible, and acknowledged historical pickup is not current liveness or
completed restoration. There is no parallel scheduler, automatic launch or
retry. Default-off/passive paths perform no such owner reads or outcome writes.
When startup evidence and epoch are confirmed, failed freezing of a new
candidate does not disable observation of readable existing attempts. A full
database still refuses new identities and retains its visible freeze blocker.

`worktree-manager mux-daemon recovery-evidence` is read-only and includes the
requested capability states. It does not connect to the system bus to establish
runtime readiness: a requested Linux notifier is `unconfirmed`, unsupported
platforms are `blocked`, and disabled requests are `disabled`. Requested but
unconfirmed/blocked capabilities, invalid configuration and unavailable
evidence produce a JSON blocker and nonzero exit. The resident daemon's
authenticated health response additionally exposes `restart_recovery` with its
actual notification transport state, admission, qualification and last
`outcome_observation` result.
The report also reads retained attempts without creating absent storage.
Attempt corruption and capacity failures remain explicit blockers; existing
attempt identities and acknowledged receipts are never discarded to permit a
retry. Retained receipts appear as each attempt's optional `outcome`; ordinary
inspection neither contacts their owners nor migrates the legacy database.

The explicit report-only `recovery-evidence --check-survivor <attempt-uuid>`
operation can inspect one frozen attempt's exact-original current hosting
without enabling either preference. It requires the target epoch to remain
current, uses the existing no-update/no-reconcile engine boundary and retains
false reuse/launch/seed-ACK/restored/automatic flags. It neither changes these
preferences nor enrolls automatic survivor polling or adoption. A missing,
unknown or unavailable survivor is not permission to launch or retry.

## Picker logical machine roster

The production Picker reads the project's existing `machines.yaml` registry
(canonical `.agent-worktrees/machines.yaml`, additive legacy root registry,
or the existing knowledge overlay fallback). It consumes the shared
`machine_transport` normalization without rewriting those files.

A legacy Windows/WSL entry named `host` becomes two independent logical
machines, `host` (Windows) and `host-wsl` (WSL). Both are Profiles source
columns **and** target rows; WSL is not excluded from the source axis.
Explicit `execution_platform` entries retain their declared keys and aliases.
Generated key, alias, or duplicate-profile collisions refuse the roster rather
than silently selecting one execution space.

`physical_host` groups only visible labels: for example, `desk-win` and
`ubuntu` associated with `desk` display as `desk Win` and `desk WSL`.
Source IDs, matrix keys, SSH profiles, and persisted machine ownership retain
their logical identities. Local detection selects the running platform's
logical entry, so the Windows and WSL siblings never both become local sources.
The internal `legacy_key` marker allows an owner-derived legacy projection to
follow the known local platform; an explicitly declared logical selector on
the wrong platform fails closed rather than selecting its sibling.
Standalone native Windows/Linux entries continue to work without a sibling.

With [binding provenance from the selected engine bootstrap](../../plugins/agent-worktrees/docs/engine-picker-contract.md),
the local source uses the observed OS hostname and actual Windows/WSL/Linux
environment, plus the existing explicit machine binding when one is configured.
Absent, empty, unrelated-only and sibling-only maps do not require enrollment:
there is exactly one ready local source, with no SSH argv. Matching customization
retains its canonical source key. The source, Profiles local host and per-row
local routing use the same context; named foreign-platform/profile targets,
ambiguity and malformed declarations still refuse. This does not rewrite
historical record owners or bypass the engine's project/record checks.
Local-only mode uses the same source key for its tab, list and per-row refresh,
rather than stamping raw hostname metadata onto differently named local rows.
Supported legacy registry keys remain the Profiles/source keys even when the
compact execution name differs; validated legacy target spellings normalize
to that same local execution space when no customization entry exists.
Setup, New/AHP, maintenance and worktree-detail routing consume the selected
`local_source_key()` contract when available; legacy sources still expose
`LOCAL`. The raw local observation remains separate for first-paint chrome
and compatibility. Known-provenance resolution preserves the full OS hostname,
preferring full-hostname customization before a matching historical short-name
customization. No map leaves that full observation intact.
Worker setup/reload publishes the selected tuple for synchronous UI consumers;
New, task embodiment and maintenance initiation never resolve configuration on
the event loop. Expired or invalidated configuration is refreshed only on a
worker. Action routing keeps the published tuple while actual engine
project/record admission remains authoritative at execution.
Older bootstrap responses without provenance retain their historical strict
roster behavior; their detected `machine` scalar is not promoted to a new
configured binding.

Live setup reloads retain rows for sources still in the resolved roster while
their replacement loader is pending or streaming an incomplete prefix. New
rows update matching identities in place; only a complete source listing
removes absent rows, including an explicitly empty listing. Sources removed
from configuration lose their retained rows immediately. Retaining a row does
not mark its source ready or skip the Actions menu's current liveness check.
The `r` footer acknowledges a reload request rather than reporting successful
completion; an immediate setup admission failure retains its actual diagnostic.

This is a read-only compatibility projection, not a persisted-state migration:
historical rows are not rewritten, existing canonical-over-legacy merge
precedence remains intact, and no bridge-address or environment dimension is
added. Transport configuration and provisioning remain owned by their existing
consumers, not by Picker display grouping.

## Same-machine AHP provider

AHP is explicitly selected when creating or recreating a hosted session.
Configuring an endpoint only makes the provider available. Resuming a worktree
whose persisted execution leg is `active` or `unknown` forces that provider
even when the default-off AHP checkbox is unchecked; an unsupported provider
fails closed. A disposed or absent binding may launch directly.

```toml
[ahp]
endpoint_url = "ws://127.0.0.1:8765"
account = "example-user"
protocol_versions = ["0.7.0"]
auth_resource = "https://api.github.com"
connect_timeout_seconds = 10
lifecycle_timeout_seconds = 30
```

| Key | Default | Meaning |
|-----|---------|---------|
| `endpoint_url` | `""` | Required when AHP is selected. Must be a loopback `ws://` URL with an explicit port and no credentials, query, or fragment. |
| `account` | `""` | Optional expected repository account. When set, it must exactly match the account resolved by `agent-worktrees repos account-for`; an empty value accepts that resolved account. |
| `protocol_versions` | `["0.7.0"]` | Non-empty list offered during AHP initialization. |
| `auth_resource` | `"https://api.github.com"` | Resource sent in the AHP authentication request. |
| `connect_timeout_seconds` | `10` | Positive connection and ordinary request timeout. |
| `lifecycle_timeout_seconds` | `30` | Positive create/dispose timeout floor. |

The Manager resolves the repository account and mints its token only through
the pinned `agent-worktrees` process boundary. The token is held in memory,
passed to the AHP controller and the launched Copilot child, and is never
written to configuration, execution-leg state, logs, or command arguments.
Selecting AHP creates or verifies a session whose listed working directory
matches the exact worktree returned by `agent-worktrees resolve --json`, then
persists an opaque `provider = "ahp"` execution leg through an engine-owned
lifecycle reservation held against the finalize fence. Concurrent ensure,
dispose, and finalize operations cannot publish stale bindings; a newly created
session is disposed if publication fails. Endpoint, account, protocol, host, or
path mismatches fail closed.

The production Picker offers **Dispose hosted session** only for a local
AHP-owned worktree in `active` or `unknown` state. This explicit destructive
action verifies the exact worktree binding, disposes the host session, and marks
the execution leg `disposed`; normal finalization is then available. Closing
the terminal client never disposes the hosted session implicitly.
