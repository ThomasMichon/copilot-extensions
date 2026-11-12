# Agenda Adoption by Default

- **Slug:** `agenda-adoption-by-default`
- **Repo:** copilot-extensions (`efforts`, `agent-worktrees` plugins)
- **Branch(es):** per-slice PRs against `dev`
- **Created:** 2026-11-07
- **Status:** Done

## Guiding Intent

Effort agendas exist, but in practice no worktree has adopted one: the managed
initialization ceremony is long, nothing tells a session that an agenda is
expected, and a session already deep in its work has no route into one. Make
agendas the ordinary default where an adopter opts in, while keeping the
active agent in control of its own agenda: a strong nudge and a short path,
never a rigid lock or an automatic mutation.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Agenda-adoption host agent | All slices (efforts helpers, agent-worktrees switch and guidance, samples) | the host's copilot-extensions worktree (named in its private checkpoint) |

## Coordination

- **Topology:** independent per-slice PRs against `dev`
- **Host (owns PRs):** agenda-adoption host agent
- **Delegates:** none
- **Handoff:** context-handoff brief and checkpoint of the host worktree; a successor resumes the next open Plan item

## Context

- Agenda skill and managed CLI: `plugins/efforts/skills/driving-effort-agendas/`.
  Fresh context-qualified lanes need a request-v2 `init` with items, exits,
  outcomes, baseline context and four resource caps, plus placement through
  `agent_worktrees worktree-data-allocate` (`scripts/agenda_managed.py`).
- Display graph and Companion git-graph rendering already landed; a session
  without a published agenda shows no Agenda tab.
- Session-scoped guidance pattern: `docs/patterns/session-scoped-dynamic-guidance.md`
  (sessionStart writes a session-folder file; a static pointer projection
  directs the agent to read it).
- Four-tier configuration: `docs/harness-and-knowledge.md`; agent-worktrees
  portable knowledge preferences: `plugins/agent-worktrees/docs/config-reference.md`.
- `effort_digest.py` already extracts open Plan / Validation Plan items.

## Request

> Let's look into adding a config switch that can be set in a harness/knowledge
> repo that enables the agenda system by-default: we'll want to use
> session-guidance to tell an agent it's using an agenda for the session, and
> potentially use a sessionStart hook to invoke the initial commands. We should
> also look into creating a skill to "backfill" an agenda for an agent's
> session, as well as extend the skillset to support deriving an agenda from
> the remainder of an effort. We do still want to allow the active agent to
> control the agenda, so it's not an ultra-rigid lock. But we should really
> nudge our agents into adopting the system, even for existing, long-running
> sessions. And I'd love to be able to look over some older, completed
> sessions to see how their backfilled agendas look.

## Plan

### Phase 1 — Short paths into an agenda (efforts)
- [x] Simple outline format: a compact list of items (action + exit) that
      expands into a valid request-v2 `init` with default outcomes and caps.
- [x] Derive an outline from the remainder of an effort (open Plan /
      Validation Plan items via `effort_digest`).
- [x] Backfill an outline from a Copilot session's recorded history
      (user requests, completed work, open work), marked as reconstructed.
- [x] Offline preview: render any outline as the same display graph used by
      the Companion, without a store or a running service.
- [x] Skill guidance for deriving and backfilling agendas.

### Phase 2 — Default-on switch and session guidance (agent-worktrees)
- [x] Portable `agendas` preference (four-tier, knowledge-graftable), default off.
- [x] Session guidance reports the effective policy and this worktree's
      agenda lane state, with the next command to run.
- [x] Static instruction: an agenda is expected; the agent authors and
      controls it; adoption is never automatic mutation.
- [x] Recurring nudge for sessions without an agenda when the policy is on,
      including already-running sessions.

### Phase 3 — Samples and rollout notes
- [x] Backfill and render several completed sessions for operator review.
- [x] Document enabling the switch from a knowledge repository.

## Validation Plan

- [x] Unit tests: outline expansion is accepted by the managed `init`
      validator; effort derivation and session backfill are deterministic on
      fixtures; preview output is a valid graph.
- [x] Unit tests: preference composition across tiers; guidance and nudge
      appear only when enabled.
- [x] Sample backfills of real completed sessions reviewed by the operator.

## Proposal

Pending: no separate proposal; the Plan is source-only and lands through reviewed PRs.

## Journal

### 2026-11-07 — Kickoff
- Effort created. Inventory showed no registered agenda lanes in active
  projects; initialization ceremony identified as the main barrier.

### 2026-11-07 — Phase 1 merged, Phase 2 in review
- Outline format, effort-remainder derivation, session backfill, offline
  preview and `adopt` landed with skill guidance and tests (#11396).
- Phase 2 adds the `agendas.default` preference (`off` | `expected`),
  an "Agenda:" session-guidance line and a throttled adoption nudge.

### 2026-11-07 — Phase 2 merged, Phase 3 complete; effort done
- Phase 2 merged (#11414): `agendas.default` composes across the four config
  tiers (settable in a knowledge repository), session start/resume adds the
  "Agenda:" guidance, and the post-tool reminder reads an owner-published
  policy record honoring `AGENT_HOME`.
- Running sessions: `agent-worktrees status` writes republish the policy, so an
  enabled default reaches an already-running session at its next disposition
  write (prompted by the disposition nudge); covered end to end by
  `test_running_session_sees_enabled_agenda_default_after_status_write`.
- Phase 3: sample backfills of completed sessions were rendered and reviewed by
  the operator; knowledge-repository enablement is documented in
  `plugins/agent-worktrees/docs/config-reference.md#default-on-agendas` and
  `docs/worktree-agendas.md#default-on-policy`.
- Rollout to installed machines follows the normal release promotion; enabling
  `agendas.default: expected` in a knowledge repository is an operator choice.
