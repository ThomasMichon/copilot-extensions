# Persistent Installer Diagnostics

- **Slug:** `persistent-installer-diagnostics`
- **Repo:** copilot-extensions
- **Branch(es):** reviewed proposal, independent diagnostics and qualified broker slices against `dev`
- **Created:** 2026-10-23
- **Status:** Active
- **Vision:** [Installer & Configurator](../../../visions/installer/README.md): independently installable plugins and truthful installation/update diagnostics
- **Umbrella issue:** #8370
- **Related issues:** #6812, #7743, #6859

## Guiding Intent

An installer must retain discoverable diagnostics even when its parent's
standard streams disappear. A failed, interrupted or timed-out orchestrated
attempt must identify its installer logs without depending on successful live
output forwarding. Logging must not introduce pipe deadlocks, alter native
results, disclose credentials or become another installation authority.

The diagnostics repair proceeds independently of the shared daemon-broker
replacement, without a dependency wait. Historical workspace completion does
not establish controller availability. Diagnostic success never waives native
lifetime, ownership, cleanup, review or deployment requirements.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| `steady-anvil` | Integration, reviewed plan, diagnostics publication and completion | Managed source worktree |
| Logging worker | Native logging helpers, reached child boundaries and focused regressions after plan admission | Coordinator-owned source slice |
| Broker investigator | Reusable finite-broker evidence and a separately admitted continuation design | Coordinator-owned evidence slice |
| Existing observability and recovery owners | Retain unrelated #6812 and #7743 work | Existing issue threads |

## Coordination

- **Topology:** review this proposal first; diagnostics and broker qualification
  use independently reviewable slices. Shared consumer integration is serialized.
- **Host (owns PRs):** `steady-anvil`.
- **Delegates:** logging implementation is admitted only after this plan lands;
  broker investigation is read-only until its bounded continuation is admitted.
- **Handoff:** retain this complete objective, current slice, exact source/native
  evidence and every deployment obligation. One merged slice is not completion.
- **Independent continuation:** the operator explicitly permits necessary
  continuation or reimplementation of #6859 without a dependency wait; this
  does not establish another controller's liveness or admit broker edits in the
  logging slice. Held #7833
  remains held; its source is reference material, not permission to merge or
  deploy an unqualified candidate.

## Context

The reproduced Windows path loses output at both a Manager-to-engine handoff
and a retained runtime-publication child: a no-window child receives no
explicit stdout/stderr destinations. A parent request log alone therefore
cannot establish complete installer diagnostics.

The current native installation also encountered a distinct daemon lifetime
failure. Its WMI bootstrap authenticated successfully but remained in a Job,
so the shared broker refused durable success. A pre-spawn cutover breadcrumb
then left installer settlement waiting for recovery that requires an absent
child handle. These are separately recorded on #6859 and #7743; logging does
not itself repair lifetime or settle uncertain publication.

This effort closes installer observability intent and extends the existing
install contract with non-authoritative pre-admission diagnostics. It preserves
standalone installation, canonical native entrypoints, immutable runtime
publication and existing execution/cutover ownership.

## Request

The operator requested the following. The private control-repository identifier
is redacted; no surrounding requirement is omitted:

This historical request is not a current owner-liveness assessment. The logging
slice has no dependency wait and conveys no broker-edit permission.

> Can we fix this by having the installers always log to some location? Then at
> least at the end of an orchestraed attempt, we can always find the logs to
> capture the error that way. Be careful around stdio piping that you don't
> cause deadlocks. Let's also avoid blocking in upstream work. Identify which
> [private control-repository] worktrees own those PRs; ignore 6859 as its owner
> isn't avaiable to drive it anymore; reimplement it if needed

The operator confirmed this effort's slug and these policy choices:

- "Use a private per-user bootstrap diagnostics area, marked unattributed;
  link it after installation validation"
- "Retain completed attempts for 30 days; never prune active/incomplete attempts"
- "Continue with an explicit warning and report missing diagnostics"

The explanation accepted for installation attribution was verification of the
specific installed runtime's identity, ownership and log path, not trust in a
source checkout.

For one isolated native integration diagnostic, the operator answered `true`
to this explicit request:

> The repaired diagnostics controls now pass independently. The separate real
> installer/monitor check hit the runner's 300-second group cap; the test
> already declares a 900-second budget and includes cold source preparation
> plus a construction subprocess with its own unchanged 300-second limit. May
> I run this one integration case with a 900-second group cap so it can report
> its actual outcome? Installer deadlines, the repaired 10-second check, and
> all assertions would stay unchanged.

This is a single diagnostic invocation, not a permanent runner/test-policy
change, a longer installer deadline or permission to waive a failure.

After that invocation timed out and the standalone/partial-log gaps were
repaired, the operator separately authorized one fresh invocation:

> The earlier 900-second integration run exhausted its one-time authorization
> before it produced useful diagnostics. The logging gaps are now repaired in
> local source. May I run that isolated installer/monitor case once more with
> the same 900-second test-group budget, after the Linux checks finish?
> Installer deadlines, containment, assertions and the installed runtime would
> remain unchanged.

The answer was `true`. This second authorization is also single-use and does
not renew the original permission or change any production deadline.

## Plan

Implementation decomposition and mechanisms below are **agent-recommended**;
the operator requirements and selected policies above remain authoritative.

### Phase 1 - Reviewed contract and ownership reconciliation

- [x] Land this proposal through normal current-head review and required CI
  before implementation.
- [ ] Trace relevant PR source attribution and creator relationships to their
  control worktrees; distinguish verified ownership from unavailable evidence.
- [x] Record the independent diagnostics claim and bounded broker continuation,
  preserving unrelated active owners and existing qualification holds.

### Phase 2 - Installer-owned durable diagnostics

- [ ] Initialize private, unique attempt diagnostics before admission where the
  native entrypoint can execute; mark early records unattributed and associate
  them only after installation validation.
  - [x] Qualify the representative retained-publication association boundary
    without claiming installed/serving rollout acceptance.
- [ ] Reuse canonical native packaging/materialization and diagnostic/redaction
  conventions rather than introducing a new service, broker or whole-script
  supervisor.
  - [x] Implement the first bounded native-helper/representative-Worktrees
    milestone and focused controls, before expanding other consumers.
  - [x] Qualify standalone Python producer initialization, inherited correlation
    and sanitized output/error evidence before implementation imports.
- [ ] Give staged and retained installer children their own discoverable writer
  records and explicit output destinations, correlated with the owning attempt.
- [ ] Preserve output independently of optional live forwarding. Drain both
  channels safely and bound post-child draining by written bytes, not EOF from
  retained descendants.
- [ ] Keep sanitized archival diagnostics distinct from access-restricted
  transient raw capture. Do not log environment, credentials or authority
  witnesses; preserve protocol stdout and exact native outcomes.
- [ ] Expose attempt/log references and missing-diagnostics status through
  existing orchestrated events/results, including failure and timeout.
  - [x] Qualify exact launch requests, actual-entry evidence and missing/unknown
    diagnostics on the representative engine updater before expanding consumers.
- [ ] Retain completed attempts for 30 days, never prune active/incomplete
  attempts, and warn explicitly rather than blocking installation when logging
  is unavailable.

### Phase 3 - Independent source delivery and exact consumer acceptance

- [ ] Update authoritative install/diagnostic documentation, declarations,
  generated consumers and every affected plugin's changefile.
- [ ] Exercise the focused unit/native validation below and record exact tiers,
  skipped lanes and reasons.
- [ ] Land diagnostics source through current-head review and required CI
  without waiting for the broker replacement.
- [ ] Observe actual promotion and use the supported, separately authorized
  update flow for installed diagnostics acceptance. A payload refresh, selected
  candidate or source merge is not serving convergence.

### Phase 4 - Necessary broker and settlement continuation

