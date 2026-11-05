# Retained manual-task reliability acceptance

Canonical component of [this same Active effort](README.md). This is not a child effort, transfer or independent completion gate.

<!-- backlog-reconciliation:7556:transfer:3681:to:efforts/active/agent-dispatch-monitor-and-confirmed-state/README.md -->

### Direct complete manual-task reliability acceptance retained from #3681

Append to this SAME existing reviewed Active ROOT, not a child-only link. Preserve EVERY
existing checked/unchecked Plan and own Validation row, entire Journal and
task-conversation amendment. Historical CONFIRMED implementation remains credited
history; current work uses SUBMITTED/COMPLETED and never restores the old terminal enum.
Planning owns documentation only; separately approved backend phase owners and
Tasks-pane/UI/native owners retain their actual responsibilities.

#### Plan: original monitor and recovery programme

- [ ] Credit actual #3684 monitor vocabulary, #3715 historical confirmation and #3780
  wired default cooldown. Reconcile current declaration,
  suspend/resume/API/CLI/coordinator paths with default cooldown, chosen override and
  deliberate more-specific-wait/opt-out semantics. A monitor resolves its chosen
  condition; its deadline is not expiry of unrelated live/unknown/nonexpiring task
  claims.
- [ ] Retain round-robin time slicing and the existing interactive versus cold-headless
  resume split. No competing scheduler/wake mechanism, overlapping receiver or unwatched
  accidental wait is introduced.
- [ ] Qualify crash/restart honesty: preserve exact durable task/progress/owner context
  and place an explicit interrupted/unconfirmed-wait explanation at the applicable
  resumed result/steer boundary. A restarted process is not uninterrupted execution or
  proof a wait resolved; never promise the same session/process without evidence.

#### Plan: independent confirmation and current state semantics

- [ ] Resolve emitter auto-confirm and manual/default/opt-out choices onto the task at
  creation, preserving configured domain evaluation. Manual approval remains the
  default; worker completion surfaces ONLY submit. Independent default evaluation may
  immediately confirm where selected but remains a separate policy decision.
- [ ] Leave absent/failing evaluator or pending review pending. Define legacy
  require_verification/existing-task migration without silently accepting existing
  submissions or bypassing domain gates; no worker self-confidence substitution.
- [ ] Use SUBMITTED for review pending and terminal COMPLETED for acceptance. Fence
  accept/reject by relevant submission revision AND owner incarnation; preserve valid
  retry/late enrichment. Do not indiscriminately delete compatibility paths or reopen a
  truly completed parent.

#### Plan: full output, schemas, history and follow-ups

- [ ] Preserve Markdown plus optional structured JSON on submissions and intermediate
  steering outputs, BESIDE existing request_input/answer forms. Submission output is not
  automatically a request for more instructions.
- [ ] Task-owned VERSIONED JSON Schema is authoritative at EVERY governed
  output/revision write. Submission schemas govern submission revisions; intermediate
  steering-output schemas govern those outputs, not form answers. Declared-schema kinds
  require data; other kinds permit Markdown-only. Prose criteria/types do not become
  executable validators or a second schema authority.
- [ ] Keep bounded non-executable validation/rendering, declared
  dialect/local-reference/size rules, no remote-reference fetch and explicit
  diagnostics. Shape-valid but substantively wrong data still requires review; optional
  derived TypeScript types do not replace runtime authority.
- [ ] Preserve append-only output/card/answer/submission/review revisions, rejected
  output/feedback and legacy migration without invented lost history. Drafts remain
  durable unpublished drafts, separate from answer acceptance/publication/delivery.
  Current cards/results are projections, not independent competing truth.
- [ ] Rejection preserves rejected result/feedback and queued continuation under its
  valid task state. COMPLETED follow-up creates a NEW explicit idempotent linked task
  from selected accepted outcome/history and new instructions, with its own
  goal/schema/policy/routing; do not erase or implicitly reopen the completed parent.

#### Plan: fast durable acceptance, actual delivery and diagnostics

- [ ] Persist answer plus durable delivery operation atomically and acknowledge
  immediately after persistence, independently of cold start/bridge prompt/next turn.
  Retry/ambiguous HTTP acknowledgment reuses the exact operation identity through
  existing #5701 acceptance/idempotency, not a second dedup store.
