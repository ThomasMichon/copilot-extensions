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
an SDK send can block. Root callbacks for the registered native session can
then tell the agent to end its turn for queued steering. The callback performs
no network, subprocess, file or history work and never awaits the drain.
Nested/foreign callbacks, intentional `/peer` mute and unavailable current
registration produce no notice. A repeated notice is coalesced for 30 seconds.
No hook acknowledgement, peer reply or new delivery queue is required.

SDK acceptance does not clear pending input. A native root input observation,
or the retained consumer's existing exact original-receipt history check,
clears its own observed ID. A session-binding change invalidates the old set.
At the 1,000-ID bound, observation becomes explicitly incomplete rather than
evicting known IDs or claiming the inbox is empty. Unknown overflow is not a
reason to replay input; it does not itself demand an end-turn after all known
pending IDs have cleared.

This is best-effort steering from this extension's observed facts, not a fresh
authoritative inbox read on every tool call. It cannot wake an idle receiver
without a tool boundary, repair a hung SDK or certify callback support from a
package version. The selected runtime must actually accept and invoke the
public hooks; native hook-policy refusal is not bypassed. The model-free source
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

## Prerequisites and unsupported cases

The current consumer requires HTTP protocol **50** or newer and the independently
advertised `compatible_native_inbox: 1` capability from the actual serving
daemon. It selects `receiving_api: "native-sdk-v1"` on the existing retained
requests. Missing support refuses before enrollment or final admission; there
is no automatic legacy or private-controller fallback. Older consumers omit the
selector and retain their original installation-bound contract unchanged.
Existing shared-token access, the admitted local endpoint, frontend readiness,
writable prepared schema and fresh exact native registration remain required.
Installation identity, epoch, process birth and PID are not ordinary message
admission requirements. Current Core omits process diagnostics from new-mode
requests; optional valid observations from other consumers remain advisory.
Proxy ingress remains unsupported.

The already-used `@github/copilot-sdk` session must expose `send()`, `getEvents()`
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
mutation controls. Interrupts, mode changes and history replay remain
unsupported: selected retained delivery never aborts a turn or replays an
in-flight cache. Pending legacy mutation controls prevent enrollment. Unassociated legacy queue
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
its original receipt identifier and envelope. A sending, unknown or accepted
handoff is observation-only across that reload: no new receiver obtains a
second SDK invocation.

Durable SDK history is read lazily, at most once per 30 seconds, and shared
across the bounded receipt page. It is not an event-handler operation or a
per-message RPC. A page awaiting the history-read cooldown keeps its cursor;
older unknown receipts cannot phase-lock all reads to the first page and starve
later consumption evidence. Neither the bounded represented-event queue nor the legacy
in-flight cache is consumption evidence. Lost outcome replies are read back
from the original receipt, or the same outcome is reported again without
another SDK effect. Missing historical evidence remains visibly outstanding;
there is no SDK idempotency guarantee or inferred nonacceptance.

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
