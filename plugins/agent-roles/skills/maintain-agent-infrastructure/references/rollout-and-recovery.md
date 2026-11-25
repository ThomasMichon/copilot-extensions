# Rollout, host recovery and maintenance-window checks

Use with [maintain-agent-infrastructure](../SKILL.md). These are evidence questions,
not commands, authority, or proof any particular platform adapter exists.
Qualify the target's actual installed owner before acting. Reuse its native
health and recovery workflow; do not build a parallel fleet controller.

## Release and installation

1. Is the contribution merged, and is its effective content in the selected
   released channel? Regenerated release history may not preserve contribution
   ancestry. A merged PR or bumped version alone is insufficient.
2. Which payload/runtime generation is installed, and which one do current
   invocation paths, service routes and service/task registrations select?
   Inspect each addressed environment, not the orchestrator's shell alone.
3. Are all requested incoming runtimes ready and required central services
   actually serving them? Verify a bounded consumer request through the normal
   route. Distinguish authenticated readiness from a listening port.
4. Did the owner preserve configuration, credentials, worktrees, sessions and
   application state? Required state compatibility remains an incoming readiness
   condition. Compatible handoff/drain is preferred, not a universal prerequisite.
5. Does separate outgoing retirement remain with the lifecycle owner, scoped to
   obsolete central generations? Never target session hosts, keepers,
   session-owned processes, foreground clients or protected connectivity.

This follows the [authoritative replacement contract](https://github.com/ThomasMichon/copilot-extensions/blob/main/visions/installer/README.md#authoritative-runtime-replacement)
and [graceful central-daemon cutover](https://github.com/ThomasMichon/copilot-extensions/blob/main/docs/patterns/graceful-daemon-cutover.md).
Those documents describe the target; inspect current adoption honestly.
The role alone neither assigns missing installer implementation nor authorizes bypasses.
Report a legacy updater refusal to its owner instead of silently adopting it as
the desired contract or improvising a forced replacement.
Continue diagnosis and approved repair strategies within the charter. An
explicitly adopted source-maintainer extension owns any implementation,
review/release/rollout and original-consumer verification, not this base role.

## Post-outage dependency checks

| Boundary | Discriminating check | Owner-led disposition |
|----------|----------------------|------------------------|
| Launcher environment | Does the actual service/user-manager process inherit the required executable search path? Does its launched child select the real CLI and dependency tools, not an interactive legacy installer prompt? | Fix declarative service configuration through its source owner when authorized. A changed interactive shell or multiplexer-global PATH does not prove a running launcher inherited it. |
| Credentials and broker | Can the selected broker perform its normal authorized readiness check without exposing a secret? Is an unavailable vault/interactive sign-in blocking downstream consumers? | Name the blocked dependency and operator ceremony. Do not print tokens, change identity, bypass auth or restart downstream services blindly. |
| Bridge, coordination and dispatch | Do routes reach the intended running generation and can the normal read operation reach its retained worktree/task state? | Preserve original IDs and ownership; process presence or an accepted request is not consumer readiness. |
| Container connectivity | Does the declared container retain its expected network attachment, sibling DNS and route after restart? | Ask the container/service owner to reconcile or recreate only under its effect authority, preserving state; do not perform ad-hoc network surgery. |
| Remote mounts/name resolution | Was DNS/network ready when a hostname-based mount was attempted, and is the intended resource now reachable? | Route declarative boot ordering/retry changes to the mount owner. A successful address-based probe does not authorize replacing hostname policy or weakening host verification. |
| Session recovery | Which retained heads were active before the disruption, and which are now running, held, unknown or superseded? | Read for the report and the crash cause only. Hand agent recovery to the fleet monitor after host-ready; the maintainer does not resume, restart or replace agent sessions. |

Use the smallest read that distinguishes the observed symptom. A broker error
can explain several downstream failures without proving they need restarts.
Preserve error text privately and route a minimal neutral finding to its owner.

## Host-ready and handback to the monitor

- After a crash or outage, find and report its cause. Restore host runtime and
  services through their owners, then verify the host with bounded consumer
  checks.
- Declare host-ready to the host's fleet monitor with the verified checks, the
  last-known-active cohort and any pending original effects. The monitor
  recovers agents one at a time under its own recovery rules; see its
  [graduated repair](../../monitor-agent-fleet/references/repair-escalation.md#recovery-rules).
- An unready host stays not-ready. Do not declare host-ready to unblock agent
  recovery before the host checks pass.

## Maintenance window

Before a multiplexer update, a reboot or another update that disrupts running
agents, use this protocol. Updates that do not affect running agents need no
monitor step.

1. **Request** (maintainer): send the host's monitor a maintenance-window
   request with the host, reason, earliest time and expected duration.
   If the monitor or the maintainer runs on that host, the quiesce also stops
   it. Proceed only when an authorized coordinator off the host survives the
   window with durable window state (the request and the agents stopped for
   it) and a path to wake and Restore, or when a supported native handoff or
   bootstrap provides that path. Otherwise hold the window.
   Before Quiesce, the monitor holds every automatic path that can launch a
   replacement onto the host, through its native owner: standing recovery,
   legacy finite-worker recovery and host-targeted admission
   ([hold automatic replacement](../../monitor-agent-fleet/references/fleet-register.md#hold-automatic-replacement)).
   If one cannot be held, hold the window.
2. **Quiesce** (monitor): each agent on the host is brought to a safe
   checkpoint and stopped without discarding work through its own authorized
   owner: the monitor for agents its charter covers, and another owner for
   each other agent. Each stopped agent is recorded as "stopped for
   maintenance". The monitor tells the maintainer the host is clear only when
   every agent on the host is confirmed stopped. If coverage or quiescence of
   any agent is unknown, hold the window; a partial stop is never host-clear.
   In-flight launches are settled before host-clear.
3. **Maintain** (maintainer): perform the update or reboot, verify the host and
   declare host-ready.
4. **Restore** (monitor): each agent's owner resumes the retained sessions it
   stopped, and the monitor verifies that each is live. The replacement holds
   stay in place until host-ready and Restore, then the monitor releases them.
   A finite Dispatch task requeued during the window is not resumed: the
   monitor reconciles it with its supervisor, releases the holds it needs, and
   verifies that the supervisor's new worker is live.

Do not start the disruptive step before the monitor reports the host clear.
If the host has no assigned monitor, report the affected agents and hold the
disruptive step. The maintainer never stops, starts or resumes the agents
itself.

## Bounded disposition

Record the observed failure, selected environment, authority, current owner,
pending original effects, next discriminating check and revisit event.
Maintain one watch/cadence owner and back off unchanged failures.
An unsupported or unreachable environment stays unknown/unfinished.
Use the existing machine-maintenance handoff for residual local work, with
declarative repeatable state and exact issue predicates. An issue is not
permission to mutate, and local assignment is not an atomic cross-machine claim.

Static role tests check that these obligations remain discoverable. They do
not qualify native rollout, broker availability, network recovery, host
readiness or fleet-wide safety; those need the assigned owner's actual
per-target acceptance checks and original effect authority.