- [ ] Independently review the reusable #7833 finite-broker components and
  isolate the actual current native activation failure before implementation.
  Do not import immediate-Job-only lifetime checks or historical-pass claims.
- [ ] If required for the authorized update/monitor acceptance, continue or
  reimplement the shared repair under its separate admission,
  first qualifying the Worktrees status-monitor consumer through its canonical
  installer.
- [ ] Preserve and report the primary spawn/publication error; distinguish
  positively confirmed no-launch from uncertain partial effects and settle
  through the existing owner with fresh routing/predecessor evidence.
- [ ] Require real native lifetime, cleanup, adoption, rollback and interrupted
  publication proof before source completion or rollout. Do not clear a
  breadcrumb, weaken a hold or substitute a contained daemon.

### Phase 5 - Conclusion

- [ ] Resolve every Plan and Validation item, or transfer it explicitly to a
  named tracked objective without silently declaring the parent work complete.
- [ ] Journal actual source/release/installed outcomes, archive the effort and
  finalize its source worktree through the native owner.

## Validation Plan

- [ ] Early admission/refusal and standalone cold paths produce discoverable
  logs without a Manager, service or newly installed Python prerequisite.
- [ ] Missing parent streams, staging and retained publication children retain
  their own diagnostics and exact nonzero exit results.
- [ ] Real stdout/stderr-heavy children and retained-descriptor descendants do
  not deadlock or require an unbounded EOF wait.
- [ ] Split credentials, Unicode and protocol JSON controls preserve redaction,
  useful error text and clean stdout.
- [ ] Concurrent attempts, ordinary-file/private-path checks, storage failure
  and missing announcements expose truthful discovery/degraded status.
- [ ] Timeout, interruption and abrupt-exit evidence survives; retention removes
  only completed attempts older than 30 days, never active/incomplete attempts.
- [ ] Canonical generators, materialization/declaration checks, focused tests,
  lint and install/resource contracts pass without expanding required CI into a
  subprocess-heavy matrix.
- [ ] Exercise supported native Windows and POSIX consumer boundaries, with
  genuine first-touch/external lanes where available and explicit reasons for
  unavailable tiers.
- [ ] Broker continuation, when necessary, proves two resident cycles survive
  caller/outer-Job closure and finite broker exit, rejects shared-outer negative
  controls, retains ambiguous cleanup and exercises actual installer adoption,
  recovery, rollback and removal.
- [ ] Record exact installed generation and serving/cutover evidence separately
  from source fixtures, source merge, release and human terminal acceptance.

## Proposal

Use the existing native installer entrypoints and supervisors to establish
installer-owned attempt records, private capture and sanitized archival
diagnostics. Make live presentation a secondary consumer, not the only
retention path. Extend existing orchestrated results with durable references.
Keep necessary lifetime/publication repair as an independently qualified slice.

## Journal

### 2026-10-25 - Complementary registered-origin timeout capture

- Admitted a bounded consumer repair under the existing diagnostics intent and
  #8370/#8826/#6812 coordination. It does not replace the canonical installer
  attempt producer, shared formatter or native watchdog owners.
- A contained Windows reproduction established that registered-origin acquisition
  settled its owned Job but closed its temporary stdout/stderr captures before
  attaching them to the timeout. Error formatting then discarded the child-stage
  evidence by rendering only the exception's command/timeout representation.
- Retain bounded capture only after confirmed settlement and pass both channels
  through the existing diagnostic redactor. Preserve timeout classification,
  cleanup-failure custody, source revalidation, retry ceilings and deadlines.
- Focused Windows qualification passed 92 tests with four POSIX-only skips,
  including actual native capture, Unicode/byte limits, delayed descendants,
  cleanup-failure precedence and the registered-origin formatter consumer.
  An earlier run detected machine-environment drift and failed its host-state
  audit; no rollback or guard suppression was applied, and guarded revalidation
  passed. The unrelated installer fixture mismatches remain with #8826.
- Source/native qualification, review, merge, release and separately authorized
  rollout remain distinct gates. This slice does not claim to repair an underlying
  native CLI stall or complete a whole-suite update.

### 2026-10-23 - Request, policy and bounded source setup

- Confirmed the slug and the operator's early-location, retention and degraded
  logging policies.
- Created the isolated source workspace and #8370 coordination issue.
- Source investigation identified canonical telemetry, native installer assets
  and existing private-capture contracts; implementation has not started.
- The previous broker owner is explicitly not a dependency. Native lifetime
  correctness and the held candidate's unresolved qualification remain gates.
- Merged #8373 independently supplied the historical Git-evidence preparation
  needed by required CI. The operator approved dropping this proposal's
  redundant CI repair and building on that upstream fix instead.

### 2026-10-24 - Reviewed contract and implementation admission

- Proposal #8376 actually merged as
  `66c8cce25dc4eff489d7b2e6d50191d3d693bc50` after current-head approval,
  required CI and resolution of the description finding. The documented
  Maintainer review-rule exception affected only the additional CODEOWNER vote.
- Pulled the source workspace forward through the native owner. Phase 2 is
  admitted; the logging worker receives exclusive source-edit ownership for
  its bounded implementation milestone. Shared consumer integration remains
  serialized, and the coordinator retains publication and completion.
- The first milestone is reusable native logging plus representative consumer
  regression coverage before expanding generated/standalone consumers.
  No installed runtime, productive task or service has been updated.

### 2026-10-24 - First bounded diagnostics implementation milestone

- Added canonical native PowerShell/Bash helpers and a standalone Python
  retained-child helper, with self-contained Worktrees projections through the
  existing installer synchronizer. No other consumer was expanded.
- Native entry records precede admission, remain explicitly `UNATTRIBUTED`,
  and correlate separate writers. Logging errors warn, preserve the primary
  result and leave a discoverable missing-diagnostics marker when storage still
  permits one. Completed-attempt pruning excludes incomplete writers and
  unsettled private raw transport.
- Wired Worktrees entry/refusal, contextual PowerShell staging and retained
  publication output. Contextual staging retains its original
  `CreateNoWindow` launch and deadline/custody behavior; both channels are
  drained by available-byte polling, with a finite final snapshot rather than
  descendant EOF. Retained Python children receive regular-file destinations.
  Existing updater events expose the discovery root without claiming attribution.
- Native cold controls exercise PS5, PS7 and Git Bash, including unspecified
  or discarded parent streams, real stderr/stdout-heavy children, a descendant
  retaining native pipe handles, exact nonzero results, split credential and
  Unicode text, JSON stdout, private/junction checks, unavailable storage,
  timeout/abrupt exit and completed/incomplete retention. Git Bash is not Linux
  or macOS qualification.
- Final focused validation: **80 passed, 169 deselected** using
  `tools/run-plugin-tests.py agent-worktrees` with the files
  `test_installer_diagnostics.py`, `test_python_runtime_install.py` and
  `test_update_telemetry_wiring.py`, and this exact `-k` selector:
  `installer_diagnostics or driver_governance or governance_recheck or source_deployment or one_owner_retains or failed_passive_exit or publication_failure or reported_commit or uncertain_publication or restart_spawn or routed_predecessor or native_exit or engine_installer_timeout or telemetry_failure_never_changes_result`.
  Canonical generator compatibility controls passed separately; touched Python
  lint, synchronization/declaration checks and the 13-plugin install contract
  also passed.
- Wider adjacent selections did not complete within the unchanged 300-second
  runner budget. An isolated real serving-slot/monitor-drain case produced a
  stack in `native_runtime_source` fixture setup, specifically
  `freeze_inputs` / `fingerprint_source`, before consumer execution. Its measured
  source set contained 1,544 files / 23,817,119 bytes across 15 package roots.
  This is an unresolved qualification condition, not a baseline exemption or
  evidence of a broker failure. No test deadline, containment, construction or
  lifetime policy was weakened.
