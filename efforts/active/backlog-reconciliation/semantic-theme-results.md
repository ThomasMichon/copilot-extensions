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
| group-013: interpreter and construction inputs | 7 historical; 6 admitted; 1 tracking wrapper superseded | Existing installer, constructor, updater and helper owners; whole Vendored Installer bundle read, feed bundle withheld | [Six-contract study](interpreter-construction-cohort.md); seventeen bounded comparisons and two withheld descriptions. Reviewed direct three-path intake and separate current readback support #5955's administrative tracking fold, not implementation; five sources remain OPEN. |
| group-014: native CI timeout evidence | 7 admitted; 1 finite source completed; 5 tracking folds | [Test Portfolio](../test-portfolio-rationalization/README.md), existing release/fixture owners | [Seven-contract study](native-timeout-cohort.md); actual #10516 delivery and separate current judgments support five administrative supersessions, not causal repairs. #6662 remains OPEN; all 36 receiver rows, original limits, unknown causes and custody remain unchecked/unchanged. |
| group-015: session intelligence and accounting | 6 historical; 5 admitted | Existing Session Intelligence and Large-Corpus Sync owners; five whole receiving documents read without a source/resource transfer | [Five-contract study](session-intelligence-cohort.md); eighteen whole texts, fifteen bounded comparisons and eleven complete source functions. CLOSED historical issue stays metadata-only, not an invented reference STOP. Two same-owner phase-tracking proposals have unchecked direct retention, pending reviewed delivery and separate judgments. |
| group-018: attention and history readers | 5 admitted | Existing Bridge wait/history/event owners; eight whole home/prior-art documents distinguish scope, not a direct receiver | [Five-contract study](attention-history-cohort.md); seven whole texts, fifteen message comparisons and two current CLI functions. Inbox, archived baseline, AHP migration, truthful transport state and retrospective CLI review do not automatically accept the newer individual contracts; all five remain OPEN. |
| group-019: native supervisor reconciliation | 5 admitted; 2 tracking folds | Actual OPEN #6137 BODY, existing supervisor/native/rollout owners | [Five-contract study](supervisor-reconciliation-cohort.md); twenty-one whole texts and fifteen bounded noncompletion relations. Two complete result/readiness families now have ten unchecked direct keeper rows; historical uncertainty and #7711/#9096 custody remain OPEN. |
| group-020: native qualification and dependency caches | 5 admitted; 1 finite source completed | Existing test/fixture/generator owners; Test Portfolio is a candidate, not an accepted receiver | [Five-contract study](qualification-cache-cohort.md); nine whole texts and fifteen noncompletion comparisons. Independent exact source history verifies #9472's expectation repair; four sources remain OPEN with native, cache, interpreter and tamper boundaries intact. |
| group-021: head succession | 4 admitted | Existing ground/head/claim owners; two whole current Draft homes are related, not accepted receivers | [Four-contract study](head-succession-cohort.md); sixteen whole texts, seven bounded noncompletion comparisons and five protected description relations withheld before rendering. Explicit KEEP, non-expiring claim, ground-versus-allocator liveness and acknowledgement/stability remain distinct; all four sources stay OPEN. |
| group-022: installation scope and directory evidence | 4 historical; 2 admitted | Assigned parked coexistence canonical and separate finite parser/consumer owner; no receiving amendment | [Two-contract study](installation-scope-cohort.md); thirteen whole texts, five noncompletion comparisons, two whole original members and one description withheld. Preserve complete inherited acceptance, explicit resumption and actual native history-consumer boundaries; both admitted sources stay OPEN. |
| group-023: standalone declarations and release test inputs | 4 admitted | Existing Index/Dispatch distribution custodians; two whole Active homes retain their original acceptance | [Four-contract study](standalone-declaration-cohort.md); thirteen whole texts, eleven bounded noncompletion comparisons and one known held message relation excluded before retry. Exact incumbent mapping, independent versions, bare bootstrap/offline decisions and released-layout input defects remain distinct; all four sources stay OPEN. |
| group-024: lifetime and reclaim controls | 4 admitted | Existing OPEN terminal/session and deployment canonicals; separate atomic-reclaim owner | [Four-contract study](lifetime-control-cohort.md); twelve whole texts, ten bounded noncompletion comparisons and two protected descriptions withheld. Exact terminal/dormancy, both destructive races, post-interrupt prompt and full deployment acceptance remain distinct; all four sources stay OPEN. |
| group-025: finalization and context recovery | 4 admitted | Existing rightful-continuation, spawn/CWD, reserved recovery and finalization custodians | [Four-contract study](finalization-recovery-cohort.md); fourteen whole texts and twelve noncompletion comparisons. Retain the already completed historical donor fold, exact owner acceptance and atomic OR resumable removal; all four sources stay OPEN. |
| group-026: repository authority and entry points | 4 historical; 3 admitted | Existing target-tracking, repository-model and native-entry owners; no receiving amendment | [Three-contract study](repository-authority-cohort.md); ten whole texts and nine noncompletion comparisons. Closed historical member stays metadata-only; paired target authority, five inherited model families and actual reattachment remain OPEN. |
| group-027: daemon scheduling and retirement | 4 admitted | Existing monitor, mux-update, idle-rotation and stranded-generation owners; no receiving amendment | [Four-contract study](daemon-scheduling-cohort.md); eighteen whole texts, ten noncompletion comparisons and two known protected relations excluded. Preserve incremental work, production-witness fidelity, exact drain and non-expiring claims; all four sources remain OPEN. |
| group-028: engine adoption and selectors | 4 admitted | Existing engine/discovery, paused adopter, scenario and independent issue-selector owners | [Four-contract study](engine-adoption-cohort.md); ten whole texts, ten noncompletion comparisons and two whole descriptions withheld. Historical endpoint intake and source conversion retain credit without replay; paused release/service and actual witness gates remain. |
| group-029: profiles and native controls | 4 admitted | Existing user-profile/settings, ACP permission and native update-control owners; no receiving amendment | [Four-contract study](native-controls-cohort.md); seven whole texts and twelve noncompletion comparisons. Native-control implementation description provides a finite source question, not complete acceptance; all four distinct contracts remain OPEN. |
| group-030: native timeout causality | 4 admitted | Existing finite signature owners and accepted exclusive physical-home repair custodian; no new retry | [Four-contract study](timeout-causality-cohort.md); twenty-nine whole texts and twelve noncompletion comparisons. Preserve all original deadline layers, causal evidence and correction/retry alternatives; all four sources stay OPEN. |
| group-031: knowledge-pair lifecycle | 3 admitted | Existing retrofit/composition, synthesized routing and responsibility/convergence owners | [Three-contract study](knowledge-pair-cohort.md); thirteen whole texts and nine noncompletion relations. Retain carve-OR-adopt, both Boolean conflicts, actual aligned writes and every removal-policy alternative; no cleanup or extra acceptance gate. |
| group-032: hosted lifecycle and custody | 3 admitted | Existing FINAL OPEN onboarding, finite pool/recycler and non-expiring resource-claim custodians | [Three-contract study](hosted-custody-cohort.md); nine whole texts and nine noncompletion comparisons. Whole inherited 81/22-row bodies and direct-readback, credential alternatives, budget and actual destructive-fence obligations remain OPEN; no resource effects. |
| group-033: componentization and worker workbench | 3 admitted | Existing componentization subject-matter, autonomous campaign and installed Configuration/Workers acceptance owners | [Three-contract study](componentization-workbench-cohort.md); nine whole texts and nine noncompletion comparisons. Preserve all installer/module/recurrence alternatives, actual campaign judgments and unread transferred activation-truth scope; all originals remain OPEN. |
| group-034: Dispatch observability and capacity | 3 admitted | Existing measurement, two nonlocal waiter-display consumers and pool-accounting custodians | [Three-contract study](dispatch-observability-cohort.md); seven whole texts and nine message comparisons. Separate attributable rates/distributions, exact wait display and ownership-isolated capacity; finite current relay reading retains #5452 OPEN without execution. |
| group-035: knowledge onboarding | 3 admitted | Existing model-startup, registration/provider/landing and finite setup-guidance owners | [Three-contract study](knowledge-onboarding-cohort.md); nine whole texts and nine noncompletion relations. Preserve actual create-OR-bind/decline admission, nonmutating provider-policy truth and confirmed conventional defaults without inventing a universal native gate. |
| group-036: self-update convergence and work truth | 3 admitted | Existing declarative-resource, finite guidance/actor and same-occurrence native-causality owners | [Three-contract study](self-update-observation-cohort.md); nine whole texts and eight noncompletion relations, with one protected candidate withheld before render. Preserve both validation platforms and documentation-OR-sweep acceptance; no scheduler operation. |
| group-037: policy-compatible CI and publication | 2 admitted; 1 withheld | Existing portfolio/release reservation and stronger trusted-CI publication-policy owners | [Two-contract study](publication-policy-cohort.md); four whole texts and six noncompletion comparisons. Original #3336 stayed withheld before rendering. Finite current source mechanisms are credited, not complete managed-publication or owner acceptance. |
| group-038: authorized Vault boundaries | 3 admitted | Existing unlock, routing, provider and vault custodians; no whole successor established | [Three-contract study](vault-boundaries-cohort.md); nine whole texts and nine noncompletion comparisons. Keep headless unlock alternatives, dynamic authorized fan-out and endpoint-plus-owner-context isolation separate from activation source completion. |
| group-039: discovery, warm-up and retained launch | 3 admitted | Existing task/worktree/session identity and original New/Resume owners; no receiving amendment | [Three-contract study](startup-reuse-cohort.md); 46 whole texts, eight message comparisons and one protected description withheld. Preserve cross-key ACTIVE/RECENT discovery, all warm-up tracks, original stopped companion and scoped publication/timing/role holds; all originals remain OPEN. |
| group-040: intended-account execution and preflight | 3 admitted; 1 finite source completed | Existing ordinary/pinned push, intended-account matrix and finite preflight owners | [Three-contract study](account-preflight-cohort.md); ten whole texts and nine relations. Exact merged/current source, delayed-Git regression, complete checks and original-author merge qualify #10291 only; #4199/#5791 retain all inherited acceptance and remain OPEN. |
| group-041: finite release-check guidance | 1 admitted; 2 withheld | Existing release-check-skill owner; no pipeline or installation assignment | [One-contract study](release-check-guidance-cohort.md); one whole 449-character original requests a new harness skill, not a contributing-section amendment. Three actual MERGED descriptions and related rollout guidance do not establish that pinned-Main effective-content/installation distinction deliverable; original remains OPEN. |
| group-042: hosted staging, bootstrap and preparation reuse | 3 admitted | Existing transport, capability matrix, exact bootstrap and independent cache/inventory/compact-home owners | [Three-contract study](hosted-staging-cohort.md); eighteen whole texts and nine noncompletion relations. Changed #8242 was freshly read; full inherited matrix and parked-ROOT boundaries remain, not a new provisioning or native lane. |
| group-043: CI selector, budget and causal diagnosis | 3 admitted | Existing standalone selector, retired-policy/budget and exclusive PowerShell diagnosis owners | [Three-contract study](ci-causality-cohort.md); fourteen whole texts and nine noncompletion relations. Keep whole invocation, budget-fix OR current-policy replacement and actual cause distinct; no CI retrigger or repair-custody transfer. |
| group-044: installer liveness and copied-bootstrap closure | 3 admitted | Existing live-slot, conditional lock-observation and source/native-consumer owners | [Three-contract study](installer-liveness-cohort.md); sixteen whole texts and six message comparisons, with three protected relations withheld before render. Preserve both copied dependencies, original Windows consumer and conditional recovery alternatives; all originals remain OPEN. |
| group-045: authoritative cutover and retained RESTORE | 2 admitted; 1 withheld | Existing cutover adapters, captured-context/native-retirement and retained assembly owners | [Two-contract study](cutover-restore-cohort.md); fifteen whole texts and five noncompletion comparisons, with one protected description withheld. Preserve inherited ordinary-Windows routing, optional protocol scope and whole assembly/controller/route convergence. |
| group-046: exact conversation and allocation recovery | 2 admitted; 1 withheld | Existing fresh-conversation native owner and same retained allocation-claim custodian; no researcher adoption | [Two-contract study](exact-recovery-cohort.md); nine whole texts and six noncompletion comparisons. Credit the owner's authorized allocation recovery while retaining SAME-CLAIM disposition; conversation release/activation/native acceptance remains separate. |
| group-047: bounded landing and cold-consumer portfolios | 3 admitted | Existing full landing/file owner and selected first-use/consumer/retention/removal portfolio owners | [Three-contract study](bounded-portfolio-cohort.md); five whole texts and nine noncompletion comparisons. Preserve all selected cases and unchanged caps; aggregate exhaustion and NOT RUN removal do not become passes. |
| group-048: Windows required-runtime and inherited update | 3 admitted | Existing attribution, required ARM64 MCP, Manager, wrapper/hook and original update owners | [Three-contract study](windows-update-cohort.md); 41 whole texts, including fresh changed #7305, and four message noncompletion relations; five protected relations withheld. Source/release/Manager results do not establish complete normal update/readiness or headed-CLI acceptance. |
| group-049: incremental pipeline and consumer integration | 1 admitted; 2 withheld | Existing incremental-pipeline claim and source/consumer/release custodians | [One-contract study](incremental-pipeline-cohort.md); 35 whole texts and three message noncompletion comparisons. Preserve original planning versus later scoped implementation, bounded count-matrix and actual consumer/serving gates. |
| group-050: existing-queue phase and long-slot selection | 2 admitted; 1 withheld | Existing update queue/native plan and exact current-slot bridge owners | [Two-contract study](queue-selection-cohort.md); two whole originals and four noncompletion comparisons, with two protected relations withheld. Preserve default behavior, phase isolation, bounded long-selector reads/path safety and no fallback. |
| group-051: SSH mesh and absolute localhost policy | 2 admitted | Existing transport/provider, six inherited finite-family, trust and bind-policy owners | [Two-contract study](ssh-transport-boundaries-cohort.md); nine whole texts and four message noncompletion comparisons; two protected-description relations withheld. Preserve exact consumer contexts, alternatives, custody/readback and strict original bind-policy reconciliation. |
| group-052: provider account and read-only prerequisite | 2 admitted | Existing account/provider/credential abstraction and finite package-workflow prerequisite owners | [Two-contract study](provider-account-boundaries-cohort.md); six whole texts, including the complete final account refinement, and six noncompletion relations. Credit delivered Phase 1; retain Phase 2 and read-only no-switch prerequisite without credential-operation authority. |
| group-053: Windows task activation and child-start diagnostics | 2 admitted; 1 withheld | Existing intended-task/native repair and reliability/SessionHost/process-custody owners | [Two-contract study](startup-activation-diagnostics-cohort.md); six whole texts and five noncompletion relations, with one protected description withheld. Credit bounded retries/fallback; retain actual native task and authoritative bounded redacted child-output acceptance. |
| group-054: exact baton resolution and foreground contention | 2 admitted | Existing assigned resolver and original foreground/save-store retest owners | [Two-contract study](handoff-resolution-contention-cohort.md); eight whole texts and four message noncompletion comparisons; two protected-message relations withheld. Preserve routing alternatives, cause-preserving errors and the original loaded-workload confirmation before closure. |
| group-055: terminal allocation and general claim reclamation | 2 admitted | Existing Dispatch terminal-allocation, general ground-layer and complete terminal-custodian owners | [Two-contract study](terminal-reclamation-boundaries-cohort.md); nine whole texts and six noncompletion relations. Credit interface-neutral source delivery without running adoption; retain nine general rows, complete acceptance and all shared/historical/semantic duties. |
| group-056: reusable hygiene and finite scrub disposition | 2 admitted | Existing consumer-inventory engine, separate metadata validator and privacy/disposition custodians | [Two-contract study](hygiene-disposition-cohort.md); six whole texts and six noncompletion relations. Credit merged scrub/exact document correction; retain reusable input/report/apply semantics, configured whole-tree evidence and disposition without inventing a rewrite gate. |
| group-057: governed build and warning prerequisites | 2 admitted | Existing governed-feed/build-isolation custodian and original warning-retirement owner | [Two-contract study](build-warning-prerequisites-cohort.md); seven whole texts and six noncompletion relations. Recover missing command/scope and incomplete custody; credit warning removal without replacing the original broad-enough prerequisite with a new rollout campaign. |
| group-058: mode-qualified hosting and independent activation | 2 admitted | Existing hosting-mode/lifecycle and strict effective-config owners | [Two-contract study](hosting-config-activation-cohort.md); six whole texts and six noncompletion relations. Preserve accepted native/standalone modes, scoped withdrawal/shared custody and independent config refusal without obsolete exclusivity or new activation authority. |
| group-059: persistent raw SSH and non-takeover admission | 2 admitted | Existing operator-policy/transport owners and retained resolver-repair author | [Two-contract study](raw-ssh-admission-cohort.md); six whole texts and five noncompletion comparisons, with one protected whole-description relation withheld. Durable policy is not one-off raw SSH; blocked preparation is not executed tests or release of the author's repair. |
| group-060: foreign writes and missing/windowless mux incidents | 2 admitted | Existing final-OPEN investigation keeper, dirty/stash custodians and incident/narrow-watchdog owners | [Two-contract study](foreign-worktree-mux-incident-cohort.md); six whole texts and four noncompletion relations, with two protected-message relations withheld. Preserve attribution, directly inherited custody and independent finite watchdog proof without imposing whole-incident native gates on it. |
| group-061: optional teardown and post-FINAL runtime WIP | 2 admitted | Existing executor-design, runtime-classification and bounded documentation owners | [Two-contract study](teardown-finalized-wip-cohort.md); ten whole texts and six noncompletion relations. Separate executors remain a valid design outcome; all six inherited runtime rows and cleanup paths survive unrelated native/guard/planning credit. |
| group-062: merge reconciliation and actual-policy readiness | 2 admitted | Existing exact task/claim evaluator and current-policy status/watch owners | [Two-contract study](merge-reconciliation-readiness-cohort.md); eight whole texts and five noncompletion relations, with one excluded whole publication unit unread. Preserve attributable provider merge/evaluation/fencing and reporting correction OR note, not unconditional release or added gates. |
| group-063: confirmed-gone goals and queued cleanup | 1 admitted; 1 withheld | Existing task/goal evaluator, ownership-class recovery and cleanup custodians | [Two-case study](goal-cleanup-reconciliation-cohort.md); four whole texts and three noncompletion comparisons. Credit concurrent tri-state recovery; retain actual goal acceptance and separate prolonged pending-cleanup diagnosis without age-based death or housekeeping replay. |
| group-064: exact-worker progress and self-emitted readiness | 2 admitted | Existing native-nudge and independent service-telemetry owners | [Two-contract study](progress-readiness-telemetry-cohort.md); five whole texts and five noncompletion comparisons, with one protected message withheld. Preserve quiet-live versus legitimate waits and ongoing serving/sink semantics without numerical or operational authority invention. |
| group-065: assigned obligation and independent adopter closeout | 2 admitted | Existing assigned documentation closeout owner and independent OPEN/Active adopter umbrella | [Two-contract study](obligation-adopter-closeout-cohort.md); nine whole texts and six noncompletion relations. Delivered vision reconciliation retains only assigned closeout; released primary docs do not finish previews or exhaustive plugin-owner audit. |
| group-066: exact steering readiness and optional abandon guards | 2 admitted | Existing delivery/owner-binding custodian and additive CLI/API guard owner | [Two-contract study](steering-abandon-guards-cohort.md); four whole texts and six noncompletion relations. Preserve three delivery/readiness alternatives and both optional guards; static consumer reads confirm the finite missing forwarding without task-operation authority. |
| group-067: drain cleanup tails and four startup signatures | 2 admitted | Existing incident/release, attempt/terminal cleanup and independent startup/infrastructure owners | [Two-contract study](reservation-container-startup-cohort.md); seven whole texts and six noncompletion relations. Credit pool/driver recovery without completing terminal tails or all incident causes, and keep investigation proportionate to current paths. |
| group-068: whole anchor/capture and scoped Git observation | 2 admitted | Existing real-capture, Windows/POSIX observation, R1b/roster and narrow advisory-slice custodians | [Two-contract study](anchor-observation-boundaries-cohort.md); twelve whole texts, including freshly changed #7944, and six noncompletion relations. Preserve three whole-operation alternatives, six capture rows and current non-overlapping claims without broader acceptance. |
| group-069: current-root discovery and reserved-anchor custody | 2 admitted | Existing head-succession Phase 2, narrow explicit native-custody claimant and independent registration contributor | [Two-contract study](root-anchor-custody-cohort.md); eight whole texts and six noncompletion comparisons preserve six root rows, no-write anchor v1, linear lineage/handoffs and explicit resource settlement without takeover. |
| group-070: independent palette configuration and clean-room CI | 2 admitted | Existing UI/config/presentation and periodic-integration/test-portfolio owners | [Two-contract study](theme-cleanroom-boundaries-cohort.md); six whole texts and six noncompletion relations preserve a supported theme seam and chosen four-outcome scenario/job coverage. Pairing is not duplication or universal native gating. |
| group-071: owner-scoped runtime cleanup and force-advice wording | 2 admitted | Existing artifact/retention/logger/token owners and independent wording-policy responsibility | [Two-contract study](runtime-cleanup-force-guidance-cohort.md); six whole texts and six noncompletion relations preserve all nine cleanup clauses, owner-loop OR consumer-schedule alternatives and whole removal OR narrower accepted wording. |
| group-072: owner-safe retirement and distinct lazy-readiness margin | 2 admitted | Existing process-slot/retirement and independent readiness investigation owners | [Two-contract study](retirement-readiness-boundaries-cohort.md); nine whole texts and six noncompletion relations credit delivered leak/family repair and actual primary rollout while retaining whole census ownership and the separate historical deadline discrepancy. |
| group-073: six-phase native messaging and combined session-tool operations | 2 admitted | Existing native-session, Bridge/transport/event and session-tool/ACP owners | [Two-contract study](native-messaging-session-tools-cohort.md); six whole texts and six noncompletion relations retain inherited direct acceptance, actual consumption/stream/result distinctions and definition/tool isolation without custody transfer. |
| group-074: operation-account preflight and distinct authorization journey | 2 admitted | Existing repository-account/SSH/credential owners and CTA operator/adopter owners | [Two-contract study](account-preflight-auth-journey-cohort.md); six whole texts and six noncompletion relations preserve all six proposed intake rows and actual operation gating, while crediting bounded source completion without closing ceremony/store/focus/adoption residuals. |
| group-075: query trust domains and independent indexing lifecycle | 2 admitted | Existing source-authorization/query-policy and standalone/canary/controller owners with separate consumer scopes | [Two-contract study](index-domain-standalone-cohort.md); thirteen whole texts and six noncompletion relations credit real grants, completed planning and staged distribution without replacing ordinary-query compatibility or native lifecycle/adopter acceptance. |
| group-076: narrow product prose and authorized dependency-closed delivery | 2 admitted | Existing documentation and selected-payload/staging/registration coordinators | [Two-contract study](casing-local-marketplace-cohort.md); five whole texts and six relations preserve human-only casing and all six local-closure outcomes; one whole description stays protected and partial native loading is not whole acceptance. |
| group-077: resident demand, safe cutover and independent polling | 1 admitted; 1 wholly withheld | Existing resident/routing owners, separate held roster/render and architecture scopes | [One-contract study](resident-demand-singleflight-cohort.md); four whole texts and three noncompletion relations preserve all four outcomes. Changed related original failed whole protected-reference preflight before rendering; no old-text or neighbor retry. |
| group-078: existing requested branches and receiving Git membership | 2 admitted | Existing generic resume and explicitly claimed checkout-membership contributors | [Two-contract study](branch-membership-boundaries-cohort.md); five whole texts and six noncompletion relations preserve fetched-head OR tracking, local-work safety and receiving registration rather than creation-root locality. |
| group-079: per-unit supervision and caller-independent native lifetime | 2 admitted | Existing supervised-lane/fleet lifecycle and explicitly claimed Linux launch-boundary owners | [Two-contract study](supervision-native-lifetime-cohort.md); four whole texts and six relations retain the complete child/registration contract, matching Linux source credit and separate migration. One known description stays protected; the exact Linux description separately failed whole preflight. |
| group-080: healthy replacement and stale-slot session recovery | 2 admitted | Existing Bridge create/reconcile/watchdog and Session-Host lifecycle custodians | [Two-contract study](bridge-replacement-recovery-cohort.md); three whole texts and six noncompletion relations preserve actual causal uncertainty, current-pointer/activation, schema alternatives and host survival without shared live-fire or whole incident acceptance. |
| group-081: monitor restart exclusivity and exit-aware transfer | 1 admitted; 1 wholly withheld | Existing singleton-correctness sole owner and accepted sequential phase handoff | [One-contract study](monitor-restart-exclusivity-cohort.md); two whole texts and three noncompletion comparisons credit truthful spawn-failure reporting without settling all three restart hazards or failure/reader/platform duties. No protected-neighbor retry. |
| group-082: caller-owned async transport settlement | 2 admitted | Existing Bridge cache/Session Host and CodeSpaces preparation/launch/cancellation custodians | [Two-contract study](async-transport-cleanup-cohort.md); five whole texts and six noncompletion comparisons distinguish resource lifetime from verified process/consumer success while preserving durable Owner/worker survival. |
| group-083: explicit integration and truthful payload provenance | 2 admitted | Existing installer/integration scopes and explicitly claimed light-review-only Git-provenance repair | [Two-contract study](install-integration-provenance-cohort.md); three whole texts and six noncompletion comparisons preserve HOME-route counterevidence, source integration reachability, Git error alternatives and the actual TLS-blocked isolated-install result. |
| group-084: unknown ownership and confirmed-dead route observation | 2 admitted; 1 finite completed | Existing endpoint-file cleanup and retained repair/source owners | [Two-contract study](ownership-observation-dead-route-cohort.md); six whole texts and six nonmatching ranked messages. Independent actual #9284 source/review/CI/promotion verification completes #9283; #5904's present-unknown cleanup gap remains. |
| group-085: independent cleanup and audited terminal recovery | 2 admitted | Existing transport/API/registry and selected cleanup-recovery/adoption owners | [Two-contract study](cleanup-recovery-evidence-cohort.md); six whole texts, four message comparisons and two unchanged invalid-scope relations preserve error/handle fidelity, actual #6542 credit and unknown native Git-wait cause. |
| group-086: deploy applicability and mixed-version launch | 2 admitted | Existing architecture decision and bounded launch-compatibility scopes | [Two-contract study](deploy-architecture-launch-compatibility-cohort.md); five whole texts and four message comparisons retain finite full-flow/narrow-engine adjudication and actual parser/second-hop negotiation. Two protected descriptions remain excluded. |
| group-087: shared machine identity and consumer designations | 2 admitted | Existing reserved Worktrees/Picker and Index routing owners | [Two-contract study](machine-identity-routing-cohort.md); eight whole texts and six noncompletion comparisons preserve actual adapter gaps, literal Index override, optional topology and landed prerequisite credit. |
| group-088: instruction source/delivery compatibility | 2 admitted | Actual assigned delivery and separate finite reader-repair scopes | [Two-contract study](instruction-source-delivery-cohort.md); fifteen whole texts and six message comparisons retain the adopted direct-copy/no-attestation contract, actual scoped releases and distinct finite source versus broader consumer/rollout gates. |
| group-089: Windows transaction completion denials | 2 admitted | Existing Logger rescue/publication and retained recovery custodians | [Two-contract study](windows-transaction-denial-cohort.md); four whole texts and five noncompletion comparisons preserve undiagnosed native causes, conditional evidence and strict same-provider success. One entire protected message stays excluded. |
| group-090: task transfer and acknowledged execution | 2 admitted | Existing Dispatch transfer/narrowing and assigned execution/effect owners | [Two-contract study](task-transfer-execution-handoff-cohort.md); fourteen whole texts, six message comparisons and complete canonical handoff effort preserve two unclaimed-transfer/narrowing designs separately from already-authorized execution/effect integration. |
| group-091: artifact publication follow-up | 1 admitted; 1 entire changed source withheld | Existing hardening driver and primary publisher custodians | [Finite study](artifact-boundary-followup-cohort.md); two whole texts, three message comparisons and complete Blocked follow-up retain actual hosted asset proof. Protected #7929 supplies no pairwise judgment or old-text substitute. |
| group-092: Bridge target and parent metadata | 2 admitted | Existing Bridge send/registration and creator-metadata scopes | [Two-contract study](bridge-target-parent-metadata-cohort.md); three whole texts, four messages and one whole description preserve clean refusal/start alternatives and caller-asserted live/worktree parentage. One whole protected message remains excluded. |
| group-093: protected original pair | 2 entire sources withheld | Existing owners unchanged | Both complete originals/discussions are protected before model rendering. No old/subset text, comparison, receiving judgment or completion is substituted. |
| group-094: watchdog exit and retained-history work | 2 admitted | Existing installer-session and claimed tombstone-fix owners | [Two-contract study](exit-observation-history-work-cohort.md); three whole texts and six message relations retain prompt root-exit cleanup before captured EOF separately from bounded repeated-history selection. |
| group-095: Windows registration/census encoding | 2 admitted; 1 finite completed | Existing registration-test, CodeSpace census and consumer custodians | [Two-contract study](windows-census-registration-encoding-cohort.md); three whole texts and six nonmatching ranked comparisons. Independent actual #9330 merged/current source, native report and final review/CI complete #9327; #7171 stays OPEN without a new broad native gate. |
| group-096: canonical Chat and hosted-task continuity | 2 admitted | Existing Chat proposal/plugin/component owners and retained native Dispatch repair | [Two-contract study](chat-graduation-hosted-continuity-cohort.md); 37 whole texts and six ranked comparisons retain graduation and exact-conversation wake duties. Separate actual merged repair descriptions credit source slices, not whole current acceptance. |
| group-097: SSH daemon root and finite forward | 2 admitted | Exact root-repair operational custodian and original finite-forward source owner | [Two-contract study](ssh-root-finite-forward-cohort.md); thirteen whole texts and six relations preserve the explicit rollout hold and actual merged leaf with Active owner closeout. One whole protected description stays excluded. |
| group-098: installer configuration/policy copies | 1 admitted; 1 whole source withheld | Existing canonical adapter and four plugin delivery owners | [Finite study](installer-policy-copy-sync-cohort.md); two whole texts and three different-contract messages retain all eight source copies. Current byte equality is not complete publication/CI proof; protected paired text is not retried. |
| group-099: protected original pair | 2 entire sources withheld | Existing owners unchanged | Both complete originals/discussions inherit the protected group-100 member before model rendering. No old/subset text, pairwise judgment or completion is substituted. |
| group-100: protected original pair | 2 entire sources withheld | Existing owners unchanged | Whole-reference preflight retains the existing protected lineage and newly propagated current-entry boundary. No original, linked-source or subset retry follows. |
| group-101: protected original pair | 2 entire sources withheld | Existing owners unchanged | Both complete originals/discussions are protected before model rendering. Metadata qualification is not text admission, a receiving judgment or closure credit. |
| group-102: protected configuration context | 1 entire source withheld; 1 prior campaign completion metadata-only | Existing owners unchanged | Whole-reference preflight withholds the remaining original through its protected current parent. The already-completed historical member supplies no repeated body comparison, new closure credit or claim release. |
| group-103: protected current configuration pair | 2 entire sources withheld | Existing owners unchanged | One original is already protected; changed metadata on the other prevents stale-text reuse, and fresh whole-current qualification also protects it before rendering. No subset or linked-source retry follows. |
| group-104: optional bootstrap configuration tuple | 1 admitted; 1 entire source withheld | Existing narrow bootstrap continuation reservation and installed-setup acceptance owner | [Finite study](bootstrap-configuration-tuple-cohort.md); three whole texts and three noncompletion comparisons credit implementation/release without substituting them for the original installed one-bootstrap/zero-scalar comparison. |
| group-105: named provider failures and check-series evidence | 2 admitted | Existing provider diagnostic and required-policy owners | [Two-contract study](provider-check-evidence-cohort.md); two whole originals and six ranked relations distinguish named Gitea evidence from identified check-series supersession. One whole protected description stays excluded; observed success never grants merge permission. |
| group-106: sparse cold-fixture prerequisites | 2 admitted | Existing native fixture and runner-lease control-plane custodians | [Two-contract study](cold-fixture-control-plane-cohort.md); four whole texts and six ranked relations retain the product/test-interpreter distinction, reported Linux progress, shared follow-through and exact bounded Windows lookup. A separate description-admission failure is excluded without source retry. |
| group-107: protected current repair pair | 2 entire sources withheld | Existing owners unchanged | Both complete originals/discussions inherit existing protection before model rendering. No old/subset text, pairwise judgment, source retry or receiving transfer follows. |
| group-108: native image and Compose compatibility | 1 retained OPEN; 1 independently completed | Existing native substrate and adopter owners | [Two-contract study](native-komodo-compatibility-cohort.md); current source/regressions and actual review/CI support the finite Compose correction. Optional-image installed acceptance remains separate; protected candidate stays unread. |
| group-109: Bridge launch and watchdog identity | 2 retained OPEN | Existing Bridge process and adoption custodians | [Two-contract study](bridge-process-identity-cohort.md); actual source credit distinguishes console argv from Windows admitted-helper PID. Native definitions/reports do not qualify protected whole-owner closeout. |
| group-110: repository hosted selector delivery | 2 retained OPEN | Existing repository projection and hosted-delivery owners | [Two-contract study](hosted-selector-refresh-cohort.md); credited catch-all/roster repairs retain the later direct-read renderer and genuine hosted-review refinement. Protected candidate stays excluded without retry. |
| group-111: publication snapshots and contributor recovery | 2 retained OPEN | Existing snapshot/recovery and individual PR custodians | [Two-contract study](publication-snapshot-recovery-cohort.md); six comparisons retain explicit historical-ref disposition and exact per-PR confirmation. All seven recovery PRs remain metadata-observed OPEN; promotion and proposals are not individual outcomes. |
| group-112: prepared-map native qualification | 2 retained OPEN | Existing startup/cutover, platform and runtime owners | [Two-contract study](guard-map-native-qualification-cohort.md); six comparisons credit source/consumer and reused finite reader delivery without substituting for large-caller startup, actual UNC or complete POSIX native entry/deployment. |
| group-113: configured default-index caller policy | 1 retained OPEN; historical member already completed | Existing caller/profile and preserved draft custodians | [One-contract study](configured-feed-policy-cohort.md); three comparisons distinguish duplicate-reference completion and redirect qualification from both caller outcomes. Released reservation preserves OPEN Draft #10202 and source/resource custody; no repeat #10203 credit. |
| groups-115/116: local transport and materialized names | 2 retained OPEN | Existing transport canonical and finite materializer owner | [Two-contract study](local-transport-materialization-cohort.md); six ranked relations retain native-first OR justified dynamic fallback and id/stem/override naming, with protected candidates excluded. |
| groups-118/123: finite prose and state settling | 2 retained OPEN | Existing narrow prose/test owners; whole current portfolio receiver protected | [Two-contract study](finite-prose-state-cohort.md); six message comparisons retain the three-module residual and actual conditional-state wait without an invented whole-parent/native gate. |
| groups-119/124: clean-room qualification and registry hygiene | 2 retained OPEN | Existing native/provider and complete registry canonical custodians | [Two-contract study](qualification-registry-cohort.md); six relations preserve all inherited acceptance, one protected description and one existing invalid-scope relation. |
| groups-120/121/125: diagnostics, pipe admission and Host proof | 3 retained OPEN | Existing diagnostic, Windows pipe and layered read-only Host owners | [Three-contract study](diagnostic-pipe-host-cohort.md); nine relations distinguish bounded retained failures, actual pre-secret admission and non-displacing observation; protected messages and reused finite proof stay distinct. |
| groups-122/126: roster performance and recognized retirement | 2 retained OPEN | Existing roster/native and held updater/source-snapshot custodians | [Two-contract study](roster-retirement-cohort.md); six relations preserve measured claim closeout, exact retirement OR quarantine and whole intent-only cleanup limits. |
| groups-127/129/131/137: attributable invocation | 4 retained OPEN | Existing metadata, finite runtime-definition, client-routing and phased cell-launch owners | [Four-contract study](attributable-invocation-cohort.md); twelve ranked relations retain full metadata coverage, finite definition credit, actual CWD-client intent and cell-owned PATH. A protected message stays unread. |
| groups-128/134: first-use and IPC framing consumers | 2 retained OPEN | Existing exact first-use and bounded framing owners | [Two-contract study](first-use-framing-cohort.md); six relations and current consumer evidence distinguish the remaining CodeSpaces race, Containers reaper improvement, SSH delegation and unchanged exact async IPC proof. |
| groups-132/138: literal venue and performance qualification | 2 retained OPEN | Existing full-user Windows venue and combined CPU/p95 canonical owners | [Two-contract study](literal-venue-performance-cohort.md); six comparisons do not substitute unrelated SDK/container work for WinGet seed/restore or measured normal-cadence performance. |
| groups-133/136: generation and embodiment-attempt recovery | 2 retained OPEN | Existing independent recovery and assigned created-allocation custodians | [Two-contract study](generation-attempt-recovery-cohort.md); six whole-message comparisons retain generation/trust/containment, all seven inherited allocation rows and explicit historical holds. |
| group-130: changed and protected current original | 1 entire source withheld | Existing owner unchanged | Fresh independent admission follows changed metadata, but the complete current original inherits protection before rendering; no stale/partial/alternative read or receiving bypass follows. |
| group-135: protected whole original | 1 entire source withheld | Existing owner unchanged | Protection applies before model rendering; no subset, old-body or alternative-source retry, receiving transfer or closure credit follows. |
| group-139: session-artifact write boundary | 1 retained OPEN | Existing structural data-handling owner | [One-contract study](session-artifact-write-cohort.md); three comparisons preserve write-time block OR flag, legitimate small artifacts and the distinction from sync exclusion/cleanup. |
| groups-140/141: ordinary guidance and conformance | 2 retained OPEN | Existing complete delivery and static/dynamic canonicals | [Two-contract study](guidance-delivery-conformance-cohort.md); six comparisons retain every direct inherited family, actual native failure tail and independent generation/source custody. |
| groups-142/143/145: POSIX ordering, path bytes and descriptors | 3 retained OPEN | Existing completed comparator slice, finite path claimant and shared descriptor canonical | [Three-contract study](posix-ordering-path-descriptor-cohort.md); nine relations and whole #8847 credit substantial merged repair without substituting it for declared broader/native acceptance. |
| groups-144/147: interactive SSH and ordinary command budgets | 2 retained OPEN | Held caller-owned interactive architecture and separate noninteractive Git owner | [Two-contract study](codespace-interactive-command-budget-cohort.md); six relations preserve loopback/PTY custody and execution-budget alternatives without replay or deadline inflation. |
| group-146: shared activation convention | 1 retained OPEN | Existing family convention and bounded Vault adopter owners | [One-contract study](family-activation-convention-cohort.md); three comparisons preserve repo OR knowledge OR personal-target opt-in. The matching whole description remains protected after complete file pagination. |
| group-148: Phase 10 live state-machine acceptance | 1 retained OPEN | Existing live integration and explicit parked-execution custodians | [One-contract study](phase10-runtime-acceptance-cohort.md); three comparisons retain all carried-launch/application/design mappings and the maintainer-resumption hold. |

