# agent-roles

> Sustained agent role charters over existing owners -- not a task queue,
> daemon or grant of authority.

Some agent work is a standing **role** rather than a single task: keep watching
a fleet of agents and correct the ones going in circles, or keep reviewing a
repository's pull requests with several independent lanes. Each role is a
skill plus a fillable charter. The charter records the original operator
authority, scope, cadence, end condition and owner; the skill is the runbook.
Roles compose existing owners -- worktrees, sessions, history, messaging,
scheduling, task queues and PR tooling -- and never replace them.

Dispatch is one optional adapter, not a prerequisite: a role can run from a
native Task session, a Bridge peer, or an `agent-dispatch` task/recipe.

## Roles

| Role | Skill | Charter | What it covers |
|------|-------|---------|----------------|
| Fleet monitor ("task master") | [`monitor-agent-fleet`](skills/monitor-agent-fleet/SKILL.md) | [charter](skills/monitor-agent-fleet/assets/CHARTER.md) | Reconstruct each agent's root goal from bounded history, classify time honestly, diagnose misdirection patterns and, only within an explicit steering scope, send deduplicated corrective steers |
| Sustained PR reviewer | [`review-pull-requests`](skills/review-pull-requests/SKILL.md) | [charter](skills/review-pull-requests/assets/CHARTER.md) | Independent review lanes with a work-conserving concurrency target, retained per-PR context, repository-specific verdict/merge policy and existing agent-to-agent communication |
| Persistent issue triager | [`triage-issue-backlog`](skills/triage-issue-backlog/SKILL.md) | [charter](skills/triage-issue-backlog/assets/CHARTER.md) | Selected GitHub/ADO backlog and filter, durable harness/knowledge addenda, bounded read-only research, one publisher, evidence acceptance and change-driven revisits |
| Topology dispatcher | [`managing-agent-topology`](skills/managing-agent-topology/SKILL.md) | [charter](skills/managing-agent-topology/assets/CHARTER.md) | Durable complete assignments, native worktree/current-head routing, eligible placement, temporary work separation and qualified minimum role coverage |

A static [progress instruction](instructions/progress.instructions.md) gives
every agent in a consuming repository the worker-side kernel: name the unmet
acceptance criterion, prefer a discriminating experiment over a rerun, save the
parent return before a detour, and deliver once the actual gates pass.

## What it does (and how to use it)

| Request | Result |
|---------|--------|
| "Task master: audit the agents on these machines read-only" | Dated roster, inferred goals, history coverage, classified work/waits, matched misdirection patterns and ranked recommendations; **messages sent: none** |
| "Monitor this fleet under this charter; steer within this scope" | The same assessment on the charter's cadence, plus at most one persisted, deduplicated steer per target and issue to the resolved current head |
| "Correct the charters of last week's dormant worktrees" | Per-candidate verdicts from full chronology; clear record corrections written through their owners with a pre-write liveness recheck and readback, ambiguous cases in one decision packet; **messages sent: none** |
| "Set up sustained PR attention with three lanes" | A filled reviewer charter, qualified adapter/policy table, complete census and lane assignment through existing owners |
| "Run the persistent issue triager role for example/project" | Resolved target/filter, durable triager charter and applicable rubric/addenda; continuous assessment through existing adapters, with writes only under an original grant |
| "Run the persistent issue-triager role for Example Controls ADO backlog, Widget issues only" | Exact provider-supported organization/project/query/filter selection or one focused scope question; no broadened intake or inferred write permission |
| "Act as topology dispatcher for these machines and roles" | Private assignment map and complete charters; native allocation/Bridge launch only under original effect authority, same-owner follow-ups and evidence-based coverage |

Example read-only sweep result:

```text
worker-a: necessary prerequisite; failing controls eliminate different causes.
worker-b: blanket deadline inflation; measure per-test durations, then rerun
          only the residual selection. Original deadline remains the bar.
Coverage: one complete day, one partial history; no fleet-wide efficiency ratio.
Messages sent: none.
```

