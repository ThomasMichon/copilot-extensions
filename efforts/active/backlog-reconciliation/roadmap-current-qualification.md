*The following contribution was assisted using Copilot.*

# Stabilization Roadmap: Current Slice Qualification

Parent: [Backlog Reconciliation](README.md), Phase 2b.
Selection and original finite exits: [next-decision matrix](roadmap-next-slices.md).
Actual tracker outcomes: [follow-through register](roadmap-follow-through.md).

**Status:** All twelve selected slices have bounded read-only qualification,
not an implementation charter. The first six use fixed
`1485994f0e7062c24118cce22ae1ef0ac560b146`; the other six use
`86a0145e35df097d1749dfc141fcee3b608d3dec`. Findings distinguish
source-supported residual predicates, planned integration/admission and a
guard-preserving archive decision. They are not twelve new live incidents,
twelve missing patches or appointments of implementation owners.

The first two results were published in #9408. Ten further slice assessments
were independently admitted after that publication unit was prepared. Source pins
identify the actual inspected revision, not an assertion that a running
installation or later development tip has the same behavior.

**Reviewed publication:** #9426 merged on 2026-10-28 at 19:03:14 UTC
(12:03:14 PDT) as `65d1fda593ab02e0d0229105333208beb975ab0d`.
Its current-head approval includes the corrected #4220 omitted-revert
description below. This completes the selected qualification publication,
not any implementation or campaign-wide acceptance.

## Current decisions and retained boundaries

### #1803 - Large-response framing

- **Current result:** Async TCP/Unix constructors and listeners retain the
  default reader limit and line-based consumers. Existing one-op cleanup and
  synchronous multi-chunk forwarding are credited; a receive chunk size does
  not establish a matching aggregate synchronous cap.
- **Next finite contribution:** Through the existing framing owner, use a small
  request and serialized UTF-8 JSON **before LF** exceeding 65,536 bytes.
  At least 65,537 JSON bytes means at least 65,538 wire bytes with LF.
  Preserve call/attach/forward, explicit oversize handling, cleanup,
  subsequent-call health and no ambiguous post-admission cold replay.
- **Nonedges:** Configurability is preferred, not mandatory. A new protocol,
  daemon, global outcome ledger or complete graceful-cutover programme is not
  required. The in-memory separator-boundary check is not product/native
  transport qualification.

### #9323 - Ordinary permission handling

- **Current result:** Normal generated commands retain blanket permission;
  final construction reaches persistence without the requested known-option
  refusal; ordinary clients default to automatic approval and absent-upstream
  forwarding synthesizes allow. Existing hosting/scoped selection and
  correlated explicit answer/refusal are credited.
- **Next finite contribution:** The original explicit claimant qualifies bare
  and cross-repository construction, changed configuration, pre-persistence/
  transport refusal, the actual no-callback client and absent upstream, plus a
  genuine connected reject with exact request/session/tool/option correlation.
- **Nonedges:** Removing only a generated flag is incomplete. Arbitrary custom
  scripts are not declared safe by lexical scan. #9325, all venue parity and
  all-host rollout are not prerequisites; no claimant is reassigned.

### #4619 - Abort versus final recovery arm

- **Current result:** The gap after the second successful validation and before
  final arm persists for **both file and task backing stores**. Existing file
  consume/abort locking, the real task expected-status transaction and the
  Worktrees record lock are positive foundations, not shared cross-store
  final-arm atomicity.
- **Next finite contribution:** Through the existing store/final-writer owners,
  design deterministic interleavings at that exact last gap and the actual
  final consumer. Both stores need one truthful abort/consume/final-arm result,
  candidate identity, idempotence and supported manual-only/off signaling.
- **Nonedges:** Another validity read, wider timeout or asynchronous receipt
  is not atomicity. An equivalent safe design is allowed; no already-existing
  conditional-arm API, second store, expiry, stealing or whole-root gate is
  invented. No handoff/task/database operation was exercised here.

