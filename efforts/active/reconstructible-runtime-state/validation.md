# Isolated qualification and cost plan

Back to [effort](README.md). These are future **agent-recommended test designs**,
not results or permission to reset an installed system.

## Fixture and containment contract

Use a disposable test-owned repository, synthetic session/checkpoint records,
fake vault/account/consent authorities, deterministic provider adapters and
test-owned runtime roots. Explicitly redirect every state root, HOME/config,
endpoint and provider handle; deny real external mutation. No recovery test
may discover the developer's installed roots, existing agents or shared
services. Test-only subprocesses are owned, headless and cleaned up.

Preservation assertions compare original bytes and Git state: staged index,
unstaged content, ignored/untracked files, stash refs/objects, branches and
worktree metadata; conversations and every checkpoint; binding/account/consent
files; secret fixtures without printing values; task payload/results/steers;
claims, leases, lineage, obligations and accepted/uncertain operation records.
An existing foreign file in a proposed recovery directory must remain untouched.

Use the normal mutation paths to seed facts before removing **only the explicit
disposable manifest**. Do not save a central-record/database clone and call its
restore reconstruction. The expected semantic oracle is independently derived
from durable anchors and provider state, excluding volatile PIDs/timestamps.

## Required matrix

| Scenario | Required evidence |
|---|---|
| Delete all qualified derived indexes, receipts and runtime projections | Rebuild identity/relations/outcomes from declared anchors; repeated rebuild is semantically equivalent and idempotent |
| Mixed store with unmigrated task/claim fact | Preview refuses destructive classification; original store survives; no blanket directory deletion |
| Legacy checkout without marker; moved worktree/reused path/duplicate codename | Read/adopt candidates are explicit; stable identity wins over path hints; ambiguity restricts only the affected mutation |
| Transfer/release/finalize then loss | Tombstones and lineage win over old local markers; no released obligation, task or dead session resurrected as live ownership |
| Missing central worktree record before finalize | Read remains usable/degraded; cleanup cannot treat unknown completeness as zero obligations |
| Crash before/after intent persistence, admission, external call, result persistence, acknowledgement, projection build, health gate and pointer selection | Restart each exact boundary, including **after acknowledged mutation**; recover accepted result once, retain uncertainty, do not duplicate effects |
| Resource created but response or separate tag lost | Lookup by operation/correlation identity adopts the same resource; impossible/ambiguous lookup remains uncertain, not retried |
| Duplicate/reordered events, truncated last frame, interrupted snapshot compaction | Supported complete facts retained; record revision/lineage determines state; corrupt/unsupported facts visible; terminal facts not dropped |
| Two current writers race at one expected revision | Exactly one accepted revision/effect; loser receives typed revision conflict; no overwrite of unknown fields |
| Older deliberately elected reader/writer on newer additive data | Known operations work; nested unknown fields round-trip exactly; no forced upgrade or foreign-owner verdict |
| Newer explicitly superseded writer on its newest schema | Revoked-incarnation rejection occurs at the effect; software version cannot restore authority |
| Missing cached epoch followed by recovery while a former writer survives | Surviving durable authority or exclusion prevents old tickets committing; if not provable, defer rather than reset generation and collide |
| Unknown required operation capability / unsupported durable schema | Read supported portions without reinterpretation; affected effect refused with doctor action; no global foreign-owner claim |
| Busy owner, unknown liveness, lease or exact-target mismatch | No stop/delete or ownership takeover; unrelated observation remains usable |
| Missing runtime receipts with surviving approved source | Installer-owned report/adoption route; health failure leaves prior selection; reparse/unknown slot targets are never deleted |
| Publication crash then rollback | Resume transaction by ID; selected compatible view only; no rollback of accepted facts, credential changes or blind external compensation |
| Result classification | Assert refusal vs deferred vs warning vs optional omission and exit/JSON adapters; after-send timeout stays uncertain |

Extend existing focused suites rather than duplicate their semantics:
Worktrees `tests/test_session_projection.py`, `test_record_read.py`,
`test_record_cache.py`, `test_tracking_claim_write.py`, `test_claim_history.py`,
`test_claim_handoffs_accept.py`, `test_run_claim_reservation.py`;
Bridge `tests/test_db_adoption.py`; installation-context
`tests/test_slot_release.py`. New recovery modules get dedicated fixture suites.
Component owners select exact lease/task fixture selectors after their fresh
source trace; this proposal does not claim those adapters are already qualified.

## Ordinary-read bounded-I/O acceptance

Hold active resource count and requested result size constant while adding
historical cardinalities 0, 100, 10,000 and 100,000. For exact record reads and
recovery-status reads, instrument file opens/stats, bytes parsed, directory
enumeration, journal records visited, provider calls and lock acquisitions.

- Warm exact reads touch only the selected record/generation and bounded
  metadata; no history replay, full session scan or cross-resource walk.
- Cold reads may initialize their exact scope, not enumerate historical stores.
  Missing projection returns a typed recoverable result, not a hidden rebuild.
- At fixed active scope, historical growth must not increase ordinary-read
  operation counts or parsed bytes; changed resource revisions invalidate only
  relevant entries. Report timings as supporting evidence, not the sole gate.
- Explicit rebuild may scale with surviving input facts. Record scan/byte counts
  and peak memory, checkpoint/resume work, and compare full rebuild to incremental
  projection reduction. Bounded batches must not discard unresolved facts.
- Mutation tests retain current-owner/target/revision verification even when
  read caches are warm. Faster reads never authorize stale destructive effects.

These counters belong in fixture assertions or existing diagnostic seams.
Telemetry primitives/adapters and status/feed implementations remain with their
owners (#7935/#5900 and #7936/#7937/#7946); this lane introduces no alternate
telemetry contract and never rebuilds from sampled data.

## Staged evidence and rollout gate

1. Unit fixtures for parsing, preservation, reduction, reasons and cost counts.
2. Simple end-to-end CLI/subprocess test against fixture Git and fake provider
   boundaries; crash injection includes an acknowledged effect and lost reply.
3. Clean-room first-touch and migrated-store fixtures with supported old/current
   software, actual old-writer behavior and revoked-newer behavior, on Windows
   and Linux. Native process/custody cases use the existing periodic/manual
   integration lanes, not expansion of required unit-only PR CI.
4. A live external-venue test requires separately authorized test resources and
   an eligible owned worker. Under this effort's present local/design-only
   boundary it is **not run**; record that restriction, not a fabricated pass.
   Existing Bridge agents, remote workers and live installations are excluded.

The current planning PR runs structure/reference/privacy checks and source-plan
review only. Runtime unit, integration, clean-room and live-venue tests are
deferred because no executable behavior changes. Every implementation PR must
state which tiers ran, results and reasons for any omission, with no live reset
as a substitute for an isolated loss test.
