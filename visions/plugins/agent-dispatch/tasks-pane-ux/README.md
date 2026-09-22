# agent-dispatch Tasks-pane UX — Vision

- **Subject:** The Worktree Manager's **Tasks** pivot as a first-class,
  disciplined operator surface for the agent-dispatch task/worktree lifecycle
  — on par with the Worktrees pane's table discipline, not a plain badge list.
- **Scope:** leaf (a child of the
  [agent-dispatch](../README.md) plugin vision)
- **Status:** Draft
- **Last revised:** 2026-09-17
- **Reality docs:** `worktree-manager/src/worktree_manager/production_picker/picker_tui/engine.py`
  (`TasksView`, `WorktreesView`, `_TASK_PHASE_PALETTE`) ·
  `worktree-manager/src/worktree_manager/production_picker/picker_tui/pivots.py`
  (`RegisteredPivot`, `Column`, `PivotAction`) ·
  `plugins/agent-dispatch/src/agent_dispatch/` (`task_state_machine.py`,
  `board_cli.py`, `overrides.py`, `registrar.py`, `steering.py`,
  `worker_charter.py`) ·
  `efforts/active/agent-dispatch-tasks-pane-ux-overhaul/README.md`

## Purpose & Intent

The Worktrees pane earned its discipline the hard way: declarative columns, a
state-derived colour palette shared with the status bar, compact status
markers, and a rich per-row action menu. The Tasks pivot is the *other* half
of the same operator's mental model — "what is the fabric doing for me, and
what does it need from me" — but it never received the same investment. It
renders as a flat badge list with no phase colour, no visible link to the
worktree actually doing the work, no surfaced artifacts, and a thin action
set. An operator managing both worktrees and tasks today has one disciplined
view and one impoverished one, for what is conceptually the same lifecycle
viewed from two ends: a worktree is *where* work happens; a task is *why*.

This vision is the Tasks pivot growing into the Worktrees pane's peer: the
same colour discipline (reusing, not reinventing, the palette), an explicit
task↔worktree cross-link in both directions, a shared "prominent artifacts"
surface usable from either side, and a menu that lets the operator actually
**drive** the lifecycle — steer, abandon, reset, inspect a charter, drill into
the claiming worktree's live status, and hold or stop the agent working it —
without dropping to a CLI. It also extends "configuration is visible and
user-overridable" (already true for the enable/disable override store) into a
real **Registrars** viewer under the Configuration menu, so the master pause
is discoverable, not tribal knowledge.

## Concepts & Components

### The phase palette — one colour vocabulary, two lifecycles

Worktrees and tasks are different state machines (a worktree's git-derived
disposition vs. a task's `agent_dispatch.task_state_machine.Status` /
`agent-dispatch-board` group), but an operator's read of a colour should not
require knowing which machine produced it. A dedicated `task_phase` palette
(distinct from, but built from, the same named colours as the Worktrees
`state` palette) projects Proposed/Queued/Started/Blocked/Suspended/
Completed/Abandoned onto the same blue-actively-worked, amber-needs-attention,
green-done, grey-not-started, dark-grey-terminal meaning the Worktrees pane
already trained the operator to read at a glance. The two palettes are kept
separate so either vocabulary can evolve without silently reshading the
other.

### The task↔worktree cross-link — at most one, both directions

A task, once embodied, is claimed by exactly one worktree; the pivot
contract already carries this (`RegisteredPivot.worktree_field`, defaulting
to `entry.worktree`/`target_worktree`) but the Tasks pane never surfaced it as
a first-class, compact identity. Showing the claiming worktree's 4-digit id
directly in the Tasks table — and, symmetrically, the task's identity and
phase on the worktree side — makes the join legible without a mental hop
through two separate screens.

### Prominent artifacts — a shared claim-graph surface

Both a task and its claiming worktree accumulate a graph of claimed external
entities (pull requests, issues/bugs, and whatever else a registrar's
recipe claims). Rather than each side inventing its own artifact rendering,
this vision treats "1–2 prominent badges inline, full graph on drill-in" as
one shared surface, reachable from a task's menu *and* from the new Worktree
Status card (see below) — so a claim recorded from either angle reads the
same way.

### The Worktree Status card — the missing companion to Recent Messages

The Worktree Manager has long had a "Recent Messages" card stub for a
worktree, but no real **status** card: session lineage (which session
spawned, resumed, or succeeded which), an expanded descriptor (turn count,
commit count, claim count, live/idle/suspended), and the claims viewer. This
vision treats that card as a first-class citizen reachable from two places —
an embodied task's menu, and (eventually) a Worktrees-row drill-in — rather
than a Tasks-only special case, since the underlying worktree identity is
shared.

### The task menu — steer, view, and actually say "no"

