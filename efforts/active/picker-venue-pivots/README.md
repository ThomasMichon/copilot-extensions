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

**Both pivots already exist and are contributed by `agent-codespaces` and
`agent-containers` today** (`pivots/agent-codespaces.json`,
`pivots/agent-containers.json`) — this is an **overhaul of two existing,
asymmetric contributions**, not a greenfield build (an earlier draft of this
effort/vision got this wrong; see Journal). The CodeSpaces pivot is already
fairly rich (`pool.picker_payload`): repo-grouped, columnar
(health/occupancy/safety/worktree/cores), with gated Release/Recycle/Verify
actions. The Containers pivot is correctly scoped to **fleet** members only
(never a general Docker browser) but is far thinner: a flat badge list with
no columns, no grouping, no worktree cross-link, and no actions at all —
even though its own `fleet --json` output already carries a `lease` field
the manifest never surfaces.

This effort brings both pivots to consistent presentation and full
information fidelity:

1. **Codespaces:** wire the manifest's dropped `subtitle` field (claim
   holder / orphaned-lock detail `pool.py` already computes but the
   manifest never maps), and add the one genuinely new integration —
   an agent-bridge live-session join (title, latest reported
   progress/intent, liveness) via `LiveSessionInfo`/`LiveSessionVenue`.
2. **Containers:** bring the manifest up to the CodeSpaces pivot's fidelity
   — `columns`, fleet-based grouping, a `lease`→worktree cross-link, gated
   lifecycle actions — plus the same agent-bridge live-session join.
3. An **Open** action on either pivot (neither has one today) that attaches
   the operator to a live row's muxed Copilot instance over the fabric's SSH
   transport.
4. **Design only** for this effort: "New codespace" / "New container" /
   "New agent"-on-a-dormant-venue, provision-then-embody. Per operator
   decision (2026-09-21), the interactive create→embody implementation is
   deferred to land alongside the parallel drive-CLI-agents-over-SSH
   capability, so this effort produces the design and the manifest/action
   shape but does not have to land a working provisioning flow itself.

See the vision for the full design intent; this effort tracks its
realization.

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
- **Current phase:** Phase 0 — grounding is done (including the 2026-09-21
  correction below); preview tooling and screenshots are not yet built.
- **Immediate next step:** stand up `scripts/picker-snapshot/venue-preview/`
  (sibling of the existing `tasks-preview/`), reusing its hermetic-demo /
  fixed-fixture / real-engine pattern, but seeded from the **real current
  manifests** (`pivots/agent-codespaces.json`,
  `pivots/agent-containers.json`) plus proposed diffs on top of them —
  not from-scratch proposed manifests — with fixed fixtures standing in for
  `agent-codespaces pool --picker-json`, `agent-containers fleet --json`,
  and a fixed agent-bridge `live-sessions --json` fixture for the
  cross-link join. Render before/after screenshots so the operator can see
  exactly what changes on each row.

## Context

The Worktrees pane and the Tasks pane (via
`agent-dispatch-tasks-pane-ux-overhaul`) already carry a disciplined
declarative-column, colour-palette, rich-action-menu presentation, and the
underlying two-line-row grammar (`RegisteredPivot.subtitle_field` +
`WorktreesView._row`/`_column_subtitle`) is already generic infrastructure
any pivot can use. **Both CodeSpaces and Containers already contribute
registered pivots** using that infrastructure, but at very different levels
of fidelity — see "Concepts grounded against real code" below for the exact
gap, cited to source. This effort closes that gap and adds the one piece
neither pivot has: a join against agent-bridge's live-session state.

## Request

Operator request (2026-09-21, paraphrased/public-safe): note that
`agent-codespaces` and `agent-containers` **already have** pivot
contributions — the ask is to **overhaul** those existing contributions to
align with where the system is currently going, and to ensure **consistency
of presentation** and **fidelity of information**, not to build new pivots
from nothing. Converge both on a two-line row (id/status/key-status/claims,
then a description). For Codespaces: repo is the key identity; also track
the locally-driving worktree and remote Copilot session info (title,
reported intent/progress, other status) via agent-bridge. For Containers:
split "fleet" (repo-shaped agent venues) from general Docker containers,
focusing on the Codespace-like agent-venue containers, not general
build/validation use. Support "New container"/"New codespace" provisioning
that launches the operator into a Copilot session through that venue (this
piece: design now, implement alongside the parallel
drive-CLI-agents-over-SSH effort). Most important end state: navigate to
this pivot, see an entry for an embodied agent, and Open it into the muxed
Copilot instance over SSH — the same flow for a fresh "New
codespace"/"New agent" against a dormant venue. Use an effort, and use the
preview-as-we-work screenshot approach (the Worktree Picker and the new
Tasks pivot are the baselines).

## Concepts grounded against real code (do not re-derive; cite this)

