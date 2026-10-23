# Existing-resident receipt contract proposal

Parent: [Ready Resident Prompt Receipt](README.md).
**Specification proposed for review, not a shipped API or edit authorization.**

## Source evidence and scope

The initial trace uses source `c66e0bd7570ef4262d1f9c312a3593fbef52fd30`.
The receipt-owner source is
`fb6d1592e103de14826e700207c9606b18b07960`.
The roster owner reports its unmerged interface/claims at
`06f93aff71b88ca5180a2d2a23294e1ee21568e4`.
Refresh those claims before assigning code; none proves installed behavior.

Below, `A` means `plugins/agent-worktrees/src/agent_worktrees/`,
`S` means `plugins/agent-worktrees/scripts/`, and `H` means
`plugins/context-handoff/extensions/context-handoff/`.

- `A/status_monitor_cutover.py::ControlServer/_ControlHandler/ControlClient`
  supplies token-authenticated loopback TCP JSON lines. Its URL-shaped endpoint
  is **not HTTP**. The existing envelope is
  `{version: 1, token, action, payload}`; responses contain `{version: 1, result}`.
- `A/status_monitor_cli.py::_handle_control` handles health, roster and
  lifecycle actions, not receipts. Its drain currently counts roster reads and
  other named surfaces, not a new receipt mutation.
- `A/config.py::install_dir` and `A/registry_paths.py` select the existing
  state root. `S/registry_root.py::resolve_registry_context` validates the
  explicit installation cell; legacy state remains separately attributable.
- `libs/payload-invocation/command_binding.py::resolve_command_binding`
  establishes source/installation ownership, not resident readiness.
- `libs/zdd/src/zdd/routing.py::Endpoint` carries route generation, PID,
  process-start identity and version. `read_active_endpoint` can use
  `previous`; this request must not use that fallback.
- `A/worktrees_runtime.py::health` exposes current-roster/feed capabilities;
  its publication UUID is not the route or runtime generation.
- `A/handoff_prompt_receipt.py::prompt_receipt` performs receipt replay and
  append under the trace lock. Its current identity lacks a creation nonce,
  lock admission blocks, and its 64 KiB ceiling applies per history record.

No new service, generic command endpoint, broker, poller, authority ledger or
credential cache is part of the proposal.

## Authority and subject

Retain the control server's existing **authenticated per-user local operator
authority**. The service credential permits its existing local control
operations; this addition is limited to receipt query/record. It grants no
arbitrary executable, path, filesystem root or CLI argument capability.

The SDK hook supplies `sessionId` as **subject data**. It is not a claimed
cryptographic identity and does not require a new session credential. An equally
privileged local token holder is not a newly invented security boundary.
Unknown or wrong-target data still fails closed.

The server resolves an exact project and full worktree identity through its
existing owner snapshot, then uses `tracking.load_record` only on the
owner-resolved record path. It validates the existing `creation_nonce`.
Subject association is either:

1. the record's `WorktreeRecord.session_entry(sessionId)` satisfying the
   existing legitimate-association rules; or
2. one exact `pending_handoffs[]` entry whose `candidate == sessionId`.

Do not substitute `resolved_head_session`, an inferred `successor-elect`
presentation label or an unrelated pending predecessor. No all-project
`_find_tracking_file_by_session` scan or CLI lookup is allowed. A missing
association remains pending until the existing registration owner supplies it.

The adapter must validate against the record lifecycle/head revisions used for
its association decision and recheck after any admission wait. A revision change
requires revalidation; it does not by itself authorize replacing the subject
with the new head. Incarnation change always refuses the old operation.
Implementations must coordinate the short association/append admission with the
existing record owner; a stale cached association cannot authorize a write.

## Delivery: additive existing catalog projection

Use the existing producer:

```text
S/hook_client.py::_command_catalog_context
  -> existing binding_for_payload result (reuse, not repeat discovery)
  -> optional resident-control projection
_enrich_session_payload -> _agentWorktrees.commandCatalog
_guidance_content -> _write_session_guidance
  -> this session's instructions/agent-worktrees/session-guidance.instructions.md
```

The proposed optional catalog member is `residentControl`. It does not alter
catalog version 1, `commands[].availability` (shim/installer presence), or
`binding.status` (installation selection). Native/generated catalog producers
may omit it; consumers treat omission as unavailable. Do not hand-edit generated
`emit-command-catalog.*` or widen all plugin catalogs merely for parity.

Proposed version-1 projection:

```text
residentControl = {
  version: 1,
  state: "candidate",
  installationId, sourceFingerprint, marketplaceId, pluginId,
  runtimeRoot,
  installGeneration?, activationGeneration?,
  activeRoutePath, tokenPath
}
```

