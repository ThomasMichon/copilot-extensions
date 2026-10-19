# Lean Runtime Dependencies

- **Slug:** `lean-runtime-dependencies`
- **Repo:** copilot-extensions
- **Branch(es):** independent sequential slice PRs against `dev`
- **Created:** 2026-10-11
- **Status:** Active
- **Vision:** [Plugin services](../../../visions/plugin-services/README.md):
  `capability-sized-runtime-dependencies`, `compatible-helper-runtime-reuse`;
  [agent-index](../../../visions/plugins/agent-index/README.md):
  `lightweight-client-and-declared-host-service`
- **Umbrella issue:** #6234
- **Related issues:** #5768, #5873, #4876, #6143

## Guiding Intent

Reduce repeated installation, package footprint and cold-start work without
removing useful capabilities or weakening independently installable services.
The first wins are removing the unused worktrees UI dependency and completing
the index client/service dependency boundary. Compatible separate-process helper
reuse is a first-class investigation, not a preselected mandatory global broker.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Proposal, integration, sequential PRs and completion | Managed upstream worktree |
| Helper investigator | Bounded shared-runtime and ZDD evidence | Read-only delegated investigation |

## Coordination

- **Topology:** independent sequential slice PRs; one implementation writer per slice.
- **Host (owns PRs):** Coordinator.
- **Delegates:** Helper investigator returns evidence and a compatibility proposal;
  no concurrent edits or publication from the coordinator's worktree.
- **Handoff:** this README owns the completion gate; each successor reads the
  current slice and journal. No phase completion closes the campaign.
