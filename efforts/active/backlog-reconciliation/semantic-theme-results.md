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
| group-004: PR/review workflow | 14 | [PR Lifecycle](../pr-lifecycle-conformance/README.md), [Durable PR Watch](../durable-pr-watch-delegation/README.md) | Keep reviewer mechanics, workflow composition and contributor conformance distinct. Preserve #5183/#5211's recorded parked-home resumption gate; this route does not resume or receive that work. |
| group-006: native fixture/cache disposal | 11 | [Test Portfolio](../test-portfolio-rationalization/README.md), original supported-uninstall owners | Start with the nine fixture/runner cases listed below. #9375/#10239 retain their separate uninstall-caller authority; no deletion, reaping, deadline relaxation or new native run follows. |
| group-007: mux/client lifetime and input | 9 | [Manager](../worktree-manager-control-plane/README.md), [Installation-Independent Sessions](../installation-independent-sessions/README.md) | Preserve current restart, source, hosting and client owners; native terminal, input and retained-session gates are not historical relocation credit. |
| group-009: session/task observation consumers | 8 | [Task Lifecycle](../agent-dispatch-monitor-and-confirmed-state/README.md), [Worker Observation](../worker-status-observability-hooks/README.md) | Distinguish CLI/API parity, observation, steering and actual session effects. Worker Observation is Draft, not an accepted implementation receiver. |
| group-011: common venue/runtime adapters | 7 | [Venue Parity](../venue-parity/README.md), [Venue Staging](../venue-plugin-staging-parity/README.md), [Remote ACP Ownership](../remote-acp-host-ownership/README.md) | Keep adapters thin without transferring lifecycle, transport, credential or hosted-session custody by theme. |
| group-013: interpreter and construction inputs | 7 | [Vendored Installer](../vendored-installer-engine/README.md), [Feed Construction](../feed-construction-release-gate/README.md) | Separate interpreter selection, native argument handling and source materialization. Preserve every original platform/feed/source and existing #7028 boundary. |
| group-014: native CI timeout evidence | 7 | [Test Portfolio](../test-portfolio-rationalization/README.md), existing release/fixture owners | Match actual failed boundaries and unchanged budgets before repair or duplicate judgments; neither an isolated retry nor broad CI success proves the finite cause. |
| group-015: session intelligence and accounting | 6 | [Session Intelligence](../session-intelligence-and-accounting/README.md), [Large-Corpus Sync](../large-corpus-session-sync/README.md) | Preserve the canonical backend, independently claimed daily adoption and #4976's remaining-home question. Do not equate related data paths with completed accounting or retirement. |
| group-018: attention and history readers | 5 | [Operator Attention](../operator-attention-contract/README.md), actual Bridge reader owners | Keep wait/deadline, history paging and event coverage separate; an attention home does not automatically receive the complete history contract. |
| group-021: head succession | 4 | [Head Succession](../worktree-head-succession-hardening/README.md) | The home remains Draft; #84's OPEN canonical and #912's actual claim conditions survive. No automatic transfer or session operation. |

### Picker/provider cohort

#542, #1193, #1239, #2801, #3657, #4117, #4118, #4175, #4182,
#4183, #4225, #4274, #5555, #5898, #6832, #7939 and #8490.

This is the first shared-presentation routing cohort, not seventeen duplicates.
Backend/source/native and #2453 Confirm custody remain separate. Existing
Manager and Tasks plans retain their own acceptance and first gates.

### Session-data cohort

#4976, #5665, #5677, #5678, #8682 and #8735.

The source snapshot's mean pair coassignment is 0.94. That makes this a strong
research group, not proof that #4976 has an accepted home or that #5678's
independently claimed daily/adoption work can be absorbed. Preserve #5676's
separate final-OPEN handoff condition even though it falls in another core.

The [whole-contract adjudication](receiving-session-data-cohort.md) separates
backend/catalog, daily/adoption and installed-sync lanes. It proposes complete
direct #4976 receiving clauses and #5678's active-call boundary while retaining
the independently claimed tokens. #8682 is separately verified completed by
#8688; that finite closure is not completion of #8735 or either whole effort.

### Native fixture/runner cohort

#7243, #7643, #8521, #9436, #9460, #9467, #9614, #9699 and #10225.

These nine cases are the fixture/runner portion of group-006. The other
two members, #9375 and #10239, remain with their actual supported-uninstall
callers and source custodians. Shared cache/process vocabulary is not proof
of one defect, a common root cause or permission to clean anything up.

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
