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
the pending input without instructing the agent to end its turn: an idle
transition alone cannot guarantee submission through an unavailable SDK or
an outstanding native effect. The callback performs
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
the original uncertain receipt and no-resend fence remain independent.
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
uncertainty, pending-native fences, original IDs and no-resend rules are unchanged.

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
`unknown`; the original is not resent. A timed-out native promise stays fenced
until it settles rather than accumulating another native call every tick.

The consumer reads pending original receipts even while muted, deregistered or
after closure. Only actual root `user.message` history from that same SDK/native
context, with the explicit source and complete original envelope association,
can record `consumed`. Sub-agent events, operator input, quoted envelopes,
reused message IDs and missing attribution cannot settle it. The outcome uses
the receipt's original consumer, including after an extension reload; it never
substitutes the new process or incarnation.

An unbegun original offer can survive a receiver reload and be delivered using
its original receipt identifier and envelope. Versioned final admission must
name the currently enrolled receiver even when that envelope retains the original
receipt consumer. A stale receiver cannot start an effect. A sending, unknown or accepted
handoff is observation-only across that reload: no new receiver obtains a
second SDK invocation.

Durable SDK history is read lazily, at most once per 30 seconds, and shared
across the bounded receipt page. It is not an event-handler operation or a
per-message RPC. A page awaiting the history-read cooldown keeps its cursor;
older unknown receipts cannot phase-lock all reads to the first page and starve
later consumption evidence. One outstanding history observation has its own
bounded slot; a rejected or timed-out read does not block compatible ordinary
delivery after fresh native-context checks and final admission. The original
receipts stay unresolved, and a timed-out history call is not repeated until
its promise settles. Legacy recovery holds and unresolved input-effect fences
remain enforced. Neither the bounded represented-event queue nor the legacy
in-flight cache is consumption evidence. Lost outcome replies are read back
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
Without an enrolled retained receiver, an explicit request can use only the
documented equivalent legacy revision with no additional required semantics;
other requests refuse before admission rather than escaping through legacy SDK
polling. This adds no enrollment requirement to ordinary legacy baseline input.

Conversion runs inside the existing queue transaction and retains the original
row, payload, IDs, key, order and admission/effect history. Missing metadata uses
this explicit legacy policy:

| Stored legacy kind | Migration / retained disposition |
|---|---|
| `prompt`, `notify`, `status-check`, `reply`, validated `startup:prompt:<id>` | Revision 1 envelope; existing prompt/startup readiness and one-shot SDK admission |
| `control:replay-history` with empty body | Revision 1; reconstruct bounded represented transcript tail through the existing history owner |
| `control:set-mode`, `control:set-mode-ready`, `control:answer-question` | Retain original type/body as revision 1; this consumer explicitly rejects them, never executes or silently converts them to prompts |
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
Enrollment and reloading do not replay uncertain work.

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
