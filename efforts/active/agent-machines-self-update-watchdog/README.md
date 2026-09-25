# Agent Machines Self-Update Watchdog

- **Slug:** `agent-machines-self-update-watchdog`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-09-14
- **Status:** done-with-this-slice
- **Vision:** agent-fabric `unattended-tiered-self-convergence`
- **Umbrella issue:** [#2620](https://github.com/ThomasMichon/copilot-extensions/issues/2620)
- **Related efforts:** [`agent-machines-declarative-control-plane`](../agent-machines-declarative-control-plane/README.md)
  (the resource/reconcile engine this calls into), [`2026/09/03 unreachable-machine-maintenance`](../../2026/09/03%20unreachable-machine-maintenance/README.md)
  (the fallback for machines that cannot be reached at all — this effort is
  the default posture for machines that *can* be reached but have nothing
  driving them forward between sessions)
- **Design source:** private `dotfiles` proposal
  `efforts/active/dotfiles/mesh-self-update-watchdog/README.md` (not public;
  captures the motivating operator pain and private-mesh rollout plan — this
  effort implements only the generic, organization-neutral primitive)

## Guiding Intent

A reachable, logged-in machine should converge toward declared state on its
own schedule, without a live interactive Copilot session and without an
operator connecting in to drive it by hand. Reliability comes from the
platform's own scheduler, not from a fabric daemon whose own liveness would
just relocate the same problem one layer down.

## Context

`agent-machines restore --apply --all-projects` and
`agent-worktrees reconcile-plugins --apply --with-payload-refresh` already
exist and already work when invoked by hand. Nothing currently invokes them
unattended, on a cadence, durably, per machine — so a machine that isn't
actively being driven by an interactive session silently drifts until an
operator notices and connects in. `agent-dispatch`'s own installers already
establish the reference pattern for the one piece this is missing: attempt a
genuine elevated Windows Scheduled Task registration; if elevation isn't
available at that moment, tell the operator to re-run the installer once,
elevated, rather than silently degrading forever.

## Request

Add a first-class `agent-machines` subcommand family that performs two
independently-scheduled, independently-locked tiers of unattended
self-convergence, registered only when a declared config opts the machine in,
through the same one-time elevated convention `agent-dispatch` already uses,
with explicit reentrancy guarding and an inspectable last-run signal.

## Design decisions (resolving the proposal's open questions)

The private proposal deliberately left these open; resolving them here turns
the proposal into an implementable plan.

- **Tier 1 allow-list scope:** start with exactly one check —
  the `agent-ssh` dtssh host launcher process's own liveness (is the
  launcher itself running; if not, start it). Do not add the coordinator's
  logon auto-start liveness or any other candidate in the first slice —
  land the narrow tier, prove it stable, then propose additions as their
  own bounded follow-ups rather than growing Tier 1's blast radius up front.
- **Reentrancy design:** a named, tier-scoped OS mutex/lock file under
  `agent-machines`'s existing state directory, recording holder PID and a
  start timestamp. A lock is reclaimed only when its recorded PID is no
  longer a live process *and* age exceeds a fixed staleness bound (Tier 1:
  10 minutes; Tier 2: 3 hours — both generous multiples of each tier's own
  expected worst-case runtime). On reclaim, an orphaned process tree is left
  alone (not force-killed) to avoid compounding a partially-applied mutation
  with an unsafe kill; the reclaiming run simply proceeds and the stale
  tree's own eventual exit is a no-op. The two tiers use independent locks
  and are not mutually exclusive of each other by default — Tier 1's job
  (process liveness only) never mutates declarative state, so there is
  nothing for it to race with Tier 2's own reconcile.
- **Scheduled Task logon scope:** "run only when logged on," matching every
  other interactive-identity operation in this mesh (dtssh, the
  `agent-dispatch` coordinator/supervisor) — these operations need the
  operator's own credentials/tokens, not a service account.