- Remaining before consumer expansion/source acceptance: validated installation
  association; exact attempt/missing-status propagation into orchestration;
  legacy staging and other native diagnostic producers; interrupted raw-capture
  reconciliation through existing ownership; real POSIX and complete contextual
  installer/serving qualification. The parent effort remains Active and all
  broader Plan/Validation gates remain open.
- Ownership reconciliation remains independent: historical workspace
  finalization is not proof an owner/controller is unavailable. #8409 is a
  separate native admission repair and was not copied or superseded here.
  No broker, CI repair, installed update or service/task registration was made.

### 2026-10-24 - Bounded staged-review corrections

- Corrected the credential-folding class identified in staged review:
  `Authorization:` and `http.extraheader=` records consume their continued
  header/scheme/value before destructive prefix replacement. The Python archive
  adapter retains incomplete header/scheme sequences together. Native
  PowerShell, Bash and Python file-byte controls construct 30 LF/CRLF variants
  each, positively verify the synthetic credential reached the input, and
  verify it is absent from sanitized archives while useful Unicode error text
  remains.
- Restored contextual child stdout/stderr presentation independently of
  archival storage. The existing native pipe owner forwards through the
  admitted producer writers with separate incremental decoders. Kernel-byte
  availability snapshots use unbuffered streams matching each handle's async
  mode, avoiding an additional managed-prefetch boundary. Storage failures do
  not stop presentation or draining; warnings wait until the final stream
  boundary so they cannot split a presented character.
- Added actual-process controls for capture success, sanitized-archive failure,
  raw-transport failure and unavailable storage on PS5/PS7. An acknowledgement
  gate proves progress arrives before the child exits; controls verify exact
  channels, split Unicode, exit 37 and retained-descriptor completion.
  Noninteractive fixture children receive explicit stdin EOF, native hosts use
  their own built-in module paths, and fixture capture drains both channels
  without joining descendant EOF. Production stdin, `CreateNoWindow`,
  construction/cutover custody and native outcomes were not redefined.
- Validation: the isolated continuation controls passed **4 tests**; the
  combined review/streaming/retained-descriptor selector passed **16 tests,
  24 deselected**; the final complete diagnostics file passed **40 tests** via
  `tools/run-plugin-tests.py agent-worktrees --test-file tests/test_installer_diagnostics.py`.
  Fixture process budgets remain 20 seconds overall and 10 seconds for the
  child operation; pytest item budgets remain unchanged. Earlier focused
  failures were diagnosed and corrected without extending those budgets.
- Touched Python lint, canonical synchronization/declarations and the
  13-plugin install contract passed. The previously recorded 300-second
  real-construction fixture condition remains unresolved and was not waived
  or broadened into this correction slice.
- No additional consumer, validated-association path, orchestration
  attempt/status protocol, legacy staging path or interrupted-raw reconciliation
  was admitted. The effort remains Active pending coordinator acceptance and
  those existing gates.

### 2026-10-24 - Cold PS5 readiness repair and independent acceptance

- Independent verification initially found one failure in the complete
  diagnostics file: the PS5 staged child did not settle within its unchanged
  10-second operation budget. The other 39 controls passed; this was not
  accepted as a flake or waived.
- Bounded phase/byte evidence retained all 400,061 bytes and localized
  avoidable cold readiness cost, including an unqualified JSON-command
  interval. Native path composition now uses filesystem APIs, and existing
  JSON cmdlets are explicitly qualified. Privacy/parser semantics, retention,
  live presentation and native results are unchanged; no prewarming or
  deadline increase was introduced.
- Fresh worker evidence measured diagnostics ready at 847 ms and child
  settlement at 3,651 ms with native exit 29 and no buffered-reader fallback.
  Coordinator verification subsequently passed the complete 40-test file in
  95.30 seconds, 103.16 seconds including preparation and cleanup.
- A separate exact serving-slot case still exhausted the default 300-second
  group cap. Its cleanup reported a denied temporary build executable; the
  runner did not return an actual installer result. This does not establish
  an installer deadlock or qualify the serving boundary.
- Source-input diagnostics completed the real 15-package freeze in 65.103
  seconds, with all three hashes retained. Cold copied-input reading was
  materially slower than reading the original inputs. This is bounded phase
  evidence, not a substitute for the actual consumer check or a reason to
  weaken immutable-input verification.
- The operator authorized one exact native integration invocation with a
  900-second group cap matching its existing item marker. Its construction
  subprocess remains bounded at 300 seconds, and all native assertions and
  shorter operation deadlines remain unchanged. Its outcome is still pending.
- The native diagnostics foundation is accepted as a bounded local milestone,
  not complete installer coverage, publication, installed association or
  serving/cutover acceptance.

### 2026-10-24 - Independent PS5 qualification failure and cold-start repair

- Coordinator verification rejected the preceding milestone: the exact complete
  diagnostics command returned **1 failed / 39 passed**, with the PS5 staged
  child exceeding its unchanged 10-second wait and returning fixture failure
  instead of native exit 29. The earlier passing run was not acceptance.
- Added bounded, content-free child phases, native exit/stream evidence,
  archived-byte counts and opt-in read/store/presentation timing. A cold profile
  delivered all **400,061 bytes** with native exit 29 but spent **4,880 ms**
  before diagnostics became ready and **8,213 ms** in the wait. The receipt
  JSON-command interval alone occupied **3,776 ms**; measured read, store and
  presentation costs were approximately **42 / 456 / 752 ms**. This localized
  the avoidable pre-output cost rather than treating the failure as a flake,
  discarding output or assuming an I/O deadlock.
- Removed PowerShell provider dispatch from logging's filesystem joins and
  qualified its existing native JSON cmdlets explicitly. This retains the same
  JSON parser/serializer, identity checks, ordinary/private-path validation,
  retention, result and lifetime behavior while avoiding broad cold command
  discovery. No module was prewarmed and no budget was enlarged.
- The staged-child regression now prohibits provider joins and unqualified
  JSON commands, verifies two completed correlated writers, and retains bounded
  phase/timing/byte evidence. After repair, a fresh PS5 child reached diagnostics
  readiness in **847 ms**, completed the wait in **3,651 ms**, retained all
  **400,061 bytes**, used no buffered-reader fallback and returned native
  exit **29**.
- Qualification: the focused PS5 selector passed **1 test, 39 deselected**.
  The coordinator's exact complete-file command then passed **40 tests in
  104.01 seconds**, **111.08 seconds including preparation/cleanup**.
  The 10-second child, 20-second fixture, pytest-item and runner limits remain
  unchanged; presentation, archival and raw privacy were not relaxed.
- The separate real-serving consumer/300-second acceptance gate remains open.
  No hashing, frozen-input invariants, broker, installed state or additional
  consumer was changed in this repair.

### 2026-10-24 - Bounded Python-owned producer coverage

- Built on the accepted local diagnostics foundation. The separately authorized
  single 900-second diagnostic also exhausted its outer cap. Its residual log
  contained only a missing-diagnostics warning and an initial admission line;
  no monitor log existed. Subsequent partial framework cleanup means missing
  candidate files are not reliable phase evidence. No construction stage,
  monitor/publication result or broker cause is inferred from those artifacts.
  The coordinator settled and removed the retained test roots through their
  existing framework owner; this worker did not inspect or mutate them.
- Added standalone Python attempt initialization before implementation imports
  and argument parsing. When no attempt is inherited, Python creates a private
  `UNATTRIBUTED` record; inherited native attempts must retain exact plugin/schema/ID
  correlation and pass private filesystem admission. Logging failure warns,
  records missing diagnostics where possible and never supplies default success.
- Added a diagnostic-only stdlib filesystem companion using TokenUser ownership
  and a protected user-only DACL on Windows, and current-user 0700/0600 checks
  on POSIX. Ordinary paths, file identity, single-link files and read privacy are
  checked; foreign/public/linked state is not repaired or adopted. Negative
  controls explicitly reject public READ access even without public WRITE.
  No lease/installation receipt is reused as diagnostic write authority.
