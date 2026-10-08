# Task conversation amendment - 2026-10-08

Back to the [canonical effort](README.md). This is a proposed implementation
plan, not as-is documentation. It realizes the
[task outputs and review vision](../../../visions/plugins/agent-dispatch/task-outputs-and-review/README.md).
The [operator record](operator-direction.md) is the intent source.

## Ownership and sequencing

This effort owns backend policy, output/history contracts, follow-up lineage,
and asynchronous delivery. The
[Tasks-pane effort](../agent-dispatch-tasks-pane-ux-overhaul/README.md) owns
presentation, composer controls, and acceptance/rejection/follow-up actions.
Land reviewed intent before executing these extensions, then land backend
contracts before their dependent UI. Each implementation slice gets its own
worktree and PR; this planning worktree owns documentation only.

Consume rather than supersede:

- #1376: existing bounded structured task results.
- #3681: this campaign's lifecycle coordination issue.
- #5701: open idempotent steering acceptance/retry implementation. Coordinate
  with its owner; after merge, prove the remaining timing and receipt gaps.
- #2057, #3002, #2453: existing cold-resume and steering-delivery issues. Re-check
  scope/status before filing further bugs.
- #5668: attention queue proposal maps submitted tasks to review attention.
  Preserve that interface; do not create a second review queue.

## Source-grounded starting point

`queue_lifecycle.py` stores bounded structured results, but unflagged worker
completion also confirms by self-attestation. `queue_completion_review.py`
already confirms or rejects SUBMITTED tasks; COMPLETED is terminal.
`queue_steering.py` retains answers and consumption timestamps, but overwrites
the current card. Cold-headless steering sets `resume_requested` while leaving
SUSPENDED, whereas another path moves directly to STARTED.
`wake.py` delivers asynchronously, but bridge acceptance is not proof that a
worker consumed an answer. The Tasks manifest has limited submitted confirmation
and no complete result/history/follow-up viewer. Treat these as starting evidence,
not timeless status; re-read current source after dependencies land.

## Phase 5 - Confirmation policy

- Emitter declarations gain `auto_confirm`; resolve the effective policy onto
  each created task rather than consulting mutable emitter settings later.
- Manual creation defaults to requiring operator review and permits a per-task
  opt-out. A simple question is still manual-reviewable.
- Worker CLI, HTTP, MCP, and library completion uniformly produce SUBMITTED,
  clear execution occupancy, and cannot also confirm.
- A default evaluator can immediately confirm an auto-confirm task after
  contract validation. Configured domain evaluation remains authoritative
  where required; `auto_confirm` must not silently bypass it.
- Acceptance and rejection remain independent operations. An unavailable
  evaluator leaves the task pending, not auto-accepted.
- Migration decisions for emitter defaults and existing `require_verification`
  tasks must be recorded before implementation. Preserve pending reviews and
  domain gates; do not infer default acceptance from worker confidence.

## Phase 6 - Output, contracts, history, follow-ups

One output envelope carries Markdown and optional structured JSON. Steering
requests use it beside, not in place of, their existing `request_input` form.
Submissions use it without implying any request for more instructions.

An optional task-owned JSON Schema is the runtime authority. Validate structured
data at all write boundaries for its declared output kind: submission schemas
govern every submission revision; steering-output schemas govern every
input-request output revision, separately from the form's answer fields. The
default task answer schema applies to submissions only, never implicitly to
intermediate steering. A declared schema requires data for its output kind;
missing data is a validation error. Kinds with no schema permit Markdown-only
output and optional data. Prose such as `{ answer: number }` remains guidance,
not executable validation. Pin a declared schema version to the task. Derived
TypeScript types are optional consumers, not a second source of truth.

For example, a manual task asks "What is 2 + 2?", has completion criteria
"Provide the answer", and declares:

```json
{
  "type": "object",
  "properties": {"answer": {"type": "number"}},
  "required": ["answer"],
  "additionalProperties": false
}
```

The worker submits Markdown `The answer is **4**.` and data `{"answer": 4}`.
The coordinator checks shape; the operator or domain evaluator judges whether
it answers the question. Valid shape alone does not prove arithmetic correctness.

