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
Manager retains terminal attachment, options, UI, status and original native
admission. This finite operation does not wait for Copilot to exit.

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
It reads the provider's port/key rendezvous and sends only bounded,
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
inventory remains unknown. Listings share one absence-coverage
snapshot within their finite budget; live positive rows use their own reads.
Native process classification accepts paired double-quotes around the same
supported `-L` and command tokens as their ordinary Windows argument spelling.
Unknown options, malformed quoting and ambiguous server forms still refuse
classification; quoting support does not reduce census or registration coverage.
Selected-namespace absence uses qualified registration identities while still
covering every native server. The legacy name-only inventory API retains its
ambiguity refusal for duplicate logical names across namespaces.
Catalog discovery unions confirmed inventory identities with directory hints
before namespace filtering, including registrations published between the reads.
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
`--describe-contract`. `--observe` consumes at most 4 KiB of JSON with exactly
`version: 1` and `session`. Its response has the contract/version and
`status` (`present`, `absent`, `unknown`, `unsupported`), with incarnation,
client count and pane observations when present. Confirmed present/absent
exits zero; unknown/unsupported exits 3. Duplicate/unknown properties and
unknown options are refused. Older independently installed providers without
the declaration stay unsupported; callers must not downgrade to passive
PSMux CLI reads. Foreground attach/create and explicit provider writes still
launch PSMux and retain its startup-cleanup risk.

The explicit headed coordinator now runs in the original caller-owned pane.
It stages the launch-bound SDK extension, retains the native child handle and
serves the same Session Host after native session/PID binding. The ordinary
application supports WS/TCP; `prepare_headed_command` retains a TCP-only compact
receiving executable under the original attempt, independent of the caller's
venv. Neither path allocates a workspace, mux or additional launch journal.
Original native startup admission and literal prompt argv are preserved.
Full caller migration and restoration qualification remain in progress.

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
