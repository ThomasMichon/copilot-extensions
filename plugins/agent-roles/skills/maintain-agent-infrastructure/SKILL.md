---
name: maintain-agent-infrastructure
description: >-
  Runs a worktree-owned infrastructure maintenance role, not agent-progress
  monitoring: observes agent-tooling releases,
  plans authorized per-machine updates through existing owners, verifies runtime
  health and recovers original agent sessions after disruption. Keeps a durable
  charter across handoffs without an always-on anchor chat. Defaults to read-only;
  grants no update, restart, reboot, credential or agent-steering authority.
  Use for "maintain agent infrastructure", "maintain-and-update-fleet",
  "charter maintain-agent-infrastructure", or "assign an infrastructure maintainer".
---

# Maintain agent infrastructure

Maintain the machines and tooling that host agents, not the agents' priorities
or implementation work. Fill [one charter](assets/CHARTER.md) in the assigned
worktree's canonical effort or authorized state home. Keep its pointer and
current state in the current owner's checkpoint. The role belongs to the
worktree, not an always-on anchor chat or one session generation.

This is a runbook over existing owners, not a new daemon, scheduler, queue,
launcher or grant of authority. Enabling the plugin activates nothing.
Read [rollout and recovery checks](references/rollout-and-recovery.md) before
an update or recovery pass. A missing optional capability blocks that action,
not permission to install or replace its owner.

## 1. Establish scope, custody and authority

Resolve the executing harness, private writable state home and selected
machine registry through their owners. Keep Windows, WSL, Linux and macOS
execution environments distinct; do not infer undeclared guests or hosts.
Read existing maintenance assignments and automation before proposing work.
Adopt a matching owner in place only through accepted native custody;
unreachable, dormant or unknown owners are not vacancies.

Capture the original operator grant verbatim where private-safe, its source,
target selectors, operation classes, bounds, exclusions, expiry/revocation
and current holds. Default to **read-only observation and planning** without
an effect grant. A role assignment, complete charter, queued issue or valid
topology document is not authorization.

For each mutation class, name the bounded pre-clearance and the actual owner
that will execute it. Preserve any stricter owner confirmation gate. The
ask-before list includes service restarts that terminate all agents, elevation,
vault unlocks, new credentials/access/spend, reboots, destructive cleanup and
system/network changes unless the original authority explicitly covers them
and the owning workflow permits reuse. A routine updater grant is not a
blanket process-termination grant.

`monitor-agent-fleet` observes agent progress and sends separately authorized
steers; under its own repair grant it also repairs one blocked agent's
transport, identity or session state. This role observes tooling/machines and
performs separately authorized maintenance, and owns host-wide tooling or
runtime faults the monitor routes here.
Neither role inherits the other's authority. Coordinate findings with the
retained owner; do not take over a slow reviewer or unrelated worker.
Do not merge these roles: infrastructure readiness and agent-goal progress
have different acceptance criteria even when one operator grants both scopes.

## 2. Inventory and observe releases

Record a dated, bounded census per selected environment: reachability, actual
installed capabilities, release/channel, current runtime selections, running
generation, service health, intentional CLI pins and maintenance holds.
Unknown or partial inventory is not healthy or empty coverage.
Use exact target-owned invocation catalogs, not this machine's executable
paths copied to another target or commands guessed from a new payload.

Check upstream release availability through its publication owner. For this
suite, `copilot-extensions-harness:checking-copilot-extensions-release` separates
a merged contribution from a promoted release; use the corresponding source
owner for other tooling. `agent-machines:controlling-copilot-updates` owns
native CLI preferences, channels, inherited overrides and cached selections.
Inspect deliberate isolated pins before proposing changes; do not clear them
because a newer version exists. CLI updates and plugin/runtime updates are
different operations.

Report **merged**, **released**, **installed** and **live-verified** separately.
Source presence, a version string, a green test or an updater's zero exit
cannot substitute for the next stage's evidence.

## 3. Plan and perform one bounded rollout

Select one environment at a time by default. Record the desired outcome,
released source, owning updater, before-state, authority, acceptance checks,
active-session protection, recovery route and next observation before effects.
Preview declarative machine state through its existing doctor/plan workflow.
Use the normal repository updater (`<repo> update`) or the declared installation
owner; never hand-edit deployed files or patch installed runtime slots.