Journal output/card revisions, linked answers, submission attempts, and review
decisions append-only. Keep the current card/result as projections over that
authority; migrate legacy current records without inventing lost history.
Rejection retains the rejected answer, records feedback, and returns the same
task to queued continuation with prior progress.

Completed tasks cannot reject/reopen. Explicit follow-up creates a new linked
task using selected accepted outcome/history and new instructions; it has its
own goal, schema, confirmation policy, and ordinary routing. Preserve the parent.

**Agent-recommended safeguards:** fence reviews by submission revision and
ownership generation; bound Markdown/data/history pagination; use a declared
JSON Schema dialect with no remote-reference fetching or executable validators;
keep Markdown rendering non-executable; give follow-up creation an idempotency
identity; derive current projections transactionally rather than dual-writing
independent truth stores. These refine implementation, not operator scope.

## Phase 7 - Acceptance, delivery, diagnostics

Record the answer and its durable pending-delivery operation atomically.
Return a receipt immediately after persistence, without blocking on worker
cold start, a bridge prompt, or the next worker turn. Retried confirmation
reuses the operation identity, including after ambiguous HTTP acknowledgment.
Coordinate the receipt contract with #5701's existing deduplication.

Make continuation eligible as QUEUED or CLAIMED without throwing away its
worktree/assignment. Do not mark STARTED until work begins or allow another
worker to overlap a still-retiring body. Live workers can be nudged; stopped
workers can be resumed by the pool. Delivery must distinguish accepted
transport from worker consumption, use the existing answer `taken_at` receipt
where possible, fence stale task incarnations, and visibly recover missing
acknowledgments. Do not wake a worker with no answer, new work, or resolved
monitored condition.

Normal diagnostic logs, not task conversation entries, record:

- operation/task/incarnation IDs, attempt and delivery mode;
- CLI launcher, identity/endpoint resolution, HTTP/DB/response phase timings;
- delivery start, bridge acceptance, worker acknowledgment, elapsed time;
- retry, timeout, transport failure, stale cancellation, and final outcome.

No answer bodies, credentials, or raw private payloads in these logs.
Do not claim the known outer 30-second action or 20-second bridge timeout
proved the latency cause: instrument and identify the actual slow phase first.

**Agent-recommended safeguards:** monotonic durations, bounded retry/backoff,
explicit accepted-but-pending UI receipts, restart-safe delivery/consumption
reconciliation, and redaction-safe correlation across process boundaries.

## Validation matrix

- [ ] Manual `2 + 2`: Markdown and numeric data survive SUBMITTED; review
      releases worker capacity; Accept completes; Reject preserves output and
      queues feedback; completed follow-up creates a new task and leaves parent
      unchanged.
- [ ] Manual default/opt-out, emitter auto-confirm on/off, configured evaluator,
      absent evaluator, and migration of pending existing tasks.
- [ ] Invalid data fails before card publication/submission; valid-but-wrong
      data reaches review without being mistaken for substantive verification.
- [ ] Schema-bearing outputs reject missing data on every governed revision;
      submission schemas do not constrain intermediate steering outputs, and
      Markdown-only output works for kinds without a declared schema.
- [ ] CLI/HTTP/MCP/library parity; schema dialect/version, local reference,
      remote-reference denial, size limits, and explicit validation diagnostics.
- [ ] Restart-safe card/output/answer/review history; stale review rejection,
      rejected-output preservation, legacy backfill, and paginated viewing.
- [ ] Duplicate steer and ambiguous acknowledgment record one answer; cold/live
      deliveries acknowledge independently of startup; worker consumption is
      distinct from bridge enqueue; restart/outage/stale-generation recoveries.
- [ ] Repeated no-work wake suppression, exclusive-lane release, and no
      overlapping old/new workers during queued continuation.
- [ ] Logs correlate each acceptance/delivery/receipt outcome without content
      leakage or task-history retry spam; measure actual consumer latency.
- [ ] Isolated real CLI-to-daemon and cold/live worker integration on Windows
      and Linux; clean-room first-use path where affected and a live venue when
      available. Record evidence and explicit reasons for unavailable tiers.