## What this plugin provides - and what it doesn't

**Provides:** role runbooks, fillable charters, a misdirection catalog with
distinguishing evidence, a steering protocol (current-head resolution,
persist-before-send, one outstanding steer per target and issue, delivery vs
consumption vs acceptance vs progress), offline behavioral scenarios, synthetic
coordination examples and one static progress instruction.
The topology role also supplies a versioned JSON Schema and standard-library-only,
offline validation/reporting helper; it never invokes a native adapter.

**Does not provide:** a task queue, reviewer engine, watcher, scheduler,
daemon, agent launcher, message transport, permission broker or rollout.
Enabling the plugin starts no monitor and sends no message. Recurring
observation, steering and dormant correction each require their own explicit
assignment.

For PR review, missing required authority, bindings or receipts remain explicit
adapter blockers or permitted manual handoffs. Optional server fencing is not a
universal prerequisite: use supported provider safeguards, fresh preflight,
revision binding and official readback without claiming atomicity. The skill
also supports bounded polling fallback, original-reviewer recovery and
work-conserving concurrency targets within actual authority and capacity.
Its loop discipline measures success as zero eligible PRs awaiting reviewer
action, starts rounds only on new input, never reposts an identical verdict on
an unchanged head, backs off unchanged watches, hands PRs back explicitly and
tells and releases a superseded reviewer.
The fleet monitor's watchdog checks inspect each agent's open questions,
obligations, prerequisite growth, budgets, validation weight, delivery state
and handoff fidelity, classify every wait as required, productive or waste,
and escalate once when a delivered steer stalls; the watchdog never asks a
blocking question itself.
Each sweep reports evidence against enforced goals (fleet discovery, on-charter
agents, fleet weak points, monitor weak points and, for recurring assignments
only, recurring until confident), and a self-improvement loop folds learnings
into the consumer's own weak-point catalogue and adapter.
A thread register classifies every operator message (one-shot, charter,
steer follow-up or delegated side-quest) so the monitor keeps its main
analysis thread and closes threads only on their exit criteria.
Its separately granted dormant correction mode fixes an inactive owner's
persisted charter (effort plan, workspace title/summary, agenda cursor or,
only through a verified record-only supersede, a pending baton) through each
record's owner. It never messages,
writes checkouts or claims, finalizes or retires work; live-idle, newly active
and unknown targets stay unchanged.

The issue triager is an agent-driven fallback, not an activated registrar or new
queue. It defaults to read-only without write authority, separates complete
assessments from partial evidence and software delivery, and keeps one
publication owner. Optional `.agent-roles/triage-issue-backlog.md` addenda in the
executing harness and bound knowledge workspace refine only applicable,
delegable parameters; target/provider owners retain rubric, access and safety.
An idle continuous pass stays Active. Restoring Dispatch does not automatically
transfer the charter or pending effects.

The topology dispatcher is a conversational allocator, not a worker. It records
complete assignments before native effects, discovers and adopts existing owners
in place when authorized, and retains allocation/launch identities across unknown
outcomes and restart. Adjustments and questions follow the assigned worktree's
current head; unrelated work gets a separate temporary assignment. Its offline
helper derives placement, coverage and routing from private intent and dated
observations without writing, networking, spawning or taking custody. Numeric
shortfalls and valid documents never grant launch or replacement authority.

## Dependencies & assumptions

No hard plugin dependencies and no runtime (`runtimeScope: none`). Roles name
optional sibling capabilities by skill when a host has them:
`agent-worktrees` and `agent-ssh` for workspace/session and cross-machine
reads, `agent-logger` for bounded history reading, `agent-bridge` for
cross-boundary messaging, `agent-dispatch` for task/recipe lifecycle,
`agent-pull-requests` for PR operations, `efforts` for canonical plans and
`agent-conduct-guidance` for peer rules and standing authority. A missing
sibling is a recorded coverage gap or adapter blocker, never an installation
step performed by the role.

