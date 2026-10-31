# Opt-in retained SDK consumer

Back to [Agent Bridge](../README.md) and the
[retained inbox owner contract](runtime-custody-foundation.md#explicit-local-transport-adapter).

The interactive JavaScript extension can select retained delivery explicitly
with `AGENT_BRIDGE_RETAINED_INBOX=1` (`true`, `on` and `yes` also select it).
Omitting the setting preserves ordinary legacy delivery, including its existing
default-on behavior. This source integration is not native-platform,
installed-cohort or guarded-restoration qualification.

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

Retained messages support `steer` and `queue` only. Startup prompts, interrupts,
mode changes, plugin-readiness controls and history-replay controls have no
qualified retained effect/recovery path. Selected retained delivery does not
poll or apply controls and never aborts a turn or replays an in-flight cache.
Pending legacy controls and unassociated legacy messages can prevent enrollment
or polling; they are not silently acknowledged or discarded.

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

SDK sends retain `source: "agent-bridge"` and a root envelope carrying the exact
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
runs both with the legacy/readiness regressions. The VM test uses Node's
`--experimental-vm-modules` flag only in tests.

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
