# Weighted Backlog Themes and Completed-Work Candidates

Parent: [Backlog Reconciliation](README.md).
Method: [weighted semantic consolidation](semantic-density-consolidation.md).

## Result and coverage

The first measured graph covers the **1,021-issue source snapshot**:
720 qualified issue descriptions and all 3,197 original/discussion texts,
301 metadata-only nodes, and one counted-but-nonclustering coordination
issue. The 719 analyzed issues produce **6,600 weighted alignment edges,
22 broad neighborhoods and 50 stable cores containing 285 issues**.
There are also 64 pair candidates covering 128 issues and 306 retained
ungrouped issues. Nothing is silently dropped to make the groups look tidy.

The later complete census at **2026-11-04 07:51:52 UTC / 2026-11-03
23:51:52 PST** counts **1,023 whole open**, 294 above the fixed 729
checkpoint: 751 creator-eligible, 272 excluded, 686 original-cohort and
337 inflow. Four new arrivals remain metadata-only; #10364 departed
externally and #10203 was independently verified and closed by this campaign.
The numerical increase is not hidden by reporting only the closure.

The export retains two historical departures plus four later metadata-only
arrivals: **1,025 recorded identities, not 1,025 currently open issues**.
Description admission completed at 07:21:41 UTC. The current projection
updates presence, not discussion contents or previously unprocessed arrivals.

| Data | Contents |
|---|---|
| [Weighted issue graph](graph/weighted-issues.json) | Public issue IDs, snapshot/current-presence roles, sparse undirected weighted edges and all 22 neighborhood memberships |
| [Stable cores](graph/stable-cores.json) | All 50 cores, 64 pairs and 306 ungrouped nodes, with original and still-open membership |
| [Completed-work retrieval](graph/completed-work-candidates.json) | Three candidates per qualified issue, evidence tiers and the separately verified #10203 disposition |

These are research artifacts, not a second issue tracker. Raw issue contents,
author/editor rosters, credentials, private paths and embedding vectors are
not republished.

## What changed after measuring the first pass

Raw whole-document centroids favored large umbrella descriptions: several
appeared in hundreds of unrelated nearest-neighbor lists. Raw density also
ranked isolated pairs above useful effort-sized groups. That baseline is
retained as rejected research, not presented as a successful consolidation.

The calibrated graph separates title, whole-original and whole-discussion
representations, removes the corpus-wide common direction, then uses weighted
reciprocal neighbors and deterministic modularity local movement.
Eighteen neighbor/threshold/resolution combinations test membership sensitivity.
Ranking considers internal weighted mass, separation and membership stability;
density remains visible rather than becoming an automatic closure score.

Stable cores require **every pair** to co-occur in at least 12 of 18 runs.
Complete-link grouping prevents a connected A-B-C path from implying an
accepted A-B-C family. This is stability over the tested parameters, not a
probability of duplicate requirements or proof of a suitable receiver.

## Effort-sized routing, not another leaf publication queue

The following are agent-recommended research/intake routes. Existing effort
identity/status was checked; complete receiving fit and source-specific
acceptance are **not** claimed from a graph, title, list or link.