- **Opt-in scope: declared config, not a bare command invocation.** Whether
  self-update is enabled at all — and per tier — is a declared setting
  resolved through the **same authority precedence** `agent-machines`
  already uses for every other resource: a state-repo-declared package can
  supply an organization/mesh-wide default (e.g. "opt in Tier 1 fleet-wide"),
  and an explicit local machine-level declaration overrides it (opt a
  specific machine in or out regardless of the shared default). This is
  deliberately modeled as a declarative resource in the existing control
  plane (`agent-machines-declarative-control-plane`) rather than a parallel
  bespoke config file — `plan`/`validate`/`restore` already resolve and
  report authority-selected desired state, and self-update's enabled/disabled
  flag per tier is just one more resource in that graph. `self-update
  install` still performs the actual one-time interactive elevation to
  register the Scheduled Task(s), but it first resolves this declared
  config and refuses to prompt for elevation at all if the resolved value is
  "not opted in" — config gates whether registration is even attempted;
  elevation remains the mechanism for the registration step itself once
  config says yes. If a later `restore --apply` observes the resolved config
  flipped to disabled, it removes the previously-registered Scheduled
  Task(s) as an ordinary drift-reconciliation apply (no elevation needed to
  delete a task the operator's own account registered).
- **Opt-in UX:** given the above, `agent-machines self-update install` is
  the one-time interactive command an operator runs after opting in via
  config (or during onboarding, once the shared default already opts them
  in) — it resolves config, attempts elevated registration, and prints an
  explicit "re-run this elevated" message on failure. No agent session ever
  attempts a silent/automatic elevation, and no session ever opts a machine
  in on the operator's behalf by editing local config unasked.
- **Tier 2 cadence:** daily. Twice-daily was considered but rejected for the
  first slice — daily already resolves the motivating pain (multi-day
  unattended drift) without doubling governed-feed network load and
  plugin-cutover risk before real-world usage data justifies a tighter
  cadence. Revisit if daily proves too coarse in practice.
- **Status marker placement:** reuse `agent-machines`'s existing status/plan
  output rather than adding a fourth file format — record last-attempt and
  last-success timestamps (per tier) alongside the machine's existing
  observed-state record, surfaced through the same status command operators
  already use to inspect a machine.
- **Non-Windows hosts:** out of scope for this slice (every current mesh
  machine is Windows per `machines.yaml`); the subcommand and lock design
  should not hard-code Windows-only assumptions where a systemd-timer /
  launchd equivalent could later slot into the same interface, but no POSIX
  scheduler integration ships now.

## Plan

### Phase 1 - Design finalization
- [x] Extend `visions/agent-fabric` with the `unattended-tiered-self-convergence`
  should-be.
- [x] Resolve the proposal's open questions (Tier 1 scope, reentrancy design,
  logon scope, opt-in UX, Tier 2 cadence, status placement) with explicit
  reasoning, above.
- [x] File the public tracking issue and this effort doc.

### Phase 2 - Tier implementation
- [x] Add a `self-update` resource type (per-tier enabled/disabled) to the
  declarative control plane, resolved through its existing authority
  precedence (state-repo-declared default, local machine-level override).
- [x] Add `agent-machines self-update run --tier watchdog` (Tier 1: dtssh
  launcher liveness check-and-start only; no state mutation beyond starting
  the launcher process).
- [x] Add `agent-machines self-update run --tier sweep` (Tier 2: fast-forward
  `dotfiles`/harness pull, `agent-worktrees reconcile-plugins --apply
  --with-payload-refresh`, `agent-machines restore --apply --all-projects`),
  reusing the existing live-session deferral guard for any disruptive step.
- [x] Both `run` entry points resolve the tier's config first and exit
  as a clean no-op if the resolved state is "not opted in" (covers a
  Scheduled Task that fires after an operator has since opted out but
  before the next `restore --apply` removed it).
- [x] Add the tier-scoped named lock (PID + timestamp, staleness-bounded
  reclaim per tier as designed above).
- [x] Record per-tier last-attempt/last-success status into the existing
  machine status surface.

### Phase 3 - Installer
- [x] Add `agent-machines self-update install` / `status` / `uninstall`,
  registering two genuine Windows Scheduled Tasks (hourly Tier 1, daily
  Tier 2) via the same elevate-or-instruct convention as `agent-dispatch`'s
  installers, scoped to "run only when logged on." `install` first resolves
  the declared per-tier config and refuses to prompt for elevation for a
  tier that resolves to "not opted in."
