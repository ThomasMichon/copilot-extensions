# Leased Daemon Instances

- **Slug:** `leased-daemon-instances`
- **Repo:** copilot-extensions
- **Branch(es):** Dedicated integration branch; reviewed proposal before source changes
- **Created:** 2026-10-23
- **Status:** Done
- **Completed:** 2026-10-25
- **Source:** #8720 (merged to `dev`; release and rollout are separate)
- **Vision:** `visions/plugin-services/README.md` - `attributed-daemon-instances`
- **Umbrella issue:** #8403

## Guiding Intent

Every shipped daemon launch has attributable instance custody, including
source-only, native-supervised and container entrypoints. Explicitly disposable
host-side test instances cannot become permanently orphaned merely because they
use independent state roots. Production election, cutover and recoverable-work
ownership remain with their existing lifecycle owners.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Integration owner | Plan, public coordination, contract integration, validation and PR stewardship | #8403 |
| Shared process owner | Common instance/lease/cleanup substrate and native bindings | Integration owner |
| Caller owner | Complete daemon-entrypoint census, caller migration and coverage guard | Integration owner |
| Installer owner | Normal install/update reconciliation adapters | Integration owner |

## Coordination

- **Topology:** One integration branch with non-overlapping source slices.
- **Host (owns PRs):** Integration owner.
- **Delegates:** Shared process, caller and installer owners; no independent
  commits, publication or deployment from delegated slices.
- **Handoff:** Return bounded source/test results to the integration owner;
  preserve original instance IDs and unresolved lifecycle effects.
- Public artifacts remain generic. No downstream-private runtime, session,
  machine, test-directory or incident identifiers are required.

## Context

The read-only census found shared Python launchers, Python-free native
launchers, raw detached paths, double-fork entrypoints, native supervisors,
standalone services and test fixtures. Instrumenting `spawn_daemon` alone would
leave bypasses. Existing root-local singleton and runtime-retention leases do
not provide cross-root disposable-instance discovery.

Existing lifetime, installation-cell, process-identity, construction, retention,
native-supervisor and cutover owners remain authoritative. Shared registration
must not require a running sibling service or a particular installation venue.
This extends the service-model vision and the lifecycle-supervision pattern;
it is not a change to the meaning of singleton supersession.

## Request

The operator proposed:

> Should we consider forcing those installs to be tracked somehow, annotated with "lease" markers, and then have them get garbage-collected by future normal installs?

After accepting the shared instance-lifetime design:

> Agree. Let's get this added consistency across all daemon launches

The operator confirmed the effort slug and these defaults:

> 10-minute renewable lease; 4-hour hard maximum
>
> Note that if an agent runs a test, and in the middle of the test does an ask_user tool call, it could be waiting for a day while the tests is still running in the background. If only hands-off tests make these then the 4 hour max lifetime is good

The shared design was agent-proposed and operator-accepted: distinguish
ephemeral instances from installed versions; retain owner and exact process
identity; limit deletion to declared disposable artifacts; preserve production
cutover; use installer reconciliation as a backstop rather than the primary
cleanup mechanism.

For Python-free Linux lease enforcement, the operator selected `prebuilt`:
ship the native helper with supported release artifacts and compile only during
explicit source builds. Ordinary production startup remains compiler-free.

## Plan

### Phase 1 - Reviewed contract

- [x] Claim #8403 and inventory shared, direct, native-supervised, standalone
  and test-fixture daemon launch paths without runtime effects.
- [x] Review and merge this proposal and its governing intent before source
  implementation.
- [x] Record the complete callable-scoped launch census as a checked contract,
  distinguishing residents, supervised service children, finite workers,
  caller-contained tools and venue-owned agent bodies.

### Phase 2 - Shared instance custody and finite test lifetime

- [x] Add one service-independent shared instance inventory outside disposable
  state roots. Reserve before detached creation; bind actual process-birth
  identity and startup admission before application work/readiness.
- [x] Cover direct/native-supervisor startup as well as launcher wrappers.
  Source provenance is supported without pretending a source launch is an
  installed production generation.
- [x] Track production instances without introducing a production expiry or
  replacing singleton, cutover, supervisor or recoverable-work authority.