- Added in-process text/binary producer tees with bounded logical-record
  sanitization. Native descriptors, encoding, flush and protocol output retain
  their original owner; archive descriptors are non-inheritable and never
  substituted for subprocess stdio. Available root/native error evidence is
  recorded without formatting subprocess commands, environment or authority
  arguments. Root exception identity/exit behavior is preserved. Abrupt exits
  retain sanitized progress without a false completion record; unsettled raw
  transport remains pending/private.
- Worktrees source, staged and retained/frozen entrypaths carry the complete
  packaged companion/redactor closure through the existing synchronization and
  lifecycle-resource lists. The Python entry re-enters its own source in-process
  under the diagnostic boundary; no child supervisor, daemon, newly installed
  Python prerequisite or native pre-parser record was invented.
- Focused validation initially caught an incorrect expected argparse message
  and a fixture child without declared UTF-8 output; the real parser result and
  native child's encoding contract were corrected, not its exit/error behavior.
  The complete final selectors
  `tools/run-plugin-tests.py agent-worktrees --test-file tests/test_installer_diagnostics.py --test-file tests/test_installer_python_producer.py`
  passed **65 tests**: the accepted **40-test** diagnostics file plus **25**
  real Python producer controls, **102.95 seconds / 114.40 seconds including
  preparation and cleanup**. The focused frozen-resource preparation control
  passed **1 test, 53 deselected**. No default budget was increased or prewarmed.
- These are source/native synthetic-root controls, not a construction, network,
  deployment or serving qualification. Actual Linux/macOS qualification is not
  claimed from this Windows run. Installed association, exact parent attempt/
  status protocol, remaining legacy/all-consumer coverage, interrupted raw
  reconciliation and the real consumer acceptance gate remain open. No
  additional 900-second invocation, Job/broker/lifetime change or rollout was
  performed.

### 2026-10-24 - Python boundary review and independent acceptance

- Coordinator review added real producer and fixed-file controls for credential
  headers whose whitespace prefix exceeds the 65,536-character record bound.
  Both Python paths discarded that prefix and exposed its next-line value;
  LF/CRLF and `Authorization`/`http.extraheader` variants reproduced the issue.
  The bounded parser now retains credential context after omission, and fixed
  snapshots reuse that same parser instead of maintaining a second copy.
- Added native stdout/stderr controls that reconfigure an original stream from
  a legacy encoding to UTF-8 before binary writes. Presentation bytes were
  correct, but the archive decoder retained the old encoding. Binary capture
  now observes the real text stream's current encoding without replacing its
  reconfiguration API, descriptors or native transport.
- The ten new controls failed before repair and passed afterward. Large
  fixture data is constructed inside the child rather than embedded in a
  Windows command line. Coordinator verification of both complete files
  subsequently passed **75 tests in 107.88 seconds**, **113.34 seconds including
  preparation and cleanup**, under the normal unchanged runner budget.
- Staged Python lint, canonical generation and whitespace checks passed.
  This accepts the bounded Python producer milestone, not installed-runtime
  association, exact parent references, all producers or real serving results.
- Interrupted/raw reconciliation must also cover an incomplete logical record
  flushed immediately before abrupt termination; the existing complete-line
  abrupt-exit control is not proof of that remaining boundary.

### 2026-10-24 - Exact representative launch discovery

- Replaced the engine updater's shared-root hint with a unique, private,
  unattributed launch reference. The caller records `launch-request.json`
  before spawn; it does not fabricate installer entry. Native PowerShell and
  Python writers join the reserved attempt, and bounded, privately readable
  writer records/logs establish actual entry separately.
- `InstallerRun` carries the exact reference and explicit missing/unknown
  diagnostics. The existing update event stream records that same structured
  evidence. A private `launch-result.json` preserves the caller outcome and
  native exit even on spawn failure, timeout or interruption. It never marks
  inherited writers or the attempt complete.
- The runtime uses its retained bootstrap helper, or its own packaged payload
  helper in a source launch, following the existing loader pattern. The
  canonical diagnostic filesystem implementation owns private paths and files;
  no receipt, environment or invocation dump is added. Child correlation is a
  copied environment; parent routing and native stdout remain unchanged.
- The new launch controls passed **16 tests** together with the existing
  **40 diagnostic controls**, **56 passed in 99.47 seconds / 103.57 seconds
  including preparation and cleanup**. They exercise real Python and both
  available Windows PowerShell hosts, parser-before-entry refusal, spawn
  failure, timeout, lost presentation, interrupted execution, privacy refusal,
  malformed writer evidence and durable replay from the existing update events.
- Native execution/receipt/updater compatibility selectors separately passed
  **194 tests, 6 platform skips in 225.12 seconds / 229.96 seconds including
  preparation and cleanup** under unchanged normal budgets. This is not
  installed-runtime association, real POSIX or full serving proof. The one
  previously authorized 900-second diagnostic was not repeated.
- The integration remains deliberately limited to the representative engine
  updater. Other installer consumers, validated installed association, raw
  reconciliation and real consumer/deployment acceptance remain open.

### 2026-10-24 - Retained-publication association boundary

- Added minimal, historical `installation.json` evidence only after the existing
  receiver publishes and verifies its deployment manifest. The receiver retains
  its original governance and candidate/rollback custody, rechecks ownership,
  compares the explicit selection, installed distribution/generation and
  completion-marker fingerprint, then validates the actual private diagnostic
  writer/log path. Checkout trust and a merely healthy candidate do not qualify.
- Original attempt records remain `UNATTRIBUTED`. Exact launch results can
  report `VALIDATED_INSTALLATION_RECORDED` with the minimal publication
  association; this is historical discovery data, not current serving evidence,
  a deployment receipt or permission to mutate the installation. No full
  receipt, environment, source provenance or authority witness is copied.
- Missing or changed ownership, selection, manifest, installed identity,
  completion marker or log privacy produces explicit missing diagnostics and
  does not fail installation or enter rollback. A committed publication observed
  while preserving a native failure can still record its association; rollback
  cannot.
- Focused association/refusal, real-lock custody placement and exact-launch
  controls passed **27 tests, 50 deselected in 22.61 seconds / 27.30 seconds
  including preparation and cleanup**. The custody control proves the hook
  executes while the existing governance scope and both retained generations
  remain held, including committed-error settlement, and never on rollback.
- This accepts source boundary controls, not actual installed construction or
  serving proof. Real native consumer acceptance, raw/interrupted reconciliation,
  safe completed-attempt retirement, all-consumer coverage and rollout remain
  open. No installer deadline, process owner or authority gate was changed.

### 2026-10-24 - Flushed incomplete producer records and retirement boundary

- Re-read the accepted launch-reference and retained-publication association
  boundaries before editing. Reproduced the new gap with a real isolated
  Python child: it flushed an unterminated record and exited 37; the earlier
  complete line survived, but no private pending state contained the flushed
  fragment. `flush()` forwarded only to native stdio while logical/decoder
  state remained in interpreter memory. This was a failing control before repair.
- Explicit text/binary flush now appends and fsyncs a private, non-inheritable
  producer checkpoint containing bounded logical state, credential context,
  oversize omission state and incomplete encoded units. The checkpoint is raw
  transient evidence, not a sanitized archive, installation receipt or
  completion/quiescence claim. Native descriptors, stdout protocol, live output
  and primary exit/exception behavior remain unchanged. Storage failure reports
  missing diagnostics and preserves the native operation.
- Flush also retains an independently meaningful partial preview in the
  sanitized writer log. Incomplete previews conservatively omit the final
  token, partial credential shapes and incomplete URL authorities; split
  headers and oversized credential context retain the same parser safeguards.
  Exact incomplete state remains only in access-restricted raw storage.
