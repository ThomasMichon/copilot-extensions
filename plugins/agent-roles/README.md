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
| Fleet monitor ("task master") | [`monitor-agent-fleet`](skills/monitor-agent-fleet/SKILL.md) | [charter](skills/monitor-agent-fleet/assets/CHARTER.md) | Keep assigned agents on task and drive them to resolution against outcome-shaped root goals (operator-defined goals verbatim first): judge alignment and projected progress, keep a summarized per-agent charter, nudge agents to establish goals and lodge them in their own charters under standing bounded authority, diagnose misdirection, and only within explicit scopes send other deduplicated steers or delegate graduated parallel repair of blocked agents |
| Infrastructure maintainer | [`maintain-agent-infrastructure`](skills/maintain-agent-infrastructure/SKILL.md) | [charter](skills/maintain-agent-infrastructure/assets/CHARTER.md) | Maintain agent-hosting machines and tooling: worktree-owned release observation, separately authorized per-machine updates and original-session recovery through existing owners; no permanent anchor chat |
| Sustained PR reviewer | [`review-pull-requests`](skills/review-pull-requests/SKILL.md) | [charter](skills/review-pull-requests/assets/CHARTER.md) | Independent review lanes with a work-conserving concurrency target, retained per-PR context, repository-specific verdict/merge policy and existing agent-to-agent communication |
| Persistent issue triager | [`triage-issue-backlog`](skills/triage-issue-backlog/SKILL.md) | [charter](skills/triage-issue-backlog/assets/CHARTER.md) | Selected GitHub/ADO backlog and filter, durable harness/knowledge addenda, bounded read-only research, one publisher, evidence acceptance and change-driven revisits |
| Finite backlog consolidator | [`consolidate-issue-backlog`](skills/consolidate-issue-backlog/SKILL.md) | [charter](skills/consolidate-issue-backlog/assets/CHARTER.md) | Duplicate/overlap cohorts, complete canonical receiving, evidenced dispositions and a whole-queue or selected-cohort target with retained ownership |
| Topology dispatcher | [`managing-agent-topology`](skills/managing-agent-topology/SKILL.md) | [charter](skills/managing-agent-topology/assets/CHARTER.md) | Durable complete assignments, native worktree/current-head routing, eligible placement, temporary work separation and qualified minimum role coverage |

**Agent progress vs infrastructure health.** These are separate roles, not
synonyms or candidates for skill consolidation. Do not merge these roles:

| | `monitor-agent-fleet` | `maintain-agent-infrastructure` |
|---|---|---|
| Subject | Agents and their goals | Machines and tooling |
| Asks | Is each agent on task and moving toward its finish line? | Are hosts and tool releases current and healthy? |
| Effects (each separately granted) | Steers, per-agent charter upkeep, repair of one blocked agent's bridge, identity or session state | Release rollout, per-machine updates, recovery of sessions after a disruption |
| Hands off | Host-wide or release faults to the maintainer | Agent progress and direction to the monitor |

A status command that self-heals or resumes services belongs to the maintainer.
Neither role inherits the other's authority.

**Name migration:** `maintain-agent-infrastructure` replaces the former
`maintain-agent-fleet` skill name and path; no second skill or invocation alias
is shipped under the old name. Update consumer skill links and future role
assignments after the renamed payload is installed. For an existing assignment,
reconcile its role label and expected/accepted charter revision through its
current owner; preserve the worktree, original grants, holds and pending effects.
Do not recreate an agent, combine charters or rewrite historical records for
this naming change. Until the new payload is available, retain the existing
owner rather than attempting an unsupported invocation.

A static [progress instruction](instructions/progress.instructions.md) gives
every agent in a consuming repository the worker-side kernel: name the unmet
acceptance criterion, prefer a discriminating experiment over a rerun, save the
parent return before a detour, and deliver once the actual gates pass.

## What it does (and how to use it)

| Request | Result |
|---------|--------|
| "Task master: audit the agents on these machines read-only" | Dated roster, inferred goals, history coverage, classified work/waits, matched misdirection patterns and ranked recommendations; **messages sent: none** |
| "Monitor this fleet under this charter; steer within this scope" | The same assessment on the charter's cadence, plus at most one persisted, deduplicated steer per target and issue to the resolved current head |
| "Unstick this blocked agent; repair up to rung R2" | Read-only diagnosis first, then one parallel sub-agent delegate per target climbing the repair ladder only to the granted ceiling; host-wide faults routed to the maintainer |
| "Correct the charters of last week's dormant worktrees" | Per-candidate verdicts from full chronology; clear record corrections written through their owners with a pre-write liveness recheck and readback, ambiguous cases in one decision packet; **messages sent: none** |
| "Set up sustained PR attention with three lanes" | A filled reviewer charter, qualified adapter/policy table, complete census and lane assignment through existing owners |
| "Run the persistent issue triager role for example/project" | Resolved target/filter, durable triager charter and applicable rubric/addenda; continuous assessment through existing adapters, with writes only under an original grant |
| "Run the persistent issue-triager role for Example Controls ADO backlog, Widget issues only" | Exact provider-supported organization/project/query/filter selection or one focused scope question; no broadened intake or inferred write permission |
| "Consolidate an issue backlog to this finite target, read-only first" | Explicit target/filter and counting rule, qualified census, requirement-preserving receiver proposals and retained gaps; no closure without original effect authority |
| "Act as topology dispatcher for these machines and roles" | Private assignment map and complete charters; native allocation/Bridge launch only under original effect authority, same-owner follow-ups and evidence-based coverage |
| "Assign an infrastructure maintainer" / "maintain-and-update-fleet" | Durable worktree charter, bounded release/health census and rollout/recovery plan; read-only unless original authority covers the selected effects |

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

