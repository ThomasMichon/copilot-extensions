# Agent Machines Self-Update Watchdog

- **Slug:** `agent-machines-self-update-watchdog`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-09-14
- **Status:** Active
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
self-convergence, registered through the same one-time elevated opt-in
convention `agent-dispatch` already uses, with explicit reentrancy guarding
and an inspectable last-run signal.

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
- **Opt-in UX:** a single `agent-machines self-update install` command the
  operator runs themselves once (during onboarding or whenever they choose
  to adopt it); it attempts elevated registration and prints an explicit
  "re-run this elevated" message on failure. No agent session ever attempts
  a silent/automatic elevation.
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
- [ ] Add `agent-machines self-update run --tier watchdog` (Tier 1: dtssh
  launcher liveness check-and-start only; no state mutation beyond starting
  the launcher process).
- [ ] Add `agent-machines self-update run --tier sweep` (Tier 2: fast-forward
  `dotfiles`/harness pull, `agent-worktrees reconcile-plugins --apply
  --with-payload-refresh`, `agent-machines restore --apply --all-projects`),
  reusing the existing live-session deferral guard for any disruptive step.
- [ ] Add the tier-scoped named lock (PID + timestamp, staleness-bounded
  reclaim per tier as designed above).
- [ ] Record per-tier last-attempt/last-success status into the existing
  machine status surface.

### Phase 3 - Installer
- [ ] Add `agent-machines self-update install` / `status` / `uninstall`,
  registering two genuine Windows Scheduled Tasks (hourly Tier 1, daily
  Tier 2) via the same elevate-or-instruct convention as `agent-dispatch`'s
  installers, scoped to "run only when logged on."

### Phase 4 - Validation and rollout
- [ ] Cover lock acquisition/staleness-reclaim, fast-forward-only pull
  safety (diverged/dirty skip, never force), live-session deferral, and
  installer elevate-or-instruct behavior on Windows.
- [ ] Land, deploy, and dogfood on one machine; verify the status signal
  reflects reality before proposing wider mesh rollout (mesh rollout itself
  is private-repo scope, tracked in the `dotfiles` proposal doc).

## Validation Plan

- [ ] A stuck prior Tier-1 or Tier-2 run's lock is reclaimed only once both
  its recorded PID is dead and its tier-specific staleness bound has
  elapsed; a live prior run's lock is never double-driven.
- [ ] A diverged or dirty anchor checkout causes Tier 2's pull step to skip
  with a loud warning rather than force-reset or force-pull.
- [ ] Tier 2 never interrupts a live mux/Copilot session or in-flight
  indexing; it defers using the same guard `agent-machines-declarative-control-plane`
  already established.
- [ ] `self-update install` attempts elevated Scheduled Task registration
  first and prints an explicit re-run-elevated instruction on failure,
  matching `agent-dispatch`'s own installer behavior.
- [ ] Tier 1 and Tier 2 use independent locks and neither tier's run blocks
  the other's scheduled tick from starting.
- [ ] The recorded last-attempt/last-success status is visible through the
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
