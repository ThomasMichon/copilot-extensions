# Installer Upgrade Recovery

- **Slug:** `installer-upgrade-recovery`
- **Repo:** copilot-extensions
- **Branch(es):** sequential bounded contribution PRs targeting `dev`
- **Created:** 2026-11-03
- **Status:** Active
- **Umbrella issue:** #10377
- **Related issues:** #9489, #7743, #9448, #7758, #10180, #6820, #7028, #7947, #6389, #5472, #10451
- **Vision:** [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)

## Guiding Intent

Drive high-impact installer/update compatibility failures to a verified usable
incoming installation through the latest supported explicit install/update
flow. Fresh-install success, a prepared candidate or a merged bug fix does not
prove that an affected older installation can recover.

This workstream closes the installer vision's existing authoritative replacement
intent; it does not extend it. Incoming readiness and every applicable current
selection define success. Old recovery protocols, provenance, stale installer
transactions and queue fences cannot veto explicit replacement. Actual incoming
admission, build, durable-state migration, permission, I/O and health failures
remain failures. Preserve user state and protected sessions; outgoing central
retirement remains independent of incoming completion.

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
  selectors, routes, launchers and registrations. Distinguish package identity
  from running generation and whole-set completion from partial progress.
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
