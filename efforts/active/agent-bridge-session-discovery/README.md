# agent-bridge Session Discovery — Any-Repo / Any-Machine

- **Slug:** `agent-bridge-session-discovery`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `main`
- **Created:** 2026-09-12
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Umbrella issue:** [#2530](https://github.com/ThomasMichon/copilot-extensions/issues/2530)
  (agent-bridge: no session discovery across same-machine/any-repo or
  any-machine/same-repo)
- **Vision:** **vision-closing** — extends two already-stated features with no
  revision needed to their intent:
  - [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
    — §*Features*/`topology-aware-agent-resolution` ("Named agents and
    sessions resolve through project context, worktree state, machine
    topology... while the caller sees one catalog and one resolution
    contract") and `reach-active-worktrees-and-configured-repos` ("A caller
    can address agents in active worktrees and in configured projects the
    fabric knows how to resolve... The reachable set is a catalog, not a
    collection of one-off connection recipes.").
  - [`visions/agent-fabric`](../../../visions/agent-fabric/README.md) —
    §*Features*/`discover-before-duplicate` ("is an agent or worktree already
    covering this repo/target?").
  - Note: PR #2490 added `cache-is-a-hint-never-authority` to the same
    agent-bridge vision this session; that is a sibling concern (state
    honesty of a resolved target), not this one (locating a target at all).

## Guiding Intent

Complete agent-bridge's addressing model along the two axes it currently
can't serve: **same-machine, any-repo** (what else is running on this box,
regardless of project) and **any-machine, same-repo** (where — if anywhere —
is a live session already working this repo). Today a caller can only ask
"what's running for *my* project on *my* machine" or reach an *explicitly
named* machine for *my* project; there is no discovery primitive for either
of the other two legs of that grid. This effort adds them as CLI/wire
primitives, without changing how existing project-scoped calls behave.

## Participants

Single-machine, single-agent effort — no multi-participant coordination
needed for the initial phases (server-side same-machine listing is local;
the cross-machine lookup phase reuses the existing peer-bridge mesh
transport agent-bridge already has for `send`/`create`).

## Context

Filed as [copilot-extensions#2530](https://github.com/ThomasMichon/copilot-extensions/issues/2530)
after the operator, mid PR-triage session, hit a live psmux status-bar
regression and separately surfaced this discovery gap. Read the issue in
full for the original narrative; summarized findings from reading the
current implementation (`plugins/agent-bridge/src/agent_bridge/__main__.py`,
`session_manager.py`, `client.py`, `models.py`, `routes/sessions.py`):

- **Bridge topology today:** exactly one bridge daemon runs per machine
  (`models.default_port()` — a single ephemeral port advertised via the
  routing table; there is no per-project daemon). `SessionManager.list_sessions`
  (session_manager.py) and the `/api/v1/sessions` route
  (`routes/sessions.py::list_sessions`) apply **no project filter at all** —
  every session registered with this machine's one daemon is already in the
  returned set, regardless of which repo it belongs to.
- **The actual gap is at the CLI/argparse layer, not the data layer:**
  - `sessions` and `live-sessions list` (`__main__.py`) have **no
    `--project`/`--all-projects` flag at all** in their subparsers — unlike
    `agents`/`machines`, which already accept `--all-projects` (see
    `_listing_project()`, `_cmd_agents`, `_cmd_machines`).
  - `_PROJECT_CONSUMING_VERBS = {"agents", "create", "machines", "send"}`
    (`__main__.py` ~L3674) explicitly excludes `sessions`/`live-sessions`; an
    *explicit* top-level `--project` before either verb is rejected by
    `_guard_project_scope` with "is not meaningful for '<verb>'" (confirmed
    empirically: `agent-bridge sessions --project X` errors as an
    unrecognized argument — the subparser itself has no such flag; a
    top-level `--project sessions` hits the guard's rejection message).
  - There is currently no way to **attribute** which repo/project a returned
    session or live-session row belongs to in the human-readable output
    either (sessions carry a `project` field server-side —
    `routes/sessions.py` `project=getattr(s.target, "project", None)` — but
    `_cmd_sessions`/`_live_session_summary_line` never print it).
  - Net effect matches the issue's reported behavior: a caller has no
    explicit, discoverable way to ask "what's running here for repo X" or
    "show me everything on this box, labeled by repo" — even though the
    underlying data already crosses project lines.
- **Cross-machine, same-repo (primitive 2) has no server or CLI support at
  all today.** `agents --all-projects` only enumerates the **topology**
  (known machines/agent profiles), not *live sessions* on peer machines'
  bridges. `send <machine> ...` requires the caller to already know which
  machine to target. There is no broadcast/fan-out verb that asks every
  reachable peer bridge (per the existing `peer bridges` / `mesh federation`
  concepts in the agent-bridge vision) "do you have a live session for repo
  X?" and aggregates the answers.

## Request

> agent-bridge currently addresses sessions along a single axis: the
> caller's own project on the caller's own machine (or an explicit remote
> machine target via send/create, still scoped to resolving that machine's
> copy of the same repo the caller is in)... Two new (or extended) discovery
> primitives: (1) same-machine, any-repo session listing... (2) cross-machine,
> same-repo lookup...

(verbatim from copilot-extensions#2530; see the issue for the full text and
motivating example — the `worktree-manager-control-plane` effort's
independently-diverging Picker/Mux implementations.)

## Plan

### Phase 1 — Same-machine, any-repo listing
- [ ] Add `--all-projects` (mirroring `agents`/`machines`) to the `sessions`
      subparser and to `live-sessions list`; wire through `_listing_project()`
      so the semantics (default = caller's project, explicit `--all-projects`
      = everything, JSON mode defaults broad like `agents` already does)
      match the existing verbs exactly.
- [ ] Surface the owning project/repo in both the human-readable and JSON
      output for `sessions` and `live-sessions list` (the field already exists
      server-side; only display is missing) — needed so an any-repo listing
      is actually useful rather than an undifferentiated dump.
- [ ] Add `sessions`/`live-sessions` to `_PROJECT_CONSUMING_VERBS` (or an
      equivalent listing-only allowance) so an explicit `--project <repo>`
      scopes down instead of being rejected by `_guard_project_scope`.
- [ ] Unit/CLI tests: default scoping, `--all-projects`, explicit
      `--project <repo>`, and JSON field presence.

### Phase 2 — Cross-machine, same-repo lookup
- [ ] Design the wire shape: a new verb (working name `agent-bridge find
      --repo <repo>` per the issue's suggestion) that fans a lookup out to
      every peer bridge in the mesh (reusing the transport `send`/`create`
      already use to reach a named machine) and aggregates
      machine + session-id matches. Confirm naming against existing verbs
      (`agents`, `machines`, `send`) before implementing — avoid a
      near-duplicate of `agents --all-projects`.
- [ ] Server-side: a peer-bridge endpoint that answers "do you have a live
      session for repo X" from its own (already project-tagged) session list
      — reuses Phase 1's any-repo listing internally, filtered to one repo.
- [ ] CLI: implement `find`/`locate`, including a reasonable timeout/partial-
      result story when some peers are unreachable (mirror how `machines`
      already reports topology errors via `_report_topology_errors`).
- [ ] Tests: single match, multiple matches, no matches, partial mesh
      unreachability.

### Phase 3 — Docs
- [ ] Update `plugins/agent-bridge` CLI reference docs for the new
      flag/verb.
- [ ] Cross-link from `visions/plugins/agent-bridge` if the feature
      description needs a concrete-capability note (no vision revision
      expected — this closes existing feature intent).

## Validation Plan

- [ ] `agent-bridge sessions --all-projects` / `agent-bridge live-sessions
      list --all-projects` on a machine with 2+ distinct-project sessions
      registered returns rows from every project, each labeled with its repo.
- [ ] `agent-bridge sessions` (no flag) continues to behave exactly as
      before for a caller whose only registered sessions are for their own
      project (regression check — today's default already returns everything
      unfiltered, since there is no data-layer filter; confirm the new
      default-scoping logic doesn't regress that for existing callers).
- [ ] `agent-bridge find --repo <repo>` (or the chosen verb name) against a
      real 2-machine mesh locates a live session on the peer machine and
      returns enough to `send`/converse with it.
- [ ] `agent-bridge find --repo <repo-with-no-sessions-anywhere>` returns a
      clean empty result, not an error.
- [ ] Existing `agents --all-projects` / `machines --all-projects` behavior
      is unchanged (no regression from touching `_listing_project`/
      `_PROJECT_CONSUMING_VERBS`).

## Proposal

_Pending — Phase 2's exact wire shape needs a short design pass before
implementation (see Phase 2's first checklist item)._

## Journal

### 2026-09-12 — Kickoff (operator-directed split)

Split out of a predecessor session's PR-triage work on this repo via an
explicit context handoff (see the handoff for the full narrative). Read
issue #2530 in full and confirmed both primitives against current code:
`sessions`/`live-sessions` have no `--project`/`--all-projects` flag at the
CLI layer at all today (unlike `agents`/`machines`, which already have it),
and there is no cross-machine same-repo lookup verb. Note that the
**underlying session data already has no project filter** at the
`SessionManager`/route layer — the gap is specifically the missing
CLI-level scoping/labeling and the missing cross-machine fan-out verb, not a
project-siloed data model. Effort created; submitting for the review gate
before implementing Phase 1.