- [x] Require explicit ephemeral-test ownership and purpose. Unattended tests
  default to a renewable 10-minute lease and 4-hour hard maximum.
- [x] Require an explicit pause-tolerant lifetime for interactive tests; do not
  silently apply the unattended maximum to a test waiting for human input.
  Renewal belongs to the test lifecycle owner, not agent-turn activity.
- [x] Enforce expiry independently of the launching command, including its
  abrupt disappearance. Preserve contained-test suppression and existing
  Windows outside-Job adoption/uncertain-outcome rules.
- [x] Reuse native birth identity and per-instance mutation custody. Avoid a
  second coordinator, cryptographic admission chain or capacity scheduler.

### Phase 3 - Complete caller and installer integration

- [x] Integrate all shipped resident/service startup paths and shared
  Python/PowerShell/Bash launchers, including managers, standalone services and
  explicit test fixtures. Preserve Python-free native launch capability.
- [x] Include the Linux kernel helper in supported release artifacts; compile
  explicitly for source builds, never implicitly on normal daemon startup.
- [x] Add a mechanical coverage guard that refuses new unclassified bypasses;
  comments or containment flags alone do not establish instance admission.
- [x] Add finite, explicit reconciliation to admitted normal install/update
  paths, including healthy/reuse returns. No destructive import, status,
  session-start or resolver sweep.
- [x] Reclaim only expired, explicitly disposable instances through the same
  cleanup owner: verify process identity, confirm stop, then settle exact owned
  runtime artifacts. Unknown custody, reused PIDs, live protected work, changed
  paths and unsafe filesystem indirection hold with diagnostics.
- [x] Preserve source checkouts, installed code slots, retained proof files,
  unrelated entries and unsettled cleanup evidence. Partial cleanup remains
  resumable under the original instance identity.
- [x] Update owner documentation, distribution/vendoring declarations and
  changefiles for actual affected consumers.

### Phase 4 - Qualification and completion

- [x] Run the validation plan, publish the source PR and resolve substantive
  review/CI findings through merge.
- [x] Update this effort from actual merged results; resolve every plan and
  validation item before setting Done and finalizing.
- [x] Report source/release/installed state separately. No runtime rollout is
  authorized by this implementation request.

## Validation Plan

- [x] Pure tests cover reservation/admission/renewal/expiry, production
  non-expiry, interactive-policy refusal, concurrent mutation, PID reuse,
  malformed/foreign records, safe paths and unknown-outcome holds.
- [x] Actual contained native-process tests prove discoverability across
  isolated roots, expiry after caller loss, exact-process stop, descendant
  settlement and protected/unrelated-instance preservation.
- [x] Real consumer checks cover fresh install, repeat install, healthy/reused
  runtime, update, missing registry, permission failure and interrupted cleanup
  without changing the selected production generation.
- [x] Shared fixtures exercise Python and Python-free native protocol parity.
- [x] Actual built-artifact tests find and execute the packaged native helper
  without a runtime compiler; source-build tests make compilation explicit.
- [x] Extend an appropriate clean-room scenario with synthetic inputs; record
  exact supported lanes and explicit reasons for unavailable ones.
- [x] Windows launch qualification retains zero visible windows/focus changes
  from a windowless parent and verifies caller-Job cleanup behavior.
- [x] Measure incremental launch/reconciliation work; no whole-machine process
  scan, synchronous export or new per-request helper service is introduced.
- [x] Run affected consumer tests, headless/coverage guards, installer contract,
  documentation checks and the repository's required change-scoped gates.

## Proposal

Proposal #8433 and implementation #8720 are merged. The shared ownership,
complete caller/installer integration and recorded validation plan are done.
The journal distinguishes actual supported-lane proof from unavailable
hardware, dependency and production-venue lanes; those are not claimed as
exercised. Release promotion and installed rollout are separate, and no
runtime rollout was authorized or performed.

## Journal

All original entries and qualification details are retained in dated sidecars,
newest first:

- [2026-10-25 - retirement and completion](journal/2026/10.25%20retirement-and-completion.md)
- [2026-10-24 - native and installer qualification](journal/2026/10.24%20native-and-installer-qualification.md)
- [2026-10-23 - design and initial integration](journal/2026/10.23%20design-and-initial-integration.md)
