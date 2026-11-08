# Fleet weak points and proposed fixes (template)

Back to [Monitor agent fleet](../SKILL.md).

Copy this template into the consuming repository, next to its fleet-monitor
adapter. **The consuming repository owns its filled-in catalogue**; this skill
owns only the template and the [self-improvement loop](../references/self-improvement.md)
that maintains it. Keep private identities and transcripts out of it: cite
evidence by date and pattern, not by session.

A living worklist. Each row is a recurring pattern seen across sweeps, with
the guidance fix that would let agents correct themselves. Promote a fix into
the owning skill, instruction, effort, charter or upstream plugin, mark it
`landed` with the reference, then prune it once it has held.

Status vocabulary: `observed` (no fix needed or not yet designed),
`proposed`, `proposed (upstream)`, `in progress`, `partly landed`, `landed`,
`mitigated` (worked around, root cause open), `open` (no recommendation yet).

## Agent guidance weak points

| Pattern | Evidence (first seen) | Proposed fix and owning guidance | Status |
|---|---|---|---|
| <pattern name> | <date; what the sweep observed> | <smallest guidance, effort or charter change; its owner> | <status> |

## Monitor blind spots

Weak points in the monitor's own charter, skill and readers (goal G4).

| Blind spot | Effect | Recommendation | Status |
|---|---|---|---|
| <unread venue, misleading signal, flaky or expensive reader> | <coverage or accuracy lost> | <reader fix, upstream change, or recorded gap> | <status> |

## Reader discovery log

Optional; keep it with the consumer's reader table if one exists. Append a
row whenever a sweep learns a better reader form, so the next monitor skips
that guess.

| Date | Wrong or missing form | Working form | How found |
|---|---|---|---|
| <date> | <command or assumption that failed> | <command that works> | <literal error, operator correction, review> |
