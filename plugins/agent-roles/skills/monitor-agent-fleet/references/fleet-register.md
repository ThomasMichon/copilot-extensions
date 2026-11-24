# Fleet register

Back to [Monitor agent fleet](../SKILL.md).

The fleet register is the monitor's record of the **actual** state of every
assigned agent. The topology dispatcher owns the **intended** state: its
assignment map of work, worktree, host and duty. The monitor joins the two
each cycle. The register records observations; it grants no authority.

## Rows

Keep the register in the monitor's supervisory record and reference it from
the checkpoint. One row per assigned agent:

| Column | Content |
|---|---|
| Agent | Stable agent or owner identifier |
| Worktree | Native worktree identity and current head session |
| Host | Machine or environment that runs it |
| Duty | Assigned duty or charter reference from the assignment map |
| State | `running`, `live-idle`, `held`, `stopped for maintenance`, `recovering`, `terminal` or `unknown` |
| Last observed | Time and native reader of the last observation |
| Source | Evidence reference, such as a reader result or a recovery record |

An error or a timeout leaves the state `unknown`, not `terminal`. Keep
`stopped for maintenance` until the monitor resumes the agent and verifies
it is live.

## Join with the assignment map

Each cycle, read the dispatcher's current assignment map and compare it with
the register. Report each mismatch to the dispatcher with its evidence:

- An assigned duty has no live agent, or its agent is on another worktree or
  host.
- A live agent has no assignment, or holds a duty that was transferred.
- Two live heads claim one duty or worktree.

The monitor does not reassign duties, allocate worktrees or change placement
to fix a mismatch. It recovers an assigned agent only under the
[recovery rules](repair-escalation.md#recovery-rules). If no assignment map
exists, report that gap and keep the register from observation alone.

## Duty handoff

1. **Assign** (dispatcher): records the duty and owner in the assignment map.
2. **Run** (monitor): keeps the assigned agent alive and on charter.
3. **Transfer** (dispatcher): records the new owner.
4. **Close** (monitor): confirms that the old owner is done, reassigned or
   finalized by its own owner, and that the new owner is live. Never discard
   the old owner's work to close a transfer.

## Maintenance windows

Record each request from the host's maintainer: host, reason, earliest time,
expected duration. During Quiesce, list every agent on the host. Mark each
agent the monitor stopped as `stopped for maintenance`, and record which
owner quiesced each agent outside the charter. Tell the maintainer the host is
clear only when every listed agent is confirmed stopped; an agent with
unknown coverage or state holds the window. After the
maintainer declares host-ready, Restore resumes each of those agents and
verifies each one is live before the row leaves that state.

When the monitor runs on the affected host, keep this window state where the
surviving coordinator or native handoff can read it, not only on that host.

### Hold automatic replacement

A worker stopped for maintenance can look gone. Before Quiesce, hold every
automatic path that can launch a replacement onto the host:

- **Standing recovery.** Revoke or hold any agent-dispatch standing recovery
  grant for agents on the host, as described in the agent-dispatch
  standing-charters guide.
- **Legacy finite-worker recovery.** Revoking a standing grant does not stop
  it. Each supervisor cycle with recovery enabled releases the spawn
  reservation of a confirmed-gone finite worker and requeues its task; a later
  poll then launches a new worker for that task.
- **Host-targeted admission.** New tasks can also launch onto the host.

Hold the last two through each agent-dispatch installation that can launch
onto the host: `agent-dispatch actions hold` with an `--instance`, `--kind`
or other exact selector, or `agent-dispatch actions inhibit worker-admission`
for the whole installation. Set the hold on every executing supervisor
installation, and on the coordinator for managed placement. These holds are
local to each installation and are not replicated. Ask the owner of each
installation your charter does not cover. If any path has no hold that its
owner can set, hold the window.

A hold does not stop an attempt admitted before it. Settle every in-flight
launch, redrive and admitted recovery operation before reporting the host
clear. Keep all holds through host-ready and Restore. Release them only after
Restore accounts for each stopped agent, so that a release cannot start a
second worker for it. A finite task requeued during the window is restored by
its supervisor after release; verify that it is live.
