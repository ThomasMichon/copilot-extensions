# Picker Venue Pivots (Codespaces & Containers) UX Overhaul

- **Slug:** `picker-venue-pivots`
- **Repo:** copilot-extensions
- **Branch(es):** `worktree/tmichon-cloud1-win-20260921-183442-d733` (design +
  preview tooling); implementation phases land on their own per-phase
  worktrees once the design below is approved (same pattern the Tasks-pane
  effort used).
- **Created:** 2026-09-21
- **Status:** Active — Phase 0 fully complete (design, operator-approved
  committed screenshots, and the shared claims-pecking-order module all
  done). Phase 1 (Codespaces implementation) next.
- **Vision:** [`visions/venue-pivots-ux`](../../../visions/venue-pivots-ux/README.md)
- **Umbrella issue:** _TBD — file before Phase 1 implementation lands._
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
4. A reserved **driving-worktree mark** (a stat slot or the `[mark]` glyph)
   plus menu actions to jump to the driving worktree's Worktrees-pivot entry
   or open its Worktree Status card directly.
5. For odsp-web: auto-journal a pushed-branch's resulting PR onto the
   driving worktree's existing claim ledger
   (`agent-worktrees claims add pr <ref>`), so it appears in the row's
   claims-list with no manual claim step.
6. Adopt a single **shared claims pecking order** (PR > bug > effort >
   bridge > CodeSpace/container > child worktree > machine SSH > dispatch
   task, tunable) for picking the "1-2 prominent" claims every pivot's
   claims-list shows — one ranking, every claim-showing pivot consumes it
   the same way.
7. **Design only** for this effort: "New codespace" / "New container" /
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
- **Current phase:** Phase 0 is **fully complete** (2026-09-21) — grounding,
  manifest/screenshot design, operator-approved committed screenshots
  (`design-previews/*.png`), and the shared claims-pecking-order module
  (`agent_worktrees.claims_rank`, real code + 13 passing tests) are all
  done. **Phase 1 has not started.**
- **Immediate next step:** open the umbrella issue (still `_TBD_` at the top
  of this README), then start Phase 1: wire `pivots/agent-codespaces.json`'s
  `entry.subtitle` and wire `pool.picker_payload` to call
  `claims_rank.summarize_claims` for its `claims_summary` field (the
  module exists; nothing calls it from the real pivot yet). If any
  implementation-phase session changes a row/menu's visual shape, re-run
  the render script (`worktree-manager\.venv\Scripts\python.exe
  worktree-manager\scripts\picker-snapshot\venue-preview\render_venue_preview.py
  --out-dir worktree-manager\scripts\picker-snapshot\venue-preview\out`,
  both venvs already built in this worktree) and diff the fresh output
  against the committed `design-previews/*.png` — a deliberate difference
  is fine (update the committed copies + note why in the Journal); an
  unnoticed one is exactly the drift this comparison exists to catch.

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

## Design Previews (approved north-star)

**Committed, not just filed on OneDrive** (2026-09-21 operator decision —
the Tasks-pane-ux effort's own previews only ever lived at
`OneDrive/2026/09.17 agent-dispatch Tasks Pane UX Overhaul Previews`, never
in-repo; this effort corrects that so the approved design has a durable,
version-controlled reference point future sessions can diff against for
drift, not just a local human-reviewed folder). These six PNGs
(`design-previews/`) are the **Phase 0-approved rendering** of the row
grammar, session column, claims list, and action menus — regenerate them
with `render_venue_preview.py` (see the venue-preview README) at any later
implementation checkpoint and diff against these committed copies to catch
visual regression or drift from the agreed design before merging. A
deliberate difference is fine (design evolves) — an *accidental* one is
what this comparison exists to catch. Update these files, with a Journal
note explaining why, whenever the design is deliberately revised.