| Stable core | Issues | Existing home candidates | First useful decision and retained boundary |
|---|---|---|---|
| group-001: fleet topology, routing and enrollment | 25 | [Linux Role Fleet](../linux-role-fleet/README.md), [Routing Foundation](../machine-fleet-routing-foundation/README.md), [Gateway/SSH Onboarding](../gateway-ssh-fleet-onboarding/README.md) | Split role placement, route/credential transport and enrollment into their existing owners; Vault design and #5940's authority gap do not become fleet acceptance. |
| group-002: Picker/provider presentation | 17 | [Manager](../worktree-manager-control-plane/README.md), [Tasks UX](../agent-dispatch-tasks-pane-ux-overhaul/README.md) | Separate shared presentation/provider observation from task lifecycle and native Confirm delivery; retain credited extraction and existing phase owners. |
| group-003: runtime entry, construction and helpers | 15 historical; 14 admitted | Existing entry, construction, helper and bootstrap owners; complete receiver fit unqualified | [Four-family contract study](runtime-entry-cohort.md); #810 excluded, #10061 separately completed, thirteen contracts retain conditions and incomplete home/history comparison. Held ROOT material is withheld. |
| group-004: PR/review workflow | 14 | [PR Lifecycle](../pr-lifecycle-conformance/README.md), [Durable PR Watch](../durable-pr-watch-delegation/README.md) | Keep reviewer mechanics, workflow composition and contributor conformance distinct. Preserve #5183/#5211's recorded parked-home resumption gate; this route does not resume or receive that work. |
| group-005: Bridge observation, discovery and continuity | 11 | [Producer Work Reuse](../producer-work-reuse/README.md) for #7948 plan coverage only; other home references unqualified | [Five-family contract study](bridge-observation-cohort.md); preserve original ORs, typed outcomes, actual owners and native gaps. Partial held-home material is withheld; three merged-description relations address different contracts. |
| group-006: native fixture/cache disposal | 11 | [Test Portfolio](../test-portfolio-rationalization/README.md), original supported-uninstall owners | Start with the nine fixture/runner cases listed below. #9375/#10239 retain their separate uninstall-caller authority; no deletion, reaping, deadline relaxation or new native run follows. |
| group-007: mux/client lifetime and input | 9 historical; 8 admitted | Existing Manager, restart, source, hosting and client owners; whole receiver fit unqualified | [Eight-contract study](mux-lifecycle-cohort.md); #8669 withheld before new rendering, 21 message-only comparisons and three withheld edges. Native/human/installed residuals and actual reservations remain. |
| group-008: continuity and lifecycle | 8 historical; 7 admitted | Existing succession, native transport, lifecycle-state, work-custody, compatibility and receipt owners; whole receivers unqualified | [Seven-contract study](continuity-lifecycle-cohort.md); #7694 withheld before new rendering, eighteen message comparisons and three withheld PR relations. Separately credit #8026's newer merged source without erasing its actual remaining gates. |
| group-009: session/task observation consumers | 8 historical; 7 admitted | Existing Bridge/Dispatch/interface and actual source/resource owners; whole effort receivers unqualified | [Seven-contract study](session-task-observation-cohort.md); #6462 withheld before new rendering, twenty bounded comparisons and one withheld relation. The finite report-design documentation proposal needs reviewed delivery and a separate judgment. |
| group-010: measurement, preservation and exact completion | 8 historical; 7 admitted | Existing Logger, Index, metadata-repair and producer-discovery owners; whole current receivers unqualified | [Seven-contract study](measurement-preservation-cohort.md); inherited-held member excluded, seventeen bounded comparisons and four withheld relations. Two whole functions confirm only #6026's retained per-call type pattern; repair, native, installed and handoff residuals remain. |
| group-011: common venue/runtime adapters | 7 historical; 5 admitted | Existing venue, assigned protocol and restricted runtime/custody owners; whole current receivers unqualified | [Five-contract study](venue-runtime-cohort.md); two inherited-held members excluded, fourteen bounded completed-work comparisons and one withheld description. Specific wrapper mapping conditions and all incumbent claims remain. |
| group-013: interpreter and construction inputs | 7 | [Vendored Installer](../vendored-installer-engine/README.md), [Feed Construction](../feed-construction-release-gate/README.md) | Separate interpreter selection, native argument handling and source materialization. Preserve every original platform/feed/source and existing #7028 boundary. |
| group-014: native CI timeout evidence | 7 | [Test Portfolio](../test-portfolio-rationalization/README.md), existing release/fixture owners | Match actual failed boundaries and unchanged budgets before repair or duplicate judgments; neither an isolated retry nor broad CI success proves the finite cause. |
| group-015: session intelligence and accounting | 6 historical; 5 admitted | Existing Session Intelligence and Large-Corpus Sync owners; five whole receiving documents read without a source/resource transfer | [Five-contract study](session-intelligence-cohort.md); eighteen whole texts, fifteen bounded comparisons and eleven complete source functions. CLOSED historical issue stays metadata-only, not an invented reference STOP. Two same-owner phase-tracking proposals have unchecked direct retention, pending reviewed delivery and separate judgments. |
| group-018: attention and history readers | 5 | [Operator Attention](../operator-attention-contract/README.md), actual Bridge reader owners | Keep wait/deadline, history paging and event coverage separate; an attention home does not automatically receive the complete history contract. |
| group-021: head succession | 4 | [Head Succession](../worktree-head-succession-hardening/README.md) | The home remains Draft; #84's OPEN canonical and #912's actual claim conditions survive. No automatic transfer or session operation. |

### Session-intelligence core

#4976, #5665, #5677, #5678 and #8735.

The [five-contract study](session-intelligence-cohort.md) reads eighteen whole
original/discussion texts. The historical sixth member is CLOSED metadata-only,
without new text, reopening or campaign credit. Six whole messages supply nine
relations; five new whole actual merged descriptions and one explicitly reused
earlier description supply six more. All scores and prior invalidations remain.