- **Pivot registry contract:** `RegisteredPivot`/`Column`/`PivotAction` in
  `worktree-manager/src/worktree_manager/production_picker/picker_tui/
  pivot_manifest.py` and `pivots.py`; a contributing plugin drops a
  `pivots/<name>.json` template, `ensure_pivots`/`scan_pivot_registry`
  materialize an attributed pointer, the picker renders a generic pivot.
- **The two-line/subtitle grammar already exists and is already used by
  each pivot differently.** `RegisteredPivot.subtitle_field` defaults from
  `entry.subtitle` (`pivot_manifest.py`); `WorktreesView._row` (badge-list
  mode) and `_column_subtitle` (columns mode) both render it as a dim
  second line when present. Containers' manifest declares
  `"subtitle": "image"` (so it renders); **Codespaces' manifest does not
  declare `entry.subtitle` at all**, even though `pool.picker_payload`
  computes a rich `subtitle` (claim holder / cross-machine hold /
  orphaned-lock warning) on every entry — that computed value is silently
  dropped today. Column fit/priority (`Column.priority` +
  `TasksView._fitted_columns()`, landed alongside the Tasks-pane effort)
  is separate, already-generic infrastructure this effort reuses as-is.
- **CodeSpaces pivot, current real shape** (`pivots/agent-codespaces.json` +
  `pool.py`): `list = ["agent-codespaces", "pool", "--picker-json"]`,
  `scope: account`, `stream: true`; `entry` maps `id`/`title=display`/
  `worktree`/`group` (no `subtitle` — the gap above); `columns` = codespace/
  health/occupancy/safe/worktree/cores/task(`worktree_title`, populated by
  the picker engine's own `_worktree_title_map`, not agent-bridge); actions
  = Release (gated `disposition=in-use`), Recycle (gated
  `disposition=stale, safe=yes`), Verify (gated
  `disposition=stale, safe∈{unknown,no}`). `pool.picker_payload`'s full
  entry dict additionally carries `repository`/`repo`/`branch`/`account`/
  `disposition`/`state`/`running`/`holder`/`orphaned` — more than the
  current manifest surfaces; some of this is candidate material for new
  columns.
- **Containers pivot, current real shape**
  (`pivots/agent-containers.json` + `agent_containers.__main__._cmd_fleet`):
  `list = ["agent-containers", "fleet", "--json"]`; `entry` maps
  `id=name`/`title=name`/`subtitle=image`/`badges=[state, fleet]`; **no
  `columns`, no `group`, no `worktree` cross-link, no `actions` at all**.
  `_cmd_fleet`'s real JSON row (per `test_fleet_json.py`) already carries
  `name`, `container_id`, `image`, `state`, `status`, `fleet`,
  `local_folder`, `lease` (holder — the direct Containers analogue of
  Codespaces' `holder`/`worktree`), `security_profile`,
  `configured_security_profile`, `security_policy_current`,
  `security_policy_errors`, `network`, `environment_names`,
  `host_credentials`, `lifecycle_hold`, `rescue` — the manifest maps only
  4 of these ~16 fields today.
- **agent-bridge owns live-session state, and neither pivot joins it
  today:** `models.py`'s `LiveSessionInfo` (`worktree_id`, `repo`,
  `driven_by`, `status`, `turn_state`, `liveness`, `latest_progress`) and
  `LiveSessionVenue` (`kind: "codespace"|"container"`, `target`,
  `mux_session_name`) already carry everything a remote-session-join
  feature needs — confirmed **absent** from both current manifests and
  from `pool.py`/`_cmd_fleet`'s output. **No new agent-bridge schema is
  expected** — this effort is a consumer, not a schema owner.
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
- [x] **Correction pass:** re-grounded against the actual
      `pivots/agent-codespaces.json`/`pivots/agent-containers.json`
      manifests and `pool.py`/`_cmd_fleet` source after an operator
      correction that both pivots already exist; reframed vision + effort
      from "build two new pivots" to "overhaul two existing, asymmetric
      pivots."
- [ ] Confirm the exact agent-bridge field(s) backing "session title" and
      "reported intent/progress" (`live_representation.py`,
      `db_live_sessions.py`) — update this README's grounding section once
      confirmed rather than leaving the placeholder above.
- [ ] Stand up `scripts/picker-snapshot/venue-preview/`: hermetic demo
      Worktrees source (reuse `render_tasks_preview.py`'s pattern), fixed
      fixtures for `agent-codespaces pool --picker-json` and
      `agent-containers fleet --json`, a fixed agent-bridge
      `live-sessions --json` fixture, and **proposed manifest diffs on top
      of the real current manifests** (not from-scratch proposals).
- [ ] Design the exact column/field mapping for the Containers parity pass
      (which of the ~16 real `fleet --json` fields become columns vs.
      subtitle vs. actions-gating, mirroring Codespaces' choices) and the
      Codespaces `subtitle` wiring + new live-session columns, as a design
      note before writing manifest JSON. Both must produce line two as
      `"[mark] <durable title> - <transient activity>"` (see the vision's
      "Row grammar" concept) — not a bare fact string — with the durable
      title sourced from the worktree-title cross-link (or venue identity
      when unclaimed) and the transient activity sourced from the new
      agent-bridge live-session join.
- [ ] Render and review before/after screenshots for both pivots: current
      (real) shape vs. proposed shape, plus one screenshot each of a row
      with vs. without a live agent-bridge session joined.
- [ ] Operator review of the screenshots; resolve any design feedback here
      before Phase 1 starts.

### Phase 1 — Codespaces pivot (implementation)
- [ ] Wire `entry.subtitle` into `pivots/agent-codespaces.json` so
      `pool.picker_payload`'s already-computed subtitle (claim/orphan
      detail) actually renders.
- [ ] Add any additional columns worth surfacing from `pool.picker_payload`'s
      fuller entry dict (per Phase 0 design note).
- [ ] agent-bridge live-session join keyed on
      `venue.kind == "codespace"` + `venue.target`, surfaced as new
      columns/subtitle detail.
- [ ] Claims summary reusing the Tasks pane's shared prominent-artifacts
      surface, if distinct from the existing worktree/claim cross-link.
- [ ] (Manifest/action shape only, per Phase 0 design) the New-codespace
      entry point.

### Phase 2 — Containers pivot (implementation)
- [ ] Add `columns` to `pivots/agent-containers.json` mirroring
      Codespaces' shape (container/fleet, state, lease→worktree, and
      whatever `security_profile`/`network` signal is picker-worthy per
      Phase 0 design).
- [ ] Add fleet-based `group` (the Containers analogue of "repo @
      account").
- [ ] Wire the existing `lease` field to a `worktree` cross-link exactly
      as Codespaces already does.
- [ ] Add gated lifecycle actions analogous to Release/Recycle/Verify,
      built on `lifecycle.py`/`lease.py`/`rescue.py`'s existing
      start/stop/remove/rescue primitives.
- [ ] Same agent-bridge live-session join as Phase 1, joined on
      `venue.kind == "container"`.

### Phase 3 — Open into a muxed session over SSH
- [ ] Add an Open action to both pivots (neither has one today), wired to
      the existing `mux_session_name` reattach mechanics for a live row.
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

- [ ] Before/after preview screenshots reviewed and approved by the
      operator before any implementation PR opens (Phase 0 gate).
- [ ] Unit tests confirming the Codespaces manifest's `subtitle` actually
      renders `pool.picker_payload`'s computed value (a regression test for
      the exact dropped-field bug this effort fixes).
- [ ] Unit tests for the Containers pivot's new columns/group/lease-cross-
      link against `test_fleet_json.py`'s existing fixture shape.
- [ ] Unit tests for the Codespaces/Containers agent-bridge live-session
      joins against fixed fixtures (venue-target match, no match).
- [ ] A live/manual check against a real CodeSpace and a real fleet
      container (not just fixtures) before Phase 1/2 are considered done,
      per this repo's "validate beyond unit tests" policy.
- [ ] Confirm the Containers pivot still never surfaces a non-fleet
      container after the parity changes (no regression on the existing
      fleet-only scoping).

## Journal

- **2026-09-21 (later)** — Operator specified a precise row grammar: line
  one stays columnar (`[ ] <id> STATUS <stats> <claims>`); line two is
  free-form and must read as `"[mark] <durable title> - <transient
  activity>"`, with the transient activity (not the title) truncated first
  when space is short. Recorded as the vision's new "Row grammar" concept
  and threaded through the Codespaces subtitle-wiring and Containers
  parity plan items above — both were previously described only as
  "restore/add a subtitle," now pinned to this exact two-part content
  contract.
- **2026-09-21** — Effort opened from an operator request to overhaul the
  Codespaces/Containers pivots. Initially drafted the vision/effort as if
  neither pivot existed ("no registered pivot surfaces a CodeSpace or an
  agent-shaped container as a first-class row at all") — **this was
  wrong**. The operator corrected: both `agent-codespaces` and
  `agent-containers` already contribute pivots
  (`pivots/agent-codespaces.json`, `pivots/agent-containers.json`); the ask
  is to overhaul those existing contributions for presentation consistency
  and information fidelity, not build new ones. Re-grounded against
  `pool.py`'s `picker_payload` (rich, columnar, but with a dropped
  `subtitle` field) and `_cmd_fleet`'s real JSON shape (only 4 of ~16
  fields currently mapped, no columns/group/cross-link/actions at all) and
  rewrote both documents accordingly. Operator decisions still in force:
  both pivots in one effort; New-venue→embody is design-only here
  (implementation deferred to the parallel drive-CLI-agents-over-SSH
  effort); effort/vision land in copilot-extensions (where
  `worktree-manager` and the venue-provider plugins already live).