| File | Shows |
|------|-------|
| [`design-previews/codespaces-before.png`](design-previews/codespaces-before.png) | The REAL current CodeSpaces pivot — no subtitle line, no session/claims columns. |
| [`design-previews/codespaces-after.png`](design-previews/codespaces-after.png) | The proposed CodeSpaces pivot: `sess`/`claims` columns, the composed row-grammar line two, column-fit dropping `worktree` for width. |
| [`design-previews/containers-before.png`](design-previews/containers-before.png) | The REAL current Containers pivot — a flat, ungrouped badge list. |
| [`design-previews/containers-after.png`](design-previews/containers-after.png) | The proposed Containers pivot at Codespaces' fidelity — same columns, grouping, and row grammar. |
| [`design-previews/codespaces-menu-driven-live.png`](design-previews/codespaces-menu-driven-live.png) | The action menu for a `sess: LIVE` CodeSpace row: Open into a CLI session, View driving worktree, Worktree status, Release. |
| [`design-previews/containers-menu-driven-live.png`](design-previews/containers-menu-driven-live.png) | The identical menu shape for a `sess: LIVE` fleet container row. |

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
- **Claims are an existing per-worktree ledger, not a new store:**
  `agent-worktrees`' `claims_cli.py` already implements
  `claims add <kind> <ref>` (kinds already include `pr`, `codespace`,
  `container`), `claims release`/`settle`/`sweep`. This effort's
  claims-list column reads that ledger through the driving-worktree
  cross-link — **no new claim storage or rendering**; the only new piece
  is a *producer* (Phase 5's odsp-web PR auto-claim) calling the existing
  `claims add pr <ref>` verb.
- **odsp-web PR detection has no existing hook yet:** nothing in
  `agent-codespaces` today watches for a pushed ADO branch turning into a
  PR (`codespace_assets/ado-auth-helper-relay`/`ado-auth-helper-wrapper`
  handle ADO *auth*, not PR detection). Phase 5 needs to design this
  detection point from scratch — likely a periodic/triggered check inside
  the CodeSpace's own git activity or an ADO API poll — before it can call
  the existing `claims add pr` verb.
- **No prominent-claims-selection code existed before this effort:**
  confirmed (grep across `engine.py`/`pivot_manifest.py`) — the
  Tasks-pane-ux vision's own "Prominent Artifacts" feature has never been
  implemented, and no other pivot picked "1-2 prominent" claims out of a
  fuller ledger. **Now implemented:**
  `plugins/agent-worktrees/src/agent_worktrees/claims_rank.py`
  (`rank_claims`/`format_claim`/`summarize_claims`) — pure functions over
  `ResourceClaim`-shaped entries, no I/O, 13 passing unit tests
  (`tests/test_claims_rank.py`). Placed in `agent-worktrees` since it
  already owns the ledger being ranked; both this effort's pivots and a
  future Tasks-pane-ux implementation import the same module rather than
  each computing their own selection. **Grounded gap recorded in the
  module's own docstring:** `claims_cli._claims_add`'s `valid_kinds` today
  is only `{worktree, codespace, container, ssh, workdir, pr, task}` —
  "bug"/"issue", "effort", "bridge" are not yet claimable kinds; the
  module ranks whatever is actually present and degrades gracefully
  (unrecognized kinds sort last, never raise) rather than assuming those
  kinds exist.
- **The row grammar needs zero new picker-engine code — confirmed by
  building the Phase 0 preview.** `subtitle_field`/`_column_subtitle`
  already render one composed string; `Column`/`group_field`/
  `worktree_field` already exist. Every proposed field in the Phase 0
  manifests (`sess`, `claims_summary`, the composed `subtitle`) is just a
  new column/entry-mapping declaration plus a new value the real backend
  command (`pool.py`, `_cmd_fleet`) would compute — exactly the same shape
  as the existing `worktree_title` auto-injection. The preview's own
  fixtures bake the agent-bridge join's *output* (`sess`, the composed
  activity text) directly into the fixture rows rather than modeling a
  separate `agent-bridge live-sessions --json` fixture and a join step in
  the preview tooling — that join is real Phase 1/2 backend work
  (`pool.py`/`_cmd_fleet` calling agent-bridge), not something the picker
  engine or this preview tool needs to simulate to prove the row grammar.
