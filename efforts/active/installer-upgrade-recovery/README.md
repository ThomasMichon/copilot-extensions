# Installer Upgrade Recovery

- **Slug:** `installer-upgrade-recovery`
- **Repo:** copilot-extensions
- **Branch(es):** sequential bounded contribution PRs targeting `dev`
- **Created:** 2026-11-03
- **Status:** Active
- **Umbrella issue:** #10377
- **Related issues:** #9489, #7743, #9448, #7758, #10180, #6820, #7028, #7947, #6389, #5472, #10451, #9716, #10054, #10499, #10486, #10652, #10653
- **Vision:** [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)

## Guiding Intent

Drive high-impact installer/update compatibility failures to a verified usable
incoming installation through the latest supported explicit install/update
flow. Fresh-install success, a prepared candidate or a merged bug fix does not
prove that an affected older installation can recover.

This workstream closes the installer vision's existing authoritative replacement
intent; it does not extend it. Incoming readiness and every applicable current
selector, command, route, launcher and registration selecting the incoming
generation define success; fresh calls must reach that generation. Old recovery
protocols, provenance, stale installer transactions, queue fences, process-census
uncertainty and occupied old control homes cannot veto explicit replacement.
Old stateful wrappers and keepers may retain old code for existing clients.
Full adopted-client reattachment remains separate work under #10486, not an
incoming prerequisite; #9716 retains census ownership and #10377 coordination.
Unknown old cleanup is deferred and reported separately. Actual incoming
authenticity/admission, build, authentication, necessary durable-state
compatibility/migration, permission, I/O and health failures remain failures.
Preserve protected sessions and transfer or exclude conflicting work admission.
Outgoing central retirement is independent of incoming completion and
revalidates identity, role and then-current selection before eligible mutations.
Never sweep process names or force-kill protected state holders.

## Participants

| Participant | Role | Reached via |
|---|---|---|
| Workstream driver | Generic issue reconciliation, selected source fixes, acceptance and publication | Managed source handle `dusty-contraption`; #10377 |
| Existing entry and sequencing owners | Retain fresh-entry, command migration and update sequencing implementation | #7743 and #6820 |
| Existing construction and execution owners | Retain canonical construction, source-managed installation and installation-request execution | #9489, #7028 and #7758 |
| Existing recovery and lifecycle owners | Retain their scoped queue, historical cleanup, durable-state and central lifecycle work | #9448, #10180 and #7947 |

These are coordination dependencies, not new assignments or ownership transfers.

## Coordination

- **Topology:** serial bounded source units, with normal independent PRs where
  their edit and acceptance scopes do not overlap.
- **Host:** Workstream driver owns this effort, its PRs and completion gate.
- **Delegates:** none selected. A future bounded delegate does not inherit the
  parent objective merely by completing a source slice.
- **Claims:** recheck the issue, current source/release and open PRs before
  implementation. Reuse an existing issue rather than duplicate it. Preserve
  contributor expressions and active claims; coordinate an overlap before
  changing its implementation.
- **Boundary:** this is a defect and upgrade-acceptance workstream, not a second
  installer, reset engine, artifact resolver, daemon, scheduler, provenance
  authority or repository-wide merge queue.
- **Handoff:** this README, [acceptance matrix](acceptance.md) and #10377 retain
  selected cases, owners, outcomes and the next source unit.
- **Effects:** source development, contained tests and normal source publication
  only. Shared installations, credentials, services, queues, sessions and
  deployment are not changed by establishing this plan.

## Context

Existing issues already describe unsupported UV options, an update entry behind
installed Python/Manager dependencies, first-foundation upgrades with existing
requests, and attributable historical cleanup. Some source corrections have
merged. An open issue does not prove that the original code defect survives,
and a source merge does not prove recovery through the released ordinary entry.

The workstream therefore starts from reproducible generic cases and actual
consumer contracts, not another broad rewrite or a copy of historical refusal
tests. The initial candidate families and their existing owners are in the
[acceptance matrix](acceptance.md). A candidate is not an implementation claim.