Preserve all seven sync follow-ups, complete portable intelligence/accounting,
claimed catalog/daily/adoption phases and bounded installed scheduled accounting.
Historical primitives, source-seam release and actual owner retentions are not
whole programme completion or live-cutover permission. Five whole current
receiving documents supply actual same-owner phase mapping; eleven complete
functions credit selected-directory pruning and preserve tenant/signature/
incremental-batching residuals without performance or test acceptance.
A merely closed member's issue-body exclusion is not a new operator reference
hold. Both tracking candidates have unchecked direct canonical retention,
pending reviewed delivery and separate judgments. All five sources remain OPEN without new native or
source/resource effects.

### Measurement/preservation core

#4186, #5302, #5676, #6026, #6407, #6819 and #7950.

The [seven-contract study](measurement-preservation-cohort.md) integrates
23 whole original/discussion texts from a completed exclusive reader packet.
The inherited-held member stays withheld before rendering, without linked
source or subset retry. Thirteen whole messages supply fourteen comparisons;
two actual merged descriptions supply three more. Four protected relations
remain unrendered. Retrieval scores and all prior invalidations survive.

Separate preservation/governed completion, retrospective analytics/index
integrity, safe metadata/file provenance and bounded producer discovery.
These are typed research interfaces, not duplicate status or hard sequence.
Two complete fixed-source functions still contain per-call Win32 type
definitions; no memory-growth, native, whole-module or fix acceptance follows.
All seven originals remain OPEN with literal handoff, selected-repair,
runtime/installed and whole-workload proof boundaries intact.

### Venue/runtime-adapter core

#954, #1266, #3985, #4227 and #4229.

The [five-contract study](venue-runtime-cohort.md) reads 35 whole original/
discussion texts after correcting transitive held-reference admission.
Historical #7237/#9056 remain withheld; the incomplete reader return is not
completed-contract credit and no linked-source/subset retry follows.
Six immutable whole messages supply twelve comparisons, and two actual merged
descriptions supply two more; one protected description remains unrendered.

Separate the complete venue programme, standard AHP host/migration and
restricted portable runtime/credential custody. Retain all directly inherited
finite clauses and both wrappers' original specific mapping conditions.
Configuration, transport and native-send foundations are not whole completion.
No whole current receiving home, implementation appointment, source closure,
native execution or source/resource transfer is qualified.

### Continuity/lifecycle core

#3000, #3367, #5664, #5683, #6028, #7942 and #9742.

The [seven-contract study](continuity-lifecycle-cohort.md) reads 24 whole
original/discussion texts and preserves every direct inherited clause,
alternative, existing claim and finite source/native distinction. Protected
#7694 remains withheld before rendering. Eight whole commit messages support
eighteen bounded comparisons; the three #10266 relations stay withheld.

#7942's literal #8026 candidate has now actually merged, and matching current
registration/module/test definitions support bounded static source credit.
Accepted factor-two evidence, historical failures and actual SDK/nominal/
dispatcher residuals remain distinct. #9742's 729-body-line count is advisory,
not decomposition proof or a block on another scoped change. No whole effort
receiver, implementation appointment, closure or custody transfer follows.

### Session/task-observation core

#3555, #3556, #3557, #4049, #4119, #4418 and #5257.

The [seven-contract study](session-task-observation-cohort.md) integrates
21 whole original/discussion texts, one separately qualified complete merged
description and thirteen whole commit messages. Protected #6462 and one
commit relation remain withheld before rendering. Twenty bounded comparisons
do not establish whole completion.

Keep CLI/API parity, complete lifecycle/current-reservation observation,
finite rendered-report design, selected-target probe, two launch axes,
genuine pending-question answering and all five worker-status affordances
distinct. The whole current playbook supports a documentation-only proposal
for #3557's explicit OR: cheap standalone usage and opt-in expensive history.
The corrected eight-file proposal has now actually merged in #10485 with
genuine exact-head approval, the earlier finding resolved and all required
checks green. All merged/current blobs match the reviewed head. Separate
fresh source/discussion qualification and whole playbook readback complete
#3557's finite documented-separation alternative; six sources stay OPEN.
No implementation appointment, runtime observation, represented-history
capability, installed acceptance or source/resource transfer follows.

### Mux lifecycle core

#2974, #4102, #4305, #4564, #7073, #7962, #8797 and #9939.

The [eight-contract study](mux-lifecycle-cohort.md) integrates 29 whole
original/discussion texts. Ninth historical member #8669 is withheld before
new rendering because of protected lineage, with no neighboring-source retry.
Separate ordinary exit/extension inquiry, observation/manual restore/reserved
automation, key defaults, mouse-wheel support, inherited policy, exact pane
diagnostics, native interaction and Windows standby cwd.

