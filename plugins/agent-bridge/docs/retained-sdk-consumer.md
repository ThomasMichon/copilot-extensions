# Opt-in retained SDK consumer

Back to [Agent Bridge](../README.md) and the
[retained inbox owner contract](runtime-custody-foundation.md#explicit-local-transport-adapter).

The interactive JavaScript extension can select retained delivery explicitly
with `AGENT_BRIDGE_RETAINED_INBOX=1` (`true`, `on` and `yes` also select it).
Omitting the setting preserves ordinary legacy delivery, including its existing
default-on behavior. This source integration is not native-platform,
installed-cohort or guarded-restoration qualification.

The [Core package](../../../libs/copilot-driver-core/README.md) owns the
registration/delivery closure. Bridge keeps one SDK entrypoint, which supplies
its original plugin root to the Core activation. Source checkouts select the
canonical Core files; released payloads use the existing packaged-asset
projection. Portable receiving reads those resources from its selected Core
package, not a guest Bridge installation. A partial native projection refuses
before SDK activation rather than falling back to source. The same original
plugin root is checked against native loadout observations after relocation.
No additional SDK join, default-mode change or permission declaration is added.

The Core-owned SDK adapter selects the current
`@microsoft/copilot-sdk/extension` namespace, or the preceding
`@github/copilot-sdk/extension` only when the current namespace is absent.
It preserves the original registrations while mapping tools to
`tools.definitions`, callbacks to `hooks.handlers`, commands to
`interaction.commands` and permission handling to `permissions.onRequest`
on current hosts. Dependency/evaluation failures and failed joins are not
retried through another SDK. Each released extension carries its own projected
helper; no sibling plugin or installed runtime supplies it.

An original Copilot Driver headed launch supplies
`COPILOT_DRIVER_HEADED_CLIENT` and thereby selects retained delivery for that
launch. Ordinary receiving uses the already-joined public SDK session, not
the private controller client. Missing or revoked private control cannot gate
ordinary queue/steer messages or acquire a new controller. Mutation controls,
launch replacement and controller restoration retain their independent owners.
Portable startup still checks the exact native plugin loadout through the
public SDK before the final send; duplicate/missing roots remain unsupported.

## Post-tool pending-input notice

The extension registers the public SDK's `onPostToolUse` and
`onPostToolUseFailure` callbacks. Its existing off-loop inbox and original
receipt reads populate a bounded in-memory set of pending message IDs before
an SDK send can block. Root callbacks for the registered native session report
the pending input, states that it is submitted when the session next goes
idle, and tells the agent not to end its turn solely for the notice or to
send an acknowledgement or replay input. The callback performs
no network, subprocess, file or history work and never awaits the drain.
Nested/foreign callbacks, intentional `/peer` mute and unavailable current
registration produce no notice. A repeated notice is coalesced for 30 seconds.
No hook acknowledgement, peer reply or new delivery queue is required.

Successful SDK submission clears its own ID from this notice, including
an accepted original receipt recovered after reconnect. The notice tracks work
still awaiting submission, not submitted input awaiting model consumption.
A native root input observation, or the retained consumer's existing exact
original-receipt history check, also clears its own observed ID. Clearing the
notice never marks a receipt consumed or answered; those still require their
original independent evidence. A session-binding change invalidates the old set.
The most recent 1,000 confirmations suppress stale offers within that binding.
A late successful SDK reply clears attention even after its reporting deadline;
the original uncertain receipt and its idle re-attempt rules remain independent.
At the 1,000-ID bound, observation becomes explicitly incomplete rather than
evicting known IDs or claiming the inbox is empty. Unknown overflow is not a
reason to replay input; it does not itself demand an end-turn after all known
pending IDs have cleared.

The existing inbox owner schedules a coalesced, off-loop drain after a root
`assistant.idle` or `session.idle` event and after successful registration or
heartbeat re-registration. It adopts the joined SDK session's current ID,
including on resume when ordinary session events omit that ID. The existing
two-second poll remains the backstop for arrivals while already idle.
Idle steering uses the SDK's `enqueue` mode to start a new turn; active-turn
steering retains `immediate` mode. Intentional mute, current-native checks and
outstanding-send fences still apply. Shutdown cancels deferred wakes.

The notice is best-effort information from this extension's observed facts,
not a fresh authoritative inbox read on every tool call. It does not itself
wake the receiver, repair a hung SDK or certify callback support from a package
version. The selected runtime must actually accept and invoke the public
hooks; native hook-policy refusal is not bypassed. The model-free source
controls do not qualify that native lane or an installed reload.

No hook return, prompt-submitted callback or send reply is treated as model
consumption. Timed content fallback remains unsupported until the native
provider supplies enforceable pre-input duplicate suppression and evidence
that the exact post-tool content reached model-facing input. Existing retained
uncertainty, pending-native fences, original IDs and the idle re-attempt rules
below are unchanged.

The legacy represented-event flusher separately preserves actual attributed
root-input events across failed or unconfirmed event POSTs. It retries only
that evidence, with the original native session and event identity, not an SDK
send or message body. Ordinary representation remains best-effort. Bounded
overflow evicts ordinary events first; an all-receipt overflow is reported off
the SDK event loop and can leave original delivery receipts unknown. This is
an in-memory retry inside the existing event buffer, not a durable outbox or
restart guarantee. Re-registration never retags retained evidence as another
session's input, and native hook/SDK acknowledgement alone creates no event.
Sub-agent events likewise cannot change the parent session's registration or
native binding; only root events can adopt a resumed conversation ID.

## Consumption matching and diagnostics

The original-receipt matcher accepts a root `user.message` from an admitted
delivery source only when its session, consumer incarnation, handoff and message
ID agree. It uses transformed input when present. One bounded runtime
`current_datetime` ISO-timestamp preamble may precede the exact opening message
header; arbitrary prefixes, quoted or nested headers and repeated preambles do
not match. Matching durable native history records mechanical input observation,
not model understanding, an answer or completion. It never authorizes another
SDK send.

The existing optional `agent_bridge.telemetry` sink receives `inbox_delivery`
metadata events. Backend `admitted`, `handoff_started`, `sdk_accepted`, `consumed`
and `unknown` observations run after their corresponding database operation
commits. A fresh admission has no invented consumer or handoff identity.
Available original message, handoff, native-session and consumer-incarnation
identities correlate later stages; `observed_at` is the telemetry observation
time, not a new native-event timestamp or delivery receipt.
Receipt stages read their original consumer identity from the retained handoff,
including an offer adopted by a successor. Hook metadata instead names its
currently observing consumer when that identity is available.

The extension uses its existing bounded represented-event buffer and flusher
for `match_failed` reason counts and `hook_notice` observations. The latter names
`pending-input-set-v1`, its count, incomplete flag and retained/legacy source;
this is the hook's derived set, not an authoritative native SDK queue count.
Reason codes and scalar fields are allow-listed. Prompt, response, event body,
tool content, error text and filesystem paths are excluded. Diagnostic ingress
never changes receipts or status projections. These events use only an already
owner-started bounded telemetry buffer, including the configured retained spool.
Synchronous legacy/custom sinks are skipped, not wrapped or called on the
handoff path. Missing, closed, full or contended buffers drop observations;
disabled or ineligible sinks also skip receipt lookup. No new writer starts
from emission. Failures remain best-effort, and no new endpoint, poller, service
or replay path is added.

## Prerequisites and unsupported cases

Ordinary producer acceptance is separate from these consumer prerequisites.
The serving daemon advertises `durable_live_inbox: 1` for the existing
`POST /api/v1/live-sessions/{session_id}/messages` operation. A valid known
represented conversation accepts ordinary input into `live_messages` even
when its heartbeat, frontend, enrollment or delivery epoch is unavailable.
The immutable producer-target observation and original operation key survive
registration cleanup. Acceptance does not resume a conversation or select a
replacement; a retired native target is not silently rebound to a successor.
An exact selector naming a retired routing alias is invalid, while a known
dormant conversation remains a valid queue target.
The receipt remains `admitted` with `queue_reason: "awaiting_consumer"` until
a receiver owns the handoff. This is not SDK acceptance or consumption.

Schema 50 expands the existing queue with nullable producer-target metadata
and retention/input guards. Preexisting rows and effects remain unchanged.
An enrolled receiver selects its delivery admission when polling, not when
the sender writes. Legacy baseline receiving remains supported for unenrolled
targets, but cannot take unclaimed retained work after receiver enrollment or execute
unknown message revisions/required semantics. Shared-token authentication,
valid target assertions and writable prepared storage still apply.
Controls and typed startup retain their independent effect admission.

A supported legacy body poll durably records its ownership before returning
new producer input. Later enrollment does not transfer that original to another
SDK sender or block progress on new input. Until its original acknowledgement,
the receipt reports `legacy_delivery_unconfirmed`; a lost reply is not proof
that no effect occurred. Late original acknowledgements remain idempotent after
enrollment and registration cleanup. Unsupported exact-native or startup polls
do not claim input they cannot return. Claimed originals cannot be reset into
fresh delivery, and no new SDK acceptance or consumption is inferred.

This is provider-first expansion: prior ordinary callers keep their wire form,
and supported current/prior receivers keep the existing retained-inbox wire
contract. Callers requiring durable acceptance must discover the capability
on the actual serving peer; older providers do not promise it. Older binaries
refuse a schema-50 database rather than deleting or downgrading accepted rows.
Rollback requires a schema-50-capable provider preserving producer metadata,
original handoffs and unknown outcomes; do not lower the recorded schema.
CLI dormant-target discovery and ACP producer adoption remain separate work.

The current consumer requires HTTP protocol **50** or newer and the independently
advertised `compatible_native_inbox: 1` capability from the actual serving
daemon. It selects `receiving_api: "native-sdk-v1"` on the existing retained
requests. Missing support refuses before enrollment or final admission; there
is no automatic legacy or private-controller fallback. Older consumers omit the
selector. An older ordinary SDK consumer can use the same compatible inbox
when the serving daemon has neither an installation-governance helper nor a
configured installation context. Its original wire form still requires process
observations and retains exact original-consumer outcome matching. Configured
or failed governance and older portable consumers keep their original
installation/reservation-bound adapter; missing owners are not an unconfigured
provider. Automatic selection also requires the stored registration's venue to
be absent inside each negotiation, enrollment, poll and final-admission
transaction; omitting a portable launch observation cannot reclassify a remote
target. Explicit native selection and late original outcomes are unchanged.
Unknown selectors remain errors.
Existing shared-token access, the admitted local endpoint, frontend readiness,
writable prepared schema and fresh exact native registration remain required.
Installation identity, epoch, process birth and PID are not ordinary message
admission requirements. Current Core omits process diagnostics from new-mode
requests; optional valid observations from other consumers remain advisory.
Proxy ingress remains unsupported.

The already-joined host SDK session must expose `send()`, `getEvents()`
and `rpc.metadata.snapshot()`. Its actual `sessionId`, metadata session ID and
working directory must agree with the registered native context. Metadata alone
cannot redirect a stale SDK handle's send target. A resume whose SDK handle
does not follow the actual native session is explicitly held.

Retained messages support `steer` and `queue` only. Typed queued startup prompts
must carry their original operation-bound successful plugin observation. The
consumer rechecks that selection before and after the final grant; missing,
disabled or changed inputs remain held without resend.
With `readonly_plugin_controls: 1`, the extension polls only plugin observations
through `controls?readiness_only=true`. This filter neither claims nor expires
mutation controls. Interrupts and mode changes remain unsupported; bounded
represented-history reconstruction uses the versioned control path below,
never a model prompt, tool rerun or in-flight send-cache replay. Backlog type
compatibility never prevents valid enrollment. Unassociated legacy queue
messages retain their original IDs and receive one immutable handoff before
delivery. A previously claimed or begun message is never treated as a fresh
offer, acknowledged or discarded.

The [explicit portable mux receiver](repository-hosting.md#explicit-portable-mux-receiving)
selects the same consumer with a `portable_launch` observation and additionally
requires `portable_inbox: 1` and `readonly_plugin_controls: 1`. Original launch
allocation and mutation controls remain separate from ordinary receiving.
The same exact native session and admitted endpoint are required; this is not
permission to create or resume a dormant target. Portable prompts cannot enter
the legacy inbox while enrollment is pending.
Durable acceptance records that retained-only boundary with the original input,
so registration cleanup cannot expose it to legacy polling or acknowledgement.
Its explicit unique-plugin/root selection is checked through readonly public SDK
observations before begin and again at the SDK edge. A known missing or duplicate
loadout therefore cannot consume the one-shot grant, and no private controller
is constructed to perform that check.

## Invocation and recovery

Registration remains independent of enrollment. Off the SDK event handler,
the serialized consumer observes the current receiver and registers a fresh
extension receipt identifier. The returned binding is an opaque mode selector,
not an installation or credential. A lost enrollment reply retries the original predecessor CAS,
not a new negotiation that treats itself as its predecessor.
If the frontend restarts while this consumer stays alive, a current-owner
refusal triggers bounded renegotiation. The recovered mode binding and
current-or-original-predecessor observations must still match; enrollment
retries the exact original CAS. A new binding or foreign consumer remains held.

Ordinary diagnostic metadata refresh does not retire a compatible receiver.
If an actual registration loss retires it, a fresh observation of no current
receiver can register a new opaque receipt identifier after liveness returns.
Original handoffs keep their old identifiers and outcomes; this is neither
native-session resume nor a new send opportunity for begun input.

Polling requests at most one message. The existing SQLite owner retains its
original handoff before exposing the payload. After checking the current native
context, the consumer requests one-shot final admission and checks the context
again immediately before invoking the SDK. Only that invocation's true reply
permits a send. A lost reply, false result or changed context grants no retry.

Direct SDK sends retain `source: "agent-bridge"`; recovery also recognizes the
explicit `agent-remote-driver-<uuid>` source of earlier original sends.
Both retain a root envelope carrying the exact
native session, consumer incarnation, original handoff and message ID.
SDK admission records `sdk_accepted`, not consumption or semantic completion.
An ambiguous exception, missing SDK message ID or timeout records/retains
`unknown`. It is not resent immediately; the idle re-attempt rules below decide
whether it is later consumed, submitted again or failed. A timed-out native
promise stays fenced until it settles rather than accumulating another native
call every tick.

The consumer reads pending original receipts even while muted, deregistered or
after closure. Only an actual root `user.message` from that same SDK/native
context, with the explicit source and complete original envelope association,
can record `consumed`. Sub-agent events, operator input, quoted envelopes,
reused message IDs and missing attribution cannot settle it. The outcome uses
the receipt's original consumer, including after an extension reload; it never
substitutes the new process or incarnation.

The existing live SDK subscription also supplies this original-input evidence
when the root event includes the SDK's native consuming `turnId`. A caller's
`clientTurnId` or a queued message without that native witness does not qualify.
Older/unavailable live evidence keeps the existing historical recovery path;
no new minimum provider version is inferred from a package number.
Its callback retains only the native binding, message correlation and envelope
header, not the message body. Bounded headers are copied into independent string
storage so substring backing stores cannot keep the full input alive.
It performs no network, SDK, filesystem or log I/O.
The existing off-loop recovery first matches those observations against the
original receipts, rechecks native context and reports through the existing
original-outcome owner before waiting for historical evidence. A failed or
pending history read therefore need not conceal already observed live input.
An unresolved input effect still retains its original serial fence.
Observed originals beyond the current historical page use their existing
exact-receipt lookup, bounded to 100 additional originals per recovery tick.
This does not move the historical cursor or skip older unresolved receipts.

The observation buffer holds at most 1,000 headers of at most 4,096
characters each. Overflow discards the oldest observation and reports
uncertainty off the native callback; it never invents consumption for the
discarded evidence. Native-session changes discard other bindings, and shutdown
clears the buffer. This is bounded in-memory evidence, not durable restart
recovery, another inbox or a new consumption authority.

An unbegun original offer can survive a receiver reload and be delivered using
its original receipt identifier and envelope. Versioned final admission must
name the currently enrolled receiver even when that envelope retains the original
receipt consumer. A stale receiver cannot start an effect. An accepted handoff
is observation-only across that reload. A `sending` or `unknown` handoff is
re-attempted while idle, as described next.

## Idle submission and bounded re-attempts

No ordinary record is left ambiguous. When the provider health capabilities
include `retained_delivery_reattempt: 1`, the receiver negotiated
`message_base: 1` and the extension tracks the main turn, the receiver
manages every ordinary record that is not `sdk_accepted`, `consumed`,
rejected or failed. That includes `sending`/`unknown` records whose handoff
started under an earlier consumer incarnation of the same native session.
While the session is idle, each drain tick:

1. Settles records already present in the session's root user-message input.
   Live observations are checked first. Durable history is read only for
   started records not yet verified in the current idle episode, in one
   batch per tick, at most once per 30 seconds. A match carrying the
   retained header and message ID reports `consumed`, and nothing is sent.
2. Requests a new attempt for each remaining started record through
   `poll(reattempt=[...])` and `begin(attempt=N)`. The bounded re-attempt
   list holds only unsubmitted originals; accepted receipts that are still
   seeking consumption never occupy it. The backend re-grants the
   same original (ID, body, order, handoff and receipt history) to the
   current enrolled receiver and records the attempt in
   `live_message_attempts`, indexed by session so `pending-queue` failure
   reads never scan other sessions' attempts. The grant is compare-and-set on the attempt
   number, is one-shot, and keeps authentication, binding, current-session,
   worktree and mute checks. The original start counts as attempt one. When a
   fourth attempt is requested, the backend records the terminal
   `failed`/`delivery_attempts_exhausted` disposition instead of granting it.
   The record then leaves recovery and the pending notice and appears in the
   `pending-queue` `failed` list. Late `sdk_accepted`/`consumed` evidence for
   an exhausted original is still accepted: it is kept on the final attempt
   row, the `/outcome` reply returns the observed stage so the receiver's
   report settles, the terminal disposition is unchanged, and the record
   leaves the `failed` list.
3. Submits granted and offered records through the ordinary `session.send`
   path with the original `<agent-message ... msg-id=...>` header and reports
   `sdk_accepted`.

If history cannot be read, the started record is not resent on that tick, and
the check is retried on the next idle tick. It never becomes a permanent
"no resend". An offered record that never began cannot be in native input, so
it needs no history read.

When a running-to-idle transition finds queued records and every one of them
proves already delivered, so nothing is submitted, the receiver sends one short
resume prompt for that idle episode. It sends nothing for an empty queue,
for an episode that submitted anything, for later ticks of the same episode,
or for the startup idle. The resume prompt also requires the
`retained_delivery_reattempt` capability and a `message_base` 1 enrollment.
Without the capability, the legacy behavior is unchanged: started records are
observation-only, the notice clears for them, and no unsolicited turn is
started.

History is bounded because a session's durable event log can be very large.
The SDK's `getEvents()` takes no options and returns the whole log. When the
runtime exposes the `session.rpc.eventLog.read` RPC, the receiver reads root
`user.message` events backward, in pages of 100. It reads at most eight pages
per read and stops at the oldest candidate's start time. It caches a
per-session high-water mark, so later reads only examine newer events. A deep
backward scan toward an older handoff keeps its cursor across reads; only the
newest page's event IDs are kept to detect overlap, so progress never resets
however many user messages precede the handoff. The cached evidence is
bounded too: at most 1,000 compact retained headers are kept. Headers for
settled (consumed or rejected) originals are dropped and never cached again,
and the originals being checked are pinned. If the cap evicts another
original's header, that original loses its history coverage until a fresh
scan re-reads it while it is pinned, so an evicted match can never authorize
a duplicate submission. Without
that RPC, each started record's handoff is checked with at most one full
`getEvents()` read for the life of the extension. Its re-attempts then rely on
live observation; if that observation overflows its bound, the memo is cleared
so the next re-attempt reads history again first. After a submission is
accepted, the original still seeks consumption evidence through the same
bounded read; without the RPC, that is one full read per handoff.

The receiver checks live input again immediately before requesting a
re-attempt grant and immediately before the SDK send; a matching input settles
the record as consumed instead. A re-attempt whose session leaves idle before
the send is deferred, or reported `unknown` once granted, and retried on a
later idle. The resume prompt waits until every recovery page of the idle
episode has been reconciled, so records beyond the first page are never missed.
A fresh offer delivered during an idle episode that began with queued records
joins that episode, so a failed or uncertain send also withholds the prompt.

With re-attempt support, a history check deferred by the cooldown or a failed
read never holds a recovery page. The checks are kept in a bounded set and
folded into the next read, and later pages keep being discovered. Any
unsubmitted original left unverified withholds the resume prompt for that
sweep.

Durable SDK history is read lazily, at most once per 30 seconds, and shared
across the bounded receipt page. It is not an event-handler operation or a
per-message RPC. Without re-attempt support, a page awaiting the history-read
cooldown keeps its cursor;
older unknown receipts cannot phase-lock all reads to the first page and starve
later consumption evidence. One outstanding history observation has its own
bounded slot; a rejected or timed-out read does not block compatible ordinary
delivery after fresh native-context checks and final admission. The original
receipts stay unresolved, and a timed-out history call is not repeated until
its promise settles. Legacy recovery holds and unresolved input-effect fences
remain enforced. Queue/cache presence and an SDK acknowledgement are not
consumption evidence; the directly observed native input must match its
original receipt. Lost outcome replies are read back
from the original receipt, or the same outcome is reported again without
another SDK effect. Missing historical evidence remains visibly outstanding;
there is no SDK idempotency guarantee or inferred nonacceptance.

## Message contracts, migration and rejection

The existing negotiation response advertises `message_base: 1` independently of
authentication, installation and the retained-inbox transport version. A capable
consumer adds that observation to its request only after negotiation. Payload
support is not stored inside the original receiver identity or handoff receipt:
older supported readers can still parse those records after provider rollback.
Older providers receive the original request form; absent support is not guessed.
Older consumers can enroll on an upgraded provider without interpreting controls:
unstarted controls unsupported by that reader receive a terminal `unsupported_reader`
disposition, not delivery or consumption. Plugin-readiness controls retain their
separate readonly owner.

Each newly produced row stores a base version, type, type revision and explicit
`requires` list, independently of package versions. The poll adds trusted original
message ID, sender, exact session, registered worktree and receiver incarnation.
Additive optional properties are compatible. Unknown base/type revisions,
malformed metadata and unknown required semantics are rejected before effects;
their minimal trusted row correlation remains usable even when metadata is invalid.
Producers can explicitly specify `message_version` and `message_requires` on
ordinary admission. Idempotency includes those semantics.
Before emitting either explicit field, producers must observe
`versioned_messages: 1` from the actual serving daemon's existing health
capabilities. The delivery client enforces that selection before POST; raw
HTTP callers have the same obligation. Older providers may ignore additive
JSON fields, so blindly sending required semantics to them is unsupported.
Without an enrolled retained receiver, only the documented equivalent legacy
revision with no additional required semantics can enter legacy SDK polling.
Other well-formed ordinary inputs are durably accepted but remain queued for
a capable receiver's interpretation or explicit correlated rejection. They
cannot be acknowledged or marked consumed through legacy delivery.

Conversion runs inside the existing queue transaction and retains the original
row, payload, IDs, key, order and admission/effect history. Missing metadata uses
this explicit legacy policy:

| Stored legacy kind | Migration / retained disposition |
|---|---|
| `prompt`, `notify`, `status-check`, `reply`, validated `startup:prompt:<id>` | Revision 1 envelope; existing prompt/startup readiness and one-shot SDK admission |
| `control:replay-history` with empty body | Revision 1; reconstruct bounded represented transcript tail through the existing history owner |
| `control:set-mode`, `control:set-mode-ready` | Retain original type/body as revision 1; this consumer explicitly rejects them, never executes or silently converts them to prompts |
| `control:answer-question` | Reserved for the independent exact-native question poll/ack owner; never offered to, rejected by or executed by the prompt consumer |
| Other kinds or nonempty legacy replay payload | Unknown revision; terminal correlated rejection, never guessed payload semantics |

`POST .../retained-inbox/disposition` uses the same authenticated local ingress,
exact current worktree/session and receiver CAS fences as polling. A pre-effect
`rejected` result includes immutable machine code and bounded human reason.
Rejected entries leave deliverable, recovery and busy-input projections, but
remain in original-row receipts/audit. Neither `delivered_at` nor `consumed_at`
is set. The normal message/keyed receipt owners expose `stage: rejected` and the
disposition; delivery observation terminates as rejected rather than a timeout.
Legacy ACK, withdrawal and expiry cannot overwrite that disposition.
Rollback readers see an existing claimed/handoff association and conservative
`unknown` outcome fence, never a new offer. That compatibility fence is not
evidence of SDK invocation: the separate disposition and unchanged null send/
consumption timestamps record the actual pre-effect rejection.
Disposition-specific database guards also retain control audit, original input
and native context, and prohibit false SDK timestamps or terminal rewrites.
Current pruning and alias-fold queries skip dispositions; older writers cannot
silently delete or retag them. The original control-custody prohibition remains
unchanged: a disposition is not an SDK handoff or grant.

History reconstruction separately requests `started`; only that invocation's
`begin: true` response permits its one effect. Lost/repeated grants never permit
replay. `completed` means represented history reconstructed, not SDK acceptance
or agent consumption. The history POST returns `replay_accepted`, atomically
selected by the existing backfill owner independently of the ingested count.
An accepted empty or deduplicated batch can ingest zero events. Expired or
unsolicited history returns false even with HTTP success. The retained callback
requires explicit true acceptance for its exact native context; missing,
malformed, refused or lost acknowledgement remains `unknown`, never completed
or permission to replay. Ordinary non-replay responses retain their prior shape.
A missing result remains `unknown`/`control_started`.
Any prior claim, send start, delivery or consumption refuses a new rejection or
control start; migration cannot certify earlier effects absent or canceled.
Enrollment and reloading do not replay uncertain control work. Ordinary records
follow the idle re-attempt rules above.

Retiring or changing a retained message type requires a documented, tested
migration or explicit terminal-rejection strategy for every supported stored
revision. Provider expansion precedes emission of new required semantics;
supported rollback must preserve receipts and uncertainty, not reenroll a
retired receiver or send input through a parallel transport.

## Reproducible source controls

`tests/retained-delivery.test.mjs` exercises controlled SDK boundaries and
`tests/retained-extension.test.mjs` loads the actual extension with isolated SDK,
endpoint and timer dependencies. `tests/test_plugin_readiness_extension.py`
runs both with the legacy/readiness regressions. VM controls link named imports
without changing the extension body.

`tests/test_portable_mux_joined.py` composes the actual Bridge CLI,
CodeSpace detached provider, selected archive and receiver, both extracted
extensions, SQLite handoff and standalone driver HTTP route. Its SDK, native
mux, provider process/SSH/forwarding and POSIX-to-private-scratch carrier are
explicit substitutions. It proves one admitted/consumed send and same-conversation
rejoin, plus duplicate-loadout refusal, not native Copilot/TMux or live transport.

`tests/test_retained_sdk_recovery.py` routes independent Node consumers through
the actual ASGI/installation/SQLite owners. It exits consumers after poll,
final admission, before/after the controlled SDK effect and after accepted or
consumed reporting; reopened storage and a closed frontend retain the exact
original payload, handoff and consumer. The effect sink is synthetic, not a
native SDK session. Run these selectors through the repository's bounded
plugin test path. A test environment without Node skips them and provides no
execution evidence; use a Node-equipped isolated runner for these cases.

Full native Linux/Windows behavior, secured proxy ingress, remaining custody
cohorts, installer restoration and authorized running adoption remain separate
gates in the [restoration effort](../../../efforts/active/bridge-retained-runtime-restoration/README.md).

`tests/headed-client.test.mjs` exercises the common framed broker client and
native SDK adapter, including urgency, actual acceptance, unknown holds,
prior-controller refusal and portable loadout checks. The core's staged Node
client also runs against the real Python Session Host. The isolated Windows
native headed fixture additionally proves one same-conversation Bridge
connector send through retained assets and actual Copilot SDK code, detach
survival and exact private mux cleanup. It does not qualify the full Bridge
acquisition/UI or restoration cohorts.
