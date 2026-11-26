# Copilot Driver core

`agent-copilot-driver-core` owns the `agent_remote_driver` Python namespace:
the canonical Session Host, TCP protocol, ACP WebSocket adapter, retained
execution and compact receiving export. It does not import Bridge, Worktrees
or Manager. Install it as an ordinary first-party distribution and run
`python -m agent_remote_driver.session_host --support-json`; plugin enablement
is not required.
Session Host shutdown closes attached clients before awaiting its cancelled
server task, so child exit does not depend on a client disconnecting first.
The local headless launcher uses canonical daemon admission with borrowed
startup-output descriptors. On Windows it verifies escape from every declared
caller-owned cleanup Job before Host entry; an inner permissive Job cannot hide
a restrictive outer Job. Refusal preserves the original creation/cleanup
evidence rather than accepting a caller-bound lifetime. Contained tests retain
their supervisor's ownership. This does not qualify every caller or headed CLI
package-replacement behavior.

The [Driver application and extension connector](../../plugins/agent-remote-driver/README.md)
provides the ordinary application front door. Bridge consumes this same
library through the existing canonical-reference and wheel-build owners,
not a sibling plugin source dependency.

Core also owns the complete Bridge registration/delivery JavaScript closure
under `agent_remote_driver.bridge_extension`. It ships in the ordinary Core
distribution and compact export. Bridge's single native SDK entrypoint supplies
its actual plugin root and existing `joinSession` adapter; promotion projects
these same assets into the payload, while source checkouts use the canonical
Core directly. A partial projection refuses rather than selecting another
source. Portable receiving exports the selected Core's package resources without
searching for a suite installation. This ownership move adds no SDK extension,
service, hook permission or automatic delivery mode.

`activateBridgeExtension` returns the exact native session it joined, after
installing its existing contributions and observers. Driver's separate
`activateDriverExtension({ joinSession })` activation can consume that result
through its supplied join operation: hooks and `/peer` are registered by the
one Core SDK join, while Driver attaches control and event forwarding to the
same session object. Existing entrypoints and portable loadout/root checks are
unchanged. This is a provider-side composition seam, not a mixed-version owner
election or retirement of the Bridge extension.

Portable receiving can explicitly prefer the launch-scoped `driver-native-v1`
connector. The retained Core advertises it through `headed_connectors` in
`--support-json`; the selected support archive separately declares its compatible
facades. Only mutual support selects composition. Missing support preserves the
legacy launch, and the preference defaults off. The receiving operation retains
that decision with its original launch inputs and Core bytes before mux creation;
a changed retained choice or Core refuses instead of selecting another sender.
A preferred launch whose retained selection is missing also refuses; missing
launch state is not a legacy capability advertisement.
No new CLI option, installed setting or global plugin migration is involved.

Core additionally accepts the explicit `driver-global-v1` contract for a
globally enabled Driver that is itself the sole SDK connector. The selected
global generation must advertise that contract in its retained
`extensions/agent-remote-driver/native-connector.json`; missing or unsupported
metadata refuses before staging or native creation. The declaration's Driver
root names that actual global generation, not a separate portable facade.
Its connector root must be the same root. Core validates and removes the
original support plugin arguments, but adds no staged connector argument.
Retained headed helpers remain private implementation resources, not another
activated SDK owner. The global entrypoint joins once with the same Core
contributions and binds that native object to the existing headed adapter.
The live observer must report the global Driver's source-qualified ID and
actual PID; only that exact executing entry is excluded from competing-sender
checks. A second Driver, Bridge or staged connector still refuses input.
An incomplete global payload, failed join or lost Host binding never selects
the staged path or reconnects another SDK owner.

This is provider-first optional support, not an ordinary receiving default.
Older callers retain `driver-native-v1` and its staged owner. A caller selecting
the global contract must discover support from both the executing Core's
`headed_connectors` and the selected global generation's declaration.
No existing launch is silently migrated, disabled or replayed on rollback.
Native installation precedence and lifecycle qualification remain separate
from synthetic SDK and closed-package evidence.

