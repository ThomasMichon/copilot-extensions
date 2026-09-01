# Balanced Profile Assignment

- **Slug:** `balanced-profile-assignment`
- **Repo:** copilot-extensions
- **Branch(es):** Serialized per-phase PR worktrees to `main`
- **Created:** 2026-09-01
- **Status:** Active
- **Vision:** `visions/plugins/agent-worktrees` — explicit, replayable session launch assignment; `visions/picker` — manual selection remains the default
- **Umbrella issue:** #1564
- **AI assistance:** Drafted with GitHub Copilot.

## Guiding Intent

Let an operator explicitly arm a named launch experiment that assigns eligible
new Copilot sessions from an existing profile pool using a balanced shuffled
bag. The assignment is durable and replayable: launch retry and ordinary resume
retain it, while a genuinely new session generation—including a handoff
successor—draws the next profile.

Keep this a generic agent-worktrees capability. Profiles continue to own the
actual backend arguments; the plugin knows only profile names, assignment
policy, opaque arm labels, and lifecycle identity. Manual profile selection
remains the default, and selection requires no coordinator or sibling plugin.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Maintainer | Design, implementation, tests, review, and release | This worktree / PR |

## Coordination

- **Topology:** One serialized implementation branch and PR.
- **Host (owns PRs):** Maintainer.
- **Delegates:** None initially.
- **Handoff:** The effort and #1564 are the public recovery points.

## Context

Agent-worktrees already supports named `copilot_profiles` from global,
knowledge-overlay, machine-local, and repository configuration. The Picker and
`--profile` select one profile and the launch planner appends its environment and
Copilot arguments. Worktree records already own session generations, lifecycle
heads, and handoff transitions; `handoff-cutover` already creates a successor in
the same worktree.

What is missing is an explicit assignment policy over those existing profiles.
Without durable assignment state, callers must select ad hoc, retries can redraw,
and downstream measurement must infer a model from mutable defaults.

## Request

Add an opt-in agent-worktrees configuration option that can be defined in
user-owned global or per-project configuration. Repository configuration may
publish a default-off policy template or narrow an already user-armed pool, but
can never arm assignment or introduce profiles absent from user-owned config.
When armed, every eligible new Copilot session selects a profile from the
acceptable set by “spinning the wheel.” Ordinary resume retains the selected
profile; handoff successors are new session generations and draw again.

## Plan

### Phase 1 — Specify configuration and assignment state

- [x] Add a default-off profile-assignment configuration block over named
  `copilot_profiles`. Only user-owned global/per-project config can arm it;
  committed repository config can define a template or narrow an armed pool.
- [x] Validate unique policy name, mode, non-empty user-defined profile pool,
  optional opaque assignment labels, and eligible launch classes.
- [x] Define per-installation balanced shuffled-bag state with deterministic
  seed/generation/position and atomic updates under concurrent launches.
- [x] Generate a durable launch-generation token before the draw; key the
  pending assignment by that token and bind it to the real Copilot session ID
  at registration. Retire the one-shot capability after bind/expiry and never
  copy it into public record or status metadata.
- [x] A failed/unregistered launch consumes its recorded bag position and
  transitions to an abandoned outcome after a bounded expiry; compact terminal
  pending records so they cannot grow without bound. Status/list and session
  lifecycle paths perform optional lazy maintenance even while disarmed.

### Phase 2 — Integrate session launch lifecycle

- [x] Select before constructing argv for an eligible new session and append the
  chosen profile exactly as manual selection does.
- [x] Preserve assignment across launch retry and ordinary resume; never redraw
  for the same launch/session generation.
- [x] Make `handoff-cutover` create a successor generation through the same
  assignment primitive and retain neutral predecessor-session linkage.
- [x] Hard-exclude explicit `--profile`, recovery, emergency, and system
  launches. Configuration cannot opt those classes into assignment.
