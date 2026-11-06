# Guidance delta

Back to [Monitor agent fleet](../SKILL.md).

Use this read-only assessment when an agent repeats an already documented
mistake, receives a correction that should already be guidance, or continues
a long task across relevant instruction changes. Do not run a whole-plugin
inventory before every action.

## Compare the guidance that could change the decision

Start with the root criterion and the concrete decision under investigation.
Locate the applicable repository instructions, enabled skill or declared
plugin guidance through its existing delivery owner. Read available current
guidance without imposing content-hash, version or provenance-receipt gates.

Record four independent observations:

| Layer | Useful evidence | Does not prove |
|-------|-----------------|----------------|
| Repository source | Actual checkout revision and relevant changed guidance against the resolved target branch | Installed plugin currency or model loading |
| Installed payload | Selected plugin's available instruction/skill content | The worktree's managed copy or running context uses it |
| Consumer copy | Applicable projected file, repository overlay and delivery availability | The running model loaded the file |
| Context delivery | The current context's observed instruction delivery or actual read; otherwise unknown | A future context will inherit that observation |

Git lag is not guidance lag. Many newer commits can be irrelevant, while one
changed architectural rule can invalidate the next experiment. Compare the
actual rule, applicability and consequence rather than ranking workers by
commit count. Distinguish missing guidance from contradictory guidance and from
an agent continuing against guidance it demonstrably read.

For a claim that an agent should already have known a rule, establish when the
rule was committed, when it reached the selected delivery source, and what read
or delivery evidence exists for the decision's context. A later source version
does not establish that earlier availability. Historical read evidence is a
dated observation, not a durable loaded-state flag.

## Keep one compact delta with the existing report

```text
Observed at: <time and source; live or historical>
Owner/root criterion: <existing references>
Source comparison: <checkout and resolved target; unavailable remains unknown>
Relevant rule delta: <paths and behavior that changes the next decision>
Payload / consumer copy: <separate available, different or unknown evidence>
Context delivery: <observed read/delivery reference and context, or unknown>
Consequence: <missed alternative, obsolete prerequisite, conflict, or none>
Next owner action: <no action, read applicable guidance, or proposed refresh>
```

Keep private identities and history in the private supervisory report. Do not
create a second guidance authority, proof registry or periodic scanner. Update
the delta when relevant source or delivery facts change, not on every timer.

## Recommend a safe refresh, not an automatic interruption

Unrelated changes need no pause. If available applicable guidance resolves the
decision, the owner can read it through the supported path without deploying a
new runtime. Missing or incompatible payloads require their own update owner;
pulling a repository is not a substitute.

When a relevant checkout refresh is needed, name the affected owner, source
changes, in-flight operation and safe checkpoint. Preserve dirty work, original
approvals, review/CI, resource custody and parent return. Do not change source
beneath an in-flight exact-source test or publication.

Only an authorized owner performs the repository's supported sync/update flow,
then resolves actual conflicts and refreshes guidance through the host's
documented context/restart mechanism. A pull or projection write alone is not
proof of reload. An anchor, reference checkout or foreign worktree may require
a different owner flow; never prescribe a forced pull or reset generically.
