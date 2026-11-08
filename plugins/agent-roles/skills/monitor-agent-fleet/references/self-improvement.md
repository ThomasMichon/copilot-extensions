# Self-improvement loop

Back to [Monitor agent fleet](../SKILL.md).

This reference implements enforced goals G3 and G4. A monitor that only
corrects agents one at a time never runs out of work; the aim is a fleet that
stays on-charter without a monitor. Finding and recording improvements is
observation. Applying them is a source change that needs recorded
source-change and publication authority and follows each owner's contribution
flow.

## Improve the fleet, not just the agent (G3)

Every steer or waste finding is evidence that some guidance let an agent
drift. After each sweep:

1. Map each new finding to a pattern in the consuming repository's
   weak-point catalogue (created from [the template](../assets/WEAK-POINTS.md)).
   Reuse an existing row when the distinguishing evidence matches, and add
   the new evidence and date instead of a duplicate row.
2. Name the owning guidance -- a repository instruction, skill, effort plan,
   charter template or upstream plugin -- and propose the smallest change that
   would have let the agent correct itself.
3. Distinguish missing, contradictory and ignored guidance with the
   [guidance delta](guidance-delta.md); a rule the agent demonstrably read
   needs a different fix than one it never received.
4. Report "no new pattern" when every finding matches an existing row. A
   recurring row with a landed fix is a delivery question, not a new pattern.

Observed misdirections that apply to any repository belong in
[misdirection patterns](misdirection-patterns.md) through this skill's owner;
the catalogue tracks the consumer's own instances and fixes.

## Improve this monitor (G4)

Learning the fleet's readers is part of the role.

- **Reader corrections.** When a roster, status or transcript reader fails or
  turns out to be the wrong form, record the literal failure as that sweep's
  coverage gap, find the working form through its owner (its own help, the
  session's command catalog, an operator correction) and fix the consumer's
  reader table with a discovery-log row, so the next monitor skips that guess.
  A reader that starts, resumes or repairs a service is never the fix.
- **Exact credential references.** A health probe or reader that needs a
  credential names the exact credential reference (the precise store entry or
  identity, never a description of it) in the consumer's reader table. A probe
  that authenticated with the wrong credential is a reader correction, not a
  venue failure.
- **Monitor blind spots.** Unreachable venues, unread session types, flaky or
  expensive readers, misleading liveness signals and missing answer paths go
  in the catalogue's "Monitor blind spots" section with their effect and a
  recommendation.
- **Charter and skill gaps.** A goal the monitor could not evidence, a
  threshold that misfired or a default that had to be overridden is a finding
  against the charter or this skill, not just against the sweep.

## Capture during the sweep, fold at the end

Improvement work is part of the sweep, not a separate project. Keep it cheap:

1. **Capture in the ledger first.** During a sweep, write each learning as one
   line in the private supervisory record, tagged (for example `skill:`) with
   its kind: reader fix, blind spot or weak-point pattern, and recorded against
   the [thread register](thread-register.md)'s self-improvement thread. Never
   stop a sweep to edit guidance or open a change.
2. **Fold at sweep end.** Apply the tagged lines as small edits: reader fixes
   to the consumer's reader table and discovery log; drift patterns and blind
   spots to its weak-point catalogue; a changed default, override or goal to
   its adapter skill. Validate only the touched files with the consumer's own
   documentation checks.
3. **Ride one rolling change.** While the monitor's improvement change is
   unmerged, add new folds to it rather than opening another; batch updates
   once review has started, since a push can restart review. After it merges,
   open the next change only when the batch is worth a review -- a reader that
   unblocks coverage, or several tagged lines -- not one per line.
4. **Route by owner.** Consumer facts (hosts, readers, holds, local patterns)
   stay in the consumer's adapter and catalogue. A lesson that applies to any
   repository goes to this skill's owner as a batched, anonymized change, or
   an anonymized issue when the fix needs design. Mark the catalogue row
   `proposed (upstream)`, then `landed` once merged, rather than patching the
   generic text locally.
5. **Promote, then prune.** When a fix lands in its owning guidance, mark the
   row `landed` with its reference. Delete rows whose fix has been landed for a
   while so the catalogue stays a short worklist rather than a history.

Without source-change authority, steps 2-5 become proposed improvements in the
sweep report; the tagged ledger lines are the record.

## Ownership

This skill owns the loop and [the template](../assets/WEAK-POINTS.md). The
consuming repository owns its filled-in catalogue, reader table and adapter
overrides; this skill never stores a consumer's hosts, identifiers or
findings.