### #3002 - Accepted steering without a route

- **Current result:** The supported non-acquired STARTED/null-session branch
  accepts input durably without enqueueing an owner wake; authorized late
  binding does not reconcile that answer into delivery. Existing immutable
  receipts, binding CAS, fenced outbox, acquired-worker binding and cold
  SUSPENDED resume remain credited. Independent worker polling is possible;
  this is not a claim that every task mode lacks consumption.
- **Next finite contribution:** Preserve the original routable binding
  **OR** alternate exact-owner route **OR** explicit not-ready/undeliverable
  alternative. Make accepted-but-unroutable input queryable/actionable; where
  late binding is supported, compose existing owner-fenced delivery and later
  consumption/application observation without changing the original receipt.
- **Nonedges:** No fabricated session, guessed head, keyed resubmission, new
  owner/outbox store, synchronous startup or whole conversation/native
  programme. Current source evidence is not an executed stranded-worker case.

### #2861 - Authored expected destinations

- **Current result:** A differing readable authored file at an enabled plugin's
  expected name can still be replaced; later legitimate disable cleanup then
  removes the generated replacement. The delivered stale-sibling repair,
  unrelated unmarked-file preservation and two-plugin source-name conflict
  detection remain credited.
- **Next finite contribution:** Through the existing hoister owner, refuse or
  preserve foreign/unknown expected destinations before replacement and before
  partial batch writes. Keep honest scan conflicts, legitimate generated
  refresh and disabled-owned cleanup. A regular unreadable target can already
  fail before its own write; unreadable-file overwrite is not demonstrated.
- **Nonedges:** Conventional `.github/agents` is not wholly disposable.
  Designated generated namespaces retain their actual replacement policy;
  genuine portable checked-in hoists are not categorically frozen. Filename
  or a copied banner alone is not an ownership grant, but no per-file
  cryptographic registry, whole native-loader gate or new owner is required.
  No real hoist, write, render or deletion was executed.

### #7758 - Dispatch fixed installation actions

- **Current result:** The incumbent's reviewed Phase 4b prerequisite remains
  Dispatch's fixed legacy `stamp`/`provision` admission and qualification.
  Python, POSIX and PowerShell fixed admission front doors safely exclude
  Dispatch. That is consistent with the planned pending integration, not a
  new defect or missing durable execution foundation.
- **Next finite contribution:** The same installation-execution owner admits
  only the canonical-installed/default-legacy-root/originating-project recipe
  through the existing foundation, queue and frozen-input authority, preserving
  actual coordinator/supervisor effects, durable receipt/result, Windows/Linux
  caller-loss/concurrency, immutable failure and no-write refusals.
- **Credit and nonedges:** Existing foundation, native admission, source
  freezer, queue, suppliers and qualified Logger/Bridge adapters are reused,
  not rebuilt. Recipe qualification precedes **that publisher's** later
  adoption, not every publisher, #7030, recurring enrollment or whole ROOT
  completion. No new effort/proposal rewrite is needed; unpublished incumbent
  work remains unknown and cannot be commandeered. No installer or native
  effect was invoked.

### #5512 - Captured-owner restart

- **Current result:** The Phase 1 restart still uses bare `terminate_pid`;
  failure does not guard subsequent metadata removal/spawn, and cleanup is not
  captured-owner conditional. Current birth-bound exit observation, the OS
  singleton lease and an available identity-bound termination helper are
  credited; that helper is **not composed at this restart seam**.
- **Next finite contribution:** Under the same sole Phase 1 owner, qualify
  intended-resident authority and identity-bound control, confirmed original
  exit, owner-conditional cleanup and actual successor publication. Preserve
  finite recovery/defer on publication failure and distinct absent,
  acquired-unpublished and serving reader states.