Assumes an assigned target scope, authorized readers, canonical charter/effort
references where available, and a private writable report home.

## What's in this plugin

| Path | Purpose |
|------|---------|
| `skills/monitor-agent-fleet/SKILL.md` | Fleet monitor runbook |
| `skills/monitor-agent-fleet/assets/CHARTER.md` | Fleet monitor charter template |
| `skills/monitor-agent-fleet/assets/scenarios.json` | Offline behavioral cases, not live commands |
| `skills/monitor-agent-fleet/assets/WEAK-POINTS.md` | Weak-point catalogue template; the consuming repository owns its filled-in copy |
| `skills/monitor-agent-fleet/references/misdirection-patterns.md` | Patterns, distinguishing evidence and corrective steers |
| `skills/monitor-agent-fleet/references/steering.md` | Authorized steer protocol |
| `skills/monitor-agent-fleet/references/history-analysis.md` | Chunking, accounting and classifications |
| `skills/monitor-agent-fleet/references/guidance-delta.md` | Source, payload, consumer-copy and context-delivery differences |
| `skills/monitor-agent-fleet/references/interventions.md` | Testing, review and priority interventions |
| `skills/monitor-agent-fleet/references/fabric-readers.md` | Optional existing reader adapters |
| `skills/monitor-agent-fleet/references/dormant-correction.md` | Separately granted record correction for inactive owners |
| `skills/monitor-agent-fleet/references/self-improvement.md` | Improve-the-fleet and improve-the-monitor loops; capture, fold and route learnings |
| `skills/monitor-agent-fleet/references/thread-register.md` | Durable thread register: classify operator messages, delegate side-quests, close threads only on their exit criterion |
| `skills/review-pull-requests/SKILL.md` | Sustained PR reviewer runbook |
| `skills/review-pull-requests/assets/` | Reviewer charter and synthetic ledger example |
| `skills/review-pull-requests/references/` | Setup/adapters, coordination/recovery and communication |
| `skills/triage-issue-backlog/SKILL.md` | Persistent issue-triager procedure and target resolution |
| `skills/triage-issue-backlog/assets/` | Fillable charter and synthetic contract walkthroughs |
| `skills/triage-issue-backlog/references/` | Durable addenda/adapters, evidence, publication and continuity |
| `skills/managing-agent-topology/SKILL.md` | Portable conversational topology-dispatcher runbook |
| `skills/managing-agent-topology/assets/` | Complete charter template, version-1 JSON Schema and synthetic topology |
| `skills/managing-agent-topology/references/` | Native-adapter qualification, state/effect recovery and reporting limits |
| `skills/managing-agent-topology/scripts/` | Bounded offline validator, coverage/placement reports, same-owner routing and charter export |
| `instructions/progress.instructions.md` | Static worker-side progress guidance |
| `instruction-projections.json` | Managed instruction delivery declaration |
| `tests/` | Guidance regressions and offline topology behavior/CLI tests, not live fleet qualification |

## Troubleshooting, contributing & issues

- **Unreachable machine:** retain a current coverage gap. An already available
  archive supports explicitly historical analysis, not current liveness.
- **Large history:** use bounded chronological chunks and a coverage manifest;
  keyword samples are not a complete review.
- **Missing messaging or ambiguous owner:** report a recommendation, not a
  send, alternate recipient, repair or replacement session.
- **Repeated warnings:** wait for the agreed event or materially new evidence;
  the monitor must not become a second busy loop.

Static tests qualify documentation structure; topology tests additionally exercise
the actual offline helper and synthetic recovery flow. Neither qualifies native
launch, cross-machine delivery or installed fleet rollout. Run the contained
plugin packet with `python tools/run-plugin-tests.py agent-roles` from the
repository root. The topology module declares filesystem-only T1 behavior checks
and four T2 subprocess consumer scenarios. It does not enroll process tests or
otherwise expand the positively admitted required unit-CI packet.

Contributions follow the repository's
[CONTRIBUTING.md](../../CONTRIBUTING.md); report issues on this repository's
tracker.
