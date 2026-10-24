# Negotiated cold drain

Bridge's ordinary drain preserves live Session Hosts for graceful reattachment.
HTTP generation 47 additionally supports an observable destructive alternative
when the caller and receiver cannot preserve sessions under the same contract.
This is not a zero-downtime or required-graceful cutover.

## Negotiation and authority

Authenticated `POST /api/v1/drain` accepts:

```json
{
  "timeout": 300,
  "poll": 1,
  "drain_contract": {"minimum": 1, "maximum": 1}
}
```

The preservation-contract range is independent of the package version, HTTP
generation and Session Host framing version. `/health` advertises the supported
preservation range; the current preservation generation is **1**.

An overlapping range takes the existing graceful path. A strictly confirmed
disjoint range automatically selects cold drain; no additional opt-in is
required. An explicit `on_incompatible: "refuse"` selects graceful-only control
and refuses a mismatch with 409 before changing the drain gate.
Missing contract information is `legacy-unnegotiated`, not a confirmed mismatch,
and retains the ordinary path. Invalid JSON, non-object control, coercible
booleans/strings, non-finite budgets, unordered or out-of-range generations and
unknown control fields refuse before changing the drain gate.

`BridgeClient.drain()` emits the cold-on-confirmed-mismatch policy by default,
checks the receiver's actual advertised preservation range and validates its
negotiated response. It does not assume the receiver's generation equals the
caller's own. `drain_gracefully()` (or explicit refusal policy) rejects a
confirmed mismatch during client preflight, before posting drain control.
The server enforces refusal policy again at the effect boundary.
Client responses must be objects; negotiated responses additionally require
actual boolean completion flags. Missing or coercible `drained`, `clean` or
`forced` values raise `BridgeClientError(502)` rather than supplying truthy
permission to retire a predecessor.

Unknown/legacy negotiation never becomes a destructive mismatch: the ordinary
legacy path omits the speculative contract/policy fields, while a caller that
requires an unsupported range refuses before POST. Malformed advertised
negotiation or a failed health request cannot authorize cold effects.
Older daemons cannot acquire this behavior through a new client.
Their ordinary drain responses are labelled
`legacy-unnegotiated`; legacy `force=True` still only bypasses the busy timeout
and is **not** proof of execution termination.

## Execution and recovery

The cold path closes admission, waits for already-entered acquisitions, and
operates only on local Session Hosts claimed by this daemon generation.
The existing launch authority must match the session, nonce and host/child
identities. Native process references are pinned before control. Before any
signal, `cold_outages` retains the original native identity, exact target,
previous status, originally incomplete turn indices and process-birth evidence.
The original host connection requests child termination; actual child exit
must be confirmed before identity-bound host retirement and its confirmed exit.
Only then can the response report that execution drained.

The response distinguishes `mode: "incompatible-contract-cold"`,
`interrupted`, `nonresumable`, `preserved` and explicit `failures`.
It always has `clean: false` and `forced: true`, including when no execution
needed termination. A protected/excluded session, nonlocal execution, missing
launch evidence, unresolved acquisition or unconfirmed termination prevents
successful drain. Failed or partially completed outages retain their journal
and host authority. A successor reconciles `prepared` or `failed` only under
kernel operation custody and only when the retained exact host and child
identities both prove exited. Live, denied or otherwise uncertain evidence stays
fenced; elapsed time never substitutes for exit evidence. The cold drain gate does not
automatically reopen on a watchdog timer.

A successor's existing host-recovery pass takes the existing crash-releasing
`OperationLock` for this session and journals its generation, PID and native
birth identity **before** any replacement launch. A different live recorded
owner refuses even after the kernel descriptor was released. The operation
holds custody through replacement registration and reconnect admission.
Recovery also enters the existing drain admission accounting before its first
await. This includes ordinary resume/resync with no cold-outage row: a dormant
session omitted from the execution census cannot launch after successful drain.
A closed gate refuses new recovery, including heartbeat recovery; HTTP
resume/resync report 503 without launching or tearing down a client.
If drain begins after recovery entered, it waits through replacement and
reconnect admission. Only that same task's exact reconnect prompt, native
identity and delivery key may finish through the closed gate while kernel
operation custody remains held; ordinary prompts and inherited child tasks
receive no exception. Cancellation releases the in-process admission accounting
without replaying an uncertain launch or send. Cancelling a launcher await does
not stop its native launcher thread. Every durable `launching` row remains in
the cold-drain census, including historical rows or rows without an in-memory
session. Drain refuses while any such launch outcome is unresolved, even after
the original coroutine returned or a new frontend rehydrated the database.
An already-registered replacement is identity-checked and adopted, including
when the former owner crashed between registration and its journal update.
A launch marked in progress without provable replacement identity remains
fenced rather than spawning another native process.