- [x] Keep assignment allocation/binding/state maintenance non-load-bearing:
  corrupt/future/unreadable state and lock contention warn and fall back without
  stranding a launch, carved worktree, or registered session. Invalid armed
  configuration still fails before creation side effects.

### Phase 3 — Persist and expose assignment

- [x] Persist policy, opaque assignment label, selected profile, bag generation
  and position, internal launch-generation token, assignment/bind timestamps,
  disposition (`pending|bound|abandoned`), predecessor session when applicable,
  and bound successor session ID.
- [x] Expose assignment through machine-readable record/status output without
  adding benchmark/analytics semantics or exposing the launch token. Distinguish
  latest history from the current bound head-session assignment.
- [x] Bound assignment history consistently with the existing session lifecycle
  ledger and preserve backward-compatible record loading.

### Phase 4 — Document, version, and release

- [x] Document global/user-local/per-repository configuration, precedence,
  manual override, retry/resume/handoff behavior, and default-off safety.
- [x] Add release notes and examples using generic profile names and synthetic
  models.
- [x] Bump agent-worktrees versions in plugin, package, and marketplace files.
- [ ] Land through the repository PR flow and deploy through the unified update
  command.

## Validation Plan

- [x] A six-profile bag emits every profile exactly once per generation before
  reshuffling, with deterministic tests for a fixed seed.
- [x] Concurrent selectors serialize state without duplicate/skipped positions
  or torn assignment records.
- [x] A launch retry reuses its pending assignment.
- [x] A never-bound launch becomes an abandoned recorded outcome after bounded
  expiry, consumes its original position, and is compacted by the retention
  policy without redrawing or unbounded growth.
- [x] Ordinary resume reuses the bound session assignment.
- [x] Resume remains usable when a persisted profile is renamed or absent on
  the current machine, including after policy disarm or on a machine with a
  different profile set.
- [x] A handoff successor draws a new assignment and records predecessor/
  successor linkage without changing default handoff behavior when unarmed.
- [x] Explicit profile, recovery, emergency/system, missing-profile, malformed
  config, and unarmed cases retain current behavior and cannot be opted in by
  repository config.
- [x] Config precedence works for global, knowledge-overlay, machine-local, and
  repository sources on Windows and POSIX.
- [x] Machine-readable output exposes assignment identity without requiring a
  sibling service, leaking private experiment semantics, or exposing the
  launch capability.
- [x] Double/expired/foreign binds and assignment state/lock failures preserve
  core registration, activity logging, context emission, status setup, and
  worktree launch.
- [x] Existing Picker profile cycling and `--profile` launch tests remain green.
- [x] Records written before profile assignment existed continue to load
  byte-compatibly with no implied assignment.

## Proposal

Use a small assignment module with two responsibilities:

1. Parse/validate an opt-in policy that references existing profile names.
2. Under the agent-worktrees state lock, allocate or reuse a token-keyed
   launch-generation assignment from a local balanced bag and persist it before
   launch.

The launch planner consumes the resulting ordinary `CopilotProfile`; it does not
special-case models or effort levels. Registration binds the pending assignment
to the actual session ID. Resume resolves the bound assignment. Handoff cutover
requests a fresh successor launch generation when the policy is armed.

Arming is a user-owned power. Repository configuration can carry portable
defaults and restrictions, but never silently enable assignment or expand the
user's profile set.

The state is local-first by design. Multiple machines each maintain their own
bag; downstream systems may aggregate records, but selection never depends on a
shared coordinator.

## Journal

### 2026-09-01 — Kickoff

- Public coordination issue #1564 filed.
- Existing profile, launch, worktree-record, and handoff-cutover seams mapped.
- Effort and vision extension drafted before runtime implementation.

### 2026-09-01 — Implementation

- Added trust-aware `profile_assignment` config: user-owned global, knowledge,
  or machine-local/per-project config is the only arming authority; committed
  repository config can publish a template and intersect the user pool/lanes.
