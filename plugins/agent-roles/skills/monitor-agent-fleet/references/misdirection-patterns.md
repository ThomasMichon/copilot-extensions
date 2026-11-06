# Misdirection patterns

Back to [Monitor agent fleet](../SKILL.md).

These are recurring ways capable agents stay busy without advancing their root
goal. Each row names the **distinguishing evidence**; a pattern applies only
when that evidence is present. Legitimate necessary work can look similar from
status alone, so read the history before diagnosing.

| Pattern | Distinguishing evidence | Corrective steer |
|---------|-------------------------|------------------|
| Blanket deadline inflation | Repeated timeouts answered by doubling a whole-suite or tool deadline, with no per-test duration or limiting-stage measurement | Measure per-test/stage durations, fix or isolate the slow case, then run only the residual selection; keep the original acceptance deadline |
| Caution becomes an indefinite stop | Worker paused for "approval" that an existing recorded grant already covers within its bounds, or waits with no named decision owner | Cite the existing grant and bounds; continue within them, or name the exact missing scope as one decision packet |
| Later vision before first complete flow | Work on generalization, extensibility or a future milestone while the first end-to-end functional flow is unproven | Return to the first complete functional flow; park the later vision as a tracked item |
| Invented qualification rounds | Additional review, audit or "qualification" passes that no repository policy, owner or charter requires, after the actual gates pass | Name the actual required gates; deliver once they pass |
| Support tooling consumes the coordinator | A coordinator or supervisor spends its time building, fixing or polishing its own helper tooling while the coordinated work stalls | Return to coordination; record the tooling gap separately unless it is the real blocker |
| Returned handle mistaken for an active worker | A finished/returned task or reviewer handle is counted as an active lane, so capacity sits idle or obligations look covered | Consume the returned result, settle its obligations, and refill the vacancy |
| Finite queue swallowed by a deeper parent | A bounded prerequisite or work queue absorbed the session; the saved parent return action is missing or never executed | Restore the parent root and exact return action; apply the prerequisite's result to it |
| Wrong consumer or platform proof | Validation passes on a platform, fixture or launch mode that cannot exercise the reported real consumer (e.g. Linux for a native desktop lifetime) | Name the actual consumer contract; obtain proof on a compatible lane or prepare an acceptance packet |
| Unreconciled existing architecture | New mechanism built beside an existing owner, contract or adapter that already serves the purpose, without reading or reconciling it | Read the existing owner's contract; extend or reuse it, or record why it cannot serve |
| Implementation fixation despite measurements | Repeated tuning of one design after measurements show it cannot plausibly meet the declared budget or contract | Compare one other viable design before another broad qualification; required outcome changes need its owner |
| Unchanged repetition | Same command, source, environment and hypothesis rerun with the same result | One discriminating control at the failing boundary; see [interventions](interventions.md) |

## Not misdirection

- Failing runs that each eliminate a different documented hypothesis.
- Waiting on a genuinely required human review, CI result or operator decision.
- An explicitly indefinite service charter that keeps intake coverage and
  response latency within its agreed objective.
- Prerequisite work with a recorded parent criterion and return action.
- Changed operator intent that legitimately enlarged the goal.

## Using a match

Cite the specific turns, commands or results that satisfy the distinguishing
evidence, the root criterion they fail to advance, and the one changed next
action. Do not stack several patterns into a general critique. A steer that
lowers a required test, review, approval or resource bar is never a valid
correction for any pattern.