For that selected launch, Core validates the real Bridge and floor Driver support
roots in the original command, then omits only their support-only plugin arguments
from the native child command. Their assets remain retained, but do not start
independent extension bootstraps that would time out without an SDK join.
Other plugin arguments and native input stay unchanged. The staged Driver entrypoint owns the one SDK join,
including Bridge's existing hooks and `/peer`, and binds that exact native object
to the common headed adapter. Retained Bridge sends pass through the same adapter's
startup, input-ownership and auth-wait fences rather than another SDK sender or
Host attachment. Metadata names the actual Driver connector root. The Host
binds the support roots to the original native command before staging or process
creation, while startup admission retains that original command. The connector
validates its own module root and observes the
live session extension registry: its source-qualified plugin identity must
name its actual running PID, with no duplicate connector or running/starting
Bridge or Driver extension. A same-named global installation record is not
proof of another executing sender. Missing or unknown live observations refuse.
The source-qualified ID is the identity key. Discovery-name metadata must be a
non-empty string, but its spelling need not equal the ID's extension component.
A matching or different display spelling cannot compensate for a different ID,
source, running state, process or executing module.
The common adapter repeats that observation before both native-send and ACP
prompt effects, retaining its input reservation across asynchronous validation
and rechecking takeover before dispatch.
Malformed or mismatched selection never triggers a fallback join. Ordinary
standalone sessions and legacy headed launches keep their existing entrypoints.
This optional source path does not qualify native plugin precedence, installed
adoption or withdrawal of independently enabled extensions.

