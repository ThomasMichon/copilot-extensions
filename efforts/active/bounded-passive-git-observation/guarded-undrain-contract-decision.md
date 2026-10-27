# Guarded undrain: bounded wire decision

Back to [the effort](README.md). **Admitted implementation candidate; source
acceptance and installed qualification remain open.** The current integration
admission covers this new operation and the bounded existing ZDD rollback
checks. It does not authorize unrelated control-plane or producer work.

The original design rationale below describes why legacy undrain cannot supply
owner-bound recovery. The implementation is
`status_monitor_recovery_contract.py`, the existing control transport and the
monitor's existing state/undrain handler. Native fixture receipts and remaining
gates are recorded in [validation.md](validation.md). The two original failing
probes remain causal regression tests and now pass.

## Legacy binding: none

Actual `ControlClient` retains `host`, `port`, `base_url`, a runtime-root token,
`config_dir`, timeout and optional transition custody. `_request` sends
`{version: 1, token, action, payload}`. `undrain()` calls `_request("undrain")`,
so its payload is `{}`: no expected PID, native start identity, runtime
generation or routing generation is transmitted.

`_ControlHandler.handle` authenticates the shared control token, extracts the
action and passes the dict payload to the monitor handler. Strict frame/version
checking currently belongs specifically to the receipt action, not undrain.
`_handle_control("undrain")` checks shutdown, then changes admission/publisher
state and activates request surfaces. No expected old receiver is compared.
The token authenticates the control domain, not one process lifetime. A fresh
TCP connection to the same host/port can reach a different, currently elected
receiver with the same token. Checking merely that *that* receiver is current
does not prove it was the old target.

Existing authenticated health already returns `pid`, `runtime_generation`
(the process's `PROCESS_GENERATION`), runtime slot and capabilities. Native
start identity can be read locally before/after that one health response.
Those are usable **observations**, but ordinary undrain carries none of them.
Routing endpoint generation is an election counter, not process generation:
legitimate rollback may republish the same old process under a new route
generation. Do not rewrite an old record to pretend these are interchangeable.

## Smallest proposed operation: `guarded-undrain/v1`

Keep the existing transport envelope version 1. Add a distinct action
`guarded-undrain` with a strict operation payload:

```json
{
  "contractVersion": 1,
  "expectedReceiver": {
    "pid": 101,
    "processStartTime": "opaque-native-start-identity",
    "runtimeGeneration": "retained-process-generation"
  },
  "expectedRoute": {
    "bind": "127.0.0.1",
    "port": 61001,
    "pid": 101,
    "generation": 9,
    "process_start_time": "opaque-native-start-identity"
  }
}
```

The route object must use the existing exact Endpoint representation, including
its version field when present; this abbreviated example is not permission to
ignore Endpoint fields. It describes the **actual restored route**, not the
pre-cutover election counter. The expected receiver remains the originally
authenticated old process; never refresh it to the current occupant of a reused
port. No monotonic observation deadline is persisted or transmitted.

### Admission and reply contract

1. Health advertises the exact `guarded-undrain/v1` capability only when the
   receiver implements strict framing/schema validation, native self-identity
   and the serialized mutation contract below.
2. Before first drain, passive spawn or route publication, the new caller
   authenticates the present predecessor with an **existing token only**,
   passed explicitly. Bind native PID/start before and after **one**
   authenticated health response, its returned PID and runtime generation.
   Missing/inconsistent proof or capability refuses before side effects.
   Genuinely no-predecessor fresh-install/restart branches remain distinct.
3. The receiver validates exact operation version/schema, compares every
   `expectedReceiver` field with its actual self identity, and compares the
   expected restored Endpoint with current authority **before reopening any
   admission, publisher, receipt or carrier state**. A different current owner
   must reject even if the port and shared token match.
4. Role validation and reopening must be serialized with route-authority
   changes using an existing authority/write boundary with reviewed lock
   ordering. A before/after snapshot alone is not an atomic fence. This must
   be proven before implementation; the current PID-only projection election
   is not silently promoted into that proof.
5. A positive result has `contractVersion: 1`, `status: "undrained"` and echoes
   the exact matched receiver and route identities. Only that positive typed
   result qualifies recovery. Refused/unavailable results do not mutate state.
   Missing/wrong identity, malformed reply, timeout or lost reply is not
   success, and `rolled_back=True` alone cannot clear reconciliation.

No automatic legacy `undrain` fallback is allowed. An idempotent guarded retry
can only target the same retained receiver/authority expectation; owner change
requires a new explicitly qualified decision, not replacement of the expected
identity inside a retry.

## Mixed-version and recovery implications

- **New caller / old owner:** missing capability refuses before drain and other
  side effects. Do not attempt the new action and then fall back. This creates
  an honest bootstrap/rollout gate: updating an old running owner may require
  an explicitly authorized maintenance/restart flow, not a forced cutover.
- **Old caller / new owner:** legacy undrain behavior remains compatibility
  behavior, but cannot earn R1a's owner-qualified recovery acceptance.
- **New caller / new owner:** every guard and reply identity must match; a
  restarted receiver at the same port refuses the old-target request.
- **Legacy route missing identity:** refuse this guarded recovery qualification
  without synthesizing metadata or rewriting routing records. Preserve the
  separate genuinely absent-predecessor branches.
- **Publication uncertainty, failed positive undrain or unconfirmed successor
  exit:** retain existing reconciliation custody/breadcrumbs and fail. No
  service repair, deployment or new controller is implied.

## Admitted decision scope and tests

The admitted amendment covers only the capability/strict new-action parser,
the guarded receiver handler, an expected-owner client operation, and the
existing cutover adapter's negotiation/result interpretation. The separately admitted existing ZDD integration adds predecessor refusal
checks at restoration publication and before undrain, without a new API. No F1 schema, new
controller or generic control-plane redesign is included.

Required injected tests pair the actual helper/orchestrator/client/receiver:
unsupported owner means zero drain/spawn/publication/reopen; PID/start/runtime
generation or port reuse rejects before effects; third-owner changes at
health/publication/undrain boundaries cause zero unauthorized reopening;
legitimate restored election generation succeeds for the retained process;
lost/malformed/failed reply and unconfirmed exit retain failure/breadcrumbs.
Keep the two existing unbound-client probes as component evidence, not an
end-to-end fence test. Current native fixture results are recorded in validation.md; those results
do not establish installed or broader performance acceptance.