An explicit dormant stop persists a fresh `cold_recovery_stop_token` before
waiting for lifecycle ownership. Startup and heartbeat recovery refuse any
retained token. Manual resume may override only the token it captured at
admission; a newer stop wins at the launch and reconnect boundaries. Admission
is checked again after workspace and instruction preparation, immediately before
invoking the native spawner. This read-only check preserves the earlier durable
launch fence; it does not clear uncertain launch evidence on a stop or crash.
Successful manual admission clears only its captured token, and background recovery can
be re-enabled only by a conditional database write that respects newer stops.
Busy-refused stops create no token; ordinary redeploy detach is not a dormant
stop. Schema 29 adds this nullable gate. Legacy stopped, recovery-disabled rows
receive conservative ambiguity tokens because the older schema cannot prove
whether suppression came from cold drain or an explicit stop. They require
manual resume rather than invented operator provenance.

An interrupted session without a surviving replacement is loaded into a new
local Session Host using the **same persisted native conversation**.
It does not adopt the terminated host, create a conversation, allocate a
workspace or reconstruct interrupted tool calls. After loading succeeds, it
admits exactly this new turn under an outage-specific durable delivery key:

```text
Resume. Host agent-bridge updated.
```

That unique admission prevents another automatic submission if recovery is
observed again. `reconnect_admitted` is not `reconnected`: the latter requires
the actual prompt RPC response for this native conversation and delivery key.
A matching turn row, including a completed-looking row without the retained
provider evidence, never supplies that confirmation. Send failure, cancellation
or a crash between journal admission and SDK submission remains
`reconnect_unknown` (or an unconfirmed admission until reconciliation), with no
automatic resubmission. An actual later provider response may resolve the
uncertainty without another send.
The unresolved disposition also fences prompt-triggered, automatic and
externally seeded context handoffs before brief authoring or successor
creation. A handoff cannot escape uncertainty by creating a new conversation.
The resumed agent decides what work to retry under the existing
operation/key safeguards. No interrupted prompt or tool payload is replayed.
Existing conversation history and original admission keys remain intact.

Cold-series `resync` is routed through the same disposition and kernel-owned
native recovery path before any teardown or launch. It cannot use the ordinary
transcript-rebuild path, bypass a live/uncertain original execution, or submit
another reconnect for an unknown admission.

Sessions with no native conversation are explicitly nonresumable, never fresh
recreation candidates. A pre-launch `ALLOCATED` workspace embodiment has no
execution to cold-resume; its row, creation key and allocation facts remain
untouched. An incomplete or failed termination journal likewise forbids
ordinary send/resume from silently creating a replacement conversation.

## Compatibility and rollout limits

New full local runtimes publish native birth evidence on Linux and Windows.
Minimal remote Session Host bundles remain dependency-free and do not confer
local destructive authority. Nonlocal/protected owners are never contacted by
this operation. Hosts launched by older runtimes without the required birth
evidence refuse the cold path; no identity is invented from a current PID.
An adopted legacy database lacking the cold-outage ledger remains readable and
usable for ordinary recovery, but cannot perform destructive cold drain.
Normal writable startup creates the new ledger without rewriting native
history or restoring SQLite/WAL bytes.

No installer flag was weakened or repurposed. Required-graceful cutover rejects
a cold result even if an erroneous producer labels it clean. Owner-pinned and
drain-before-publication cutovers use the strict client hook before control,
including the existing required-live-predecessor installer path. Ordinary,
unrestricted drains retain automatic cold-on-confirmed-mismatch behavior.
Conditional cutovers reject an explicitly cold response before normalizing its
result, even if its completion flags incorrectly claim clean, unforced success.
A generation that selected cold
drain cannot subsequently attest a graceful drain, even after its admission gate
is released; releasing a gate cannot undo an interruption.
A new cold drain capability is
therefore not permission to update, drain or terminate any live installation.

The contained `test_cold_drain` family exercises real host/child processes and
a stdio stand-in, including matching native IDs, no `session/new`, and one
reconnect prompt. It does not qualify real Copilot recovery or a native Windows
rollout.

See [architecture](architecture.md#admin--deployment) for the control surface
and [contract evidence](../contract/README.md) for publication capture policy.