- **The Worktrees pane already has the exact column this effort needs for
  session liveness:** a compact `sess`/`live` column (key `sess`, header
  "live", 4 chars wide) with a multi-valued vocabulary (a pulsing "●" for a
  live mux, `PROC`/`LOCK` otherwise) — not a boolean. An operator review of
  the first-draft screenshots correctly flagged this effort's own `driven`
  column as too wide for what amounts to a yes/no, and pointed at this
  existing column as the right model. Adopted directly: same key/header/
  width, values `LIVE`/`IDLE`/blank. The separate `driven` boolean is
  **dropped entirely** — it was redundant with the already-present
  `worktree` cross-link column (non-blank already means driven).

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
- [x] Stand up `scripts/picker-snapshot/venue-preview/`: hermetic demo
      Worktrees source (reuse `render_tasks_preview.py`'s pattern), fixed
      fixtures for `agent-codespaces pool --picker-json` and
      `agent-containers fleet --json` (`fake_pool.py`/`fake_fleet.py`), and
      **both a byte-copy of the real current manifest and a proposed
      manifest** for each pivot (not from-scratch proposals) — see
      `worktree-manager/scripts/picker-snapshot/venue-preview/README.md`.
- [x] Design the exact column/field mapping for the Containers parity pass
      and the Codespaces `subtitle` wiring, realized directly as the two
      `*.proposed.json` manifests (not just a prose note): a compact
      `sess` (session liveness) column and a `claims` column on both,
      `subtitle` composed as
      `"[mark] <durable title> - <transient activity>"`, a `worktree`
      cross-link on Containers via `lease`.
- [x] Decide the driving-worktree indicator's slot: **dropped the
      dedicated `driven` boolean entirely** (redundant with the already-
      present `worktree` cross-link column) after operator review of the
      first-draft screenshots; replaced with the Worktrees pane's own
      compact `sess`/`live` column (same key/header/4-char width,
      `LIVE`/`IDLE`/blank) for the one genuinely new signal — session
      liveness. "View driving worktree"/"Worktree status" menu entries
      gated on `sess` being `LIVE` or `IDLE`.
- [x] Design **and implement** the shared claims-pecking-order module:
      `plugins/agent-worktrees/src/agent_worktrees/claims_rank.py`
      (`rank_claims`/`format_claim`/`summarize_claims`), pure functions over
      `ResourceClaim`-shaped entries (objects or plain dicts), no I/O.
      13 unit tests, all passing (`tests/test_claims_rank.py`). Grounded
      against the real `valid_kinds` vocabulary while building it — "bug"/
      "issue", "effort", "bridge" are not yet claimable kinds; the module
      degrades gracefully (unrecognized kinds rank last, never raise) — see
      the module's own docstring for the full gap note. **Still preview-only
      placeholders**: `fake_pool.py`/`fake_fleet.py`'s `claims_summary`
      values are hand-authored, not wired to the real module yet — Phase 1/2
      wire `pool.py`/`_cmd_fleet` to actually call `summarize_claims`.
- [x] Render and review before/after screenshots for both pivots: current
      (real) shape vs. proposed shape (`codespaces-before/after.png`,
      `containers-before/after.png`), plus a menu screenshot for each
      showing Open/View-driving-worktree/Worktree-status
      (`codespaces-menu-driven-live.png`, `containers-menu-driven-live.png`).
      All six ran successfully against the real Textual engine on the
      first attempt — see the venue-preview README for the full
      before/after description of each.
- [x] Operator review of the screenshots; resolve any design feedback here
      before Phase 1 starts. **Approved 2026-09-21** (after the
      `driven`→`sess` column fix — see Journal); committed as
      `design-previews/*.png` (see the section near the top of this
      README) as the durable north-star reference for future
      visual-regression/vision-alignment checks.

### Phase 1 — Codespaces pivot (implementation)
- [x] ~~Build the shared claims-pecking-order module~~ — done in Phase 0
      (`claims_rank.py`, see above). Remaining: wire `pool.py`'s
      `picker_payload` to actually call `claims_rank.summarize_claims`
      against the driving worktree's real claim ledger for its
      `claims_summary` entry field, instead of the preview's hand-authored
      placeholder strings.
- [ ] Wire `entry.subtitle` into `pivots/agent-codespaces.json` so
      `pool.picker_payload`'s already-computed subtitle (claim/orphan
      detail) actually renders as the durable-title half of line two.
