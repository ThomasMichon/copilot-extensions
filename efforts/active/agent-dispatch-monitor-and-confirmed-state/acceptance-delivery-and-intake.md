# Exact-owner delivery and consume/intake acceptance

Canonical component of [this same Active effort](README.md). This is not a child effort, transfer or independent completion gate.

<!-- backlog-reconciliation:7556:qualified-thirteen:3002:Plan -->

### Proposed exact owned STARTED/null-owner-session delivery contract

_Agent-recommended finite Phase 7 clarification of the complete current
source contract. Existing durable/idempotent acceptance, explicit
`no_owner_session` receipts, custody fences and shared drafts are credit,
not proof that the rightful worker received or applied the requirement._

- [ ] **D3002-P1:** Preserve ALL THREE original alternatives for an owned
  STARTED task lacking `owner_session_id`: correct routable binding BEFORE
  steerable/ready advertisement OR supported alternate exact-owner/headless
  route OR explicitly undeliverable/not-ready outcome with actionable
  recovery. Never infer the worker from a newer head, default coordinator
  or unrelated session.
- [ ] **D3002-P2:** Distinguish durable data acceptance, route readiness,
  enqueue, transport delivery, worker consumption and actual application.
  Preserve accepted input through restart/idempotent retry and asynchronous
  startup; do not discard durable input merely to reproduce an older output
  shape.
- [ ] **D3002-P3:** Temporarily unroutable accepted input has queryable,
  actionable pending/blocked state and correct-owner binding recovery
  without loss or false success. Permanent unsupported routing is explicitly
  undeliverable/not-ready, not success-shaped readiness.
- [ ] **D3002-P4:** Preserve exact task/source authority, owner/session/
  generation and request identity across late binding, retry, concurrent
  owner change and stale delivery. Admission/rebind belongs to the owning
  lifecycle, not the submitting session.
- [ ] **D3002-P5:** Distinguish STARTED/null from SUSPENDED cold-headless
  `resume_requested`, intentional detached wait and no owner. Cover
  supported local/fleet/interactive variants without treating one branch's
  fixture as the entire routing matrix.
- [ ] **D3002-P6:** Preserve delivery/consumption/application acknowledgement
  gates and visible failures. Exercise null binding, unavailable/unsupported
  route, owner-change race, restart/retry and eventual exact-worker
  consumption/application through existing receipt/outbox machinery.
  Stored steer or accepted Bridge prompt is not a completed requirement.

### Proposed finite intake: uncertain consume and identifier kinds

_(Agent-recommended documentary refinement; no task mutation or service
operation is authorized.)_

- [ ] **D4202-P1:** After uncertain consume mutation response/read timeout,
  read the EXACT task from the SAME selected authoritative writer BEFORE
  concluding failure or retrying. Committed work may lose its reply;
  never replay at another coordinator/database or infer noncommit from
  generic transport failure.
- [ ] **D4202-P2:** Continue only on independently confirmed expected
  success for this exact task, attempt/incarnation/generation, worker AND
  owner session. Shared worker_id is insufficient. Preserve binding CAS,
  stronger ordinary success/claimed/started fences, true competing winner/
  lost race and spent/retired handoff refusal.
- [ ] **D4202-P3:** Continue idempotently with the correct consume transition
  AND payload; preserve deferred-completion VERSUS baton and suspended/
  deferred paths. Delivery is not work completion or permission to recreate
  a spent baton. Failed/conflicting reconciliation stays visibly uncertain,
  with no ownership reset, expiry/release or blind non-idempotent retry.
- [ ] **D4202-P4:** Credit pre-delivery connection-only retries, keyed/
  capability-qualified steering and existing selected re-fetch/fences at
  their scopes, not generic consume retry. Preserve actual task/writer/
  session custody and truthful unknown outcomes.
- [ ] **D4202-LV:** Through the actual consume/client/authoritative-task
  boundary, cover server-committed/lost-response AND true-lost-race cases,
  exact-attempt success, same worker in another session, CAS/stale
  generation, spent/retired baton and failed/conflicting reconciliation.
  Prove same-writer read before retry, correct payload/transition and
  deferred-versus-baton truth, with no second mutation or manufactured
  release. No new native benchmark or service probe is required.
- [ ] **D4214-P1:** Qualify semantic worker-versus-task wrong-kind refusal
  at BOTH claim admission AND completion, including accidental task-as-worker
  and worker-as-task/wrong object cases. Separate field names or an admitted
  string later matching its own persisted owner are not independent proof.
- [ ] **D4214-P2:** Preserve legitimate ARBITRARY worker names, accepted
  identifier kinds, existing task existence/state/owner/session/generation/
  native-incarnation and idempotent-completion fences. Credit the settled
  typed/completion foundations; change only a proven uncovered boundary,
  not a new global namespace, lexical restriction or identity registry.
- [ ] **D4214-LV:** Exercise actual authoritative claim AND completion
  wrong-kind refusals and valid arbitrary-name positive controls,
  retaining all existing ownership/incarnation/error/idempotence safeguards.
  Separate source/consumer/release proof from any original native request;
  no fabricated live invalid-ID result or whole backend/UI/native gate.

<!-- backlog-reconciliation:7556:qualified-thirteen:3002:Validation-Plan -->

- [ ] **D3002-V1:** Resolve every D3002-P1 through P6 at the original finite
  route/readiness and consumer tier. Retain ALL THREE OR alternatives and
  independently observe acceptance, readiness, enqueue, delivery,
  consumption and application across restart/idempotent retry; asynchronous
  startup is allowed and accepted input is preserved.
- [ ] **D3002-V2:** Cover temporary pending/correct-owner recovery, permanent
  unsupported/not-ready, null/unavailable failures, late binding and
  owner/session/generation/request races. Distinguish STARTED/null from
  SUSPENDED cold resume, detached waiting and unowned work across supported
  local/fleet/interactive variants. Existing custody/receipt/outbox owners,
  #2879's independent transfer duties and current SUBMITTED/COMPLETED
  direction remain; no speculative session selection, competing dedup or
  automatic task takeover.

- [ ] **D4202-G:** Resolve ALL D4202 Plan/local Validation: exact same-writer
  uncertain-result reconciliation, independently proved task/attempt/worker/
  session/generation success, correct payload and deferred/baton semantics,
  genuine-lost-race refusals and visible unknowns without replay or release.
  Preserve every incumbent ROOT gate; documentary delivery is not task
  completion, execution, custody transfer or parent completion.
- [ ] **D4214-G:** Resolve ALL D4214 Plan/local Validation at original finite
  tiers: semantic kind refusal at BOTH claim and completion AND legitimate
  arbitrary-worker-name positives with existing typed/owner/incarnation
  foundations intact. Intake is not an implemented namespace repair,
  native request result or source/resource disposition.
