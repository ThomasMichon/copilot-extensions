# Prompt Receipt Observation

- **Slug:** `prompt-receipt-observation`
- **Repo:** copilot-extensions
- **Branch(es):** managed proposal and implementation PRs
- **Created:** 2026-10-22
- **Status:** Blocked
- **Vision:** `visions/plugins/context-handoff/README.md`: continuous work,
  durable recovery locator, objective and authority boundary
- **Umbrella issue:** #7942

## Guiding Intent

First-prompt receipt observation must never delay a user's turn on runtime
provisioning, identity discovery or an unavailable optional worktrees service.
Observation remains distinct from consuming a baton or acquiring ownership.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| hinged-socket | Proposal, implementation, validation and source PR owner | Managed source worktree; #7942 |

## Coordination

- **Topology:** sequential proposal and implementation PRs.
- **Host (owns PRs):** hinged-socket.
- **Delegates:** none; required independent review uses repository review.
- **Handoff:** source qualification packet; release and installed adoption
  remain outside this bounded source objective.
- Preserve existing source claims. Collision detection is read-only forge/task
  inspection; no contact with pre-existing Bridge agents, agent-chat or remote
  worker dispatch. Any review requiring such contact is a blocker, not an
  exception. Only campaign-owned native workers or newly created owned local
  agents may participate when explicitly needed.

## Context

The first `user.message` callback currently calls `agentWorktreesGet` and
`logHandoffPromptReceived` synchronously. Their shared CLI path may resolve
and provision an optional runtime. Existing async non-provisioning helpers
provide a seam, but merely wrapping synchronous work in a promise is insufficient.
This closes existing continuity intent without changing hosting authority.
Design principles: optional siblings degrade independently; payload-only
shape remains; Windows and POSIX share the lifecycle contract.

## Request

Public-safe excerpts of the authorized bounded request:

> "make first-prompt recovery receipt observation asynchronous and non-provisioning"
>
> "Implement cheap locator capture, launch-provided identity preference and bounded
> async receipt observation. NO synchronous execFileSync/installer from prompt event,
> no per-event unbounded queue/process/thread."
>
> "Missing runtime/blocked identity/absent worktrees service must not stall user's
> turn; preserve idempotent replay, pending/unavailable state and later
> reconciliation. Ownership/consumption authority is unchanged."
>
> "Whole hook objective normally instant, <=1-2s worst-case; configured timeouts
> not measured proof."

Scope: context-handoff extension/core/timeouts, their tests and directly related
documentation. Excludes native launch scripts, worktrees cache/readiness,
Manager, telemetry, mention guard, installed payload and shared services.

## Plan

### Phase 1 — Reviewed proposal
- [x] Land this bounded plan through repository review and required CI.

### Phase 2 — Observation implementation
- [x] Capture only the first prompt's canonical locator without synchronous CLI
  calls; prefer existing launch identity without inventing authority.
- [x] Use one bounded async observation, with coalesced duplicates, explicit
  pending/unavailable/logged outcomes and later reconciliation.
- [ ] Prevent provisioning and bound the complete child lifecycle, including
  cancellation/cleanup, rather than only promise scheduling.
- [ ] Preserve receipt format, replay behavior and all consumption/ownership checks.
- [x] Implement the approved atomic backend, bounded history output and late-session
  recovery corrections; retain process-custody qualification as a separate blocker.
- [ ] Update consumer documentation, add changefile, review and merge source.

## Validation Plan

- [ ] Unit coverage: launch identity, missing runtime, blocked identity, absent
  service, concurrent duplicates/replay and deferred availability.
- [ ] Native consumer integration: exercise the actual registered callback with
  blocked child processes and verify return latency, event-loop responsiveness,
  process cleanup and no installer descendants.
- [x] Isolated first-touch execution with no provisioned runtime.
- [x] Measure callback and lifecycle durations; target normally instant callback,
  at most 1-2 seconds worst case. Do not call configured deadlines evidence.
- [ ] Run applicable source checks and required CI; assess independent review.
- [x] Record genuinely available safe external qualification, or explain skipped
  tiers without implying installed/released/live proof.

## Proposal

Approved in #8014. Implementation may refine internal seams after the plan clears;
new authority or ownership behavior requires a separate proposal.

## Journal

### 2026-10-22 — Bounded source intake

- Confirmed open #7942 has no existing implementation PR or competing task.
- Created isolated managed source worktree `hinged-socket` and claimed the issue.
- Traced the synchronous prompt callback, identity helper and receipt logger.
- Captured the authorized scope and latest coordination restrictions. No existing
  agent session was contacted and no shared runtime was changed.

### 2026-10-22 — Proposal reviewed and merged

- #8014 received Copilot approval and green required CI; merged as
  `b2ed7bf3b24e47e82197f36db2ccd7a2292902fb`.
- Re-read the plan after merge. Implementation and all runtime validation remain
  open. The managed sync refused the deleted merged PR ref; a clean, backed-up
  local rebase onto fetched `origin/dev` dropped the already-applied proposal.

### 2026-10-22 — Source implementation and local qualification

- Registered callback now captures the locator and schedules one coalesced
  observer. It has no synchronous CLI or runtime resolution. Qualified launch
  identity avoids a lookup; other sessions retain binding-first async discovery.
- One-second shared subprocess budget plus bounded tree termination replaced an
  initial 1.5-second design: the initial tree-cleanup measurement was 2.13 seconds,
  exceeding the requested ceiling. The revised runs stayed below two seconds.
