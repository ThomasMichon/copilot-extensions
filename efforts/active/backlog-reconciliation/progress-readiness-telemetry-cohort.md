# Exact-worker progress nudges and continuous self-emitted readiness

Canonical contract: [README.md](README.md).

## Qualification and outcome

This study preserves #2879 and #5215 through five whole original/comment
texts, 17,909 characters. Fresh unchanged metadata, editor/history and complete
comment rosters preceded transitive whole-reference preflight. Five whole
messages provide five noncompletion relations; one protected whole-message
relation was withheld before reading, without source, neighbor or subset retry.
Both originals remain OPEN with independent custodians.

## #2879 - Nudge the actual worker, not every quiet process

Built-in supervision must safely nudge a STARTED worker no longer progressing,
covering both direct IDLE and quiet-but-live cases while distinguishing legitimate
waits. Quiet time is not completion, death or authority to seize work.

Credit #3504's resume prompt, #3732's lifecycle exemptions/task-directed wording
and #4152's live-nudge refinement. Direct-IDLE and stale-activity nudging are
wired into supervision; source existence is not full-case native forward progress.

Retain supported worker-shape coverage using trustworthy current-owner/body
evidence and an appropriate bound. Establish actual progress/activity/heartbeat/
liveness timestamp relationships: heartbeat need not mean progress or hide
staleness. Account for pending/delivered wakes to avoid duplicate turns/storms.
Preserve yielded/detached, operator-steer, suspended/cold and lifecycle exemptions.
Target the exact worker with its recorded goal/completion criteria and observable
outcomes.

Validation retains stale-but-live, direct IDLE, recent progress, existing wakes,
legitimate waits, owner/generation changes, probe/send failures and cooldown/retry
behavior. No numeric SLA or cooldown was specified. Preserve LIVE/GONE/UNKNOWN,
manual work, #6677's explicit-resume policy and non-expiring/live/unknown claims.
The residual stays under #2879 unless its current owner accepts a complete
transfer; this is the original specific condition, not a new blanket ACK gate.

## #5215 - Publish ongoing serving readiness through the existing sink

The contract is continuous, self-produced readiness/outcome telemetry grounded
in actual serving responsiveness, usable by operators/health consumers even
when no command runs. Keep it separate from #5225 supervision ownership.

Credit the actual HTTP readiness/identity check, post-listening start record,
session/reader telemetry and #4727 generation-marker/drill observability.
Command/deploy-driven checks and one-time lifecycle records are not a continuous
overall launching/ready/degraded signal through the configured telemetry sink.

Use the existing telemetry seam with honest integration, unknown/unavailable and
consumer-staleness semantics. A dead process cannot emit; another instance's
healthy endpoint is not this instance. Degraded telemetry grants no restart,
kill or claim authority. The original specifies neither a numeric interval nor
a vendor sensor, new daemon or universal native-test gate.

## Completed-work comparisons

| Original | Whole candidate | Contract comparison |
|---|---|---|
| #2879 | Commit `20ec6d6544e3465a25ae4daa57eb0bb270282243` | Admission-safe polling/pending-script behavior is not quiet-live progress qualification, exact nudge outcomes or the complete wake/cooldown matrix. |
| #2879 | Commit `a4eb1bea87773f9dbdbbb26fa655af58bbcca585` | Retained native startup/retry custody is not proof of actual forward resumption or legitimate-wait discrimination. |
| #2879 | Commit `29aa3cd82fbc1e1f471714e61e8385463cc244ff` | A short documentation selection for startup budgets and STARTED counter resets is not implementation or native nudge acceptance. |
| #5215 | Commit `b924eae22610b5f0f7f638c802f4fddd30d96667` | Strict typing of canonical Update Telemetry preserves native output and independent consumers, not continuous self-emitted serving readiness. |
| #5215 | Commit `047b1bfcb812151e52732b09ae707d82c27acb83` | Idempotent native send/HTTP-51 evidence preserves ambiguous acceptance and exact admitted operations; it is not continuous readiness/outcome publication. |
| #5215 | Protected whole-message relation | Withheld before reading; no linked source, neighbor or subset retry or completion credit. |

No worker nudge, health request, sink configuration, spool read, daemon,
integration or native/system probe was performed. See the [dated
journal](<journal/2026/11.04 progress-readiness-telemetry-contracts.md>).
