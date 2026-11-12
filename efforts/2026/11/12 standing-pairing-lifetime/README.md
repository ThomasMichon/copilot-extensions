# Standing Pairing and Lifetime

## Goal

Make standing responsibilities manually assignable without a default worker
pool, then allow explicitly delegated mechanical recovery of that retained
pairing. Local standing declarations use supported native admission rather
than requiring adopters to invent permissive callbacks. Preserve matching
existing agents, workspaces, task obligations and finite-task pool behavior.

- **Slug:** `standing-pairing-lifetime`
- **Repo:** copilot-extensions
- **Created:** 2026-11-11
- **Status:** Done
- **Current slice:** Source scope complete; issue #12171 retains archive and source-worktree settlement.
- **Vision:** [Agent topology](../../../../visions/agent-fabric/agentic-factory/agent-topology/README.md)
- **Umbrella issue:** #12171

## Guiding Intent

Declaration, assignment, acceptance and lifetime authority are different
operations. An unpaired standing task is not demand for a new worker. A
dispatcher discovers matching existing agents and explicitly pairs the native
owner. A granted recovery policy restores that pairing; it never becomes a
second allocator or interprets unknown liveness as an empty seat.

## Participants

| Participant | Role | Reached via |
|---|---|---|
| Source owner | Implementation, evidence and PR stewardship | Managed source worktrees; #12171 |
| Native owners | Worktrees custody, Bridge conversation/lifetime and Dispatch queue/policy | Existing owner APIs and contracts |
| Repository reviewer | Review plan and source against repository gates | Normal PR review |

## Coordination

- **Topology:** Dependency-ordered source PRs, one continuing source owner.
- **Host (owns PRs):** Source owner.
- **Delegates:** None.
- **Handoff:** Preserve the whole objective, exact active source, accepted plan,
  open PRs and native obligations. A completed phase is not completion.

## Context

[Standing charters](../../../../plugins/agent-dispatch/docs/standing-charters.md)
already provide durable declarations, explicit reserve/accept/release and
native relationships. The registrar reconciles complete standing source sets
without process registration; ordinary finite claiming excludes standing tasks.
The [source topology integration](../11%20topology-dispatcher-integration/README.md)
adds read-only flow, routing, reconciliation and admission reports.

Before this effort, ordinary coordinator construction required a separately supplied
Python admission factory for native standing attachment. Existing finite pool
recovery does not supply a complete retained-standing-agent lifetime contract.
Do not treat those implementation gaps as reasons to add a default standing
pool, skip custody checks or auto-create an agent.