### Qualification/cache core

#7136, #9456, #9462, #9466 and #9472.

The [five-contract study](qualification-cache-cohort.md) preserves the complete
originals/refinements and fifteen bounded message/description comparisons.
None of those retrieved relations proves completion. Independent source
history instead identifies #9623's exact expected-manifest repair, preserved
in the whole current test function and actual production manifest. Complete
provider admission, exact-head gates and fresh source/readback checks support
only #9472's finite COMPLETED disposition. Four sources remain OPEN; no
native rerun, receiver acceptance, rollout or source/resource transfer follows.

### Native timeout and connection-lifetime core

#6662, #6663, #6690, #6900, #7820, #9442 and #9815.

The [seven-contract study](native-timeout-cohort.md) preserves fourteen whole
texts and distinguishes diagnostic progress, different native boundaries and
the assigned connection-lifetime repair. Ten whole messages and five whole
merged descriptions support sixteen bounded relations; two whole messages
remain withheld. Similarity does not prove a shared cause or completed repair.

The complete current portfolio ROOT has been read with unchanged whole-read
framework/intake/vision/history reuse and unchanged current #1303 custody
metadata. Six finite direct families are authored under its existing owning
waves, still pending review, actual complete delivery and separate current
source judgments. Required-unit CI, quiesced hosted Windows execution, original
deadlines and all source/resource holders remain. #7820 is separately verified
COMPLETED at its original source tier; no clean whole-PR verdict, broader #7805
acceptance or installed adoption is implied.

