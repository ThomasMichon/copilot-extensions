# Picker Venue Pivots (Codespaces & Containers) UX Overhaul

- **Slug:** `picker-venue-pivots`
- **Repo:** copilot-extensions
- **Branch(es):** `worktree/tmichon-cloud1-win-20260921-183442-d733` (design +
  preview tooling); implementation phases land on their own per-phase
  worktrees once the design below is approved (same pattern the Tasks-pane
  effort used).
- **Created:** 2026-09-21
- **Status:** Active — Phase 0 (grounding + preview tooling) in progress.
- **Vision:** [`visions/venue-pivots-ux`](../../../visions/venue-pivots-ux/README.md)
- **Umbrella issue:** _TBD — file once the Phase 0 previews are approved._
- **Sub-issues:** _TBD, one per Plan phase._

## Guiding Intent

The Worktrees pane, and (since `agent-dispatch-tasks-pane-ux-overhaul`) the
Tasks pane, are disciplined operator surfaces: declarative columns, a
state-derived colour palette, compact status markers, a rich action menu.
CodeSpaces and containers — the fabric's two remote agent venues — have no
comparable home in the Picker today; there is no registered pivot for
either. This effort brings both venues in as two new pivots that share one
**two-line row** grammar: line one carries id / status / key status /
claims, line two carries a descriptive string. See the vision for the full
design intent; this effort tracks its realization.

Concretely, in priority order:

1. A **Codespaces** pivot: repo-first identity, `agent-codespaces`' own
   lifecycle as key status, a cross-link to whichever local worktree is
   currently driving it, and whatever agent-bridge already knows about a
   live session hosted there (title, latest reported progress/intent,
   liveness) via `LiveSessionInfo`/`LiveSessionVenue`.
2. A **Containers** pivot scoped to **fleet** members only (repo-shaped,
   devcontainer-spec-built, agent-venue-capable containers —
   `agent_containers.fleet`) — explicitly not a general Docker browser.
3. An **Open** action on either pivot that attaches the operator to a live
   row's muxed Copilot instance over the fabric's SSH transport.
4. **Design only** for this effort: "New codespace" / "New container" /
   "New agent"-on-a-dormant-venue, provision-then-embody. Per operator
   decision (2026-09-21), the interactive create→embody implementation is
   deferred to land alongside the parallel drive-CLI-agents-over-SSH
   capability, so this effort produces the design and the manifest/action
   shape but does not have to land a working provisioning flow itself.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| tmichon-cloud1 (this session) | Grounding against real code, vision + effort authoring, preview-rendering tooling, Phase 1+ implementation | `copilot-extensions.worktrees/tmichon-cloud1-win-20260921-183442-d733` |

## Coordination

- **Topology:** independent per-phase PRs once the Phase 0 preview is
  approved by the operator — each Plan phase below is a self-contained,
  reviewable slice. Land each phase's PR before starting the next phase's
  implementation, per the Tasks-pane effort's own hard-won sequencing rule
  (`ThomasMichon/copilot-extensions#2908`).
- **Host (owns PRs):** whichever worktree lands each phase.
- **Delegates:** none yet.
- **Handoff:** the preview tooling and captured screenshots in this
  worktree are the artifact the operator reviews before any implementation
  PR opens. Implementation follows the same manual sequenced-session
  handoff loop the Tasks-pane effort used: a session drives until context
  fills, saves a handoff prompt, and a fresh session picks up from the
  Runbook below.

## Runbook — for whichever session is currently driving this effort

**Read this section FIRST in any new session picking up this effort.**

- **Worktree:** this one
  (`copilot-extensions.worktrees/tmichon-cloud1-win-20260921-183442-d733`)
  until Phase 0 previews are captured and approved; each implementation
  phase gets its own fresh worktree off `main` afterward (Tasks-pane
  precedent).
- **Current phase:** Phase 0 — grounding is done (see Journal); preview
  tooling and screenshots are not yet built.
- **Immediate next step:** stand up `scripts/picker-snapshot/venue-preview/`
  (sibling of the existing `tasks-preview/`), reusing its hermetic-demo /
  fixed-fixture / real-engine pattern, with two proposed pivot manifests
  (`agent-codespaces.proposed.json`, `agent-containers.proposed.json`) and
  fixed JSON fixtures standing in for `agent-codespaces list --json` /
  `agent_containers.fleet` output plus a fixed agent-bridge
  `live-sessions --json` fixture for the cross-link join. Render screenshots
  of the two-line row for both pivots (populated + empty-state + New-venue
  action) for operator review before Phase 1 implementation starts.

## Context