- [x] Make Scheduled Task presence itself a `restore --apply` reconciliation
  target: an ordinary apply registers a newly-opted-in tier's task (falling
  back to the same elevate-or-instruct message if unelevated) and removes a
  newly-opted-out tier's task, without a separate elevation prompt for
  removal.

### Phase 4 - Validation and rollout
- [x] Cover lock acquisition/staleness-reclaim, fast-forward-only pull
  safety (diverged/dirty skip, never force), live-session deferral, and
  installer elevate-or-instruct behavior on Windows.
- [x] Land, deploy, and dogfood on one machine; verify the status signal
  reflects reality before proposing wider mesh rollout (mesh rollout itself
  is private-repo scope, tracked in the `dotfiles` proposal doc).

### Bug sweep — linked open bugs (2026-09-24)

_Correlated via a facility-driven sweep of open `bug`-labeled issues against active efforts (VEI + direct review). Not yet triaged into a numbered phase — listed here as upcoming work for whoever picks this effort back up._

- [ ] **#3444** agent-dispatch: 8h reviewer-pipeline outage from a truncated recipes_cli.py in a self-updated venv (stale build/egg-info shadowing, #2863-class recurrence)
  - A self-updated venv silently going stale/truncated is exactly the failure mode this watchdog effort exists to catch.
- [ ] **#3419** agent-dispatch: published dev193 marketplace payload has a truncated recipes_cli.py (missing register_recipes_commands, breaks CLI entirely)
  - Same truncated-payload-after-self-update defect family as #3444 above.

## Validation Plan

- [x] A stuck prior Tier-1 or Tier-2 run's lock is reclaimed only once both
  its recorded PID is dead and its tier-specific staleness bound has
  elapsed; a live prior run's lock is never double-driven.
- [x] A diverged or dirty anchor checkout causes Tier 2's pull step to skip
  with a loud warning rather than force-reset or force-pull.
- [x] Tier 2 never interrupts a live mux/Copilot session or in-flight
  indexing; it defers using the same guard `agent-machines-declarative-control-plane`
  already established.
- [x] `self-update install` attempts elevated Scheduled Task registration
  first and prints an explicit re-run-elevated instruction on failure,
  matching `agent-dispatch`'s own installer behavior.
- [x] A tier resolved as "not opted in" (by state-repo default, local
  override, or their combination) is never registered by `install` and is
  never prompted for elevation; a `run` invocation for a since-opted-out
  tier is a clean no-op rather than performing its steps.
- [x] A local machine-level opt-in/opt-out declaration overrides a
  conflicting state-repo-declared default for that machine, matching the
  control plane's existing authority precedence; equal-authority
  contradictions are still a hard validate-time error, not a silent
  last-writer pick.
- [x] `restore --apply` registers a newly-opted-in tier's Scheduled Task
  (or reports the elevate-and-retry message) and removes a newly-opted-out
  tier's Scheduled Task without requiring a fresh elevation prompt to
  remove it.
- [x] Tier 1 and Tier 2 use independent locks and neither tier's run blocks
  the other's scheduled tick from starting.
- [x] The recorded last-attempt/last-success status is visible through the
  existing machine status surface without an SSH round-trip.

## Non-goals

- Not a general CI/CD system — reconciles only already-declared state.
- Does not replace operator-invoked `<repo> update`; reduces how often it is
  *necessary*, not how often it is *possible*.
- Does not wake a hibernated/powered-off machine — only addresses "logged in
  but silently stale."
- Mesh-wide private rollout, machine roster, and org-specific gating stay in
  the private `dotfiles` proposal; this effort ships only the generic,
  organization-neutral `agent-machines` primitive.

## Journal