- Added non-destructive checkpoint recovery anchored to the exact private
  attempt/writer. It reads a bounded fixed-byte tail, never descendant EOF,
  validates complete checkpoint identity/state, and writes a separate private
  sanitized recovery log. A later torn append preserves the preceding complete
  checkpoint with explicit truncation status; malformed/unknown complete
  records refuse recovery without deleting raw evidence. Recovery always
  reports quiescence as not asserted and never fabricates writer/attempt
  completion or raw retirement. Pending journals remain protected from pruning,
  including after normal writer completion, until reconciliation is authorized.
- Retirement subpiece is explicitly blocked, not silently completed. The
  representative engine updater requests ordinary execution and the accepted
  launch-result handoff carries no positive tree-quiescence proof. The optional
  strict Windows native owner verifies empty-Job teardown, but no existing
  root-safe handoff binds that result to all diagnostic writer/raw evidence.
  Start records and pending files are correlation/data, not such authority.
  This slice does not enable stricter teardown for logging, duplicate a process
  owner, enumerate processes or infer quiescence from root exit/metadata.
  A genuine strict-owner timeout control still retains raw pending data and
  unfinished writers after bounded recovery. The owner handoff/reconciliation
  needed for completed caller-attempt retirement remains open.
- Validation under unchanged default budgets: **11 new focused controls passed,
  51 deselected**; then all affected files
  `test_installer_diagnostics.py`, `test_installer_python_producer.py` and
  `test_installer_launch_diagnostics.py` passed **104 tests in 130.86 seconds /
  135.04 seconds including preparation and cleanup**. Controls cover abrupt and
  owner-terminated producers, partial header/extraheader/short-token/URL
  credentials, oversized context, split Unicode, exact JSON stdout, torn and
  unknown records, active-writer recovery, raw privacy and storage failure.
- Final targeted generator controls passed **3 tests, 68 deselected**.
  Ruff F/E9, canonical installer-engine synchronization, install-contract
  validation (13 plugins), and staged/unstaged whitespace checks passed.
- Accepted launch discovery and installation association remain unchanged.
  No real install/serving or repeated 900-second test, POSIX qualification,
  consumer expansion, broker/settlement, rollout or objective-complete claim is
  made. The coordinator retains those gates and the separate claim-side work.

### 2026-10-24 - Independent review and remaining native construction boundary

- Coordinator review of the actual returned checkpoint implementation and
  independent partial/interrupted selectors passed **14 tests, 50 deselected
  in 8.15 seconds / 11.17 seconds including preparation and cleanup**.
- Genuine Linux qualification was attempted through the existing contained
  runner. Missing UV was restored only after the runner's explicit failure,
  using an official digest-verified release older than seven days in disposable
  private scratch, without changing user/system installation or PATH defaults.
  The first setup failed with a filesystem I/O error while building dependencies
  on the Windows-mounted cache. A retry using the same runner and a private
  Linux-native venv/cache completed dependency installation, but exhausted the
  aggregate budget before producing test results. Its reported total exceeded
  that budget substantially; the cause is unresolved. Neither attempt qualifies
  POSIX behavior or excuses a source failure. Disposable tool/cache scratch was
  cleaned after each invocation.
- The separately authorized second 900-second native invocation returned a
  complete failure report: its unchanged inner construction subprocess timed
  out at **300 seconds**; the test failed in **661.72 seconds / 685.85 seconds
  including preparation and cleanup**. Only `admitting frozen inputs` was
  printed, without the earlier missing-diagnostics warning. It did not reach
  monitor cutover and proves neither a broker regression nor installed success.
  Both single-use 900-second authorizations are now consumed.
- That precise remaining observability gap led to an optional fixed-label
  progress callback in the existing engine: source admission/fingerprinting, lease,
  retained UV inputs, construction, installed preparation/verification and
  retention. Only the representative Worktrees owner opts in; other generated
  consumers retain a default `None` callback and unchanged output.
- Early source/producer controls passed **7 tests, 162 deselected**. A full
  changed-producer selection then caught an announcement regression in exact
  stderr bytes. Announcements are now explicitly requested by the CLI entry,
  not every generic writer. The unchanged encoding/protocol controls plus
  entry/phase regressions passed **6 tests, 43 deselected in 11.30 seconds /
  39.40 seconds including preparation and cleanup**; the preceding selection
  had **68 passing controls and that one now-repaired failure**.
- Native qualification is still open, not waived: one cold PowerShell control
  timed out, then both original cold controls passed on the bounded
  reproduction. A real small-package UV construction subsequently hit its
  existing item watchdog while the retained guardian awaited captured output;
  sandbox cleanup encountered a locked candidate interpreter. The existing
  runner verifies its Job empty before cleanup, but exact-file owner inspection
  did not identify the remaining handle. The sandbox remains explicitly
  retained pending safe cleanup; no unrelated process was killed. The
  unresolved UV/guardian output and native qualification boundaries must be
  diagnosed before publication, not dismissed as pre-existing or made green
  by longer deadlines.

### 2026-10-24 - Durable retained Windows child capture

- Inspection confirmed that the existing Job API exposes termination plus
  positive empty-Job verification, not a non-mutating empty-wait operation.
  Replacing the child's pipes with files and waiting only for its root would
  kill descendants earlier; waiting for every Job member would change the
  completion/timeout contract when a descendant closes its pipes. The operator
  explicitly selected **preserve current completion behavior**, using
  concurrent streaming capture inside the existing deadline and guardian.
- The representative Worktrees construction owner now opts in explicitly.
  The flag survives retained-input freezing and supplies the known frozen
  plugin's packaged helper path to its existing Windows guardian. Other
  generated consumers default off. There is no ambient helper lookup, second
  process owner, new installation authority or changed deadline.
- Both pipe readers preserve native UTF-8 replacement/universal-newline output
  and copy bytes into private file capture while the child runs. Storage
  failure warns without stopping native draining. The guardian's protocol JSON
  is never captured as child output. On caller death the existing strict Job
  settles before the bounded final drain and sanitized snapshot. Raw/pending
  retirement remains deliberately unimplemented.
- The initial new native selection exposed two fixture mistakes: a plain
  `password=` value was not one of the canonical redactor's supported shapes,
  and implicit Windows stdio inheritance did not create the intended held-pipe
  descendant. Corrected fixtures use a supported synthetic credential with a
  positive raw-byte assertion and explicit handle inheritance. All **7**
  outcome/flood/redaction/lifetime controls then passed.
- A write-failure/partial-write control, both flag/path wiring controls and
  the two real `pythonw` cancellation/timeout cycles passed **5 tests**. The
  existing desktop observer verified no surfaced windows or focus changes.
  A subsequent complete guardian/progress selection passed **27 tests,
  1 platform skip, 57 deselected in 125.22 seconds / 192.71 seconds including
  preparation and cleanup**. It covers original default consumers, long owner
  interpreter paths, exact native outcomes, inherited-versus-closed descendant
  pipes, warning/missing-marker propagation and live private bytes before
  caller cancellation.
- Real offline UV construction now has default and capture-enabled variants,
  preserving its original item watchdog and all freeze/origin assertions. The
  first invocation caught and fixed a missing internal forwarding parameter.
  Both variants subsequently passed their assertions in **56.22 seconds**,
  but that runner invocation still **failed** when sandbox cleanup could not
  delete a candidate interpreter. This is not a green qualification result and
  does not explain the earlier item timeout.
- Read-only inspection found ordinary writable interpreter files with inherited
  full-control access and no live process reference to the exact sandbox.
  Later exclusive read, shared read, delete-access-only and write-access-only
  opens all succeeded without modifying the file. After this evidence and the
  runner's prior positive Job settlement, its own sandbox cleanup function
  successfully retired both explicitly retained sandboxes. The cause and
  immediate-cleanup reliability remain unresolved; no unrelated process was
  killed, permission changed or cleanup failure waived.