- Added a per-project atomic shuffled-bag ledger with deterministic
  seed/generation/position, token-keyed retry reuse, bounded pending expiry to
  abandoned outcomes, and bounded history.
- Extended worktree records with bounded assignment metadata and a monotonic
  revision so unrelated stale record writers preserve assignment state.
- Integrated eligible cold/new launches, ordinary resume replay,
  `handoff-cutover` successor draws, registration binding, and machine-readable
  worktree/session output. Explicit profiles, recovery, base, ACP/bridge,
  system, and delegated launches remain outside assignment.
- Documented configuration, lifecycle, record/status output, and the
  `1.5.3-dev701` release behavior with organization-neutral examples. The
  version advanced as concurrent upstream work consumed `1.5.3-dev699` and
  later `1.5.3-dev700`.

### 2026-09-01 — Validation

- Focused profile/config/tracking/launch/registration/handoff coverage passed,
  including a real cross-process `spawn` concurrency matrix and deterministic
  six-profile generations.
- The complete agent-worktrees suite passed after repairing three stale
  baseline hook/Picker test contracts encountered during validation: context
  contributors now leave the replaceable payload cwd, the aggregate-context
  cold-start budget test names the current contributor, PowerShell replay
  canonicalizes directory symlinks, and the resume-decision test double exposes
  the engine's current local-source helper.
- Ruff F/E9, version consistency and bump, install-contract, docs consistency,
  and generated payload-invocation guards passed.

### 2026-09-01 — Review hardening

- Made assignment binding and state maintenance explicitly non-load-bearing:
  lifecycle token mismatches, expired launches, unsupported/corrupt state, and
  lock contention emit bounded warnings while core registration and launch
  continue.
- Retired one-shot tokens after bind/expiry and removed them from worktree,
  status, list, and session metadata. Status now distinguishes the latest
  history entry from the current bound head-session assignment.
- Made missing/renamed-profile replay degrade to the ordinary profile path,
  preserved disarmed and cross-machine resume, and added predecessor-session
  linkage for handoff successors.
- Added the review failure matrix for double/expired/foreign bind, malformed
  arming, missing-profile replay, state corruption/future schema, lock timeout,
  post-carve fallback, redaction/current semantics, disarmed expiry, and handoff
  linkage. The focused assignment/config selection passes; full-suite and guard
  reruns remain the next validation step.

### 2026-09-01 — Review validation

- The lifecycle-focused selection passed with 133 tests.
- The complete agent-worktrees portfolio passed: 3,255 tests passed and 6
  platform-gated tests skipped.
- Ruff F/E9, version consistency, install contract, docs consistency, generated
  payload invocation, and diff whitespace guards passed.
- Agent-worktrees advanced to `1.5.3-dev701` with marketplace
  `1.7.5-dev725` after current `origin/main` consumed `1.5.3-dev700` /
  `1.7.5-dev724` during final validation.
- Status remains Active. PR publication, merge, and deployment are intentionally
  pending and are not part of this review-fix worktree pass.

### 2026-09-01 — Reviewed blocker corrections

- Partitioned assignment-policy parser errors by trust provenance. Malformed
  repository-only default-off templates are now non-load-bearing for create,
  resume, embody, and handoff launch paths, while malformed user-owned or
  effectively armed policy remains a pre-carve failure.
- Added strict finite, nonnegative, integral, 63-bit-bounded validation for
  allocator counters, assignment generation/position fields, and record-local
  assignment revisions. Corrupt JSON/YAML numeric state now takes the existing
  optional fallback path; overflow is also covered by allocation, maintenance,
  binding, and record-sync guards.
- Changed ledger compaction to retain every live pending assignment and trim
  only terminal `bound|abandoned` history. The ledger may temporarily exceed
  its history limit, then returns to the limit as entries bind or expire.

### 2026-09-01 — Reviewed blocker validation

- All 34 blocker-specific parser/launch, numeric-state allocation/registration,
  record-loading, and pending-compaction regressions passed.
