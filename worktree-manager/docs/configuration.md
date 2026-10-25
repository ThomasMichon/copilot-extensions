# Worktree Manager configuration

The Manager's user-owned configuration lives at
`~/.worktree-manager/config.toml`. It contains control-plane preferences and
provider settings that must not be committed to a project. The existing
`[source]` update-source, AHP provider and restart-recovery tables coexist in this file;
`worktree-manager source set/reset` preserves all non-source tables.

## Installation and Updates

Configuration includes a standing **Installation & Updates** list. It combines
Manager's catalog and the locally available canonical marketplace catalog with
the selected engine's installation inventory; it never copies a plugin roster
into the UI. Each row has contextual details and, where supported, **Update
selected component only**. The confirmation names the component and local
machine scope. Browsing and refreshing are read-only and run off the UI thread:
they do not install prerequisites, start services/workers, request credentials
or enroll automatic maintenance.

The table distinguishes delivered **Payload**, **Selected runtime**, **Observed
running**, enablement and durable update activity. A deployment marker is not
running-service proof. Manager's running version describes the querying process,
not its independently running mux daemon. Unknown, missing, payload-only and
machine-policy outcomes remain explicit in details. Unavailable catalog or
engine metadata is a finding, not an empty healthy installation.

Updating submits a durable request through the existing installation driver.
Acceptance is not completion: return to the list and refresh to see queued,
running, succeeded, failed, blocked or interrupted owner state, attributable
progress and the actual resulting versions. Closing the result or Manager
does not cancel service-owned work. A failed worker launch may leave an
accepted request; inspect its receipt before retrying. The same queue retains
exclusive execution, deduplication and recovery; the page adds no queue or
scheduler. A busy owner queues through that same driver.

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
