# Installed Manager TTY Qualification

- **Slug:** `installed-manager-tty-qualification`
- **Repo:** copilot-extensions
- **Branch(es):** reviewed proposal, then implementation PRs against `dev`
- **Created:** 2026-11-07
- **Status:** Active
- **Vision:** [release qualification](../../../visions/release-pipeline/README.md#configured-feed-construction-and-first-launch), [fresh-machine behavior](../../../visions/clean-room-validation/README.md#fresh-machine-fidelity), [authoritative replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
- **Umbrella issue:** #11377
- **Related owners:** #3336 release control; #7529 configured-feed construction; #5579 installed launcher chain

## Guiding Intent

Promotion must not publish a candidate that installs successfully but cannot
open the Manager's interactive terminal through an adopted project's binstub.
The installed consumer, including its terminal admission and input handling,
is the acceptance boundary. This is a bounded extension of installed-entry
qualification, not a return of the general integration portfolio to unit CI.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Qualification coordinator | Plan, integration, validation and PR ownership | Managed source `threaded-ledger` |
| Native witness contributor | Isolated installation and real terminal consumer witness | Same source branch; coordinator-mediated assignment |
| Promotion contributor | Existing release-lane integration and evidence admission | Same source branch; coordinator-mediated assignment |
| Main activation coordinator | Workflow-only controller preparation and reviewed adoption | Managed source `quiet-sprocket`; same PR owner |

## Coordination

- **Topology:** one Dev implementation worktree and a separate workflow-only Main
  activation worktree; Dev contributors have disjoint edit ownership.
- **Host (owns PRs):** Qualification coordinator; contributors do not commit,
  rebase or publish independently.
- **Delegates:** Native witness contributor owns the selected native fixture;
  Promotion contributor owns selected workflow/control and gate tests.
  Exact files are agreed before edits.
- **Handoff:** return tested artifacts and exact candidate/evidence identities
  to the coordinator. Keep release-control, native execution, construction and
  lifecycle owners; do not supersede another contributor's open PR.

## Context

A project launcher discovers its registered Manager and checks its usability.
The installed Windows Manager launcher can perform a native terminal capability
query even for a version request. Failure there prevents the interactive
front door from reaching the TUI, despite an existing installation completion
record. A version-only or construction-only witness misses the requested
install-to-interaction contract.

The existing release vision already admits bounded construction/installed-entry
and native Windows consumer qualification. This slice extends that owner rather
than creating another release publisher, test scheduler or unit-test category.
The installed-launch witness must address the exact frozen candidate and
configured execution inputs that the existing promotion owner will publish.

## Request

Verbatim operator requirement:

> We must block promotion if launch after install fails, so we need a test that actually does install, with a representstive harness repo, and then launches the worktree-manager via the binstub, and ensure it loads the TTY.

## Scope Approvals

This request authorizes the bounded source test and promotion-gate change.
It does not authorize changes to an operator installation, queue recovery,
runner enrollment, service restarts, security/feed policy or credentials.
Any separately authorized live rollout remains a separate objective.

## Plan

The following realization details are **agent-recommended**.

### Phase 1 - Review the consumer and gate contract

- [x] Identify the existing actual-install/native-terminal fixture owners and
  supported Windows qualification lane; agree exact implementation file ownership.
- [x] Review this proposal and the narrowly extended installed-entry vision
  before implementation.

### Phase 2 - Prove actual installed terminal readiness

- [x] Install the candidate through its authentic installation owner into fresh,
  isolated state with a representative generic adopted harness repository.
- [x] Invoke that repository's generated binstub through the supported Windows
  terminal path, reaching the installed Manager rather than a source import
  or a substituted direct module invocation.
- [x] Assert real visible TUI readiness and input response, then close the
  fixture through its owner and verify bounded descendant cleanup.
- [x] Add a falsifying control where installation succeeds but installed launch
  fails; the witness must fail without retries or improvised repair hiding it.

### Phase 3 - Enforce the result before promotion

- [ ] Reuse the supported native qualification lane with evidence bound to the
  frozen candidate, installation inputs and actual terminal consumer.
- [x] Refuse promotion for failed, timed-out, missing, skipped, incomplete or
  stale qualification; cover these decisions with bounded in-process tests.
- [x] Document invocation, input/evidence identity and failure ownership; retain
  the existing unit coverage, source integrity and publication gates.
- [ ] Validate and land the implementation through normal review, and verify
  actual gate activation without claiming an unperformed host rollout.

## Validation Plan

- [x] The actual installer constructs a fresh fixture; it does not reuse the
  operator's selected runtime, caches as installation state, or private repository.
- [x] The exact generated project binstub launches the installed Manager under
  a real supported Windows TTY and the observed UI responds to a test input.
- [x] Positive and install-success/launch-failure controls exercise the actual
  consumer; process existence, `--version`, mount callbacks or synthetic ready
  files cannot satisfy the TTY assertion.
- [x] Timeout, cancellation and failure retain diagnostics and settle only
  fixture-owned resources without altering host daemons, sessions or selections.
- [x] Gate decision tests reject stale candidate identities and every missing
  or non-success result; the live/native witness is not reclassified as unit proof.
- [ ] A supported promotion run or the existing owner's equivalent exact-candidate
  qualification demonstrates that this gate actually blocks publication.
- [x] Source review, required CI and installation-contract checks pass; any
  unavailable validation tier or actual infrastructure blocker is explicit.

## Proposal

Proposed: preserve the existing bounded installed-entry/native qualification
architecture and strengthen its consumer contract to actual project-binstub
TTY readiness. Exact lane and fixture integration follows the bounded owner
inventory; unsupported execution must not be reported as a successful gate.

## Journal

### 2026-11-07 - Request captured and source scope claimed

- Captured the full request, retaining actual installation, a representative
  harness repository, binstub invocation, TTY readiness and blocking promotion.
- Claimed #11377; existing construction, native qualification and release-control
  owners remain authoritative. No implementation or live rollout is claimed.

### 2026-11-07 - Reviewed contract and implementation assignments

- Proposal #11380 merged after current-head approval and required CI.
- The native witness contributor owns
  `worktree-manager/tests/native/test_manager_installed_tty.py` and the bounded
  `tools/run-manager-runtime-acceptance.py` contract extension. Existing real
  installation, source-origin and ConPTY cleanup owners are reused.
- The promotion contributor owns the installed-TTY job in
  `.github/workflows/validate-and-promote.yml` and focused gate wiring tests.
  The existing frozen-candidate import and disposable Windows lane are reused.
- The independently governed Main controller preserves its workflow bytes;
  ordinary Dev promotion cannot activate this workflow change. After reviewed
  implementation, a separate lawful workflow-only Main PR and actual gate
  execution remain required. No gate activation is yet claimed.

### 2026-11-07 - Gate decisions validated; native installation still pending

- The coordinator independently ran the bounded canonical tool packet:
  installed-Manager gate, release-controller contract, promotion lifecycle and
  merge-wait tests. All 138 tests and 60 subtests passed.
- The first genuine fixture installation failed before TTY startup because its
  nested temporary root exceeded the Windows path budget with immutable runtime
  generations. The fixture and workflow use a shorter owned root; no production
  path policy, package feed or timeout was changed to conceal the failure.
- The real installed terminal positive and negative controls are still required.
  Unit decisions, source wiring and artifact paths do not establish that result.
- The workflow-only Main candidate retains independently governed control bytes.
  Its exact-controller merge-wait tests passed all 42 cases, and release-tool
  compatibility passed for active and prepared inputs. Publication-boundary
  validation exposed a coupled fixture missing the new native dependency.
  The corrected fixture retains legacy expectations and explicitly rejects
  failed, cancelled, skipped, missing, unknown and incomplete native results.
  The coordinator independently re-ran all 25 exact-controller boundary cases
  successfully. These fixture results are not live gate activation.

### 2026-11-07 - Hosted validation candidate prepared

- The installed-TTY contract uses one shared 720-second bound, including
  30 seconds for cleanup. Production inactivity limits, the native capability
  deadline and other acceptance contracts are unchanged.
- Genuine local construction completed Manager installation and inventoried
  3,757 canonical slot entries. Engine became healthy, but helper publication
  refused the fixture's marketplace ownership layout. The fixture now uses
  the exact candidate marketplace declaration; native TTY acceptance is still
  unproven.
- The coordinator independently passed both receipt regressions through the
  isolated plugin runner, plus scoped lint, version and installation checks.
- Local fixture diagnostics and custody receipts were retained outside the
  checkout. All original fixture processes were absent and owned Jobs settled
  before canonical cleanup removed only the two fixture roots.
- The actual installed-project-binstub selector will run on the supported
  owner-only hosted Windows lane against a published draft implementation
  commit. That functional run does not replace the final frozen-candidate
  promotion gate or its separately reviewed Main-controller activation.

### 2026-11-07 - Hosted engine refusal retained as failure

- The owner-only hosted request admitted the exact published source but failed
  during engine installation before any TTY readiness assertion. Manager
  installation succeeded; both installation process Jobs settled.
- The original failure artifact truncated the nested installer error twice,
  leaving the engine cause unknown. The fixture now preserves bounded causal
  stderr and xunit2-compatible evidence without changing installation policy,
  seed versions or deadlines.
- The coordinator independently passed all four receipt/diagnostic regressions.
  The same actual consumer selector must run on the new published head; no
  native pass or live gate activation is inferred from this diagnostic repair.

### 2026-11-07 - Cold helper-publication handoff corrected

- Both hosted attempts failed before TTY startup: the Python installer held an
  admitted lifecycle lease, but its helper-publication child received no ticket.
  Existing callback fixtures supplied the ticket explicitly, masking cold
  installation. The new regression starts without an inherited ticket.
- Helper publication now forwards the same validated borrowed lease into its
  child environment. It creates no new authority, leaves the parent environment
  unchanged and does not preselect runtime generations or bypass ownership.
- The isolated source-pin and cold-callback packet passed all seven cases.
  Eight native legacy authority-refusal cases passed separately, preserving
  owner, stale-owner, governance and token checks. Larger exploratory packets
  reached their existing group deadline; no full-suite pass is claimed.
- Scoped Python lint and all 13 runtime installation contracts passed.
  The updated source must still pass the genuine hosted install-to-TTY positive
  and falsifying controls before review and Main-controller activation.

### 2026-11-07 - Retained-source fixture path corrected

- The next hosted run passed both installers, then stopped before TTY startup
  because the witness looked for the engine source receipt inside its runtime
  slot. The installation owner actually retains it under
  `snapshots/<source-runtime-version>/runtime-source.json`.
- The native witness now follows that existing retained-source contract.
  Exact receipt equality and source-byte validation remain required; no
  missing receipt is fabricated or copied into place.
- The coordinator independently passed the focused fixture regression through
  the canonical isolated runner, covering the real snapshot path, mismatched
  identity and missing receipt. Scoped lint and whitespace checks passed.
  Genuine installed-binstub terminal acceptance and final frozen-candidate
  gate activation remain pending.

### 2026-11-07 - Registration failure diagnostics retained

- The corrected source receipt check passed in the next hosted attempt.
  Project registration then exited nonzero before any TTY assertion.
- Registration reports command failures on stdout, but the witness exposed only
  stderr and its initial polling counts. The actual refusal was therefore
  unknown, not evidence of a particular registration-policy defect.
- The witness now retains both streams, final output counts, argv, fixture
  environment and child-custody diagnostics. Registration arguments, source
  validation, deadlines and acceptance assertions are unchanged.
- The coordinator independently passed the focused failure-diagnostic
  regression through the canonical isolated runner. Scoped lint and whitespace
  checks passed. A new exact-head hosted run must expose the original refusal.

### 2026-11-07 - Representative harness adoption corrected

- Complete diagnostics exposed a valid production refusal: the fixture had
  declared `expose_agent: false`, making it a source-only project ineligible
  for managed adoption. `--no-agent` does not waive that policy.
- The representative fixture now positively declares an eligible harness and
  retains `--no-agent`, with no launch or headless intent. Production policy,
  installation isolation and the actual generated-binstub consumer are unchanged.
- The coordinator independently passed the focused real-policy regression,
  including rejection of the source-only declaration, through the canonical
  isolated runner. Scoped lint and whitespace checks passed.
- The next exact-head hosted run still owes installed TTY/input/exit and
  falsifying-control evidence. None of the earlier failures establishes it.

### 2026-11-07 - Native CMD binstub invocation corrected

- The eligible harness registered successfully, and the generated project
  binstub's ownership check passed. Its first ConPTY invocation then failed
  because the fixture supplied embedded escaped quotes to CMD's command parser.
- CMD now invokes the same generated binstub with `call` and normal path
  encoding. This does not replace the installed consumer with a module import.
- The coordinator independently passed the real CMD/ConPTY regression through
  the canonical isolated runner: a spaced binstub path produced the expected
  output and exit code, and the owned Job was empty. Scoped lint and whitespace
  checks passed.
- Actual installed Picker rendering, input response and the falsifying control
  must still pass on the next published candidate before review and activation.

### 2026-11-07 - First actual installed consumer qualification passed

- The supported owner-only hosted Windows lane passed the actual installed
  project-binstub TTY test on source
  `6fd7331c8db742e5365953973543bdf158d1cb68`, run 42434959858, attempt 1.
  The typed result qualified the exact requested and actual source, and its
  canonical-test and runtime phases passed. The actual test took 146.909 seconds.
- This test performed fresh installation, generated CMD and PowerShell
  project-binstub invocation, native terminal rendering/input/exit observations
  and the install-success/launch-failure falsifying control. Receipt-only and
  diagnostic fixtures are not the basis of this result.
- The source PR moved out of draft. Dev had advanced during qualification;
  reconciliation preserved the newer adjacent effort-index state. The resulting
  source head still owes exact-head qualification, review and required CI.
- Phase 2's implemented consumer contract is proved. Phase 3, final frozen
  candidate execution, independently governed Main activation and any authorized
  operator installation remain open. This effort is not Done.

### 2026-11-07 - Blocking cleanup and Git-context review findings fixed

- Source review identified two High-severity fixture defects: an unreaped
  process-tree error could be converted into ordinary failure before an outer
  sandbox removed the retained tree, and inherited Git context could redirect
  private fixture operations into the caller's repository or index.
- The acceptance runner now writes bounded failure evidence and re-raises the
  original unreaped-process exception. Diagnostic or report-write failures cannot
  mask it, and the falsifying-control path preserves the same exception.
- Candidate inspection and private harness operations use the existing
  repository-identity environment scrubber, preserving fixture HOME while
  removing inherited Git repository and configuration selectors.
- The coordinator independently passed all three focused regressions through
  the canonical isolated runner: nested-owner retention, original exception
  identity despite report failures, and real private Git/index isolation.
  Scoped lint and whitespace checks passed.
- These fixes still require current-head native qualification and a passing
  source review before merge. Main activation and actual frozen-candidate
  publication remain separate obligations.

### 2026-11-07 - Negative control distinguishes launch rejection from cleanup failure

- The review-fixed source `eb57a2414ad5867bc5e944419fd4777feb34f7d7`
  passed real hosted native qualification in run 42438047426, attempt 1
  (actual test 143.850 seconds), and required unit/guard CI passed.
- Fresh review resolved the first two findings but identified a remaining
  High-severity case: generic cleanup or infrastructure exceptions could be
  credited as the negative control's expected launch failure.
- The negative control now accepts only a dedicated rejection with a nonzero
  controlled consumer exit and documented rejection text, after successful
  terminal cleanup. Job accounting and terminal teardown failures retain
  uncertain custody through the existing unreaped-process exception contract.
- The coordinator independently passed all five focused regressions through
  the canonical isolated runner, including actual cleanup exception types,
  nested-owner retention, expected rejection and bounded native CMD invocation.
  Scoped lint and whitespace checks passed.
- Current-head native qualification, fresh review and required CI remain
  necessary. No Main-controller activation or operator update is claimed.

### 2026-11-07 - Qualified source implementation merged; Main activation next

- Source PR #11398 merged as
  `c0f2173cd30c5af3532ad4b75f2c3bbdf856e6ff` after exact-head native
  qualification of `72b76c864b191e7a81cf34d49aeb19934cd167cb`.
  Hosted run 42439662356, attempt 1 qualified the exact requested/actual
  source; the real test took 195.898 seconds.
- Current-head Copilot review 5816193369 had no open findings. Its remaining
  exact-head-native validation request was satisfied before merge. The latest
  required unit, guard, lint, source-attribution and workflow checks passed.
- A description update legitimately retriggered same-head CI. The first merge
  attempt was refused while that newest required check was expected; the
  coordinator waited for run 42440169515 to pass before normal managed merge.
  No CI bypass or raw provider merge was used.
- The source workspace pulled forward onto the confirmed squash merge.
  Its native, failure-retention, isolation and bounded gate-decision validation
  items are resolved; the effort remains Active for actual release enforcement.
- Next stretch: reviewed workflow-only Main adoption, then an actual frozen
  candidate run proving the installed-TTY gate participates in publication.
  The prepared Main controller reconciled without conflict and passed its
  25 publication-boundary and 42 merge-wait cases. Prepared tests and source
  merge are not Main activation, released deployment or an operator update.

### 2026-11-09 - Actions context validation fixed before Main activation

- GitHub rejected the Dev workflow and the prepared Main controller because
  job-level environment expressions cannot reference `runner.temp`.
  The earlier unit and controller tests did not detect this Actions schema error.
- The installed-TTY job now resolves its short temporary root in the invoking
  step and persists that same root for the existing receipt and artifact steps.
  Candidate identities, deadlines, native controls and promotion decisions
  are unchanged.
- The coordinator independently passed 41 installed-Manager gate tests,
  25 exact-controller publication-boundary tests and 42 merge-wait tests.
  Scoped Python lint and whitespace checks passed.
- Checksum-verified actionlint 1.7.12 rejects the previous Main workflow with
  the same invalid context and accepts both corrected workflow copies.
  Shellcheck and pyflakes were disabled for this Actions-context check.
- Both corrections still require normal publication, current-head review and
  required CI. Main activation and actual frozen-candidate release evidence
  remain open; this effort is not Done.
- Dev advanced while the correction was being prepared. PR #11484 already
  landed the same parser fix, using one early initialization step. The
  follow-up preserves that implementation and #11563's bounded pinned
  PSMux acquisition rather than publishing a redundant workflow correction.
  The stronger forbidden-context regressions remain, adapted to the
  canonical early initializer. Main still lacks the installed-TTY gate.
- The latest release attempt, run 42630958288, failed during artifact
  attestation because no Sigstore verifier could initialize. The existing
  watchdog report #11691 retains that failure; no attestation policy was
  weakened and no release retry is claimed as a fix.

### 2026-11-09 - Native monitor fixture blocked workflow-only Main adoption

- PR #11693 merged as `14b9295cbccab2c708566fb7b6f1647d1a37bb96`
  after current-head Copilot approval and all required checks passed.
  The source workspace pulled forward and this effort was reread.
  The actual Main gate and frozen-candidate completion items remain open.
- Rebased Main PR #11478 passed Actions parsing, but its existing
  native monitor lane failed on both Linux and Windows in run 42636151633.
  The failure precedes installation: adding marketplace metadata changed
  the frozen fixture's payload fingerprint.
- Main's release source vendors dependencies beneath the plugin, so the
  common frozen package root can equal the fixture repository root.
  Dev's sibling-library layout had hidden that collision. The fixture now
  retains source beneath a dedicated child of the repository, keeping Git
  and marketplace metadata outside both dependency layouts.
- The source correction retains real Git provenance and pre/post payload
  fingerprint equality. Focused cases exercise both layouts. It must land
  through Dev and normal release before the workflow-only Main PR can use it;
  no non-workflow source is inserted directly into Main.
- Both focused real-Git layout regressions passed through canonical test
  containment. Scoped Ruff, whitespace and all 13 installation-contract
  checks passed. The existing actual Windows installer case is still under
  validation; no native pass is claimed by those two regression cases.
- Two local invocations used the generic 300-second group limit and timed
  out during construction, with owned cleanup completed. The native lane
  already declares 900-second test, group and plugin bounds. Validation
  now uses that existing profile, not a changed timeout or production policy.
- Draft fixture PR #11696 qualified source
  `13aff99a06c99178983152ee5f4c46de8ba7608e`: hosted native run
  42641072850 passed on Linux and Windows. The local actual installer
  case passed in 416.32 seconds; canonical preparation and verified cleanup
  completed in 521.31 seconds. The PR is now out of draft.
- Current-head review found a second consumer of the moved fixture:
  the native source-preparation copy helper only accepted the old layouts.
  The helper now normalizes the source child while retaining its original
  recursive-destination refusal. Focused layout and recursion cases cover
  old and nested, vendored and separate-library inputs. All nine focused
  fixture, copy and recursion cases passed through canonical containment;
  scoped Ruff and whitespace checks passed. Updated publication and fresh
  current-head review remain required.
- A complete shared-fixture call-site check also found the native seed runtime
  deriving its source root by parent depth. It now resolves the real Git root
  with its existing bounded process owner and uses the same normalization
  helper, preserving the original runtime-only copy exclusions.
  Nine copy, exclusion and recursion controls passed with the native consumer
  module imported through canonical containment; scoped Ruff and whitespace
  checks passed. No additional shared-fixture consumer assumes the old root.
- Fresh review identified the remaining provenance dependency: normalization
  must recreate the fixture marketplace catalog outside copied package inputs,
  with its plugin source pointing at the normalized checkout location.
  The shared helper now supplies that generic catalog and the layout controls
  resolve its source to the actual copied plugin.
- The canonical focused packet passed all ten cases, including fresh runtime
  construction and the real no-seed native backend consumer. Test execution
  took 281.93 seconds; preparation and verified cleanup completed in
  395.14 seconds. This closes the observed catalog gap without weakening
  provenance checks or substituting a source import for installation.
  Fresh review, required CI, normal release and Main activation remain open.
