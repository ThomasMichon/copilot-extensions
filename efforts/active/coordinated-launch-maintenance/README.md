# Coordinated Launch Maintenance

- **Slug:** `coordinated-launch-maintenance`
- **Repo:** copilot-extensions
- **Branch(es):** managed per-slice PRs against `dev`
- **Created:** 2026-10-21
- **Status:** Active
- **Vision:** `visions/installer/README.md` (adopted out-of-band automatic maintenance); `visions/picker/README.md` (responsive session launch)
- **Umbrella issue:** #7694

## Guiding Intent

An already-healthy session must not wait for unrelated runtime installation.
Keep adopted automatic maintenance available, coordinated and observable rather
than disabling updates or starting an independent updater for every launch.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Launch driver | Design, implementation, review, release and qualified acceptance | Managed source `welded-circuit`; #7694 |
| Broker repair | Follow-up #6859 bug and native qualification prerequisite | Managed source `whirring-doohickey`; same launch driver |
| Broker design | Source-only lifecycle amendment and review | Managed source `tinkling-cylinder`; same launch driver |
| Normal-process follow-up | Direct spawn/Job guidance and runtime qualification; no deployment ownership | Managed source `rusty-buckle`; #9522 |

## Coordination

- **Topology:** sequential source PRs against `dev`.
- **Host (owns PRs):** Launch driver.
- **Delegates:** none.
- **Handoff:** canonical effort and #7694; no silent ownership transfer.
- #6812 retains formatting, telemetry and operation-cost audit ownership.
- #6820 retains historical-command migration and its paused constructor handshake.
- #7667 retains its contributor-owned installer/context implementation.
- #6237/#3336 retain release control. This effort does not authorize retries,
  installed mutation, service restarts or another concurrent updater.

## Context

The launch scripts stage a marketplace download but join it before handoff,
then run bootstrap maintenance and two plugin-reconcile passes synchronously.
Only one core installer branch is detached. Several healthy launches can
therefore wait behind slow installation; a detached child alone does not prove
coalescence, distinct-project delivery or survival of caller Job cleanup.

This closes the installer's existing adopted, out-of-band maintenance intent,
using the process-lifetime and operation-custody patterns. The original
no-new-service mechanism proved insufficient for the Windows lifetime guarantee.
The source-only broker amendment below supersedes that mechanism without
changing dependency policy or authorizing installed mutation.

**Current delivery:** scheduling-only #7788 merged into `dev` on 2026-10-23
as `85544de549b2c8c32541e845d61e04177df5199f`. It submits optional work
through the already-merged canonical execution
driver and does not change daemon admission, add a broker or introduce another
mutation owner. The canonical Windows implementation refuses a bootstrap or
resident still in any Job. A rejected worker must remain a visible durable
failure, never a successful acceptance or foreground-installer fallback; the
launcher can still hand off on its healthy retained runtime.

### Source, release and installed evidence

These are separate states; a selected version or installed file is not an
end-to-end behavior result.

| Boundary | Recorded state |
|----------|----------------|
| Checked-in source | #7788 merged into `dev`; its reviewed completion journal merged in #8207. The independent broker/cold-Worktrees repair #7833 merged as `e58ba45041ca533d4a97ebee478195ce23462eeb` on 2026-10-29 UTC. |
| Released source | Main `2aa36e8f4d853024c9f18b4309efb5e6e4fd2a8b`, promoted by #9486 through run 40798872550, contains both the scheduling change and the independent broker/cold-Worktrees repair. Worktrees is `1.24.338-dev1`; the three final runtime correction modules are byte-identical to reviewed head `e3e789d983`. |
| Observed installed consumer | One existing Windows consumer selected Manager `0.6.104-dev1` and Worktrees `1.24.191-dev1`. Isolated package/distribution origins were inside the selected slots with `win-amd64` interpreters. Both installed Manager launchers matched #7788 after normalizing line endings. |
| Installed execution-driver parity | The selected Worktrees executor and shared participant were not byte-identical to #7788; the difference has not been qualified as equivalent behavior. Do not infer parity from their version numbers. |
| Running acceptance | No new installed healthy-handoff, automatic completion, failure or fresh-first-use proof is claimed. Existing source-fixture evidence remains separate. |
| Rollout authority | These were read-only observations of existing installations, not an update performed by this driver or authorization to change other consumers. Existing deployment holds remain in force. |

The same driver retains independent ownership of #6859; #7833 is now merged.
#6918 is merged, and neither its historical author nor another PR's delivery
is a dependency of #7788. The immediate-Job inference and new Scheduled Task
design belong to that separate repair, not the scheduling diff. Its reviewed
source, native disposable-host qualification, generation retention, installer
integration and promotion are complete without a lifetime-check waiver.
Installed rollout and running-state acceptance remain held. Historical journal
entries below record the former
broader hold; the explicit scheduling-only decision supersedes that delivery
coupling, not the independent repair's technical gates.

## Request

Public-safe literal excerpt: "Installation is just dragging out and blocking
other worktree starts".

Settled direction (summary): use coordinated-background automatic maintenance,
not permanent update opt-outs; preserve the running installation and do not
start another updater as an investigative shortcut.

Confirmed effort slug: `coordinated-launch-maintenance`.

Subsequent operator direction (verbatim):

> 6918 is merged. 6859 is a bug; if we need it fixed, we should fix it

Design-scope form selection (verbatim): `source-redesign`.
The selected option was "Authorize the source-level broker redesign; keep
deployment separately gated". This authorizes source design and implementation,
not installation, task/service registration, a host update or live rollout.

Launch-overlap form selection (verbatim): `reuse-merged-driver`.
The selected option was "Rework our draft around the merged execution driver
(recommended)". Reuse #7860's canonical execution front door and retain only the
remaining off-handoff scheduling work plus the independent #6859 broker repair.
This does not expand deployment authority.

Native-probe form selection (verbatim): `disposable-windows-ci`.
The selected option was "Allow a disposable Windows CI experiment, including
synthetic task registration and cleanup". This permits only a credential-free
probe on an ephemeral Windows CI runner. It does not permit task registration
on shared hosts, host updates or deployment.

Merge-scope form selection (verbatim): `scheduling-only`.
The selected option was "Qualify and merge the scheduling change; keep the
broker redesign separately tracked and owned". This retains the canonical
execution driver's lifetime refusals and does not authorize installed rollout.

## Plan

### Normal-process follow-up (#9522)

Operator direction, verbatim:

> We need to do something that isn't the S4U task model, to achieve the same goal

The initial `interactive-token` selection was reviewed in #9523. Its candidate
#9526 did not satisfy native lifetime qualification and was closed unmerged.
The operator then superseded that mechanism, verbatim:

> The intent is that it's just a normal "detached process". Are those not possible to create anymore?
>
> Let's try using jobs and normal detached-process spawn. Stay away from COM and Schduled Tasks is possible. Avoid UAC needs
>
> Great. Update general process-spawn guidance to reflect your chosen pathways
>
> Drive this fix first and get it promoted to main

The following decomposition is **agent-recommended**. Deployment remains with
its existing owner; neither source work nor publication disables that hold.

- [x] Review the original activation proposal in #9523; retain its history and
  the failed #9526 qualification rather than calling that candidate delivered.
- [x] Land the general spawn-guidance correction first and verify legitimate
  promotion to Main, separately from any runtime change.
