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

An original Copilot Driver headed launch supplies the versioned private
`COPILOT_DRIVER_HEADED_CLIENT` declaration and thereby selects retained
delivery for that launch. The extension loads the client from the retained
core assets and sends through the same Session Host/native SDK edge. It
does not call its own SDK handle's `send` or fall back to the old standalone
driver. Missing or unsupported selected support remains held.

That client preserves queue/steer urgency and the actual native message ID;
it never authorizes abort or mutable session controls. The original state,
nonce, native session/PID and first-controller support must match. A previous
controller's buffered RPCs, changed state or lost acceptance do not permit
reconnection or re-hooking. Native user input revokes control. A portable
selection retains the existing exact native loadout checks before the common
send; the new path does not waive duplicate/missing-root refusal. Read-only
SDK metadata and history still serve original consumption recovery. Full
controller restoration is a separate qualification, not an inferred grant.

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

The selected consumer requires an advertised HTTP protocol of at least **50**,
the admitted local endpoint, fresh exact registration and the existing
installation/inbox owners. It never downgrades to legacy delivery when those
prerequisites fail. Existing shared-token access remains unchanged; there is no
new consumer credential. Proxy/container ingress remains unsupported.

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
in-flight cache. Pending legacy mutation controls and unassociated legacy
messages can prevent enrollment or polling; they are not silently acknowledged
or discarded.

The [explicit portable mux receiver](repository-hosting.md#explicit-portable-mux-receiving)
selects the same consumer with a `portable_launch` attempt observation and
requires `portable_inbox: 1`. Its first remote enrollment must match the host's
claimed launch reservation, exact native ID and endpoint. A same-process
successor uses the existing predecessor CAS, not another native launch; another
attempt or process cannot inherit it. Ordinary remote venues and proxy ingress
remain unsupported. Portable prompts cannot enter the legacy inbox while
enrollment is pending. The selected standalone driver supplies mutation control
and its attributed source; Bridge does not gain a direct-SDK fallback.

## Invocation and recovery

Registration remains independent of enrollment. Off the SDK event handler,
the serialized consumer negotiates the actual installation and a fresh extension
incarnation. A lost enrollment reply retries the original predecessor CAS,
not a new negotiation that treats itself as its predecessor.
If the frontend restarts while this consumer stays alive, a current-owner
refusal triggers bounded renegotiation. The recovered installation/epoch and
current-or-original-predecessor observations must still match; enrollment
retries the exact original CAS. A new binding or foreign consumer remains held.

Polling requests at most one message. The existing SQLite owner retains its
original handoff before exposing the payload. After checking the current native
context, the consumer requests one-shot final admission and checks the context
again immediately before invoking the SDK. Only that invocation's true reply
permits a send. A lost reply, false result or changed context grants no retry.

Direct SDK sends retain `source: "agent-bridge"`; selected driver sends use their
explicit `agent-remote-driver-<uuid>` source. Both retain a root envelope carrying the exact
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