The infrastructure maintainer keeps machine/tooling health separate from the monitor's
agent-progress steering. Its worktree-owned charter survives handoffs and records
bounded update/cutover/resume authority, operator holds and ask-before actions.
It composes the existing installation, machine-state, credential and session
owners; it does not replace them or promise interruption-free updates.
Merged, released, installed and live-verified remain separate facts.
Topology dispatch can assign this standing role with scoped minimum coverage
through its existing complete-charter contract; a live process alone is not
qualified coverage.

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

The backlog consolidator is a separate finite role, not continuous per-issue
triage or implementation. It defaults to read-only proposals, records the
adopter's actual authorship and acquisition policy, and preserves every
unresolved requirement before authorized source retirement. No numeric queue
target, collaborator restriction or event exception is universal. A new
activation supplies its own scope and authority; it never resets old limits.
Reaching the verified target stops extra cohorts, while final review, journal
and genuinely resolved own-resource gates still apply.

The topology dispatcher is a conversational allocator, not a worker. It records
complete assignments before native effects, discovers and adopts existing owners
in place when authorized, and retains allocation/launch identities across unknown
outcomes and restart. Adjustments and questions follow the assigned worktree's
current head; unrelated work gets a separate temporary assignment. Its offline
helper derives placement, coverage and routing from private intent and dated
observations without writing, networking, spawning or taking custody. Numeric
shortfalls and valid documents never grant launch or replacement authority.
Its read-only `reconcile` report separates uncovered demand from retained owners,
pending original effects and unsettled retirement. It proposes owner coordination
or in-place adoption before new assignments; stale, dormant and off-role owners
are never replacement slots.

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
For fleet maintenance, `agent-machines` optionally owns declarative state,
unreachable-machine handoffs and native CLI update controls; the repository's
installation owner retains runtime rollout. Neither companion is mandatory.

Assumes an assigned target scope, authorized readers, canonical charter/effort
references where available, and a private writable report home.

## What's in this plugin

| Path | Purpose |
|------|---------|
| `skills/monitor-agent-fleet/SKILL.md` | Fleet monitor runbook |
| `skills/monitor-agent-fleet/assets/CHARTER.md` | Fleet monitor charter template |
| `skills/monitor-agent-fleet/assets/AGENT-CHARTER.md` | Per-agent summarized charter template kept in the monitor's private state |
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
| `skills/monitor-agent-fleet/references/repair-escalation.md` | Graduated, separately granted repair of blocked agents via parallel sub-agent delegates |
| `skills/monitor-agent-fleet/references/agent-charters.md` | Per-agent charter upkeep, backfill, light touch and drive-to-resolution |
| `skills/monitor-agent-fleet/references/thread-register.md` | Durable thread register: classify operator messages, delegate side-quests, close threads only on their exit criterion |
| `skills/review-pull-requests/SKILL.md` | Sustained PR reviewer runbook |
| `skills/review-pull-requests/assets/` | Reviewer charter and synthetic ledger example |
| `skills/review-pull-requests/references/` | Setup/adapters, coordination/recovery and communication |
| `skills/triage-issue-backlog/SKILL.md` | Persistent issue-triager procedure and target resolution |
| `skills/triage-issue-backlog/assets/` | Fillable charter and synthetic contract walkthroughs |
| `skills/triage-issue-backlog/references/` | Durable addenda/adapters, evidence, publication and continuity |
| `skills/consolidate-issue-backlog/SKILL.md` | Finite bulk-consolidation runbook, distinct from individual triage |
| `skills/consolidate-issue-backlog/assets/CHARTER.md` | Explicit target, eligibility, authority, read limits and closeout template |
| `skills/consolidate-issue-backlog/assets/scenarios.json` | Synthetic census, provenance, acquisition, receiving, recovery, ownership and finite-closeout walkthroughs |
| `skills/consolidate-issue-backlog/references/` | Cohort/acquisition discipline, canonical receiving, dispositions and continuity |
| `skills/managing-agent-topology/SKILL.md` | Portable conversational topology-dispatcher runbook |
| `skills/managing-agent-topology/assets/` | Complete charter template, version-1 JSON Schema and synthetic topology |
| `skills/managing-agent-topology/references/` | Native-adapter qualification, state/effect recovery and reporting limits |
| `skills/managing-agent-topology/scripts/` | Bounded offline validator, coverage/placement reports, same-owner routing and charter export |
| `skills/maintain-agent-infrastructure/SKILL.md` | Worktree-owned infrastructure maintenance runbook |
| `skills/maintain-agent-infrastructure/assets/CHARTER.md` | Bounded authority, target scope, rollout/recovery and continuity template |
| `skills/maintain-agent-infrastructure/references/rollout-and-recovery.md` | Release/install evidence, dependency checks and original-session recovery |
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