Twelve complete commit messages supply 21 bounded comparisons; one protected
target's three edges remain withheld. No computed PR relation or independently
verified source completion exists for this core. One complete Mux Companion
vision supports scoped-default reconciliation only; no whole current effort
receiver/history is qualified. Preserve every reservation and explicit
human/native/installed residual. All eight sources stay OPEN without intake,
appointment, execution, rollout or source/resource transfer.

### Bridge-observation core

#4035, #5531, #5532, #5616, #5761, #5969, #7948, #7978, #8451,
#8774 and #8775.

The [eleven-contract study](bridge-observation-cohort.md) integrates 34 whole
texts and three independently qualified complete merged descriptions. Saved
work, event observation, idle-stop handling, reconcile diagnosis, knowledge
overlays, discovery truth, producer cost, immutable caching, live correlation
and requested preferences remain distinct. All eleven sources stay OPEN.

The complete current Producer Work Reuse ROOT/S1/validation retains #7948's
plan but leaves its implementation, exact slice admission, native cost/
membership, release and installed acceptance unresolved. Other homes remain
unqualified; all partial related-ROOT material is withheld after a history
hold, with no neighboring-source retry or retroactive admission.
The three #5532/#9620, #5616/#10293 and #7948/#10119 relations retain their
original scores and explicit different-contract outcomes. Commit-message
candidates remain unresearched and earlier invalid targets remain invalid.
No receiving transfer, new appointment, closure or rollout follows.

### Runtime-entry core

#4032, #4036, #4163, #4547, #5479, #5611, #5703, #7387, #8384,
#8410, #9281, #9732, #10061 and #10071.

The [fourteen-contract study](runtime-entry-cohort.md) integrates 55 whole
original/discussion texts and four qualified merged descriptions. The graph's
historical #810 member was already closed and excluded. Incomplete documentary
home/history comparisons and held-ROOT-derived material supply no receiving
acceptance. Preserve supported entry/caller identity, construction/staging,
helper/status and bootstrap/uninstall as four distinct families.

Ten PR relations receive bounded comparisons; the eleventh description
reference is refused before rendering, with original retrieval scores retained
as historical only. Commit-message candidates remain unresearched and previous
invalidated targets remain invalid. Separate exact merged/current source,
original-contract, review and CI verification supports #10061's finite completed
guard-reader correction. Thirteen other contracts retain their own conditions.
No new implementation, native/installed proof, appointment, rollout or source/
resource transfer follows; whole-queue counts remain separately measured.

### PR-workflow core

#1734, #2847, #2887, #2908, #3582, #5352, #6818, #7051, #9233 and #9809.

The [whole ten-contract study](pr-workflow-cohort.md) reads 26 unchanged
metadata-qualified texts and current existing-home contracts. Changed #9535
is withheld; the parked-home #5183/#5211 pair and departed #10364 issue text
are excluded. No parked ROOT is researched or amended.

Keep reviewer discovery/lineage, configured review-flow consumption, provider
conformance, read-only wait planning, effort-record truth, publication cadence,
historical mining, installed-host conformance, model-guided selection and
Bridge guidance distinct. Eight qualified merged descriptions supply bounded
attention-guidance, callable-source or planning credit. Fourteen corresponding
graph edges retain their scores with explicit partial/different-contract
comparisons, not newly verified whole completion. A complete finite #6818
direct intake is the receiving candidate; canonical OPEN, actual assignment,
Draft and independent source conditions remain.

### Fleet core

#951, #2680, #3442, #4309, #5767, #5789, #5861, #5877, #5940,
#6030, #6236, #6283, #6383, #6424, #6443, #6617, #6681, #6804,
#7122, #7192, #7206, #7257, #8551, #8729 and #9493.

The [whole 25-contract study](fleet-core-cohort.md) reads 240 original/
discussion texts after complete metadata qualification, including a separate
fresh admission of changed #6424/#6681 rather than reuse of their old bodies.
Existing current ROOTs and necessary contracts separate actual direct scope
from residual gaps, navigation and source-specific owner conditions.

The existing Linux role hierarchy can receive #5861's complete finite generic
named-operation matrix through ordinary reviewed direct intake. That is a
receiving candidate, not an already accepted transfer. Other canonical,
claimed, independent or Draft sources retain their conditions. Current
completed-work comparison credits #9620's external-driver foundation without
calling it capability-to-host selection or actual FleetSpawner task placement;
the two graph edges retain their scores with explicit partial-match outcomes.
No new closure, implementation, native proof or resource transfer is claimed.