### 2026-09-14 - Kickoff
- Opened [#2620](https://github.com/ThomasMichon/copilot-extensions/issues/2620)
  as the public coordination issue.
- Extended `visions/agent-fabric` with `unattended-tiered-self-convergence`,
  generalizing the private `dotfiles` proposal's design into an
  organization-neutral fabric should-be.
- Resolved the proposal's open design questions (Tier 1 scope, reentrancy,
  logon scope, opt-in UX, Tier 2 cadence, status placement) with explicit
  reasoning so Phase 2 can start from a concrete plan rather than an open
  design space.

### 2026-09-14 - Opt-in gated by declarative config, not a bare command

- Operator steer: registration must be **opt-in via config**, resolved from
  a state-repo-declared default with an explicit local machine-level
  override taking precedence — not solely gated by "the operator happened
  to run the install command."
- Revised the design to model per-tier enabled/disabled as a declarative
  resource in the existing `agent-machines-declarative-control-plane`
  authority model (reusing its precedence, validation, and drift reporting
  rather than a parallel config mechanism). `install`/`run` now resolve this
  config first: a not-opted-in tier is never registered, never prompted for
  elevation, and a stray `run` is a clean no-op. `restore --apply` becomes
  the ordinary path that both registers a newly-opted-in tier and removes a
  newly-opted-out one, so opting out does not require a person to
  separately remember to run an uninstall command.

### 2026-09-14 - Phase 2 landed

- Added the `self-update` declarative resource type with tier-scoped
  authority selection, so a shared default and a more-specific local machine
  declaration resolve through the same schema-v4 precedence used by the rest
  of `agent-machines`.
- Added `agent-machines self-update run --tier watchdog|sweep`. `watchdog`
  resolves the installed dtssh launcher config, checks whether the launcher
  process is alive, and starts only the launcher when it is missing. `sweep`
  performs fast-forward-only pulls for discovered adopted repos, then gates
  the disruptive plugin refresh and machine restore steps on the existing
  live-session deferral boundary.
- Added independent named per-tier locks with PID/timestamp state,
  staleness-bounded reclaim, and per-tier last-attempt / last-success status
  recorded into the existing plan/status surface.
- Validation added focused unit coverage for opt-in no-op behavior, authority
  precedence, live-run non-double-drive vs stale-lock reclaim, dirty/diverged
  pull skips, sweep deferral while a live session exists, and plan-surface
  visibility of the recorded timestamps. The full `agent-machines` suite,

### 2026-09-14 - Phase 3 landed

- Added `agent-machines self-update install`, `status`, and `uninstall`, with
  Windows Scheduled Task registration/removal for the `watchdog` and `sweep`
  tiers using the same elevate-or-instruct pattern as `agent-dispatch`:
  already-opted-out tiers are skipped without any registration attempt, while
  first-time registration emits an explicit elevated retry command.
- Promoted Scheduled Task presence from a placeholder `self-update` resource to
  a real `restore --apply` reconciliation target. Newly opted-in tiers now
  register their task during restore (or defer with the same install retry
  instruction), and newly opted-out tiers are removed in-place without a new
  elevation prompt.
- Added focused unit coverage for task registration, elevation deferral,
  opted-out install skips, status JSON, and restore-time add/remove behavior
  without touching any live Scheduled Tasks.
  install-contract check, and version-consistency check passed.

### 2026-09-14 - Phase 4 landed

- Re-ran the focused and full `agent-machines` suites against the merged
  Phases 2 and 3 implementation and re-checked the effort's own Validation
  Plan item-by-item; the landed tests now cover lock reclaim, live-run
  non-double-drive, dirty/diverged pull skips, live-session deferral,
  installer elevate-or-instruct behavior, authority precedence, restore-time
  task registration/removal, independent tier locks, and status visibility.
- Deployed the merged runtime on this machine with `copilot-extensions update`
  and dogfooded the installed `agent-machines self-update status --json`
  surface. This machine is currently not opted in, so the expected real
  output is both tiers reported as `opted_in: false` / `registered: false`
  with no last-attempt or last-success timestamps; the shipped status command
  reported exactly that.
- Marked the public implementation slice done. Private mesh-wide rollout,
  machine selection, and any organization-specific opt-in policy remain in the
  separate private `dotfiles` proposal, per this effort's non-goals.