- **Nonedges:** Spawn alone does not prove two serving residents. Failed
  termination need not become a permanent veto when original exit is
  independently proved. Preserve Windows/Linux/macOS proof **OR justified
  exemptions**; only Phase 1's reviewed merge precedes that effort's
  Phase 2/#5453 design. No live process or service was controlled.

### #4220 - Source-family ownership and alias checkpoints

- **Current result:** The selected acquisition-to-cursor/GC ownership path
  retains a gap, with a **conditional, unexecuted** sequential-alias A/B/C
  counterexample: shared canonical rows may reflect B while another alias's
  completed A cursor selects a net A..C delta. If C reverts a B-only file
  change, that delta can omit the file and leave B-only content indexed.
  The regression targets this stale-row failure, not a delta that performs
  the omitted revert. This does not claim corruption in every integration.
- **Next finite contribution:** Qualify the actual supported shared-family/
  independent-cursor case, then use a shared completed-family watermark
  **OR** explicit stale-alias repair/refusal through the existing authority.
  Compose acquisition, publication/cursor and GC ownership with crash/adoption
  and independent-family controls.
- **Credit and nonedges:** Participating kernel/FTS exclusion, atomic pending
  journal admission/recovery, checkpoint validation, GC refusal and admitted
  #9023/#4222 deliveries remain real. Old writers keep supported
  participation/drain/isolation; no lock retrofit, old-worker kill or second
  service is authorized. No crawl, store or GC action was run.

### #5842 - Joint responsibility acceptance

- **Current result:** Ordinary source release and terminal acceptance still
  precede fallible consumer/resource completion. The receiving record is
  already preflighted: a consumer missing **at entry** is refused before
  source release. The relevant disappearance/fault must occur **after
  successful receiving-record preflight**, or at another later conflict,
  lease or save boundary.
- **Next finite contribution:** Under the original claim/resource owners,
  design a recoverable nonterminal join that retains source responsibility and
  its handoff-specific finalize block until the actual consumer/child/lease
  outcome commits. Cover mixed claims, partial progress, crashes and remote
  reservation without blindly reversing two calls.
- **Nonedges:** Existing actor/state/kind, receiving-project/space, source-owner
  and cross-machine fences remain credited. Inherited #1602 coordination
  readiness, all entry paths and later legitimate binding remain distinct
  obligations; space checks are not readiness proof. No source claim, lease or
  ledger was offered, accepted or released.

### #5904 - Present-UNKNOWN endpoint cleanup

- **Current result:** Under the existing mutation lock, a present malformed,
  unreadable, unsupported or missing-PID publication can still become an
  unvalidated read result that reaches unlink. The actual canonical helper,
  Dispatch/Index/Vault copies and cleanup callers were checked.
- **Next finite contribution:** Preserve present UNKNOWN versus true absence,
  validated own cleanup and successors under the same serialized authority.
  Qualify the real file protocol and affected callers/copies, intentionally
  correcting the old non-UTF8 deletion expectation where still applicable.
- **Credit and nonedges:** #7074's cooperating-publisher serialization and
  successor-PID checks are delivered, not a second lock to build. No new
  incarnation framework, all-host race reproduction or whole #5940 admission
  gate is introduced. No endpoint, PID, file or service was queried or erased.

### #8370 - Executable attempt diagnostics, not another proposal

- **Current result:** The existing own refinement credits #8376's reviewed
  proposal, and its actual reported merge is in the selected source ancestry.
  The retained Draft declaration/unchecked early row is **not evidence that
  another proposal review is needed**. Existing request logs and child-stream
  capture suppliers are not the complete early/retained attempt product.
- **Next finite contribution:** The same diagnostics incumbent reconciles the
  already-reviewed plan and admits the exact executable attempt-writer/
  discovery seam and owned qualification scope. Preserve early-unattributed,
  staged and retained venues; private bounded sanitized both-stream output;
  protocol stdout; primary versus diagnostics outcomes; completed-only
  30-day retention and protected incomplete attempts.