- Direct review also found that closing a raw capture file could replace the
  native result if close itself raised. Both setup-failure and final-close
  paths now warn and retain missing-diagnostics status instead. Successful and
  exceptional native outcomes plus adjacent file-capture controls passed
  **5 tests in 1.37 seconds / 11.73 seconds including preparation and cleanup**.
  Targeted Ruff, generated-engine closure and the **13-plugin install
  contract** passed. Shared-library release declarations include every
  registered consumer; no versions were hand-edited.
- No further 900-second integration invocation is authorized. POSIX/full
  consumer qualification, immediate runner cleanup, broader consumers,
  interrupted raw reconciliation, broker/settlement work and installed rollout
  remain open. This is unpublished source progress, not effort completion.

### 2026-10-24 - Admission of bounded Windows sandbox cleanup recovery

- Following the real-UV cleanup failure and subsequent successful exclusive
  access/owner cleanup, the operator explicitly authorized a runner repair:
  retry Windows access/sharing cleanup errors for **at most 5 seconds**, only
  after the existing settlement gate. The authorization does not permit
  unrelated process termination, permission repair, another 900-second
  consumer invocation or any installer/item/group/aggregate budget increase.
- The repair is confined to the existing `TestSandbox` exit boundary.
  `UnreapedProcessError` still retains the directory and bypasses cleanup.
  Only Windows errors 5, 32 and 33 enter a monotonic, warning-visible retry;
  other errors and expiry retain the original failure behavior, including
  preserving a pending native interruption. The existing cleanup operation is
  reused without adding process discovery, permission changes or forced
  deletion.
- The first actual rerun exposed a missed consumer: the asynchronous group
  cleaner called `TemporaryDirectory._rmtree` directly before the outer
  sandbox exited. Both boundaries now reuse one bounded retry operation after
  their existing reaping gate. Expiry keeps the original error as the cause
  of `SandboxCleanupError`; the outer owner retains its sandbox without
  deleting again or allocating a second retry budget.
- Recovery, Windows error allowlisting, expiry, native interruption,
  unreaped/expired-child retention, original aggregate-budget checks and
  asynchronous group cleanup passed **36 tests, 155 deselected in
  13.76 seconds**; targeted Ruff passed. A real exact-file open/close test
  exercised the Windows sharing failure and recovered after only its own
  handle closed. No external process or security setting was changed.
- The actual UV case remains a separate hard failure. A default-path rerun
  again hit its unchanged **30-second** item watchdog during UV installation.
  A capture-only invocation then hit the same watchdog; its group cleanup
  exhausted exactly the permitted retry and explicitly retained the outer
  sandbox rather than retrying indefinitely or reporting success. The
  intended cleanup guard behavior is demonstrated, but the real UV case is
  **not qualified** and the stall is not explained. Partial disposal had
  already removed its test-local diagnostic files before encountering the
  held interpreter; retained fixture files are not a complete log archive.

### 2026-10-25 - Identified external Windows image-cache owner

- The selected Windows UV reported version **0.13.5**, built October 21.
  The operator authorized a disposable, officially verified release at least
  seven days old for command-scoped diagnosis, without changing the installed
  tool, native deadlines or integration permissions. Official **0.13.1**
  release metadata was non-draft/non-prerelease and published October 14.
  Its ARM64 Windows archive matched the release asset's SHA-256 digest before
  safe extraction into private timestamped scratch.
- With this command-scoped tool, the capture-enabled real UV item passed its
  unchanged assertions in **15.43 seconds**. The runner nevertheless failed
  because the interpreter still could not be deleted within the allowed
  cleanup retry. This is not a green invocation or proof that another UV
  version caused the earlier item timeout.
- A read-only Restart Manager query on that exact candidate interpreter
  identified **`XtaCache`**, the Windows translation-cache service. The service
  and process records agreed on its live identity. Shared read/delete-access
  opens succeeded, while exclusive read/write opens returned sharing
  violations. The cached test interpreter was **x86-64 Python 3.14** on a
  Windows ARM control; its candidate interpreter therefore exercises Windows
  emulation. This explains an external owner of the deletion lock, not a
  leaked installer child or permission to kill a system service. Its role in
  the separate item watchdog remains unproven.
- The runner reuses environments keyed by platform and dependency fingerprint,
  not interpreter architecture, and a fresh environment delegates Python
  selection to UV. Native Python selection can therefore differ from a cached
  interpreter. Any native-architecture diagnostic must use a separate owned
  environment and retain the emulated-lane gap explicitly; no cached test
  environment, system service or machine Python selection has been changed.
- The operator authorized that separate native control using the existing
  ARM64 Python 3.13 and the verified private UV. Only the runner's disposable
  environment root and command-scoped Python/PATH inputs differ; source,
  containment, item/group/aggregate deadlines and the existing cached x64
  environment remain unchanged. This control cannot waive the emulated-lane
  failure or establish an installed production rollout.
- The first native invocation failed its unchanged aggregate budget:
  preparation consumed most of the 900 seconds, leaving a **37.644-second**
  group allowance; total including cleanup was **900.35 seconds**, with no
  test verdict. The prepared interpreter's actual PE machine is ARM64 and its
  dependency fingerprint exists. Reuse of this already-created environment
  is a warm component diagnostic only, not a successful first-touch runner or
  clean-room result.
- The warm native component attempt also failed its **30-second** item
  watchdog, in **86.61 seconds including preparation and cleanup**. This
  time UV had returned and the stack was inside the required post-build
  `fingerprint_source` / `Path.read_bytes` guard. It is therefore not evidence
  of a UV pipe deadlock in that invocation. Native ARM execution removes
  neither the observed source-I/O timing problem nor the qualification gate.
  No source hash, assertion or deadline was removed, and no further local
  timing retry is justified without a distinct new intervention.
- After all owned runs ended and exclusive probes succeeded for the remaining
  executable/library files, canonical cleanup removed the three retained
  sandboxes and the entire private UV/native-environment task directory.
  Installed UV/Python, the original cached x64 environment and `XtaCache`
  were untouched. No diagnostic scratch remains from this comparison.
- Existing hosted routing is not a substitute result: ordinary CI is
  unit-only; the periodic Windows portfolio is explicitly unenrolled, and
  the current manual native workflows target derived copies or the shared
  foundation, not this Worktrees consumer selection. Any new focused native
  acceptance lane and pushed validation candidate require their normal
  review/publication flow. Windows/POSIX consumer qualification remains open.

### 2026-10-25 - Focused manual native acceptance candidate

- The operator authorized a review-gated, opt-in diagnostics mode in the
  existing manual acceptance workflow and a draft validation candidate.
  This does not authorize merging or rollout, enrollment of the disabled
  Windows portfolio, another large installer/monitor invocation, or longer
  native test deadlines.
- `installer_diagnostics=true` selects only the Worktrees diagnostic helper,
  standalone Python producer and retained guardian files, partitioned one file
  per bounded sub-suite, followed by the real-UV construction item with capture
  disabled and enabled. The existing item, group and aggregate runner limits
  remain unchanged. Desktop observation is not opted in. Linux executes through
  the canonical hardened, network-disconnected container wrapper; hosted
  Windows uses the existing native contained runner. Both select the exact
  dispatch SHA and do not persist checkout credentials.
- The Linux CLI preparation was extracted from the periodic portfolio into
  one shared helper with the same locked npm metadata seasoning, disabled
  lifecycle scripts, manifest immutability and exact installed-version checks.
  Neither the periodic portfolio's selection nor its literal disabled Windows
  condition changes. Required PR CI and promotion remain unit-only.
- The first focused workflow/helper/periodic guard invocation found four
  identical stale expectations that required the unit workflow to call the
  runner directly. The current workflow already invokes its canonical
  `ci_pr_unit_reuse.py` adapter. The guard now verifies that exact source-SHA/
  output-directory invocation instead of its retired spelling.
