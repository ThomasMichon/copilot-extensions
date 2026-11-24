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
expected duration. During Quiesce, mark each agent the monitor stopped as
`stopped for maintenance` and tell the maintainer the host is clear. After the
maintainer declares host-ready, Restore resumes each of those agents and
verifies each one is live before the row leaves that state.

When the monitor runs on the affected host, keep this window state where the
surviving coordinator or native handoff can read it, not only on that host.
Before reporting the host clear, revoke or hold any agent-dispatch standing
recovery grant for agents on the host, and confirm no admitted recovery
operation is still pending, as described in the agent-dispatch
standing-charters guide.
