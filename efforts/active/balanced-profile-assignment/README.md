# Balanced Profile Assignment

- **Slug:** `balanced-profile-assignment`
- **Repo:** copilot-extensions
- **Branch(es):** Serialized per-phase PR worktrees to `main`
- **Created:** 2026-09-01
- **Status:** Draft
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

- [ ] Add a default-off profile-assignment configuration block over named
  `copilot_profiles`. Only user-owned global/per-project config can arm it;
  committed repository config can define a template or narrow an armed pool.
- [ ] Validate unique policy name, mode, non-empty user-defined profile pool,
  optional opaque assignment labels, and eligible launch classes.
- [ ] Define per-installation balanced shuffled-bag state with deterministic
  seed/generation/position and atomic updates under concurrent launches.
- [ ] Generate a durable launch-generation token before the draw; key the
  pending assignment by that token and bind it to the real Copilot session ID
  at registration.
- [ ] A failed/unregistered launch consumes its recorded bag position and
  transitions to an abandoned outcome after a bounded expiry; compact terminal
  pending records so they cannot grow without bound.

### Phase 2 — Integrate session launch lifecycle

- [ ] Select before constructing argv for an eligible new session and append the
  chosen profile exactly as manual selection does.
- [ ] Preserve assignment across launch retry and ordinary resume; never redraw
  for the same launch/session generation.
- [ ] Make `handoff-cutover` create a successor generation through the same
  assignment primitive.
- [ ] Hard-exclude explicit `--profile`, recovery, emergency, and system
  launches. Configuration cannot opt those classes into assignment.

### Phase 3 — Persist and expose assignment

- [ ] Persist policy, opaque assignment label, selected profile, bag generation
  and position, launch-generation token, assignment timestamp, disposition
  (`pending|bound|abandoned`), and bound session ID.
- [ ] Expose assignment through machine-readable record/status output without
  adding benchmark/analytics semantics.
- [ ] Bound assignment history consistently with the existing session lifecycle
  ledger and preserve backward-compatible record loading.

### Phase 4 — Document, version, and release

- [ ] Document global/user-local/per-repository configuration, precedence,
  manual override, retry/resume/handoff behavior, and default-off safety.
- [ ] Add release notes and examples using generic profile names and synthetic
  models.
- [ ] Bump agent-worktrees versions in plugin, package, and marketplace files.
- [ ] Land through the repository PR flow and deploy through the unified update
  command.

## Validation Plan

- [ ] A six-profile bag emits every profile exactly once per generation before
  reshuffling, with deterministic tests for a fixed seed.
- [ ] Concurrent selectors serialize state without duplicate/skipped positions
  or torn assignment records.
- [ ] A launch retry reuses its pending assignment.
- [ ] A never-bound launch becomes an abandoned recorded outcome after bounded
  expiry, consumes its original position, and is compacted by the retention
  policy without redrawing or unbounded growth.
- [ ] Ordinary resume reuses the bound session assignment.
- [ ] A handoff successor draws a new assignment and records predecessor/
  successor linkage without changing default handoff behavior when unarmed.
- [ ] Explicit profile, recovery, emergency/system, missing-profile, malformed
  config, and unarmed cases retain current behavior and cannot be opted in by
  repository config.
- [ ] Config precedence works for global, knowledge-overlay, machine-local, and
  repository sources on Windows and POSIX.
- [ ] Machine-readable output exposes assignment identity without requiring a
  sibling service or leaking private experiment semantics.
- [ ] Existing Picker profile cycling and `--profile` launch tests remain green.
- [ ] Records written before profile assignment existed continue to load
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
