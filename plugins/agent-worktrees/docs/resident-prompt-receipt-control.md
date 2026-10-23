# Optional resident prompt-receipt control

This is a closed extension of the existing per-user-authenticated Worktrees
control server. It does not create a server, credential, process broker or
alternative receipt store.

## Availability and delivery

The session-start catalog producer may add `residentControl` version 1 to the
existing exact-session command catalog. It derives only references and identity
from the already-selected command binding:

- installation/source/marketplace/plugin identity and selected runtime root;
- installation and activation generations when supplied by the binding;
- the canonical existing active-route and control-token paths.

Its state is **`candidate`**, not ready. There is no network probe, token read or
creation in this projection. Existing command `availability` still describes
shim/installer presence. An old producer, unresolved binding or invalid
projection simply has no optional member.
When a registration snapshot already includes the older generated catalog, final
guidance replaces that catalog's JSON block with the current enriched block,
preserving surrounding registration text and emitting only one catalog.

An observer must use only the active route and existing token and negotiate the
`handoff-prompt-receipt-v1` capability in authenticated health. No previous route,
default root, ensure/recovery, CLI, or SDK-shell fallback is part of this path.
`promptReceipt` in health supplies version, readiness and current owner identity.
Source/installation, route generation, process start identity and runtime
generation are distinct; the roster publication UUID is not receipt authority.
The resident pins its initial selected binding in memory and refuses a later
source/install/activation change rather than relabelling the old process as a
new owner. This reuses existing identity; it publishes no additional receipt.

The adapter loads the receipt backend only if it exports integer
`RESIDENT_RECEIPT_CONTRACT = 1` and `resident_prompt_receipt`. Until the receipt
owner qualifies that backend, health advertises no receipt capability and the
action returns unavailable. The marker is an API contract, not a new runtime
attestation or a claim that any live deployment was tested.
The resident obtains payload ownership from its validated installation context,
or its existing legacy command-binding receipt, and revalidates that selection
using the runtime's packaged validator. It does not infer a payload from the
deployed `bin/hook_client.py` path or require sibling helpers beside that copy.

## Request and result

The existing transport is loopback **TCP JSON lines**, not HTTP. Its envelope is
`{version: 1, token, action, payload}`. The fixed action is
`handoff-prompt-receipt`; both its request and response frames are limited to
8 KiB. Its payload contains exactly:

```text
contractVersion: 1
operation: "query" | "record"
subject: {project, worktreeId, creationNonce, sessionId, locator}
expectedOwner: {installationId, sourceFingerprint, marketplaceId, pluginId,
               installGeneration?, activationGeneration?,
               routeGeneration, pid, processStartTime, runtimeGeneration}
remainingMs: <finite positive remaining client budget>
```

There are no executable, CLI argument or caller filesystem-path fields.
The locator is context-handoff's canonical `task:`/`file:` identity.
The result contains `contractVersion`, `status`, `reason`, `replayed`, `subject`
and `owner`. Status is `received`, `absent`, `unavailable` or `unknown`; malformed
requests omit subject/owner rather than reflecting unvalidated content.
Diagnostics use bounded reason codes, never raw errors, tokens or handoff text.

Authentication remains the existing per-user operator authority. `sessionId`
is subject data from the trusted hook, not a new session credential.
The adapter resolves exact project/worktree state through existing owners,
checks the creation nonce and accepts a registered session or an explicitly
recorded pending handoff candidate. It does not require current head status or
infer association from a presentation label.

## Backend and ownership

The closed adapter calls, in process:

```text
resident_prompt_receipt(project, worktree_id, locator, session_id,
                       *, creation_nonce, record, deadline, admission)
```

`deadline` is absolute monotonic time. `admission()` is a context manager yielding
`revalidate()`. It takes the existing tracking record lock nonblocking with
cross-process sidecar ownership required. The backend takes the canonical trace
lock **after** that record guard, then revalidates after waiting and before
append. Revalidation rereads the owner and subject; the record lock remains held
through backend settlement. No broad daemon state lock spans storage I/O.

There are at most four active receipt operations and no queued admission.
An active record fences identical query/record requests as `unknown`, preventing
a query from reporting absent while that write can still commit. Drain closes
new receipt admission and counts existing operations. Shutdown waits for actual
receipt settlement before releasing the resident's ownership.

The separately qualified backend must add incarnation-qualified idempotency,
bounded canonical trace-lock admission and total scan budgets. The agreed
backend ceilings are 100 ms trace admission, 1 MiB/4,096 total history records,
64 KiB per record, 200 ms scan and 1,000 ms pre-effect work, always constrained
by the request's deadline. These are backend obligations, not guarantees supplied
by the transport or a marker check alone.

A client disconnect/timeout does not cancel fsync. The handler retains admission
and accounting until the backend returns. Completion after deadline or owner
change is `unknown`. The observer must retain the exact immutable identity and
reconcile it rather than redirect a mutation to a new head/source/incarnation.
Unknown is not absent and does not grant duplicate-effect admission.

Compatible service replacement uses the existing status-monitor
`status_monitor_cutover` activation/drain owner. Receipt admission closes during
drain; cooperative shutdown retains in-flight accounting through fsync. This is
not a veto on the lifecycle owner's bounded, identity-checked forced retirement
when cooperation cannot settle. Forced interruption is a lost/unknown outcome,
not a successful flush; the qualified backend and consumer must reconcile the
same durable receipt identity after a verified same-installation takeover.
No child process or new termination authority is introduced here. Existing
single-instance election, routed process-start identity and cutover recovery
remain the exclusion/retirement authority; this source change does not qualify
their live installation or promise interruption-free upgrades.

## Qualification boundaries

Focused tests cover real control sockets, actual source-binding resolution over
synthetic installation state, bounded frames, token rejection, stale owners,
non-head/pending-successor association, existing record-lock exclusion, replay
fencing, four-slot admission, disconnect/drain accounting and late completion.
Missing qualified backend is tested explicitly.

macOS additionally requires qualification of the existing route publisher's
process-start identity contract (#7659). The current zdd publisher reads
`/proc` on non-Windows platforms, whereas receipt validation uses Worktrees'
native Darwin identity reader. A synthetic route written with the latter does
not prove actual publisher compatibility. Until the lifecycle owner aligns and
tests that shared identity contract, macOS receipt readiness remains unavailable;
the identity check must stay fail-closed. This slice does not change the shared
publisher or claim macOS readiness.

The receipt owner separately owns the real backend/Node integration, canonical
lock qualification, total history bounds and the whole 2,000 ms observation
budget. A timely pending result is not completed-write or cleanup proof.
The existing launch/session binding projection does not yet supply the required
project plus creation nonce: that producer addition requires its own narrow
admission. Missing subject identity remains unavailable. No live host, installed
runtime or deployment qualification is implied by these source tests.