Identity values come from the **already-selected binding**, including explicit
legacy ownership where applicable. `activeRoutePath` and `tokenPath` are
producer-owned references to the existing route and credential; they contain
no token bytes and create no new file. The producer derives them only within
that selected runtime root using the existing control owner's canonical path
contract. A focused parity test checks them against
`routing_dir(runtime_home)` and `control_token_path(runtime_home)`. The proposed
payload-local projection helper must remain stdlib/in-process; it may not import
and start the resident or invoke its Python CLI to obtain those paths.

`candidate` intentionally does **not** mean ready. Publication needs no network
probe: only the authenticated handshake at observation time establishes a
usable active owner. Missing/blocked binding omits the member. No home guessing,
default marketplace, directory scan, alternate root or previous-owner recovery.
This reuses existing authoritative selection rather than making the projection
an independent attestation or registry.

The consumer's new bounded reader uses the already-resolved exact session
directory, reads only that guidance file, and parses the unique matching
agent-worktrees catalog JSON. It verifies `residentControl` against its enclosing
binding and expected session/source selection; malformed, duplicate, foreign,
oversized, linked/reparse or missing input is unavailable. It does not invoke
guidance hydration or any producer from the prompt callback.

Before opening a socket, read only the **active** row of the projected route
file, reject malformed or non-loopback addresses, and read the existing token
without creating/replacing it. Limit token reads to the existing 4,096-character
contract. Do not log the token, locator or secret-bearing request. No DNS,
redirects, arbitrary URL, token command or credential-service lookup.

The source/installation binding and route must remain the same through
handshake/admission. A producer/source switch invalidates this observation's
candidate; refresh at the existing lifecycle boundary, not by a callback
discovery loop. The server independently validates its currently selected
installation before effects. If that current identity cannot be obtained
boundedly from existing owner state, refuse; do not invent a persisted proof
chain or silently trust old projected values.

## Closed wire action and negotiation

Proposed capability: **`handoff-prompt-receipt-v1`**.
Proposed action: **`handoff-prompt-receipt`**.
The existing health response adds an independent `promptReceipt` object with
`contractVersion: 1`, `status`, and the serving owner identities below. It adds
the capability without modifying the roster owner's capability implementation.
Only an elected, published, non-draining owner with the bounded receipt backend
may advertise receipt status `ready`. Roster status alone is not receipt readiness.

Proposed payload (reject unknown fields):

```text
{
  contractVersion: 1,
  operation: "query" | "record",
  subject: { project, worktreeId, creationNonce, sessionId, locator },
  expectedOwner: {
    installationId, sourceFingerprint, marketplaceId, pluginId,
    installGeneration?, activationGeneration?,
    routeGeneration, pid, processStartTime, runtimeGeneration
  },
  remainingMs
}
```

The canonical locator is context-handoff's `task:`/`file:` locator, never the
numbered Worktrees handoff token. `remainingMs` is a capped admission budget,
not a promise that fsync can be interrupted. No token is duplicated inside the
payload; authentication stays in the existing envelope.

Keep these concepts distinct:

| Identity | Owner and meaning |
|---|---|
| Source fingerprint / installation ID | Existing command/installation binding |
| Install / activation generations | Existing installation receipts, if present |
| Route generation + PID/start identity | Current active routing publication |
| Runtime generation | Serving process/runtime incarnation from health |
| Roster publication UUID | Roster owner's storage generation; not receipt authority |
| Worktree creation nonce | Worktree incarnation |
| Lifecycle/head revisions | Existing association snapshot freshness; not session credentials |

Health negotiation and receipt admission both require an explicit supported
contract. The server checks active election and expected owner on each request,
not just once when health was read. A stale generation cannot admit a mutation.
Unsupported action/version or missing capability stays unavailable/pending,
without `promote`, `undrain`, ensure/recovery, generic exec or CLI fallback.

Proposed response:

```text
{
  contractVersion: 1,
  status: "received" | "absent" | "unavailable" | "unknown",
  replayed: boolean,
  reason: <bounded non-secret code>,
  subject: <same immutable identity>,
  owner: <serving identities>
}
```

`absent` is valid only for a completed exact query under a valid owner and
qualified incarnation. It is never synthesized from a timeout, unsupported
server, storage failure or missing association. Transport cancellation before
send is unavailable/not attempted; after possible admission it is unknown.
Bound request and response frames to **8 KiB**; validate existing field bounds
and use bounded error codes, never raw exception contents.

## Direct call and storage responsibility

The existing control handler dispatches only this action to a small proposed
`A/handoff_prompt_receipt_control.py` adapter. The adapter performs election,
installation, subject and deadline admission, then calls the receipt owner's
`prompt_receipt` **in process**. No parser, `__main__`, subprocess, tracking-write
generic command or per-request monitor recovery is involved.

