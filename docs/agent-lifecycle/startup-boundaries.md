# Where did the reviewer stop during launch?

[Review snapshots](review-snapshots.md) · [Bridge sessions](bridge.md) · [Worktree records](worktree.md)

Start with the **last thing the owner can prove**. “Launch timed out” might
mean no command was selected, preparation blocked before a pane was created,
or a working process failed to report back. These have different next steps.

| Last boundary | Who writes what? | If the next observation is missing |
|---|---|---|
| Checkout allocated | Worktrees writes its Git checkout and canonical worktree YAML. Keyed requests also have the creation journal. | Look up the original allocation key. An existing directory alone proves neither successful pairing nor successful launch. |
| Knowledge paired, if required | After the main record exists, [pair completion](../../plugins/agent-worktrees/src/agent_worktrees/creation_pair_completion.py) creates the companion and stamps the composition. Incomplete preparation marks `<checkout Git admin dir>/agent-worktrees-local-guidance/pair-pending`. | Some local pairing failures are reported as nonfatal; trust/policy errors propagate. Required receiving-side pairing leaves the original child incomplete; allocating another child is not the repair. |
| Launch plan resolved | Worktrees returns an action, command, working directory and environment, sometimes nested under `launch`. An [instrumented resolver attempt](../../plugins/agent-worktrees/src/agent_worktrees/launch_attempt.py) writes `launch-attempts/<attempt>/<operation>.json` under the tracking directory: `resolution-requested`, then `plan-ready`, `forwarded`, `remote-admission-returned` or `unknown` as applicable. | Inspect the **consumer** that refused it. For example, [Bridge local resolution](../../plugins/agent-bridge/src/agent_bridge/transport.py) rejects an empty `cmd`; remote project resolution requires a worktree ID and directory. `plan-ready` is not a PID, pane or conversation. |
| Plugins prepared | For a typed CLI acquisition requiring plugin preparation, the [pre-mux owner](../../plugins/agent-worktrees/src/agent_worktrees/bridge_acquisition_launch.py) reads `<Git admin dir>/copilot-venue-plugins/launch-requests/<operation>/request.json`. It checks the allocated root, worktree, producer plan, deadline and cancellation; successful preparation writes `request.prepared.json`. | A changed root, missing plan, deadline or cancellation can fail **before** the mux call; its pre-launch failure receipt says `native_launch_started=false`. That is stronger than an outer caller's timeout. Manager launch maintenance/reconciliation is a separate step, not this receipt. |
| Instructions projected | Worktrees prepares `.github/instructions/agent-worktrees-generated/…`; its [checkout-local metadata](../../plugins/agent-worktrees/src/agent_worktrees/static_preparation.py) includes `owners.json` and `publication.json`. Bridge calls the [static-preparation owner](../../plugins/agent-bridge/src/agent_bridge/local_static_preparation.py) and reads status and counts (`ready`, `partial`, `failed`, `timeout`). | An [exhausted publication budget](../../plugins/agent-worktrees/src/agent_worktrees/static_instructions.py) or a blocking result is a preparation diagnostic, not a task status. A publication revision is recorded before possible writes and does not prove that the agent loaded the instructions. |

These are path-specific checkpoints, not compulsory steps in every launch.
For example, the Manager [POSIX launcher](../../worktree-manager/bin/launch-session.sh)
can join an already-live mux without doing fresh-launch maintenance. Its
setup progress records the stage and duration separately from the worktree
record. A missing plugin, a failed plugin reconciliation, a pending knowledge
pair and a projection timeout are not interchangeable reasons to start over.

## What the mux wrappers record

| When | Record or observation | What it does **not** prove |
|---|---|---|
| Before mux | `launch-session.sh` (or [PowerShell counterpart](../../worktree-manager/bin/launch-session.ps1)) parses the resolved plan. On the local interactive path it distinguishes join from create, correlates setup/activity by launch ID and selects the pane command. | Worktrees' typed `embody` path and hosted ACP are separate callers. Not every Dispatch job runs this top-level launcher. |
| New pane, before Copilot | The [pane wrapper](../../worktree-manager/bin/pane-wrapper.sh) reads the mux-native session incarnation and pane ID. Its bounded `start` callback can register `(project,W)`, mux name/incarnation, pane and revision in Manager's [`mux-mapping.json`](../../worktree-manager/src/worktree_manager/mux_mapping_registry.py). A seed-enabled wrapper can record `launching` before executing the child. | A terminal or seed-launch receipt is not a Copilot session ID. Typed Bridge acquisition is unseeded here; task delivery is a later operation. |
| Copilot reports a native session | The Worktrees [session-start hook](../../plugins/agent-worktrees/scripts/register-session.sh) reads `sessionId` and `cwd` from the hook payload; registration records the activation and eligible head. Separately the Bridge extension writes its live registration. | A successful registration is not task acceptance, a delivered review, or evidence that all startup guidance was read. |
| Child exits or user detaches | The wrapper's [pane lifecycle owner](../../worktree-manager/src/worktree_manager/pane_lifecycle.py) handles end/cancel against the same mapping and can report `pane_exited` with runtime and exit code. The outer launcher records detach separately and checks whether the mux session actually disappeared before optional post-exit finalization. | Detaching a terminal is not exiting Copilot. Abrupt termination may skip the end callback; an old mapping alone cannot prove the process survived. |

## If a Host or mux dies suddenly

**Dispatch reconciles; it does not copy Bridge status into a reservation.**
The [lane supervisor cycle](../../plugins/agent-dispatch/src/agent_dispatch/supervisor_cycle.py)
checks current reservations, incomplete starts, requested releases and cold
bodies before selecting new tasks. Bridge events can wake that cycle sooner;
periodic inspection remains necessary. The configured driver, identity,
availability of its observer, recovery policy and phase of the attempt matter.

* **Only the ACP frontend disconnected:** the original Session Host and child
  may both be live. Reattach to the surviving Host if qualified; do not infer
  the workspace or slot is free.
* **The Host connection failed or the mux pane disappeared:** the last
  reservation can still read `spawned`. Check the original Host/child or mux
  incarnation, native session, activity and assignment. Bridge has a
  [process-death settlement path](../../plugins/agent-bridge/src/agent_bridge/session_host_liveness.py);
  CLI registration freshness is a different observation. Loss of one is not
  proof of the other's exit.
* **Absence confirmed:** [Dispatch liveness recovery](../../plugins/agent-dispatch/src/agent_dispatch/queue_liveness.py)
  can preserve a resumable owner in `suspended`, or requeue/abandon where the
  policy and attempt limit call for it. A known startup failure and normal
  retirement have their own reservation updates. **Outcome unknown:** retain
  the attempt and any unresolved delivery or cleanup; don't free capacity or
  replay a review solely because a heartbeat expired.