- The index service distribution and rollout remain owned by
  [agent-index standalone service](../agent-index-standalone-service/README.md)
  (#5768). This effort owns dependency boundaries and measured footprint changes,
  not a competing index service implementation. Coordinate overlapping installer
  work with #5873 and open PRs before editing.
- Shared daemon ownership composes with #6143; artifact selection composes with
  #4876. Do not recreate either campaign or preempt another contributor's PR.

## Context

[Dependency audit](audit.md) preserves the measured motivation, package frequency,
source evidence, recommended reductions and limits of the measurements.
[Shared-runtime investigation](shared-runtime.md) defines the decision gate for
installer de-vendoring and candidate shared ZDD fronts.
[Profile results and remaining acceptance](profile-results.md) consolidates the
delivered source changes, fresh-install measurements, validation limits and
explicit unresolved owner boundaries.

Promotion already expands canonical shared-library references into independently
shippable payloads. That source-sharing mechanism does not itself avoid repeated
venv installation. Installed compatible helpers can potentially be reused over
efficient process boundaries while payloads remain self-contained.

## Request

Operator direction, with only private destination names abstracted:

> This is great analysis. Ensure this is documented as the motivation for an
> effort (and put a log/journal entry in [the private knowledge repository]), and
> then plan an effort to go down your priority list and tackle the biggest
> issues. Textual removal from agent-worktrees is an obvious win, as is finishing
> the server/client split in agent-index. I also think there's an opportunity to
> have our service installers explicitly "de-vendor" shared code, so that if
> installation of one service already put together a shared-lib venv then another
> install doesn't have to. This of course only works if we have some efficient
> separate-process helpers. ZDD fronts are a good example: we might get away with
> having only one instance of ZDD, for example.
>
> Use your analysis to drive vision updates, effort buildouts, and get started
> on work.

The operator confirmed the slug `lean-runtime-dependencies`.
The additional package/profile priorities below originate in the audit and are
marked agent-recommended; exploratory backend replacements are not commitments.

### 2026-10-16 - Operator-approved zero-configuration default

Follow-up direction, with only the private adopter name abstracted:

> We need to make an executive decision on [the adopting harness], on the
> assumption that most users will never adjust local config for themselves.
>
> Great. Update and drive the vision and effort for this

**Approved decision summary:** the adopting harness owns a complete baseline,
not a requirement for personal approval configuration. Prefer verified
release-approved exact profiles; automatically select a distinct governed
source lock when artifact availability/admission prevents that mode. CPU is
the fresh engine default; retain explicit accelerator intent and explicit
unprofiled migration. Reuse admitted unchanged inputs. Existing feed,
seasoning, integrity and provenance restrictions remain authoritative.
Optional local/knowledge overlays are not prerequisites. This authorizes
driving source implementation, not production rollout, model/corpus mutation or
new package feeds.

## Plan

### Phase 1 - Reviewed intent and dependency baseline
- [x] Land this proposal, the audit motivation and vision amendments through review (#6235).
- [x] Refresh declaration/import and installation-profile evidence against the
  execution snapshot; record direct/transitive, active/retained and logical/physical distinctions.
- [ ] Coordinate overlapping index and daemon work with existing owners.

### Phase 2 - Worktrees UI dependency removal
- [x] Remove Textual from agent-worktrees while retaining Worktree Manager's UI.
- [x] Preserve version comparison and Manager process dispatch without relying
  accidentally on removed transitive packages.
- [x] Add dependency-minimal regression evidence, update directly related docs
  and ship the slice with a changefile.

### Phase 3 - Index client and hosted-runtime separation
- [x] Complete lightweight client import/install boundaries and route hosted
  controller, indexing workers and foreground commands through their owning runtime.
- [x] Eliminate duplicated store/server installs only after replacing the
  primary-runtime fallback with a supported explicit outcome.
- [ ] Preserve native and standalone-service contracts, configuration, queue,
  corpus state and the separately managed warm embedding engine.
- [x] Isolate legacy stdio MCP in its own opt-in extra without removing
  agent-facing tool capabilities. _(agent-recommended)_

### Phase 4 - Largest optional installation costs
- [ ] Govern explicit compatible CPU/GPU engine wheel profiles; eliminate
  accidental accelerator downloads and unsafe install-then-swap assumptions.
  _(operator-approved default; coordinate artifact ownership with #4876)_
- [ ] Implement the reviewed [zero-configuration default](zero-configuration-defaults.md)
  through the existing installation authority: authentic release profiles first,
  automatically authorized governed source fallback, optional overrides only.
  Do not require users to assemble an approval map or wait for artifact
  publication before source installation.
- [x] Make restricted-SSH Paramiko provisioning genuinely opt-in.
  _(agent-recommended)_
- [x] Replace full-table pandas statistics materialization with projected
  bounded aggregation. _(agent-recommended)_
- [x] Decide syntax grammar extras and unseeded installer profiles from measured
  benefit; retain supported fallback installation. _(agent-recommended)_

### Phase 5 - Installer de-vendoring and shared helpers
- [x] Execute the [shared-runtime investigation](shared-runtime.md), including
  separate-process reuse and a shared ZDD front candidate.
- [x] Review the chosen compatibility, lifecycle and installation-cell design
  before implementing a reuse mechanism.
- [ ] Implement the smallest accepted reuse slice, or explicitly transfer an
  accepted-but-deferred design to a named tracked objective. If evidence rejects
  reuse, retain per-service execution and close that implementation path with
  measured costs and the rejection decision; do not transfer rejected work.

### Phase 6 - Close the measured delta
- [x] Publish before/after profile footprint and fresh-install evidence, with
  running-state proof for any authorized rollout.
- [ ] Resolve or explicitly transfer every implementation and validation item;
  experimental ONNX/storage replacements remain separately tracked decisions.
- [ ] Update reality docs and archive only after the campaign completion gate.

## Validation Plan

- [x] Unit and dependency-contract tests cover each changed declaration,
  optional extra, import boundary and helper compatibility rule.
- [x] Simple end-to-end checks exercise actual client/Manager/worker process
  boundaries in dependency-minimal environments, not only mocked imports.
- [ ] Clean-room first installation proves each affected solo plugin/profile,
  combined installations, reuse and missing/incompatible helper outcomes.
- [ ] Windows, Linux and macOS checks preserve supported installer and process
  behavior; unavailable platform lanes require an explicit documented safe
  fallback or feature exemption, not assumed parity. Checks preserve
  headlessness, caller-Job independence,
  graceful cutover, ownership and rollback; shared-runtime concurrency includes
  two installers and two independent marketplace installations.
- [ ] Index integration preserves authenticated routing, HTTP/SSE/MCP contracts,
  background jobs, retrieval semantics and durable state across upgrades.
- [ ] Fresh adoption with no runtime-profile overrides proves the shared
  baseline, CPU closure and automatic governed fallback; unchanged warm reuse
  makes no resolver/feed call. Exercise stricter policy, supplied revocation,
  retained accelerators, unprofiled migration and all-modes-blocked retention.
- [x] Every slice records applicable unit, integration, clean-room and available
  live-venue evidence, or a specific reason a tier is unavailable.
- [x] Measurement separates compressed downloads, unpacked logical files,
  hardlink-aware physical allocation, model/cache data and retained version slots.
- [ ] Publication checks, changefiles, review, merge and release attribution are
  satisfied; rollout authorization and its completion owner are explicit.

## Proposal

Approved and merged in #6235. Implementation proceeds in reviewed slices.
Shared reuse is a design investigation; neither one global ZDD process nor one
shared mutable venv is presumed acceptable.

### Current execution boundary

The delivered dependency reductions, helper investigation and compatibility
boundary have cleared review. This is not approval to enable a production
artifact fast path. The remaining engine-profile application (#6737) depends on
#4876's authenticated authority integration and actual permitted exact closures.
Release-profile publication is required for artifact-mode acceptance, not a
prerequisite for the independently verified source mode approved in #7066.
The selection contract's design merged in #6795; actual approval adapters,
feed-compatible closures and Phase 3 consumer admission remain open. Do not
invent a substitute resolver here. Held construction merged in #6598; that is
not profile admission or proof of this campaign's remaining native acceptance.

The existing owners have the concrete acceptance requests; no reply or accepted
transfer is assumed. To drive rather than merely wait on that prerequisite,
this campaign contributes the bounded
[exact-profile contract](../governed-python-artifact-promotion/exact-profile-contract.md)
to #4876's existing authority design. The inert exact record and pure selection
API merged in #7044, not approval-source integration, a loader, publication or
acquisition. #6941 retains the separate contributor-owned
candidate-input generator. Native/standalone queue, schema, canary, warm-engine and
Darwin acceptance remain with #5768. Apply the reviewed profile contract under
#6737, composing #6598 construction and #6707 admission rather than overlapping
their open implementations. The effort and retained runtime/state stay open.

The next stretch follows the operator-approved
[zero-configuration default](zero-configuration-defaults.md). A harness-supplied
policy reaches the same authority seam for release and source modes; hand-built
adopter approval files are not the default integration. The amended intent and
plan merged in #7066. The next implementation boundary is the authenticated
adopting-policy/source adapter and its actual governed closure, not another
profile-record API or a competing admission constructor.

## Journal

### 2026-10-11 - Audit and operator direction
- Preserved the dependency audit as motivation and captured the operator's
  explicit Textual, index-split and installer de-vendoring priorities.
- Claimed #6234 after searching existing work; retained #5768, #5873, #4876 and
  #6143 as neighboring owners rather than creating duplicate implementations.
- Created the proposal and validation gate. Private deployment-specific
  motivation and the full operator request are recorded only downstream.

### 2026-10-12 - Proposal review clarification
- Corrected vision navigation and reconciled helper reuse with standalone
  runtime ownership: each consuming installer must secure a durable compatible
  dependency and its reference lifetime, not borrow a sibling's private venv.
- Reuse remains an unimplemented investigation; reality-pattern changes will
  accompany a separately reviewed design before any runtime migration.

### 2026-10-12 - Reviewed proposal and first implementation slice
- #6235 merged after approval and green CI; the worktree advanced onto the
  reviewed `dev` proposal before implementation.
- Began Phase 2: remove unused Textual and declare `packaging` directly to
  preserve monotonic version reconciliation, with an isolated no-UI import
  regression and dependency-contract check.
- Refreshed the execution census: 40 canonical pyprojects, 36 external base
  declarations, 16 optional declarations and 25 external names. The historic
  audit remains tied to its original snapshot rather than silently rewritten.
- Phase 2 targeted validation: 144 tests passed, including actual isolated CLI
  and Manager-process handoff with UI imports rejected; install contract reports
  zero violations and touched Python passes focused lint.
- The fresh managed test environment resolved without Textual/Rich; Pygments is
  present only through the test runner's pytest dependency. Docker clean-room
  execution is unavailable because no daemon is listening on this host.
  Native Windows evidence is local; Linux runs in PR CI. No macOS lane or live
  runtime rollout is claimed for this declaration-only slice.

### 2026-10-12 - Textual slice merged
- #6293 merged to `dev` as `cd7f0f939` after a current-head approving review
  and successful CI run `38250963792`, including Linux guard execution and the
  Windows launch lane. Version comparison and the actual Manager process boundary
  remain intact without Textual. Release promotion is tracked separately; a
  `dev` merge is not a production rollout or proof of publication to `main`.
- Review added normalized-distribution guards for equivalent UI package spellings.
  The final eight focused tests pass. Dependency comments reflect required
  `packaging` while retaining existing defensive damaged-runtime behavior.
- The docstring correction briefly exceeded the shrink-only module ceiling;
  it was condensed rather than widening the baseline or bypassing CI.

### 2026-10-12 - Index dependency boundary implementation
- Implemented the native base-only primary CLI and required healthy host sibling
  without duplicating complete service-cell or explicit-source native runtimes.
  Hosted query/index/worker commands preserve original arguments and exit codes;
  missing host dependencies are explicit errors. No production activation occurred.
- Full contained Windows suite passed: 789 tests, 97 platform/explicit-tier skips.
  Subsequent headless-command and lexical-path normalization checks passed:
  43 tests, one POSIX-only symlink skip. Both installer parsers, focused Python
  lint, headless-launch and installation-contract guards pass.
- Real fresh base installation contains no store/server/model packages and
  refuses hosted startup honestly. The actual PowerShell sibling provisioner
  installed a complete, origin-checked host environment beside it without
  starting a daemon, registering supervision or publishing an active version.
- RECORD-accounted external logical files measured 2.46 MiB for the unseeded
  development client profile and 508.85 MiB for the signed native host sibling.
  The latter includes pip/setuptools; versions and seeding differ, so these are
  profile evidence, not physical reclaim or a universal before/after estimate.
  LanceDB 0.39.0 contributes 314.31 MiB, chiefly its 312.02 MiB native extension.
- Docker clean-room and local Linux/macOS execution remain unavailable.
  Available Linux validation belongs to PR CI; existing live canary/warm-engine
  rollout remains with the standalone-service owner and was not performed.
- The first Linux CI run exposed eight stale POSIX test-harness expectations:
  a missing dropin fixture, an optional-server success assertion and an overly
  broad procutil extraction. Corrected those harnesses to exercise the required
  host package failure before activation; Linux execution of the corrections
  remains a gate rather than being inferred from Windows skips.

### 2026-10-14 - Merge reconciliation and next slice
- #6317 merged to `dev` as `a0869fbae` on 2026-10-13 after the POSIX fixture
  corrections. Its final Linux index suite, Windows checks and required PR gate
  all passed. The PR description now records the graceful-cutover assessment.
- Marked the delivered client/host boundary and duplicate-install removal items
  complete. The broader native/state-preservation validation remains open:
  passing contained checks are not authorized live rollout or macOS evidence.
- The Textual slice includes the dependency-minimal regression, docs and patch
  changefile in merged #6293. Its remaining "ship" checkbox awaits release
  attribution rather than treating a `dev` merge as a release.
- Prepared the bounded two-consumer helper experiment for review. It does not
  introduce a shared runtime or close the compatibility/lifecycle design gate.
  Next implementation slice: isolate legacy stdio MCP dependencies, retaining
  the existing CLI bridge and explicit opt-in legacy entry point.

### 2026-10-14 - Helper evidence merged
- #6643 merged to `dev` as `9015d09d8` after approval and green CI.
  Review corrected assertion-elision under optimized Python; four process
  contract tests now pass under `-O`. This closes publication of the bounded
  probe, not installation deduplication, auth isolation or shared lifecycle work.
- The full Phase 5 design gate remains open. Proceeding with the already
  planned optional dependency reduction before choosing a reuse mechanism.

### 2026-10-14 - Explicit legacy stdio MCP profile
- Moved the MCP SDK requirement out of `[store]` into `[stdio-mcp]`; native
  `[store,server]` installers do not request it. The normal agent read CLI is
  unchanged. The legacy adapter is HTTP-client-only and runs in the explicitly
  opted-in invoking runtime, not a host sibling that no longer installs its SDK.
- Full contained Windows validation passed: 857 tests, 180 explicit/platform
  skips. The suite includes a real `agent-index mcp` initialize/tools-list
  handshake, plus role-parametrized no-host-dispatch and manifest checks.
- A fresh 12-package base profile has no MCP SDK, NumPy or FastAPI and refuses
  `mcp` with exit 2 and actionable opt-in guidance. Adding only `[stdio-mcp]`
  exposes all five real SDK tools and completes the real stdio handshake without
  NumPy or FastAPI. The SDK has its own transitive HTTP dependencies; the extra
  is not described as dependency-free.
- Focused Python lint and the 13-plugin installer contract pass. Linux behavior
  remains subject to PR CI; no local macOS lane or Docker daemon is available.
  No managed runtime slot, service, model or production endpoint was changed.

### 2026-10-14 - Optional MCP slice merged
- #6658 merged to `dev` as `0976e89aa` after a current-head approval and green
  Linux/Windows checks. Review also removed MCP from both launcher provisioning
  allowlists, aligned the solo clean-room witness with honest SDK absence, and
  declared the Python 3.10 test backport.
- Linux CI exposed premature EOF in a pipelined protocol fixture. The final
  test uses the actual SDK client to await initialize and list-tools responses
  before closing its caller-owned transport. No sleep or retry hides the race.
- This completes the explicit optional-MCP implementation item. Release/rollout
  and broader native-state validation remain separate outstanding obligations.

### 2026-10-14 - Bounded statistics implementation
- Confirmed Textual removal in released `main` snapshot `f327cf447` (#6642):
  required `packaging`, absent Textual and the normalized UI dependency guard
  are all present. Phase 2 publication is complete; this does not prove local
  installation or authorize runtime rollout.
- Replaced full-table pandas materialization with a source/language-only Arrow
  scan requesting 1,024-row batches. Histograms omit nulls, retain empty strings
  and preserve the existing empty-store shape. Count and mid-scan failures now
  propagate rather than manufacturing zero or partial success.
- A real LanceDB fixture with 2,502 rows proves complete counts beyond the
  default query limit and across multiple batches without `to_pandas`.
  Structural coverage pins projection and batch size; failure tests reject both
  count errors and partial scans.
- The full contained Windows suite passed: 858 tests, 180 platform/explicit-tier
  skips. Focused content/statistics checks passed 16 tests. The implementation
  stays within the existing shrink-only module ceiling; no baseline was widened.
- This changes status-query execution, not storage layout or service ownership.
  No fresh installer, production endpoint, embedding model or live deployment
  was exercised. Linux validation remains a PR CI obligation.

### 2026-10-14 - Bounded statistics merged and next dependency slice
- #6686 merged to `dev` as `cbbef0837` after current-head approval and complete
  CI, including the actual Linux index suite. Marked bounded aggregation done.
  Release promotion and any authorized deployment remain separate.
- Next: make restricted-SSH Paramiko provisioning genuinely explicit while
  preserving existing configured provider-exec capabilities and update behavior.
  Inspect current installer/config profile ownership before selecting a mechanism;
  no new mutable shared runtime or machine-global default is presumed.

### 2026-10-14 - Persistent optional restricted-SSH profiles
- Chose a persistent installation profile rather than a transient environment
  switch or a second always-on helper. This closes the reviewed
  `capability-sized-runtime-dependencies` intent without extending it.
- New Containers installations default to base. Explicit provider-exec selection
  persists across updates; an existing unprofiled selected runtime with Paramiko
  preserves that capability. Neither incidental repository configuration nor a
  shared mutable environment selects the dependency closure.
- Both native installers use one stdlib profile coordinator with canonical
  source admission, construction/retention leases, immutable completion and
  marker activation. Slots bind package version plus profile. A failed requested
  extra never silently activates base; completed slots are reused, not rewritten.
- Windows contained coverage: 774 passed, 20 skipped. Actual isolated first-touch
  installs proved base and provider-exec, the whole PowerShell installer, ordinary
  profile retention, base rollback, real CLI dispatch and plain-pip local-library
  resolution. The tests cover source conflicts, failure retention, legacy
  capability migration, bounded diagnostics and current-profile bootstrap.
- Same-interpreter fresh package-file measurements: base 18 distributions /
  23.29 MiB versus provider-exec 25 / 36.30 MiB: 7 distributions and 13.01 MiB
  logical recorded-file delta. Both include pip/setuptools. These are not full
  venv, compressed-download or hardlink-aware physical-allocation measurements.
- Linux shell execution remains assigned to PR CI. No Docker daemon or macOS
  lane is available locally; the isolated fresh-root checks exercise first touch
  without modifying any deployed installation. No production rollout, listener,
  container, lease or service activation is part of this slice.
- Publication, Linux CI and review remain pending; the Plan item is not yet
  claimed complete.

### 2026-10-14 - Profile review corrections and engine ownership
- #6735 opened for the persistent profile slice. Review identified relative-root
  interpretation, Windows PowerShell's unavailable-interpreter stderr handling
  and fallback ordering of profile-qualified versions. Added focused regressions
  and corrected all three boundaries rather than widening module ceilings.
- Canonical Python/PowerShell/POSIX recovery now compares package versions before
  profile suffixes. Synchronized every primitive/resolver consumer and supplied
  changefiles for all 13 affected runtime plugins. Focused owning-suite coverage:
  Containers 34 passed / 7 skipped, Bridge 126 / 4, Codespaces 9 / 8.
- The native staging smoke check also exposed a receipt deadline shorter than
  the cold parent startup plus its child's 20-second watchdog. The receipt wait
  now permits 30 seconds while still rejecting early child exit immediately;
  18 staging/watchdog tests passed locally, one platform-specific skip.
- Filed #6737 for complete engine CPU/accelerator dependency profiles, preserving
  #4876's exact authority-owned lock/profile decision and #6598's seasoning and
  construction ownership. The current engine's default-install/torch-only-swap
  assumption is not a safe substitute for an approved compatible closure.

### 2026-10-14 - Native staging and bounded CI follow-through
- The next review identified caller-relative roots crossing marketplace
  self-staging and a contradictory profiled development-version fixture.
  Normalize and forward installation roots before either shell changes CWD;
  a native staged-child regression preserves the root, profile, action and
  force flag without provisioning a live runtime. Both recovery parameters now
  compare development versions before testing the final release.
- Windows CI then exhausted the fleet lane's 120-second budget in the
  production-window replay test: over 1,000 independent fully synchronous
  SQLite transactions. The exact unmodified selection passed locally in
  61.47 seconds, with that test taking 10.15 seconds; hosted-runner progress
  stopped at the same boundary. Required CI now runs the identical retention
  assertions with a four-event window; the full 1,024-event parameter remains
  in unfiltered and explicit manual validation, with an appropriate full-suite
  budget. Production retention, durability and required CI timeouts are unchanged.
- The required fleet selection passed 597 tests / 6 skips in 55.11 seconds
  under its unchanged 120-second containment budget. The complete suite,
  including production-window retention and the full artifact matrix, passed
  605 / 6 in 69.78 seconds. Workflow selection contracts: 17 passed.
  Containers profile/dependency/transaction coverage: 34 / 8; Codespaces
  recovery: 8 / 8. Focused lint and the install contract pass.
- These are candidate checks, not a merge, released payload or production
  rollout. Current-head review and hosted CI remain required.

### 2026-10-14 - Immutable helper ownership discovered during measurement
- Two-consumer closure measurement exposed an installer boundary that unit
  command mocks had not exercised: uv honored development editable-source
  declarations even when all canonical library paths were explicit. Those
  helpers could resolve outside a supposedly immutable runtime slot.
- Profile construction now disables uv source remapping after explicitly
  supplying the admitted local libraries. Health rejects any Containers/helper
  module outside the selected slot. A real isolated Python regression proves
  that an editable `.pth` escape fails rather than activating a partial runtime.
- Targeted profile/dependency/transaction coverage: 36 passed / 8 skipped;
  module ceilings, focused lint and the install contract pass. Both profiles
  were also freshly provisioned with the registered, signed Windows Python
  3.11.9 interpreter; both copied runtime interpreters retain valid signatures,
  own every helper and execute the actual CLI outside the checkout.
- This supersedes the earlier development-source distribution-record census:
  the corrected self-contained seeded base has 11 distributions / 25,165,401
  logical RECORD bytes; provider-exec has 18 / 38,810,561. The difference remains
  7 distributions / 13,645,160 bytes (13.01 MiB). The full runtime trees allocate
  29,044,088 and 45,901,048 NTFS file-stream bytes respectively on this test host;
  filesystem metadata, shared interpreter and retained production slots are
  excluded. No live rollout is claimed.

### 2026-10-14 - Required Windows reconciliation collection bound
- The approved profile head could not merge because the actual required CI run
  timed out in the reconciliation wrapper, despite metadata checks reporting
  success. The merge guard correctly refused the missing required PR gate.
- Reproduced the exact selection locally: all 18 checks pass, but collecting
  10,175 items for those checks takes the suite to 145.09 seconds. Each native
  policy/pointer case still performs both real helper calls and whole-profile
  byte/mtime snapshots; this is aggregate collection/cold-start cost, not a
  diagnosed helper deadlock.
- Added explicit owned test-file selection to the contained plugin runner.
  The required lane now collects the two owning files, preserving all 18 checks
  and the existing 120-second budget rather than skipping policy cases or
  widening the gate. The actual focused native run passed in 102.84 seconds.
- Runner selection/admission and all directly dependent CI scope contracts:
  122 passed / one unavailable-symlink skip. Focused lint and source module
  ceilings pass. Fresh hosted CI and review remain required before merge.

### 2026-10-14 - Persistent profile slice merged; next measured reduction
- #6735 merged to `dev` as `b54361f6f` after an exact-head approving review and
  complete successful required CI run `38637358082`, including both native
  runner lanes, standalone services and all affected plugin suites. Restricted
  SSH provisioning is now explicitly opt-in with durable compatibility.
- Retained the continuing effort worktree and pulled forward to the merged
  contribution branch. No production update, listener, model or corpus mutation
  occurred. Promotion and an owner-authorized rollout remain separate.
- Shared helper measurements were supplied to the existing artifact owner
  (#4876 / #6697); standalone activation, schema/queue rollback, Darwin and warm
  engine acceptance remain with #5768. No parallel artifact or service owner
  was introduced.
- Next implement the measured Windows Containers reduction: when uv performs
  installation, create signed stdlib venv copies without pip/setuptools seeding.
  Ordinary-pip fallback still receives its required seed packages. The isolated
  signed-interpreter proof already preserves the real base CLI with nine
  runtime distributions and 3,225,176 allocated file-stream bytes, versus
  29,044,088 for the seeded profile.

### 2026-10-14 - Conditional unseeded Windows construction
- Implemented the narrow Containers continuation: signed Windows stdlib copies
  receive `--without-pip` only when uv will install the admitted packages.
  The ordinary-pip constructor remains seeded; no package is removed from a
  completed runtime and no shared mutable environment is introduced.
- Full contained Windows coverage passed 781 tests / 21 skips. Native fresh
  base and provider-exec construction passed with 9 / 16 distributions, without
  pip or setuptools; all copied `python.exe` and `pythonw.exe` signatures remain
  valid. Both actual CLIs, retained provider intent and immutable base rollback
  pass outside the checkout.
- The candidate base records 2,265,172 logical package bytes / 3,248,664 allocated
  runtime file-stream bytes; provider-exec records 15,903,843 / 20,141,176.
  The real no-uv fallback also constructs and runs successfully with its 11
  seeded distributions and functioning `python -m pip`. These are isolated
  source-profile observations, not production storage reclaim or rollout.
- Focused lint, install contract and module ceilings pass. Linux/native
  platform coverage, publication and review remain assigned to the next gate.

### 2026-10-14 - Unseeded slice merged and helper decision prepared
- #6766 merged to `dev` as `5d08c74d7` after Copilot's exact-head approving
  review and complete successful required CI run `38641865532`. Native Windows
  and Linux runner checks, Containers and the owning harness suite passed.
  The first review wait expired without a verdict; an explicit re-request
  produced the approval. No gate was bypassed.
- Resolved the remaining syntax/seed decision: retain the five existing host
  syntax-grammar defaults (5,390,318 logical / 5,639,720 allocated file-stream
  bytes, about one percent of the measured host closure). Another profile is
  not justified by that cost; no parsing-quality claim is made. Windows
  unseeded construction is now conditional on uv, preserving signed copies,
  ordinary-pip fallback and completed immutable slots.
- Prepared the [helper closure evidence](helper-closure-evidence.md), its
  reproducible probes and the compatibility/lifecycle decision table for
  review. Preserve independent venvs/processes; prefer immutable artifact/file
  reuse under #4876 rather than a new helper-runtime owner or ZDD broker.
  This does not approve a production fast path or transfer an unresolved item.
- The campaign remains Active. Next secure review of this evidence/design,
  reconcile the remaining engine/standalone-service acceptance with their
  existing owners and publish the final profile/validation matrix. No
  production runtime update or warm-engine/corpus mutation occurred.
- Consolidated the [profile/validation matrix](profile-results.md) for that
  same evidence review. Measurement and retirement guards remain effective
  under optimized Python; all eleven retained-probe controls pass with `-O`.
  The artifact owner has been asked to acknowledge the existing acceptance
  boundary and name the exact approved profile contract needed by #6737.
  Pending replies and native/service gates remain open, not completed by
  relabeling them as another owner's work.
- Evidence review identified that in-runtime metadata alone could conceal a
  `.pth` helper import outside the measured environment. Both measurement
  lanes now check required first-party module origins and refuse escaping
  RECORD entries. Real disposable-venv regressions cover both failures;
  the original 9/30 source environments and surviving 30-package artifact
  environment pass the stronger checks. The retired first artifact consumer
  was not reconstructed merely to claim a new installation measurement.

### 2026-10-14 - Helper decision and profile matrix merged
- #6767 merged to `dev` as `9c5ce3bd4` after exact-head Copilot approval and
  successful complete required CI run `38648816562`. The import-origin finding
  is fixed across both measurement lanes and the survivor check; its thread
  is resolved. The fresh review, not the author's own reply review, supplied
  approval. Required CI completed normally before the authorized self-merge.
- Re-read this effort after merge and closed the delivered investigation,
  design-boundary review, measured-profile publication and corresponding
  changed-code/process/measurement records. The artifact implementation,
  engine profile, native/service, platform and release/rollout gates remain
  open. No unacknowledged owner transfer was used to clear them.
- The accepted choice retains separate owned venvs/processes and prefers
  immutable helper artifacts under #4876. Reject the measured routing-read
  broker; do not create a reusable helper runtime without an actual matching
  closure and a measured end-to-end benefit.
- Confirmed the remaining dependency in the artifact owner's canonical Plan:
  exact authority-approved profile design/publication and consumer admission
  are still unchecked. Requested its selected-contract/acceptance response
  before applying CPU/accelerator profiles under #6737; no response is
  presumed. The campaign remains Active at that explicit external gate.

### 2026-10-15 - Driving the authority-profile prerequisite
- Resumed upstream work after explicit direction to drive blockers. The source
  worktree advanced to current `dev`; #6697 is now merged, while its new-trusted-
  workflow publication proof is blocked by the existing #6551 PID-admission
  repair. Preserve that original repair rather than creating a competing PR.
- Reserved a proposal-only contribution to #4876's unchecked exact-profile
  contract and inspected its schema-3 manifest/build-toolchain prior art.
  Prepared the linked authority/selection/closure/fallback and engine-consumer
  contract for review. It names concrete acceptance and composes the still-open
  #6598 construction and #6707 admission owners.
- Native context management preserves this session's responsibility. No manual
  handoff, profile implementation, engine/model download or rollout is part of
  this proposal.

### 2026-10-16 - Reviewed exact-profile prerequisite and bounded implementation
- #6795 merged to `dev` as `40084a99c` after current-head Copilot approval
  `5527977396` and successful required CI `38680105914`. The review corrected
  the blocked-artifact case: authority-selected source fallback may use a
  distinct admissible closure, never bypass a blocked dependency or an
  integrity/provenance failure.
- Re-read both owning efforts and advanced this retained worktree through
  managed post-merge reconciliation. #6598 has since merged; #6707 and #6551
  retain their open scopes. #6941's candidate-input contribution is separate
  and is neither modified nor superseded here.
- Implemented the reviewed inert record/selection slice under #4876. Full wheel
  graph, extras, markers, targets and whole-record approval references are
  validated without downloading or installing dependencies. Actual authority
  adapters, inspected-wheel admission and native engine application remain
  open. No production rollout, model or corpus mutation occurred.
- The existing wired installation-contract suite passed 171 cases with 14
  unavailable native-POSIX skips on Windows. The final 53 exact-profile cases include
  an actual fresh Python process using the API without creating a runtime.
  The focused cases also run under optimized Python with explicit production
  refusal checks, not removable production assertions.
  This is not a released lock, fresh installer or acquisition/admission proof.
  Hosted current-head checks and normal review remain required.
- Review corrected optional Requires-Python handling: an absent wheel header
  has an empty specifier, not an invented minimum. Empty values now remain
  representable while malformed or untrimmed metadata still refuses.

### 2026-10-16 - Inert exact-profile record merged
- #7044 merged to `dev` as `8f144844d` after current-head approval
  `5554395793`, successful CI `38974962162` and all 45 actual check contexts
  completed. Linux/Windows service and native Windows/macOS construction
  contexts passed; these regression checks do not prove a profile integration
  that this inert slice does not enable.
- The post-push review wait timed out after the full 600-second window.
  Explicit re-request produced the approving review without a dummy commit or
  merge bypass. The optional Requires-Python finding is fixed and resolved.
- Re-read the canonical completion gates and reconciled the retained worktree.
  Closed only the inert record/selection item. Authority-source adapters,
  real permitted exact CPU/accelerator closures, inspected-byte admission,
  engine constructors and this campaign's remaining native/service acceptance
  are still open; the effort remains Active. No production rollout occurred.

### 2026-10-16 - Complete baseline rather than per-user approval setup
- The operator approved a complete harness-owned default on the assumption
  that most users never edit runtime configuration, then directed updating and
  driving the vision and effort. Captured the literal direction above and the
  approved decision separately; no new rollout or package-feed permission is
  inferred.
- Amended Runtime Assembly and engine intent plus the linked authority,
  resolution, integration and fresh-adopter validation plan. Keep one canonical
  campaign and the existing construction/admission/input-generator owners.
  The inert API's caller-provided approval map is not an implemented default;
  ordinary users must not have to construct that map themselves.

### 2026-10-17 - Zero-configuration plan reviewed and merged
- #7066 merged to `dev` as `1d18d3a05` after reduced current-head Copilot review and
  successful CI `38986244874`; all 42 actual check contexts completed without
  failures. The earlier approving review found a conflicting automatic-GPU
  definition; that definition now consistently follows authority capability
  policy. The final automated review could not run its full analysis because a
  runner was unavailable before its timeout. Its reduced review completed with
  only optional wording suggestions, considered and explained on their resolved
  threads; there were no unresolved blocking findings.
- Re-read the canonical effort and default sub-plan after merge. Closed only
  the ordered review gate and transitioned this retained worktree from Phase 5
  (helper investigation) back to the still-open Phase 4 engine-profile work.
  This does not reopen completed helper investigation. The parent campaign
  remains Active; no implementation, platform
  acceptance, accepted owner transfer or production rollout is inferred.
- Continued the authority integration trace: the existing shared
  `installer_dependency_policy.py` owns configured UV resolution, stronger
  seasoning and private retained input/configuration comparison, but has no
  exact-profile approval-source adapter. The record API remains repository-only.
  Existing promotion validators bind first-party source/manifest/wheel content;
  they do not by themselves authorize a consumer runtime closure.
- Keep #6941's contributor-owned candidate inputs and #6707's constructor/
  admission work intact. Establish a disjoint authenticated-policy and
  exact-closure integration contract with those existing owners before native
  application under #6737. Do not turn the missing contract into a self-approved
  JSON loader, replace the original drafts, or make source mode wait for
  artifact publication.
- The operator selected "Keep coordinating with the existing owner; leave its
  admission implementation untouched" rather than taking over #6707. Requested
  the explicit adapter handoff on the original PR. Its published source
  codename did not resolve through the available managed-worktree lookup;
  that is not evidence of abandonment or permission to create a replacement.
  Native integration remains blocked on an accepted owner contract, with
  responsibility retained here and no completed transfer claimed.