### Picker/provider cohort

#542, #1193, #1239, #2801, #3657, #4117, #4118, #4175, #4182,
#4183, #4225, #4274, #5555, #5898, #6832, #7939 and #8490.

This is the first shared-presentation routing cohort, not seventeen duplicates.
Backend/source/native and #2453 Confirm custody remain separate. Existing
Manager and Tasks plans retain their own acceptance and first gates.

The [complete seventeen-contract study](picker-provider-cohort.md) synthesizes
all 79 original/discussion texts into four routed families with typed
dependencies and explicit residuals. It finds no demonstrated whole-contract
duplicate or newly verified completion. Relevant existing-home fit is
distinguished from complete current receiving acceptance; assigned-source,
claimed integration and native/installed conditions remain.

### Session-data cohort

#4976, #5665, #5677, #5678, #8682 and #8735.

The source snapshot's mean pair coassignment is 0.94. That makes this a strong
research group, not proof that #4976 has an accepted home or that #5678's
independently claimed daily/adoption work can be absorbed. Preserve #5676's
separate final-OPEN handoff condition even though it falls in another core.

### Native fixture/runner cohort

#7243, #7643, #8521, #9436, #9460, #9467, #9614, #9699 and #10225.

These nine cases are the fixture/runner portion of group-006. The other
two members, #9375 and #10239, remain with their actual supported-uninstall
callers and source custodians. Shared cache/process vocabulary is not proof
of one defect, a common root cause or permission to clean anything up.

The [complete nine-contract study](native-fixture-cohort.md) reads 21 whole
texts and separates interpreter/profile fixtures, post-exit disposal,
guarded-launch preconditions and production ownership. Current completed-work
comparison independently verifies #9699's finite merged fixture correction;
the other eight retain their individual conditions. The study also corrects
one qualified-reference admission error: seven `pr:10236` match edges are
explicitly invalid, not completion evidence. Historical target counts and
scores are not silently presented as freshly requalified coverage.

## Completed-work matching

The bounded pilot examines 300 recently updated merged PR metadata records,
admits **190 PR descriptions**, and combines them with **370 distinct commit
messages** after held-reference filtering and merge-commit deduplication:
**560 targets compared with 720 qualified issues**. This is not exhaustive
repository history. Incomplete/held-linked descriptions are retained as gaps,
not inferred from an index miss or silently counted as reviewed.

All candidates use one prose-model space. Title/outcome centering and
cross-domain local scaling correct the same broad-umbrella bias; uncentered
baselines remain available as research evidence. Scores are not completion
probabilities. Documentation-only targets and message-only evidence are
explicitly labeled.

Examples nominated for independent verification include #6937/#6941,
#10291/#10293, #10060/#10065, #10105/#10110, #10078/#10086,
#10061/#10070 and #10079/#10100. A matching merged PR is not automatically
whole-issue completion, release, installed acceptance or owner release.

**Further independently verified results:** the
[finite caller/probe judgments](<journal/2026/11.04 finite-caller-and-probe-completions.md>)
support completed-source closures for #10079/#10100 and #10092/#10119 after
whole originals/refinements, actual merged/current code, focused regressions,
review resolutions and complete CI checks. The final #10079 verdict remains
accurately COMMENTED with its actual carried-over finding fixed; #10092 has
genuine exact-head approval. No new tests, live authentication, native latency,
installed behavior or custody release is claimed. #10060/#10065 remains
unqualified and OPEN. The finite #10092 fix does not complete #7948's distinct
producer-scheduling relation to the same PR.

**Verified result:** #10203 is closed as completed after checking its complete
original and reservation-release comment, actual #10210 merged diff, current
comparison caller, positive and three refusal regression definitions, current-head
approval and provider-required CI. The code accepts repeated references only
when both index URI and captured credential identity match, preserving all
capture evidence. The [public disposition](https://github.com/ThomasMichon/copilot-extensions/issues/10203#issuecomment-6625554570)
does not claim installation/rollout or resolve separate #10188 policy work.

## Continue from the graph

Work the coherent cohorts and high-value completed-work candidates, refreshing
only changed metadata/text and source evidence. Turn accepted groups into actual
requirement-preserving receiving packets; do not resume serial unrelated leaf
amendments or treat an issue list as accepted transfer. Keep broad unstable
neighborhoods, independent tokens, Draft/parked homes, new inflow and every
source/resource obligation visible. The parent remains Active.