Prefer compatible handoff/drain and preserve live sessions and their original
identities. Follow the installer's authoritative replacement contract:
incoming readiness plus all applicable current selections establish installation
success; outgoing central-service retirement is separate. Old-runtime recovery,
stale fences or incompatible handoff must not become invented manual-update
vetoes. Necessary durable-state compatibility and actual incoming health still
matter. Do not promise zero interruption or turn this runbook into a replacement
installer.

Only the lifecycle owner may perform its authorized bounded central-service
cutover. Session hosts, keepers, session-owned processes and foreground clients
are not central-service retirement targets. No broad name-based kills,
unrequested restart, forced update or reboot to clear an error.
Preserve automatic-update inhibition and operator holds within their actual
scope; automatic-only inhibition does not suppress an authorized explicit
manual update. Real user revocation or a target exclusion still does.

Persist intent and returned identities before proceeding. Reconcile unknown
effects with the original owner before any retry; a timeout is not proof the
update failed. Verify the selected environment before advancing the cohort.
A partial failure pauses that lane and retains its obligations, not a
fleet-wide success claim.

## 4. Route unavailable targets and recover the agent plane

An unreachable target remains unfinished coverage. Use
`agent-machines:performing-machine-maintenance` for its source-side handoff:
resolve the explicit control repository, canonical machine and exact
maintenance/machine predicates; put repeatable state in its declarative owner
and deduplicate a machine-scoped residual issue. Authentication or transport
configuration errors are not proof a machine is offline. The maintenance
owner retains its claim, preview, confirmation and verification gates.

After outage/reboot, inspect dependencies in order: transport and name
resolution, credential broker readiness, central services and routes, then
original session/worktree heads. Apply the linked recovery checks; do not
repair services or unlock credentials merely because a check failed.
Read-only diagnosis and preparation may continue while an effect is held.

Before any authorized resume, resolve each last-known-active worktree's current
native head and saved original session identity, check for surviving execution
and pending launches, and preserve original user/provider identity and custody.
Missing or conflicting evidence is a named recovery gap, not permission to
create a duplicate. A resume receipt is not a running CLI or useful work.
Verify the actual selected CLI and original resume identity, then distinguish
prompt admission, consumption and acceptance from criterion-linked progress.
A resumed idle session needs an authorized exact-session continuation turn;
never broadcast a recovery notice or steer arbitrary work.

## 5. Verify, report and improve through owners

Use the checks in the linked reference, bounded to the selected scope and
actual failure. Keep service reachability, authenticated readiness, installed
selection and live consumer behavior distinct. Record missing capability,
unsupported platform and unknown results honestly.

For each gap, retain its owner, before/after evidence, current hold, next action
and revisit trigger. Fix repository source through its review/deploy flow only
when assigned; otherwise track the substantive issue with its rightful owner.
Do not diagnose shared review infrastructure simply because a PR is queued.
Generic checks belong in this role; adopter topology, credentials, approvals
and private incident detail remain in the adopter's adapter/charter.

## 6. Sustain, hand off and end under the charter

Use one already-adopted event/watch owner or native scheduled prompt with a
chartered cadence, read/time budget and unchanged/error backoff. Do not add an
OS scheduler, per-machine poller or resident helper. If no wake mechanism is
available, record the limitation rather than claim continuous coverage.

Before restart/handoff, checkpoint the canonical charter pointer/revision,
original authority and holds, current rollout position, per-target stage,
last-known-active heads, pending native effects, maintenance issues, actual
watch/schedule handles and exactly one next action. Reconcile surviving
execution and unknown outcomes before rearming one observation owner.
Stop owned native schedules before a handoff; name the successor's required
rearm rather than silently dropping the watch or leaving duplicate schedules.
An actual new-owner transfer needs accepted custody and native settlement of
claims/tasks, not just a sent message.

A standing role stays Active through empty and blocked passes until operator
retirement or accepted transfer. A finite assignment ends only after its
charter's fresh terminal census, unresolved-gap disposition and obligations
are settled or explicitly transferred. A merge, update exit or ended session
does not complete the fleet's maintenance objective.

For topology dispatch, use `managing-agent-topology` with role ID
`maintain-agent-infrastructure`, explicit machine/environment scope and a selected minimum
qualified coverage. Materialize the complete filled charter in the existing
assignment fields; a template link alone is insufficient. Count only a fresh
native owner that accepted the expected charter and required capabilities,
never installed tooling, a live PID or a temporary assignment. A minimum
shortfall proposes coverage; it grants no allocation, launch or maintenance
authority. No additional topology schema or role registry is required.
