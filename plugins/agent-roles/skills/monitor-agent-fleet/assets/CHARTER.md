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
  End condition: <finite stop criterion or explicit indefinite service + owner>

Steering scope (only if explicitly granted; else "none")
  Recipients: <owners/selectors allowed to receive steers>
  Permitted purposes: <e.g. return to parent, smaller discriminating experiment>
  Excluded purposes: <e.g. scope change, merge, deploy, stop/start, approvals>
  Validity: <expiry, revision or revocation condition>
  Limits: <one outstanding steer per target+issue; other caps>

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
- Static documentation tests do not qualify runtime behavior, delivery or
  rollout of this role.
