# Fleet monitor charter

Back to [Monitor agent fleet](../SKILL.md).

Copy this template into the existing canonical effort, supervisory record or
current-owner checkpoint. Leave a field `unknown` rather than inventing it.
A filled charter records authority; it never creates authority that the
original operator source did not grant.

```text
Original operator source: <exact reference and necessary wording>
Charter owner: <session/worktree that owns this role; transfer requires acceptance>
Report home: <private destination for findings>

Scope
  Targets: <machines/workspaces/owners, resolved through topology readers>
  Exclusions: <owners, repositories or work never observed or steered>
  Observation period: <time range or "from now">

Mode: read-only sweep | recurring observation | steering
  Cadence and wake source: <existing scheduler/event; required for recurring>
  Active and parked cadences: <e.g. 30 minutes active, 2 hours parked>
  Early-check triggers: <question at half its revisit time, two observations
    without delta, validation over budget, unanswered change request past its
    limit, recorded deadline passed>
  End condition: <finite stop criterion or explicit indefinite service + owner>

Steering scope (only if explicitly granted; else "none")
  Recipients: <owners/selectors allowed to receive steers>
  Permitted purposes: <e.g. return to parent, smaller discriminating experiment>
  Excluded purposes: <e.g. scope change, merge, deploy, stop/start, approvals>
  Validity: <expiry, revision or revocation condition>
  Limits: <one outstanding steer per target+issue; other caps>

Watchdog thresholds (see references/watchdog-checks.md)
  Question revisit time and required default: <e.g. 30 minutes; every
    question carries a recommended default>
  Prerequisite depth limit: <e.g. one level without a named failed boundary>
  Validation proportionality budget: <e.g. minutes per small, low-risk change>
  Open-obligation limits: <unanswered change request, approved without
    consent, unconsumed handoff>
  Delivery bar: <merged | released | deployed and live-verified, per root>
  Escalation: <two unheeded steers per issue, then one report to owner>
  Unknown-state limit: <consecutive unknown observations before one report>

Holds and later narrowing: <references, newest first>
Acceptance: <what a completed sweep or a healthy service period must show>
```

## Acceptance checks

- Every target resolves through an existing owner, or is a recorded gap.
- Every finding cites evidence and a coverage manifest; inferred purpose is
  marked with confidence.
- **Messages sent** is `none` in read-only mode, and each steer in steering mode
  has a persisted pending record before send and a recorded result after.
- Holds, review bars, approvals, resource limits and ownership are unchanged
  by the monitor.
- The monitor asked no blocking question; every wait it reports is classified
  required, productive or waste with evidence.
- Each escalation was reported once with evidence, a recommended decision and
  its default, after the steer ladder or for a decision only the operator owns.
- Static documentation tests do not qualify runtime behavior, delivery or
  rollout of this role.
