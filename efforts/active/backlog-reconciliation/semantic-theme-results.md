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
| group-007: mux/client lifetime and input | 9 | [Manager](../worktree-manager-control-plane/README.md), [Installation-Independent Sessions](../installation-independent-sessions/README.md) | Preserve current restart, source, hosting and client owners; native terminal, input and retained-session gates are not historical relocation credit. |
| group-009: session/task observation consumers | 8 | [Task Lifecycle](../agent-dispatch-monitor-and-confirmed-state/README.md), [Worker Observation](../worker-status-observability-hooks/README.md) | Distinguish CLI/API parity, observation, steering and actual session effects. Worker Observation is Draft, not an accepted implementation receiver. |
| group-011: common venue/runtime adapters | 7 | [Venue Parity](../venue-parity/README.md), [Venue Staging](../venue-plugin-staging-parity/README.md), [Remote ACP Ownership](../remote-acp-host-ownership/README.md) | Keep adapters thin without transferring lifecycle, transport, credential or hosted-session custody by theme. |
| group-013: interpreter and construction inputs | 7 | [Vendored Installer](../vendored-installer-engine/README.md), [Feed Construction](../feed-construction-release-gate/README.md) | Separate interpreter selection, native argument handling and source materialization. Preserve every original platform/feed/source and existing #7028 boundary. |
| group-014: native CI timeout evidence | 7 | [Test Portfolio](../test-portfolio-rationalization/README.md), existing release/fixture owners | Match actual failed boundaries and unchanged budgets before repair or duplicate judgments; neither an isolated retry nor broad CI success proves the finite cause. |
| group-015: session intelligence and accounting | 6 | [Session Intelligence](../session-intelligence-and-accounting/README.md), [Large-Corpus Sync](../large-corpus-session-sync/README.md) | Preserve the canonical backend, independently claimed daily adoption and #4976's remaining-home question. Do not equate related data paths with completed accounting or retirement. |
| group-018: attention and history readers | 5 | [Operator Attention](../operator-attention-contract/README.md), actual Bridge reader owners | Keep wait/deadline, history paging and event coverage separate; an attention home does not automatically receive the complete history contract. |
| group-021: head succession | 4 | [Head Succession](../worktree-head-succession-hardening/README.md) | The home remains Draft; #84's OPEN canonical and #912's actual claim conditions survive. No automatic transfer or session operation. |

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