The receipt owner must extend the backend to accept/persist `creation_nonce`
and honor bounded lock admission and total history scan limits. The immutable
receipt identity is:

```text
(installation scope, project, worktree_id, creation_nonce, session_id, locator)
```

Existing per-worktree trace/lock/fsync lifecycle remains canonical. No separate
idempotency database or receipt ledger is introduced. The backend derives paths
from its selected installation and project; the request cannot set `legacy_path`.
Compatibility with legacy trace rows must not guess an incarnation. If an old
matching row cannot be qualified by existing owner evidence, return unavailable
without appending a purported replacement or silently backfilling ownership.
The receipt owner must document and test that compatibility decision.

The backend's current blocking `_append_lock` and unlimited-total `_matching`
scan are **not resident-qualified**. Bounded lock acquisition, scan bytes/rows/
elapsed time and existing process/thread admission need an implementation
review. Do not hold the daemon's broad state lock during filesystem waits or
queue unlimited blocked handlers.

Once append/fsync may have happened, socket timeout/disconnect cannot prove
rollback. Report unknown to the observer and retain its single-flight/pending
identity. Reconciliation queries the **same exact** identity; it must not
replace session, nonce, locator or installation with whichever is current.
An absent query while an earlier record may still be in flight is not permission
for a blind new effect: the backend must serialize/fence that admission using
the existing trace ownership and bounded in-flight tracking.

A newly elected runtime in the **same verified installation/worktree incarnation**
may reconcile the same durable identity after explicit owner revalidation;
the failed mutation is never automatically redirected. A different source,
installation or worktree incarnation leaves the old result unknown/pending.
No background poller is added.

Receipt requests enter the existing server's admission/in-flight accounting
before effects and remain counted until backend settlement, even if the client
disconnects. Drain closes new receipt admission and sees outstanding requests.
An in-flight commit may outlast the client deadline: its ownership and
reconciliation remain explicit. The plan does not promise cancellable fsync,
instant drain, or cleanup from a socket timeout.

## Consumer boundary and tests

The receipt owner replaces only `H/handoff-core.mjs::observeHandoffPromptReceived`
and its receipt read integration with an async bounded Node socket client;
`H/prompt-receipt.mjs` keeps registration/reconciliation/disposal scheduling.
The first-prompt callback schedules optional work and returns without blocking.
`runReceiptChild` is not the new transport and must not remain a fallback.

One monotonic **2,000 ms whole-observation budget** covers catalog/token/route
reads, health negotiation, admission, backend response and any reconciliation
performed in that attempt. Abort destroys the owned socket and settles the
local observer as unavailable/unknown; it does not claim server cancellation.
No retry admits another mutation while commit is uncertain. Retain the exact
pending identity through dispose/source change; a future already-authorized
reconcile event may query it, without starting a timer/poller.

Build an isolated fixture around the actual existing `ControlServer`, real
receipt backend and real Node client. The test harness alone starts/owns the
synthetic server and client; **the path under test starts zero helper processes**.
Use synthetic selected-installation/route/token/tracking files, not live state.

Required assertions, in addition to the parent Validation Plan:

- Intercept process creation and lifecycle entry points across producer,
  Node client and backend; capture the actual call graph/call counts and elapsed
  stages. No hidden binding/token-command/discovery/health retry loop.
- Missing service/capability/token/descriptor, corrupt route, previous-only
  route, non-loopback target and unsupported versions have zero effects/wakes.
- Wrong token/source/installation/subject/nonce; registered non-head subject;
  exact pending successor; removed association; revision change while waiting.
- Concurrent identical records; independent locators; lost reply after durable
  commit; timeout before lock, during scan and after append; exact replay after
  same-installation cutover; refusal after source/incarnation switch.
- Drain versus record; client cancellation before send and after admission;
  backend settlement retained after disconnected client; no false absent.
- Long total history, oversized individual record, contention, storage error,
  delayed fsync and bounded buffers. A <2s pending result is not a completed
  write, and server settlement is reported separately rather than hidden.
- Two windowless cycles and cleanup of the owned synthetic server's sockets,
  threads and processes. No production/native benchmark is implied.

## Files and owner reservations

These are proposed future changes, not files edited by this planning PR.