- **Nonedges:** Credit #8934's narrower registered-origin capture. A healthy
  later start is not diagnosis of the original missing exception. The
  #8826 candidate's unpublished state is unknown; no duplicate logger,
  implementation appointment, broker-completion gate or retention cleanup.

### #9290 - Qualified archive mapping

- **Current result:** The Manager non-project archive-source-mapping fallback
  persists; it is a conservative boundary to characterize, not a newly
  measured cause of all update cost. Delivered composer/cache/hardlink work
  and #9389's released distinct build-requirement groups remain credited.
- **Next finite contribution:** The existing source-assembly owner qualifies
  admitted project/archive identity, active Linux versus inactive Windows
  markers and complete dependency/build inputs **OR** independently proves
  removal through the existing affected-feed qualification authority.
  Preserve supported fallback until that decision is justified.
- **Nonedges:** No all-mappings guard skip, dropped archive, marker filtering
  without semantic proof, unioned build environments or duplicate provenance
  scheme. Source/build/profile invalidation, isolated tooling, interrupted
  candidate refusal, immutable hardlinks and existing #7606/#8949/#5851/
  #6937/#6941 custody remain. Actual prepacked-service consumption and the
  complete strict-under-60-second ledger including payload refresh remain
  separate overall duties; partial timings are not the full equation.

## Independent admission and proof limits

For the four continuing reports (#4619/#3002/#2861/#7758), independent checks
verified 39 raw envelopes, all 77 current original/refinement texts against
actual responses and admitted prior UTF-8, full collaborator/author/editor/
version coverage and stable terminal guards. Exact source captures and all
65/27/17/52 source quotations were checked; #7758 additionally has eight
literal acceptance quotations. Historical unchanged predicates were reused
only after comparing the actual relevant current source/test blobs.

Those are evidence-consistency checks, not product tests. The failure/race/
delivery/cleanup examples remain unexecuted fixture designs. No claim,
resource, service, runtime, task, handoff, installer or store was operated,
released, transferred or repaired. Existing source and resource custodians,
literal alternatives and original venue/metric obligations remain.

For the final six, independent checks verified 43 raw envelopes, all
25 current texts and two necessary actual #5842 history snapshots against raw
responses and admitted prior UTF-8. These are two snapshots, not two inferred
edit operations. Current immutable source and 69/31/49/41 source quotations
were checked, with twelve additional literal #8370/#9290 acceptance quotes.
All actual eleven-member roster and whole-record terminal guards were stable.
The history preserves the distinct readiness intake; the corrected #5842 fault
timing and #8370 reviewed-plan credit govern the projected first slices.

## Continuing inflow and parent work

The three eligible arrivals #9412/#9413/#9414, outside the frozen four-wave
roster, now have independently admitted [bounded inflow decisions](roadmap-inflow.md)
at newer fixed `326a72d1332afe351e7f5e8ac0841ecba56a8230`.
#9413's original finite producer outcome is completed; #9414's P1 standalone
snapshot closure and #9412's P2 final-verdict shape remain selected-source gaps
with their existing claimant. The excluded arrival stays metadata-only and
counted. No implementation or broader native/rollout gate follows.

The later complete 20:23 UTC inventory contains three further eligible
arrivals, #9435/#9436/#9442, outside this qualified subset. Their separate
[bounded P2 assessments](roadmap-inflow.md#later-inflow-at-fixed-94df-three-retained-p2-gaps)
are independently admitted at `94df`, with actual later `611e` source delivery
separately credited. Precise residual branches, unknown runtime/native causes
and finite qualification remain explicit. They are not
silently covered by the historical four-wave count or implementation grants.

Qualifying these twelve first slices does not establish a complete execution
order for every priority, qualify running systems or reach the numerical gate.
The existing scoped dependency register, original acceptance and owners still
govern any later authorized contribution.

The [parent Plan and Validation Plan](README.md#plan) remain open.