The Worktrees pane and the Tasks pane (via
`agent-dispatch-tasks-pane-ux-overhaul`) already carry a disciplined
declarative-column, colour-palette, rich-action-menu presentation. CodeSpaces
and containers — the fabric's remote agent venues — have no registered
pivot in the Picker at all today (confirmed: no `pivots/*.json` exists under
either `plugins/agent-codespaces` or `plugins/agent-containers`). Meanwhile
agent-bridge already tracks everything needed to join a venue back to its
driving worktree and live session (`LiveSessionInfo`/`LiveSessionVenue`), and
`agent-containers` already models the fleet-vs-general-container split this
effort's Containers pivot needs to surface. See the vision's Reality docs and
this README's own "Concepts grounded against real code" section below for the
specific modules.

## Request

Operator request (2026-09-21, paraphrased/public-safe): overhaul the
under-served Codespaces and Containers pivots in the Worktree Manager,
converging both on a two-line row (id/status/key-status/claims, then a
description). For Codespaces: repo is the key identity; also track the
locally-driving worktree and remote Copilot session info (title, reported
intent/progress, other status) via agent-bridge. For Containers: split
"fleet" (repo-shaped agent venues) from general Docker containers, focusing
on the Codespace-like agent-venue containers, not general build/validation
use. Support "New container"/"New codespace" provisioning that launches the
operator into a Copilot session through that venue (this piece: design now,
implement alongside the parallel drive-CLI-agents-over-SSH effort). Most
important end state: navigate to this pivot, see an entry for an embodied
agent, and Open it into the muxed Copilot instance over SSH — the same flow
for a fresh "New codespace"/"New agent" against a dormant venue. Use an
effort, and use the preview-as-we-work screenshot approach (the Worktree
Picker and the new Tasks pivot are the baselines).

## Concepts grounded against real code (do not re-derive; cite this)