Steering a blocked task already exists (`kind:"form"` + the `PivotFormScreen`
elicitation modal). This vision extends the SAME declarative action
mechanism — no new modal machinery needed for a read-only view, since
`kind:"card"` (`PivotCardScreen`) already exists — to cover: a charter viewer
(what this task *is*: description + structured metadata), the Worktree Status
card, and mutating verbs an operator needs to actually say "no": force-abandon
(already present), **reset to Proposed** (the gentler "not like this, but
don't kill the idea"), and, for an embodied task, **force-stop** the agent and
an explicit **pause** distinct from anything an agent would invoke on its own.

Pause is a genuine, durable **system state** — a per-task hold with its own
actor/reason/timestamp — not a UI-only flag, and not a reuse of the existing
system-recoverable `Suspended` state (which normal resume/recovery paths
already reopen on their own schedule). Every part of the dispatch flow
(claim, resume, wake-delivery, liveness recovery, release, supervisor
spawning) checks and honors the hold, not just the Picker's own display.
Pause's defining property is that it blocks agent-dispatch from re-queuing
the task until the operator explicitly unpauses it — a deliberate, visible
hold, distinct from both system-Suspended and Blocked/awaiting-steer, so a
forgotten pause reads as a stuck task demanding attention rather than a task
that silently vanishes from the active count or gets confused with an
unrelated system-recoverable state.

A task not yet embodied by a LIVE agent also gets **Open into a CLI
session** — a genuinely different embodiment path from a headless kick, not
"assign the pool's dedicated worker, then attach a terminal": it never
routes through the pool or a named worker identity's fixed
acting-theme/focus/rules — those rails exist to keep an *unattended*
headless agent on target, exactly what an operator opening a CLI session
does not want. Eligibility is fundamentally a **liveness** question, not a
raw-phase one: in principle a Started or Blocked task becomes eligible again
the moment its actual live session (CLI or headless) has died — the
dispatcher's own responsibility is to never let that state linger, by
auto-transitioning a task to Suspended the instant its active session/lease
is confirmed dead. A cheap phase-based approximation of "not currently live"
(Proposed / an unpooled Queued / Suspended) is then a correct, cheap read of
that invariant rather than a parallel, potentially-wrong check. Opening the
session runs a single atomic **interactive embodiment transaction**: create
the task's worktree if it has none (else resume its existing one), bind and
auto-claim the task to it under the same ownership/generation fencing a pool
claim would use, then seed the session with a succinct `--interactive`
context prompt (the task's charter, current phase, and its own tooling to
move the task's state). The agent is expected to set to work toward the
task's next phase, but — being genuinely non-railroaded — may pause and ask
the operator directly for instructions at any point; past that seed, the
operator has direct, open-ended control.

### Registrars configuration — the master pause, made visible

`agent_dispatch.overrides` already implements a durable, user-level
enable/disable store per registration (`set_override`/`clear_override`/
`overridden_off_ids`), and `agent_dispatch.registrar.ProfileDeclaration`
already carries everything worth showing about a registration (what recipe
or template it runs, its source repo or local-config origin, schedule and
concurrency limits, provenance). This vision closes the gap between "the
mechanism exists" and "the operator can see and use it": a Configuration →
Registrars view lists every registration with that metadata and lets the
operator flip the existing override — never add or remove a registration,
which stays an agent-chat-driven flow, since composing a new registration is
an authoring act, not a configuration toggle.

## Relationship to the Worktrees pane

This vision is deliberately **not** a rewrite of Worktrees, nor a merge of
the two panes into one. A worktree and a task are genuinely different units
(a worktree can exist with no task; a task can exist with no worktree yet)
and keeping them as sibling pivots that *cross-reference* rather than
*collapse* preserves that. What transfers is the **discipline** — palette,
compact status markers, a real action menu, a status-card drill-in — not the
schema.

## Non-Goals / Boundaries

- Re-deriving the task lifecycle's authoritative states; this vision reads
  `agent_dispatch.task_state_machine.Status` and `board_cli._group`, it does
  not invent a parallel vocabulary.
- A general-purpose claims/artifacts backend for arbitrary entity types; the
  scope here is surfacing whatever a registrar already claims, not building a
  new claim-tracking store.
- Repo-scoped **filtering** as a persisted, first-class UI axis (parallel to
  the Worktrees machine filter) — the repo *column* is in scope now; a
  dedicated filter chip is real, more invasive engine.py work, tracked as its
  own effort phase once the table design above is validated.

## See Also

- Parent vision: [plugins/agent-dispatch](../README.md)
- Child visions: none (leaf)
- Reality docs: `worktree-manager/src/worktree_manager/production_picker/picker_tui/engine.py` ·
  `worktree-manager/src/worktree_manager/production_picker/picker_tui/pivots.py` ·
  `plugins/agent-dispatch/src/agent_dispatch/` ·
  [`efforts/active/agent-dispatch-tasks-pane-ux-overhaul/README.md`](../../../../efforts/active/agent-dispatch-tasks-pane-ux-overhaul/README.md)
- Visual reference: the effort README's Journal (see its 2026-09-21 "Checked
  the Phase 0 preview screenshots" entry) documents the current local-only
  visual-regression workflow (`render_tasks_preview.py`, diffed by eye
  against the operator's own previously-saved capture) for this pane's
  concrete appearance — screenshots themselves are not committed to this
  repo (generated binary artifacts) and there is no repository-accessible
  or CI-enforced baseline for this tool today, so this workflow is
  single-operator only, not something another contributor or CI can run
  the diff for yet.