### Interpreter/construction core

#5955, #6888, #7313, #9489, #10060 and #10186.

The [six-contract study](interpreter-construction-cohort.md) reads eleven
whole original/refinement texts, nine whole messages and four actual MERGED
descriptions: seventeen bounded relations, two protected descriptions withheld.
One inherited-held historical member remains unrendered. All four complete
Vendored Installer documents support only a finite #5955 direct proposal;
the feed receiver stays wholly withheld. Preserve constructor/updater/helper
ownership, actual-child proof, urgent UV source credit, finite diagnostic
acceptance and public post-update inheritance as distinct unfinished work.
The subsequent reviewed #10510 delivery and separate current readback qualify
only #5955's administrative tracking supersession, recorded in the dated
journal. Its three acceptance rows stay unchecked; the other five sources
remain OPEN. No technical or native acceptance follows from that fold.

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

**Further verified finite result:** [#9283/#9284](ownership-observation-dead-route-cohort.md)
is closed completed after independently checking the whole original, exact merged
and current implementation, seven regression bodies, complete final review/CI
and actual promoted runtime equality. Its three ranked messages remain
noncompletion comparisons; the extra exact #9284 evidence is recorded separately.
No new test/native execution, rollout, source/resource acceptance or custody
release is claimed. The complete **2026-11-04 20:40:47 PST** census is
**1,007 whole open**, 278 above the fixed 729 target: 733 eligible, 274
excluded, 672 original-cohort and 335 inflow. One finite campaign completion
raises campaign credit to 775; external #10575 supplies none, with no changed
closed-body read or arrivals.

Work the coherent cohorts and high-value completed-work candidates, refreshing
only changed metadata/text and source evidence. Turn accepted groups into actual
requirement-preserving receiving packets; do not resume serial unrelated leaf
amendments or treat an issue list as accepted transfer. Keep broad unstable
neighborhoods, independent tokens, Draft/parked homes, new inflow and every
source/resource obligation visible. The parent remains Active.
