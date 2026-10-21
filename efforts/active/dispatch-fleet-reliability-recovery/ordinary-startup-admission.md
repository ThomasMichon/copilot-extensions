# Ordinary native-startup keyed admission

**Status: PROPOSED.** This is the bounded Phase 7 amendment to the
[canonical effort](README.md#phase-7---ordinary-native-startup-keyed-admission-proposed),
coordinated through #7486 under umbrella #6466. Runtime implementation requires
normal public plan review and merge first. Rollout requires separate authority.

## Intent and scope

Close existing
[single-stream admission](../../../visions/plugins/agent-bridge/README.md#single-stream-message-admission)
and [one-owner](../../../visions/plugins/agent-bridge/README.md#one-owner-many-callers)
intent. No new vision, backend, agent type, scheduler, reservation store,
delivery table or competing state machine is proposed.

Ordinary startup creates an agent without a prompt, stores its ready identity,
then sends the startup prompt. If admission succeeded but its response was
lost after settlement, another ordinary supervisor pass can submit another turn
to the same idle native conversation. Retaining the allocation/session alone
does not establish at-most-one startup turn.

The scoped deterministic evidence coordinated in #7486 has a passing preamble
control and a failing exactly-one-turn counterexample. The latter uses two real
ordinary supervisor polls: canonical first-pass deferral, positive retry
eligibility, and one retained allocation/peer/conversation but two turns. Keep
that assertion unchanged. A proposed repair must make it pass, not xfail it,
keep the peer artificially busy, relax assertions or widen deadlines.

The witness uses real SessionHost/owner handlers, an isolated ASGI HTTP carrier,
a queue-backed coordinator facade and compatibility-only test ACP identity.
It is not network-daemon, CAR-native, model, task-result or verification proof.
The known-failing test candidate is excluded from this plan-only change.

## Ownership and non-goals

- The existing effort Coordinator owns integration; independent slices follow
  its [Coordination](README.md#coordination) contract.
- #6467 retains master-supervisor budgeting/recovery; #6793 retains source
  safety. Preserve acquisition #6076, head-resume #6934 and remote relationship
  #7237 without consolidating their authorities into this fix.
- #1453 represented-interactive admission and #5843 remote-carrier response loss
  are adjacent scopes, not permission to replace their implementations.
- Do not change native-source, workspace, retirement, owner or cleanup guards.
  Do not manufacture Copilot history, native hold files or attestations.
- Do not change task completion/verification or infer it from response text.
  Do not promise exactly-once native execution or distributed atomic revocation.

## Stable invocation and durable intent

Persist one versioned startup invocation with the existing ready-agent facts,
before first send, under the existing coordinator transaction/fences:

- task ID and immutable **admission generation**;
- ORIGINAL allocation-reservation lineage, allocation revision and creation key;
- exact Bridge/native session and workspace/project/path/incarnation binding;
- exact fully rendered startup prompt and its immutable admission receipt identity.

The identity must survive a deferred attempt's new reservation key. Do not use
the current attempt key as the logical invocation key. Do not use a prompt
digest as the invocation identity: changed payload must conflict under the same
identity, not become a new invocation.

Freeze the entire prompt before first send: assignment body, worker label,
charter/routing-derived text and native-owner suffix. Current seed composition
includes a reservation-derived worker label even with implicit worker identity.
Bind that label to stable original lineage, not the retry attempt. An exact
same-invocation retry sends the frozen bytes; changed facts refuse rather than
silently overwrite them.

_(Proposed implementation detail, subject to review)_ Extend the existing
versioned `agent_info` facts rather than adding another Dispatch ledger.
Ready-agent identity and invocation intent must commit together. Read the stored
facts back and compare them before first send. A response echo alone is not proof
that the receiver implements the versioned storage/fence contract.

Older storage that ignores the invocation, unknown facts versions, malformed
facts, or missing read-back must refuse before model send. A legacy retained
agent with an uncertain previous send must not receive a newly invented key.
There is no permissive fallback for missing historical evidence.

## Generation: historical receipt versus fresh permission

A real claim increments generation: queued g0 can become worker-claimed g1.
The g0 admission receipt remains historical truth; g1 is not automatically a
different startup invocation. Never infer a legitimate claimant from arithmetic
such as `current == admission + 1`.

| Operation | Contract |
|---|---|
| Fresh startup insertion | Preserve existing AD server generation, QUEUED/unowned, hold, reservation, allocation and retirement fences. |
| Read existing receipt | Existing Bridge authentication and exact stored invocation/session/native/prompt binding; return historical facts only. No task write, new grant, resume or turn. |
| Receipt absent after uncertain send | Do not create on a read. Fresh insertion is possible only if the existing fresh-admission fences still pass and the original history remains provable. Claimed/started g1 is not fresh g0 permission. |
| Explicit intentional follow-up | Preserve normal unkeyed new-turn semantics under their existing permissions. |

Keep `invocation.admission_generation` immutable and distinct from mutable task
generation metadata carried during reconciliation. Do not broaden ordinary
created-agent storage's QUEUED/unowned write gate to accept claimed/started tasks
merely to recover a receipt. Stale/foreign ownership still refuses fresh work.

Bridge turn deduplication is not a new task-authority service: it must not blindly
trust a client generation as current permission or import the #6076 acquisition
workflow. Existing AD server fences retain fresh-task authority; Bridge owns
authenticated native-bound turn admission. No new cross-database atomicity
guarantee is introduced.

## Atomic turn admission and receipt replay

Reuse `turns.acquisition_delivery_id` and its existing non-null unique index,
with a distinct ordinary-startup namespace. Reuse the existing turn transaction
pattern, not a parallel delivery table.

_(Proposed implementation detail, subject to review)_ Add nullable versioned
immutable admission facts/receipt data to the existing turn row. Persist key,
exact prompt, session binding, facts version and original admission receipt in
the same transaction as the turn. Existing unkeyed/acquisition rows remain
compatible and are not fabricated into ordinary keyed receipts.

Under the existing native turn lock:

1. Preserve authentication and native/session identity checks; revalidate mutable
   local binding after any awaited checks.
2. Look up the invocation before **fresh-turn** busy checks.
3. Atomically return a matching persisted receipt or insert the turn and its
   immutable receipt/facts. The unique index also fences concurrent identical
   requests. Same key with different prompt/native/binding/facts refuses.
4. Only a new insertion increments the turn count, emits admission events or
   schedules the SDK call. Replay performs none of those effects.

| Case | Required outcome |
|---|---|
| First accepted insertion | HTTP 200 admission receipt with invocation, Bridge/native IDs, original turn index and `queued:false`. |
| Matching replay while RUNNING or already settled | HTTP 200 with the same original admission identity/index; any replay annotation is not a new turn or completion claim. |
| Same key, different payload/native/workspace binding | Typed conflict refusal; zero new turn. |
| Missing, malformed, incompatible, retired or unprovable original history | Explicit unavailable/conflict/retired outcome; no new key, replacement allocation or invented receipt. |
| New unkeyed intentional send | Existing new-turn behavior remains unchanged. |

The receipt reports **admission**, not current liveness or successful execution.
If persistence precedes a crash before SDK scheduling, replay must not resubmit;
execution remains unproven for the existing recovery owner. Session/history
retention must not erase deduplication proof while treating the same invocation
as fresh. Missing history is not evidence that no prior effect occurred.

## Capability and cutover boundary

Require the serving Bridge's keyed-send capability before ordinary creation,
using the existing protocol-floor preflight and per-attempt floor machinery.
Require compatible invocation storage/read-back before model send.

Add a distinct keyed native SEND endpoint so an older handler cannot ignore a
new field. Older CLI/receiver refusal, including 404/426, must not fall back to
unkeyed send or another backend.

Retain the existing keyed CREATE route and create/store/send ordering. Do not add
a capability-specific CREATE route. A create-side minimum-protocol option may
compose the existing floor mechanism for callers requiring keyed send, without
changing other callers' defaults.

A replacement between preflight and effect can leave one known, unprompted keyed
allocation/conversation. Preserve its identity and refuse unsupported send;
do not repeatedly allocate or claim hard zero allocation under arbitrary cutover.

## Cohesive implementation boundary after review

Existing code anchors (contracts to extend, not parallel replacements):

- [Unique delivery index](../../../plugins/agent-bridge/src/agent_bridge/db_schema.py)
  and [turn insertion](../../../plugins/agent-bridge/src/agent_bridge/db_events.py).
- [Native-bound request](../../../plugins/agent-bridge/src/agent_bridge/native_bound_prompt.py),
  [route](../../../plugins/agent-bridge/src/agent_bridge/routes/prompts.py) and
  [turn lock/admission](../../../plugins/agent-bridge/src/agent_bridge/session_prompts.py).
- [Per-attempt protocol floor](../../../plugins/agent-bridge/src/agent_bridge/client_protocol_floor.py).
- [Ordinary create/store/send](../../../plugins/agent-dispatch/src/agent_dispatch/ordinary_bridge.py),
  [ready-agent fences](../../../plugins/agent-dispatch/src/agent_dispatch/ordinary_agent_storage.py),
  [seed composition](../../../plugins/agent-dispatch/src/agent_dispatch/embody_prompts.py)
  and [claim-generation transition](../../../plugins/agent-dispatch/src/agent_dispatch/queue_claim_queries.py).

The proposed implementation slice is limited to:

- Dispatch ordinary startup/storage/seed composition: stable original invocation,
  frozen exact prompt, transactional ready intent and validated read-back.
- Bridge request/CLI/protocol and native-bound admission: opt-in keyed send,
  history-only receipt lookup, per-attempt capability refusal and unchanged
  unkeyed sends.
- Existing Bridge turn persistence/migration: unique key plus atomic immutable
  facts/receipt, without a new table or delivery state machine.
- Targeted tests/docs/changefiles for touched plugins, through normal review,
  required CI, containing release and separately authorized rollout.

## Validation matrix

All items below are proposed acceptance gates, not completion claims.

- [ ] Preserve the passing real-host preamble control and its one
  allocation/nonce/host child/conversation/accepted turn, thought/text and
  correlated `end_turn` assertions.
- [ ] Preserve the exact one-turn assertion in the two-real-supervisor-poll
  counterexample; prove canonical deferral, actual retry eligibility and stable
  original creation key/revision/workspace/native identity.
- [ ] Lose a response before admission, while RUNNING and after settlement;
  replay the original receipt/index without another SDK submission.
- [ ] Concurrent identical invocations admit once; different payload or binding
  under one key refuses. Distinct legitimate unkeyed follow-up admits a new turn.
- [ ] A g0 receipt remains readable after an actual g1 claim without task writes
  or fresh permission; missing receipt with claimed/started or foreign ownership
  cannot authorize a stale fresh insertion.
- [ ] Changed task/assignment, owner, native identity, allocation revision,
  retirement or superseded reservation facts refuse fresh work under existing
  AD fences. Do not infer ownership from a generation increment.
- [ ] Old storage ignoring invocation fields, failed read-back, unknown historical
  facts versions and missing/corrupt records fail explicitly without invented
  receipts, new keys or silently weakened compatibility.
- [ ] Old CLI/daemon and receiver replacement respect capability floors and
  distinct keyed-send refusal. Preserve any one known unprompted allocation;
  no unkeyed fallback or repeated empty allocations.
- [ ] Crash after persistence but before SDK scheduling returns admission truth
  without claiming execution or automatically resubmitting.
- [ ] Retain original failing evidence and harness-authoring failures separately.
  No xfail, artificial busy state, assertion weakening or deadline widening.
- [ ] Capture raw receipts, actual reservation/task events, turn/peer counters,
  immutable source/runtime provenance and independent owned-process cleanup.
- [ ] Keep ASGI/queue-facade compatibility evidence distinct from real network,
  model, native-source and task-verification evidence. Record each additional
  validation tier or its explicit unavailability before implementation lands.
- [ ] Review public sanitization and documentation impact, then satisfy normal
  publication/required checks. A source merge is not runtime rollout or adoption.