| Surface | Proposed change / ownership |
|---|---|
| `S/hook_client.py::_command_catalog_context` | Assigned delivery owner reuses selected binding and appends optional projection; existing enrichment/writer remains the delivery path |
| `S/resident_control_projection.py` (proposed new) | Small stdlib owner-local projection of selected binding and canonical existing artifact references; no network or lifecycle |
| `A/status_monitor_cli.py::_handle_control`, health and drain accounting | Assigned control owner adds only closed receipt route/readiness/counting after refreshed claims |
| `A/status_monitor_cutover.py` handler/client contract tests | Assigned control owner integrates bounded action frames/admission; do not touch roster owner's `routing_dir` conditional-import hunk |
| `A/handoff_prompt_receipt_control.py` (proposed new) | Fixed-purpose validation/dispatch, no generic command execution |
| `A/handoff_prompt_receipt.py`, relevant `activity.py` branches, receipt tests | Existing receipt owner alone; nonce/bounded admission/reconciliation gate |
| `H/handoff-core.mjs`, `prompt-receipt.mjs`, registration and receipt tests | Existing receipt owner alone; optional small `resident-receipt-client.mjs` belongs to that owner |
| Existing hook catalog tests; proposed `test_resident_prompt_receipt_control.py`; real Node/server fixture | Delivery/control owners plus receipt-owner integration; exact focused test partition before edits |

Fresh roster-owner reservation: `worktrees_{projection,observation_contract,
runtime,feed,feed_client,client}.py`, `current_roster_entry.py`,
`invocation_project.py`, `list_arguments.py`; `__main__.py` early direct-entry
selector, front-door context extraction and list parser/reexports; the
`pyproject.toml` script target; focused tests and Manager roster parsers.
Its only `status_monitor_cutover.py` change is the conditional import in
`routing_dir`; it does not claim new receipt actions or server election/drain.
That distinction does not certify other hunks globally free.

The receipt owner retains its entire new receipt backend/scheduler, specified
activity/consumer hunks, tests, documentation and effort root/index row.
Preserve #7833 process custody, #7788 launch-maintenance, #8031 native mux and
#7449 Manager launch ownership. This proposal needs no edit to those behaviors.
No shared procutil, new broker or SDK-shell fallback is included.

## Remaining admission decisions

1. Integration owner approves these exact proposed names, field/byte contracts
   and future file assignments, or requests a bounded revision.
2. Existing owners accept the minimal subject-snapshot/append synchronization
   and legacy-row compatibility design during implementation review; no
   per-session authorization system or head-only shortcut is an option.
3. Complete producer execution is tested for hidden work before calling it
   zero-helper. Its current enclosing enrichment performs other established
   work; this plan neither audits nor rewrites unrelated lifecycle behavior.
4. Plan source review/CI and refreshed claims clear before functional edits.
   None of these decisions authorizes installed-state changes or rollout.

## Trusted subject producer amendment

The integration owner has admitted this narrow follow-on source slice.
Automated source-plan review precedes its implementation. It closes the existing
subject-delivery prerequisite, not the backend/Node or installed acceptance gates.

- Extend only `handoff_diagnostics.stamp_session_state_worktree_binding` with
  optional keyword-only `worktree_record=None`, and the existing
  `session_binding_cli.cmd_register_session` post-registration stamp block.
  Preserve all legacy fields/callers, best-effort `OSError` behavior, atomic
  replacement and the rule that the session directory is never created.
- Add `project` from the actual record's `repo` and `creationNonce` from its
  existing `creation_nonce` only when exact worktree/incarnation, owning
  registration project and legitimate session association are qualified.
  Registered non-head sessions and exact pending candidates remain valid;
  current head, controller, path and PR repository are not identity sources.
- Leave the existing registration critical transaction unchanged. For the
  optional stamp use its exact existing record path and
  `_RecordLock(blocking=False, require_sidecar=True)`, check `acquired`, and
  skip stamping on contention. The default blocking lock can wait and degrade
  without a sidecar; neither behavior is valid admission here.
- Under the acquired guard, reread using existing `_load_record_uncached` /
  record-I/O machinery, not cached `load_record`. Compare the supplied snapshot's
  project/worktree/nonce and lifecycle/head revisions against that fresh record,
  and validate the actual registered session or exact pending candidate.
  Keep the guard through validation and atomic stamp. No retry, new wait,
  provider lookup, nonce creation or lock/cache implementation change.
- Known invalid/stale snapshots and legacy rewrites must omit trusted fields
  rather than retain old `project`/`creationNonce`. Unknown contention must
  neither invent freshness nor overwrite a newer qualified binding.

Focused caller-and-writer tests must cover acquired-false/no-stamp admission,
stale cache versus uncached record, valid non-head/pending subjects, wrong
project/worktree/nonce/session, stale lifecycle/head revisions, removed
association, missing nonce, legacy callers, absent directory, write failure and
stale trusted-field removal. Tests exercise the actual registration caller and
writer, not just a fabricated projection. Source review/required CI/merge and
delivery of the exact contract to the receipt owner complete this slice.
