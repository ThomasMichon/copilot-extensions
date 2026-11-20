# Finite fixture-authority and cleanup contracts

Back to [Test Portfolio Rationalization](README.md).

These are complete technical details of the canonical
[Phase 4 Plan](README.md#finite-historical-workflow-fixture-contracts-from-9348)
and [Phase 5 Plan](README.md#finite-finished-daemon-cleanup-qualification-from-12973),
including the [portable fixture families](README.md#finite-portable-pidfd-fixture-contract-from-13082),
not a separate completion ledger or implementation assignment. The
P/V state anchors and actual global G rows remain in the
[Portfolio README](README.md#validation-plan), all unchecked during proposal.
Its continuing Coordination, actual owners and Phase 7 migration-intake route
apply to every clause. The
[dated source and ownership crosswalk](../backlog-reconciliation/journal/2026/11.20-fixture-authority-and-cleanup-receiving.md)
retains evidence, distinctions and separate source-judgment gates.

## Phase 4 tools/workflow - #9348

### P9348-1: Historical recipes

Replace current-CI-derived historical fixtures in
`tools/test_qualify_promotion_candidate.py` with explicit bounded historical
recipes. Retain the `promotion-gate`, `smoke` and release-state-file
preparation signatures and the original three-file selection with
`test_promotion_release_evidence.py` and `test_promotion_artifact_readiness.py`.
The reported 65 failures/90 passes are historical, not a current release failure.

### P9348-2: Controls and current declarations

Preserve qualification, producer, source, effect, timing and publication
controls while current unit-profile tests use their real current declarations.
Retain the existing adjacent `test_verify_promotion_workflows.py` claimant
and its separate 19-failure/51-pass migration. Credit #12981 only at its
verified scope; neither it nor #9349's evidence-size correction proves the
original 65-case family repaired. Coordinate with #3336/#6237.

### V9348-1: Historical qualification

Through the existing bounded development-test path, exercise all three
original files and their intended historical qualification/refusal assertions
after fixture preparation. Preserve every control; do not skip failures,
restore obsolete required release gates or widen permissions.

### V9348-2: Independent current evidence

Independently exercise current unit-profile declarations and the adjacent
workflow-verifier selection. Attribute each actual packet and result
separately, preserving original containment, budgets and source/release custody
rather than treating required unit CI as the historical packet's pass.

## Phase 4 Worktrees - #12968

### P12968-1: Execution identity

Make both `TestCreatePRAutoOpen` fixtures explicitly model distinct
host/persisted-machine and execution identities. The exact cases are
`test_auto_open_records_pr_and_embeds_marker` and
`test_codename_mode_embeds_codename_and_encrypted_identity`; assert intended
`record.execution_machine` attribution, not the configured host or `record.machine`.

### P12968-2: Privacy and current contribution

Preserve raw/codename privacy and the current #13297 contributor's
already-proposed correction, source and publication holds. Its reported prior
hosted qualification is not merged delivery or a new campaign execution.
Its Draft status, older-publisher exclusion and review hold remain; do not
enable its recovery path or repeat its implementation. No new implementation
owner or unrelated #13279 whole-legacy-recovery requirement is appointed to
this finite fixture family.

### V12968-1: Portable case qualification

Qualify both unchanged named cases with a portable fixture whose host and
execution identity differ independently of the test machine. Prove the
intended raw and codename/encrypted attribution while retaining existing
privacy exclusions and original test budgets.

### V12968-2: Historical failure and delivery state

Retain the original 459-pass/two-skip/two-assertion-failure observation as a
deterministic failed qualification, not a flake or CI waiver. Credit the
existing contribution only at its actual source and delivery state; a
different foreign-binding matrix does not satisfy these two cases.

## Phase 4 Worktrees - #13164

### P13164-1: Canonical receiving layout

Align `test_register_session.py` and `test_bind_session.py` fixture inputs
with one canonical receiving layout: the shared `monkeypatch_config` tracking
directory, project directory, synthetic authority configuration and literal
record subjects must agree. Preserve project, workspace and
physical/execution identity distinctions.

### P13164-2: Distinct authority outcomes

Retain successful registration with fresh trusted identity, omission of
foreign-project identity fields, unchanged binding when the optional-stamp
lock is contended, and actual stale-record/foreign-authority negative controls.
These are distinct expected outcomes, not uniformly refusals. Correct fixture
setup rather than blanket-patching the production authority fence, suppressing
failures or increasing budgets. The original 65 failures/151 passes and
downstream missing registration are failed broader command qualification,
not a result of #13162's focused controls.

### V13164-1: Command and authority qualification

Re-run the unchanged two-module command selection AND independent
receiving-authority controls through their existing contained runner.
Demonstrate intended handoff/event behavior, successful fresh trusted identity,
omitted foreign-project identity fields, unchanged binding under optional-stamp
lock contention and meaningful actual stale/foreign negatives. Preserve
production authority; a successful or unchanged-binding case must not be
rewritten as a refusal.

### V13164-2: Neighboring packet boundaries

Keep #13300's twelve-file/prune packet, #12879's seed-delivery/native seed-exec
family, hosted activation, native hold, registration transaction and
request-budget selections separate. A pass of those controls or required
unit CI does not qualify this command packet.

## Phase 5 owning runner - #12973

### P12973-1: Attempt-bound evidence

Preserve the historical Windows observation: two selected real-installer
monitor-drain tests passed, then `process-reused state=finished` held the
sandbox and the runner exited nonzero with unsettled cleanup. Retain available
historical evidence through its current owner, with unavailable historical
record/process-birth/supervisor/settlement/artifact details explicitly unknown,
not a mandatory unavailable-input prerequisite.

A bounded instrumented Windows reproduction may instead capture its OWN daemon
record, exact process-birth identities, supervisor/settlement witness, artifact
state and cleanup result. Bind every result to the attempt that supplied it.
New reproduction evidence does not establish missing facts or the cause of the
historical run.

### P12973-2: Discriminate the cleanup cause

Diagnose whether the fixture failed to settle its instance OR cleanup
mishandles a proven-dead original identity, using the evidence-bearing attempt.
Keep both possibilities unresolved until that evidence discriminates them.
A merged #7715 reference, finished record or reused PID is not original
descendant/artifact settlement. Preserve actual daemon-instance, fixture,
topology and cleanup custodians. A reproduced cause remains distinct from
the historical cause unless available historical evidence establishes the link.

### V12973-1: Bounded Windows evidence

Use available CI evidence OR a bounded instrumented Windows fixture for the
exact selection
`real_installer_returns_before_resident_owned_monitor_drain and not absent and not stale`
in `tests/test_python_runtime_install.py`. A reproduction supplies its own
record, process-birth, supervisor/settlement and artifact evidence; historical
artifact availability is not a prerequisite for that path.

Bind the historical 2-pass/151-deselected/258.79-second assertion result
separately from its failed final cleanup gate. Establish the actual ownership
transition and safe final settlement for the evidence-bearing attempt; report
the historical cause as unknown when its evidence is unavailable.

### V12973-2: Safe settlement and refusals

Preserve refusal for unknown identity, unrelated PID occupants, unconfirmed
descendants and changed artifacts. Qualify actual safe settlement and cleanup
under original bounds; do not treat every reused PID as safe, skip cleanup,
raise budgets or blindly rerun the workflow. The reproduction alternative
does not grant native execution, process action or artifact deletion.

## Phase 4 Vault/shared-library - #13082

### P13082-1: Portable peer handle

Correct the peer-process-handle helper in
`plugins/agent-vault/tests/test_service_generation.py` so Linux portable
CPython builds need not expose `os.pidfd_open`. Use the existing
`agent_procutil._linux_pidfd.open_pidfd` wrapper/libc fallback OR skip with
a clear reason when neither wrapper nor libc support is available.
Preserve the Windows branch and intended peer-handle behavior; this is a
test-helper correction, not a new production fallback or relaxed custody.

### P13082-2: Original module and custody

Retain the original nine-error `AttributeError` observation on a managed
portable Python 3.12 Linux/WSL interpreter and the exact
`tests/test_service_generation.py` selection. Earlier #6354 diagnostic work
is precedent for the capability gap, not delivery of this test-helper repair.
Keep existing Vault generation, process-identity, fixture and runner owners.

### V13082-1: Original portable module

Exercise the original module through the bounded managed plugin-test runner
on the supported portable Linux interpreter that lacks the OS wrapper.
Demonstrate the shared libc fallback where available OR the clear unsupported
skip when neither supported mechanism works. Do not silently skip a supported
fallback, alter assertion intent or substitute another cache/diagnostic test.

### V13082-2: Faithful outcomes

Retain peer-handle/process-incarnation assertions and actual owned cleanup.
Distinguish a passing supported module from a justified unsupported skip
and unavailable execution. Preserve original item/group/plugin/resource
bounds and the Windows path; planning supplies no new test/native authority.

## Phase 4 Containers - #13369

### P13369-1: Complete resource fixtures

Align BOTH `tests/test_execution_runtime_distribution.py` fixture projects
and `tests/test_runtime_profile.py` staged payloads with the current intended
`standalone_build.py` runtime-resource contract. Retain
`versioned_runtime_identity.py`, `versioned_runtime_fingerprint.py` AND
`versioned_runtime_cache.py` alongside the existing resources. Refresh
wheel/sdist contents and expectations AND per-resource refusal cases;
adding three fixture-map entries alone does not complete this correction.

### P13369-2: Assertions and independent scope

Preserve builder/source-completeness, owned-runtime safe-path/isolation and
native-policy refusal assertions rather than changing the builder contract
merely to satisfy stale fixtures. The report at `562e27da78` observed five
failures/twenty errors in distribution and three profile failures while
validating #13366. #13371's discovery work and #13479's Fleet changes
explicitly excluded this packet; #13698's local-transport plan is not a
fixture implementation. Required CI's unit-only admission stays unchanged.

### V13369-1: Both original files

Exercise both original test files through the existing bounded managed
Containers runner against the actual intended current builder resource
contract. Qualify complete fixture projects, generated wheel/sdist
expectations and each missing-resource refusal rather than counting a
successful build or three added files as the entire packet's pass.

### V13369-2: All three profile cases

Qualify BOTH
`test_staged_profile_loads_only_its_owned_runtime_with_safe_path[safe-path|isolated]`
cases AND `test_native_profile_policy_refusal_retains_selected_runtime`.
Retain safe-path/isolation and selected-runtime retention on refusal.
Attribute actual current results separately from historical missing-file
errors; preserve source/build/fixture/runner custody, original limits and
explicit native admission without enrolling these tests in required CI.

## Phase 4 Bridge - #13378

### P13378-1: Six-case packet

Retain all six original cases and their distinct Linux managed-runner failures:

- `test_caller_session_id.py::test_migrates_existing_v24_database` refused
  writable `live_messages_runtime_admission` preparation with
  `RuntimeCustodyError`.
- The four `test_remote_venue_cli_records_caller_session` combinations,
  create AND send across both CodeSpace AND SSH venues, exceeded the
  30-second pytest timeout.
- `test_cli_mode_reservations.py::test_a_pid_reused_within_250ms_is_still_a_different_process`
  returned `live` instead of `incarnation_mismatch`.

The original run stopped at sub-suite 4/39; later sub-suites were not reached.
These are attributed failures found while validating #13366, not fresh
reproduction, one proven cause, or evidence that later selections passed.

### P13378-2: Original outcome alternative

For each original case, obtain a supported managed-plugin-runner pass OR
justify its actual host dependence and declare its explicit tier. Preserve
writable migration preparation, correct caller attribution and process-birth
identity expectations; investigate fixture versus production cause rather
than blanket-patching custody checks, treating a reused PID as the same
process, increasing timeouts or labeling deterministic failures flakes.
Keep the existing migration, caller, reservation/process, fixture and runner
owners; explicit tiering retains coverage instead of silently removing it.

### V13378-1: Complete selected outcomes

Qualify all six named cases through the original bounded managed Bridge
runner, recording each selected supported pass OR justified host-dependent
tier and its intended execution path. Cover all four operation/venue
combinations independently; a different transport/caller pass, discovery
change or required unit result does not qualify this six-case family.

### V13378-2: No false aggregate

Preserve migration/preparation refusal controls, caller-session assertions
and the 250-ms PID-reuse incarnation distinction under original deadlines,
containment and owned cleanup. Attribute a failed/stopped packet and every
later not-run sub-suite truthfully. Tier classification is not an executed
pass or permission for live venues, native work, process action or resources.