- Focused routing, locked setup/failure propagation and unchanged periodic
  policy controls passed **88 tests, 4 platform skips in 18.11 seconds**.
  Targeted Ruff and whitespace checks passed. These are local workflow
  contracts, not native consumer qualification. The four skipped Bash reporter
  cases require Linux; the new lane has not run. The earlier UV, Windows
  emulation, source-I/O, Linux preparation and large-consumer failures remain
  open and are not waived by a draft or by a selected workflow.
- Documentation-impact review: `TESTING.md` owns the focused scope and explicit
  exclusions; the workflow inventory in `docs/pipelines.md` points to it.
  This is a below-altitude acceptance-routing change for the already reviewed
  diagnostics contract, not a new runtime authority or an expanded vision.
- Initial managed draft publication stopped before any push because `dev`
  had advanced with additional manual acceptance selectors and candidate
  local-package/closed-wheel profiles. The original unpublished history was
  preserved on a local backup, collapsed for one coherent rebase and reconciled
  without discarding the new profiles, source guards or workflow selectors.
  Diagnostic opt-in now survives both ordinary source and closed-wheel retained
  inputs; the latter has explicit profile/field propagation regression controls.
  Canonical generated consumers were refreshed through the existing sync owner.
- After that integration, workflow/setup/periodic/sync contracts passed
  **157 tests, 22 platform/prerequisite skips in 105.49 seconds**.
  The bounded plugin runner separately passed the seven new/existing
  profile-propagation, diagnostic opt-in and phase-failure controls
  (**1.47 seconds; 155.68 seconds including environment preparation and cleanup**).
  This selection did not repeat the failing real-UV construction item.
  The 13-plugin install contract and canonical sync check passed.
- One independent read-only code review of the new acceptance routing,
  shared CLI setup and merged profile/capture seam at `c676985bba` reported
  no significant findings. It did not re-review or qualify every prior
  diagnostics surface. The selected source was clean and unchanged during
  review. Native hosted execution and all remaining effort gates are still open.
- Managed publication opened draft #8826 at `57f7bd761f96905b710dc96afc59bcf485fece76`.
  Its implementation scope matched the reviewed code; only the journal changed.
  Required CI then failed the source-module cap: upstream growth plus the
  bounded cleanup addition put `plugin_test_containment.py` over the limit.
  No native acceptance was dispatched and no required check was bypassed.
- The already-authorized retry operation, cleanup error and reaped-directory
  removal were mechanically extracted into `tools/plugin_test_cleanup.py`.
  The containment owner still decides reaping/retention and preserves its
  public cleanup/error imports; package and directly executed script imports
  use the same owning helper. Clock-injection tests now patch the helper's own
  clock rather than its former module. Retry limits, warning, allowlist,
  original causes and interruption behavior are unchanged. Focused native
  sharing recovery, group cleanup, retained/unreaped, interruption and
  aggregate-limit controls passed **48 tests, 143 deselected in 2.20 seconds**;
  targeted Ruff passed. Source size is checked by the normal guard, not a raised
  baseline.
- The normal GitHub review request was accepted while the PR remained draft,
  but no review was delivered or remained requested; draft automation skips
  review. The bounded independent code review above is recorded separately,
  not represented as a passing GitHub verdict. The draft and no-merge/no-rollout
  hold remain in force.
- The repaired draft's required CI passed at `b77e38d70a` (guard/lint, eligible
  units and the required PR gate). The explicitly requested GitHub review
  eventually delivered a `Comment` on the prior candidate, despite the
  automatic draft-review skip. It identified three valid blockers: synchronous
  live forwarding can prevent deadline enforcement under an open unread parent
  pipe; Bash rejects Python's whitespace-bearing reservation header; and a
  later lease-release EXIT trap replaces native diagnostic completion.
  Required CI success does not waive those findings or qualify the native lane.
- Bash reservation now matches JSON field whitespace using a finite, private
  file read and Bash's own regex, without a bootstrap Python dependency.
  One early exit function saves the primary exit code, releases an existing
  lease when that function is available, completes the native writer and
  explicitly preserves the primary result even if cleanup fails. The later
  lease-only trap registration was removed.
- The new native Bash controls execute the actual exit function and every
  source EXIT registration, with success/failure primary outcomes and both
  successful/failing lease release. They passed **4 tests, 3 native-POSIX
  prerequisite skips in 21.93 seconds; 33.43 seconds including preparation and
  cleanup** on Windows/Git Bash. The three skipped reservation-parser cases
  cover the canonical Python header, multiline JSON whitespace and a wrong
  plugin, and still require native POSIX execution. The manual optional lane
  now includes this file; no existing deadline or portfolio gate changes.
  The PowerShell backpressure repair remains under native API investigation.
- Lease-cleanup failure is now explicitly warned and archived when a native
  writer is available, rather than silently suppressing that secondary result.
  The four native trap controls also verify that warning/evidence while keeping
  the original success/failure exit code; they passed in **28.18 seconds;
  33.02 seconds including preparation and cleanup**.
- The first PowerShell repair draft is not qualified. An isolated native API
  proof established anonymous-pipe mode query/set/restore, shared alias state,
  and zero-progress writes without a deadline extension. Actual repeated
  scoped presentation then failed: the new eight-case selection failed, and
  two narrow diagnostic isolations identified Win32 231 after the first
  1,024 forwarded bytes. Capture, primary results and restored modes survived,
  but loss on a normally reading channel is unacceptable. No synchronous
  fallback, deadline increase, native acceptance dispatch or rollout was used.
  Operation-tagged native diagnosis must distinguish normal write backpressure
  from a mode/query/restoration failure before further implementation.
- The tagged proof located 231 specifically at setting NOWAIT after queued
  data; the mode remained positively unchanged, and the same handle became
  writable after the reader drained. The next draft defers only that exact
  unchanged-mode case and performs a bounded batch per mode transition.
  Query, write and restoration failures still degrade presentation explicitly.
- Combined native validation then passed **27 tests, failed 1, deselected 26
  in 96.93 seconds; 100.39 seconds including preparation and cleanup**.
  Existing live acknowledgement, exact output, storage-failure,
  retained-descendant, CP1252 and native-failure controls passed. The remaining
  PS7 stress failure saturated the 256KiB stdout queue even while the parent
  was reading that channel: 32,768 of 300,000 live bytes were forwarded.
  There were no native API failures; exit 0, complete private capture and
  mode restoration survived. This is not qualification or permission to
  weaken the actively-reading-channel assertion. Further implementation is
  paused for an explicit bounded-backlog policy choice, rather than larger
  queues, longer deadlines or another timing retry.
- The operator selected **replay from the existing private raw capture within
  the original deadline, keeping memory bounded**, rather than accepting
  avoidable loss on an actively reading channel. This does not guarantee
  delivery to indefinitely slow readers or authorize another raw spool,
  a larger memory queue, a longer deadline, source completion, merge or rollout.
  Replay must preserve channel ordering and the existing storage-failure
  presentation behavior, and must not turn a fixed capture snapshot into a
  descendant-held EOF wait.
- The first replay implementation exhausted its unchanged 300-second group
  limit (301.23 seconds total), after progress showed 11 passes and 5 failures.
  No complete failure summary survived that limit, so it was not treated as
  qualification. A single-case diagnosis subsequently observed an actual
  10-second child timeout with 278,528 stdout bytes captured and no stderr yet;
  the replay implementation was doing an avoidable disk readback of every
  just-captured buffer.
- The current buffer now enters the bounded queue directly only when it is the
  next recorded range and fits; earlier backlog still replays from the existing
  raw file. Native counters distinguish those paths. The PS7 reading-stdout/
  blocked-stderr control then retained 300,000 bytes on each raw channel and
  all 300,000 live stdout bytes, with zero native failures, restored modes and
  7,748ms wait time. Five PS7 buffer/order/cutoff/append-failure/remaining-budget/
  retained-snapshot controls passed in **23.66 seconds; 30.75 seconds total**.
  These results precede the later writer-lifecycle corrections below.
