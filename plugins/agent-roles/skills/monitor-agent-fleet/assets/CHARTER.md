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
  Roster source: <fixed list | re-resolved each cycle from the topology
    manifest and transport readiness: add ready, retire removed, keep
    unready as a gap>
  Exclusions: <owners, repositories or work never observed or steered>
  Model requirement: <selected model targets must run, or none; verify from
    runtime context/usage metadata, not per-turn model notices>
  Observation period: <time range or "from now">

Mode: read-only sweep | recurring observation | steering | dormant correction
  | repair (modes combine only when each has its own grant)
  Enforced goals: <G1-G4 and G6 for a finite sweep | G1-G6 for a recorded
    recurring assignment; see SKILL.md "Enforced goals">
  Cadence and wake source: <existing scheduler/event; required for recurring>
  Confidence cadence: <tight cadence used until confident, e.g. 15 minutes;
    recurring only>
  Healthy sweeps to relax: <consecutive healthy sweeps before leaving the
    confidence cadence, default 2; a sweep is healthy only when every target
    was read and classified with no waste disposition. A coverage gap or
    unknown target earns no healthy-sweep credit (unreadable state is never
    healthy) unless the charter explicitly excludes that venue; the
    unknown-state limit governs reporting, not health. Any waste finding
    returns to the confidence cadence>
  Active and parked cadences: <e.g. 30 minutes active, 2 hours parked>
  Adaptive cadence: <granted | not granted; when granted, switch by fleet
    health by replacing the single owned schedule, never running two>
  Early-check triggers: <question at half its revisit time, two observations
    without delta, validation over budget, unanswered change request past its
    limit, recorded deadline passed>
  End condition: <finite stop criterion or explicit indefinite service + owner;
    an open-ended assignment is never self-declared done>

Steering scope (only if explicitly granted; else "none")
  Recipients: <owners/selectors allowed to receive steers>
  Permitted purposes: <e.g. return to parent, smaller discriminating
    experiment, next acceptance criterion, verify and finalize, rework own
    charter/plan, backfill own charter>
  Excluded purposes: <e.g. scope change, merge, deploy, stop/start, approvals>
  Validity: <expiry, revision or revocation condition>
  Limits: <one outstanding steer per target+issue; other caps>

Answer scope (only if explicitly granted; else "none")
  Recipients: <owners/selectors whose parked questions may be answered>
  Permitted question classes: <routine decisions within the worker's own
    delegated authority, e.g. ordering, retry/validation depth, continue>
  Excluded: <permission/approval prompts, destructive, access/spend,
    objective change, deployment, raising caps or review-round limits>
  Validity: <expiry, revision or revocation condition>

Dormant correction scope (only if explicitly granted; else "none")
  Record kinds: <effort plan, workspace title/summary, agenda cursor,
    pending handoff baton (only via a verified record-only supersede)>
  Candidates: <inactive owners active in the observation period; finalized
    workspaces with open efforts included or not>
  Apply mode: <clear corrections direct, ambiguous batched | recommend only>
  Excluded effects: <messages, checkout/branch/claim writes, finalize,
    abandon, reset, approvals, deployment>
  Record publication: <monitor's own writable workspace and review gate>
  Validity: <expiry, revision or revocation condition>

Repair scope (only if explicitly granted; else "none"; see
    references/repair-escalation.md)
  Recipients: <owners/selectors whose blockers may be repaired>
  Rung ceiling: <R1 diagnose | R2 doctor records | R3 recover transport or
    session | R4 named disruptive effects>
  Host-shared services: <services that may be restarted, or "none">
  Direct access: <declared SSH profiles/transports usable for diagnosis>
  Delegation: <parallel sub-agent delegates allowed; concurrency bound; one
    per target or shared service>
  Excluded effects: <e.g. credential changes, reinstall, session replacement,
    lock or baton removal, owner-store hand edits>
  Validity: <expiry, revision or revocation condition>

Agent charter maintenance (see references/agent-charters.md)
  Summary home: <monitor-owned private directory, one summary per target>
  Backfill: <recommend only | steer agents to backfill | dormant backfill>
  Light-touch rule: <exploratory agents get no completion or rework pressure
    until set to work with a goal>
  Churn report: <per recurring sweep: finalized, allocated, aging targets>

Watchdog thresholds (see references/watchdog-checks.md)
  Question revisit time and required default: <e.g. 30 minutes; every
    question carries a recommended default>
  Prerequisite depth limit: <e.g. one level without a named failed boundary>
  Validation proportionality budget: <e.g. minutes per small, low-risk change>
  Open-obligation limits: <unanswered change request, approved without
    consent, unconsumed handoff>
  Delivery bar: <merged | released | deployed and live-verified, per root>
  Escalation: <one report to owner when a delivered steer stalls past its
    revisit time or a second steer goes unheeded>
  Unknown-state limit: <consecutive unknown observations before one report>

Self-improvement (see references/self-improvement.md)
  Weak-point catalogue: <consuming repository's own filled-in catalogue,
    created from assets/WEAK-POINTS.md; unknown until one exists>
  Adapter and readers: <consuming repository's adapter skill / reader table>
  Source-change and publication authority: <none | scope, destination and
    rolling-change policy for folding learnings>
  Upstream route: <owner of this generic skill; anonymized issue or change>

Holds and later narrowing: <references, newest first>
Thread register: <monitor-owned notes file referenced from the checkpoint;
  see references/thread-register.md. Seeded with the main and
  self-improvement threads>
Acceptance: <what a completed sweep or a healthy service period must show>
```

## Acceptance checks

- The report shows evidence for every applicable enforced goal (G1-G4 and
  G6, plus G5 under a recorded recurring assignment); a missing goal marks the
  sweep incomplete, and an open-ended assignment was never self-declared done.
- Every waste finding and steer maps to a weak-point pattern or "no new
  pattern", and monitor difficulties were captured as proposed improvements.
- Every target resolves through an existing owner, or is a recorded gap.
- A recurring cycle with a re-resolved roster records its manifest source,
  readiness results and machines added, retired or kept as gaps.
- Every finding cites evidence and a coverage manifest; inferred purpose is
  marked with confidence.
- **Messages sent** is `none` in read-only mode, and each steer in steering mode
  has a persisted pending record before send and a recorded result after.
- Every parked question was reported or classified; each answer was within
  the answer scope, persisted before sending and rechecked as still pending.
- Each dormant correction had a liveness recheck and revision re-read
  immediately before the write and an owner readback after; live-idle, newly
  active and unknown targets were left unchanged.
- Each repair stayed within the repair scope's recipients and rung ceiling,
  climbed one rung at a time with evidence captured first and a readback
  after, and each parallel delegate held one target or shared service and
  never messaged or steered a worker.
- Every target has a current charter summary with its kind; exploratory
  targets received no completion pressure, and every completion target shows
  its remaining acceptance and the resolution step due.
- Holds, review bars, approvals, resource limits and ownership are unchanged
  by the monitor.
- The monitor asked no blocking question; every wait it reports is classified
  required, productive or waste with evidence.
- Each escalation was reported once with evidence, a recommended decision and
  its default, after the steer ladder or for a decision only the operator owns.
- Every operator message was classified in the thread register; each reply
  named the threads that moved, side-quests were delegated with a recorded
  owner, and no thread closed without its exit criterion.
- Static documentation tests do not qualify runtime behavior, delivery or
  rollout of this role.
