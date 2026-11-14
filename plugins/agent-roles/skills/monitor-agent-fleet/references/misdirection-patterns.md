# Misdirection patterns

Back to [Monitor agent fleet](../SKILL.md).

These are recurring ways capable agents stay busy without advancing their root
goal. Each row names the **distinguishing evidence**; a pattern applies only
when that evidence is present. Legitimate necessary work can look similar from
status alone, so read the history before diagnosing.

| Pattern | Distinguishing evidence | Corrective steer |
|---------|-------------------------|------------------|
| Activity mistaken for goal | The agent's stated root, effort Goal or handoff objective describes its current slice, PR, experiment or role activity rather than an observable end state, so no acceptance evidence can say it is done | A goal-establishment steer: lodge the outcome-shaped root (the operator-defined goal verbatim when one exists, else a confidence-marked draft from the original source) with acceptance evidence, then re-derive the next action from it |
| Blanket deadline inflation | Repeated timeouts answered by doubling a whole-suite or tool deadline, or a request to raise a timeout, cap or retry limit, with no per-test duration or limiting-stage measurement and no check of whether the work itself is excessive | Measure per-test/stage durations, fix or isolate the slow case, then run only the residual selection; keep the original acceptance deadline |
| Unbounded question | A question to the operator with no recommended default, no revisit time, and no tool activity on other authorized work while it is open, or a question an existing recorded grant already answers | Restate once with a recommended default and revisit time; continue other authorized work meanwhile; never treat silence as consent for a gated effect |
| Self-added prerequisite | A new prerequisite, child PR or detour that names no failed parent boundary, often opened right after the previous one merged, with depth growing past the charter's limit | Name the failed boundary it unblocks or park it as a tracked item; execute the saved parent return action |
| Merged treated as done | Completion reported at merge while the root requires live behavior, with no deployment, installed-version check or live test recorded | Name the remaining rollout and live check, or record "merged, pending rollout" with its owner; deployment still needs its own authority |
| Handoff rewrites intent | The newest handoff's objective drops, narrows or contradicts the verbatim original request, or adopts a peer-relayed hold over the operator's instruction | Restore the verbatim original request as the scope contract; a relayed hold needs the original scope owner's authority |
| Disproportionate validation | Validation time or breadth far exceeding a small, low-risk change, such as full suites rerun on an unchanged code state | Run the smallest affected selection once per code state; keep only genuinely required gates |
| Superseded agent untold | A role was reassigned, but the predecessor is still running, watching or holding claims and was never told | Tell the predecessor and release its overlapping task, claims and watches through their owners |
| Over-narrow self-proposed grant | Repeated authorization requests for the same operation, target and policy that differ only in a pinned run, attempt or commit, so each rerun or rebase reopens the question | Propose one grant over the operation class, target selector and bounded retry/revision loop through the standing-authority procedure; the operator decides |
| Caution becomes an indefinite stop | Worker paused for "approval" that an existing recorded grant already covers within its bounds, or waits with no named decision owner | Cite the existing grant and bounds; continue within them, or name the exact missing scope as one decision packet |
| Later vision before first complete flow | Work on generalization, extensibility or a future milestone while the first end-to-end functional flow is unproven | Return to the first complete functional flow; park the later vision as a tracked item |
| Invented qualification rounds | Additional review, audit or "qualification" passes that no repository policy, owner or charter requires, after the actual gates pass | Name the actual required gates; deliver once they pass |
| Support tooling consumes the coordinator | A coordinator or supervisor spends its time building, fixing or polishing its own helper tooling while the coordinated work stalls | Return to coordination; record the tooling gap separately unless it is the real blocker |
| Returned handle mistaken for an active worker | A finished/returned task or reviewer handle is counted as an active lane, so capacity sits idle or obligations look covered | Consume the returned result, settle its obligations, and refill the vacancy |
| Finite queue swallowed by a deeper parent | A bounded prerequisite or work queue absorbed the session; the saved parent return action is missing or never executed | Restore the parent root and exact return action; apply the prerequisite's result to it |
| Wrong consumer or platform proof | Validation passes on a platform, fixture or launch mode that cannot exercise the reported real consumer (e.g. Linux for a native desktop lifetime) | Name the actual consumer contract; obtain proof on a compatible lane or prepare an acceptance packet |
| Unreconciled existing architecture | New mechanism built beside an existing owner, contract or adapter that already serves the purpose, without reading or reconciling it | Read the existing owner's contract; extend or reuse it, or record why it cannot serve |
| Implementation fixation despite measurements | Repeated tuning of one design after measurements show it cannot plausibly meet the declared budget or contract | Compare one other viable design before another broad qualification; required outcome changes need its owner |
| Unchanged repetition | Same command, source, environment and hypothesis rerun with the same result, or an identical review verdict republished on an unchanged head | One discriminating control at the failing boundary; see [interventions](interventions.md). For review rounds, keep the standing verdict until a qualifying trigger |
| Reported failure ends the turn | The agent reports a failure or blocker and then ends its turn, with no diagnosis, retry, recorded default or handoff, while other authorized work or a next diagnostic step remains | A reported failure does not finish the turn: diagnose within scope, take the next discriminating step or a recorded default, or hand off with the failure named |

The [watchdog checks](watchdog-checks.md) map each pattern to a per-agent
inspection, a wait class and an escalation rule.

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
