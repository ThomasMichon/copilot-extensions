# Stabilization Roadmap - First 200 Issues

**Status:** Evidence-backed planning proposal; not implementation authorization
or a claim that the whole backlog is ranked.

Parent: [Vision-Linked Stabilization Roadmap](stabilization-roadmap.md).

## Coverage and evidence

The complete metadata inventory observed on **2026-10-28 at 06:27 UTC** contained
932 open issues: 680 eligible for content research and 252 excluded,
metadata-only issues. This first wave covers 200 of the 680 eligible issues;
480 eligible issues remain pending, including 62 newly routed inflow issues.
The excluded 252 still count toward the fixed whole-queue target of 729.
These are snapshot counts, not a claim about the current live queue.

A subsequent complete **08:37 UTC** metadata-only census observed **933 open:
681 creator-eligible and 252 excluded**. Two issues arrived and one outside
this wave closed; neither arrival is ranked here and the unrelated closure
earns no campaign credit. This leaves **481 creator-eligible issues outside
the first 200**, subject to their own complete content-eligibility gates.
All 200 selected records passed a separate fresh author/editor/edit-metadata
check without drift before publication.

| Research track | Accounted | Researched proposals | Provisional | Per-issue evidence and work slices |
|----------------|-----------|----------------------|-------------|-----------------------------------|
| Bridge, MCP and continuity | 50 | 50 | 0 | [Bridge appendix](roadmap-wave-1-bridge.md) |
| Lifecycle, installation and release | 50 | 32 | 18 | [Lifecycle appendix](roadmap-wave-1-lifecycle.md) |
| Transport, Dispatch and indexing | 50 | 39 | 11 | [Transport appendix](roadmap-wave-1-transport.md) |
| Worktrees, guidance and qualification | 50 | 48 | 2 | [Worktree appendix](roadmap-wave-1-worktree.md) |
| **Total** | **200** | **169** | **31** | All assigned identities retained |

The appendices connect each issue's whole outcome to current vision, existing
effort or an explicit home gap, priority rationale, bounded work and validation.
They are navigation and triage records, not replacement acceptance contracts:
the linked original issues, their refinements and existing effort owners retain
all inherited requirements, alternatives, custody and holds. A provisional row
has not earned implementation readiness merely by receiving a priority.

