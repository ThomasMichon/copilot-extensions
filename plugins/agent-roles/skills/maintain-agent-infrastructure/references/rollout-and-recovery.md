# Rollout and agent-plane recovery checks

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
The role neither implements missing installer support nor authorizes bypasses.
Report a legacy updater refusal to its owner instead of silently adopting it as
the desired contract or improvising a forced replacement.

## Post-outage dependency checks

| Boundary | Discriminating check | Owner-led disposition |
|----------|----------------------|------------------------|
| Launcher environment | Does the actual service/user-manager process inherit the required executable search path? Does its launched child select the real CLI and dependency tools, not an interactive legacy installer prompt? | Fix declarative service configuration through its source owner when authorized. A changed interactive shell or multiplexer-global PATH does not prove a running launcher inherited it. |
| Credentials and broker | Can the selected broker perform its normal authorized readiness check without exposing a secret? Is an unavailable vault/interactive sign-in blocking downstream consumers? | Name the blocked dependency and operator ceremony. Do not print tokens, change identity, bypass auth or restart downstream services blindly. |
| Bridge, coordination and dispatch | Do routes reach the intended running generation and can the normal read operation reach its retained worktree/task state? | Preserve original IDs and ownership; process presence or an accepted request is not consumer readiness. |
| Container connectivity | Does the declared container retain its expected network attachment, sibling DNS and route after restart? | Ask the container/service owner to reconcile or recreate only under its effect authority, preserving state; do not perform ad-hoc network surgery. |
| Remote mounts/name resolution | Was DNS/network ready when a hostname-based mount was attempted, and is the intended resource now reachable? | Route declarative boot ordering/retry changes to the mount owner. A successful address-based probe does not authorize replacing hostname policy or weakening host verification. |
| Session recovery | Is the retained worktree's original current head already running, resumable, held, unknown or superseded? | Resolve through the native worktree/session owner before resume; do not infer abandonment from an ended PID or unreachable host. |

Use the smallest read that distinguishes the observed symptom. A broker error
can explain several downstream failures without proving they need restarts.
Preserve error text privately and route a minimal neutral finding to its owner.

## Recover original agency, not just a pane

- Read the last-known-active cohort and current native head/succession data.
  Reconcile surviving execution and unknown original launch/resume effects
  before requesting another one. Preserve the same user/provider identity,
  worktree, session and accepted objective/authority.
- Use only the session owner's supported resume path under explicit recovery
  authority. Do not silently fall back to a new session, old CLI wrapper or
  different account when the original path is unavailable.
- Verify the actual launched executable and its original resume identity
  (`--resume <original-id>` for a CLI provider using that interface), plus the
  native owner's readback. A pane, launcher success or resume receipt alone
  does not establish a running agent.
- Resume can leave a session idle without a new turn. Under an original grant
  covering continuation, resolve its exact retained session and send one
  scoped recovery notice through the native messaging owner, preserving the
  message identity across an unknown result. Do not send twice merely because
  acknowledgement is slow.
- Report delivery/admission, consumption, charter acceptance and useful
  progress separately. Reattached execution that has not resumed its objective
  remains a recovery gap; message receipt is not consumption.

## Bounded disposition

Record the observed failure, selected environment, authority, current owner,
pending original effects, next discriminating check and revisit event.
Maintain one watch/cadence owner and back off unchanged failures.
An unsupported or unreachable environment stays unknown/unfinished.
Use the existing machine-maintenance handoff for residual local work, with
declarative repeatable state and exact issue predicates. An issue is not
permission to mutate, and local assignment is not an atomic cross-machine claim.

Static role tests check that these obligations remain discoverable. They do
not qualify native rollout, broker availability, network recovery, session
resumption or fleet-wide safety; those need the assigned owner's actual
per-target acceptance checks and original effect authority.