- Fixture result publication now uses a completed temporary file plus rename:
  the observer had raced `WriteAllText` and read an empty JSON file.
  The remaining-budget fixture positively establishes a tiny real child's exit
  before testing the one-second replay budget, retaining a 100,000-byte private
  backlog; it no longer assumes a large blocking child write already finished.
  Neither fix extends the asserted budget or removes reading-channel exactness.
- Native coverage remains incomplete. A local PS5 fixture timed out at 20s
  before raw capture appeared; the broader PS7 compatibility selection stopped
  on the real invalid-bootstrap entrypoint's unchanged 20s limit. Another
  PS7 parent-pipe case stopped before its capture-ready phase. These observations
  do not establish a compiler, pipe or filesystem cause and are not waived as
  passes or retried as a timing loop. Fresh hosted acceptance is still required.
- Independent review of the native replay diff found two additional defects:
  storage warnings could cache pre-UTF-8 writers, and one child's terminal
  presentation loss could suppress every later child. The receiving human-output
  seam and each new capture now settle/persist the preceding presentation before
  binding fresh output state. Private loss history survives rearming.
  The four production-order/next-child controls passed on PS5 and PS7 in
  **46.03 seconds; 137.98 seconds including preparation and cleanup**.
- The original eight native presentation functions and decorators were moved
  AST-identically into `test_installer_presentation.py`, separating that
  behavioral contract from the diagnostic foundation. The manual lane selects
  both files one per existing bounded sub-suite; no time budget grows.
  Windows now pins the previously verified seasoned UV 0.13.1 release, while
  Linux keeps its existing canonical profile pin.
- Static contract checks caught an overbroad change to the byte-identical shared
  staging catch and an insertion inside the canonical human-output prologue.
  The shared catch retains its original renderer; rebinding is outside the
  unchanged prologue. The 13-plugin install contract, synchronization and
  targeted Ruff pass. Bounded claims apply to diagnostic forwarding, not a new
  global output owner. No source merge, native-lane result or rollout is claimed.
- Named-defect verification confirmed both writer-lifecycle repairs, without
  claiming runtime qualification. The held source was committed at
  `fd7ac3f6406ecaddc694265e988b4faf396b4d96`. Its managed push hit the existing
  30-second transport deadline after successful identifier/blob guards; a fresh
  provider read positively confirmed that exact draft head reached GitHub.
- Current-head PR CI did not start because the provider reported a dirty merge
  ref. The PR's base snapshot still named `a88812a84e`, but a separate live
  `git/ref/heads/dev` read showed `235e97636d`; the stale PR snapshot was not
  treated as the current base. A preserved local backup preceded reconciliation.
  The rebase retains the new SSH/source-profile lanes and the upstream absolute
  guardian deadline alongside opted-in diagnostic capture. Four option-dispatch
  controls cover neither/either/both inputs and passed in **0.52 seconds;
  13.72 seconds including preparation and cleanup**; the selector-union guards
  passed separately. No deadline, ownership or qualification hold was relaxed.
- A subsequent fetch advanced the base to `0d8e4c8603`. The original five
  published commits were replayed in order, retaining their authors and full
  messages. The reconciliation also retains the newer native manifest-builder
  controls and Logger command portfolio guard. Recovery backups preserve both
  the published head and the earlier locally reconciled candidate; no
  publication bookkeeping was hand-edited.
- Against that reconciled base, canonical-copy synchronization passes, both
  focused workflow-contract guards pass, and the four deadline/capture dispatch
  controls pass in **0.47 seconds; 5.43 seconds including preparation and
  cleanup**. Targeted Ruff also passes. These are source-contract checks, not
  another native timing retry or hosted acceptance result.
- The reconciled draft head `2131ac59b7` reached GitHub through the managed
  source-owned rebase path and was mergeable. Its CI run `40176109325` exposed
  a duplicate `Callable` import introduced by the upstream union and stale
  admitted hashes for the two changed canonical engine modules. The duplicate
  was removed and canonical copies regenerated; only those two source hashes
  were readmitted. Unit nodes, closure membership and budgets remain unchanged.
- Review `4370039962` identified combined cleanup expiry and cancellation:
  preserving `KeyboardInterrupt` or `SystemExit` could hide the exhausted
  cleanup from the outer sandbox and permit another removal attempt. Both
  cancellation variants failed a regression forbidding that attempt. The
  joined cleanup worker now records retention directly on its existing sandbox
  owner before preserving the primary exception; an already-retained sandbox
  cannot enter outer cleanup. All **43** selected cleanup/ownership controls
  pass in **32.15 seconds**, including actual contained readonly-file removal.
  An initial host-interpreter attempt lacked `pytest-timeout`; the provisioned
  runner environment supplied it without changing test deadlines.
- Full-tree Ruff, the touched-module cap, canonical synchronization and all
  admitted unit-source hash checks pass. Missing coverage was provisioned only
  into the repository-local test environment through the seasoning adapter.
  Hosted native acceptance, current-head review and required CI remain gates;
  the draft is not merge or rollout authorization.
- The cleanup/admission repair is committed at
  `d758702702f84b9e3fb5e95df46793996de29f6e`. Two managed publication attempts
  exhausted their unchanged **30-second** transport deadlines. Fresh provider
  reads after each attempt still reported draft head `2131ac59b7`; neither
  attempt is treated as successful. The actual pre-push hook runs identifier
  and pushed-blob checks, not an updater; a separate invocation of those exact
  guards completed in **9.60 seconds**. That does not establish which
  transport phase timed out. No hook, lease or timeout was bypassed.
- A local whole-unit-packet attempt refused the repository-local test
  environment's framework source as an unadmitted repository read before
  executing tests. It produced no evidence packet; its empty output directory
  was removed. Hosted Linux remains the canonical whole-packet lane.
  Its source-admission guard stays intact. Hosted native dispatch remains
  deferred until the repaired head is published, reviewed and passes CI.
- One explicitly authorized publication-only **180-second** managed attempt
  published the repair; its temporary configuration was removed, leaving the
  normal 30-second transport and every test/installer budget unchanged. The
  contribution base then advanced to `de9cd33fef`, requiring another preserved
  eight-commit replay. Only generated human-output hashes conflicted; canonical
  regeneration retained the upstream targeted-update entrypoint behavior.
  A subsequent ordinary push timed out, but the provider positively confirmed
  exact head `360d7d9681f349ed5b30851256ade8a5728dc8a0`.
- That head passed required CI `40179137411`, including the genuine eligible
  unit packet, guards and the required PR gate. Copilot review `5642413763`
  assessed that exact head and recognized the cleanup-retention repair. The
  original output/backpressure and Bash qualification threads remain open;
  no merge approval or native qualification is inferred from its Comment.
- Manual native run `40180104836` checked out that immutable head. Only the
  two selected diagnostics jobs executed. The hardened Linux lane passed its
  five one-file groups (**82 passed, 43 platform skips**) and both real-UV
  capture-off/on variants (**2 passed**). The skipped Windows-only cases are
  not Windows evidence.
- Hosted Windows stopped in its first Bash group: four composed-exit tests
  tried to read the UTF-8 installer using the Python 3.12 host locale, cp1252,
  and raised `UnicodeDecodeError` before native execution. Three POSIX-only
  reservation cases skipped. No later group or real-UV Windows control ran.
  The Bash fixture now names UTF-8 explicitly for source, receipt and archive
  reads/writes rather than changing the runner locale or excluding the tests.
- A targeted local check confirmed UTF-8 mode disabled and cp1252 selected,
  then progressed beyond that source read but timed out inside the first
  Bash subprocess cleanup; the runner failed at **118.91 seconds** and reaped
  its owned group. This is not a successful native result or a proven pipe/
  compiler cause. No local timing retry or budget increase is admitted.
  The fixture repair requires a new exact-head hosted run; all draft and
  rollout holds remain.
