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

The original planning PR ran structure/reference/privacy checks and source-plan
review only. Every implementation PR must
state which tiers ran, results and reasons for any omission, with no live reset
as a substitute for an isolated loss test.

## G1 admitted evidence

- Windows contained runner:
  `python tools/run-plugin-tests.py agent-worktrees --test-file tests/test_recovery_facts.py`:
  **79 passed**, including file-backed close/reopen and relocated/backup fixture
  roots. All actual file creation is in test-owned roots, not live installations.
- Ordinary placement opens exactly two identity markers and performs no history
  enumeration with 0, 100 and 1,000 real synthetic historical files.
- Malformed/duplicate/truncated/oversized envelopes, unknown required
  capabilities, lossless nested extensions, foreign paths and reparse-ancestor
  refusal are covered. Software-version variations are synthetic; they are not
  historical-runtime qualification.
- Initial long parametrized test IDs failed on Windows's 32,767-character
  environment-variable limit; explicit short IDs fixed the fixture and the
  complete focused rerun passed.
- Source review found JSON exponent overflow could become infinity despite
  literal-nonfinite rejection. Fact and marker readers now reject both signs;
  five added regression cases also cover typed excessive-integer refusal.
- A follow-up precision finding is covered by explicit scoped refusal for
  decimal underflow/excess precision before exposing a candidate. Exact decimal
  oracle tests verify that supported numeric values survive unrelated edits;
  fact and marker paths share the same parser.
- Near-limit Unicode checks include both input and canonical escaped output;
  future marker versions produce `unsupported_schema`, not identity conflict.
- Marker I/O denial/failure is `source_unavailable`, distinct from malformed
  JSON, missing marker and excessive nesting; fixture faults cover both stat
  and open without touching real permission settings.
- Ruff F/E9, install-contract, documentation consistency and effort-structure
  checks passed. Linux/native Git adoption, process-crash/power-loss, actual old
  runtime and external-venue qualification remain unexecuted: G1 has no live
  integration/installer/default writer, and those effects are not admitted.

## G3 admitted evidence

- `python tools/run-plugin-tests.py agent-worktrees --test-file tests/test_recovery_facts.py --test-file tests/test_recovery_report.py`:
  **175 passed** on Windows (79 G1, 96 G3), through the contained runner.
- G3 uses the real `doctor_cli_parser.add_parser` and
  `maintenance_cli.cmd_doctor` consumer, never a synthetic alternative framework.
  Actual `__main__.main` regressions cover explicit project spellings, relative
  request paths and mixed repair options before any registry/CWD discovery.
  Accepted abbreviated report options are covered with both separated and `=`
  values, across explicit project spellings and `--fix` refusal.
  Selected existing `test_cli_routing.py` cases: **9 passed**, 166 deselected
  (`extract_project_flag`, active-project, CWD-preservation and missing-project
  selectors). Unencodable paths are typed `invalid_root`.
  Fixtures forbid project/session/registry scans, provisioning, Git subprocesses
  and the repair body; mixed mutation/target options refuse before input reads.
- Protected synthetic index/stash/untracked/ignored/checkpoint/credential and
  record bytes retain exact bytes/mtime. Unknown optional data is untouched.
  Missing cache, missing/foreign anchors and unsupported/malformed/truncated
  facts remain typed evidence; caller assertions cannot mark G2 migration complete.
- Ordinary reports open exactly the request and two marker files, with
  `6 * fixture-root-depth + 11` lstat calls and no enumeration at 0/100/1,000
  history files. Explicit file selection opens at most the declared 0–32 budget
  and labels omitted counts; it never replays facts or discovers directories.
- Ruff F/E9, install-contract, docs consistency and effort structure passed.
  Full-process/native Git, Linux, installed/runtime and live-resource tests are
  not claimed: execution is explicitly limited to isolated synthetic resources,
  and no later mutation/rollback/installer capability is admitted.

## Compatibility proposal evidence boundary

The [compatibility proposal](compatibility-admission.md) and
[candidate provenance](candidate-provenance.json) record static source/archive
inspection only: four archive SHA-256 checks, 15 wheel-member digest checks per
archive and six pinned module-byte comparisons. JSON/local-reference/effort
structure and public-safe scope are checked for publication.

No historical candidate import, installation, execution, native custody,
old-writer behavior or mixed-version test is claimed. The exact proposed matrix
and containment must be admitted before running it. Source-artifact identity is
not installed-runtime provenance; the observed R191 artifact remains unbound.
No runtime tests are needed for this documentation-only delta, and no plan
review silently selects a support floor, retires a store or enables G2/G4/G5/G6.