- [ ] Preserve assignment/worktree while making cold continuation QUEUED or CLAIMED;
  mark STARTED only when work begins. No overlap with a still-retiring body. Nudge live
  workers or admit stopped workers through their actual pool/owner; no wake without
  answer/new work/resolved monitor cause.
- [ ] Distinguish transport/bridge acceptance from actual worker consumption, using the
  existing applicable answer receipt and exact task/owner/incarnation fences. Reconcile
  missing routes, stale delivery, lost acknowledgments, disconnects and
  delivered-but-unconsumed input with bounded retry and visible partial/failure
  outcomes, never a fabricated application.
- [ ] Attribute actual launcher/identity/endpoint/HTTP/DB/ack/delivery/worker stages
  with bounded redacted monotonic diagnostics OUTSIDE task conversation history. Retain
  attempt, outcome and unknown metrics without content/credential logs, overlapping-span
  sums or inferring cause from configured outer timeouts.
- [ ] Compose merged #5701 retry/idempotency and #5668 attention foundations as
  delivered interfaces, not still-open claims. Preserve #2057/#3002/#2453, current
  investigating UI/source/native holds and backend-versus-Tasks-pane presentation
  ownership. No competing implementation or responsibility takeover.

#### Direct Validation and the ROOT GLOBAL completion requirement

- [ ] Qualify default/overridden/suppressed cooldown and true resolution through current
  pure, queue, HTTP, CLI/MCP/library and coordinator paths, with race/abandon/repeated
  resume and original round-robin semantics. Preserve source/native/integration tier
  distinctions.
- [ ] Qualify original crash-in-wait/restart case with retained identity/progress and
  the explicit honest interruption result, not a bare recovery label.
- [ ] Manual question -> Markdown/data SUBMITTED -> independent accept/reject ->
  preserved rejected output/feedback -> new linked completed-task follow-up. Validate
  actual capacity release and unchanged accepted parent.
- [ ] Cover manual default/opt-out, emitter auto-confirm on/off,
  configured/default/missing/failing evaluator, pending-task migration and uniform
  CLI/HTTP/MCP/library behavior; shape validation is not substantive verification.
- [ ] Cover missing/invalid/schema-bearing data at EVERY revision, separate
  submission/intermediate/form contracts, Markdown-only allowed kinds,
  dialect/version/local reference/remote-reference denial/bounds and truthful errors.
- [ ] Prove restart-safe append-only card/output/answer/review history,
  draft/publication distinction, stale revision/owner review rejection, rejected-result
  retention, honest legacy backfill and bounded pagination.
- [ ] Prove duplicate/ambiguous acknowledgment records one answer/operation, immediate
  persistence receipt independently of cold/live startup, distinct worker consumption,
  route outage/restart/stale-generation reconciliation, no no-work wake churn and no
  old/new overlap.
- [ ] Preserve actual consumer latency/correlation/privacy tests and original isolated
  real CLI-to-daemon/cold-live-worker Windows/Linux, affected first-use/clean-room and
  available live-venue evidence, with explicit unavailable-tier reasons. Do not force
  effectful matrices into required unit-only CI or invent unrelated every-host gates.

#### Continuing Coordination, Journal and receiving gate

Keep this ROOT Active under the SAME planning, separately approved backend-phase,
Tasks-pane/UI/native, task/evaluator/history/steering and current source custodians.
Record later slices through eligible OPEN bounded-source claims and this ROOT
Plan/Validation/Journal; do not make a closed #3681 a required live coordination token.
A NEW reviewed receiving PR must reconcile operative ROOT/header/amendment pointers that
otherwise require #3681 live, without deleting historical references, altering claims or
assigning all implementation to the documentation planner.

Journal the actual administrative intake only after ordinary review, ACTUAL merge and
FULL CURRENT ROOT Plan AND Validation/Coordination/Journal readback proves every
original, refinement and amendment clause survives directly here, all old rows and
independent owners remain, and no genuine hold still requires the source live. Otherwise
RETAIN #3681. No task/claim/configuration/native/model/process/service/source effect or
implementation authority is created by this proposal.