The observer contract is declared in the
[public SDK `v1.0.28`](https://github.com/github/copilot-sdk/blob/v1.0.28/nodejs/src/generated/rpc.ts),
[paired with CLI `1.0.105`](https://github.com/github/copilot-sdk/blob/v1.0.28/nodejs/package.json):
`session.extensions.list` reports extension IDs, discovery source,
status and running PID. `plugins.list().installedFrom` instead names a live
plugin's **marketplace** directory; it cannot prove an invocation plugin's
executing root. The selected connector uses its existing module/launch/Host
binding and the live registry, not a guessed plugin-origin field. Legacy
connectors retain their existing plugin-list validation; no observer failure
selects another connector or retries input.

Selected-context refusals retain JSON-RPC error `-32001` and append a bounded
`[reason]` or `[reason:role]` diagnostic to the existing pre-dispatch message.
Reasons distinguish unsupported observers, failed reads, changed session/CWD,
and missing, duplicate, uninstalled or wrong-origin plugins, as well as an
unsupported live registry, wrong executing identity/PID or competing sender;
roles are only
`bridge`, `driver` and `connector`. Unknown failures use
`context-validation-failed`, never raw SDK exceptions, metadata or paths.
The suffix is advisory text, not a new capability or retry permission. A failed
observation still holds control without input, abort, fallback or replay.
Older peers retain the same refusal without the diagnostic suffix.

For a connector identity mismatch, an optional second `[owner-checks ...]`
suffix distinguishes the observed ID comparison, directory/qualified/other
name shape, known source/status enums and same/other/missing/invalid PID
comparison. It contains only a fixed vocabulary, not actual IDs, names, PIDs,
paths or raw registry records. Incomplete, extra or unrecognized diagnostic
fields are omitted. These are comparisons from the refused observation, not
proof that another field passed its later admission check or that startup
completed. The diagnostic does not participate in identity, running-state, PID
or competing-sender admission and cannot authorize retry or clear the adapter's
hold.

Compact receiving exports retain the selected process provider's separate
Windows output helper when present, without requiring that layout from older
providers.

Headless and headed coordinators publish their original private state-file
locator through the same instance-bound daemon registration. Inventory can
therefore discover a headed Host whose caller selected a state file outside
the runtime directory, including when its SDK backend or receiving owner
admitted that same process first. Other daemon registrations without a Host
locator remain outside Host discovery; a known headed backend without usable
Host state remains explicitly unbound and unavailable. A failed headed locator
publication reports unavailable control while retaining the original native
child; it does not relaunch the child or replay its input.

Host state observes the native Windows kernel boot GUID when the exact query
contract succeeds; unsupported, failed, incomplete or empty observations remain
unavailable. Linux retains its kernel boot-ID observation. A boot identifier is
not an environment identity, complete recovery descriptor, whole-scope settlement
receipt or permission to restore execution. Windows restart/recovery support is
not enabled by this observation.

### Read-only Host inspection

The executing Host's `--support-json` declares
`copilot-driver.host-inspection` version 1. H1 remains version 1: a separate
connection may send a first-message PING (`?`) containing the contract/version,
a 32-hex challenge and the existing connect credential. Its PONG (`!`) echoes
the challenge and returns at most 4 KiB of content-free metadata: Host
PID/incarnation, child PID/alive, observed prompt-in-flight state, maximum
buffered sequence, frontend ACK, last child-frame age and buffer depth.
Unknown additive response fields are ignored; malformed, mismatched and
unsupported responses are not liveness successes.

`SessionHostClient.inspect()` uses the already-selected route and a default
one-second deadline, without re-forwarding, attaching or retrying. It never uses the active
frontend's bulk send lock, ACKs or writes ACP, renews custody, changes reapability,
or displaces the controller. A successful round trip proves only the observed
Host event loop and route, not child health, semantic progress or task completion.
If ACP bulk delivery blocks the child reader, the last-frame age and sequence
describe only what the Host has observed, not unread child output.

Older H1 Hosts reject the unknown first message without changing their frontend;
inspection reports unsupported. Ordinary H1 attachment and legacy receiving
remain supported with unchanged lifetime semantics. Private native-authorized
attachments do not expose this channel: their grants/tickets are not consumed
and no legacy inspection fallback is attempted. This is source/artifact support,
not native, installed or external-venue qualification.

Capability-selected ordinary Bridge receiving uses that already-joined public
SDK session. It does not construct or reacquire a private headed/remote controller
to deliver queue/steer input. Native target/loadout checks and original one-shot
receipts remain; mutable controls, creation and replacement retain their
independent authorities.

The compact legacy export preserves its disconnect-driven lifetime, rather
than adopting ACP lifetime implicitly. An idle graceful detach closes that
Host normally; cancellation of its external owner still propagates. Explicit
legacy child termination need not close the retained broker, so socket EOF
alone is not evidence that the broker exited.
Private Host-state publication retries transient Windows sharing failures
within a short fixed bound; persistent failures still raise without replacing
the previous record.

The explicit optional `sdk` extra supplies a public-Python-SDK headless provider
behind that same Host. Native ACP remains the default; the compact export stays
native-ACP-only. The base Python floor is unchanged, while SDK selection requires
Python 3.11+ and its selected dependency closure before native creation.
See the [optional provider contract](../../plugins/agent-remote-driver/README.md#optional-headless-sdk-provider)
for supported ACP operations, permission limits and separate activity snapshots.
Inventory availability does not enable automatic idle retirement.

The Session Host Python `main(..., headed_run=...)` seam accepts an explicitly
supplied in-process headed receiver while retaining the ordinary parser,
repository policy and native creation/wait owners. Headless execution rejects
that override rather than silently ignoring it. `--support-json` additively
declares `headed_receivers: ["explicit-inprocess-v1"]` when headed execution
is present. It is not a serialized-code or environment hook; a consumer owns
any fixed retained executable and additional dependencies. The optional
[MCP headed receiver](../../plugins/agent-mcp/docs/session-generation.md#opt-in-retained-windows-headed-receiver)
is one explicit consumer. Ordinary core bundles have no MCP dependency or
automatic originating-owner enrollment.

Worktrees' mux-creation adapter also consumes this core. The core owns its
short native check/create lease, neutral client working directory, nesting
scrub and uncertain-creation handling. Workspace allocation, pane argument
preparation and seed custody remain caller-owned; this seam alone does not
establish headed broker or all-caller convergence.

Manager's launchers select the finite boundary from their chosen
Worktrees interpreter with
`python -I -m agent_remote_driver.mux_creation --describe-contract`.
Contract `copilot-driver.mux-creation`, version 1, advertises
`windows-psmux-create-or-join` on Windows or `posix-tmux-create-or-join` on
POSIX and names the exact executing
interpreter and physical entrypoint. A selected older provider without this
declaration retains the documented legacy creation path; support is not
inferred from a package version. No retirement of that compatibility path
is scheduled by this change.

The selected entrypoint's `--create` operation reads at most 1 MiB of JSON
text from stdin. Its exact request properties are `version: 1`, `binary`,
`session` and literal `argv`; the latter starts with
`["new-session", "-d", "-s", session, "-c", work_dir]` and contains the pane
command. Duplicate/unknown properties and unknown CLI options are refused.
The response carries `contract`, `version` and `status`: `created` or `joined`
exits zero, `rejected` or `unknown` exits 3. Rejection precedes possible
dispatch; unknown retains handoffs and forbids automatic cleanup, replay or
legacy retry. The core does not echo pane argv or environment values.
POSIX creation checks the exact target before pane preparation. A failed
dispatched client is uncertain, including a concurrent tmux creation race;
it is never reported as created or retried through the old launcher.
The Bash consumer retains the selected physical interpreter/entrypoint,
uses bounded caller-owned processes and preserves AHP auth scrubbing.
Unknown creation retains any original token handoff; a confirmed join
settles only the unused local handoff and attaches without creating a pane.
Manager retains terminal attachment, UI and original native admission. Native
status presentation uses Core independently of Manager. This finite creation
operation does not wait for Copilot to exit.

The standalone Host front door also advertises `mux_creation` with
`posix-private-create-v1` on POSIX. Its exact `--mux-create-private` invocation
reads at most 1 MiB of JSON text containing `version: 1`, an absolute native
TMux `binary`, absent private `socket`, `session`, canonical `cwd` and literal
child `command`. It creates only that socket/session and returns the same
creation contract with `status: created` and native `incarnation`/`pane`.
Pre-dispatch refusal returns `rejected`; any unconfirmed dispatch returns
`unknown`, both exiting 3 without echoing commands or diagnostics. There is
no default-server join, retry, cleanup or caller journal. This stdlib receiving
closure does not load the optional Windows creation-lease dependency.

Portable Bridge receiving selects that operation from its copied executable
before prerequisite, registration or mux effects. Bridge retains its original
attempt lock, digest, private socket identity and reuse observation. An older
core without the operation refuses a fresh attempt before effects; an already
created attempt still reuses its original incarnation without requiring a new
core. Unknown creation never falls back to direct TMux or replays the child.
Existing default-mux creation syntax and legacy Host entrypoints are unchanged;
no compatibility retirement or native POSIX qualification is implied.

### Finite PSMux observation

`agent_remote_driver.mux_observation` owns native PSMux reads for Driver
creation, Worktrees presence/listing and Manager discovery/client counts.
Point observation reads the provider's port/key rendezvous and sends only bounded,
authenticated `list-panes`/`list-clients` queries and a final read-only
`display-message` identity fence over loopback. Each command uses its own
authenticated connection for providers that accept only one command per
connection; all three share one deadline and aggregate response-byte cap.
It never executes PSMux, repairs registrations or starts a background observer.
Pane and final identity responses must identify the requested session and
one incarnation; port/key hints must remain unchanged across the reads.
The final identity also names the current pane, so window-local active flags
from other windows cannot select the wrong pane.
Missing rendezvous hints alone never establish absence: Driver retains the
complete native server/PID-birth coverage reader introduced for prompted Resume.
Legacy PSMux 3.3.5 servers publish port/key/session-ID files but no PID sentinel.
Inventory therefore discovers port/key pairs, authenticates the live reply's
server PID against the native census, and rechecks process births and registration
contents afterward. A PID sentinel, when present, must still match.
Failed PID-less endpoint candidates may be stale; they do not block a complete
authenticated inventory when every live native server is covered elsewhere.
An unreachable or unregistered live server, unsupported process mode or changing
inventory remains unknown.
`list_psmux()` is a membership-only read: one complete inventory pass supplies
authenticated session incarnation and actual attached-client count, without a
second pane-observation pass. Its rows intentionally omit pane details; callers
needing those use the unchanged `observe_psmux()` or `observe_psmux_many()`.
The finite inventory batch uses at most eight short-lived query workers under
the original one-second listing deadline. Each reads client names between
matching initial/final session identities, preserving the shared byte bound and complete
process-birth/registration coverage checks. It does not create a resident worker.
Native process classification accepts paired double-quotes around the same
supported `-L` and command tokens as their ordinary Windows argument spelling.
Unknown options, malformed quoting and ambiguous server forms still refuse
classification; quoting support does not reduce census or registration coverage.
Selected-namespace absence uses qualified registration identities while still
covering every native server. The legacy name-only inventory API retains its
ambiguity refusal for duplicate logical names across namespaces.
Catalog discovery uses the complete current inventory rather than probing
stale directory hints again. Registrations published during the inventory
must pass its unchanged before/after coverage checks.
Registration basenames retain their authenticated logical names, so `__`
inside a default-namespace session is not mistaken for a namespace separator.
Unicode worktree identity components remain intact; path separators and
control characters are refused before reading rendezvous files.
Stale, partial, unreadable, changed, malformed or timed-out observations remain unknown.
Client counts come from client rows, not PSMux's boolean `session_attached`.

The default namespace uses the native `PSMUX_DATA_DIR` or native home.
An explicitly namespaced launcher can pair its existing `psmux -L <name>`
selector with `COPILOT_DRIVER_PSMUX_NAMESPACE=<name>` for observation.
An in-process caller can instead supply `observe_psmux(..., namespace=<name>)`
to select its exact creation transport without changing the environment shared
with other observers. An explicit empty namespace selects the default transport.
This Driver selector does not configure the PSMux CLI or isolate its startup
cleanup; the caller must keep both selectors consistent. No environment
setting makes native tests safe on a shared host.

The finite CLI advertises `copilot-driver.mux-observation`, version 1 and
operation `psmux-observe` with its actual interpreter/entrypoint through
`--describe-contract`. The ordinary `--observe` request takes `version: 1`
and `session`. A runtime advertising `psmux-observe-pinned` also accepts an
`expected_incarnation` (native `$id:created` string). This form requires an
explicit absolute `PSMUX_DATA_DIR` and an explicitly set
`COPILOT_DRIVER_PSMUX_NAMESPACE` (empty for the default). These select the
caller's trusted scope, not scope supplied by a remote request. A missing
rendezvous or different incarnation is `unknown`; this form does not consult
host inventory. Consumers requiring the pin must not retry without it. Even
a match establishes only mux identity, not a reachable Copilot session.
Both forms accept at most 4 KiB of JSON. The response has the contract/version and
`status` (`present`, `absent`, `unknown`, `unsupported`), with incarnation,
client count and pane observations when present in ordinary mode. The pinned
form returns the operation, namespace, session, expected incarnation, status,
reason and observed incarnation, without pane details or authentication data.
Confirmed present/absent
exits zero; unknown/unsupported exits 3. Duplicate/unknown properties and
unknown options are refused. Older independently installed providers without
the declaration stay unsupported; callers must not downgrade to passive
PSMux CLI reads. Foreground attach/create and other explicit provider writes
still launch PSMux and retain its startup-cleanup risk.

### Finite PSMux presentation

`agent_remote_driver.mux_status.apply_psmux_status` publishes only the harness
status fields and their status-bar formats through the native authenticated
loopback endpoint. Worktrees' elected monitor and Manager's compatibility
writer use this Core-owned operation instead of starting the cleanup-bearing
PSMux CLI. TMux retains its existing writer.

The caller supplies the current native session incarnation. The operation
rechecks that identity and unchanged port/key hints before each option write,
reads each value back, and checks the target again before reporting success.
Rendezvous hints must also remain unchanged after each identity response.
All requests share a bounded deadline. Unsupported options, control characters,
oversized values, changed targets, failed authentication and incomplete readback
return false, including after partial publication. No process, pane, input,
session, provider registration or new background updater is created. This
in-process seam ships with each consumer's declared Core dependency; it adds no
independently deployed CLI or service contract and does not change observation
or launch authority.

Worktrees' native sweep retains its existing read-only observation and roster
pass, then publishes its computed presentation directly through Core. Fresh
Manager mappings remain usable when their native incarnations match the
observation, but Manager is not a discovery or publication prerequisite.
The original content owner publishes each completed path to that pass's admitted
target before the bounded attribution rotation can outlive its cache entry.
Unmapped or replaced sessions require an exact canonical name from a unique
local tracking record, its existing worktree directory and a live native pane
at that exact path. Two candidates are probed per round-robin pass. Unknown,
ambiguous, foreign-path or changed-incarnation observations authorize no write.
This path does not enter legacy PSMux CLI writes, handoff or pruning paths,
start Manager, or allocate a second status producer.
Ordinary rendering failures stay local to their session, allowing other sessions
and later sweeps to proceed. Governance deferrals still propagate, and unsettled
native cleanup remains with its existing cache owner rather than being discarded.

Manager recognizes the legacy `@aw_updater`/`@aw_updater_prefix` bookkeeping
fields without forwarding them as native ownership changes. An ownership-only
delta requires no native write. Other unknown fields remain refused. Context
publication initializes the canonical status formats, including for older
mapping records, while ordinary segment refreshes retain existing coalescing.

The presentation pass compares every cached incarnation with a fresh native
observation, including a replacement under the same session name. A replacement
clears context and delta caches only after independent native attribution.
This does not rewrite Manager's mapping, tombstone or revision ledger; retained
Manager callers keep their existing CAS registration and refusal semantics.
Worktrees never sends native ownership markers or changes its monitor election.

The monitor consumes the existing in-process Core operation, not a new Manager
RPC. Its bundled Core dependency establishes receiver support without requiring
an independently installed Manager upgrade. Older monitors and Manager callers
retain their existing transport and refusal behavior; rolling the monitor back
restores that older publication path, not an automatic CLI fallback. Service
replacement and actual refresh verification remain with their existing owners.

The explicit headed coordinator now runs in the original caller-owned pane.
It stages the launch-bound SDK extension, retains the native child handle and
serves the same Session Host after native session/PID binding. The ordinary
application supports WS/TCP; `prepare_headed_command` retains a TCP-only compact
receiving executable under the original attempt, independent of the caller's
venv. Neither path allocates a workspace, mux or additional launch journal.
Original native startup admission and literal prompt argv are preserved.
Full caller migration and restoration qualification remain in progress.

Headed ACP forwards native text deltas and falls back to complete assistant
messages when no deltas were observed for that message. Native message identity
prevents duplicate full-message delivery alongside an existing stream; legacy
unkeyed streams retain their streaming-only behavior. Empty deltas do not hide
an available complete reply. This does not create, reload or replay a native turn.
Delegated child events do not contribute text or complete the root ACP prompt.

An in-process Windows caller may explicitly supply
`run_headed(..., native_handoff=WindowsNativeHandoff(callback, handles))`.
The existing headed native-child owner creates the selected direct
`copilot.exe` suspended, retains its original `Popen` and strict Job, and calls
the finite callback before execution. Explicit handles must already be
inheritable; the native handle list borrows them without changing their flags.
The callback must return `None` and must not resume or substitute the child.
The same owner retains the original wait and settles its Job after exit.
Interrupted settlement retains its original cleanup task and native custody
in the cleanup error; it never authorizes replay.
Script/coordinator images, shell/executable overrides, unsupported platforms
and auth-wait composition are refused. This API is not a serialized-code hook,
CLI option or automatic MCP overlay. Default headed creation, startup admission and auth custody remain
unchanged; selecting and consuming a productive overlay through the retained
executable remains separate qualification.

`headed.caller.execute` retains that executable as the original Python
caller's foreground child. A live, PID/birth-bound admission channel rechecks
the original launch immediately before native creation. The native receipt
uses the original private nonce, session and coordinator identity to resolve a
distinct live Copilot PID/birth; creating the coordinator alone is not native
acceptance. Receipt failure retains the wait without replay or automatic seed
restoration. The API returns the actual coordinator exit code separately from
receipt certainty. `--support-json` advertises `headed_process_receipts` with
`native-child-v1`; older exports are refused before coordinator creation.

The retained extension assets also carry a bounded JavaScript client for
the same TCP Session Host. The native child receives its launch-bound
`COPILOT_DRIVER_HEADED_CLIENT` declaration; it is not a public endpoint or
permission grant. The client verifies the original state/native identity,
CLI lifetime, nonce and support before sending. It refuses a prior
controller's buffered RPCs and never reconnects after unknown admission.
Read-only native history remains available for original receipt recovery.

Headed `initialize` advertises `_meta.nativeSend` version 1 for the additive
`copilot-driver/native-send` method. This is not a standard ACP method. It
preserves explicit `enqueue`/`immediate` delivery, uses a generated
`agent-remote-driver-<uuid>` source and returns the actual SDK message ID
without treating acceptance as turn completion. Unknown SDK results,
concurrent effects or native input revoke control without replay. The
original argv-seed exception expires at the first controlled send, even if
its native event was not observed; later identical user text is not exempt.
The existing standard ACP prompt/turn-completion interface is unchanged.
The advertised `copilot-driver/input-status` and `copilot-driver/native-send`
operations accept both their existing bare wire names and the leading-underscore
forms emitted by standard ACP SDK extension-request helpers. Unknown operations
remain unsupported; prior callers need no syntax change.
Bridge's launch-selected retained inbox uses this client rather than a
second direct SDK sender; Bridge keeps its own durable handoff and
consumption authority. Restoration after a previous controller remains
unqualified, not an automatic re-hook.

### Opt-in headed source-authorization receiver

The Python `headed.launcher.run_headed(..., auth_wait=...)` entrypoint adds
`copilot-driver.headed-auth-wait`, version 1. Its absence preserves legacy
one-shot behavior. The compact CLI, application and existing callers do not
enable this receiver. This is provider implementation, **not native Copilot
history/UI qualification or deployment authority**.

The explicit configuration has exactly `source_owner`, `source_revision`
(nonempty immutable owner/configuration bindings), `lease_revision` (positive
integer), `budget_seconds` and `expires_at` (Unix wall time). The receiving
caller owns verification of the actual source and must supply no more than
its remaining authorization. Budgets must be finite, positive and at most
3600 seconds. Either wall expiry or monotonic budget exhaustion stops the
native process, so clock rollback cannot extend the monotonic grant. Stop
begins up to one second early to reserve the bounded SDK-freeze exchange;
sub-second grants stop immediately.
An accepted replacement wakes the deadline watcher. A grant already inside
the stop reserve closes authorization synchronously and cannot start a new
native incarnation, including renewal of a stopped session.
Native input and other irreversible broker-control holds do not expire a
still-valid source grant or retire its native CLI. Active source renewal can
preserve that CLI without clearing its hold; recovery after actual source
expiry or revocation still refuses while the control hold remains.
No credentials are read, copied or accepted by the receiver.
An idle freeze acknowledgement does not mark owned retirement. SDK loss
before retirement, malformed records or incomplete observation drains preserve
an irreversible hold. Broker EOF follows the admitted SDK stream's bounded
drain rather than racing the final native replies.
An owned-retirement TCP reset is delivered as EOF to the auth stream reader
so complete buffered records still drain; an incomplete record remains held.
The retirement marker follows the owned native Job termination request, before
asynchronous settlement is queued; merely scheduling a worker cannot suppress
a connection-loss hold.

The private host-state file's `auth_wait` member declares the **running**
contract/version, loopback `port`, random `nonce`, `session_id`, receiving
`incarnation`, `source_owner`, `source_revision`, `lease_revision`, `phase`
and irreversible `hold`. A caller must select this exact live declaration;
a missing endpoint/declaration or unsupported contract is unsupported, never
permission to fall back to a raw SDK send or a version-based guess.
The nonce belongs only in the private control transport, not logs or reports.

One bounded JSON-line request per connection supplies exactly `contract`,
`version`, `nonce`, `session_id`, `incarnation`, `source_owner`,
`source_revision` and `operation`: `status`, `revoke` or `renew`.
`renew` additionally requires `lease_revision`, `budget_seconds` and
`expires_at`. Unknown fields/operations, obsolete bindings and non-increasing
lease revisions are refused. Replies have `ok` and either current status or
`auth-wait-request-refused`; refusal has no send/restart fallback.
Renewal during an active grant can replace its remaining finite budget.
Once expired/revoked, renewal is refused until exact owned stop and old
observation-stream drain finish.

Expiry first freezes adapter sends and captures pending/sending/native-input
state, then terminates and settles the retained Windows Job, including ordinary
inherited descendants. It does not kill a shared mux, remove Job limits or
resolve a process by name. The opt-in auth-wait launch refuses POSIX before
opening its endpoint: a private process group alone cannot enforce owner death
or source expiry after coordinator failure. Independent POSIX owner-death custody
and native qualification are still required; no guardian is inferred from group
creation. Ordinary `auth_wait=None` execution remains unchanged.

The admitted SDK output is a strict auth-state record or a complete JSON-RPC
response/notification envelope. Complete but invalid envelopes and non-finite
wire JSON hold recovery just like truncated observation. Expiry-task failure
remains monitored after SDK loss and reaches exact owned cleanup. A Windows
auth resume reuses the coordinator Job already armed by that same launch.
Coordinator cancellation retires the auth-owned native scope before cancelling
the expiry watcher or draining client transports. A connected client cannot
extend native execution while transport shutdown waits; final cleanup retains
the same exact retirement fallback. Legacy execution without `auth_wait` keeps
its existing shutdown order.

A confirmed idle, controlled stop enters `auth_wait`; a fresh source grant
then creates a new receiving incarnation and cold-starts `--resume` with the
same durable session ID. Original `-i`/`--interactive` input is removed,
positional launch input is refused, no request is buffered/replayed, and the
old endpoint binding cannot act on the new incarnation. Previous Host/client
connections close; callers must select the new private declaration, not
replay an uncertain send. Actual native history restoration remains unqualified.
Bare positional input before `--` and ambiguous attached short-seed forms
also refuse before endpoint or native creation; option values keep their
declared boundaries.
Missing/invalid observation, native input, closed/uncontrolled state,
pending/sending/outstanding work, connection loss or cleanup uncertainty
cannot be cleared by a lease. These remain held or terminally unavailable.
Ordinary lost-controller recovery remains unsupported.

`headed.auth_child.AuthPaneChild.native_scope` exposes an opaque in-process
identity only after the original native Popen handle and required Job have
been captured. Keep it with that exact retained child object.
`scope_settled(original_token)` returns `True` only after owned whole-Job
confirmation, native process wait and successful Job close; it returns `None`
for missing/foreign identity or failed/incomplete cleanup. Neither callable
polls, waits, confirms, terminates or acquires a source. Retirement progress
and partial confirmation are insufficient. This live-only token does not
publish environment/boot/conversation identity, a complete recovery descriptor,
post-loss reacquisition or current source authority. It does not wire a
recovery consumer or change the POSIX receiver refusal.

### Effect-free crash-recovery bookkeeping

The additive Python `headed.recovery` interface exports diagnostic crash
candidates and records original-owner recovery transitions. It does not add
a CLI/wire operation, canonical journal, observer, native recovery provider or
automatic recovery. `AuthWaitReceiver.crash_candidate(descriptor, ...)` reads
existing status without changing authorization, holds, incarnation or grants.
Missing observation remains unknown; silence or a candidate never authorizes
retirement. Nonces and credentials are not exported.

An immutable `RecoveryDescriptor` binds the original owner/worktree, descriptor
and source revisions, conversation, environment, boot, scope, incarnation and
lease revision. The original owner supplies a `RecoveryStore` with durable
atomic compare-and-swap, one record per exact descriptor and unique operation
IDs across descriptors. No production store adapter or lifecycle effect
callback is provided. A store's evidence references are assertions requiring
owner admission, not independently verified settlement or authorization.

`record_recovery_step` requires an exact store revision and records
`retire_intent`, `settled`, `resume_intent`, then `observation_restored`.
Resume intent records a newer authority revision; restored observation names
a different native incarnation of the same conversation. Duplicate identical
steps return the retained record, including after an acknowledgement is lost;
conflicting IDs, bindings, evidence or revisions refuse without retrying.
Old EOF/control holds and unknown-effect references remain sticky even after
observation is recorded as restored. No step proves idle or completed work,
clears the legacy receiver's refusal, resends a prompt, or executes an effect.
All status remains native-recovery-unqualified and reconciliation-required.
Actual provider settlement, current admission and native seedless history
restoration need separate qualification before any owner wires recovery.

`headed.recovery_codec.encode_recovery_record` and `decode_recovery_record`
exchange the complete typed record, not its lossy `status()` projection.
The UTF-8 JSON envelope has exactly `contract` (`copilot-driver.headed-recovery-record`),
integer `version` (1) and `record`. All descriptor/step fields, including null
optional step fields, are required; unknown or duplicate fields, unsupported
versions, incorrect types, invalid Unicode, floating-point/non-finite values,
invalid history and records over 262144 bytes refuse without repair or truncation.
No authorization/capability flags are serialized. Canonical encoding retains
the operation, exact binding, complete ordered history, holds and unknown effects.
`load_recovery_status(store, descriptor)` reads that exact binding without CAS
or any effects. Missing records remain unknown, not settled or absent sessions.
Neither codec availability nor successful decoding establishes store/native
admission. The compact receiving archive includes these pure modules but adds
no recovery command or native provider.

`PermitRecoveryIntent` is a separate, effect-free data contract, not version 2
of that record. `encode_permit_recovery_intent` and
`decode_permit_recovery_intent` use exactly `contract`
(`copilot-driver.headed-permit-recovery-intent`), integer `version` (1) and
`intent`, with the same strict encoding rules and byte limit. The intent carries
the unchanged original v1 descriptor, operation ID, intent phase and record
revision (1 for retirement, 3 for restoration), an opaque `source_lease_ref`,
independent `receiver_ref`/`receiver_revision`, original `permit_id` and
`original_permit_revision`, and the action-specific `permit_revision` and
`decision_ref`. Only the two execution-permit revisions are compared: the
recovery decision must be later than original execution. Neither source-lease
identity nor receiver ordering is converted into execution permission.
The receiver revision belongs only to its exact retained `receiver_ref`;
it cannot order a different receiver incarnation.

The descriptor's `lease_revision` and existing `RecoveryStep.authority_revision`
retain their v1 meanings. These codecs do not convert, reseal or rewrite old
records, infer an action decision from a counter, or validate current authority.
The owner must independently bind the intent to its exact committed recovery
record, original permit and current action-specific decision, source and native
scope before any future effect. Unknown work and legacy holds stay in that
original record; an intent cannot replace it or clear them.

The selected non-legacy entrypoint's existing `--support-json` response adds
`headed_recovery_intents`, naming this contract and supported versions with
`scope: data-only`, `owner_store: unqualified` and `effects_wired: false`.
The legacy front door returns an empty list. Missing metadata from an older
runtime means this optional data contract is unsupported, not that v1 records
have changed or all legacy operations must stop. Read the actual selected
executable's declaration, never a package version or a newer payload's metadata.
No ordinary caller emits these intents. Worktrees' store and passive permit
inspector remain unchanged and do not support this intent contract; their
own provider expansion and support/refusal declaration must clear review before
consumer use. No compatibility retirement or effectful integration is enabled.

See the [ACP WS boundary](../../plugins/agent-remote-driver/docs/session-host-websocket.md).
The compact dependency-free receiving archive supports TCP only. Tests stay
with the application and Bridge consumers; the canonical receiving-policy
units remain in the required actor-free packet.