The installer validation prerequisite preserves
[authoritative runtime replacement](../../../../visions/installer/README.md#authoritative-runtime-replacement):
the already-declared local Driver and policy dependencies must rebuild from
incoming source rather than reuse a stale local-path wheel. It changes only
the existing refresh/reinstall arguments, not activation, retirement, service
roles, operator state or rollout authority.

## Request

Public-safe statement of the source assignment:

> Standing charters have no pool by default and use manual assignment. A
> dispatcher first looks for a matching agent and explicitly pairs it. Once
> paired, Dispatch can maintain that pairing through boot or confirmed agent
> loss under a granted lifetime policy. Ordinary emitted tasks continue to
> auto-claim their configured pools. Ensure that capability is implemented.

This is the generic source contract; an adopter retains its original private
approval evidence and live scope.

## Scope Approvals

Source design, implementation, focused isolated validation and normal
issue/PR/review/merge delivery in this repository. No live enrollment, agent
creation/restart/termination, service update, monitoring handoff, new access or
deployment is authorized by this source effort. Test effects use contained
synthetic stores and native fixture owners.

## Plan

### Phase 1 - Manual standing responsibility

- [x] Prove the actual registrar/queue/supervisor path declares an unpaired
  standing responsibility without a pool, auto-claim or worker creation.
- [x] Preserve independent finite emitter/pool execution; dispatcher-controlled
  reserve/adoption remains the only initial pairing path.

### Phase 2 - Supported local native admission

- [x] Provide the production owner-supported local admission assembly for
  trusted local standing declarations, with exact workspace/participant,
  materialization, host policy and native head/conclusion checks.
- [x] Preserve explicit alternate authority/factory selection, capability
  reporting, local denial and unknown/unsupported outcomes. No permissive
  no-op callbacks, redundant user enable flag or implicit launch.

### Phase 3 - Retained pairing lifetime

- [x] Persist and enforce an explicit bounded recovery grant tied to the
  retained pairing. No recovery grant exists merely because a seat is declared.
- [x] Compose the existing supervisor and native lifetime owners to restore
  that same agent/workspace after boot or confirmed loss, preserving task,
  charter, relationships, obligations and original pending operation identity.
- [x] Hold unknown liveness, changed heads/owners/charters, denied or revoked
  policy, concurrent recovery and uncertain effects without reallocation,
  replacement, duplicated start/send or force-cleared claims.

## Validation Plan

- [x] Focused units for no-pool defaults, explicit pairing, granted versus
  ungranted recovery, revocation and legacy/mixed-version behavior.
- [x] Real local consumer proof: registrar to native standing task, pairing
  to existing owner and finite-task pools unchanged; input/state conservation
  outside authorized fixture effects.
- [x] Restart/race/reply-loss qualification preserves one pairing and original
  operation; no new workspace or competing agent on unknown outcomes.
- [x] Fresh synthetic stores and first-use admission prove the ordinary path,
  not only a test-injected factory. Document unavailable external/live tiers.
- [x] Affected test packets, F/E9 lint, strict affected type projects,
  install-contract/documentation checks and required changefiles pass.
- [x] Every source PR clears actual review and required CI and merges.
- [x] Deferred to `#12171`: Archive delivery and finalization of this source
  worktree through its normal publication and completion owners.

## Journal

### 2026-11-12 - Source accepted; deployment remains independent

- Implementation merged in [#12213](https://github.com/ThomasMichon/copilot-extensions/pull/12213)
  as `fff1f4f7880a6dd899499ffae719ca56ab09cdaa`, following exact-head
  review with no open code findings, all resolved prior threads and required
  green CI. The normal role-scoped Maintainer merge path was used; no CI gate
  was bypassed.
- The [complete native qualification](https://github.com/ThomasMichon/copilot-extensions/actions/runs/43260976330)
  passed at `1b2514798cc252350e8ce84bc627b50b0a105106`: Linux entry/installed,
  Linux seed, Windows setup/terminal/mux, Windows entry/paired Sync/installed
  consumers and actual macOS seed/copied-setup/literal forwarding. Windows
  passed all nine independent controls and all ten strict installed consumers,
  including both fault rollbacks and live replacement, within the original
  300-second group. A never-started macOS job was retried once after explicit
  hosted-capacity refusal; its actual execution passed in attempt 2. No
  assertion, child/item/quiet/group deadline or resource cap was relaxed.
- [Final exact-head CI](https://github.com/ThomasMichon/copilot-extensions/actions/runs/43264884372)
  passed freshly executed eligible units, source guards and the required PR
  gate after the final evidence update. The 403-case canonical capture and
  its immutable historical controls remain the qualified source-contract
  evidence described below.
- Post-merge sync initially tried to replay the incremental commits already
  accepted in the squash. The published tip was retained, the checkout was
  aligned to the verified accepted merge without republishing source, and
  native sync then completed. Independent upstream additions were preserved.
- Source completion does not enroll agents, grant recovery policy, update an
  installed runtime or transfer monitoring. Release promotion and any adopted
  deployment/live qualification remain separate owner-authorized operations.
  Issue #12171 retains this archive's publication and exact source-worktree
  settlement until those operations actually complete.

### 2026-11-12 - Final source-pinned retention evidence

- The [seventh native lane](https://github.com/ThomasMichon/copilot-extensions/actions/runs/43258882909)
  passed bounded Git staging, construction, all nine independent controls and
  eight installed consumers, including actual live replacement. Two publication
  fault cases still did not reach their injection: Windows selector replacement
  uses the native `replace_selector` boundary rather than `os.replace`. The
  fixture now injects before/after that real cross-platform boundary, retaining
  exact fault, error, no-cutover and byte-preservation assertions. The existing
  native selector-fault ownership/recovery control passed on Windows; lint
  passed. The full corrected physical lane remains required.
- The [sixth native lane](https://github.com/ThomasMichon/copilot-extensions/actions/runs/43255188400)
  stopped before the replacement assertions: staging the 1,667-file synthetic
  qualified source with one `git add` exceeded its 20-second child deadline.
  Preparation now enumerates the real Git-admitted paths and stages at most
  64 literal paths per child, reporting only completed batches and stopping on
  failure. Real-Git success/failure controls and isolated preparation passed
  three cases; all 34 coordinator controls and touched-file F/E9 lint passed.
  Child, item and group deadlines are unchanged. Complete physical qualification
  remains required; no failed lane is treated as a pass.
- The [403-case capture/replay](https://github.com/ThomasMichon/copilot-extensions/actions/runs/43250136923)
  passed at `9eab001f71535daae4d9259cb9b06831318b0358`, including independent
  no-write verification, immutable historical-publication controls and the
  canonical contract checker. The qualified artifact's source, stages and
  export receipt were verified before the native publisher materialized 188
  outputs. Local validation passed both contracts and 1,096 retained fixtures.
- The [fifth native lane](https://github.com/ThomasMichon/copilot-extensions/actions/runs/43250113709)
  demonstrates that disjoint construction and all 17 Windows items fit the
  original group: construction took about 197 seconds and the ten consumer
  assertions finished in 59.90 seconds, without a group timeout. Three later
  assertions failed because the shared fixture already selected the target:
  the two publication-fault cases never reached their injection and live
  replacement correctly reported already-current. Replacement cases now
  explicitly establish the healthy previous generation through the native
  activation owner before taking snapshots; all fault, rollback, update-action
  and live process assertions remain unchanged. This correction still requires
  a physical pass, not an accepted failure.
- That lane passed Linux seed, Linux entry/installed and Windows terminal/mux.
  macOS did not execute: GitHub reported that no hosted runner acquired the job
  after multiple attempts and disclosed arm64 capacity constraints. This is
  unavailable venue evidence, not a macOS pass or product failure.
- A subsequent review identified an ordinary-resume gap after completed retained
  recovery: constructing a new direct or Host-backed client could restore default
  automatic permission approval. All construction paths now consult the durable
  original recovery journal. The regression completes supported native recovery,
  performs an ordinary resume through the retained writer, and verifies a real
  permission request remains pending. All 71 connected creation/writer controls
  passed, as did six actual Host restart/reconnect permission cases. The
  canonical capture selection now contains 403 cases.
- The [third native lane](https://github.com/ThomasMichon/copilot-extensions/actions/runs/43232865903)
  passed Linux entry/installed, Linux seed, macOS seed and Windows terminal/mux
  stages. Windows Manager construction consumed about 263 seconds, leaving too
  little of the unchanged 300-second group for all assertions. Its runtime
  coordinator now overlaps only the seven disjoint entrypoint/progress controls
  with genuine construction under one existing containment owner, then runs the
  remaining consumers serially. No item, quiet, group or resource cap increased.
  Five scheduling/failure controls and all 24 focused wrapper controls passed;
  the broader native-suite coordinator file passed 31 controls. Cold native
  acceptance remains open until the complete group actually passes.
- The first local overlapped attempt still reached 300 seconds without usable
  child-phase diagnostics and is unqualified. The coordinator now explicitly
  forwards native child streams when suppressing Windows console allocation,
  and emits the independent-control/construction and serial-consumer boundaries.
  A complete source-pinned native run remains required; no deadline is enlarged
  or failure waived.
- The [first 403-case attempt](https://github.com/ThomasMichon/copilot-extensions/actions/runs/43245242812)
  correctly refused its incomplete probe surface: the listed primary probes
  passed, but the new ordinary-resume regression was missing from the explicit
  capture-test re-exports. The export is corrected rather than reducing the
  expected portfolio. Required source guards also required classifying the new
  input-control process as caller-contained in the existing lifecycle census.
  Neither correction changes product behavior or qualifies the failed capture.
- The [fourth native lane](https://github.com/ThomasMichon/copilot-extensions/actions/runs/43245224029)
  passed Linux seed, Linux entry/installed and Windows terminal/mux stages;
  macOS was cancelled. Windows finished all seven input controls and genuine
  construction, but the remaining 39 seconds admitted only seven of ten
  consumers before the original group deadline. The coordinator now constructs
  its disjoint fresh and legacy roots in separate processes, merges only
  successful private reports, then prepares rollback and runs shared-root
  consumers serially. All eight scheduling/failure controls and 24 existing
  containment-wrapper controls pass. The actual capture export executes the
  new permission regression (one pass, 402 deselected). Fresh complete native
  qualification and canonical capture remain open.
- A subsequent review found stale claim timing under write contention, a headed
  record fence held past confirmed creation and a preserved-seed checker that
  could reacquire its parent's sidecar. Preparation/claim now sample time after
  `BEGIN IMMEDIATE`; a verified headed native-child callback releases just the
  record fence before the retained wait; admission-only seed reads use the
  parent's protected snapshot. Worktrees passed 176 controls, including real
  child/writer exclusion and the cross-process preserved-seed check. Dispatch
  passed 51 controls, including real SQLite contention crossing grant expiry.
- The [second native lane](https://github.com/ThomasMichon/copilot-extensions/actions/runs/43223757185)
  passed Linux entry/installed consumers and Windows terminal/mux acceptance.
  Its physical Linux seed stage passed 468 cases with 45 declared platform skips;
  that job later failed older Manager forwarding fixtures. macOS exposed Bash
  3.2 empty-array forwarding, and Windows construction exhausted its remaining
  group time while rebuilding a second test-tool environment before assertions.
  The fixes preserve empty/literal arguments and use current forwarding snippets.
  The native owner now reuses its admitted interpreter with the portfolio and
  command guard, isolated roots and the unchanged process/item/group limits.
  All 25 wrapper controls passed. Local argument forwarding passed four cases,
  database migration compatibility passed 12 and Manager command consumers passed
  48 with four POSIX-only skips. A local cold Windows attempt still exceeded the
  300-second limit during fresh bootstrap, before assertions; it is unqualified,
  not a claimed cold-budget pass. The complete external lane must still pass.
- The first [physical native lane](https://github.com/ThomasMichon/copilot-extensions/actions/runs/43218439323)
  failed its five selected jobs. The failures were retained, not waived:
  a generated POSIX probe needed escaped newlines and a positive fixture head
  revision; older controls needed native-platform records and the precise earlier
  missing-record refusal; isolated Manager preparation could not import its
  sibling helper. The Manager entry-point repair passed its exact isolated
  `--help` consumer check, and the generated POSIX driver compiles.
- The mux overlap fixture now exercises the current authenticated read-only
  observer against one exact-PID/birth, fixture-owned protocol process. The
  production observation/creation readers and kernel lease remain real; no
  productive mux, whole-host census claim or safety-guard bypass is involved.
  All four literal check/create overlap controls passed, preserving release at
  discoverability and independent-target concurrency. The cold-resume Windows
  control also passed. Physical native acceptance must be rerun at the corrected
  checkpoint without widening its budgets or weakening failure/skip gates.
- A later review required version-appropriate v44-to-v45 database migration,
  malformed terminal-entry retention, native setup fencing, durable reattached
  permission holds and resolver failures that advance the reconciliation page.
  The new integrity guard preserves existing deletion-trigger definitions.
  Native POSIX holds the actual head-writer lock until kernel exec and consumes
  its execution marker; Windows holds the interoperable sidecar through actual
  backend creation, or through the headed native-child receipt. An uncertain
  headed receipt retains that fence until its owned coordinator exits.
- The affected Bridge journal/migration/Host packet passed 121 controls;
  real Host restart/reconnect permission checks passed six cases; local Dispatch
  admission passed 28 cases. The native packet passed 203 cases with one stale
  callback-count expectation, then all 30 Windows native-shell cases passed
  after correcting that expectation and strengthening actual child-creation
  lock probes. Linux/macOS physical native-exec qualification remains required.
  The canonical capture now adds 13 migration, malformed-entry and work-growth
  cases for 402 total; earlier 389-case captures are not substituted.
- The [389-case capture](https://github.com/ThomasMichon/copilot-extensions/actions/runs/43204632444)
  passed canonical write, independent no-write replay, historical publication
  controls and registry validation against
  `62be5b270a38f6e36cf9e46e2f13807c114dcea7`. The source remained fixed throughout.
  The workflow export receipt and artifact identity were verified before the
  canonical publisher materialized all 184 outputs with source/input preimage
  checks, native locking and registry-last publication.
- This supersedes the earlier capture as proof of the final lineage correction.
  It is isolated source-contract evidence, not installed-runtime, native Copilot,
  mux, remote authentication or live deployment qualification. Fresh review and
  required CI on the published head remain open.
- Upstream moved before final CI and required a source reconciliation. Retained
  the common immutable provenance chunks and both complete append-only suffixes;
  current upstream capture bytes remain unchanged until the canonical recapture.
  The only source-test conflict retains repair-source refusal and the copied
  native-entry prerequisites from both branches. The installer declaration was
  regenerated through its existing owner. The 389-case portfolio is unchanged,
  but the combined source requires a fresh capture/replay before merge.
- The next review found repeated full-table retention lookups. Writable schema
  preparation now adds the original-handle index, and launch-gate membership is
  collected once per query rather than correlated per session. Null gate handles
  do not retain unrelated sessions. All 30 GC controls passed, including SQLite
  VM-work scaling for both retention queries with growing settled and unresolved
  histories, original/successor retention and existing-database preparation.
  The reconciled native-installer packet also passed all 92 controls.
  The intermediate combined-source capture was canceled before publication;
  final capture/replay and exact-head CI must include this correction.

### 2026-11-11 - Native boundary review and CI qualification

- Corrected actual Host-boundary stop/custody checks, prepared-operation
  settlement, atomic head-write exclusion, first-call succession observation
  and child-environment lifetime. Recovery now uses the core's existing live
  startup-owner protocol without fabricating acquisition custody. Native
  session/load also rechecks the original owner at the RPC boundary.
- Contained compatibility: 142 Bridge lifecycle/Host/route controls, 71
  Dispatch standing/admission controls and 27 Worktrees native-admission
  controls passed. Four final observation/withdrawal controls and the complete
  ten-case Host fault packet also passed, including one-shot native-RPC
  enforcement. These packets overlap rather than adding distinct coverage
  totals. The local Windows unit run passed 4,804 cases but was correctly
  rejected as complete evidence because three admitted POSIX-only filesystem
  cases skipped. Required Linux CI must qualify all 4,807 admitted cases;
  no node, skip gate, effect restriction or budget was relaxed. These are not
  installed-runtime evidence.
- All 42 admitted strict projects passed on Linux/Windows. Touched Python
  F/E9 lint, install contracts, documentation, headless launch and scoped
  module-size checks passed. Required Linux unit CI then qualified the whole
  admitted packet. Source-contract coverage still requires fresh HTTP-52
  evidence for the optional recovery capability. The declared current capture
  portfolio is now 389 cases, preserving all historical generations.
  Actual review and the remaining required CI gates remain open.
- A second review exposed permission, original-handle collection, coordinator
  drain, sibling credential and shared-deadline gaps. Recovery now holds manual
  permission resolution, retains unresolved handles through collection/deletion,
  participates in drain safe points, scrubs sibling control credentials, never
  implicitly starts a missing Bridge daemon and reuses the active cycle deadline.
  The selected Bridge permission/daemon-loss controls, all 31 collection/deletion
  controls and seven Dispatch drain/deadline/credential controls passed.
- [Source-pinned HTTP-52 qualification](https://github.com/ThomasMichon/copilot-extensions/actions/runs/43179374070)
  passed its 386-case canonical writer, independent no-write replay, historical
  publication controls and unchanged contract guard against source checkpoint
  `3453ad5ce2579d444919ac3bdddc4d066ff2e4be`. The controlled artifact was verified
  against the workflow/source and export receipt, then materialized through the
  existing source-pinned publisher. Local contract validation passed both
  contracts and 1,096 retained fixtures; all 43 changed modules meet their caps.
  Final exact-head CI and a fresh review remain required.
- Broad Bridge guards exposed stale fixture seams and missing fresh-build
  arguments for four already-declared local dependencies. Native fault
  injection now addresses the actual extended-length paths; fixtures retain
  source relocations, historical Git bytes, strict repair-source refusal and
  the real copied native entry prerequisites. No kernel, evidence-size or
  approval check was weakened. The exact 551-case functional inventory is
  being resolved with named bounded residuals, not a larger timeout or a claim
  that the timed-out monolithic packet passed. Only two current authority
  vectors still require the existing source-bound capture owner.
- The original 551-case functional guard inventory was resolved through named
  bounded packets: 544 passed and seven platform cases skipped; this is not a
  monolithic cold-budget pass. All 134 affected metadata guards and all 92
  native installer/PS5/cache-argument controls passed. The canonical authority
  owner re-observed three current vectors and verified two byte-identical
  relocations against `b4255373643e7358c090f94ecdedd1e0afb2afd6`; a separate
  no-write verification passed.
- Final retention validation also fences explicit teardown against native
  recovery and persists legacy deletion guards. The 113-case keyed/retention/
  deletion packet and 29 ordinary teardown controls passed. Changed HTTP sources
  require a fresh source-bound capture before final review and CI.
- A successor can share its original journal, so retention now follows that
  journal key as well as the initial handle. The 24 focused lineage/migration/
  handoff controls passed, including raw deletion, forced teardown and a narrow
  unrelated-journal control. Existing deletion-trigger definitions stay intact;
  a separately installed lineage guard extends their protection. The passing
  387-case run on the prior checkpoint is not final evidence for this correction;
  the exact final portfolio is 389 cases.
- Required CI also requires exact actor-free input requalification and
  component extraction for already-large route/connection modules. Extracted
  resume/inspection routes and Host launch custody, preserving existing route
  exports and manager methods; startup timing lives with startup observation.
  Existing unit nodes, effect restrictions and budgets remain unchanged.

### 2026-11-11 - Native retained-pairing recovery

- The existing supervisor now processes one explicitly granted pairing per
  cycle through the ordinary coordinator's native owner. A live pairing
  requires one Bridge status read and no materialization subprocess; stopped
  participants receive fresh source, host-policy, materialization, original
  journal and exact-head admission before the once-only native invocation.
- Dispatch persists preparation, bounded attempt consumption and one original
  operation ID. Revocation/expiry/holds before claim prevent invocation;
  claim admits one in-flight operation. Unknown replies retain it for
  read-only reconciliation, including after revocation/source withdrawal.
- Bridge retains original native recovery outcomes in its keyed workspace
  journal, disables mutation retry/recreation/prompt drain and preserves
  unknown startup custody. Worktrees checks the original writer, workspace
  incarnation and native head revision inside its native process fence.
  Exact dead local Host records settle only after both recorded process
  births prove exit. Legacy unknown identity and other lifecycle owners hold.
  The existing native launch gate commits with the recovery journal and blocks
  legacy ordinary resumes after rollback while an outcome remains unknown;
  acceptance settles only its own gate atomically.
  Native durable stops remain independent holds under automatic admission;
  qualified pre-entry refusals retain a no-effect receipt for exact
  reconciliation. Observing an original journal's new native
  updates its observation, not the task assignment.
- Unit/contained packets: 317 Bridge resume/stop controls and 110 Dispatch
  standing/source-flow tests and 26 native admission tests passed. A final
  actual HTTP/native-owner round trip is included; the final three
  native-driver cases also passed after bounding the live probe work.
  Final CLI controls passed for complete, unsupported and explicitly unknown
  recovery outcomes. Touched Python F/E9 lint, all 42 admitted strict projects
  on Linux/Windows after syncing the current contribution base,
  the 13-plugin install contract, documentation consistency and headless
  launch checks passed. Dispatch/Bridge typing admission remains pending;
  these results do not claim new runtime modules are strict projects.
- Residual qualification: the expanded Bridge packet passed 316 cases and
  exposed a legacy fixture retaining post-v28 inbox guards. Removing only
  those later guards from the synthetic v28 fixture preserved production
  protection and the migration residual passed. The added original-native
  observation control passed separately without transferring assignment.
- Validation tiers: units exercise the new state/contract behavior; local
  integration exercises the real registrar, default coordinator, queue,
  parser/client/HTTP and native resume owners with contained synthetic native
  I/O, plus the real process exclusion boundary. Fresh synthetic stores
  prove first use and nullable journal preparation. No installer/provisioning
  behavior changed, so a fresh-OS provisioning lane was not required.
  Live external execution was not run: this effort authorizes source and
  isolated validation, not live enrollment/restart/deployment.
- No source PR is yet merged for this implementation. Required review, CI,
  merge, canonical completion and source-worktree finalization remain open.

### 2026-11-11 - Native default standing admission

- Ordinary coordinator construction now supplies a separate local standing
  writer without changing the finite-task writer or authority. Real trusted
  discovery, original queue admission, selected Worktrees CLI/resident and
  original Bridge keyed-journal inspection compose the supported path.
- Materialization uses the active native inspector and a source-contained
  reference; local denial and exact native head/conclusion checks remain in
  force. Original journal participation is retained on the assignment event.
  Cancellation/release can settle that original relationship after source
  withdrawal and native head conclusion.
- Contained native/default-and-relationship packet: 86 passing tests. Bridge
  keyed-writer packet: 53 passing tests, including read-only inspection of a
  terminal original journal without reopening or replacing it. The earlier
  two assertion failures expected a generic error where the implementation
  correctly returned specific host/source refusal causes; corrected controls
  passed.
- All 41 repository-admitted strict projects passed Linux/Windows type
  checking. Touched Python F/E9 lint and the 13-plugin install contract passed.
  No live enrollment, deployment or agent-lifecycle effect occurred.
- The actual bounded retained-agent recovery consumer, boot/loss qualification,
  reply-loss/race controls and full source review/CI/merge remain open.

### 2026-11-11 - Bounded recovery delegation storage

- Added revision-fenced grant/revocation commands and control-authorized HTTP
  paths for an already accepted pairing. Grants retain the workspace, native
  owner, original relationship, declaration/materialization, approval reference,
  expiry and attempt limit without staffing or launch effects.
- Original logical IDs cannot renew, change limits or reactivate a revoked
  grant. Ownership/declaration drift, holds, expiry, unsupported state and
  pending effects withhold effective authority; revocation retains custody and
  remains available without the native attachment writer.
- Initial contained state/admission/charter packet: 192 passing tests.
  Client/CLI packet: 69 passing tests. Final grant packet: 31 passing tests,
  including unknown-state and numeric-boundary controls. Focused malformed
  record controls and repository-admitted type checks complete the final gate.
- Native default admission and the actual retained-agent lifetime consumer are
  still open. Grant storage deliberately advertises only `recovery_grants`,
  not automatic recovery; source delivery is not live adoption.

### 2026-11-11 - Plan accepted; native no-pool consumer proof

- Plan #12173 merged at `a9d2c51045b94d865859f112408038d3a9802010`
  after actual approval and required CI. Source work continues on that reviewed
  intent; no live adoption occurred.
- Extended the real Git registrar-to-coordinator consumer proof to assert an
  unpaired standing task has no pool assignment or spawn reservation, while a
  finite emitted child claims and completes independently. Native local
  admission and retained-lifetime implementation remain open.

### 2026-11-11 - Source gap claimed

- Claimed #12171 after bounded open issue/PR deduplication. Verified the
  existing registrar path is non-spawning and ordinary standing attachment
  currently depends on separately configured admission assembly.
- Proposed native defaults plus separately delegated retained-pairing
  lifetime, preserving the existing owners and no-pool standing behavior.
- Planning only; no implementation or live effects yet.