- `node --test` over `prompt-receipt.test.mjs`, `cli-timeouts.test.mjs` and
  `handoff-core.test.mjs`: 117 passing tests. Largest observed final callback:
  0.10 ms; first-touch lifecycle 1,689.82 ms; blocked identity and tree cleanup
  1,827.39 ms; cancellation and cleanup 1,711.35 ms; deferred successful native
  receipt 838.43 ms. These are measurements, not universal latency guarantees.
- A second native Windows run from `pythonw.exe`, monitoring only its own
  descendant windows across timeout and cancellation cycles: ten passing receipt
  tests, 93 samples / 24 owned processes, zero visible owned windows and zero
  owned foreground transitions. Callback maximum 0.10 ms; first touch 1,692.08 ms,
  timeout cleanup 1,677.05 ms, cancellation cleanup 1,225.78 ms, deferred success
  745.76 ms. Fixtures and captured output were removed.
- First-touch uses the real source runtime resolver under an isolated empty home,
  not an installed runtime or provisioner. Deadline expiry remains honestly
  unavailable. Native success uses a controlled CLI child, not a live service.
- `python tools/check-install-contract.py`: all 13 runtime plugins conform.
  The Python plugin runner rejects `.mjs` selectors by contract; these tests use
  the existing Node test runner directly. Python 3.9 cannot run repository tooling;
  the already-installed Python 3.11 ran the contract and changefile tools.
- External/installed SDK-host qualification and Linux execution were not run:
  this bounded source lane has no authorized external deployment, remote worker
  dispatch is prohibited, and no shared runtime was changed. The tested production
  registration helper is wired by an extension-source contract assertion.
  Release promotion and installed adoption remain separately owned, not claimed.
- Documentation impact: the plugin README now explains observation scheduling,
  failure/retry and cleanup. No configuration, consumption or ownership change.

### 2026-10-22 — Independent review blocks source completion

- The implementation review identified five substantive unresolved cases:
  cross-process check/append races; failed tree termination retaining inherited
  pipes; output-limit termination bypassing tree cleanup; unbounded retained
  activity history; and a captured missing session ID not being refreshed.
- Prior passing measurements remain evidence for their exact controlled cases,
  not complete lifecycle/idempotency qualification. Reopened the corresponding
  plan/validation checks. No implementation merge or installed rollout occurred.
- **Decision needed:** the scoped implementation needs an atomic receipt
  operation at the existing data owner and a reliable process-custody primitive
  that survives root exit/termination failure. The current context-handoff-only
  CLI composition cannot make the backend check/append atomic, and best-effort
  `taskkill` is not a custody guarantee. Confirm the owner and permission for
  those additional source seams, or select a reviewed replacement design within
  context-handoff. Do not introduce a parallel activity store or silently widen
  this lane into shared backend/lifecycle work.
- Preserve this worktree and draft implementation PR pending that decision;
  source review and merge remain required. Continue without contacting existing
  agents, agent-chat, remote workers or shared installed services.

### 2026-10-22 — Approved bounded backend continuation

- The coordinating owner authorized tightly coupled receipt persistence,
  bounded history and missing-session recovery changes. Reuse existing canonical
  locks, transactions and durable stores; no new generic ledger or authority
  framework. Process custody must first reuse qualified platform/agent-procutil
  APIs; no new broker or native Job primitive and no competing native-launch edits.
- Refreshed issue, PR, task and source-file evidence before implementation.
  Existing ownership remains intact. `activity.py` and `handoff_trace.py` have
  no intervening upstream delta; the only matching receipt PR is this candidate.
  Existing process-custody PR #7833 remains untouched.
- Backend design: one atomic receipt operation under `handoff_trace._append_lock`
  in its existing per-project/worktree trace. Stream matching with bounded
  record buffers returns one compact result instead of exporting history.
  The trace's existing cleanup owns retention. No new ledger is introduced.
- Missing captured session IDs may be filled only from the current session
  object's known identity on later reconciliation; known identities and the
  original locator remain unchanged.
- Implemented the approved backend slice in `handoff_prompt_receipt.py`,
  integrated the existing activity commands and bounded result consumer.
  Forty-seven contained backend/trace tests pass, including two real contending
  processes, history larger than 64 KiB, oversized records, partial-tail recovery,
  legacy receipt migration and fsync failure. Touched Python passes Ruff F/E9.
- The process primitive investigation is now concrete:
  [exact symbols, ownership, reproductions and minimal integration contract](custody-boundary.md).
  Existing qualified `run_caller_owned` handles bounded stream custody, but the
  pre-runtime Python-free invocation measured 3,015 / 2,297 ms cold. The present
  adapter leaks Python descendants on failed tree-kill and output overflow;
  Node-only child fixtures concealed this. No generic native primitive was added.
- Full receipt-suite execution is **not green**: first-touch failed its
  <2,000 ms lifecycle assertion (test-case duration 2,663.75 ms) and deferred
  native success exhausted the current budget.
  Do not merge or reinterpret these as pre-existing failures. Retain the draft
  candidate until the owning lifecycle/readiness seam can be integrated and the
  full negative-path suite passes.
- Final backend revalidation: **48 tests passed** through the contained
  `run-plugin-tests.py agent-worktrees` selector for the receipt and trace files;
  Ruff F/E9 and the 13-plugin install-contract gate pass. Seven targeted consumer
  logic tests pass. These do not replace the full native qualification.
- The committed exhaustive Python-descendant regression runs both custody cases
  and fails both: apparent settlement with a live descendant after 2,800 ms.
  Each test then cleans its exact owned processes and fixtures before asserting.
- Completed an all-open-PR file check, including remaining file pages for large
  PRs #5694, #6928 and #7895: no other open PR touches the receipt backend's
  `activity.py`, `handoff_trace.py` or new receipt module. The known process-custody
  and launch-maintenance owners remain untouched.