- **Pivot registry contract:** `RegisteredPivot`/`Column`/`PivotAction` in
  `worktree-manager/src/worktree_manager/production_picker/picker_tui/
  pivot_manifest.py` and `pivots.py`; a contributing plugin drops a
  `pivots/<name>.json` template, `ensure_pivots`/`scan_pivot_registry`
  materialize an attributed pointer, the picker renders a generic pivot —
  no engine code per new pivot except the (new, this effort's) two-line row
  mode.
- **Column fit/priority:** `Column.priority` + `TasksView._fitted_columns()`
  (landed alongside the Tasks-pane effort) — any declarative pivot's columns
  already run through the same fit/shrink/drop algorithm; the two-line row
  this effort adds is a presentation mode on top of that, not a replacement.
- **agent-codespaces owns venue identity/lifecycle:** `status.py`
  (active/recovered/prunable), `lease.py` (borrow/release), `pool.py`
  (pool membership), `worktrees.py` (same-cell worktree adapter). `list
  --json` (CLI) and the underlying `gh codespace list --json` are the
  existing data source; no new backend command should be needed for the
  base row, only cross-link joins (below).
- **agent-containers owns the fleet/generic-container split already:**
  `fleet.py`'s own module docstring: "A *fleet* is a named pool of
  long-lived dev containers built from one devcontainer spec... kept warm
  ... between uses; an effort borrows one via the lease broker." This is
  exactly the boundary the vision's Containers pivot renders — this effort
  does not invent that split, it surfaces it.
- **agent-bridge owns live-session state:** `models.py`'s `LiveSessionInfo`
  (`worktree_id`, `repo`, `driven_by`, `status`, `turn_state`, `liveness`,
  `latest_progress`) and `LiveSessionVenue` (`kind: "codespace"|"container"`,
  `target`, `mux_session_name`) already carry everything the vision's
  remote-session-join feature needs. **No new agent-bridge schema is
  expected** — this effort is a consumer, not a schema owner, matching the
  Tasks-pane effort's own hard lesson about not inventing a second copy of
  state another layer already owns.
- **"Bridges" pivot already exists** (`plugins/agent-bridge/pivots/
  agent-bridge.json`) but lists registered **bridge agent profiles**
  (`admin_resolver`-style targets), not live sessions or venues — a
  different subject from this effort's Codespaces/Containers pivots. Do not
  conflate or merge them.
- **`report_intent`-style progress:** the phrase does not appear literally
  in agent-bridge; the closest real mechanism is `LiveSessionInfo
  .latest_progress` (a parsed progress-beat object, "the live-session
  analogue of a task's `latest_progress`" per its own docstring) plus
  whatever a session's own reported title surfaces through
  `RegisterLiveSessionRequest`/session registration. Confirm the exact
  title/intent field names against `db_live_sessions.py` /
  `live_representation.py` during Phase 1 grounding rather than assuming a
  field name from this README.

## Plan

### Phase 0 — Design + review-ready previews (this worktree)
- [x] Ground the pivot-registry contract, agent-codespaces' lifecycle
      surface, agent-containers' fleet/generic split, and agent-bridge's
      `LiveSessionInfo`/`LiveSessionVenue` model against the real code.
- [x] Author the `venue-pivots-ux` vision.
- [x] Author this effort README.
- [ ] Confirm the exact agent-bridge field(s) backing "session title" and
      "reported intent/progress" (`live_representation.py`,
      `db_live_sessions.py`) — update this README's grounding section once
      confirmed rather than leaving the placeholder above.
- [ ] Stand up `scripts/picker-snapshot/venue-preview/`: hermetic demo
      Worktrees source (reuse `render_tasks_preview.py`'s pattern), fixed
      fixtures for `agent-codespaces list --json` and fleet-container
      listing, a fixed agent-bridge `live-sessions --json` fixture, and two
      proposed pivot manifests.
- [ ] Design (in the manifest + a design note, not code yet) the two-line
      row rendering mode: how `Column`/`entry` fields map onto line-one
      (id/status/key-status/claims) vs. line-two (description), and whether
      this is a new `RegisteredPivot` flag or inferred from manifest shape.
- [ ] Render and review screenshots: Codespaces pivot (populated,
      empty-state+New-codespace), Containers pivot (populated,
      empty-state+New-container), and one screenshot each of a row with vs.
      without a live agent-bridge session joined.
- [ ] Operator review of the screenshots; resolve any design feedback here
      before Phase 1 starts.

### Phase 1 — Codespaces pivot (implementation)
- [ ] Two-line row rendering mode in `engine.py`/`pivots.py`, generalized
      (not Codespaces-specific) so Containers reuses it unchanged.
- [ ] `agent-codespaces` pivot manifest (`pivots/agent-codespaces.json`)
      wired to real `list --json` output.
- [ ] Driving-worktree cross-link (reverse-join, same shape as
      `find_claiming_task` but keyed on CodeSpace identity).
- [ ] agent-bridge live-session join keyed on
      `venue.kind == "codespace"` + `venue.target`.
- [ ] Claims summary reusing the Tasks pane's shared prominent-artifacts
      surface.
- [ ] Empty-state hint + (manifest/action shape only, per Phase 0 design)
      the New-codespace entry point.

### Phase 2 — Containers pivot (implementation)
- [ ] `agent-containers` pivot manifest scoped to fleet members only
      (never general `docker ps` output).
- [ ] Same driving-worktree / live-session / claims cross-links as Phase 1,
      joined on `venue.kind == "container"`.
- [ ] Empty-state hint + (manifest/action shape only) New-container entry
      point.

### Phase 3 — Open into a muxed session over SSH
- [ ] Wire the Open action on both pivots to the existing
      `mux_session_name` reattach mechanics for a live row.
- [ ] Confirm behavior parity with the Worktrees pane's own Open/resume
      action for a row with no live session (resumable but dormant venue).

### Phase 4 — New-venue → embody design handoff (design only)
- [ ] Record the finalized create→embody flow design (target-info prompt,
      provisioning call, hand-off into a fresh Copilot session) as a design
      note in this effort, explicitly scoped as **input to** the parallel
      drive-CLI-agents-over-SSH effort rather than an implementation
      obligation of this effort.
- [ ] Cross-link that design note from both efforts once the other effort
      exists / is identified.

## Validation Plan

- [ ] Preview screenshots reviewed and approved by the operator before any
      implementation PR opens (Phase 0 gate).
- [ ] Unit tests for the two-line row renderer (column→line-one/line-two
      mapping, graceful-absence of cross-link fields).
- [ ] Unit tests for the Codespaces/Containers cross-link joins against
      fixed fixtures (worktree match, agent-bridge venue-target match, no
      match).
- [ ] A live/manual check against a real CodeSpace and a real fleet
      container (not just fixtures) before Phase 1/2 are considered done,
      per this repo's "validate beyond unit tests" policy.
- [ ] Confirm the Containers pivot never surfaces a non-fleet container in
      a real `docker ps`-populated environment.

## Journal

- **2026-09-21** — Effort opened from an operator request to overhaul the
  Codespaces/Containers pivots. Grounded the design against
  `worktree-manager`'s pivot registry, `agent-codespaces`/`agent-containers`
  source, and `agent-bridge`'s `LiveSessionInfo`/`LiveSessionVenue` model.
  Operator decisions captured: both pivots in one effort; New-venue→embody
  is design-only here (implementation deferred to the parallel
  drive-CLI-agents-over-SSH effort); effort/vision land in copilot-extensions
  (where `worktree-manager` and the venue-provider plugins already live).