- [ ] Add any additional columns worth surfacing from `pool.picker_payload`'s
      fuller entry dict (per Phase 0 design note).
- [ ] agent-bridge live-session join keyed on
      `venue.kind == "codespace"` + `venue.target`, surfaced as the
      transient-activity half of line two.
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
- [ ] Wire Containers' claims-list column to the same `claims_rank` module
      Phase 1 wires — no second implementation.
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

### Phase 4 — Driving-worktree navigation + odsp-web PR auto-claim
- [ ] Implement the reserved driving-worktree mark on both pivots (stat
      slot or `[mark]` glyph per Phase 0 decision) and its two menu
      actions: jump to the driving worktree's Worktrees-pivot entry, and
      open its Worktree Status card directly.
- [ ] Confirm the claims-list column already reads the driving worktree's
      existing `agent-worktrees` claim ledger with no new storage (should
      require no new code beyond the existing cross-link, per Phase 1/2).
- [ ] Design and implement the odsp-web push→PR detection point (new: no
      existing hook watches for this) that calls
      `agent-worktrees claims add pr <ref>` on the driving worktree when a
      CodeSpace's pushed ADO branch produces a PR.
- [ ] Confirm the auto-claimed PR shows up in the claims-list with no
      manual step, and that it is visually indistinguishable from a
      manually-claimed one (same ledger, same rendering).

### Phase 5 — New-venue → embody design handoff (design only)
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
- [ ] Unit tests for the shared claims-pecking-order module: correct
      ordering across a mixed ledger (PR + bug + child worktree, etc.),
      correct truncation to "1-2 prominent," and stable behavior with an
      empty ledger.
- [ ] A live/manual check against a real CodeSpace and a real fleet
      container (not just fixtures) before Phase 1/2 are considered done,
      per this repo's "validate beyond unit tests" policy.
- [ ] Confirm the Containers pivot still never surfaces a non-fleet
      container after the parity changes (no regression on the existing
      fleet-only scoping).
- [ ] Unit tests for the driving-worktree mark/menu-navigation actions
      (jump-to-worktree, worktree-status-card) against fixed fixtures with
      and without a driving worktree.
- [ ] A live/manual check on a real odsp-web CodeSpace: push a real ADO
      topic branch, open the resulting PR, and confirm it appears in the
      row's claims-list with no manual claim step (Phase 4's own
      "validate beyond unit tests" case, not just a mocked push→PR
      fixture).

## Journal

- **2026-09-21 (latest+5)** — Implemented the shared claims-pecking-order
  module for real (the last open Phase 0 item):
  `plugins/agent-worktrees/src/agent_worktrees/claims_rank.py`
  (`rank_claims`, `format_claim`, `summarize_claims` — pure functions,
  `ResourceClaim`-shaped input, no I/O) plus
  `tests/test_claims_rank.py` (13 tests, all passing; ran the existing
  `test_claims_cmd.py`/`test_claim_handoffs.py` suites too — 83 total, no
  regressions). Grounded the exact pecking-order-to-kind mapping against
  `claims_cli._claims_add`'s real `valid_kinds` set while building it, and
  recorded in the module's own docstring that "bug"/"issue", "effort", and
  "bridge" aren't claimable kinds yet — the ranking degrades gracefully
  (unrecognized kind sorts last) rather than assuming they exist. Phase 0
  is now fully complete; Phase 1 has not started (the module exists but
  nothing calls it from the real `pool.py` yet — that's Phase 1's first
  task).
- **2026-09-21 (latest+4)** — Operator approved the Phase 0 screenshots and
  asked that they be **committed to the repo**, not left OneDrive-only —
  noting the Tasks-pane-ux effort's own previews never got this treatment
  (they still live only at `OneDrive/2026/09.17 agent-dispatch Tasks Pane
  UX Overhaul Previews`) and that a durable, version-controlled copy lets a
  later session diff a re-render against the *actually-approved* design
  rather than trusting memory or a local-only OneDrive folder. Copied the
  six approved PNGs into `design-previews/` (committed alongside this
  README, embedded/linked from the new "Design Previews" section) and
  marked Phase 0's operator-review item done. Phase 0 is now fully
  complete except the still-open claims-pecking-order module design.