Intent and effort research used source snapshot
[`307584006a70`](https://github.com/ThomasMichon/copilot-extensions/tree/307584006a70b5ca2de0ca82297fa71fc072f9b0).
Sixteen selected implementation traces were then checked against
[`d77783f0a10b`](https://github.com/ThomasMichon/copilot-extensions/tree/d77783f0a10b067a249dd39cee09f48f9dc6024b).
Their exact code/test excerpts, containing blobs and before/after issue metadata
were independently checked. This is static source evidence: no reproduction,
product test, installed-runtime probe or native experiment was executed.
Intervening source changes require a delta check before execution.

After correcting #870 to P2, the 200 proposals comprise **101 P1, 91 P2 and
8 P3**, with no established P0. Those numbers describe triage, not confirmed
bugs or incident frequency. In particular, 169 researched proposals are not
169 demonstrated implementation defects.

## Concrete fixes versus already-delivered foundations

The sixteen current-source traces support twelve defects, two qualification-only
cases, one already-fixed predicate and one evidence/design gap. Eleven of the
twelve defects are P1 candidates below; #870 is P2. Confidence in a source
counterexample does not establish runtime incidence or whole-issue completion.

| Issue | Recommended next contribution | Existing work home and retained boundary |
|-------|-------------------------------|-----------------------------------------|
| #5842 | **P1 source fix:** keep source responsibility and the finalize hold nonterminal until consumer claims and resource/lease changes durably commit; inject failures at each boundary. Reversing two calls is insufficient. | [Authoritative Worktrees daemon](../agent-worktrees-authoritative-daemon/README.md); existing handoff, readiness, remote and producer-specific owners remain |
| #5904 | **P1 source fix:** distinguish absent endpoint state from present-but-unverifiable state inside the existing mutation transaction; retain the latter across canonical and consuming copies. | [Versioned Singleton Manager](../versioned-singleton-manager/README.md); preserve the delivered publication lock and valid-owner cleanup |
| #2861 | **P1 source fix:** refuse a differing authored file at an expected hoister destination before replacement; qualify later disable and genuine generated refresh/cleanup. | Existing #2861 tracker and hoister owner; no verified whole-outcome effort home, namespace migration or new ownership framework is assumed |
| #5006 | **P1 source fix:** extend the existing birth-bound signal operation for the actual force semantics and qualify the ordinary/unbound Bridge false-group fallback. | [Versioned Singleton Manager](../versioned-singleton-manager/README.md); shared keeper and independent native consumers remain broader than this call site |
| #5512 | **P1 source fix:** preserve incumbent/new-owner metadata on identity or termination uncertainty and retire only the captured publication through the existing protocol. | [Status-monitor singleton correctness](../status-monitor-singleton-correctness/README.md), Phase 1; the existing OS singleton lease is credited, not reported missing |
| #4619 | **P1 source fix/design:** make final pickup admission atomic with abort/consume at both file and task authorities; qualify the gap after the second validity read. | [Context Handoff Overhaul](../context-handoff-overhaul/README.md); existing token, Dispatch and Worktrees authorities remain separate owners |
| #4202 | **P1 source fix/design:** reconcile a committed claim with a lost response using persisted exact attempt/session/generation evidence; otherwise report uncertainty without replay or rebind. | [Dispatch Task Lifecycle and Conversations](../agent-dispatch-monitor-and-confirmed-state/README.md); same worker is not same consuming session |
| #3002 | **P1 source fix/design:** qualify STARTED/null-session steering and provide a supported exact-owner route after authorized binding, or the original actionable undeliverable/not-ready alternative. | [Dispatch Task Lifecycle and Conversations](../agent-dispatch-monitor-and-confirmed-state/README.md); preserve immutable acceptance and cold-suspended/acquired-mode distinctions |
| #1803 | **P1 source fix:** choose a coherent bounded async framing limit and prove a valid response strictly greater than 65,536 bytes; distinguish post-admission failure from safe cold fallback. | Existing #1803 tracker and MCP framing owner; no verified whole-outcome effort home or protocol redesign is assumed |
| #2793 | **P1 source fix:** preserve or explicitly refuse parent-before-subcommand options before the real registration spec is persisted; retain true defaults and supported placements. | [Dispatch Fleet Reliability and Recovery](../dispatch-fleet-reliability-recovery/README.md); the durable registrar is credited, and child-only machine/env options are not falsely called erased |
| #4220 | **P1 source fix:** first qualify the completed-family A/B/C alias-revert counterexample, then add a family watermark or explicit repair/refusal rule using existing authority. | [Index Standalone Service](../agent-index-standalone-service/README.md); coordinate with [Incremental Indexing Delta Pipeline](../incremental-indexing-delta-pipeline/README.md), without transferring either owner's scope |
| #870 | **P2 hardening:** normalize ordinary early port/spawn failures into truthful failed results while preserving the predecessor and any uncertain partial-launch custody. | [Runtime Operational Contracts](../review-pipeline-operational-contracts/README.md); the original describes benign, fail-safe early failure, not a demonstrated outage |
| #5754 | **P1 qualification, not a duplicate lock fix:** exercise actual Bridge recovery and the accepted periodic Dispatch backstop against a live transition. Both already use guarded helpers. | [Bridge Retained Runtime Restoration](../bridge-retained-runtime-restoration/README.md); preserve real consumer A/B concurrency and native proof or justified exemptions |
| #5356 | **P1 qualification, not a duplicate completion guard:** qualify the normal Windows update front door against matching completed, conflicting completed and genuinely owned incomplete work. | [Vendored Installer Engine](../vendored-installer-engine/README.md); current guard already encloses venv and package mutation; retain GOOD and separate historical stall/exit uncertainty |
| #5791 | **P1 evidence/qualification:** reconcile delivered source with containing review/release/selected-installation evidence. Do not schedule another missing-token dispatcher patch. | [Account-Aware Operations](../account-aware-operations/README.md); explicit credential failure already raises before subprocess launch; broader inherited acceptance remains |
| #3444 | **P1 evidence/design:** bind the finite selected source/payload/three-wheel/installed/actually-serving coordinator and supervisor matrix, retaining named historical uncertainties. | [Immutable Dev Slot](../immutable-dev-slot/README.md) with existing installer/publication owners; current source/wheel/import foundations are credited, not replaced by a blanket rewrite |

Detailed consumer paths, immutable source/test links, credited work and missing
witnesses are in the four appendices. The recommendations extend existing
operations; none creates a second lifecycle controller, registry or custody
authority. P1 qualification can be important without an unproved current bug.

## Proposed execution and qualification sequence

This sequence is **agent-recommended**. It is a partial order over finite
contributions, not a new umbrella implementation effort or repository-wide
merge queue. The [scoped dependency register](roadmap-wave-1-dependencies.md)
records the 33 proposed edges and their limits.

| Work lane | Entry and first useful work | Exit before claiming the corresponding outcome |
|-----------|-----------------------------|------------------------------------------------|
| Admission and owner alignment | Existing owner confirms the precise residual, selected source delta, original alternatives and a finite reviewed change/qualification plan. Resolve provisional policy or home gaps before conflicting effects. | Named work slice and validation owner; no transfer inferred from this roadmap, empty assignees or file authorship |
| Durable work and authority protection | Independently start the #5842, #5904, #2861 and #5006 regression-first slices where their own owners/gates permit. | Failure/refusal/crash controls preserve responsibility and protected bytes; successful exact-owner controls still work. Native process proof stays separately authorized. |
| Recovery and intended delivery | Independently start #5512, #4619, #4202, #3002, #1803 and #2793 at their existing consumer seams. Apply only their actual interface/identity prerequisites. | Truthful refusal and indeterminate outcomes, no blind replay, exact consumer delivery or supported alternative, and preserved normal-path behavior |
| Data and checkpoint consistency | Start #4220's completed-family alias test using existing source identities, journal admission and source-domain exclusion. | The A/B/C revert cannot leave B-only stale data; affected acquisition/publication/GC and crash recovery obey one selected authority |
| Credit and qualify delivered work | In parallel with source review, prepare #5754/#5356/#5791/#3444 evidence packets through their existing owners. No production execution is authorized here. | Exact original consumer/platform/selected-mode evidence, or explicit retained tracked uncertainty; a helper test, import or healthy different daemon is not acceptance |
| Bounded hardening and expansion | Keep #870 at P2; select other P2/P3 work from the per-issue appendices after comparing its actual benefit and dependencies with later waves. | Its own finite regression and release/adoption obligations; no invented requirement to finish every P1 before independent work |

For each approved source contribution, the normal repository review and required
checks precede merge. A containing release precedes a claim of released support;
an authorized selected installation and its actual serving/consumer evidence
precede an adoption claim. These are evidence stages, not permission for this
campaign to implement, update or run native trials.

No whole-harness readiness verdict follows from completing these lanes. The
remaining 481 creator-eligible issues and 31 provisional rows can reveal higher-priority
work or additional scoped prerequisites. Preserve literal golden-path judgments,
fresh-substrate requirements and existing held native fixtures rather than
substituting improvised success.

## Remaining research and publication gates

- [ ] Integrate later disjoint eligible waves and newly changed/inflow records;
  retain excluded issues as counted metadata-only gaps.
- [ ] Resolve the 31 provisional outcomes through their actual policy, evidence
  and ownership homes; do not convert uncertainty into a new blanket gate.
- [ ] Refresh changed issue/source/custody facts before downstream execution or
  any requirement transfer; this snapshot creates no closure credit.
- [x] Review this first-wave plan through the normal PR flow in #9382 and
  reconcile the parent effort without marking the full roadmap or numerical
  gate Done.
- [ ] Complete the full roadmap, whole-queue threshold and operator-guidance
  steps in the [parent effort](README.md).
