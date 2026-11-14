# Agent charter summary

Back to [Monitor agent fleet](../SKILL.md) and
[per-agent charters](../references/agent-charters.md).

The monitor keeps one copy of this summary per observed target in its own
private state. The agent's own charter, in its effort, checkpoint or workspace
record, remains authoritative. Leave a field `unknown` rather than inventing
it, and mark inferred fields with a confidence.

```text
Workspace: <stable workspace identity; host; project>
Current head: <session reference and observation time>
Kind: completion | standing | exploratory  (evidence; confidence)
Set to work: <operator source and time the goal was set, or "not yet">
Original source: <operator request or assignment reference>
Root: <end-state outcome true when done, or the steady state a standing
  service maintains; never the current activity>
Root source: operator-defined (verbatim; reference) | agent-stated (record
  location) | monitor draft (confidence) | missing
Acceptance: <remaining real completion criteria>
Finalization criterion: <what lets the agent finalize, or "indefinite">
Agent's own charter: <location, or "missing: backfill <recommended | sent | applied>">
Divergence: <monitor summary vs agent charter, with sources, or "none">
Now: <current slice and why it serves the root>
Alignment: <aligned | detour | tangent | unknown: recent work vs Root, with evidence>
Projected progress: <converging | stalled | receding | unknown: remaining
  acceptance against recent deltas>
Last delta: <result and time>
Blocker: <condition and owner; repair rung if any, or "none">
Outstanding steer: <key and stage, or "none">
Last reconciled: <time and sources read>
```