The authoritative references are the
[install contract](../../../docs/install-contract.md#authoritative-replacement-and-completion),
[service-contract evolution](../../../docs/patterns/service-contract-evolution.md)
and current architecture adoption boundaries. Existing #6820 sequencing,
#7028 installation and #7947 state work remain canonical for their mechanisms.
New caller syntax must not assume that an arbitrary installed older runtime
already supports a capability. A reviewed architecture cutover uses its
documented fresh migration entry rather than pretending old protocols work.

## Request

Public-safe requested outcome, verbatim:

> So, we need to take the high-pri issues and drive fixes for them. We also need to ensure that for any update incompatibilities that we ensure our latest install/update flow will be able to recover the user into a good flow once applied

The requested coordination destination is:

> Yes, hoist relevant issues to copilot-extensions and drive a workstream up there

Implementation decomposition below is agent-recommended. The request does not
authorize production updates, recovery mutations or new access.

## Plan

### Phase 1 - Reviewed scope and issue ownership

- [x] Establish #10377 and reconcile adjacent public workstreams before claiming
  their existing source units.
- [x] Land this canonical plan and acceptance matrix through repository review
  before implementation: #10379, current-head approval and required CI.
- [ ] Classify each selected candidate as a surviving source failure, released
  fix needing consumer proof, owned dependency or unsupported-contract migration.
  Hoist only additional generic gaps that do not already have an upstream issue.

### Phase 2 - Priority fixes and latest-flow recovery

- [x] Select the first bounded, unclaimed reproducible source gap, or obtain
  explicit coordination with its existing owner. Record its old-footprint case
  before changing production code.
- [x] Fix the owning operation and directly dependent callers; add focused
  red-on-old coverage and actual parser/process or installer consumers.
- [ ] Prove recovery through the latest supported normal install/update entry,
  including incoming readiness, complete current selection and preserved user
  state. Internal helper success is not whole-flow acceptance.
- [ ] Repeat for the selected priority cases, preserving existing construction,
  admission, durable-state and lifecycle owners instead of reimplementing them.

### Phase 3 - Release and completion

- [ ] Drive each source PR through review, required CI, merge and containing
  release observation. Use changefiles for affected plugins; do not hand-bump
  versions or equate `dev` merge with release.
- [ ] Record source, release and contained-consumer outcomes separately.
  Installed/affected-host qualification remains a separately authorized lane,
  not an inferred result or an implicit deployment obligation.
- [ ] Resolve every selected Plan/Validation item or transfer it explicitly to
  a named tracked objective, update affected reality documentation, and archive
  only when this parent gate is satisfied.

## Validation Plan

- [ ] For each selected incompatibility, start from representative broken or
  incompatible prior state and exercise the real latest entry, not only a
  pristine installation or a mocked successful recovery.
- [ ] Prove required incoming runtime/service readiness and all applicable
  selectors, commands, routes, launchers and registrations, including fresh
  calls reaching the incoming generation. Distinguish package identity from
  running generation and whole-set completion from partial progress.
- [ ] Qualify MCP's cross-platform ordinary-entry fresh-settlement gate under
  #10652, retaining #10054/#10499/#7758/#7028 ownership and U12's delivered boundary.
  Pair successful incoming installation with a live old keeper, unknown old
  census and occupied old control home against genuine incoming source,
  authentication, build, permission, I/O and health failures. Use the incoming
  generation's owned namespace, not takeover of an unknown foreign listener.
  Record native platform results and unavailable lanes separately.
- [ ] Reuse #6389 for stale-capable-serving/fresh-admission acceptance. Exercise
  automatic `--force` with inhibited updates and held clients under #10653:
  force does not change request classification or authorize unsafe retirement.
  Keep #9716 census and #10486 full reattachment separate from incoming
  completion.
- [ ] Preserve configuration, credentials, repositories, worktrees, sessions,
  databases and accepted/uncertain effect history; repeat without manual cleanup.
- [ ] Retain actual incoming acquisition/build/migration/I/O/permission/health
  failures as failures. Preserve useful errors and confirmed child cleanup.
- [ ] Exercise healthy-current reuse, repeat convergence, source-worktree
  independence, automatic-only inhibition and protected-session/central-only
  retirement controls from [acceptance.md](acceptance.md).
- [ ] For changed peer boundaries, qualify current/prior callers and providers,
  supported rollback, missing metadata and unsupported requirements. Do not
  blindly replay an effectful operation with older syntax.
- [ ] Use focused unit checks, real subprocess/native fixtures and practical
  clean-room lanes. Keep broad native matrices opt-in, not added to required
  unit-only CI. State exact reasons for unavailable lanes.
- [ ] Audit every issue, effort, test, example, PR and commit for public-safe
  scope. Do not attach private reports, raw logs or deployment identifiers.

## Proposal

Start with update reachability and explicit/manual classification, followed by
the Windows construction/legacy-state cases that still reproduce. Existing
source corrections receive ordinary-entry recovery proof before another patch
is proposed. Select each concrete unit only after current owner and source
reconciliation; the matrix is not a blanket reservation of those components.

## Journal

### 2026-11-03 - Workstream established

Opened #10377 after checking existing installer/recovery issues and adjacent
efforts. The existing UV compatibility source PR #9501 is merged; #9489 still
needs its remaining evidence reconciled rather than a duplicate source fix.
Fresh-entry and first-foundation cases have existing owners and retained
qualification boundaries. This plan proposes canonical coordination and
acceptance, not completed implementation, release or installed recovery.

### 2026-11-03 - Reviewed plan merged; first recovery case under reconciliation

#10379 merged to `dev` as `589f9c41c544e671f1180621fc5c3202ed3ef674`
after current-head Copilot approval, required green CI and an empty review-thread
set. #10377 remains open. This clears the plan gate, not source implementation
or installed recovery.

The first candidate trace found that the registered/module runtime planners
derive a current no-op from the deploy-manifest version, while the executor
returns that status before invoking the installer. Missing/incomplete selected
runtime recovery therefore needs a concrete consumer regression. Reconcile this
non-serving-footprint case with #6389 and its existing serving/lifecycle owners
before taking a source slice; a related tracker is not a new ownership grant.

### 2026-11-04 - Selected-footprint recovery source unit

The bounded non-serving footprint slice is claimed on #6389 in
[comment 6625866728](https://github.com/ThomasMichon/copilot-extensions/issues/6389#issuecomment-6625866728).
Its service adapters, independent retirement work and the other contributor's
#6928 remain untouched. This is case U8 in the acceptance matrix, not a claim
on all serving convergence.

Three native regressions failed against the pre-fix source: the ordinary
selected-target update returned success and `SKIPPED (current)` for a missing
interpreter, incomplete slot or wrong selector without reaching the installer.
The planner now qualifies that shortcut through the owning payload's existing
read-only generation/completion/interpreter helpers. Missing or unsuccessful
probe support reaches the normal installer; no old-footprint permission or
additional recovery command is introduced. Source/profile identity checks,
legacy selector-free venv support and Bridge's mandatory serving reconciliation
remain intact.

The focused Windows source packets passed 257 tests with three explicit platform
skips. Four native selected-target controls also prove real shell invocation,
usable fixture-venv Python, preserved configuration bytes, unchanged healthy
slot bytes on immediate repeat and a failed repair remaining failed. These
controls use a synthetic installer to isolate the updater contract; they do
not qualify every production installer, package import, service readiness or
an installed-host update. The first combined group exceeded its existing
300-second bound; one-file groups passed under unchanged group, item, aggregate
and resource limits. Ruff F/E9, whitespace and the 13-plugin install-contract
guard passed.

Performance is scoped to explicit update planning, not session hooks or launch.
Each equal-version candidate adds one bounded read-only child per collected
plan; the target consumer replans after payload acquisition, so it checks twice
per update. Prepared-source identity checks are reused, not repeated in this
probe. Ordinary marketplace payloads require no tree scan; source-qualified
marketplace payloads retain the canonical source helper's assembly validation.
Native timing and deterministic counts are recorded with the selected-target
controls: 14 probes, median 0.680 seconds and maximum 1.031 seconds in the
final Windows fixture run. Small fixture samples are not a portfolio or
tail-latency guarantee. The inventory consumer shares this planner but has an
eight-second Manager deadline; it retains advisory version/support planning
without the new readiness probes. A focused control covers registered, module
and engine inventory paths. Only actual update plans require the footprint.

Source review, required CI, merge and containing release remain pending.
Clean-room first provisioning is unchanged; this unit exercises affected
existing footprints. Linux/macOS native and installed/external-venue operations
were not run in this Windows source-only lane. No host rollout, service,
credential, queue, protected-session or retirement effect is claimed. The
parent effort and #10377/#6389 remain open.

### 2026-11-04 - Marketplace profile currentness

Ordinary Containers installations select `+base` or `+provider-exec` generations,
not the unprofiled package version. Applying the generic source/repair generation
helper to those healthy selections unnecessarily reached the installer on every
repeat. Both healthy marketplace profiles reproduced that miss; six changed
intent, incomplete and missing-interpreter controls retained reconstruction.

The footprint probe now uses the owning Containers target selector for ordinary
marketplace profiles, binding desired profile, package/source identity and
deployed generation before the same completion/interpreter check. Prepared-source
targets retain their existing qualification. The changed profile and native
target-consumer packet passes 76 tests with three platform skips; the other
already-qualified packets are unchanged. This corrects healthy reuse without
loosening incomplete selection or source identity.

### 2026-11-04 - Selector-free legacy directory qualification

A stale `.venv` or `venv` link can survive a missing selector while pointing to
an older completed runtime. Following that link does not prove that the matching
manifest's requested generation exists. Six controls using actual Windows
junctions reproduced false current skips across the engine/module/registered
planners. The legacy fallback now uses the canonical link classifier to exclude
symlinks and junctions while retaining real-directory reuse. The affected native
consumer and serving/footprint packet passes 78 tests with three platform skips,
including unchanged old-slot evidence and real legacy-directory controls.

### 2026-11-04 - First source unit merged; next producer recovery unit

#10398 merged to `dev` as `fbcbf22b0ef518519daf67f865a2f9b551f7af89`.
Copilot approved exact head `9bbf63f6b12aeb8212cfae880f6f1a00d96b0de1`;
both review threads were resolved and required CI run 41805066341 passed.
An earlier same-head CI run also passed, but the provider required the newer
run before accepting the merge; no CI requirement was bypassed. All eleven
authored files match the verified squash merge, and the source worktree has
pulled forward. Containing release and installed application remain separate,
unperformed outcomes. This completes the bounded #6389 planner reservation,
not the parent convergence objective.

The next serial unit is the Codespaces-only normal install/update repair
adapter, claimed on #5472 in
[comment 6627075322](https://github.com/ThomasMichon/copilot-extensions/issues/5472#issuecomment-6627075322).
The actual contained Windows producer currently refuses a selected malformed
or incomplete same-version slot and preserves its bytes; two existing
consumer controls pass at that refusal contract. They do not prove recovery.
The next regression must require a distinct admitted healthy generation and
usable selected CLI without rewriting the retained old identity or resetting
state. Pair PowerShell/POSIX adapters and retain source identity, healthy-repair
reuse, failure preservation and applicable publication/service hooks.

Shared construction/retention/guardian ownership, the Worktrees first adopter,
#6928 watchdog work, #8826 diagnostics and #6389 lifecycle adapters remain
separate. No production edit for this next unit preceded #10398's merge.
The canonical effort and #10377 remain active; no shared-host installation,
service change, credential operation or rollout is authorized by this journal.

### 2026-11-04 - Codespaces native producer recovery

The U9 normal `install` and `update` regressions failed against the pre-fix
producer at construction admission for a selected incomplete same-version slot.
The Codespaces adapter now selects a fresh repair generation before entering
the unchanged lower construction guard. It reuses only an exact completed,
isolated-CLI-healthy target; occupied incomplete, malformed, conflicting or
unhealthy targets are not rewritten. Existing construction, completion,
retention, activation and failed-candidate cleanup remain canonical.

PowerShell and POSIX entries are paired. Source receipt validation keeps its
strict base identity independently of the repair suffix, and source reuse also
requires the exact deployment receipt. The manifest records the actual selected
generation while preserving package/source identity. The normal updater's
existing readiness reader already accepts that qualified repair identity; no
additional caller syntax or updater patch is needed.

The focused Windows packets pass 27 unique controls, with four native POSIX
skips. They cover normal install/update and immediate reuse, source-qualified
repair/reuse, malformed completion, failed dependency installation and incoming
CLI health, fresh first touch, bootstrap/activation failure, and existing lower
reuse/admission controls. Real disposable venvs and the generated command prove
the selected fixture CLI runs; old-slot/configuration bytes remain unchanged,
and failed incoming construction leaves selectors unchanged. Fake UV and guarded
private profiles prevent package acquisition and host-service effects.

Validation retained the existing 120-second native-child, 180-second item and
300-second group bounds. The initial combined normal/source packet exceeded
the group bound; a monolithic source-install-plus-repeat case also exceeded the
item bound. Splitting source assertions around one module-scoped disposable
installation passed in 162.15 seconds without increasing limits. The final
normal/unit/lower-reuse packet passed 17 controls and four platform skips but
exposed one stale exact diagnostic assertion. The fixture now accepts only the
canonical confirmed-abort diagnostic, including its exit-1 suffix, and retains
absence-of-candidate/selector assertions; that case and four fresh/failed-entry
controls then passed in 191.82 seconds. These are bounded functional-packet
measurements, not per-update latency or installed-host performance claims.

Actor-free generation controls are on-demand, not an expansion of required
unit-only CI. A full fresh VM/container, native POSIX execution and live external
installation were not run in this Windows source-only lane; no such deployment
is authorized here. The private synthetic fresh profile covers first-touch
construction directly, but not real package feeds, service registration,
CodeSpace connectivity, protected sessions or central retirement. README,
testing recipes and the existing immutable-generation pattern describe that
adoption boundary. Source review, required CI, merge and containing release
remain pending; #10377 and the parent effort remain active.

### 2026-11-04 - Review catches Bash 3.2 nounset compatibility

#10426 received a current-head Comment review identifying a real macOS
portability failure: the two new optional arrays could be empty under
`set -u`, which Bash 3.2 treats as an unbound expansion. Both arrays now retain
their mandatory command arguments, so they are never empty. Optional receipt
and keep arguments still preserve their individual boundaries.

Three focused controls execute the actual selection and activation functions
under nounset, with absent/present receipts and no GC keep arguments. They pass
on Git Bash 5.3.15 in 1.68 seconds (49.20 including isolated preparation and
cleanup). The same on-demand packet accepts an explicit Bash 3.2 interpreter;
native macOS/Bash 3.2 execution is unavailable in this source lane and is not
claimed. These add three controls to the prior 27 unique passes. Windows
runtime behavior, shared construction helpers and the existing four platform
skips are unchanged. Review-fix lint/whitespace pass; current-head review and
required CI must pass again before merge.

### 2026-11-04 - Invalid predecessor reference is not runtime admission

The next current-head Comment review exposed a surviving ordinary-entry
failure when the selected base slot is a regular file. The focused real
update regression failed at `ordinary runtime slot not installed` before
fresh construction, despite the adapter selecting a distinct generation.
The bounded prerequisite is coordinated on #5472 in
[comment 6629192533](https://github.com/ThomasMichon/copilot-extensions/issues/5472#issuecomment-6629192533).
It does not transfer shared guardian or Worktrees coordinator ownership.

The existing canonical retention primitive now permits an explicit
predecessor-reference pin without requiring that old entry to qualify as an
installed directory. Only construction's predecessor pins select that mode.
The same kernel ownership excludes cleanup; ordinary runtime/candidate
retention remains strict by default, and payload-hash admission is independent.
No invalid old entry is rewritten, no lock protocol is replaced, and
installer guardian/process transport and candidate admission are unchanged.
Adopting the maintained footprint plan would instead require migrating
Codespaces' legacy removal profile and broaden the cleanup contract; that
alternative was rejected for this fix.

The canonical reference packet passes 19 controls, including three new strict
default/preservation/admission controls. Normal update from the regular-file
slot, immediate reuse and the two existing failed incoming controls pass
three native cases in 129.90 seconds. Existing Bridge retention/construction
packets pass 85 controls with eight explicit platform skips, including real
competing holders, cleanup, process death and failed-candidate receipt custody.
Together with the settled Codespaces cases this is 135 unique passes and
twelve platform skips, not installed-host qualification.

Canonical copies are synchronized across thirteen plugins, the Manager and
Update Driver, with release changefiles for every affected consumer. MCP's
maintained producer digests refresh without changing contract revision 36.
The required unit manifest refreshes only three changed admitted source
fingerprints; its 4,019 nodes, coverage targets and 300-second budget remain
unchanged. That new source snapshot still requires hosted CI and a fresh
passing review before merge. No rollout or whole-parent completion is claimed.

The shared module-size guard rejected five lines of facade growth. Rather than
widen its shrink-only ceiling, the touched custody implementation moved into
the existing construction component behind the same public facade and kernel
callbacks. The full module-size sweep now passes without a baseline edit;
the canonical 19-control packet passes again, and native consumer qualification
is repeated against that extraction before publication. Those final reruns pass
85 Bridge controls with eight platform skips, and the three normal-entry/failure
controls in 162.14 seconds. No baseline, test budget or candidate-admission requirement was
relaxed to obtain those results.

### 2026-11-04 - Undecodable selectors and stale retained helpers

The next current-head review found two additional ordinary-update failures.
Both reproduced through the real PowerShell entry: invalid UTF-8 current and
last-known-good selectors blocked constructor/publication reads, and stale
retained helpers passed the CLI launch check but failed completion verification.
Canonical selector readers now handle undecodable old content without relaxing
link, type, permission or other I/O checks. PowerShell's reuse probe additionally
requires the exact retained helper bundle its completion step consumes.
The existing canonical helper verifier is read-only; old slots are not restamped.
POSIX completion does not require that bundle and retains its existing probe.

Both native regressions now pass normal update and immediate repeat, preserving
old-slot and configuration bytes. Two pure helper controls and two canonical
publication controls also pass. The final focused evidence totals 141 unique
passes and twelve platform skips, including repeated source-qualified, receipt,
helper and shell packets. Existing child/item/group bounds remain unchanged.
Canonical copies and maintained producer digests are synchronized; only three
admitted source fingerprints change in the required unit manifest, not its
4,019 nodes, coverage targets or 300-second budget.

The same Windows source-only limitations remain: no native macOS/Bash 3.2,
fresh external VM, real package feed, service registration or live installation
is claimed. Required CI and a fresh current-head verdict remain merge gates.
The earlier planner fix is released through #10430; it is not installed by
this source work. The separate artifact-publication gap remains with #10432's
existing owner. Neither containing release nor this PR authorizes rollout or
completes #10377.

The following review had no open findings but identified unexecuted native
completion-failure controls. Both Install/Update cases reproduced a missing
generation-selection fixture prerequisite before reaching completion refusal.
The focused extracted-function fixture now records selection followed by
completion, retaining the real activation function, unchanged selector and
throwing publication callbacks. All four native broker/publication controls
pass in 5.08 seconds (53.00 including isolated preparation/cleanup). This fixes
test integration, not production behavior, and is additional to the 141-control
evidence above. Current-head CI/review and reconciliation with Dev remain due.

### 2026-11-04 - Producer recovery merged; outer consumer qualification next

#10426 merged to Dev as `8d71dd2f275c3289801767440a0adef0b2abf162`
at 13:13:27 UTC. Copilot approved exact head
`258d1964c3784090c85bbb7210dabd670c56dd73` in review 5768817967;
all three review threads were resolved. Required CI 41834233630 passed,
including exact-input unit qualification and the final PR gate. The earlier
same-head CI and native Linux/Windows legacy-monitor lanes also passed.
The maintained rebase preserved current Dev's 4,044 unit nodes, coverage
targets and 300-second budget; this unit changed only three source hashes.
All 54 authored paths match the verified squash merge.

This completes the bounded Codespaces adapter and predecessor-reference
prerequisite reservations on #5472 at the source tier. It does not complete
the shared immutable-slot campaign or its guardian, watchdog, lifecycle and
other adopter work. The previous #10398 planner fix is separately released
through #10430; #10426 still needs containing-release observation. No installed
or serving generation was changed by either source result.

The next serial acceptance unit joins the normal outer collected update to
the real Codespaces producer in a disposable guarded profile. A matching
manifest must not suppress incomplete-slot repair; the resulting selected CLI
and generated command must work, an immediate rerun must retain the healthy
generation without dependency writes, and actual incoming failure must remain
failed with prior state preserved. Acquisition and unrelated portfolio members
remain explicit fixture boundaries, not evidence for feeds, services or fleet
completion. The parent Plan/Validation items and #10377 remain active.

### 2026-11-04 - Outer collected consumer recovery qualified

The new bounded Windows packet joins the actual normal collected update
orchestration and currentness subprocess to the real installer process/receipt
owner and Codespaces producer. A matching deployment manifest with an incomplete
selected slot now reaches fresh repair, publishes a usable selected Python and
generated command, and preserves the old slot/configuration. Immediate repeat
is a truthful outer `SKIPPED (current)` without dependency writes. Failed
incoming dependency preparation remains failed and retains the original
selectors, manifest, old slot and configuration without a candidate left behind.

Both cases pass in 88.12 seconds (133.91 including isolated preparation/cleanup)
under the original 120-second native-child, 180-second item and 300-second
group bounds. Initial fixture construction failed at real machine admission
before reaching the installer; the fixture now explicitly supplies synthetic
participation, alongside controlled acquisition, unrelated engine and final
host integration. The currentness, producer, process/receipt checks and final
CLI remain real. No production behavior was changed to obtain this result.

These are U8/U9 collected-consumer results, not proof of repository-shell fresh
bootstrap, governed feeds, services, every portfolio member or installed-host
recovery. The packet reuses the existing guarded Codespaces fixture rather than
creating another installer. It remains on-demand and outside required unit-only
CI. The test/documentation PR and #10426's containing release remain due; the
other candidate owners and parent Plan/Validation gates remain open.

### 2026-11-04 - Joined acceptance merged and producer release verified

#10448 merged as `7fa780be4f7792d4453e6233a8e387b146e88750`
at 13:39:04 UTC from Copilot-approved head
`277d1b9bd1c040a6899404ea63d366d94cc5375a`. Required CI 41838618639
passed, with an empty review-thread set. The continuing source worktree
pulled forward. The two joined native cases now have reviewed, versioned
consumer coverage; this is not repository-shell or installed-host acceptance.

Containing promotion #10449 merged at 13:37:26 UTC. Its actual qualified
Dev `8c64db93fc7bf165a362a17820211b352b7b9122` contains #10426's
`8d71dd2f275c3289801767440a0adef0b2abf162`; released Main is
`ec816c940921a5c7b42d3201ca2a6f4326e52aac`. The generation selector,
both source-receipt adapters and both canonical runtime/construction helpers
have matching blobs across the fix, qualified source and release. The native
entry scripts differ only by the canonical repository-to-local installer-engine
reference materialization. The released Codespaces version is `0.5.248-dev1`,
with Worktrees `1.24.456-dev1`. This is released/pending authorized rollout,
not a change to any running installation. #10448's later qualification-only
change still needs its own containing release observation.

The construction-family reconciliation also found existing delivered fixes,
not another unclaimed source patch: #9623 supplies the packaged pywin32 static
bootstrap allowance and #9878 preserves completion/settlement diagnostics.
Both commits are in qualified Dev `03548e10d689419f3e887b0ac625c1df3c5669df`
and release #10446; canonical construction blob
`57f7bbb255475750d881f2bae74d29254be1ea8d` matches its Main snapshot.
Ten strict packaged-pywin32 controls pass in 1.50 seconds (4.11 including
isolated preparation/cleanup), without executing the bootstrap hook.
The existing #7305 installed-consumer owner retains real-feed, Dispatch,
normal published update and headed-CLI preservation acceptance, informed by
[comment 6632045751](https://github.com/ThomasMichon/copilot-extensions/issues/7305#issuecomment-6632045751).
No duplicate fix, installation or acceptance transfer is inferred.

### 2026-11-04 - Windows venue fallback recovery

#10451 records a surviving default-layout defect, reproduced before production
edits: a real native Python child launches from a short control directory but
fails with `WinError 267` from the existing 270-character prepared payload
directory. The path exists; filesystem long-path support is not native process
working-directory support. The bounded slice is coordinated with #8242 in
[comment 6632477155](https://github.com/ThomasMichon/copilot-extensions/issues/8242#issuecomment-6632477155).
Its cache/inventory, transport, closure and contributor-owned changes remain
separate. Parked installation-cell coexistence/migration is not resumed.

On Windows the owning fallback layout drops redundant marketplace/installation
directories but retains the full SHA-256 of qualified source and complete
declaration, beneath the same Git-local staging lifecycle. Registration validates
the payload against that canonical home. POSIX layout is unchanged. Normal
preparation selects a fresh short home without reading or rewriting an older
malformed completion receipt, moving its payload, changing the marketplace
source, or adding a recovery command. Immediate repeat reuses the completed
short home. Revision/source changes remain distinct and old bytes survive.

Seven focused controls pass in 7.92 seconds (81.03 including isolated
preparation/cleanup), including real preparation, registration, native child
launch and repeat; two selected plugins in one marketplace; declaration revision;
and strict deep-root refusal before installation or registration. Native limit
qualification caught an initial off-by-one: 258 UTF-16 units work, whereas 259
fail because native normalization also needs a separator and NUL. The corrected
preflight covers completed reuse and a surrogate-pair path, not just character
count. It is an honest supported-path boundary, not a claim of arbitrary depth.

All 234 existing preparation tests also pass in disjoint bounded packets.
Stale tests that assumed literal `installation` directories now derive the
canonical home; the independent fixture derives catalog identity from its
actual private settings instead of ancestor names. A first combined run reached
its 300-second limit, and concurrent cached-venv preparation interrupted later
packets. Those packets were rerun only after the canonical runner restored its
own test environment; no host runtime was repaired. Item, group and resource
limits were not increased. New coverage remains on-demand, outside required
unit-only CI.

This closes a bounded authoritative-replacement/source-layout gap, not whole
installation convergence. Synthetic acquisition and private fresh homes qualify
the first-touch/older-footprint consumer contract without a network or live
Copilot. Native POSIX, a fresh external VM, actual feeds/hooks, installed runtime
selection and service readiness were not exercised in this Windows source-only
lane; no rollout is authorized. Source review, required CI, merge and containing
release remain due. The parent effort and #10377 remain active.

### 2026-11-04 - Joined acceptance packet release observed

#10452 merged at 14:16:22 UTC, publishing qualified Dev
`150efa4dcbc8b129cf0735c4735beea8e3197542` as Main
`bbdb61526541f2ba0c0e9fe0f69f671d4376237f`. That actual qualified source
contains #10448's merge, and the outer Codespaces recovery test has identical
blobs in the source merge, qualified Dev and released Main. This completes
the qualification packet's containing-release observation for U8/U9; it is
not another native run or an installed/runtime/service acceptance result.
The remaining matrix boundaries and parent Plan/Validation gates stay open.

### 2026-11-04 - Windows venue source merged; remaining ownership reconciled

#10456 merged to Dev as `affb395f47114f88671c4aecd75c17b1000b74bd`
at 15:10:45 UTC. Copilot approved exact head
`8c92f789c9c9f1fcfc77089280bbf9c206fb48b8` in review 5770571274.
Required CI 41852993195 passed all source/contract, eligible-unit, identifier
and PR gates; there were no review threads. All thirteen authored paths match
the squash merge and the continuing source worktree pulled forward.
The seven native/identity and 234 preparation controls remain the functional
evidence. All forty admitted strict type-check projects also pass for both
declared platforms; pending inventory scopes are not implied to be strict.
Containing release remains due. Nothing was installed or activated.

The bounded default-layout reservation on #10451 is complete at the source
tier, not whole-host acceptance. The next reconciliation preserves an existing
owner rather than opening a duplicate comparator task: #2404's newer-local/
older-marketplace launch requirement is retained as seven explicit clauses in
still-open #7030. Its current stage derives version drift from inequality
separately from publication policy. Delivered #7564/#7576 and the current
physical-environment consumer PR #10438 do not establish the complete monotonic
source-selection/native-apply requirement. This is read-only classification;
no downgrade was attempted and no source claim or held ownership was cleared.
The existing managed-deployment, source construction, first-entry and lifecycle
owners remain canonical while this workstream selects its next unconflicted
consumer/recovery unit. The parent stays Active.

### 2026-11-04 - Windows venue release and remaining construction boundary

The exact Dev commit for #10456 passed CI 41855027461, then promotion
41857182391 qualified `affb395f47114f88671c4aecd75c17b1000b74bd`.
Release #10463 merged as Main
`5861bb058f0905d2f0dab16f7d1e56684b8f8b6b`. The canonical preparation
and registration bodies, maintained vendored copy and native regression have
identical source/release blobs. Bridge's materialized resolver copies match
canonical source too. Released Bridge is `0.9.299-dev1`, Codespaces
`0.5.250-dev1`, Containers `0.2.198-dev1`, Worktrees `1.24.458-dev1`
and Customizing Copilot `0.2.63-dev1`. #10451 is closed at its bounded
source/ordinary-contained-consumer contract; #10377 and other acceptance
owners remain open. No runtime was installed or activated.

Current source-wheel reconciliation preserves #9290's existing owner.
The delivered #9572 correction excludes changing Job ancestry from the cache
binding while retaining it for owned children. The distinct lifecycle and
publication-envelope correction remains claimed; no competing patch is taken.
Four existing native constructor controls pass in 187.69 seconds under the
unchanged 180-second item and 300-second group bounds: fresh-workspace
per-project reuse, changed-leaf invalidation, request-witness/Job key stability
and meaningful build-environment invalidation. Actual UV uses a synthetic
backend and loopback fixture feed. This is neither varying lifecycle-ticket
qualification nor a full ordinary-update, real-feed or latency pass.
The remaining owner/handle request is
[comment 6635053007](https://github.com/ThomasMichon/copilot-extensions/issues/9290#issuecomment-6635053007).

The original immutable-UV contribution #9501 is merged and contained in that
qualified release source; its exact-head native bootstrap/UV results remain
the original contributor's evidence. Fresh-entry, existing-queue and public
Python prerequisite work stay with #7743/#7758/#9448/#10186 rather than a
second repair entry. #3004's first actual restarted-model prompt is distinct
from #1159's completed stateless write guard. Source, containing release,
contained consumers and affected-host readiness remain separate facts. The
remaining selected Plan/Validation items and original holds are unchanged.

### 2026-11-04 - Bounded automatic launch-freshness source unit

The retained #2404 scope is claimed under #7030 in
[comment 6640613933](https://github.com/ThomasMichon/copilot-extensions/issues/7030#issuecomment-6640613933),
as U11 in the acceptance matrix. ROOT coordination, first-use adapters and the
existing native application's external-cache correction remain with their
declared owners.

Read-only inspection found inconsistent decisions: reconciliation compared
against the serving version preferentially, staging treated version inequality
as apply-worthy, and pre-launch/native execution admission did not share the
source/freshness decision. The candidate now uses one read-only guard, preserves
independently selected sources and both installed/serving freshness, and compares
prepared facts again at actual apply. Existing publication, context and root
admission remain independent. Explicit update/recovery is not gated by this
automatic-action decision.

The eighteen new consumer regressions (including four real child-process
protocol controls) and all 106 retained publication-policy cases pass.
The combined file exceeded its unchanged 300-second group limit;
the same complete case inventory passes in three disjoint selections. The
staging/monotonic compatibility packet passes 75 cases after replacing only
affected shorthand test versions with real package-version spellings. The
durable launch consumer packet passes 53 cases with one explicit platform skip.
Ten actor-free decision units, Python lint and the thirteen-plugin install,
documentation and version guards also pass. Exact whole-packet unit admission,
review, merge and containing release remain pending. These isolated source
controls do not establish native installed adoption or close #7030, #7028 or
this parent effort.

The declared enforced MyPy portfolio subsequently passed all forty strict
projects for both Linux and Windows type targets, after explicit preparation
of its missing checker environments. Worktrees and repository tools remain
pending projects; this does not claim their typing admission. Tracking-write,
headless-launch, daemon and asyncio-timeout source guards pass.

The complete normal unit packet remains locally unqualified. All 4,086 selected
tests passed, but the first run exceeded the unchanged 300-second aggregate
budget. A second run without another owned validation job still failed aggregate
accounting: pytest took 287.63 seconds, with admission, source-bytecode
preparation and temporary teardown also inside the packet clock. No source,
node selection, coverage or budget was relaxed. Missing tooling was prepared
through the seasoned dependency owner; UV-owned tooling outside the checkout
preserved the source-read boundary. Required exact-head hosted CI must qualify
the packet before this source unit can merge. These local budget failures are
not represented as successful unit evidence.

PR #10508's initial head `418da023bcc85f9189e5422040b88bc3d0efa88b`
received actual hosted qualification in run `41912963474`: all 4,086 units
passed in 95.477 seconds with coverage recorded once and fresh evidence
uploaded. This is separate from the failed local Windows aggregate runs.
The required gate remained red because this slice grew `update_cli.py` to
1,001 lines. Moving the new pure-helper import to the normal module import
section restores the existing 1,000-line cap without changing it or the
decision's behavior. The module-size guard, Python lint and all eighteen new
consumer controls pass after that correction. Current-head review and complete
required CI still precede merge; the successful old-head unit job is not the
new head's gate.

### 2026-11-04 - Launch-freshness review corrections qualified locally

Eight regressions reproduced all four substantive findings from #10508 before
correction: preexisting selector/manifest disagreement, successful owned
acquisition invalidating its own planned runtime action, omitted constructor
inputs, and comparison of a repair slot with a package version. The revision
uses the existing canonical selection primitive for versioned roots, retains
source/profile and exact package consistency, fingerprints owned construction
inputs separately, and defers successful acquisitions to the next normal
planning pass. Explicit update/recovery remains unchanged.

All 149 isolated consumer controls pass: forty new cases and 109 retained
cases, run in bounded disjoint groups. The retained inventory includes three
additional namespaced execution controls. Independent namespaced planning and
execution contracts now run separately; intentionally malformed JSON avoids
an unused lookup. Item and aggregate deadlines are unchanged. Earlier combined
and per-item timeout attempts remain failures, not successful evidence.
The final staging/monotonic compatibility packet passes 75 cases; all seventeen
actor-free units pass and their three admitted source hashes match. Python
F/E9 lint, six-source module caps, thirteen-plugin install contract,
documentation consistency, tracking/headless/daemon/asyncio source guards and
whitespace checks pass. The forty admitted strict projects' earlier Linux and
Windows type-target results still apply to unchanged admitted source; pending
projects are not newly admitted.

The canonical lookup is an actual read-only caller-owned subprocess, bounded
to fifteen seconds and 64 KiB per stream. Fixture completion/interpreter
observations are synthetic: the selected fixture interpreter is never run.
This proves selection and protocol behavior, not installed runtime health,
native lifecycle activation or whole-launch latency. No host rollout, external
venue or shared installation was changed. The previous failed local aggregate
unit runs and initial-head hosted success remain separate evidence.
Current-head hosted CI and a fresh review must qualify this revision before
merge; containing release is still due. #7030, #7028 and this parent remain
open with their existing native and construction owners.

### 2026-11-04 - External construction-input review correction

Exact head `2b739eb101ceb268a5b97b4ff5b3d53ac14e6bc3` passed required
CI 41935837433, including 4,108 executed units in 94.401 seconds with coverage
recorded once and fresh evidence uploaded. Review 5777656241 confirmed all five
prior findings resolved, but identified another input-closure gap: declared
local dependencies outside the payload were not fingerprinted.

Three actual cached-action controls reproduced that gap before correction.
The construction-only fingerprint now follows transitive declared local
sources, handles cycles, includes external file inputs, and refuses missing
declared inputs. It does not import dependency code, execute another process
or grant installer admission. Generated dependency environments remain excluded,
and ordinary staging keeps its existing curated fingerprint.

All forty-three new freshness consumer cases pass in 147.48 seconds of pytest
(191.17 seconds including preparation and cleanup). The staging/monotonic
compatibility packet passes 75 cases in 13.85 seconds (21.81 seconds total).
Python lint, unchanged module ceilings, documentation consistency and all four
source AST guards pass. The earlier 109 retained cases and seventeen pure units
are separate retained evidence; their admitted production sources are unchanged
by this correction. No test limit or required gate was weakened.
The revised head still needs its own hosted CI and passing review before merge;
the previous head's green result is not substituted. Parent, native acceptance,
release and rollout boundaries remain unchanged.

### 2026-11-05 - Python 3.10 construction-observer compatibility

Head `0d1df88228f1c67dc59aa2f02db4a0a7417db280` passed required CI
41938167466, but review 5777812790 correctly identified that importing stdlib
`tomllib` unconditionally broke the declared Python 3.10 floor. Three
missing-stdlib consumer regressions failed before correction. The observer
now falls back to `tomli`, declared as a conditional runtime dependency below
Python 3.11. The dev extra also supplies the real backport on newer test
interpreters; no supported Python floor or fallback failure is hidden.

All six direct/transitive/missing-input controls pass under actual CPython
3.10.22 in 23.70 seconds (84.88 seconds including canonical isolated preparation
and cleanup). Installed runtime metadata selects the backport on 3.10 and its
real import succeeds. After restoring the ordinary CPython 3.12 test environment,
all forty-six freshness consumer controls pass in 121.07 seconds (158.72 total),
including real backport parsing with stdlib TOML explicitly unavailable.
This is contained test-runtime qualification, not an installed-host update.

Lint, module ceilings, thirteen-plugin install contract and whitespace checks
pass. The Python-only dependency-age audit covers 541 declarations with zero
violations at the unchanged seven-day minimum. The local all-ecosystem audit
remains failed: four public npm metadata TLS handshakes failed with certificate
validation enabled. The attempted plugin-directory audit found zero declarations
and is not evidence; the repository-root Python audit is the counted result.
TLS and age policy remain unchanged. The new head must pass its actual complete
hosted declaration audit, required CI and fresh review before merge. Earlier
green heads do not clear those gates; parent and rollout boundaries remain open.

### 2026-11-05 - Launch-freshness source merged; containing release next

#10508 merged at 00:31:13 UTC as
`7ebeee4b2b46fcea8eb06b0df418651ca1f416b0`. Copilot review 5777948367
approved exact head `7f3f8e2920d9f0c754e3a94e84f9ac0dad48b1e2` with no
open findings; all seven threads are resolved. Required CI 41940407320 actually
executed 4,108 units in 54.401 seconds with coverage recorded once and fresh
evidence uploaded, plus the complete 545-declaration age audit with zero
violations. A description edit queued a later same-head run, so the first
managed merge was refused until 41940484664 also passed its required gate.
The configured review-rule exception did not bypass CI.

Thirteen authored paths, including every production/test body, have identical
reviewed-head and squash-merge blobs. Peer base changes are retained in the two
shared documentation files and unit manifest; this slice's complete documentation
sections, seventeen admitted nodes, changed source hashes and coverage-target
addition are preserved. Initial managed pull-forward auto-aborted a replay
conflict, leaving the approved tip and recovery ref intact. After proving the
clean tip and merged content, continuation skipped only those confirmed merged
commits; managed sync succeeded on the squash merge.

This completes the bounded launch source/freshness reservation on #7030 at the
source tier. It does not complete ROOT coordination, the native application/
cache consumer, source construction, ordinary whole-update or affected-host
acceptance. The canonical README has been reread in full and remains Active.
Containing release is the immediate next gate; existing entry, execution,
construction and lifecycle owners retain the other matrix boundaries. No
installed runtime, service, credential, queue or protected session was changed,
and no rollout is implied by this merge.

### 2026-11-05 - Launch-freshness containing release verified

#10542 published the canonical source milestone and U11 acceptance row to Dev
as `91091ecacc322ce3ccb1e244cbfd8541494e4926`, after exact-head approval
and green required CI. The canonical README was reread and remains Active.
Post-merge Dev CI 41941930339 also passed the source fix's actual merge.

Validation/promotion 41943616923 succeeded. Release #10548 merged at
01:02:40 UTC, publishing qualified Dev
`20c140f142b273ec132b8d740240ed0dd3135e02` as Main
`0adc4e2e8284bdbd72b8a99cbb4172841aac2088`, Worktrees `1.24.468-dev1`.
That actual qualified source contains #10508's merge. All six changed production
bodies match the fix, qualified source and released Main; the materialized
runtime dependency set retains the Python 3.10 backport. The earlier release
#10540 qualifies an older snapshot and is not substituted for this proof.
No test rerun or installed/runtime/service result is inferred from matching
released bytes.

This completes U11's source and containing-release observation, not #7030's
native adoption or this parent's remaining Plan/Validation gates. No updater
was invoked. Existing fresh-entry, ordinary interrupted-request/coalescing,
first-foundation and native application/cache owners retain their scope.
Read-only reconciliation found #10499's related report of five legacy Windows
startup reconciles losing their worker with an SSH session; it is a candidate,
not a claim or source change. Current source and overlapping ownership must
be reconciled before selecting any migration, preserving the Python-free
budget hook, source/action policy and existing execution owner.

### 2026-11-05 - Bounded MCP first-use prerequisite proposed

Broader first-use/bootstrap and matching-source reconciliation retains #9026's
Dispatch recipe, #9448's first-foundation work, #7758's ordinary admission/
recovery/coalescing owner and #4195's broader hook-spawn scope. The narrow
[public scope note](https://github.com/ThomasMichon/copilot-extensions/issues/10499#issuecomment-6647379317)
selects MCP's fixed `stamp`/`provision` prerequisite, not the whole five-plugin
migration or live queue repair.

The [MCP proposal](../source-managed-runtime-installs/mcp-first-use.md) extends
the existing finite recipe roster through its canonical foundation and queue.
Its actual initializer is `init`; borrowing another plugin's installer front
or service flags would not preserve that contract. Review and merge the plan
before source implementation, then qualify actual materialized native
Windows/Linux construction, installed consumers and original-caller loss.
Ordinary bootstrap adoption follows separately. No production code, runtime,
queue, service or host state is changed by this proposal, and the parent
remains Active.

### 2026-11-05 - MCP prerequisite review and first source controls

#10558 merged as `ac9ac7c03ef2e740871696755438434cd1ee9cd8` after
current-head approval and required green CI. The canonical README was reread;
this is a reviewed plan, not completed recipe or bootstrap adoption.

Four regressions failed against the unchanged source at unsupported MCP recipe
admission. The bounded adapter adds its actual init-backed actions to the
existing Python/native front doors and retained queue. Those four controls now
pass in 93.35 seconds under an explicit 60-second native item bound. The first
default-30-second receipt attempt timed out; its isolated stamp subsequently
passed in 29.94 seconds. The combined refusal packet also exceeded its
unchanged 300-second group limit and is being qualified in disjoint groups,
not reported as passing.

Native qualification also reconciles the plan's overly broad Python-free
wording with the existing setup-owned prerequisite amendment. Windows lifetime
admission requires supported `python` on PATH before a plugin runtime exists.
Preserve and test that current boundary; do not add another acquisition path
or claim a fully Python-free machine. The Python-free Budget hook remains
outside this MCP slice. Materialized native qualification, failed-candidate
preservation, source review, release and affected-host recovery remain open.

The latest declaration/protocol packet passes eight controls, including the
four lifecycle/CMD admission cases. Actual materialized missing-Python refusal
and corrected CMD stamp both pass. The first cold native attempt failed while
the fixture read queue status before observing a durable ordinary receipt;
foundation readiness alone was not a request-acceptance signal. The observer
now waits for that existing public receipt before querying its request and
still propagates schema/terminal failures afterward. This is a fixture
correction, not queue recovery or a relaxation of schema admission. Cold native
qualification remains pending under the same budgets.

The corrected cold observer's next run failed honestly at the unchanged native
foundation builder's five-minute owned-process deadline. Source wheel assembly
completed, but foundation construction did not finish and no ordinary MCP
receipt was accepted. This is neither MCP qualification nor evidence that a
larger test deadline would repair production construction. Preserve the actual
failure and existing foundation owner; run the explicit Windows/Linux recipe
portfolio against the candidate before merge. No constructor, queue, recovery
policy or process deadline was changed to obtain a passing result.

### 2026-11-05 - Hosted MCP cold path exposes missing UV handoff

Draft #10574 published the actual candidate, not an accepted native result.
Explicit run `41959758429` at `53b83d549022286da0abca0a26232ae808256af5`
passed all admission/protocol/refusal and materialized stamp/prerequisite
steps on Windows and Ubuntu. Both cold cases completed foundation and stamp,
then failed during provision because the legacy MCP initializer lacked UV
while its dependency-policy helper still required it. This is a real
acquisition handoff gap, separate from the earlier local foundation timeout.

Two focused regressions fail before the correction; the deferred-stamp
negative control already passes. The recipe now invokes the frozen payload's
existing canonical UV acquisition helper only for provision, supplies the
selected tool on PATH and stops before init on acquisition failure. The actual
MCP initializer, constructor, foundation, queue and deadlines are unchanged.
All three queue/native-process controls pass in 43.39 seconds of pytest
(74.31 seconds including preparation and cleanup). Eleven retained fixed
first-use controls also pass in 66.24 seconds (108.98 total) on the first
published head.

The revised candidate still needs actual Windows/Linux cold construction,
installed consumers and failed-candidate preservation, plus current-head CI
and review. The draft stays held. Parent and ordinary publisher adoption,
original-caller Job loss and affected-host recovery gates remain open.

Required CI `41961691461` also found that the new native portfolio's dynamic
runner matrix violated the existing trusted-CI contract. The workflow now uses
separate fixed Ubuntu/Windows jobs with shared steps and environment, following
the repository's existing pattern. The canonical trusted-CI checker passes;
both jobs retain identical commands, environment and 35-minute job budgets.
The guard and required unit-only selection are unchanged. This workflow
correction does not turn the still-pending native run into a pass.

Run `41961771318` at the UV correction completed as a failure on both
platforms. Linux progressed through actual provisioning, installed consumers
and healthy repeat, then its negative control expected an exception string
that MCP's health gate intentionally suppresses. The actual initializer
reported `Fresh runtime slot failed its health gate` and refused activation.
The fixture now requires that public health-gate diagnostic while retaining
the real incoming module failure and all old-selection/configuration checks.
Windows exceeded the unchanged 900-second aggregate envelope without a useful
stage report; no passing outcome or specific hung operation is inferred.

The test now emits a finite stage marker at each native transition, without
another polling process or changed deadline. Run `41963933663` qualifies
candidate `92551780178074204922f0e615cfd8214cc23ee3` through the corrected
fixed-runner jobs. Its outcome is pending; the source PR stays draft.

The fixed-job run completed with Linux passing its full recipe, cold
construction, installed-consumer, healthy-repeat, failed-candidate preservation
and native settlement portfolio. Windows completed cold stamp, then timed out
inside concurrent provision before any installed-consumer assertion. Required
CI `41963918779` at the same candidate passed source guards, eligible units
with coverage and the required PR gate. Neither result substitutes for the
remaining Windows native qualification.

The next diagnostic revision reports at most three 4 KiB active log tails per
minute during the bounded provision wait. The wait now refuses at ten minutes,
within the unchanged aggregate envelope; no timeout, process or storage limit
is raised. This is test-only progress reporting, not a production poller or
another execution owner.

The bounded diagnostic run `41966134747` again passed Linux and failed Windows.
Its Windows log shows that actual MCP construction, manifest publication and
module verification finished, but the enclosing native consumer rejected
CLIXML mixed into the forwarded error stream. A focused queue regression with
real native host/warning records reproduces that output-format defect.

A contained stock-PowerShell probe confirms that `-EncodedCommand` serializes
those streams even with explicit text input/output flags; the ordinary fixed
`-Command` form does not. The adapter now uses that fixed form while retaining
base64-encoded JSON path data, actual init, canonical acquisition and the same
queue custody. All three acquisition/stamp/refusal controls pass in 33.13
seconds of pytest (52.97 total). The unsuccessful text-flag attempt remains
failed evidence. Actual Windows native convergence still needs qualification;
neither completed installer output nor the passing focused test clears it.

### 2026-11-05 - Native functional pass and participant-handoff review gap

Run `41969056781` at `c4c7de194e4ed158516bccc451e2e1c46d4401ea`
passes the complete native recipe portfolio on both Windows and Linux.
Required CI `41969027369` and the later same-head `41969073865` also pass.
The draft hold was removed for review; no merge or release was inferred.

Review `5780027744` correctly identifies a high-severity production handoff
gap. The contained installer path bypasses native-driver admission, so its
functional pass did not prove that the real initializer would join the outer
request instead of submitting another request against its held lock.
Two actual-native-admission regressions fail before the correction, and the
explicit-root case independently reproduces the receiver's default-root
assumption after supplying the missing witness.

Windows MCP provision now explicitly re-exports the existing authenticated
participant witness and native re-entry marker. The shared native receiver
addresses the execution root carried in the bounded witness, authenticates
that original IPC owner, and retains exact running-request/backend checks.
No new root override, queue, protocol, launcher, installer or containment
exception is introduced. Invalid witness metadata remains a refusal.
Canonical synchronization updates only the declared helper copies.

Both materialized native-admission cases pass in 89.33 seconds of pytest
(115.19 total), with runner containment unchanged and exactly the original
queue request retained. Eighteen focused shared-receiver controls pass in
5.40 seconds (96.58 total), including malformed/oversized/missing-root/
foreign-root refusal and retained participant contracts. Current revised
native portfolio, required CI and a fresh review must still qualify this
correction; the earlier head's passing results do not close this finding.

The next native run `41972283777` passes Linux, including the new shared
receiver controls. Windows stops in the synthetic protocol fixture: it still
rejects every witness with exit 91, including the newly required legitimate
native handoff. The MCP-only fixture now requires witness plus re-entry marker
for Windows provision, keeps stamp witness-free and still rejects unrelated
lifecycle custody. Actual authenticated admission remains separately exercised
by the materialized native tests; no production check is removed for this
fixture correction.

The intervening review confirms the native custody finding resolved and
correctly requests that preservation cover MCP's real configuration directory.
The cold fixture now seeds a synthetic stdio configuration under the
installation's `bridges` directory instead of an unrelated home-directory
sentinel. Installed consumer checks load it through the real configuration
loader, require the actual configuration root and unchanged declared command,
and retain byte comparisons after healthy reuse and incoming failure. The
declared server is never started and no authentication provider is consulted.
The corrected Windows synthetic protocol packet passes five focused controls
in 91.10 seconds of pytest (117.45 total). Revised configuration preservation
still needs actual native qualification before completion is credited.

### 2026-11-05 - MCP fixed-recipe source merged; containing release next

#10574 merged at 05:39:20 UTC as
`322a4e5adebcd066a920ca52c5e808d41fe1d617`. Exact head
`5834222f8a6cab0b286d96bcd8582921169104a1` received clean Copilot
review `5780289807`, with no open findings and both prior threads resolved.
Required CI `41973744346` passed; the later same-head metadata-triggered
`41973863391` also passed before the managed merge. The configured
Maintainer review-rule exception did not bypass CI.

Native run `41973788482` passes every selected Windows/Linux step, including
the actual native participant handoff, shared receiver controls, real cold
construction, installed command/module/distribution and configuration loading,
healthy reuse, incoming-health failure preservation and native settlement.
The installed loader reads the unchanged synthetic configuration from the
actual `bridges` directory without starting a server or authentication provider.
Earlier failed runs remain failed evidence; their eventual corrections do not
retroactively qualify those runs.

All twenty-five non-shared authored files and the complete MCP testing section
match the actual squash merge; unrelated peer additions in `TESTING.md` are
retained. Managed pull-forward safely aborted a replay conflict. After verifying
the clean tip, backup and already-merged content, continuation skipped only
that confirmed merged range and managed sync succeeded on the squash merge.
No unmerged work was discarded.

The canonical README has been reread in full and remains Active. U12 records
this bounded source/contained-consumer result, not ordinary publisher adoption,
production caller-Job survival, existing queue repair or affected-host recovery.
Containing release is the immediate next gate; existing first-foundation,
execution/coalescing, MCP construction/application and broader hook owners
retain their remaining scope. No updater, host integration or rollout ran.

### 2026-11-05 - MCP containing release verified

Validation/promotion `41977870141` succeeded. Release #10626 merged at
06:18:46 UTC, publishing actual qualified Dev
`f21518183df46b54a1328ebdd2626c96aa62d28f` as Main
`19649c0bc538638cb5e58f8f16c96038b06acb8a`. That qualified source contains
#10574's merge. All four changed canonical implementation bodies match the
source merge, qualified Dev and released Main. MCP's packaged recipe and native
receiver match canonical source; its plugin/package versions agree at
`0.2.181-dev1`, with Worktrees `1.24.473-dev1`.

The preceding successful release #10617 qualified an older Dev snapshot and
does not contain this fix; it is not substituted. The normal scheduling filter
waited for the then-current Dev source CI before dispatching the newer gate.
No release-control change, force retry, capacity provision or updater was used.
The source journal/U12 milestone separately merged through #10613 after
current-head approval and required green CI.

This completes U12's bounded source and containing-release observation, not
ordinary publisher adoption, production caller-Job survival or whole-update/
affected-host acceptance. The parent remains Active. The next stretch
reconciles current MCP bootstrap ownership and the existing production-lifetime
qualification lane before selecting any ordinary-publisher adoption; no new
source claim or live recovery operation is established by this release record.

### 2026-11-05 - MCP ordinary bootstrap slice selected for review

Current #10499 and the matching open MCP/bootstrap/lifetime PRs were inspected
before selecting U14. No MCP bootstrap adoption PR was present. The bounded
claim is [comment 6652903046](https://github.com/ThomasMichon/copilot-extensions/issues/10499#issuecomment-6652903046);
existing Dispatch, first-foundation, ordinary recovery/coalescing, whole-hook
and MCP construction/application/publication/lifetime owners retain their scope.

The ROOT's [MCP bootstrap proposal](../source-managed-runtime-installs/mcp-bootstrap.md)
routes only legacy first stamp and drift provision through the released recipe
and original queue. It preserves MCP's actual init and selected-source readiness,
Windows' setup-provided Python prerequisite, automatic controls and independent
guidance. Review and actual proposal merge precede implementation.

The direct validation requires real hook deadline/output, installed configuration
and consumers, and genuine production submitting-Job/group loss through the
existing native lifetime owner with all outer cleanup boundaries retained.
U12's contained caller-process-loss evidence is not substituted. No production
hook was run, no source implementation or native acceptance is claimed, and no
shared installation, queue, credential, service or rollout effect is authorized.
The parent remains Active; the next gate is review of this bounded proposal.

The proposal subsequently merged through #10658 at 08:25:00 UTC as
`ff1ae2a9fb9a818dbd905af7e77c2ceff13f9017`. Its final head
`c43e52a4875ada22ff16b99594f74aacff5e663c` received genuine approval
`5781807321`, with no review threads and both required source CI runs green.
The preserving rebase retains the separate U13/#10652 fresh-settlement plan;
this bootstrap slice is U14. All six plan files match the merge.

The parent Plan and Validation Plan remain unresolved. The next source step
implements only the reviewed MCP bootstrap adapter, preserving the original
queue, real readiness and all incumbent custodians, before exact-candidate
hook and production-lifetime qualification. A plan merge is neither a source
fix nor native/affected-host acceptance; no updater or live repair ran.

### 2026-11-05 - MCP bootstrap functional result and native lifetime candidate

The local adapter implements the reviewed U14 proposal: fixed stamp/provision
handoff, real source-qualified readiness, preserved controls and guidance,
unchanged deadline/output and removal of this hook's direct installer/PID-age
custody. The actual Windows functional packet passes in 686.14 seconds. All
twelve original lifecycle records settle and final inventory is empty; restoring
the fixture's original source identity before canonical teardown fixes the
earlier test-only missing-runtime-root hold. That result is contained functional
and cleanup evidence, not production caller-Job loss.

The candidate now includes an explicit ephemeral hosted Windows/Linux lifetime
packet through the existing native owner. It holds the ordinary execution lock,
invokes the real hook, pins acknowledged owners, destroys every fixture caller
boundary while work is pending and requires completion without resubmission.
The existing restrictive shared-outer negative and qualified Windows desktop
witness remain separate controls. An explicit renewed lifecycle, bounded-child
controls, ordinary/coalesced work and exact cleanup remain required.

Seven local admission/filter/CLI controls pass; the bounded shared cold-environment
packet passes three checks and correctly skips the hosted-only native item.
Focused Ruff and the install-contract gate pass. No actual hosted result is
claimed yet. The new lane retains the existing item/step/portfolio bounds and
uploads only receipts and logs. Exact-candidate Windows/Linux native results,
implementation review/CI, merge and containing release remain open. The parent
Plan and Validation Plan remain Active; no updater or shared installation ran.
The live declaration-age audit checked 548 declarations but could not verify
four npm metadata reads because their TLS handshakes failed with certificate
validation enabled. It is not recorded as passing; no trust/verification
setting was weakened. Hosted CI must resolve that validation boundary.

The first hosted candidate, #10700 at `12b71e11466e`, fails run
`42016085082` before either production-lifetime step executes. Linux reports
five PowerShell repaired-readiness failures (37 passed, seven skipped); its
assertion truncates the subprocess diagnostic. Windows completes the actual
functional body in 208.10 seconds but fails strict teardown on one
`process-reused` lifecycle hold. The earlier twelve-settled local packet is
not substituted for this exact candidate's failed result.

The next diagnostic revision retains the Windows functional JUnit properties,
remaining original records and read-only native birth observations, and prints
the complete Linux subprocess error. It changes neither production cleanup
policy nor the all-settled assertion and does not skip the failing cases.
Both roots require diagnosis before another native acceptance claim.

The diagnostic run `42018348269` at `b7d9d68d25b7` identifies the Linux
failure: PowerShell resolves both `/usr/bin/bash` and `/bin/bash`, then tries
to invoke their combined `Source` array as one executable. The adapter now
selects the first application, retaining the existing native POSIX path.
Windows passes the functional packet and reaches native qualification, where
the generic control reports three scope identities: the runner's inherited
Job plus both fixture Jobs, not a missing caller ancestor. Scope validation
now requires the exact inherited prefix, the returned outer Job name and one
additional inner Job. All tracked scopes remain checked for daemon escape;
both fixture Jobs, bounded-control death, pinned-daemon survival and the
restrictive shared-outer negative remain required.

Nine local lane/scope guards pass, including inherited-scope preservation and
missing/reordered-boundary refusals. The Windows hook contract packet passes
42 cases with seven platform skips in 85.77 seconds; focused Ruff and whitespace
checks pass. These are source corrections, not native lifetime qualification.
The first run's original `process-reused` hold remains unmodified and is not
retroactively settled by the second run's functional pass. Exact revised-head
Windows/Linux proof, review/required CI, merge and release remain open.

Run `42020741107` at `b947f06d92f7` clears all functional packets on both
platforms, including Linux's previously failing readiness cases. Native proof
still fails before the actual MCP hook. The exact-scope check exposes an
incorrect attribution in the preceding diagnostic: the third Windows scope
comes from the leased intermediate's own expiry Job, not an established
runner Job. The fixture had launched a bounded intermediate through
`spawn_daemon(breakaway=False)`. It now uses ordinary headless `Popen`, retaining
the outer Job and unchanged inner caller-owned Job without introducing another
daemon lifetime. A regression rejects using daemon admission for that bounded
intermediate; exact inherited/outer/inner validation remains.

Linux's native entry creates `libs/update-driver/.venv` before materialization,
which refuses symlinks in canonical input. Its assertion did not print the full
materializer result. The hosted step now declares its UV environment under the
runner's temporary root instead of the canonical library, and the fixture prints
every materialization finding on refusal. This does not change materialization
policy or treat a skipped source as successful.

The same head's required CI fails the daemon census: the removed MCP
`Start-Process`/`nohup` sites remained listed and the new bounded MCP control was
unclassified. The census now removes those retired sites and classifies both
bounded fixture launches as caller-contained. Ten focused lane controls pass,
as does the actual materialized stamp consumer (55.26 seconds). Native
qualification and required CI must pass on the revised head; no failed result
is waived or credited as acceptance.

Run `42022964161` at `93066796f1b4` passes both functional packets and gets
past the preceding materializer and two-caller-Job failures. Linux reaches the
real hook and acknowledges its owner, then the fixture incorrectly requires
the log pathname in `/proc/<pid>/cmdline`; native launch redirects stdout to
that log instead. The identity check now compares the acknowledged live
pidfd-pinned owner's actual stdout descriptor with the unique acknowledged log,
with liveness checks before and after the descriptor observation.

Windows retains exactly both caller Jobs and an admitted daemon with a
different expiry Job. Its control incorrectly requires no Job metadata at all,
rejecting the daemon's own renewed-lease custody. It now rejects any retained
caller identity and checks the daemon's native nonmembership in every pinned
caller Job. It does not confuse an independent expiry Job with caller
dependence. Both fixture Job closures, bounded-control death, pinned-daemon
survival and restrictive-outer refusal remain mandatory. Neither failed run
is native acceptance; the revised exact-head lane still owes actual MCP work
completion and exact cleanup on both platforms.

Run `42024316058` at `b15bf45f37d9` fails Windows' functional coalescing
assertion, with all nine lifecycle records settled, and Linux's native item
deadline while waiting for its first ordinary receipt. Windows' two sequential
hook calls did not guarantee pending overlap. An attempted execution-lock
barrier also blocks the native foundation producer, so that local validation
times out and is not acceptance. It is removed rather than extending a deadline.
The functional and native assertions now require identical identities for
equivalent overlapping requests; distinct identities require durable proof
that the earlier request finished before the next was accepted. Actual
coalescing is recorded, not assumed for sequential non-overlap.

The Linux fixture pre-created its ordinary queue before cold foundation
admission. Both native producers require an existing queue to have its existing
foundation, so that setup entered the separately owned existing-queue recovery
boundary instead of genuine first touch. The unrelated ordinary request now
joins only the later provision cycle after the real cold hook establishes the
foundation. The actual caller-boundary destruction and post-loss completion
requirements are unchanged.

Failed/interrupted foundation state and an acknowledged owner exiting without
an ordinary receipt now fail promptly. Bounded owner-log tails are retained
before waits and on teardown; Linux uses the existing signal timeout so finally
and exact cleanup diagnostics can run. Item/step/portfolio deadlines are not
increased. Neither failed validation is credited as a source or native pass.

The corrected local Windows actual functional packet passes in 631.51 seconds
(660.66 seconds including preparation and cleanup). Hook times are
8.328/6.609/5.594/6.813 seconds against the unchanged 15-second deadline.
Installed consumers, healthy repeats, incoming-failure preservation,
source-independent consumers, all original lifecycle settlements and empty
final inventory pass. This remains contained functional evidence, not native
caller-Job/group proof. Ten lane guards, focused Ruff and whitespace pass.

A bounded npm-only metadata retry still fails all four public TLS handshakes
with certificate validation enabled. Normal PR CI skipped declaration age
because this diff changes no dependency declaration, so its green result is
not substituted for that audit. The existing CI workflow's explicit manual
dispatch audits all declarations and can qualify the hosted metadata lane
without changing a manifest or trust setting. Exact-head native results,
required CI/review, merge and release remain open.

Exact-head full manual CI `42029890331` at `c3a3024c1c64` passes, including
all 548 live dependency declarations with zero violations, source guards and
eligible units. That hosted result does not claim the local npm TLS problem
was repaired. Native run `42029879542` still fails both platforms; its functional
packets pass, while native lifetime acceptance remains open.

Windows' generic controls qualify two normal cycles and the restrictive outer
negative, with both caller Jobs closed, bounded controls retired and independent
daemons surviving. The desktop witness observes zero new windows/foreground
changes. The actual MCP owner then fails nested native admission. Source tracing
finds that disposable PowerShell shell custody always creates a restrictive
expiry Job, unlike the Python guard. The correction retains kill-on-close and
normal contained-mode prohibition, permitting breakaway only in the existing
explicitly non-contained mode. No caller Job limit, native nonmembership check,
external restriction or fallback policy is changed. Thirty-eight focused
custody/protocol tests pass, and a real contained Windows original-shell-loss
case still retires the exact helper and bounded child.

The canonical lifecycle archive is regenerated through its owner tool for all
consumers; an additional changefile names the four archive consumers not covered
by the existing eleven-consumer changefile. The MCP producer digest is refreshed
without a footprint contract-revision change.

Linux's retained diagnostics now show both cold callers coalesced to one
ordinary pending request, followed by worker-launch refusal. The restricted
native PATH omits the real `systemd-run` required under the host's cleanup unit,
and replaces native runtime inputs with the cold profile's directory. The
explicit hosted fixture now preserves that prerequisite and configured runtime/
bus inputs, without exposing product Python or installing a user manager.
An unavailable real scope remains a failure. An exited acknowledged owner with
an accepted-but-incomplete request also fails promptly rather than consuming
the whole item deadline. The prior Linux signal-timeout cleanup settles both
original lifecycle records; Windows retains four settled records plus one
`launch-outcome-unknown` hold. Neither historical hold is modified or waived.

Run `42034236248` at `a9b14b243917` qualifies the full Linux portfolio. Actual
native stamp/provision completes in 98.97 seconds after all caller groups are
destroyed; both cold callers coalesce, bounded controls die, acknowledged
owners survive and all six original lifecycle records settle with empty final
inventory. Hook times are 1.104/1.134/1.115 seconds. This is native Linux
caller-group proof, not another contained substitute.

Windows' native stamp also completes after both caller Jobs close, with two
coalesced callers and acknowledged-owner survival. The generic restrictive
negative and desktop witness pass; no visible windows or foreground changes
are observed. Provision then fails after runtime/command publication because
the fixture's credential-free OS map omitted `COMPUTERNAME`, which the existing
deployment metadata writer requires. All eleven original lifecycle records
settle. The map now preserves that required native metadata; the production
installer is not changed to hide a missing prerequisite. Full exact-head
Windows qualification, current CI/review, merge and release remain open.