- [x] Amend and review the [normal-process direction](windows-launch-broker.md#normal-process-direction)
  before implementing the runtime repair: ordinary native headless/detached
  creation, explicit launcher-owned Job scopes and no automatic COM, Task
  Scheduler or UAC fallback.
- [x] Qualify the direct runtime boundary against all actual caller-owned
  cleanup Jobs, including bounded tools and the restrictive shared-outer
  negative. No blanket any-Job rejection or immediate-Job-only escape claim.
- [x] Exercise stopped-resident concurrent Manager recovery, preserve
  singleton/current-roster ownership, and land source through ordinary review,
  consumer changefiles and CI.
- [x] Record runtime release separately and return installed adoption to the
  existing deployment owner; no shared-host registration or rollout is implied.

The guidance-only #9530 reached Main through #9531 as
`d93a7fd482561760201cb479f27e1622f1dd1506`; this is not the runtime fix.
The operator subsequently selected `owned-cleanup-scopes`, explicitly limiting
the native runtime lifetime guarantee to every tracked launcher-owned Job
rather than arbitrary unknown external supervisors. The concrete
[ownership mechanism](windows-launch-broker.md#selected-runtime-ownership-contract)
retains the restrictive shared-outer negative and the separate deployment hold.

Runtime candidate #9542 qualified source
`96feb3a0396f6d38f8f45b07d636c9f20aca3f9d` in
[run 40856274752](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40856274752):
two native Python survival cycles, the restrictive shared-outer negative,
two native PowerShell post-caller owned-work cycles, actual cold Worktrees
and Manager construction, and two concurrent stopped-resident Manager
current-roster cycles all passed. Every reader Job closed before resident
health rechecks and canonical shutdown. Automatic handoff preceded the held
installer gate by 1.437 seconds. All three native installer-retention controls
and 211 finite-removal cases passed, with five platform skips; POSIX passed.
This is not a release or installed-host rollout.

Further hosted qualification isolated a discarded operating-system
prerequisite in the credential-free fixture: its environment omitted
`PSModulePath`, causing cold native PowerShell command discovery to stall.
Restoring only that native module path preserves credential filtering and
the fresh private profile. Run
[40869393791](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40869393791)
passed all 18 native admission/cleanup controls, including opaque argument
fidelity, and both production post-caller owned-work cycles. Neither the
30-second caller limit nor the 15-second handshake limit changed. The
unsuccessful resume, serialization and progress alternatives and temporary
diagnostic probes are removed. A separate live-supervisor artifact test now
uses the store's existing bounded lock acquisition instead of racing renewal.
The final clean runtime source `f0223457b60fc4b79ff19ec535f15bdd0263f7be`
passed all three jobs in
[run 40870443339](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40870443339):
18 native admission/cleanup controls, two native PowerShell post-caller
owned-work cycles, the nested-Job Python survival/refusal packet, actual
cold Worktrees/Manager construction, and two concurrent stopped-resident
Manager/current-roster cycles. Every caller Job closed before same-resident
health rechecks; canonical shutdown settled cleanup. Automatic handoff
preceded installer release by 1.297 seconds. All three native installer
serving-slot retention controls, 211 finite-removal cases and the POSIX
packet passed (five finite-removal platform skips). Ordinary review,
source merge and Main release remain open; no installed rollout is implied.
The concrete ownership contract received independent design critique before
runtime implementation.

### Phase 1 - Reviewed contract
- [x] Land this plan through ordinary review before implementation.
- [x] Coordinate the bounded scope on #6812/#6820 without taking over their owners.

### Phase 2 - Coordinated maintenance

The following implementation choices are **agent-recommended** mechanisms for
the requested coordinated-background behavior.

- [x] Review the [independent Windows broker amendment](windows-launch-broker.md)
  before implementing its lifecycle and consumer integration. No deployment is
  implied.
- [x] Transfer the independent broker repair, its installer integration and
  lifetime qualification to separately owned #6859/#7833 under the reviewed
  [broker contract](windows-launch-broker.md). They are not dependencies of the
  scheduling-only #7788. The repair's reviewed source and disposable-host
  qualification and promotion are now complete; separately authorized
  installed rollout and acceptance remain open.
- [x] Move optional stage/bootstrap/reconcile work off the healthy handoff path
  on both PowerShell and Bash; remove the join and inline-download fallback
  from ordinary launches. Preserve explicit blocking recovery behavior.
- [x] Use one bounded on-demand owner and durable pending requests. Coalesce
  equivalent requests without losing a different project, repository, source,
  installation context or stronger requested operation.
- [x] Share operation custody with explicit update/reconcile entrypoints;
  retain canonical installer locks and execution admission. A busy owner is
  pending work, not permission for another installer.
- [x] Preserve exact action argv, scoped environment/unsets and project
  context. Re-plan against the current installed state rather than execute a
  stale or foreign action. Keep runtime replacement and retention safety.
- [x] Use the canonical headless lifetime primitive, explicit logs and durable
  working directory. No inherited-Job fallback presented as durable success;
  preserve contained-test suppression and bounded child-tree cleanup.
- [x] Make queued/running/failed/completed outcomes inspectable and truthful.
  Interrupted work must not be reported complete or retried over a possibly
  surviving child without ownership/completion evidence.
- [x] Preserve `WORKTREE_NO_UPDATE` and `WORKTREE_NO_RECONCILE` independently,
  explicit update behavior, clean JSON/stdout and required first-use safety.
  Missing prerequisites cannot be called a healthy launch merely to avoid waiting.

### Phase 3 - Release and acceptance
- [x] Land source, documentation and changefiles through review and required CI.
- [x] Observe legitimate promotion of the independent repair #7833.
- [ ] Use only the separately authorized normal rollout owner; do not start a
  competing update to finish this effort.
- [ ] Record installed handoff, maintenance completion and failure evidence.
  Close this effort only when every validation item is resolved or explicitly
  transferred to a named tracked objective.

## Validation Plan

### Normal-process follow-up (#9522; agent-recommended)

- [x] Guidance surfaces agree on normal native creation, independent visibility
  and ownership choices, retained cleanup-Job proof and explicit unsupported
  external restrictions; no unsupported runtime delivery claim.
- [x] Runtime controls distinguish launcher-owned Job dependence from arbitrary
  Job membership, retain bounded descendants and refuse unproven escape from a
  shared outer cleanup owner.
- [x] Disposable native Windows qualification proves direct creation, exact
  native child custody, survival after every fixture-owned caller Job closes,
  the shared-outer negative, bounded cleanup and headlessness without COM,
  Task registration, UAC or modified external Job limits.
- [x] Actual concurrent installed Manager readers recover a stopped resident and
  obtain one current roster under the unchanged deadlines and election owner.
- [x] Shared portable regression, consumer materialization, release changefiles
  and required review/CI pass; source qualification is not shared-host rollout.

All detailed scenarios below are **agent-recommended** acceptance checks.

- [x] Unit tests cover queue equivalence, distinct contexts, stronger requests,
  owner contention, crash/ambiguous-child handling and truthful terminal states.
- [x] Real shell fixture holds an installer behind a gate while two healthy
  launches reach handoff before that gate opens; maintenance remains automatic
  and single-owned. Measure handoff independently of installer completion.
- [x] Prove a different project's pending request is eventually processed,
  and manual update/reconcile cannot overlap its mutation boundary.
- [x] Exercise exact argv/environment, admission refusal, malformed plans,
  failed spawn, failed installer, opt-outs and explicit blocking mode.
- [x] Prove first-use/missing-prerequisite handling in an isolated fresh home
  (actual cold Worktrees/SSH/Manager construction in run 40783189911).
- [x] Transfer Windows independent broker activation, all-caller-Job survival,
  native control/desktop observation and installer lifecycle qualification to
  #6859/#7833. The scheduling change retains existing strict admission; verify
  that its refusal produces no accepted receipt or foreground fallback.
- [x] Exercise native POSIX handoff, owner exit and pending-request delivery.
- [x] Run targeted contained suites, lint, install-contract and source guards;
  record native/live lanes separately from mocked wiring.
- [ ] Verify a promoted installed release through the normal authorized flow;
  a payload version or queued receipt alone is not installed/running proof.

## Proposal

The scheduling change adds no service, periodic updater or maintenance queue.
The independently authorized Windows broker redesign remains a separate
tracked objective. The launcher submits optional
work to its installed owner's bounded coordinated maintenance path and proceeds
on the healthy retained runtime. The owner drains admitted requests using the
existing planners/installers and then exits. Coordination must cover explicit
mutation entrypoints as well as launch requests; a lock around only the new
worker is insufficient. Machine-local request/status/log data never enters
repository history.

## Journal

### 2026-10-29 - Verify legitimate normal-process runtime release

Automatic promotion
[40901961398](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40901961398)
passed candidate composition, source units, first-party artifact construction
and attestation, exact candidate reuse, immutable source publication/readback
and non-administrator exact-head merge. Generated #9554 merged at 15:41:31 UTC
as Main `74285132aef3f69ba75a139d11e55655486b8739`. Its promotion receipt
names the actual #9542 Dev merge `5dc91c68a41b83524e431933985bf3be9a4869f4`.
No manual Main merge, administrator release bypass, forced retry or parallel
controller was used.

Read-only Git object comparison confirms all 16 changed runtime source blobs
are identical to the reviewed Dev source, along with 144 materialized
`agent_procutil` copies. Released Worktrees is `1.24.349-dev1`, Manager is
`0.6.206-dev1` and Bridge is `0.9.212-dev1`; all 16 affected consumers have
their pipeline-stamped release versions. This verifies generated Main content,
not ancestry of the original Dev commit.

The normal-process source/release follow-up #9522 is complete. Installed
adoption and promoted running-state acceptance remain with the existing launch
driver under #7694 and #6859; this parent effort stays Active. The normal
unified update requires that owner's separate authorization. No update,
registration, service restart, updater replay or deployment-hold change was
performed or authorized by this source session.

### 2026-10-29 - Merge qualified normal-process runtime repair

#9542 merged to Dev as `5dc91c68a41b83524e431933985bf3be9a4869f4`
at 15:25:21 UTC through managed completion. Final-head review 5700238656
approved `6bbab26ffb7b07a264aa2d0eeae0cd5be5d6a1ca` with no open
issues; all four inline threads are resolved and required CI 40898700235
passes. The Maintainer review-rule exception omits only the additional
CODEOWNER vote, not review assessment or required checks.

Runtime/test source `9977600e626cb4c7b7838c0747382ec74daba06c`
passed all three jobs in native run
[40895126840](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40895126840):
20 native admission/cleanup controls plus three metrics controls, two actual
first-use logging controls, four packaged-Bridge controls and both native
PowerShell post-caller owned-work cycles. The actual cold Worktrees/Manager
consumer passed two Python survival cycles, the restrictive shared-outer
negative through a contained replacement-environment intermediate, and both
concurrent stopped-resident current-roster cycles. Every caller Job closed
before same-resident health checks and canonical shutdown. Handoff preceded
installer release by 1.547 seconds. All three native serving-slot retention
controls, 211 finite-removal cases and the POSIX packet pass; five
finite-removal cases retain explicit platform skips. Deadlines are unchanged.
Final head `6bbab26ffb` changes only the Bridge test-source fingerprint and
its nonsemantic reason; no runtime, fixture, workflow or frozen evidence bytes
changed after qualification.

The shared library passed 884 controls with 181 platform/environment skips,
the packaged Bridge passed four controls, and installation contracts,
materialization, lint and contribution-scoped size checks pass. These are
source and disposable-host proofs, not a shared-host rollout. Ordinary
review and Dev merge are complete; legitimate Main promotion and exact
released-source verification remain the next slice. The existing deployment
owner retains installed adoption and running-state acceptance under #7694
and #6859. No shared-host update, registration, restart or updater replay is
authorized.

### 2026-10-29 - Prefer normal detached creation and correct guidance first

- The operator superseded the interactive-task candidate with ordinary native
  detached/headless spawning and explicit Job ownership, avoiding COM, Task
  Scheduler and UAC. #9526 was closed unmerged; its failed native run
  40826195814 remains failed qualification, not a shipped repair.
- A bounded direct native fixture created a headless child with breakaway from
  an explicitly breakaway-enabled launcher Job. The child remained associated
  with some other Job but survived closure of the actual launcher-owned Job.
  A nested different-inner/shared-outer negative remained in the restrictive
  outer cleanup Job and died when it closed. Both fixture children were settled.
  These are controlled native observations, not whole Manager qualification.
- The requested first slice corrects the general skill, ambient process kernel
  and Windows pattern without changing runtime implementation or deployment.
  Main promotion must be observed before treating that guidance as released.

### 2026-10-29 - Propose existing-token desktop activation

- Claimed #9522 after the operator rejected S4U and selected an existing
  interactive token as the replacement availability contract.
- Reuse the canonical finite broker, installer, native custody and qualification
  lanes from #7833; do not add another launcher, supervisor or deployment owner.
- Plan-only proposal. The existing deployment inhibition and installed adoption
  hold remain intact; no registration, source implementation or rollout is
  claimed by this entry.

### 2026-10-29 - Verify legitimate promotion; retain installed acceptance

The green merged Dev CI 40798342150 is followed by the legitimate current-Dev
admission of `c5f12192e0e9143a80e8d52311d0c1bea3253a9d`. Automatic pipeline
40798872550 passes source units, first-party artifact construction and
attestation, exact candidate reuse, immutable source publication/raw readback
and non-administrator candidate merge. Generated #9486 merged at 01:23:03 UTC
as Main `2aa36e8f4d853024c9f18b4309efb5e6e4fd2a8b`.

Read-only Git blob comparison confirms the released `worktrees_client.py`,
`runtime_dev_cli.py` and `update_driver_json_cli.py` exactly match reviewed
head `e3e789d983de4bb6c5f09b0f6bcec6e830420aeb`. The released Worktrees
manifest is `1.24.338-dev1`. This uses the actual generated promotion and
content identity, not ancestry of a Dev commit on regenerated Main.

Earlier run 40796936253 failed only at source-release readback when the
Public Good Sigstore verifier could not initialize after its existing bounded
retry. The literal failure is recorded on #9484. The new, automatically
admitted current-source run passes that boundary without policy weakening,
forced retry, administrator merge or a concurrent release controller; this
does not assert a permanent fix to the earlier external initialization error.

The promotion observation is resolved. The effort remains Active because
installed handoff, automatic maintenance completion/failure and promoted
running-state acceptance still require the existing owner's separately
authorized rollout. No installed version, selected payload, source merge or
disposable-host proof is substituted for that acceptance. Shared-host update,
Task adoption, restart, updater replay and incumbent/custody retirement remain
held. The next slice is owner-authorized installed rollout and acceptance,
not another updater started by this source session.

### 2026-10-29 - Merge the qualified broker and cold Worktrees repair

#7833 merged through managed `pr-merge --now` as
`e58ba45041ca533d4a97ebee478195ce23462eeb` at 01:06:15 UTC. The provider
confirmed the merge and the effective self-merge profile; the ordinary
Maintainer review-rule exception omitted only the additional CODEOWNER vote,
not required CI, review assessment or unresolved threads.

At the final reviewed head `e3e789d983de4bb6c5f09b0f6bcec6e830420aeb`,
required CI 40797280463 passes. Review 5691634979 is `COMMENTED` with no open
findings, and all ten review threads are resolved. Its remaining overview
request for exact-head native qualification is satisfied by 40797339663:
portable/native POSIX, Windows activation/shared-outer negative and desktop
controls, and actual cold Worktrees/SSH/Manager concurrent-reader recovery
with caller Job closure, resident health and canonical removal all pass.
The consumer job takes 7m35s within its unchanged 15-minute cap.

The source-repair gate in `windows-launch-broker.md` is resolved. The next
slice is the legitimate promotion of this merged snapshot, then a separately
authorized installed rollout/acceptance under the existing owner and holds.
The parent effort remains Active. No shared-host update, Task adoption,
restart, updater replay, incumbent retirement or unresolved-custody cleanup
is authorized by this merge.

### 2026-10-28 - Preserve the concurrent reader's initial unready response

Required CI 40795693816 and all three native lanes in 40795747288 pass at
`9c603633d6`. Fresh review 5691542008 has no open findings and acknowledges
the preceding two fixes, but its overview calls out the concurrent-reader
shape. Direct assessment confirms the same error class on the initial read:
another caller can reach the authenticated resident after launch but before
its first publication, without entering this caller's recovery loop.

The strictly validated initial `unready` response now retains retryable pending
classification when no advisory roster exists. It does not call ensure/start
again. The four response controls now exercise both initial and post-launch
recovery reads; malformed, incompatible and superseded live replies remain
terminal. Neither the half-second recovery deadline nor the two-second
full-roster fixture cap changes.

The complete 119-case client/feed packet passes in 30.08 seconds including
preparation and cleanup with the existing import-timing diagnostic enabled;
the three actual CLI entry variants separately pass the unchanged cap. Two
earlier uninstrumented full-packet attempts fail the module entry's initial
two-second cap, observing first completion at 2.227 and 2.064 seconds. Those
are failed timing evidence, not successful cold-start measurements; no broader
native cold-performance claim or budget waiver is made. The final source
correction still needs its own hosted qualification, CI and fresh review.

### 2026-10-28 - Keep dev release inside retained foreground custody

Review 5691343499 identifies a real immutable-launcher mismatch: Windows
release invoked from `versions/dev` cannot use the numbered-slot bootstrap.
The installed public entry now retains its selected slot throughout dispatch
and settlement. Only foreground `dev-release` from this installation's exact
dev slot constructs the direct isolated interpreter command. The existing
finite driver's normal caller-owned executor drains it synchronously, with
the same queue, participant witness, native settlement and JSON result.
Neither another worker nor a new lifecycle owner is introduced. Generic
bootstrap refusal of mutable dev, numbered-slot dispatch, original-owner
validation and original-only dependency restoration remain intact.

The canonical Windows packet passes 74 controls in 120.06 seconds including
preparation and cleanup. Two uncontained disposable dev-venv cases execute the
actual generated command through the real driver and native child custody,
covering both success and the original nonzero JSON result; a deliberately
refusing immutable bootstrap and forbidden background-worker call prove
neither path was used. Public-entry controls cover lease lifetime and failed
retention before dispatch. Invalid command, slot and detach attempts never
enqueue. Lint and all 13 installation contracts pass.

This is a finite installed-command boundary, not a full native dev build and
rollback, interactive Picker launch or shared-host rollout. Final hosted
broker/consumer qualification, required CI and a fresh review remain open.
No shared-host installation, adoption, restart or updater replay is authorized.

### 2026-10-28 - Preserve validated unready recovery and portable AST identity

The reconciled all-three run 40792231745 passes at `a18b633de8`, including the
actual installed Manager reader and canonical removal. Required CI identifies
a Python-version difference in AST rendering rather than a changed installed
actor: Python 3.13 omits optional empty lists by default, while the required
Python 3.12 lane includes them. Fingerprints now retain those fields explicitly.
All 202 loader controls pass, and the actual Python 3.12 interpreter admits
both pinned actor bodies without weakening the exact-body boundary.

Review 5691288450 correctly identifies a second first-publication interval:
after admitted launch, a reachable resident can return a well-formed `unready`
error before its first roster exists. Validation now preserves that distinct
type; only an already-admitted recovery continues polling within its unchanged
half-second deadline. Malformed, incompatible and other live errors remain
terminal. Four explicit first-reply controls cover that distinction through the
CLI error frame and missing advisory path. The full roster/feed packet passes
115 tests in 19.29 seconds including preparation and cleanup. An earlier run
missed the guarded CLI's two-second startup boundary while another test command
shared its rebuilding environment; the isolated warmed rerun retains every
deadline and passes. The earlier failure remains non-passing evidence.

These source corrections still require final hosted qualification and CI.
Review 5691343499 additionally identifies the Windows `dev-release` invocation
using an immutable numbered-slot launcher from `versions/dev`; that lifecycle
entry must be corrected and qualified before fresh review or merge. Shared-host
rollout, adoption, restarts and updater replay remain unauthorized.

### 2026-10-28 - Reconcile the current publication base before CI

The provider reports a real merge conflict after the latest gate fixes, so
ordinary PR CI cannot start. Preserved `886bd22590` on a local backup before
rebasing the owned branch onto Dev `cc63cf74cc8bcea4c1afc27324e818e9b9f5bbc8`.
The only manual conflict is the generated Worktrees installer fingerprint;
the canonical sync owner regenerates it from the merged installer, retaining
the upstream native setup/callback and literal pane-environment changes.
All 13 install contracts, 219 changed-module limits and exact release-helper
source admission pass. The actual broker/Manager consumer lanes must now run
again against the reconciled dispatch SHA; the prior run remains evidence
for its own functional generation, not this new one. Required review, CI,
merge and promotion remain open, with all installed-host holds unchanged.

### 2026-10-28 - Preserve exact release admission and historical contract sources

Hosted run 40787588118 passes all three selected lanes at
`23e7fca3d73d002feb75f946397d0e4770a6661b`, including actual cold Manager
construction, two concurrent stopped-resident roster readers, survival after
all caller Jobs close, serialized maintenance and canonical removal. This
qualifies the latest functional corrections, not a full interactive Picker
session or an installed shared-host rollout.

Required CI 40787575877 found two remaining metadata/admission boundaries.
The release helper checker now admits only the two named installed-slot actor
functions through complete signature/body AST fingerprints and unchanged
canonical import bindings. Other loaders and scopes retain their normal
source-edge checks. All 202 focused loader controls pass, including actual
actor bytes, changed path/execution/import refusals and additional loader
discovery. The exact manifest adds the 14 reachable source bodies and two
new controls, without removing nodes or changing its 300-second budget.

The retired Worktrees ZDD shadow remains absent. Its bridge contract source
record instead retains its original historical path with an explicit
byte-identical canonical relocation. The checker requires an independently
registered target, unchanged digest, absent old path and no relocation chain;
all historical fixture and provenance bytes remain untouched. The full actual
registry validates all 814 fixtures. All 47 checker controls pass across the
complete packet and the corrected two-case hook-fixture rerun. The latter
fixture now includes the real canonical pre-push source it invokes, not a
replacement stub.

Local strict-packet execution reached 1,888 passing controls before a required
directory-symlink capability was unavailable on this Windows host; 15
platform-dependent skips also make that run non-qualifying. No privilege,
effect-admission or skip policy is weakened. The required Linux CI packet
remains the qualification gate. Unit preflight, targeted regression and real
registry invocation supply the lightweight consumer checks; no clean-room or
native deployment rerun is needed for these repository-only metadata tools.
Review, required CI, merge and legitimate promotion remain open. No shared-host
update, task adoption, restart, updater replay or rollout is authorized.

### 2026-10-28 - Address fresh review and preserve required unit admission

Fresh review 5690887170 on `1c6b66442b` accepted the five earlier corrections
and identified three additional High findings. The fixed foundation validator
now permits only the known optional `agent-zdd` sibling source for
`agent-procutil`, without widening its mandatory dependency recipe. Its
real-manifest regression and refusal controls pass with the complete 18-test
foundation input packet. Both Codespaces shell implementations now use the
copied/non-editable package recipe unconditionally, removing unbound legacy
`mode`/`Editable` references. Four native strict-PowerShell retry/fatal controls
pass; four POSIX controls are explicitly skipped locally and are added to the
existing bounded Linux integration lane.

Windows Manager apply-removal now joins the existing execution driver before
any daemon/removal custody and retains its participant through broker and
runtime deletion. Ordinary callers enqueue the fixed supported uninstall
invocation; executing participants remove directly under asserted custody.
Unsupported legacy declarations retain their structured refusal before loading
the driver. All 100 broker/removal controls pass with three platform skips.
Initial fixture failures were corrected by reproducing production source
initialization and retaining the existing policy-argument normalization.
Fresh hosted qualification remains required for these corrections.

Required CI 40784553719 also found a missing Fleet Manager shared-consumer
changefile and stale unit-source admission. The changefile is added. Refreshing
the eight changed source digests does not waive the unit packet's static or
runtime effect admission: the strict preflight now identifies two installed
broker runtime file-load boundaries as unsupported release-helper edges.
The existing checker has an exact-AST external-runtime seam for the canonical
installer actor, not a general exemption. The broker cell/retention boundary
must be modeled and qualified without weakening that rule before required CI
can pass. No merge, promotion or installed rollout is claimed.

### 2026-10-28 - Qualify the actual cold Manager pivot on reconciled source

Run 40783189911 at `c1800f38cc503bcc0bbd178feb0bfad745656cf5`
passed all three hosted lanes. Windows proved actual adoption, both native
child-transfer cycles, shared-outer negative topology, crash recovery and
generation retention, zero visible windows or foreground changes, and canonical
cleanup. The actual consumer lane passed cold Worktrees/SSH/Manager
construction, automatic maintenance and manual exclusion, plus both concurrent
installed Manager recovery cycles with every caller Job closed before resident
health observations. Handoff preceded the held installer release by 2.25
seconds. Its complete finite-removal packet passed 211 tests with five explicit
skips. Linux passed 98 process/lifetime tests with one skip and 74 actual finite
execution/participant tests with two skips. This is not a full interactive
Picker run or shared-host rollout.

The candidate was rebased onto `17c8272e76` with a durable backup.
Conflicts were a generated installer fingerprint, independent daemon-coverage
entries (both retained), and equivalent test formatting (upstream retained).
Canonical regeneration refreshed the fingerprint and three source-build copies.
The installed constructor and feed source changes did not conflict. The joined
materialization/fixture packet passed 159 tests with 15 explicit platform/link
skips. Its first attempt exposed an ignored stale Ruff-cache directory,
unstaged generated snapshot inputs and a missing host Python import; removing
only that owned cache, staging the actual regenerated inputs and using the
existing test runtime resolved all three without a product-code workaround.
The reconciled roster/feed packet passed all 111 tests in 100.84 seconds
including preparation and cleanup. Install contract (13 plugins), source AST
guards and contribution-scoped module size passed without baseline widening.
`TESTING.md` now documents the actual Manager lane and retry distinction.
Fresh review, required CI, merge and promotion remain open. Installed rollout,
the separate source feed work and unresolved custody retain their named owners.

Diagnostic run 40775390267 at `6690ace740` confirmed both actual Manager
readers reach instance reservation and enter broker admission, but neither
publishes a current roster within the existing 30-second budget. No admission
exception reaches the owned monitor log before reader exit. The next
credential-free hosted probe added bounded thread locations
(basename, line and function only, no source lines or frame locals) at failure.
Run 40777108354 at `b3381801aa` reproduced the failure; compiled package
filenames did not contain `site-packages`, so that location filter produced
no thread evidence. The revised disposable reader prints bounded basename-only
thread locations and projects only monitor instance state plus native identity
liveness. Absence of a failure log alone does not distinguish successful
admission from a blocked admission.

Run 40778723621 at `33d999ec0a` established an admitted, native-live monitor
and a second finished monitor while the Manager reader had no surviving loader
thread. The feed client exhausted the unchanged half-second roster recovery
window before the first durable projection existed; its generic
`ProjectionUnavailable` became a nonretryable `malformed-peer` frame, so the
Manager permanently abandoned a cold recovery. A distinct admitted
recovery-pending exception now crosses the existing feed boundary as retryable
`unready`; refused launches and malformed/live-peer failures remain terminal.
No deadline, broker lifetime proof or installation authority changed.
Run 40781121144 at `b4b22cfc5a62f3db9fc7fbea25a0888e6390a4d5`
first passed actual cold Manager construction and both installed `LiveLoader`
recovery cycles. The correction passed all 111 contained roster/feed tests.
Initial test-venv preparation exhausted the unchanged aggregate budget; the
completed warmed packet passed in 23.57 seconds including cleanup, not under a
widened budget. Shared-host installation and restart remain unauthorized.

### 2026-10-28 - Requalify joined source and exercise the stopped-resident reader

- Exact joined source `f13e7b1008fb1623e56aff6e3d8bf892a9c4dc1d` passed
  all three hosted lanes in run 40763408792: original Windows activation and
  negative topology, native/portable POSIX, and actual Worktrees construction,
  maintenance, manual-update exclusion and canonical SSH/Worktrees/Task cleanup.
- The desktop-startup intake additionally requires the actual Manager
  current-roster consumer, not just maintenance or a synthetic daemon.
  Add two concurrent copied-Manager reads through the normal installed engine
  with its resident stopped. Confirm one serving owner after both caller Jobs
  close, then use authenticated canonical shutdown and native exit confirmation;
  repeat from stopped state. This new consumer boundary remains unqualified
  until its own hosted result passes. It is not a full interactive Picker run.
- No shared-host installed change or retirement is authorized by these results.
  Source review/CI, release and eventual installed cutover remain separate.
- The first reader fixture at `f60dd5c524` failed in run 40765081012: it called
  the one-shot engine client instead of the Picker's live loader, so the first
  half-second CLI recovery read failed before the new resident published a
  roster. The fixture also omitted late-started resident cleanup on that error.
  Correct the boundary to the actual live loader, preserving its existing
  startup/reconnect policy and deadlines; capture an eventually published
  fixture-owned route for canonical shutdown on failure. No production recovery
  budget is increased and no advisory data is relabeled current.
- Run 40766979493 then exposed the fixture's missing Manager dependency
  closure (`rich`): copying Manager source into a Worktrees interpreter is not
  an installed Manager consumer. Construct Manager itself with its canonical
  installer and declared dependencies, verify the reader imports from that
  installed slot, and retain the same caller-Job and live-loader contract.
  Canonical Manager removal and absence of its additional owned Task join final
  cleanup. Do not patch the Worktrees environment with individual packages.
- The real Manager constructor at `434c79d709` failed in run 40768772886
  before enqueue: cold `request_context()` eagerly imported the normal engine
  client and its not-yet-installed `worktree_identity` dependency. Capture the
  existing environment allowlist without importing that client; preserve any
  already-consumed provider when the client is already loaded. A real isolated
  `-I -S` source-only subprocess now exercises the cold boundary, while the
  warm consumed-provider and credential-exclusion controls remain passing.
  Three focused Manager controls passed; fresh native construction is required.
- Run 40770648028 constructed Manager successfully, but the actual live loader
  still failed to obtain a current resident roster. Capture the bounded owned
  resident log tail on that failure to diagnose the native startup boundary;
  neither increase production timeouts nor substitute cached/advisory rows.
  Final cleanup also exposed that Manager's supported uninstaller removed
  runtime files without retiring its adopted broker Task. Remove that component
  under existing quiescent Manager custody before exclusive generation removal,
  retaining the action through native confirmation and refusing pending
  publication. Four focused ownership/order/preservation controls passed.
- Run 40773264612 confirmed canonical Manager Task cleanup, but the roster
  recovery still failed. The owned monitor log reached the caller-Job broker
  branch without a completed admission or the existing broker-refusal message.
  Add fixed phase/type/numeric-error diagnostics around reservation and
  unexpected admission failures, preserving the original exception and unknown
  custody. Do not log exception payloads, argv, IPC witnesses or credentials.
  Fifteen failure/settlement controls passed, including a no-payload-disclosure
  regression. This is diagnostic progress, not successful resident recovery.

### 2026-10-28 - Integrate the merged finite cleanup owner before release

- The operator authorized ordinary review, required CI, merge and promotion of
  #7833, while retaining the separate installed-update and shared-host rollout
  holds. The subsequent overlap decision selected merged #9253's cleanup owner
  plus the broker-specific custody requirements, with fresh qualification.
- Candidate `67614173467d7edcc676cc55da41119c384ca79a` reconciles current
  `dev` without retaining the superseded staged-publisher exemption. Generic
  teardown controllers, Darwin inspection, finite profiles, project-command
  ownership and explicit purge semantics remain owned by upstream cleanup.
  Native broker removal enters the same Worktrees policy adapter in process;
  it proves the fixed driver stack and actual teardown lease before broker
  removal and finite code deletion. Staged source retains the original project.
- Local finite cleanup passed 149 tests with five platform skips; workflow and
  synchronization controls passed 156 with 55 skips. The isolated native driver
  and in-process policy packet passed 34 tests. An actual selected-slot project
  cleanup probe passed after updating its extracted frontend fixture to include
  the real headless invocation wrapper. Install-contract and source AST guards
  passed; no module-size ceiling was widened.
- Native whole-uninstall positive controls on the shared machine refused an
  unrelated module consumer under tracked #8669 and were explicitly skipped;
  they do not qualify the joined candidate. The earlier real-consumer result
  below belongs only to its recorded source. All three hosted lanes must
  requalify the reconciled source before this draft becomes reviewable.
- No installed payload, runtime, Scheduled Task or shared-host service was
  changed. The temporary resident and inherited feed/recovery obligations remain
  held; the parent effort is Active.
- Joined-source run 40755951967 passed the native/portable POSIX lane. Both
  Windows lanes failed visibly: source assembly retained a newly upstreamed
  license-only nested ZDD shadow, and generic broker teardown still called the
  superseded `admit_removal` keyword. Remove the remaining shadow file while
  retaining canonical library licensing, and use upstream's `check_consumers`
  API in the canonical driver and synchronized consumers. Actual tracked-source
  materialization passed for Worktrees and SSH; five shared-driver admission
  controls and the native missing-task refusal passed. Fresh Windows
  qualification remains required, without a guard waiver or timeout increase.
- Follow-up run 40758894728 at `16abfd72bb34bc5eb9165507f06feae215357777`
  passed both original Windows activation and native/portable POSIX. Actual cold
  construction then refused upstream #9340's immutable extracted-wheel-cache
  hardlinks. Reconcile completion inspection with that installed-file contract:
  ordinary files may have multiple links; symlinks/reparse redirection, editable
  origins and escaping/unverified path hooks still refuse. Real cache unlink
  leaves admitted installed bytes usable; 59 environment controls passed with
  four platform skips. No published slot is rewritten by inspection.
- The final historical PID-collision fixture now chooses a target distinct
  from its caller; all 76 broker/retirement controls passed. The stopped-resident
  desktop-startup failure remains owned by this repair, with a peer-owned
  temporary mitigation retained until separately authorized installed cutover.
- Run 40760494432 passed original Windows activation and native/portable POSIX.
  Real cold Worktrees construction, automatic SSH construction, all four scoped
  maintenance requests and manual update completed; Worktrees broker/runtime
  cleanup passed. SSH cleanup correctly refused its unbound self-staging
  publisher, so the overall consumer result remains failed. Establish SSH
  teardown lifecycle custody before self-staging on both native frontends,
  using the existing canonical binding owner rather than restoring the removed
  publisher exemption. Twenty canonical owner/staging controls and four SSH
  exclusion guards passed; the actual hosted cleanup still must qualify it.
- Reconciled the subsequent current-base Bridge snapshot change by preserving
  its complete repository-policy closure and the broker's update-driver entry
  exactly once. Both real PowerShell snapshot controls passed. No independent
  installer queue, stop-consumer behavior or deployed change was introduced.

### 2026-10-28 - Qualify real broker consumers and canonical removal

- Exact candidate `3f92119913ff893fe8d3035d744ec601c7c1fb1a` passed all three
  hosted jobs in run 40660567152: original Windows activation, native/portable
  POSIX regression and real Worktrees consumers in a credential-free private
  disposable Windows profile.
- Actual cold construction and Task adoption, gated automatic SSH provisioning,
  equivalent pending coalescence, distinct scopes and manual-update exclusion
  all passed. Handoff completed in 2.125 seconds before installer release.
  All submitter Jobs closed before that release; canonical request, project,
  both runtime and broker Task cleanup were confirmed.
- The complete finite-removal packet passed 192 tests with five platform skips.
  A positive owned GUI/foreground canary retired before 16,715 samples with
  zero new visible windows or focus changes. Local fail-closed census
  limitations and prior failed candidates remain recorded in the broker journal;
  no production guard was waived.
- Actual qualification exposed and fixed manual-update project loss, finite
  native teardown ownership, original typed-source/project preservation across
  self-staging and the missing packaged native cleanup dependency.
- Draft #7833 remains held. Making it reviewable, merging it and installed
  rollout still require their separately authorized gates. Restore/Reclaim
  #9341 is released through promotion run 40634439651 / #9352, but its installed
  adoption remains held. The temporary resident and inherited feed/recovery
  obligations are unchanged; this effort remains Active.

### 2026-10-28 - Complete native component qualification; admit the real consumer lane

- Candidate `c7c964084a` passed run 40634644449 on Windows and Linux.
  The Windows witness now covers actual cell receipt/completion and retained
  helper API, cold/repeat Task adoption, payload disappearance, two concurrent
  installed callers, distinct native children, both caller-Job closures and
  confirmed child/Task removal. The positive GUI/foreground canary retired
  before 7,165 samples with zero new visible windows or foreground changes.
- Keep synthetic application packages and generic gated execution-worker
  products distinct from actual Worktrees automatic maintenance and explicit
  update. That last consumer gate is not closed by the component result.
- The operator authorized `authorize-disposable-consumer-flows`: bounded real
  Worktrees consumer install/update tests in a credential-free, private fixture
  on a disposable hosted Windows runner, restricted to owned components and
  task/service cleanup. Shared-host updates, registrations, rollout and replay
  of an earlier failed updater remain prohibited.
- Independent Restore/Reclaim correction #9341 merged into Dev as
  `f37d97361aa1e127d52315e7fb79b90dfeb8bf47`; its source worktree is finalized.
  Its promotion and installed adoption are separate, retained obligations.
  The release gate's complete-coverage capacity failure was already repaired
  by merged #9349; do not duplicate that repair or force promotion.

### 2026-10-28 - Qualify the integrated native broker and actual cell discovery

- Hosted run 40628307090 passed candidate `ac87600c60`: both actual native
  protocol cycles, all caller-Job closures, exact child control/delivery,
  shared-outer negative topology, crash recovery, generation retention,
  installer adoption/removal and execution-worker coalescing/distinct scopes.
  The fixture controller and its interpreter remain outside removable content;
  no production process-census or home-ancestor guard was waived.
- Actual cold-cell completion, immutable reuse, canonical registration factory
  and installed-module discovery now have a real Windows venv control.
  The complete cell packet passed 17 cases, including wrapper cleanup.
  This still substitutes application construction and broker mutation; it is
  not actual cell Task adoption or a full product build.
- That control exposed a real interpreter-path incompatibility: a physical
  direct interpreter can use extended DOS spelling. Normalize only that drive
  spelling after proving native file identity; keep UNC and arbitrary device
  registrations refused. Direct-base discovery avoids assuming that `-S`
  changes Python 3.14's venv-prefix semantics.
- Positive desktop/foreground observation, actual cell Task lifecycle,
  real maintenance/manual-update exclusion, portable qualification and
  authorized installed rollout remain open. No shared-host registration,
  updater replay or deployment was performed.

### 2026-10-28 - Close the positive desktop witness and add portable regression

- Candidate `ef7096e75e` passed hosted run 40630822365. Its actual GUI canary
  was observed as this fixture's window and foreground, then retired. The
  observer collected 5,755 samples through adoption, both Job-destruction
  protocol cycles, worker execution and uninstall; zero new visible windows
  or foreground changes were observed. Missing desktop evidence remains
  an explicit refusal rather than a passing zero count.
- Add a separate bounded Linux job to the same explicit broker selection:
  portable daemon contracts, actual POSIX process-group/lifecycle behavior
  and finite executor/participant regressions. Its result is still pending.
- Actual cell Task adoption and real maintenance/manual-update acceptance
  remain distinct from the completed synthetic-product and real cell-factory
  controls. The draft stays unqualified for merge or deployment.

### 2026-10-28 - Retain custody across launch-finalizer failures

- Normalize setup and native transport failures to redacted daemon refusals,
  retaining definite pre-request versus uncertain transaction evidence.
- Transfer the channel to the protocol once, and close finite-broker custody
  before returning the actual child. A failed close after launch retires that
  birth-pinned child instead of discarding its handle in a failing finalizer.
- Attempt channel cleanup, child retirement and finite-broker joining
  independently. Retain failed native closes and failed retirement for retry;
  unresolved cleanup prevents another admission. The private Windows transport
  retains each failed handle while releasing the other independently.
- Contained broker packet: 186 passed. Focused private transport close controls:
  2 passed. Python correctness lint and whitespace checks passed. These controls
  do not prove native Task activation, desktop visibility or deployed recovery.
- The operator authorized updating existing draft #7833 for credential-free,
  disposable hosted Windows qualification. This releases the draft-publication
  hold only; shared-host registration, runtime updates, deployment and
  unqualified merge remain prohibited.
- Hosted run 40622787009 exercised candidate `69b8a20b3e`: both actual
  protocol/Job-cleanup cycles, exact delivery, every installer-adoption adapter,
  crash recovery, retained generation collection and the real execution-worker
  coalescing/distinct-scope controls passed. Overall qualification failed during
  native removal: the older fixture omitted the external shell and finite
  product-ownership declaration now required by admission. Cleanup was
  confirmed. Correct the fixture without weakening those production guards;
  surface the bounded participant log when a failed transaction fences custody.
- The complete private Windows transport packet passed 55 cases with one
  off-Windows-only skip. The hosted-fixture admission/adapter packet passed
  56 contained cases. Native removal, real cell/desktop and remaining parent
  acceptance gates are still open.

### 2026-10-27 - Reconcile the held broker with current installation ownership

- Integrated the held broker source with current Dev's publication guards,
  daemon-instance journal, state-preserving removal and Manager source/custody
  contracts. This is an unqualified local candidate, not an installed change.
- Carry the instance receipt through the ordinary daemon adapter and bind the
  birth-pinned, suspended native child before the protocol grants resume.
  Failures preserve creation, admission and native-identity evidence; definite
  pre-request refusal settles its reservation without activation or replay.
- Move Manager broker adoption outside selector and target publication leases.
  Validate source ownership before that mutation and again before publishing
  frontdoors; retain transactional cutover and the closed bootstrap request's
  instance-reconciliation option.
- The operator requested: "Make sure install and uninstall align". Broker-capable
  native Windows install and uninstall now share the existing Python 3.11+
  construction-driver prerequisite. Removal requires an already available
  external interpreter; launcher enumeration never provisions one.
- Native component removal follows structural admission and owned service
  retirement. The finite engine's new admission-only custody check verifies
  consumers without stopping processes or deleting code; the existing execution
  owner then removes the broker before runtime deletion reacquires and rechecks
  custody. An adopted or pending broker blocks direct finite runtime deletion.
  CodeSpaces retains its Connection Owner and reconnect-transport refusals.
- Focused evidence: daemon/protocol/Task/refusal packet 194 passed; Manager
  adoption packet 24 passed; Pull Requests legacy and complete contained cell
  packet 30 passed; CodeSpaces packet 5 passed. Native driver controls passed
  70 cases, then the corrected MCP-prefix selection passed all 8 selected cases.
  These are contained adapter/control results, not actual cell broker adoption.
- The finite-removal packet initially passed 68 cases with 3 platform skips.
  Later full host runs refused uninspectable current-user process candidates.
  No attribution guard was relaxed: missing CIM image recovery requires a
  native birth match, and its matching/reused-birth controls pass. An isolated
  idle census also passed, but the latest complete host packet is not green and
  remains an open qualification obligation.
- Canonical installer/runtime synchronization and all 13 install contracts pass.
  Portable/POSIX regressions, cold and retained real cell adoption, maintenance
  exclusion, exact-candidate all-caller-Job and positive desktop evidence,
  required review/CI, promotion and authorized installed recovery remain open.
  Draft #7833 stays held; no shared-host registration, updater replay, rollout
  or deployment permission follows from this source reconciliation.

### 2026-10-21 - Kickoff

- Traced foreground stage join, bootstrap and two-pass reconciliation on both
  shells. Existing progress work did not create the serial maintenance path.
- Claimed #7694 and created isolated source `welded-circuit`; no installed
  updater, running launch, service or registry was changed.
- Confirmed the effort slug. Compared the captured request and settled
  direction with the operator input; implementation/test mechanisms are
  explicitly marked agent-recommended.

### 2026-10-21 - Reviewed contract

- #7716 merged after exact-head approving review and successful required CI.
  Synced the managed source onto the reviewed plan before implementation.
- Posted the bounded ownership notice on #6812 and #6820. Their retained
  scopes and #7667's contributor-owned implementation remain unchanged.
- The initially assigned backend worker did not survive the session
  interruption and left no source changes; the launch driver retains and
  directly resumes that implementation. No production maintenance was started.

### 2026-10-21 - Source implementation and qualification blocker

- Implemented durable optional requests, equivalent-request coalescence,
  distinct project/selector delivery and operation-scoped mutation custody.
  Different inherited feed/auth/package-policy environments retain separate
  workers; only their digests enter the queue. Scope workers wait for shared
  custody and release their empty-queue ownership transactionally.
- Removed the shell stage join, inline-download retry and shell-owned
  detached installer. Both shells submit through the same engine owner;
  blocking recovery also uses that custody. Explicit update, marketplace
  stage and applied reconciliation share the mutation boundary.
- Preserved canonical planner/installer admission, action environment/unsets
  and installer deadlines. Nonzero acquisition, diagnostic-only refusal and
  failed actions cannot produce successful completion. An interrupted mutation
  remains blocked without a positive completion witness.
- Targeted contained backend and native shell contracts passed. The real
  process fixture proves two handoffs before a gated installer completes,
  single mutation ownership, eventual distinct-project delivery and separate
  inherited-environment delivery. Existing apply-plan consumers passed.
  Two unrelated smoke fixtures initially omitted required installer siblings;
  repaired only their fixture assembly and confirmed the native smoke paths.
- Linux dependency preparation did not finish within its unchanged bound;
  the new test environment had no pytest or runtime packages. The stdlib-only
  native fixture then ran successfully under the existing bounded supervisor
  from a disposable native-filesystem source snapshot: both real process
  contracts passed. This does not qualify the unfinished full dependency graph.
- Windows caller-Job qualification exposed and corrected a local integration
  issue: use canonical `direct_python` plus its venv environment before
  `spawn_daemon`, rather than hand the broker a GUI interpreter or redirector.
- The strict no-breakaway caller still fails at shared broker
  `bootstrap-identity`. Bounded diagnostic instrumentation proves PID and
  cookie match, but `_outside_job(bootstrap)` is false. The shared primitive
  refuses startup with confirmed cleanup; no inherited-lifetime fallback,
  policy weakening or host remedy was attempted. #6859 retains this
  prerequisite's original owner. The effort remains Blocked, not Done.
- Reported the failed predicate to #6859's retained owner. Native fixture
  cleanup now waits on each observed worker's exit, not merely the last
  database completion row; that closes a real Windows log-handle cleanup race.
- Final contained backend execution passed with the production-survival lane
  explicitly suppressed under containment. Native Linux Python fixtures and
  eight native Bash handoff subcases passed from disposable native-filesystem
  snapshots using the existing bounded supervisor and standard-library runners.
  Native Windows handoff exercised sixteen subcases with the production log
  helpers. One combined progress group encountered an unexplained twenty-second
  formatter pipe-completion timeout; both exact formatter nodes passed in
  isolation at the unchanged budget. This is not an accepted flake exemption
  or a claim that the entire combined group passed.
- Reconciled the moving Dev base with a backup ref, preserving #7727's Bash
  fallback diagnostics and #7736's marker normalization plus retained formatter
  tests. The exact rebased Windows group then passed: nineteen tests and
  sixteen handoff subcases, with seven native-POSIX skips. Native Bash's eight
  subcases also passed against the rebased source. No deadline was raised or
  failed check bypassed. The shared broker blob is unchanged; #6859 remains
  the substantive native acceptance blocker.

### 2026-10-21 - Own the follow-up bug

- Captured and read back the operator's explicit direction above: merged
  implementation #6918 does not create a standing ownership hold on bug #6859.
  No open broker PR was found. Claimed the follow-up under source
  `whirring-doohickey` rather than waiting for an unverified active owner.
- Native observation found ambient Jobs distinct from the caller's cleanup
  scope. Merely adding breakaway flags did not fix the failure; those
  speculative flag changes were removed. The candidate instead queries complete
  caller-Job membership and retains both pre-staging bootstrap and pre-resume
  resident checks, identity admission and fail-closed query handling.
- Focused protocol/error tests passed, and the actual containing consumer
  completed two strict caller-Job cleanup cycles with the candidate library.
  Shared-consumer validation, review and publication still remain open.

### 2026-10-22 - Review exposes the ancestor-Job proof boundary

- Published the broker candidate for review without installing or activating it.
  The original contained shared suite passed 416 tests; the separate production
  Windows lane passed eight tests, and the real maintenance consumer completed
  two cycles after closing both nested caller Jobs.
- Pulled the repair forward after #7811 merged. Retained its canonical process
  library consolidation rather than restoring the removed Worktrees vendor.
  The composed shared suite passed 452 tests with 30 platform/lane skips.
  Two runs encountered fixture-image deletion errors after passing assertions;
  exact retained fixture roots were subsequently retired without killing
  unrelated processes. A focused native run and an unchanged full canonical
  runner then passed including sandbox retirement. No failure waiver was used.
- Review found that the passing nested-Job fixture proves the exercised
  topology, not every ancestor-sharing topology. Windows documents that
  `QueryInformationJobObject(NULL)` queries only the immediate Job. A candidate
  sharing an outer cleanup Job but not the caller's inner Job could pass the
  candidate check and still die when the outer Job closes.
- Held the repair as a draft and kept this effort blocked rather than weaken
  the lifetime contract. An independently managed Windows launch broker would
  change the no-new-service design and requires operator direction. The other
  review findings remain owned with the same repair, not waived.
- The merged execution driver under #7758 now owns installation execution.
  The launch slice must integrate with that canonical owner instead of retain
  its own competing mutation custody. Posted coordination on the driver change;
  no rollout or additional updater was requested.

### 2026-10-22 - Source-only broker redesign authorized

- Asked for a design-scope decision rather than infer service authority from the
  original bug-fix request. The operator selected `source-redesign`, approving
  the source-level independent broker option with deployment separately gated.
  Read the selection back against the offered option and preserved that limit.
- Resume design work: reconcile the lifecycle patterns and standing service
  vision, then review the amended contract before implementation. All broker
  topology, lifecycle and transport choices remain agent-recommended mechanisms.
- Do not register or start a host task/service, install a candidate, restart
  shared services, or launch a competing updater under this authorization.

### 2026-10-22 - Broker design review complete

- #7910 merged as `8b1437d07f9808a48009a2683d7bff394fd70e56` after an
  exact-head Copilot approval and successful required CI. The managed merge
  command timed out after the provider accepted it; a separate scoped provider
  read confirmed `MERGED`, rather than blindly retrying the mutation.
- Re-read this effort's Plan and Validation Plan. Only the design-review
  prerequisite is closed. Actual broker implementation, independent native
  activation/transfer proof, consumer integration, release and authorized
  rollout remain open; a reviewed plan is not an implemented broker.
- The repair source now retains retryable query-Job/member-handle custody,
  checks the deadline within its census, and isolates synthetic PIDs. The full
  composed shared suite passed 455 tests with 30 platform/lane skips, including
  canonical sandbox retirement. These corrections do not resolve the remaining
  ancestor-Job proof boundary by themselves.
- Start the broker implementation from the reviewed contract, preserving the
  source-only boundary. The plan-only source's pull-forward helper refused a
  deleted PR ref; its merged provider state is confirmed and its checkout is
  retained, not manually reset or removed.

### 2026-10-22 - Reconcile the merged launch execution front door

- Pulled the independent repair forward, preserving #7860's newly merged
  caller-Job guard and cancellation handling. The two owned repair commits
  are now based on current Dev; the independent broker is not implemented.
- #7860 also routes native launch bootstrap, reconciliation and staged apply
  through the canonical execution driver. Its launchers still join optional
  preparation before session handoff, so the responsive-launch objective is
  not closed. However, this effort's draft #7788 now overlaps a merged
  implementation and retains competing maintenance custody.
- Pause that substantive integration choice for operator direction rather
  than silently supersede the draft or restore its separate execution owner.
  The recommended continuation is to reuse the merged execution front door
  and narrow this effort's implementation to off-handoff scheduling plus the
  independent #6859 lifetime repair.
- The rebased shared assertions passed 467 tests with 30 skips. The outer
  sandbox retirement failed on an installed fixture image; all 90 recorded
  process birth identities were subsequently confirmed exited and the exact
  retained sandbox was removed. This is not a green full-runner result or a
  failure waiver. The focused lint check passed.
- Task Scheduler's documented engine PID is not an action-process identity
  guarantee. Do not assume equality or use task registration alone as the
  independent activation proof. Native activation and lifecycle qualification
  still require an approved disposable Windows lane; this work has registered
  no task, changed no installation and restarted no service.
- The operator selected `reuse-merged-driver`. Continue by reworking this
  effort's own draft around #7860, not by retaining a second execution owner
  or declaring the remaining responsive-launch and broker gates complete.

### 2026-10-22 - Reuse the execution owner for automatic maintenance

- Preserved the original draft and qualification work in a local backup before
  reworking it around merged #7860. The replacement has no separate maintenance
  queue, worker implementation or mutation lock.
- Added fixed `launch-update automatic` execution inside the existing driver.
  Healthy native launchers submit it with `--detach`, then proceed without a
  shell-owned stage, installer join or inline-download fallback. Explicit
  blocking recovery keeps the existing fixed-phase path and progress handling.
- Acquisition now uses bounded caller-owned process control and records its
  real exit outcome. Automatic acquisition refusal, publication refusal and
  phase failure produce failed durable outcomes rather than installation
  success. Update and reconciliation opt-outs remain independent.
- The actual Windows shell, canonical request boundary, finite driver,
  bootstrap executor and a gated fixture installer proved four handoffs before
  the installer gate opened. Equivalent pending requests coalesced without
  losing another project's request, and both projects completed after release
  of the gate under the same execution owner.
- The final contained composed suite passed 140 tests with 15 explicit
  platform/lifetime skips, including canonical sandbox retirement. Installer
  contracts passed for all thirteen plugins. This is real handoff/queue proof,
  not caller-Job survival, a registered broker test or installed qualification.
  Native POSIX and the independent Windows broker lanes remain open.
- The operator separately authorized `disposable-windows-ci` for a synthetic
  Task Scheduler activation/cleanup experiment. Use that lane to determine
  actual instance/process binding and conditional child escape before treating
  the Scheduled Task candidate as supported. The shared-host prohibition and
  deployment gate remain unchanged.

### 2026-10-22 - Disposable native activation candidate works

- Ran the explicitly selected Windows probe in workflow run
  [39850345336](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39850345336)
  against exact repair head `7b4e857a3e292470f4e94739239fca279b751688`.
  Verified that the actual Windows probe step ran successfully; unrelated
  native modes were skipped, not credited as passing.
- Both independently activated task instances reported an engine PID equal
  to the native pipe-server PID. The broker's process generation was pinned
  and checked. Both brokers were themselves in a Job, but each suspended
  resident successfully broke away and was confirmed in no Job before resume.
- Each actual child resumed without a visible console and remained alive
  through two observations after its finite broker exited. Actual native
  child handles controlled termination and confirmed exit. The artifact reports
  both child cleanups and deletion of the uniquely owned S4U registration.
- This supports the finite, per-user, on-demand task candidate on the
  exercised runner. It does not establish a universal engine/action PID rule:
  production admission must verify the equality on each actual instance and
  refuse mismatches. It also does not qualify authenticated production IPC,
  nested caller-Job survival, installer adoption/update/rollback or a shared
  host rollout.
- Continue canonical broker implementation using these conditional native
  checks, not the unproved immediate-Job inference. The three other retirement
  corrections and the probe passed the unchanged full contained runner:
  476 tests passed, 30 explicit platform/lifetime skips, with sandbox retirement.

### 2026-10-22 - Implement finite native transaction before normal-launch adoption

- Added the finite transaction, actual native handle duplication and an explicit
  consumer-owned registration/lifecycle adapter in the held repair source.
  Reused the canonical private Windows transport rather than introduce another
  secrets-bearing channel. It is an optional library capability, not a sibling
  plugin dependency or an automatic installer.
- The client authenticates the scheduler instance and native pipe process
  before sending live launch data. It adopts actual child control before resume;
  the child must be outside every Job. The finite broker stays alive through
  final commit and client pipe closure. Failed retirement retains retryable
  custody and refuses more work rather than silently permitting a duplicate.
- Contained protocol, adoption, framing, custody and existing process regressions
  passed: 637 tests, 31 explicit platform/lifetime skips, including canonical
  sandbox retirement. Lint, native PowerShell parsing, thirteen installer
  contracts, source-vendoring checks, headless guard and trusted workflow policy
  passed. The workflow-policy check used the existing YAML-capable test
  environment after the process-only environment explicitly lacked that tool.
- Extended only the already-authorized disposable Windows probe lane to exercise
  the production protocol through synthetic registration, nested caller-Job
  closure, exact resident arguments/environment/logs and native cleanup twice.
  That lane has not yet run against this candidate, so these are not native
  qualification results.
- Normal launches still use the old fallback pending cohesive replacement.
  Installer health-gated adoption/update/rollback, scope propagation, actual
  caller-Job negative topology, full desktop visibility, native POSIX and the
  containing maintenance consumers remain open. This is not a completed #6859
  repair, a source merge, promotion or host deployment.

### 2026-10-22 - First-touch native qualification exposes task lookup mapping

- Exact-head run
  [39860195613](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39860195613)
  executed the real Windows step. Its preliminary capability probe passed, but
  the production-protocol fixture failed during first adoption; it completed no
  resident cycles and did not claim cleanup or qualification.
- A missing task can surface as `System.IO.FileNotFoundException`, rather than
  only a COM exception. Corrected that exact missing-object case without
  treating denied or unknown lookups as absence. The native Windows PowerShell
  regression executes the actual lookup function against synthetic CLR methods;
  missing and access-denied cases both passed without connecting to or modifying
  Task Scheduler. The composed adoption suite passed 24 tests.
- Published the correction on the held repair draft and dispatched only the
  authorized disposable lane again in
  [39862177885](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39862177885),
  exact source `186c0e3003231c7a22692f1bdbf36ae41ac5c5e1`.
  That result remains pending here; a retry dispatch is not a passing result.

### 2026-10-22 - Native adoption progresses; activation remains unqualified

- The second run rejected the registered principal's account-name form as if it
  were a different owner. Normalized account names and SID strings through the
  OS SID resolver, preserving exact principal ownership. Native PowerShell
  mapping and adoption tests passed 26 cases without host registration.
- Run
  [39863715312](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39863715312)
  at `e01073ba24267f95c57a02e72a97769e27d50709` passed first/repeat adoption,
  status and owned removal sufficiently to confirm cleanup. The fixture caller
  then failed before IPC because its case-sensitive synthetic environment filter
  dropped Windows' uppercased `SYSTEMROOT`. Corrected only the fixture allowlist;
  retained credential exclusion and added three casing controls.
- Run
  [39864605984](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39864605984)
  at `8deff8d33e968c2b85bf68203c0a0656f989eaac` reached actual activation from
  the nested caller, but the bounded scheduler adapter exceeded its unchanged
  thirty-second deadline before returning control. Owned cleanup was confirmed;
  no resident cycle or production pipe handoff passed. This is the current
  native blocker, not a successful lifetime result.
- Narrow read-only controls proved that the filtered caller environment can
  initialize the scheduler COM connection, read the exact adapter's JSON stdin
  and refuse an absent task under caller Job containment. No task was created or
  started on the shared host. Diagnose the actual activation wait with bounded,
  fixed diagnostics rather than increase the deadline, infer independent
  lifetime or proceed to normal-launch adoption.
- Added an unqualified negative-topology fixture in the repair worktree:
  distinct immediate Jobs under one shared outer Job must fail the canonical
  child offer, and the native control must die when the outer handle closes.
  Positive controls now close actual caller Job handles rather than use
  termination as a proxy. Source/admission tests passed 15 cases; the new
  topology has not yet run on the disposable Windows lane.

### 2026-10-22 - Shared-ancestor negative proof and bounded activation evidence

- Exact-head disposable run
  [39872678419](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39872678419)
  at `72f65f3ed78a78b162dbb34f577fce279470c0d6` established different
  immediate Jobs under one shared outer cleanup Job. The canonical native child
  offer refused durable success, and closing the actual outer Job handle killed
  the controls. This closes that negative-topology item only.
- The production activation still completed no resident cycles. The adapter
  returned a nonzero native result; the former timeout-only diagnostics did not
  preserve its last fixed phase. Cleanup was confirmed. Neither this result nor
  the preliminary capability probe qualifies the production handoff.
- Added bounded raw timeout evidence after confirmed retirement and reader
  joins, plus activation-only, fixed allowlisted phase diagnostics. Native
  errors never disclose request, task XML or arbitrary COM exception text.
  Activation deadlines remain unchanged. The composed contained regression
  suite passed 113 tests with three explicit platform skips; the refined
  nonzero-refusal adapter suite passed 35 tests. Lint and native parsing passed.
- Published the nonzero-refusal phase correction on the held repair draft and
  dispatched exact source `616d9e3f7dadada8cede035ede12040dcb29c427` in
  [39873481651](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39873481651).
  It timed out in the input-read/parsing stage before scheduler connection;
  cleanup and the negative topology passed again, but no resident cycle did.
- Added a native nested-live-parent stdin regression using a synthetic
  PowerShell script, without a scheduler connection. Script-file JSON input
  reached EOF locally while the outer caller's input pipe remained open. That
  narrows the hypothesis but does not explain the CI-only wait. The fixture and
  refined input subphase passed 36 contained cases.
- Published exact source `81f10c880565ad157008939767f687223ccf75f3` and
  dispatched
  [39874898967](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39874898967)
  to distinguish input EOF from JSON parsing in the actual disposable caller.
  That run reached `run-requested` and refused in the actual `RunEx` call;
  neither stdin nor scheduler connection explains that particular refusal.
- The documented native `RunEx` contract accepts a BSTR or BSTR array, not an
  untyped variant array. A regression evaluated the actual source expression
  against native VARIANT marshalling without connecting to Task Scheduler.
  It rejected the original object array. Explicit `string[]` marshalling then
  passed the native `VT_ARRAY | VT_BSTR` check and all 36 adapter cases.
- Published the argument-type correction at
  `5b749a980749ef41dc3934237792362ec3b41b0e` and dispatched
  [39875766662](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39875766662).
  Continue from that exact native result before normal-launch integration;
  no shared-host adoption or deployment is authorized.

### 2026-10-22 - Production protocol passes the disposable native lane

- Run
  [39875766662](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39875766662)
  still timed out before JSON parsing completed. The BSTR-array correction
  alone did not qualify activation. The synthetic caller had removed OS profile
  and program-directory context from its environment; restored only an explicit
  OS-variable allowlist, retaining arbitrary credential exclusion.
- Exact-head run
  [39876801692](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39876801692)
  at `4d75361610125ad1818fd867eda164c4db05630b` passed the actual Windows
  probe step and production protocol. Both resident cycles retained actual
  process control, survived closure of all fixture-owned caller Jobs and finite
  broker exit, delivered exact argv/environment/cwd and split logs, had no
  visible own console, and completed actual child retirement.
- The different-inner/shared-outer negative control passed again, and the
  result confirmed removal of the uniquely owned registration and owned native
  resources. This is synthetic source qualification, not installed deployment
  or complete desktop-focus evidence.
- The composed process, private framing/Windows transport and probe suite passed
  663 tests with 31 explicit skips and confirmed sandbox retirement. The
  diagnostic/context-focused suite passed 56 cases; lint passed.
- Continue with owning-installer health gating and lifecycle/rollback evidence,
  then cohesive default consumer integration. The ordinary launch path still
  retains the old fallback; this repair draft must not merge or deploy as a
  completed fix. Native POSIX, containing maintenance consumers, review,
  legitimate release and separately authorized rollout remain open.

### 2026-10-22 - Exact action health and native lifecycle pass

- Added a bounded exact-`pythonw` health entry before registration mutation.
  It imports the immutable action's colocated transport, checks native user
  identity and requires a strict readiness response. Missing dependencies and
  unhealthy candidates cannot change task registration or published metadata.
  The real copied-runtime health and targeted adapter/probe suite passed 62
  contained tests; lint passed.
- Exact-head run
  [39878472832](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39878472832)
  at `0f238d4772557db38e017a7fdfa738e8f6ee3442` passed the actual Windows
  step, both native resident cycles and the shared-ancestor negative control.
  It exercised first/repeat adoption, a replacement immutable action, unhealthy
  candidate refusal, restoration after injected publication failure, stop,
  readoption and final owned removal. Cleanup was confirmed.
- This closes the bounded native lifecycle scenario, not whole-installer
  integration. Reuse canonical installation ownership and crash-consistent
  publication/recovery before wiring normal launch: task registration and
  configuration publication are separate effects, and synchronous rollback
  tests do not prove interrupted-transition recovery. Scope propagation,
  generation retention, standalone dependency closure, desktop observation,
  native POSIX and actual maintenance consumers remain open. Both drafts stay
  held; no deployment authorization has changed.

### 2026-10-22 - Publication intent implemented; changed candidate is unqualified

- Source `97d9820907281f23dd367cfda3d8aab668d1ad47` adds installer-owned
  credential-free publication intent before Scheduler mutation. Ordinary
  admission refuses while recovery is pending. Explicit recovery restores the
  original action, enabled state and configuration, preserves prior adoption
  on repeat-install failure, revalidates metadata around native effects and
  retains unresolved intent. It introduces no second execution lock, queue,
  automatic replay or normal-installer adoption. Both generations still need
  canonical retention; power-loss atomicity is not claimed.
- The changed broker/protocol/probe suite passed 127 contained cases and
  retired its sandbox; lint passed. The wider process run did not qualify:
  three native cases reported unconfirmed process exit or a member appearing
  after census. Those three passed in isolation; a larger 167-pass run still
  failed deletion of its installed-console fixture executable. Neither result
  is a full-suite pass or a waiver. These failures remain owned prerequisites.
- Exact-head disposable run
  [39886572524](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39886572524)
  failed the production protocol before its first custody transfer:
  activation timed out at fixed phase `input-read`. The shared-ancestor
  negative control and final owned cleanup passed. The new real
  subprocess-death controls, placed after the two publication boundaries,
  were not reached and are **not qualified**. The phase narrows the failing
  interval; it does not establish why conversion/configuration selection
  stalled. No unchanged retry or timeout increase is justified.
- Keep both drafts held and the effort Active. The next action is bounded
  evidence for this actual nested-caller activation stall and the native
  retirement/fixture-cleanup failures, before installer integration or default
  replacement. Historical native passes qualify only their recorded source.
  No shared-host registration, update, restart or rollout occurred.

### 2026-10-22 - Drive scheduling-only delivery through review

- The operator explicitly selected `scheduling-only`: qualify and merge #7788
  using the existing execution driver, retaining #6859/#7833 as a separately
  owned repair. The current target already refuses a broker bootstrap or
  resident in any Job; #7788 does not change that admission code or install a
  Scheduled Task. This supersedes the prior containing-PR hold, not a lifetime
  check or the independent broker's remaining qualification gates.
- Rebased the owned scheduling branch onto current `dev`, retaining peer
  effort-index additions and the unrelated native post-exit diagnostic fix.
  A local backup preserves the previous published tip. No other contributor's
  open PR is adopted as a delivery dependency.
- Added a real canonical-driver refusal contract: rejected worker startup
  returns no accepted receipt, retains a pending request and durable error,
  preserves strict `contained_fallback=False` admission and never executes an
  installer in the foreground. Native launcher controls also demonstrate
  handoff after that refusal and preserve logged failure details.
- The composed Windows scheduling slice passed 163 cases with 20 explicit
  platform/lifetime skips and confirmed sandbox retirement. The unchanged
  driver's equivalence, stronger-request identity, custody, contention and
  failure contracts passed 34 cases with one native capability skip. Install
  contract and touched-source lint passed. An earlier run reported machine
  environment drift and is not counted as passing; no host configuration was
  rolled back.
- Added a manual targeted mode to the existing hardened Linux integration
  route for Worktrees and Manager. It retains exact candidate pinning,
  dependency admission and network-isolated execution, records the actual
  selector/outcome, and cannot file the full-portfolio failure tracker.
  Scheduled nine-plugin coverage and disabled Windows rotation are unchanged.
  Native Linux execution, final review and required PR CI remain pending.

### 2026-10-22 - Linux qualification finds the native endpoint length limit

- #7788 is reviewable. Its first targeted hardened-container run
  [39911749036](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39911749036)
  executed the actual Linux selections but failed request participation:
  the existing witness path under the synthetic queue root was 117 bytes,
  exceeding Linux's 108-byte Unix-socket structure. The resulting interrupted
  transaction correctly fenced later work; it was not cleared or replayed.
  That run remains failed. Required PR guards and eligible units passed for
  its exact source, independently of the missing native acceptance.
- Fixed the canonical witness's address allocation, not the queue or execution
  owner. Short legacy Unix paths stay unchanged. A long path uses an owned
  private temporary directory within the native byte budget, preserving the
  authenticated original root/request and cleaning the endpoint on close.
  Clients verify private ownership/permissions, while unavailable short
  temporary storage fails explicitly. Windows named-pipe behavior is unchanged.
- Both existing consumer changefiles cover the shared driver. Tests cover
  encoded-byte length, legacy compatibility, unavailable temporary paths,
  listener-construction cleanup and live long-root participation/refusals.
  The current Windows driver/routing slice passed 63 cases with nine explicit
  native-platform skips and sandbox retirement; lint and module-size checks
  passed. The next exact-source Linux run also selects the shared driver's
  core and transport tests through the same hardened wrapper, not host-side
  integration or an enlarged required-PR test portfolio.

### 2026-10-23 - Native Linux passes; preserve independent work after review findings

- Exact-source run
  [39914962618](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39914962618)
  at `afb2d47f7ab156c3e6ab9ba6d31624c46a91eda9` passed all three actual
  Linux container targets: Worktrees, Manager and the shared driver. This
  includes live long-root participation and native Bash handoff. The disabled
  Windows rotation and shared-issue reporting were correctly skipped, not
  treated as qualification.
- Copilot identified two concrete orchestration misses. A denied Worktrees
  publication must not suppress independently eligible repository bootstrap
  and reconciliation. Likewise, an unrelated reconciliation failure must not
  suppress eligible staged Worktrees apply. The revised executor continues
  those canonical phases, retains the refusal diagnostic and returns a failed
  aggregate outcome. Acquisition failure and unhandled custody failures are
  not converted into permission to continue.
- Added managed-refusal and phase-failure regressions, then re-ran the full
  local composed slice: 209 passed, 25 explicit platform/native skips, with
  sandbox retirement. Four actual healthy handoffs completed in 18.36 seconds
  total before the gated installer was released. Their existing 20-second
  per-handoff limit, installer gate and production deadlines are unchanged.
- Local-only investigation separated that latency contract from the explicit
  blocking compatibility command, whose completion includes serial worker
  and executor startup. Its diagnostic samples completed in 5.56--19.69 seconds;
  the result-correctness watchdog is now separately bounded at 30 seconds,
  not represented as healthy-handoff latency. Failure-only phase/request/log
  diagnostics remain; all temporary timers and source instrumentation were
  removed before publication. Final-head review, CI and Linux acceptance
  still precede merge. The operator selected local-only Windows qualification.

### 2026-10-23 - Preserve the real reconciliation failure result

- Exact-source run
  [39933889758](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39933889758)
  passed all three Linux container targets at
  `ec7f986b80faf8fb8ff484fc7b37f8dbcd8598d7`; required PR checks also passed.
  Copilot confirmed the original findings were resolved but identified a
  further concrete producer-contract gap: handled reconciliation exceptions
  returned nonzero without JSON, so automatic aggregation parsed an empty
  buffer and skipped later eligible work. The absence of an open inline
  thread did not make that reported Medium finding a passing review.
- The real apply-error handler now emits structured unsuccessful JSON and
  preserves its existing failed status fields. Execution-state errors,
  participant/custody loss and unconfirmed native cleanup remain fatal rather
  than ordinary plugin failures. Regressions call the actual handler, verify
  retained earlier results and continued eligible staged work, and verify
  that authority loss stops subsequent mutation.
- The focused producer/orchestration/routing controls passed 48 cases with
  four platform skips. Full producer, acquisition and automatic-phase
  coverage then passed 88 cases with one native-lifetime skip.
  Deployment-policy regression coverage passed all 106 cases in two complete
  partitions (76 and 30), using the canonical short Windows fixture layout.
  Earlier diagnostic runs with oversized fixture paths or incomplete
  aggregate execution budgets are not counted as passing. Individual test
  and production deadlines were not widened for those policy checks.

### 2026-10-23 - Keep unconfirmed acquisition cleanup with the canonical owner

- Exact-source run
  [39939723023](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39939723023)
  passed all three Linux targets at
  `587775c90c0d91ffc5986ac2070713f43ba3d95b`; required PR CI passed too.
  The subsequent review identified another concrete custody boundary despite
  its empty inline-issue list: acquisition caught `OwnedCleanupError` through
  the generic subprocess-error handler. It must not become an ordinary
  unsuccessful download followed by more queued installation.
- Acquisition now rethrows that exact error, retaining its original native
  process/Job control. Borrowed JSON execution registers cleanup responsibility
  with the existing transaction witness before executing and releases it only
  after normal completion/result publication. A participant that abandons
  execution cannot regain mutation authority; disconnect or a converted
  integer exit cannot discard the owner's outstanding obligation. The same
  canonical journal records interruption and fences later queued work under
  its existing positive-settlement policy.
- This adds neither a queue nor another mutation lock/owner. Scope control
  requires an explicit owner acknowledgement, so an unsupported predecessor
  refuses before mutation instead of silently ignoring the responsibility.
  Existing native-foundation ownership assertions retain their wire contract.
- Real-process regression coverage has a product abandon execution and exit
  with an ordinary nonzero integer: its request remains interrupted, and the
  next queued product never executes. Separate controls verify normal nonzero
  completion, nested scopes, downlevel refusal, native-control retention and
  both borrowed JSON-adapter branches. The combined local Windows slice passed
  200 cases with 11 explicit native/platform skips and sandbox retirement,
  including native-foundation compatibility and actual healthy handoff.
  Lint and module ceilings passed. Final-source review and CI remain required.

### 2026-10-23 - Scheduling source merged; release and installed acceptance retained

- #7788 merged into `dev` as
  `85544de549b2c8c32541e845d61e04177df5199f` after the actual Copilot review
  approved exact source `18262e0f0d4763da369604bc4ae6fcabf99c0f83` with no
  blocking findings. Both original inline threads were resolved; the later
  producer-result and acquisition-custody findings were also fixed before
  approval. Required PR CI passed. Managed completion verified the merge
  using the repository owner's configured review-rule exception; no required
  check was bypassed.
- Exact-source Linux run
  [39944492386](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39944492386)
  passed all three actual hardened-container targets: Worktrees, Manager and
  the shared driver. Together with the recorded local Windows slice and
  real-process interruption regression, this closes scheduling source
  qualification, not an installed rollout. The hosted Windows lane stayed
  disabled, as requested.
- The checked source items reuse the existing bounded owner, queue,
  authenticated witness and installer admission. Native Windows all-caller-Job
  survival, broker activation and installer integration remain exclusively
  in the independently owned #6859/#7833 repair. Historical native passes do
  not qualify that repair's changed candidate.
- This effort remains **Active** under #7694 with the same launch driver.
  Next, observe the legitimate #3336/#6237 promotion route without dispatching
  another updater. First-use/missing-prerequisite acceptance and actual
  promoted installed handoff, maintenance completion and failure evidence
  remain unchecked until proved through the separately authorized normal
  rollout. No host registration, installation, restart or deployment was
  performed to complete the merge.

### 2026-10-23 - Release and installed state reconciled

- Legitimate promotion #8268 published Main
  `931e3d1734bb41496c8f4d526051b18e63914db4`. Exact source-blob checks
  confirmed the scheduling executor, shared participant and both launchers
  were released; this is no longer merely a pending-release change.
- Read-only selected-slot observations found Manager `0.6.104-dev1` and
  Worktrees `1.24.191-dev1` with completed construction receipts. Fresh
  isolated metadata probes verified the selected package/distribution
  origins and actual AMD64 Python platform. The Manager launcher scripts
  contain the released scheduling change; the Worktrees executor and
  participant differ from the merged source and remain behaviorally
  unqualified. No host maintenance was started or attributed to this driver.
- Keep installed acceptance and first-use checks open. Finish source parity
  and isolated acceptance before considering an authorized rollout; do not
  convert a receipt, version, metadata probe or successful source test into
  running-state evidence. The independent broker repair remains held.

### 2026-10-24 - Broker foundation adoption, source only

- Added a fixed canonical numbered-runtime registration factory and a
  driver-owned adoption adapter. The factory derives installation identity
  from its durable root, validates immutable completion with the installed
  canonical helper, and selects the direct base GUI interpreter rather than
  a Windows venv redirector. No environment hint grants installation identity.
- Foundation construction adopts only after settled selection and outside
  publication custody, while still borrowing the same execution owner.
  Its fixed Python/PowerShell/Linux input inventory now includes the private
  native transport. Existing PowerShell witnesses support nested execution
  scopes and refuse successful disposal after an abandoned scope. No second
  installation lock or queue was added.
- Unit/integration evidence: 142 focused factory, retention, driver and
  fixture-refusal cases passed; the real copied venv action qualified without
  Task registration. Separately, 55 foundation state/native cases, 15 native
  source-inventory/progress cases and all 26 actual PowerShell witness cases
  passed. Two gated progress cases were skipped. Install contracts and changed
  Python checks passed. Larger aggregate runs hit their existing wall limits;
  one aggregate failure lacked a traceback, although all its partitioned
  selections subsequently passed. This does not establish its root cause or
  license a deadline increase.
- Clean-room/external qualification remains open. The hosted-only protocol
  fixture now constructs real venv actions and routes successful adoption
  through the actual queue/default native executor, but that changed fixture
  still needs its exact-candidate hosted run. It refuses shared-host execution
  before construction or imports. Ordinary consumer installers, normal-launch
  selection, desktop observation, maintenance-consumer qualification and
  native POSIX regression remain open under #6859/#7833.
- The PR remains draft/held. No shared-host Task, installed runtime, service,
  updater, rollout or lifetime policy was changed.

The first exact-candidate hosted run,
[40083708067](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40083708067)
at `e209652e3a9ddd2dcc88e3344c587f2b8dffe32d`, failed with confirmed fixture
cleanup. Actual driver-owned adoption, direct installed action, the first
resident cycle, negative topology and both crash-recovery/retention boundaries
passed. The second resident launch refused `broker-admission-disabled` after
stop/readopt. The adapter had dropped a same-generation previous registration,
incorrectly choosing idempotent install rather than explicit re-enablement.
The correction retains that previous identity; a healthy, already-published
registration now returns without disabling or re-registering it. Stopped
readoption and failed-readoption rollback have separate controls. This requires
a fresh exact-candidate hosted run, not a retry of the failed commit.

That corrected run,
[40084215435](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40084215435),
passed in 48 seconds at exact source
`b213a588f4cccd3eb3b64ee80e35d0f026390be1`. It reported actual driver-owned
adoption and canonical installed actions, both native resident cycles and
all-caller-Job closures, exact delivery, finite broker retirement, negative
topology, both publisher-death/recovery boundaries, crash-surviving references
and old-action collection before the second cycle. Stop/readopt and final
owned cleanup passed. The corrected contained selection passed 144 cases.
This closes the disposable foundation-adoption slice, not the remaining
normal-consumer, maintenance, desktop, POSIX, review/release or rollout gates.

### 2026-10-24 - Installed-module discovery, source only

- The explicit Task client now discovers adoption from its imported
  `agent_procutil` module's fixed numbered installation layout, not an
  environment-selected registration, working directory or `sys.prefix`.
  Source/vendored and linked layouts refuse before Scheduler access. A newer
  consumer slot can still discover an older retained action under the same
  installation root. Missing adoption remains a read-only failure.
- The targeted discovery, adapter, retention and probe controls passed 125
  contained cases. This includes a real venv subprocess under `-I -S` whose
  prefix names the base interpreter, while discovery returns the exact owned
  installation without changing adoption metadata. Changed Python lint passed.
- The hosted-only fixture now imports each cycle's actual installed consumer
  package under `-I -S`, with no broker owner/config environment hints. This
  changed exact candidate still requires its disposable Windows run; prior
  hosted results do not qualify the new client binding.
- `spawn_daemon` still uses the held legacy default. Canonical consumer
  installers, dependency closure, actual maintenance handoff, interactive
  desktop/POSIX qualification and review/release remain open. No shared-host
  Task, installed runtime, service, update or rollout was performed.

Exact-candidate hosted run
[40085820659](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40085820659)
passed at `8b1b392f794a693fa9c87b9fe84defc8e1dac77c`. Both actual resident
cycles reported installed-module discovery under isolated no-site Python,
survival after all caller Jobs closed, exact delivery, finite broker exit
and confirmed cleanup. Actual driver adoption, lifecycle, the shared-ancestor
negative control, both publication-crash recovery boundaries and collection of
the replaced action passed again. This qualifies the explicit installed client,
not a switched ordinary launch default or whole consumer-installer integration.

The previously failing aggregate selection was repeated with stop-on-first-
failure and verbose node reporting under its unchanged 180-second supervisor:
211 cases passed in 69.75 seconds. The complete contained process-utility suite
then passed 659 cases with 64 explicit skips in 105.83 seconds under its existing
300-second bound. These are current passing selections, not a root-cause
explanation for the earlier timeout/failure or a waiver of a lifetime budget.

Scoped native Linux maintenance run
[40086630381](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40086630381)
at the same source passed through the canonical hardened container route:
Manager 60 passed/25 skipped, Worktrees 108 passed/1 skipped, and the shared
driver 59 passed. The recorded selectors deliberately exclude most tests;
this is not the full portable process-library suite, macOS evidence or a new
Windows registration qualification.

### 2026-10-24 - Fixed installer adoption entry, source only

- Added an installed `update_driver.windows_broker` module entry point with
  explicit runtime-root, immutable-slot and execution-root arguments. It calls
  the same custody-requiring adapter; it accepts no command recipe, creates no
  second lock/queue and prints success only after adoption finishes.
- The targeted adapter/foundation/probe selection passed 54 contained cases;
  missing paths, absent witness, exact argument binding and a real driver
  transaction have focused controls. Changed Python lint passed.
- The hosted-only fixture now runs the actual installed module entry point
  with each action slot's interpreter through the default native executor.
  Its changed exact candidate still needs disposable hosted qualification.
  Consumer installer wiring and the ordinary launch default remain held;
  no shared-host installation or rollout occurred.

Exact-candidate hosted run
[40087309148](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40087309148)
passed at `4723fff84b141d2ec1696cd81d39d7bc19771788`. Adoption now executed
the installed module entry point with the target slot's interpreter through
the actual default native executor. Both installed-client resident cycles,
all-caller-Job closure, exact delivery, finite broker exit, lifecycle,
shared-ancestor refusal, both publisher-death/recovery boundaries, durable
retention, old-action collection and final owned cleanup passed.
This closes the fixed-entry qualification slice, not consumer-installer
integration, default migration or host rollout.

### 2026-10-24 - Manager pre-publication adoption, source only

- Manager's qualified candidate now adopts the broker before selector
  publication and the first transactional activation callback. The adoption
  subprocess uses the already-qualified installed module entry and a live
  nested witness; no runtime-publication lease is held at that boundary.
  Broker runtime ownership is independent of application-selector rollback.
- Explicit Windows bootstrap construction and `self-install --apply` use the
  existing request queue and default native finite executor. A live inherited
  or scoped caller joins its owner rather than enqueueing behind itself.
  This is explicit installation, not an `ensure_worker` failure fallback.
  Arbitrary application arguments never enter the bootstrap request, and its
  bounded root/source/slot receipt prevents dispatch of a different request's
  subsequently selected generation.
- The broader contained selection exposed a missing transitive dependency in
  source-only repair recovery. Its exact source-closure allowlist now admits
  the driver's required `agent-zdd`; both source and materialized recovery
  remain construction-free. The corrected selection passed 245 cases with
  seven explicit skips in 82.61 seconds under the unchanged 180-second bound.
  Additional receipt-negative cases were then added and await the final pass.
  The final changed-surface selection, including those receipt controls,
  passed 250 cases with seven explicit skips in 76.51 seconds under the same
  180-second bound; changed Python lint and whitespace checks passed.
  Final direct-API admission and cross-drainer safe-context controls then
  passed 251 cases/seven skips in 95.57 seconds under that same bound. A raw
  apply without custody now refuses before construction; explicit requests
  restore their existing safe root/provider context rather than inheriting
  whichever caller drains the queue.
- The existing hosted-only fixture now performs first adoption through the
  real Manager adapter with a private default execution root before the
  direct installed-entry repeat and remaining native lifecycle controls.
  Exact-candidate hosted qualification is still pending.
- Documentation-impact review updated the Manager and shared-driver adoption
  contracts and the existing consumer changefile. Other consumers, actual
  cold Manager construction, normal launch-default migration, desktop,
  maintenance, review/CI, promotion and authorized rollout remain open.
  No shared-host Task, service, runtime, launcher or enrollment was changed.

Exact-candidate hosted run
[40091138033](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40091138033)
passed at `02cb0126913baccd08e268e3ed51d728ed4e2e65`. The recorded
`manager_installer_adoption` control is true: first adoption used the actual
Manager adapter, its private ordinary default execution root, the real native
executor and the target slot's installed adoption CLI. Both installed-client
resident cycles again proved all caller Jobs closed, exact delivery, finite
broker exit and actual cleanup. The shared-ancestor negative, lifecycle,
publisher-death recovery at both boundaries, durable generation retention,
old-action removal and final owned cleanup all passed.
This qualifies the Manager adoption adapter, not full cold Manager construction
or its live maintenance/desktop consumers; the draft and remaining gates stay open.

### 2026-10-24 - Worktrees native installation and pre-publication adoption

- The canonical installer runner now re-exports its scoped execution witness.
  Worktrees' Windows native construction joins that owner before any legacy
  command lifecycle or namespaced publication lease. Cold explicit installation
  submits a fixed native recipe to the same finite driver, not a new queue,
  lock or ordinary daemon-launch fallback. A native reentry marker requires a
  fresh authenticated witness check before continuing. Safe request context
  is retained; live publication/authentication tickets are not journaled.
- Both construction and installed settlement retain that owner, forward it to
  the installed candidate and adopt the broker before helper publication,
  Manager recovery or status-monitor activation. The candidate remains retained
  throughout, outside the runtime selector-publication lease. Application
  rollback preserves independent durable broker ownership. Mutable dev slots
  are not registered as actions; the ordinary launch default is unchanged.
- The real source-closure control exposed Worktrees' old local ZDD mapping
  conflicting with the driver's canonical transitive ZDD edge. Worktrees now
  uses the same canonical source, with ordinary release materialization.
  The actual Manager-return control also exposed a source-only fixture relying
  on parent `PYTHONPATH`, which the Manager intentionally clears. Each fixture
  hop now supplies its fixed source graph, without weakening production
  environment isolation, and asserts the actual request outcome.
- The focused Windows selection passed 103 cases with two explicit skips in
  109.60 seconds under the unchanged 180-second bound. It includes a real
  default native-driver execution into a copied no-install PowerShell fixture,
  live-witness reentry, both Manager-return cycles with the native window
  observer, raw/scoped handoffs, exact request checks and retained adoption
  before publication. Earlier fixture failures were corrected, not waived.
  A broader selection exhausted that same bound; its remaining surfaces are
  being isolated rather than increasing the budget or treating the timeout as
  passing evidence.
- The hosted-only protocol fixture now performs first adoption through the
  actual Worktrees adapter, then repeats through Manager. Shared-host guards cover
  its new entry before imports or mutation. Exact-candidate hosted execution
  is pending; neither these controls nor this source change authorizes host
  rollout, normal-default migration, or completion of the parent effort.

The remaining publication/rollback selection passed 102 cases with five skips
in 137.72 seconds except the isolated-import dependency guard: the process-only
test environment lacked Worktrees' declared `machine-transport`. The canonical
Worktrees runner then prepared its declared graph and passed all 11 dependency
guards, including that exact isolated child. No import-isolation policy was
weakened. The final custody/mode/native-entry selection passed 79 cases with
two skips in 82.47 seconds; three full native construction cases were explicitly
outside that focused selection. Each bounded group retained the original
180-second limit, resolving the earlier combined portfolio timeout without a
budget waiver.

The installed Windows `dev-release` entry now uses the existing JSON driver
boundary before its newly protected internal API. Its adapter preserves real
JSON-envelope capture as well as human output; six focused final controls
passed after self-review corrected the distinction between `sys.stdout` and
the canonical real-stdout JSON channel. Cold admission refuses an already-held
publication ticket before enqueue, and direct APIs acquire custody before
governance or allocation. The source-bootstrap child environment does not
import uninstalled driver dependencies on POSIX or contained first construction.
Install-contract regeneration changed only Worktrees' native human-output
declaration; all 13 installer contracts, changed Python lint and whitespace
checks passed. Full cold consumer construction and external/desktop qualification
remain open.

Exact-candidate hosted run
[40096105342](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40096105342)
passed at `208112add4a0da324acbbec53581622f2644e447`. First adoption used
the actual Worktrees adapter under the existing default native execution
driver, followed by Manager and installed-CLI repeat adoption.
`worktrees_installer_adoption`, `manager_installer_adoption` and
`driver_owned_adoption` are true. Both installed-client cycles proved all
caller Jobs closed, exact delivery, finite broker exit and actual child cleanup.
The shared-outer/different-immediate negative, both publisher-death recovery
boundaries, crash-surviving generation retention, old-action removal, lifecycle
and final owned cleanup all passed. This qualifies the Worktrees adoption
adapter, not full native Worktrees construction, normal launch-default
migration, maintenance or desktop behavior. The PR remains draft and held.

### 2026-10-24 - Bridge native custody and immutable helper retention

The next adapter is Bridge's existing Windows native installer. A canonical
payload-local helper submits only its fixed install/update/provision/uninstall
recipe to the existing finite execution driver, or joins the current raw or
scoped witness. Native reentry validates that live witness before self-staging.
The driver binds the full source closure, installation root and native options;
switches cross the PowerShell boundary as repeated named selections, not a
JSON-quoted command vector. The source bootstrap selects a base Python 3.11+
interpreter. Bridge declares and refreshes the same canonical driver dependency
in both installers and in retained snapshot materialization.

Native completion can retain the six canonical runtime helper/launcher files
while construction is held. Completion transfers to retention before broker
adoption; reuse verifies the existing bytes without repairing the immutable
slot. Bridge adopts from its installed interpreter before selection, manifest,
serving reconciliation or launch callbacks. Adoption remains inside update's
existing rollback boundary. Native uninstall removes only its adopted broker
under the same driver before deleting application runtimes. The action
generation stays retained through removal; pending publication requires explicit
installer recovery rather than deletion. Dry-run never enters the driver.

Unit and simple end-to-end evidence:

- The real copied Bridge PowerShell front door, default finite native executor
  and authenticated native reentry passed for install and uninstall, with exact
  option forwarding; the dry-run control created no driver or runtime state.
  The owner/rollback/removal group passed 78 tests with 29 platform skips in
  62.59 seconds. It includes raw/scoped custody, exact admitted identity, retained
  removal and adoption failure before publication. The additional dry-run control
  passed in 9.63 seconds.
- Source construction/retention and native compatibility controls passed in the
  declared Bridge environment. Adding the new callback initially exposed
  incomplete extracted-function fixtures; they now name that boundary explicitly
  and seed complete immutable helper evidence instead of changing production
  isolation. All 48 selected Windows completed-slot controls ran: 47 finished
  before the 180-second aggregate bound, and the remaining lock-contention
  control passed separately in 3.37 seconds. This is not a passing combined run
  or an increased timeout.
- Completion-result/shared-host-refusal controls passed 219 tests with 16 skips;
  canonical distribution controls passed 53 tests with 17 native-link/platform
  skips. Ruff, generated-helper consistency and all 13 plugin install contracts
  passed. The broader six-file 180-second run timed out; its observed fixture
  failures were diagnosed and corrected in the focused controls above, not
  waived.

The declaration-age audit also exposed this PR's optional procutil broker edge
being interpreted as a public registry dependency. Its canonical local ZDD source
is now declared explicitly and covered by the package-data contract, while base
procutil still has no runtime dependencies. No age floor or feed policy changed.

The hosted probe now uses the actual Bridge completion/adoption functions for
first adoption, followed by Worktrees, Manager and installed-CLI repeat
adoption. It also exercises the Bridge driver's owned component-removal path.
That changed probe has not yet run at this journal point. Full fresh Bridge
dependency construction, normal daemon-default migration, remaining consumers,
maintenance, desktop observation, portable regression, review/CI, promotion and
host rollout remain open. No shared host was changed; #7833 remains draft and
held.

Exact-candidate hosted run
[40101608420](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40101608420)
passed at `84e3b766ff027db062f4b8c18ad998988bfb084a`. The actual Bridge
completion/adoption adapter performed first adoption under the default finite
execution driver and private fixture profile; Worktrees, Manager and the
installed CLI repeated adoption. `bridge_installer_adoption` and
`bridge_installer_removal` are true. Both installed-client cycles again proved
all caller Jobs closed, exact delivery, finite broker exit and actual child
cleanup. The shared-outer/different-immediate negative, both publisher-death
recovery boundaries, crash-surviving generation retention, old-action removal,
the lifecycle controls and final owned cleanup all passed. This qualifies the
Bridge adoption/removal adapter, not full cold Bridge construction or another
consumer, the normal launch default, maintenance, desktop or deployment.
The PR remains open, draft and held.

### 2026-10-24 - Codespaces integration and non-editable deployment correction

The operator reiterated: "We should not stll have editable venvs around" and
"Everthing needs to be deployed into an immutable, versioned slot". This is
conjunctive with the broker and feed gates, not another deployment exception.
The operator separately authorized the narrow shared-materializer source fix
while preserving CI-controller, merge, host-update and rollout holds.

Codespaces now has a fixed native driver entry before staging, retained
completion helpers, adoption before selection/Connection Owner callbacks, and
owned component removal before application cleanup. Bridge shares the extracted
entry helper. The actual copied native entry controls passed 28 cases, including
Bridge and Codespaces install/update/provision/uninstall and dry-run routes.
Codespaces adoption/removal refusal and existing reuse controls passed 10 cases
with four POSIX skips. The hosted fixture now starts with the Codespaces
adoption adapter and ends with its removal; that changed fixture is not yet
qualified at an exact published candidate.

Deployment CLI admission now refuses explicit editable operands and shared
artifact links and forces non-editable copied installation across the canonical
native adapters. Codespaces development generations no longer request editable
packages or advertise live checkout imports. Its full package/dependency packet
is resolved together rather than repeatedly snapshotting the same graph for
each library. Release materialization walks the complete nested closure, with
cycle/shared-edge handling and retained provenance checks. Focused closure and
CLI refusal controls passed 17 cases with one Windows symlink-privilege skip.
The real offline UV CLI control also passed: three distributions declared through
editable source mappings were installed into a fresh venv, all checkout modules
were then changed, and isolated installed imports retained their original code,
venv-local origins and non-editable installation metadata.

Native qualification remains incomplete. The original sequential dependency
fixture reached only the first package's retries and one subsequent package
within its 45-second execution budget. The combined packet's native ARM64 body
now passed in 38.20 seconds, but fixture setup took 50.33 seconds and that
environment lacks the pytest timeout plugin; this is not a passing 60-second
item or a budget waiver. The x64 lane still exhausts that item limit, including
an observed unrelated whole-CLI import in an autouse fixture; installer-only
fixtures now avoid that unnecessary import. The full development
generation/rollback fixture and broader materializer lane also remain open.
No host installation was changed or claimed migrated. Completed-slot editable
reuse/refusal, every remaining deployment route, full source/assembly identity,
maintenance/default/desktop consumers, portable evidence, review/CI, promotion
and separately authorized rollout remain parent completion gates.

### 2026-10-24 - Installed-environment completion and reuse admission

The canonical versioned-runtime publisher now checks installed Python content
without executing candidate imports. New completion, including explicit
development completion, refuses editable origins/finders, egg links, linked or
hardlinked package files, external/system site-packages and unverified executable
path hooks. Standard UV and setuptools bootstraps remain supported. Valid
completed-marker admission applies the same check and reports
`health-repair-required` without deleting, rewriting or selecting that slot.
The attached construction protocol rechecks completion even after initial reuse
admission, so a later detected mutable installation cannot receive a successful
completion reply. New unpublished failures still use owned candidate cleanup.
The canonical implementation and construction adapter are synchronized to all
13 runtime plugins, Manager and the shared driver.

The new static matrix plus existing Bridge versioned-runtime regression selection
passed 169 cases with 22 explicit platform skips in 136.60 seconds. Additional
development and actual attached-protocol refusal controls passed 57 cases with
four Windows symlink-privilege skips in 28.49 seconds. Those native controls
preserved the prior active generation and refused mutation introduced after
both new construction and completed reuse admission.

The existing offline UV fixture now also publishes completion and verifies
byte-identical reuse after its three copied packages survive checkout-module
changes. Its first enhanced canonical-editable attempt timed out at the existing
30-second dependency-CLI bound before reaching completion admission. A bounded,
phase-instrumented repeat passed in 18.71 seconds: venv creation 4.330 seconds,
production installation 9.855 seconds, isolated imports 0.957 seconds and the
expected unowned-source refusal 1.673 seconds. The initial timeout's cause is
not established; this is no latency waiver or overall performance qualification.
After adding actual hardlink refusals, the final static matrix and real
canonical-materialized graph passed 56 cases with four symlink-privilege skips
in 84.19 seconds. That ARM64 environment lacks the pytest timeout plugin;
native calls retained their 30-second bounds, but no 60-second item qualification
is inferred. Source lint, canonical synchronization, whitespace and all 13
runtime install contracts passed.
The final case-insensitive metadata refusal and all-adopter parity selection
also passed five cases after self-review found the Windows filename-alias edge.

Documentation-impact review extends the install contract and existing consumer
changefile. This closes the installed-environment admission slice, not all
installed launch paths or full source/assembly identity. Cold native construction
budgets, remaining consumers/default/maintenance/desktop integration, portable
qualification, hosted exact-candidate adoption, review/CI, promotion and
authorized rollout remain open. Nothing was installed or migrated on a shared
host; the draft and all deployment/controller holds remain in force.

### 2026-10-24 - Preserve incremental publication within its existing deadline

The next managed incremental publication of #7833 exhausted its unchanged
30-second push deadline. Provider inspection confirmed the draft still named
the prior published candidate; no merge, readiness change or rollout occurred.
Direct measurements isolated a publication bottleneck: the identifier guard
took 80.26 seconds for 102 changed paths and the blob guard took 81.19 seconds
for 106 introduced blobs, with both checks passing. Each guard spawned Git
separately for individual objects.

The identifier guard now resolves selected paths from the exact Git tree and
reads their distinct blobs in bounded batches. The size guard batches object
sizes while preserving every introducing commit, path and staged-index check.
Missing, foreign, malformed and truncated batch responses fail explicitly.
Neither the deadline, identifier policy, blob caps nor publication scope changed.

All 86 guard regressions passed across two disjoint contained selections:
17 focused batch/exact-ref cases in 88.17 seconds and the remaining 69 cases
in 663.20 seconds. Six actual native pre-push hook controls also passed in
122.24 seconds, including every-ref checking and unselected Git trees with
Unicode, tab and newline paths. The original candidate's unchanged checks
then took 20.64 and 5.27 seconds respectively under concurrent test load.
Ruff and whitespace checks passed. The managed push itself remains the next
publication-boundary proof; these measurements do not qualify native installer
latency, the hosted broker candidate or any deployment.

Documentation-impact review records this publication prerequisite here; no
installer, release policy or user-facing command contract changed. Clean-room
construction and external runtime lanes do not apply to this inert Git-object
optimization. The exact-candidate hosted broker lane, all remaining broker and
immutable-runtime qualification, review/CI, promotion and authorized rollout
remain open, with the draft and existing holds intact.

The first batched candidate still exhausted the managed deadline; provider
inspection again confirmed the old draft head. An actual-hook measurement took
40.22 seconds. Profiling then isolated a second, independent defect: the
optional live identifier sweep's nominal ten-second subprocess timeout spent
28.87 seconds in its launcher/pipe boundary. Killing only the launcher could
leave descendant-held pipes delaying return.

The sweep now uses the existing canonical caller-owned process primitive from
the repository's stdlib-only procutil source, retaining the same ten-second
deadline with separate one-MiB stream limits and owned descendant cleanup.
Ordinary absence/settled timeout still has the existing optional-source meaning;
unconfirmed cleanup or output-limit failure is no longer swallowed as absence.
Nineteen contained live-sweep controls passed in 129.36 seconds, retaining
malformed/partial/configuration refusals and adding exact owner/deadline and
unconfirmed-cleanup controls. The actual unchanged-ref native hook then passed
in 17.86 seconds under concurrent load. No shared-host service, Task, registry,
runtime selection or update was changed. Managed publication remains the final
transport proof, not a reason to waive its deadline.

The subsequent managed push completed both publication guards but still
exhausted the thirty-second transport deadline; provider inspection confirmed
that the draft remained at the earlier candidate. The operator explicitly
authorized a sixty-second publication timeout after those measurements.
The repository's publication setting and contribution reference now match that
authorization. This changes only managed push transport: no guard is skipped,
and installer, construction, process-lifetime, release and test budgets remain
unchanged. Draft/merge, controller and host-rollout holds also remain unchanged.

### 2026-10-25 - Exact-candidate Codespaces fixture failure and lexical-root repair

Managed incremental publication succeeded at
`94e9c7a9a39244650c02dbfb0e532c7b63b65230`; #7833 remained open and draft.
The approved hosted run
[40168246848](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40168246848)
executed that exact candidate. Its synthetic activation result recorded two
successful cycles and confirmed cleanup. The separate production-protocol result
failed before first Codespaces adoption, with `supported: false`,
`cleanup_confirmed: false` and no completed cycles. Those results are not a
passing qualification.

The literal failure was the fixture's missing `controller/versioned_runtime.py`.
The extracted Codespaces adapter resolves its helper through PowerShell's
lexically scoped `$PSScriptRoot`, but the fixture placed its generated script
one directory above the retained helpers. Assigning that automatic variable
outside the extracted function did not change its definition-site resolution.
The subsequent unconditional finalizer also obscured that initial error.

The inert native-adoption renderer now places the generated script beside the
copied helpers and preserves the adapter's actual path semantics. Its finalizer
uses the same function-presence guard as the owning installer, without creating
a second cleanup path. Thirty-one contained fixture contracts passed in
12.10 seconds, including both rendered adapters, all shared-host refusals and
the existing read-only missing-Task control. Ruff and whitespace checks passed.
The registration/protocol fixture itself was not executed locally. An updated
exact-candidate hosted run remains required; no failed cleanup or prior green
result is promoted into that evidence. Production consumer/default/maintenance,
desktop, immutable construction, portable and deployment gates remain open.

The repaired exact candidate
`2da98e083f4981bc1139a08a8471350b48e356a1` subsequently passed hosted run
[40170079312](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40170079312).
Its actual production-protocol JSON has `supported: true` and
`cleanup_confirmed: true`. Both installed-client cycles proved all caller Jobs
closed, exact delivery, finite broker exit and actual child cleanup. Codespaces
performed first adoption and owned removal; Bridge, Worktrees and Manager repeat
adoption, the shared-outer negative, both publication-death recoveries,
crash-surviving generation retention, old-action collection and lifecycle
controls also passed. This closes that exact direct-client fixture gate, not
normal update-worker startup or every consumer.

### 2026-10-25 - Integrate the normal Python daemon entry point

The remaining startup trace is concrete: `update_driver.core.ensure_worker`
calls public `spawn_daemon`, whose broker-required branch still selected the
legacy WMI implementation even after successful explicit Task-client
qualification. The reviewed finite-broker contract requires the actual consumer
path, not just a separately callable transport.

The source now lazily routes that branch through the installed consumer's
adopted Task client. Ordinary direct creation, POSIX launches, explicit shorter
lifetime and contained behavior remain unchanged. Missing optional native
transport support is an explicit `DaemonSpawnError`; other import bugs are not
reclassified. Missing/stale adoption or interrupted publication still refuses
without installation, repair or a WMI fallback. This is an intentional source
integration under the existing held draft, not an installed migration.

The hosted fixture now drives both installed cycles through public
`spawn_daemon`, retaining actual-child offers and all-Job closure controls.
Expected launch diagnostics share its log, so resident JSON observations are
selected explicitly rather than treating a launcher header as resident output.
The updated default-entry lane has not yet run at this journal point.

The targeted contained broker/generic-process/split-log/fixture selection passed
199 tests with two explicit platform skips in 48.67 seconds. It includes actual
isolated stdlib-only missing-support refusal, exact default transport forwarding
without legacy activation, direct and contained behavior, and shared-host
fixture refusal. Ruff, whitespace and canonical vendored-reference checks passed.
Documentation-impact review updates the process library, launch pattern and
existing consumer changefile. Clean installed default-entry proof, actual
maintenance/update-worker startup, remaining installer/default consumers, full
cold construction, desktop/portable evidence, review/CI, promotion and authorized
rollout remain open. No host runtime, registration, service or pending update
request was modified.

The normal-entry exact candidate
`d16289cbc6026396cb4e3ec5955b4bdccfd56d3a` then passed hosted run
[40172615304](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40172615304).
The actual protocol JSON records `default_daemon_entrypoint: true` in both
installed cycles, with finite exit, all caller Jobs closed, exact delivery and
actual child cleanup. Every previous adoption, negative topology, crash
recovery, retention, lifecycle and removal check remained true, and overall
cleanup was confirmed. This qualifies the public entry point at that candidate,
not actual execution-driver startup or an installed host update.

### 2026-10-25 - Add actual shared update-worker startup qualification

The next hosted-only control uses installed `ensure_worker` and the ordinary
`drain` executor against its own disposable queue and synthetic product.
It requires acknowledged startup before a held installer gate opens, pins the
actual worker before closing both caller Jobs, and checks continued worker
liveness. Identical pending and active requests must coalesce; two distinct
project contexts, including an explicitly absent second-context variable,
must execute without overlap and complete before the finite worker exits.
This does not invoke a real update or exercise the held shared host's pending
request. An incomplete worker phase retains explicit cleanup uncertainty.

All new worker entry points refuse a shared host before imports or native work.
The contained refusal/render/read-only controls and existing startup-receipt/
pending-identity regressions passed 37 cases in 5.45 seconds; source lint and
whitespace checks passed. The positive worker fixture is deliberately not run
locally: its updated exact-candidate hosted result remains required. The test
portfolio reference now names that proof's synthetic-product boundary.
Actual Manager/maintenance consumption, independent explicit-update overlap,
remaining consumers, full cold construction, desktop/portable, review/CI,
promotion and authorized rollout remain open, with all existing holds intact.

The actual shared-worker exact candidate
`84d71e200cdeb6110a6d7e44a90771f8cf4424ab` passed hosted run
[40173462124](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40173462124).
Its production-protocol JSON confirms actual `ensure_worker`, startup before
the held synthetic installer, survival of all caller-Job closure, pending and
active coalescing, complete distinct scopes with exact context/unsets, no
transaction overlap and finite worker exit. All previous default-entry,
adoption, crash, retention, lifecycle and removal checks remained true; overall
cleanup was confirmed. Synthetic products are explicit in the result. This is
not the live Manager update, an authorized rollout or acceptance of a previously
failed installed request.

### 2026-10-25 - Bound metadata-fixture setup and qualify native development rollback

Profiling isolated fixture construction rather than treating a timed-out native
item as a pass: canonical metadata setup spent 13.55 seconds, principally in
file opens and copy operations, and the module fixture eagerly built both
layouts. The ordering/retry fixture now copies only the metadata, README and
scripts it previously retained after trimming; its complete real dependency
metadata closure is unchanged. Module-scoped parametrization constructs only
the requested local or canonical layout. Actual artifact/source-corpus fixtures
are not reduced.

All four x64 native PowerShell packet cases passed with the existing pytest
timeout plugin enforcing the unchanged 60-second item budget and unchanged
180-second aggregate bound: 49.95 seconds total. Local/canonical retry bodies
took 14.74/16.41 seconds, fatal bodies 6.94/7.35 seconds, and layout setup
3.28/0.91 seconds. This is native ordering/retry/error-contract evidence, not
full cold production dependency construction.

The separate native development-generation control now emits bounded phase
labels so a future timeout identifies initial construction, injected dependency
failure, retry or release/restore. It passed in 30.04 seconds under its existing
45-second native-call and 60-second item limits, preserving selection, completion
and environment bytes across failure, a distinct copied retry generation, and
restoration of the original numbered release. The canonical materialization
control also passed; those two items took 36.17 seconds total. The earlier
timeout and cleanup failure's root cause remains unproven and is not erased or
waived by this passing run. Full native/artifact/portable qualification and
production construction budgets remain open.

### 2026-10-25 - Integrate Dispatch, PullRequests and Vault native broker ownership

The remaining native installer slice adds closed execution-driver recipes for
Dispatch, the legacy PullRequests installer and Vault. Admission precedes
publication custody. Completed-slot helper retention spans adoption before
selection or serving callbacks, including reused complete slots; owned uninstall
refuses runtime removal if broker removal fails. Dispatch and Vault preserve
client-only suppression, and contained/POSIX paths do not register a broker.
Dispatch and PullRequests declare the local execution-driver source closure;
their independent payloads now receive canonical engine/driver support assets.
Scoped Dispatch downgrade, base-Python, routing, run-directory, supervision and
deadline context and the PullRequests deadline remain explicit. No credentials
or ambient installation-directory hints become request identity.

The inert native reuse controls initially exposed fixture defects, not passing
consumer evidence: Dispatch's full function bodies exceeded Windows' command-line
limit, and PullRequests' inline script had no lexical script directory. Both
helpers now execute owned temporary script files. The contained Dispatch
selection passed 15 cases in 51.63 seconds and PullRequests 14 in 25.00 seconds.
Vault's adoption and existing service-launch selection passed 26 cases; its
six focused adoption cases also passed separately. Canonical synchronization,
focused Python lint and whitespace checks passed.

The actual copied native-entry fixture still has unresolved cold-path timing
evidence. A second freeze of its already-copied library closure spent 55.16
seconds before native entry, so the fixture now retains real production source
and manifests once, without copying unrelated library tests/docs or freezing the
same owned copy twice. An isolated native entry completed with the unchanged
30-second subprocess limit, but other runs exhausted that same limit. A later
pytest item passed in 19.45 seconds with a separate 60-second test watchdog;
the subprocess limit remained 30 seconds. These observations do not establish
the earlier timeout's root cause or complete the expanded native action matrix.
Failure diagnostics now surface the fixture's own driver logs.

The hosted-only protocol fixture additionally renders the actual Dispatch,
PullRequests and Vault completion/adoption functions and exercises their owned
removal and first adoption after removal through the default execution owner.
Local renderer/refusal/read-only controls passed 49 cases in 9.14 seconds;
no positive protocol fixture or Task registration ran on a shared host.
Exact-candidate hosted execution of this added slice is still required.

This does not close Dispatch retained-record restore, explicit PullRequests
installation cells, MCP's native bootstrap, or Index's separate engine. Index's
actual engine-start management process imports procutil from its primary runtime,
not the interpreter named by the launched engine argv; engine-only adoption is
therefore insufficient. Existing complete engine slots also lack retained
bootstrap helpers and cannot be retrofitted in place. Correct caller ownership
and fresh construction remain necessary. Full cold construction, maintenance/
explicit-update overlap, desktop/portable qualification, review/CI, promotion and
authorized rollout remain open. PR #7833 remains draft and held.

### 2026-10-25 - Add MCP bootstrap and Index primary-runtime ownership

MCP's fixed native recipe targets `init.ps1`, with its actual install/init/
provision actions rather than silently assuming every plugin owns `install.ps1`.
Its completed immutable runtime retains bootstrap helpers before broker adoption
and selection. The new explicit Windows `broker-remove` action removes only
that installation's broker under live execution custody; it does not delete
configuration, remove runtime files, stop the optional serving process or make
that process a prerequisite. Source and runtime dependency closures include the
local execution driver. Stamp, contained and non-Windows behavior stay separate.
The seven inert native/structural controls passed, and an actual copied native
`init` entry completed in 13.25 seconds with the unchanged 30-second child limit.

Index adopts the primary CLI environment, whose procutil actually owns normal
engine management, rather than guessing that the engine argv selects broker
ownership. Its optional native-installer closure leaves base engine dependencies,
the declared Python floor and seasoning unchanged. Fresh completion retains
helpers and adoption checks the exact primary construction session; helper-less
completed generations refuse rather than being modified. Source-only dedicated
and existing engine-daemon controls passed 48 cases, including eight primary
adoption controls rechecked after canonical integration. The broader engine
lifecycle selection could not collect in the selected environment because
NumPy was absent; no dependency installation or full-suite pass is claimed.
The explicit engine-update fallback's engine-owned caller remains under review.

The real Windows PowerShell 5.1 frontend exposed another concrete defect:
PullRequests' native governance probe lost the embedded JSON quotes through
ordinary executable argument forwarding. It now uses the existing Python-free
native process helper, preserving structured absent-legacy metadata and stderr.
Actual Windows PowerShell and PowerShell 7 round-trip controls pass; the combined
PullRequests inert selection passed 16 cases. The generic copied-entry fixture
also incorrectly treated an optional persistent-environment marker as a required
boundary, so it retained the real PullRequests lifecycle body. It now asserts
and truncates at the mandatory shared self-stage boundary before appending the
inert body. Admission itself remains real, with no governance bypass.

Current canonical integration passed 31 shared driver/custody controls,
55 renderer/refusal/read-only controls, all 13 installer contracts, synchronization
and focused lint. The complete copied native action matrix is being re-exercised
with a separate setup watchdog and the unchanged 30-second native child limit.
Earlier cold Dispatch deadline failures remain evidence, not a waived gate.
The hosted-only fixture now includes all five newly wired native adapters and
retains the surviving replacement generation for post-GC removal/re-adoption;
it has not yet run against this new exact source candidate.

Explicit PullRequests cells still lack canonical retained bootstrap publication
and a compatible execution-owned completion/removal contract. No adapter
retrofits those completed cells or substitutes ambient context. Dispatch
retained-record restore, full native/cold/portable proof, maintenance/explicit
update overlap, required review/CI, promotion and authorized rollout remain open.

### 2026-10-25 - Remove redundant native governance hosts

The complete copied native-entry matrix passed 24 of 25 cases, with the
PullRequests uninstall case exhausting its unchanged 30-second child deadline.
It used the naturally discovered native bootstrap interpreter rather than
pinning an emulated interpreter. The failure's empty driver log does not prove
where its elapsed time was spent.

PullRequests' frontend now invokes the same read-only native governance script
in its own script scope with structured parameters, rather than starting a
second PowerShell host. Both outer and participant admission still run; no
policy, context, provenance or execution-owner check was skipped. Native
diagnostics remain on stderr and a failing validator cannot admit even
success-shaped JSON. Four Windows PowerShell/PowerShell 7 controls passed,
including same-process execution, structured probe preservation and nonzero
refusal. The four real copied PullRequests driver entries then passed with
15.24--20.91-second calls under the unchanged 30-second child limit. These
measurements qualify that bounded correction, not the unexplained earlier
timeouts or a full cold installation.

The other 21 copied entries passed in the preceding matrix. The held draft's
changed consumer adapters still require exact-candidate disposable hosted
qualification; shared-host registration, rollout and all remaining parent
gates stay held.

Exact-candidate disposable Windows run
[40190546045](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40190546045)
passed at `667e0379278c38a767229a9d3d9d16319958bb9d`. The actual
Dispatch, PullRequests, Vault, MCP and Index native adapter bodies adopted
their synthetic immutable installations and each proved owned removal and
first adoption after removal. Existing Manager, Worktrees, Bridge and
Codespaces adoption also passed. Both actual default daemon cycles survived
closure of all caller Jobs, preserved exact delivery, retired the finite
broker and confirmed child cleanup. Negative shared-ancestor topology,
publication-crash recovery, retained generations, replaced-action collection
and update-worker coalescence/context/exclusion controls passed. Artifact
`windows-broker-activation-40190546045` preserves both result documents.

The update-worker products remain synthetic. This is not ordinary installed
maintenance, explicit-update overlap, desktop/foreground observation, full cold
construction, retained-record restore or explicit-cell qualification. No
shared-host registration or rollout occurred, and the PR remains draft/held.

### 2026-10-25 - Bind retained Dispatch restoration to execution custody

Windows Dispatch restoration now enters the same finite native installation
driver before staging or publication. Request identity binds the exact bounded
record bytes by digest, not the helper's private copy path. Both native
re-entry and the retained helper reject changed input. The helper preserves the
original bytes while holding the existing generation lease; restoration adopts
the qualified immutable action before selection without construction, marker
rewrites or GC. Helper-less historical assemblies refuse rather than receiving
new content in an already-completed generation.

The focused driver selection passed 37 cases. Seven restore cases passed,
including an actual copied restore entry through the default native executor
in 11.70 seconds under the unchanged 30-second child limit. That entry uses a
synthetic record and inert action body; it does not qualify full historical
restoration or Task adoption. The retained-record/native-boundary selection
passed 31 cases with three POSIX-only skips, covering byte changes, mismatched
assembly bytes, private-copy preservation, real GC retention and refusal before
selection/publication.

An encountered Bash reuse fixture timeout exposed its extraction order: the
extractor sourced canonical helpers after its mock declarations, replacing the
intended inert publication functions. It now loads sources before installing
those mocks and explicitly supplies its inert health/lock dependencies.
Failure diagnostics remain, and no production or child deadline was widened.
The shared fan-out tests also use the existing native-symlink capability fixture
rather than failing before exercising synchronization on a Windows account
without that privilege. Missing capability remains a skip, not positive
symlink-refusal evidence or permission to alter host settings.

Index's generated engine-local launcher is a foreground `engine run` consumer,
not a detached manager. Actual generated-launcher and CLI-boundary controls,
primary-manager ensure/restart, role policy and immutable engine reuse passed
26 cases. No engine-local adoption or mutation was added. Arbitrary direct
engine-interpreter management is not the supported detached management path.

Explicit PullRequests cells remain a shared-contract gap: legacy numbered
completion/retention and broker discovery do not bind cell ownership,
namespace/install generations and cell completion. No speculative adapter or
completed-cell retrofit was applied. A cell-qualified shared construction,
execution-custody, adoption and removal seam is still required. Full cold,
maintenance/explicit-update, desktop/portable, final review/CI, promotion and
authorized-rollout gates remain open.

Canonical fan-out controls then passed 54 cases with 79 explicit capability/
platform skips; the previously stale Dispatch/Index human-output declarations
were regenerated. The symlink cases could not exercise native creation without
the unavailable Windows privilege and remain unqualified locally. All 13
installer contracts and canonical engine/runtime synchronization remain required
before publishing this slice. These results do not waive the separate POSIX lane.

The persisted functional source
`37a40ff1d97acb0c08d2e557b5f7aa219adf377e` subsequently passed disposable
Windows run
[40193560692](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40193560692)
with confirmed final cleanup. The five consumer adapter lifecycles, both default
daemon cycles/all-caller-Job closures, existing foundation/Manager/Worktrees/
Bridge/Codespaces adoption, crash/retention and synthetic-worker controls passed
again. The fixture does not exercise a full retained-record restoration or the
missing cell-qualified lifecycle. The PR remains open, draft and held; no
shared-host adoption, source merge or rollout was performed.

### 2026-10-25 - Cell-qualified lifecycle work paused by the operator

The operator selected implementation of the shared cell-qualified lifecycle.
The unqualified source slice adds fixed cell identity, retained canonical
installation-context validation of snapshot/ownership/completion and receipt
generations, caller/action qualification, a closed cell recipe on the existing
execution driver, retained helper publication before completion, adoption before
selection and an owned component-removal action. Target activation now accepts
the originally admitted receipt generations instead of silently rebinding them.
Completed helper-less cells refuse rather than being retrofitted.

Three inert construction/adoption controls passed with native-safe fixture paths
under the existing item and aggregate bounds. An earlier long-path fixture run
timed out and is not passing evidence. The next focused driver/reuse/generation
batch was stopped at the operator's explicit request; its results are incomplete
until separately captured and assessed. This is a local work-in-progress
checkpoint, not qualified cell installation, a reviewed implementation, or
permission to publish, merge or deploy it. The previously recorded hosted runs
qualify only their exact older functional candidates.

The parent effort, feed construction objective and existing claims remain open.
No further implementation or background qualification continues after this stop.

### 2026-10-27 - Resume the preserved broker source checkpoint

The operator resumed the stopped work with this direction (verbatim):

> Got it. Ramp up into that session and finish driving that work, since it's on your path

Recovered the later successor delta and the clean, locally committed cell
checkpoint before continuing. This resumes source implementation and permitted
qualification; it does not waive the existing shared-host registration,
rollout, updater-replay or trusted-controller holds. The draft stays held until
the complete candidate has earned its remaining technical and review gates.

The three stopped focused selections have now settled under their original
contained item and aggregate bounds: native-driver contracts passed 45 cases;
cell reuse/missing-helper/adoption-refusal passed three; admitted-generation
and running-activation refusals passed five. These are inert local controls,
not actual Scheduled Task adoption, cold construction, desktop observation or
qualification of a newly integrated candidate.

Preserved a local backup of the complete inherited branch before integrating
current Dev. Canonical source and copied helpers must retain subsequent upstream
changes rather than reintroduce the older execution or daemon-admission paths.
The independently tracked feed-construction objective remains open under #7529;
no terminal updater is replayed and no inherited claim is discarded.