- The complete agent-worktrees portfolio passed with 3,289 tests and 6
  platform-gated skips using `--test-timeout 180`. The default 30-second run
  and a 90-second rerun each exhausted their per-test budget in unchanged
  PowerShell-heavy replay tests while the shared host load was elevated; both
  exact tests passed independently, and no PowerShell child leak was present.
- Agent-worktrees guards passed with 18 tests and 1 platform-gated skip. Ruff
  F/E9, version consistency, install contract, docs consistency, generated
  payload invocation, and diff whitespace guards passed.
- Current `origin/main` still carries agent-worktrees `1.5.3-dev700` and
  marketplace `1.7.5-dev724`; the existing `1.5.3-dev701` /
  `1.7.5-dev725` bump remains the correct next version and was not advanced
  again.
- Status remains Active. PR publication, merge, and deployment remain
  intentionally out of scope for this worktree pass.

### 2026-09-01 — Final requested corrections

- Moved authoritative user-policy validation ahead of explicit-profile,
  recovery, ACP/bridge, system/delegated, and base-repo launch exclusions, and
  ahead of create/resume mutation. Repository-only malformed default-off
  templates remain non-load-bearing.
- Made terminal-history compaction mutation-aware. Pending retries now persist
  compaction even when no expiry occurred, every live pending assignment remains
  retained, missing state returns before the assignment lock, and unchanged
  maintenance no longer rewrites the ledger.
- Restored list/Picker fast paths: cache-only and coalesced cache hits skip
  assignment maintenance, never-armed projects take no assignment lock, and
  ordinary worktree rows expose only `current_profile_assignment`. Bounded
  history and the latest entry moved behind
  `list --json --profile-assignment-history`.

### 2026-09-01 — Final requested validation

- Focused assignment, list-cache, launch-preflight, and resume coverage passed
  with 150 tests.
- The complete agent-worktrees portfolio passed with 3,303 tests and 6
  platform-gated skips using `--test-timeout 180`.
- Agent-worktrees guards passed with 18 tests and 1 platform-gated skip. Ruff
  F/E9, version consistency, install contract, docs consistency, runbook
  references, generated payload invocation, marketplace-isolation baseline,
  and diff whitespace guards passed.
- Current `origin/main` remains at agent-worktrees `1.5.3-dev700` and
  marketplace `1.7.5-dev724`; the existing `1.5.3-dev701` /
  `1.7.5-dev725` versions remain consistent and were not advanced.
- Status remains Active. No PR, merge, or deployment was performed.

### 2026-09-01 — Final reviewed blocker corrections

- Separated the ordinary picker/launch profile from explicit-profile authority.
  Assignment exclusions, unassigned pre-feature resumes, unavailable persisted
  replay profiles, and optional allocator state/lock failures now retain the
  concrete default/manual profile, including its Copilot arguments and
  environment, while an explicit profile still bypasses assignment.
- Made record synchronization disposition-monotonic for one assignment identity.
  A delayed pending reflection can no longer overwrite an already bound or
  abandoned record view.

### 2026-09-01 — Final reviewed blocker validation

- The focused profile-assignment selection passed with 79 tests.
- The complete agent-worktrees portfolio passed with 3,307 tests and 6
  platform-gated skips using the runner's documented expanded sub-suite budget.
  The first full attempt reached 96% of one PowerShell-heavy sub-suite before
  its default 300-second wall-clock budget expired; the budgeted retry completed
  without assertion failures.
- Agent-worktrees guards passed with 18 tests and 1 platform-gated skip. Ruff
  F/E9, version consistency, install contract, docs consistency, runbook
  references, generated payload invocation, marketplace-isolation baseline,
  and diff-whitespace guards passed. The public-identifier check skipped
  because no forbidden-identifier inventory is configured on this host.
- Existing versions remain consistent at agent-worktrees `1.5.3-dev701` and
  marketplace `1.7.5-dev725`. No PR, merge, or deployment was performed.
