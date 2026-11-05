# Task transfer and acknowledged execution are different contracts

Canonical contract: [README.md](README.md).

## Qualification and outcome

Preserved #6189/#7268 through fourteen whole original/comment texts, 28,581
characters, after unchanged complete metadata/editor/history, collaborator
roster and protected-reference checks. Six whole ranked commit messages are
comparisons, not completed acceptance. Both originals remain OPEN.

## #6189 - Two independent, still-undesigned outcomes

Retain a safe cross-coordinator transfer for UNCLAIMED queued/proposed tasks
and graceful emitter narrowing after machine reassignment. Remote creation
and override are transport precedents, not a transfer transaction. The actual
mutating Dispatch precedent is `remote_dispatch.dispatch_to_remote`, not
`force_stop.py`'s Bridge transport.

The proposed target-create/source-abandon sequence needs reviewed provenance,
partial-failure, idempotence and claim-race semantics. It is not permission
to recreate tasks, move claimed execution, abandon another owner's rows or
operate a transfer. Longer-term federation is context, not a prerequisite
for this finite stopgap.

Narrowing must BOTH stop creating new tasks for a reassigned role AND continue
updates/evaluation for its already-created active local work. A declaration's
all-or-nothing machine cutoff is not that continuation mode. Preserve the
original correction: roughly ninety stale quiet rows did not prove uncovered
business work; delivered review verdicts may correctly become quiet.

## #7268 - Credit implemented prerequisites; retain actual final boundaries

The [canonical handoff effort](../acknowledged-execution-handoff/README.md)
remains Active under the existing assigned owner. Proposal review, explicit
source/test authorization and the adopter effect-contract prerequisite are
already complete, not new ACK gates.

Credit transactional closure/inventory, reservation-send custody, qualified
scope provenance, coordinator enrollment/association and actual coordinator
CI/task delivery plus BOTH wake-outbox mediation. These consumers are not
wholly unwired. Logical physical/OS/container machines retain local coordinator
authority and a separate local supervisor that survives coordinator upgrades;
federation does not silently share stores.

The remaining contract covers authenticated local-supervisor enrollment and
every actual final launch/RESUME/delivery/REDRIVE cohort, complete legacy and
in-flight assignment/effect inventories, adopter-owned final mutation fencing,
durable crash/timeout/late-response recovery, DRAIN acknowledgement and explicit
handback. Accepted effects finish OR remain visibly unresolved before ACK;
empty inventory, construction, enrollment or a valid adapter response cannot
acknowledge a transition. Expiry never clears ambiguity or reopens automation.

Preserve unrelated scopes, natural tasks/workspaces/native history, review
lineage/findings and separate author consent. Deliberate transfer is not
same-native continuation. Focused deterministic CI remains distinct from
isolated periodic/manual real-process/fake-effect, both-direction, mixed-version
and separately authorized running adoption. No scheduler, global shutdown,
process-kill API or live handoff is introduced.

## Completed-work comparisons

| Original | Whole candidate | Contract comparison |
|---|---|---|
| #6189 | `0d9f523d2f5dbcccb062835846958589fe4f8f27` | Same-queue endpoint continuation and stale-read refusal preserve publication custody, not cross-coordinator task transfer or reassigned-emitter narrowing. |
| #6189 | `da6db5171438f3167d308158b5bd4b85d1b4a8fb` | Pool capacity, placement, startup/native retirement and retained workspace custody are substantive source work, not the two requested transfer/narrowing designs. |
| #6189 | `a4eb1bea87773f9dbdbbb26fa655af58bbcca585` | Retained native task redrive and exact retry preserve existing ownership; they do not move unclaimed work to another coordinator or implement emitter narrowing. |
| #7268 | `42ce06d3941774b0d0d7c725a3257500f76cb335` | A charter-bounded autonomy plan is not execution enrollment, final-effect fencing, drain ACK or handback. |
| #7268 | `44b228351e33b0bc39c5739fb05e3cbc890e9f6a` | Windows updater output-stream handoff is a different boundary from Dispatch execution-scope handoff. |
| #7268 | `7206432bda763ba9141937d4f05bca40fc949a28` | Preparation/preservation and supervisor-survival source credit do not complete runtime enrollment, every sender or actual adopter mutation/transition acceptance. |

No product source, queue, task, process, owner or resource changed. See the
[dated journal](<journal/2026/11.04 task-transfer-execution-handoff-contracts.md>).