- **2026-09-21 (latest+3)** — Operator reviewed the rendered screenshots and
  flagged the `driven` column: too much width for a boolean, and pointed at
  the Worktrees pane's own compact `sess`/`live` column (key `sess`, header
  "live", 4 chars, multi-valued: pulsing "●"/`PROC`/`LOCK`) as the right
  model. Dropped `driven` entirely — it duplicated what the already-present
  `worktree` cross-link column signals (non-blank = driven) — and adopted
  that same column verbatim (same key/header/width) for the one genuinely
  new signal, session liveness: `LIVE`/`IDLE` (`IDLE` already exists in the
  picker's own `state` palette)/blank. Updated `fake_pool.py`/
  `fake_fleet.py` (removed `driven`+`live` fields, added one `sess` field)
  and both proposed manifests (column + all three `when` gates), re-ran the
  render script — all six screenshots regenerated successfully — and
  re-synced the updated PNGs to the same dated OneDrive folder.
- **2026-09-21 (latest+2)** — Built and ran the Phase 0 preview tooling
  (`worktree-manager/scripts/picker-snapshot/venue-preview/`): hermetic
  demo Worktrees source, `fake_pool.py`/`fake_fleet.py` fixtures (4/3 rows
  each covering driven+live, driven+idle, undriven, and orphaned-lock
  scenarios), byte-copies of both real current manifests, and two proposed
  manifests. Built the `agent-worktrees` and `worktree-manager` `.venv`s in
  this worktree and rendered all six screenshots against the real Textual
  engine on the **first attempt** — no engine.py changes needed (confirmed
  the row grammar, driven column, and claims column are all achievable as
  pure manifest + backend-command work, using the exact same
  `subtitle_field`/`Column`/`worktree_field` mechanisms `worktree_title`
  auto-injection already proved out). Decided the driving-worktree
  indicator lives in a dedicated `driven` column (not the `[mark]` glyph),
  freeing `[mark]` for line two's own relation semantics — **later
  superseded by the entry above.** The shared claims-pecking-order module
  remains undesigned; the preview's `claims_summary` values are explicit
  placeholders. Next: operator review of the screenshots.
- **2026-09-21 (latest+1)** — Operator supplied a shared claims prominence
  ranking (PR > bug > effort > bridge > CodeSpace/container > child
  worktree > machine SSH > dispatch task, tunable; human-mappable/
  quick-find first, dispatch tasks penalized for lacking an externally
  referenceable id) meant to apply across every pivot's claims-list, not
  just this effort's two. Grounded: confirmed no prominent-claims-selection
  code exists anywhere yet (`engine.py`/`pivot_manifest.py` grep) — this is
  genuinely new shared infrastructure, proposed to live in `agent-worktrees`
  since it already owns the ledger being ranked. Added Phase 0 design task,
  Phase 1 build-it task, Phase 2 reuse-it task, and flagged in the vision's
  Non-Goals that aligning the Worktrees/Tasks panes' own prominent-artifact
  selection onto this ranking is a cross-vision coordination item, not
  something owned outright here.
- **2026-09-21 (latest)** — Operator refined the title/activity model and
  added new scope: `<title>` is a declared checkout intent (distinct from
  the venue's repo/spec identity, which stays a line-one fact); `<activity>`
  is an accumulating snagged-signal stream, not limited to agent-bridge.
  Added a new Phase 4: a reserved driving-worktree mark/navigation (view
  driving worktree, or its Worktree Status card — mirroring
  agent-dispatch's own direction) and an odsp-web-scoped PR auto-claim
  (a CodeSpace's pushed ADO branch auto-journals its resulting PR onto the
  driving worktree via the *existing* `agent-worktrees claims add pr <ref>`
  ledger — clarified in grounding that claims are that existing ledger, not
  a new store this effort invents; the only new piece is the push→PR
  detection point, which has no existing hook today).
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
