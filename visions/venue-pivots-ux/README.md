# Venue Pivots UX (Codespaces & Containers) — Vision

- **Subject:** The Worktree Manager's already-registered **CodeSpaces**
  (`agent-codespaces`) and **Containers** (`agent-containers`) pivots —
  overhauled for presentation consistency with each other and with the
  Worktrees/Tasks panes, and for fidelity of the information each surfaces.
- **Scope:** leaf (concrete component; child of
  [agent-fabric](../agent-fabric/README.md), sibling of
  [picker](../picker/README.md))
- **Status:** Draft
- **Last revised:** 2026-09-21
- **Reality docs:**
  `worktree-manager/src/worktree_manager/production_picker/picker_tui/engine.py`
  (`WorktreesView._row`/`_column_subtitle`, `TasksView`,
  `_TASK_PHASE_PALETTE`) ·
  `worktree-manager/src/worktree_manager/production_picker/picker_tui/pivot_manifest.py`
  (`RegisteredPivot`, `Column`, subtitle/group/worktree field parsing) ·
  `plugins/agent-codespaces/pivots/agent-codespaces.json` (the CURRENT
  manifest) · `plugins/agent-codespaces/src/agent_codespaces/pool.py`
  (`picker_payload`, `build_pool`, `PoolMember`) ·
  `plugins/agent-containers/pivots/agent-containers.json` (the CURRENT
  manifest) · `plugins/agent-containers/src/agent_containers/__main__.py`
  (`_cmd_fleet`) and `fleet.py`/`lease.py` ·
  `plugins/agent-bridge/src/agent_bridge/models.py` (`LiveSessionInfo`,
  `LiveSessionVenue`) ·
  `efforts/active/picker-venue-pivots/README.md`

## Purpose & Intent

**Both pivots already exist and are not greenfield.** `agent-codespaces`
contributes a genuinely rich CodeSpaces pivot: a live pool view
(`pool --picker-json`, streamed) grouped by "repo @ account", with
health/occupancy/safety columns, a claiming-worktree cross-link column, and
gated Release/Recycle/Verify actions — presentation quality already close to
the Worktrees pane's own standard. `agent-containers` contributes a Containers
pivot scoped correctly to **fleet** members only (never a general Docker
browser — `fleet --json` was already built for exactly this pivot), but at a
fraction of the CodeSpaces pivot's fidelity: a flat badge list (no columns,
no grouping, no worktree cross-link, no actions at all), even though its own
`fleet --json` output already carries a `lease` (holder) field the manifest
never wires in.

Neither pivot surfaces what its own hosted agent is actually doing: agent-bridge
already tracks a hosted session's `LiveSessionInfo`/`LiveSessionVenue` (title,
latest reported progress, liveness, the reattach target), but neither
manifest joins on it. And the CodeSpaces pivot's own `pool.py` computes a rich
`subtitle` (claim/orphaned-lock detail) that the manifest never maps into
`entry.subtitle` — a wired-but-unused field, silently dropped today.

This vision is therefore an **alignment and completion** exercise, not new
construction: bring the Containers pivot up to the Codespaces pivot's
presentation fidelity (columns, grouping, worktree cross-link, actions) using
the *same* vocabulary and column shapes so an operator reads both the same
way; wire the CodeSpaces pivot's already-computed subtitle into its manifest;
and add the one genuinely new integration both pivots are missing — the
agent-bridge live-session join — so either pivot's row shows what its remote
Copilot session is actually reporting back, not just its container/venue
lifecycle state.

## Concepts & Components

### The row grammar — columnar line one, free-form "title - activity" line two

This vision pins down a **precise row grammar** on top of the Picker's
existing two-line rendering (`RegisteredPivot.subtitle_field` +
`WorktreesView._row`/`_column_subtitle`), so every venue row — and, as
Worktrees/Tasks converge on it, every pivot's row — reads the same way:

```
[ ] <id>  STATUS  <stat1> <stat2> <stat3>  <claim, claim, ...>
  [mark] <durable subject/theme title> - <transient current activity>
```

- **Line one is columnar** — it lines up across every row in the pivot, the
  same way the Worktrees/Codespaces column tables already do: a short id, a
  palette-coloured primary status word, a handful of compact secondary
  stat badges (the venue's own key-status axes — e.g. Codespaces'
  `health`/`occupancy`/`safe`), and a claims list (prominent PR/issue refs).
  Nothing here is prose; every field is a fixed-width or fit/shrunk column,
  chosen so a operator's eye can scan straight down any one column across
  every row.
- **Line two is free-form**, not columnar, and carries exactly one
  "**title - activity**" string: an optional leading **relation/mark**
  glyph identifying what kind of thing follows (a driving-worktree link, an
  orphaned-lock warning, a live-session marker — whatever the row's most
  relevant cross-link is), then the **durable subject/theme title** — the
  stable "what is this for" (a worktree's task title, a claimed PR's title,
  the CodeSpace's own repo/branch context) — a literal `" - "` separator,
  then the **transient current activity**: the most recent thing the row's
  actual driving agent reported doing, which **may be cut** (ellipsis-
  truncated) when it doesn't fit the render width. The durable title is
  stable across many renders; the transient activity is expected to change
  turn-to-turn and is the part most likely to be truncated first when
  space is short.

This is not a new rendering mechanism — `subtitle_field` and
`_column_subtitle` already exist and already truncate to width — it is a
**content contract** for what goes into that one field: durable title,
literal `" - "`, transient activity, with an optional leading mark. Today,
Codespaces drops its subtitle entirely (see below) and Containers' subtitle
is just `image` (a durable fact, no transient activity, no mark) — neither
currently follows this grammar. Bringing both into line is this vision's
concrete row-level target.

### Where "durable title" and "transient activity" come from

Consistent with *derive-never-duplicate*, both halves of line two are read
from whichever layer already owns them, never independently authored by the
pivot:

- **Durable subject/theme title** — the claiming worktree's own task title
  (already resolved by the picker's `_worktree_title_map`, the same value
  Codespaces' `worktree_title` column already carries) when a worktree is
  driving the venue; falling back to the venue's own stable identity (repo
  + branch, or the fleet/devcontainer spec name) when no worktree claims it.
- **Transient current activity** — agent-bridge's most recent reported
  beat for the live session in that venue (`LiveSessionInfo.latest_progress`
  / whatever title/intent a session last reported), when one exists; absent
  entirely (graceful-absence, not a placeholder) when no live session is
  registered for the venue.
- **Relation/mark** — a single glyph naming which of these signals is
  present and most relevant right now (driving-worktree link vs. an
  orphaned-lock warning vs. a live vs. idle session) — reusing whatever
  glyph vocabulary the Worktrees/Tasks panes already use for an equivalent
  distinction, not a new one invented for this pivot.

### Codespaces: already repo-grouped; wire the dropped subtitle, add the live-session join

The CodeSpaces pivot (`pivots/agent-codespaces.json`,
`pool.picker_payload`) already gets most of this right: entries are grouped
by "repo @ account" (repo-first identity), a compact RUNNING/STALE/STOPPED
status carries the palette, `health`/`occupancy`/`safe` columns carry
`agent-codespaces`' own lifecycle vocabulary, a `worktree` column already
cross-links to the claiming local worktree (resolved to that worktree's task
title via the picker's own `_worktree_title_map`), and Release/Recycle/Verify
actions are gated on disposition/safety. Two real gaps remain:

- **The dropped subtitle, and the missing "- activity" half.**
  `picker_payload` computes a `subtitle` (claim holder, cross-machine hold,
  orphaned-lock warning) on every entry, but the manifest's `entry` mapping
  never declares `"subtitle"`, so `subtitle_field` stays unset and the
  computed line is silently never rendered — a one-line manifest fix
  restores the **durable-title** half of line two. It still needs the
  **transient-activity** half appended (see next point) to fully match the
  row grammar's `"<title> - <activity>"` shape, not just the title alone.
- **No remote-session join.** Nothing today reads agent-bridge's
  `LiveSessionInfo`/`LiveSessionVenue` for a CodeSpace-hosted session. Where
  agent-bridge has a live registration keyed by `venue.kind == "codespace"`
  and `venue.target` matching this entry, the row's line two gains its
  `" - "` and transient-activity half from what agent-bridge already
  receives back: the session's latest reported progress/intent — genuinely
  new information, not a duplication of the existing worktree/task-title
  cross-link, which stays the durable-title half.

### Containers: bring to Codespaces' fidelity; same live-session join

The Containers pivot (`pivots/agent-containers.json`, `_cmd_fleet`) is
already correctly scoped to **fleet** members only — `fleet --json` was
purpose-built for this pivot and was never a general Docker-container
browser needing narrowing. But its manifest is far thinner than its
Codespaces sibling: a flat badge list (`id`/`title`/`subtitle`=image,
badges=[state, fleet]) with no `columns`, no `group`, no worktree
cross-link, and **zero actions** — even though `fleet --json` already emits
a `lease` field (the holding effort/worktree, the direct analogue of
Codespaces' `holder`/`worktree`) that the manifest never maps in. Bringing
this pivot to parity means:

- Declaring `columns` mirroring Codespaces' shape (container/fleet, state,
  lease→worktree, and whatever `security_profile`/`network` signal is
  genuinely picker-worthy) instead of the current bare badge list.
  Grouping by fleet (the container analogue of "repo @ account").
  Wiring `lease` to a `worktree` cross-link the same way Codespaces already
  does — giving line two a real **durable title** (today's subtitle is
  just `image`, a durable fact with no title/activity structure at all).
- Adding gated actions analogous to Release/Recycle/Verify — a container
  fleet member's own lifecycle (`lifecycle.py`, `lease.py`, `rescue.py`
  already model start/stop/remove/rescue) deserves the same menu treatment
  Codespaces already has, not a read-only list.
- The same agent-bridge live-session join as Codespaces, keyed by
  `venue.kind == "container"` and `venue.target`, supplying line two's
  **transient-activity** half exactly as it does for Codespaces.

Both pivots converge on one shared column vocabulary and lifecycle-state
palette so an operator reads a CodeSpace row and a fleet-container row the
same way, differing only in the fields each venue actually has (e.g.
`cores` for Codespaces, `security_profile` for Containers).

### Claims — the same shared surface Tasks already established

Both pivots reuse the "1-2 prominent claims inline, full graph on drill-in"
convention the Tasks pane's Prominent-Artifacts feature established, rather
than inventing a second claims rendering. A CodeSpace or fleet container that
is backing a claimed PR/issue (via its driving worktree, or a registrar that
claims venues directly) reads the same way a Task or Worktree row already
does.

### Open — into the muxed Copilot instance, over SSH

The single most important action on either pivot is **Open**: for a row that
already has a live or resumable agent-bridge session, Open attaches the
operator into that session's muxed Copilot instance over the fabric's SSH
transport — the same reattach mechanics `LiveSessionVenue.mux_session_name`
already exists to support, and the natural landing point for the parallel
**drive-CLI-agents-over-SSH** capability. Opening a row is meant to feel
identical whether the venue is a CodeSpace, a fleet container, or (today) a
local worktree — the operator picks *what* to open, not *how* the transport
works. Neither pivot has this action today (Codespaces' current actions are
Release/Recycle/Verify only; Containers has none), so this is genuinely new
for both, not a realignment of something existing.

### New codespace / New container — provision, then embody

A dormant pivot (no CodeSpaces or fleet containers exist yet, or the operator
wants another) offers **New codespace**/**New container**, prompting for the
target repo/devcontainer spec and any other required target info, then
provisioning the venue and handing the operator directly into a fresh Copilot
session inside it — the same eventual destination as **Open** on an existing
row, just preceded by a provisioning step. This is deliberately the same flow
an operator reaches by doing **New agent** against a dormant/idle venue: both
paths converge on "provision or select a venue, then embody a Copilot
session into it."

## Features

### row-grammar-consistency
Line one of every Codespaces/Containers row is columnar
(id/status/key-status/claims); line two carries exactly one
`"<mark> <durable title> - <transient activity>"` string. Codespaces gets
its already-computed durable title restored (currently dropped) and gains
the transient-activity half for the first time; Containers gains both
halves for the first time (today's subtitle is a bare `image` fact with
neither title nor activity structure).

### codespaces-pivot-parity
The CodeSpaces pivot keeps its existing repo-grouped, columnar,
action-gated shape, with its dropped `subtitle` wired in and a new
agent-bridge live-session join (title, latest progress/intent, liveness)
surfaced inline.

### containers-pivot-parity
The Containers pivot — already correctly scoped to fleet members only — gains
the columns, fleet-grouping, worktree/lease cross-link, gated lifecycle
actions, and agent-bridge live-session join needed to match the CodeSpaces
pivot's presentation fidelity, using the same shared column vocabulary and
lifecycle palette.

### open-into-muxed-session
A new **Open** action on either pivot attaches the operator to a live row's
muxed Copilot instance over the fabric's SSH transport, regardless of
whether the venue is a CodeSpace or a fleet container — neither pivot has
this today.

### new-venue-then-embody
"New codespace" and "New container" (and "New agent" against a dormant
venue) provision the target venue from the information the operator
provides, then carry the operator directly into a newly embodied Copilot
session in that venue — one continuous flow, not a provisioning step
followed by a separate manual attach.

## Behaviors

### derive-never-duplicate
Every field a Codespaces/Containers row shows is read from its owning
layer — `agent-codespaces`/`agent-containers` for venue identity and
lifecycle, `agent-worktrees` for the driving-worktree cross-link,
agent-bridge for live-session state — never independently tracked or
cached as a second copy inside the pivot itself.

### graceful-absence
A venue with no live agent-bridge session, no driving worktree, or no
claims renders its row with those fields simply omitted — never an error,
a stale value, or a blocked pivot load. A pivot with zero venues (no
CodeSpaces provisioned, no fleet yet built) renders an empty-state hint
with the New-venue action, matching the existing pivot-registry contract's
"a bad or absent pivot simply doesn't appear" defensiveness. When only one
half of line two's `"<title> - <activity>"` is available (a durable title
with no live session, or — never expected, but degrade the same way if it
occurs — activity with no resolvable title), the row shows that half alone
rather than a dangling `" - "` separator or a placeholder for the missing
half.

### transient-truncates-first
When line two doesn't fit the render width, the **transient activity**
half is truncated (ellipsis) before the durable title is touched — the
title is the stable fact an operator relies on to recognize the row across
renders; the activity is already expected to change turn-to-turn and is
the cheaper thing to lose first. The leading relation/mark glyph is never
dropped for width; it is the cheapest, most information-dense element on
the line.

### preview-before-implement
Every visual iteration on either pivot's row/menu/empty-state design is
validated as a **real, driven screenshot** of the actual Textual engine
(reusing the `picker-snapshot`/`tasks-preview` pattern) against a hermetic
demo data source, before any implementation PR — never a hand-drawn mockup.

## Non-Goals / Boundaries

- **Not a general Docker browser.** The Containers pivot already correctly
  scopes to fleet members only (`fleet --json` was purpose-built for it);
  this vision does not change that scope or attempt to surface every
  container on the host.
- **Not a new venue-lifecycle owner.** This vision adds presentation, a
  live-session join, and a provisioning entry point; it does not change how
  `agent-codespaces` or `agent-containers` themselves provision, lease, or
  reclaim venues.
- **Not a new live-session protocol.** Remote session state is read from
  whatever agent-bridge already receives back from a hosted session
  (`LiveSessionInfo`); this vision does not extend what agent-bridge itself
  collects or how a hosted session reports back.
- **Not a new row-rendering mechanism.** The two-line/columnar row grammar
  already exists generically (`subtitle_field`, `Column`); this vision uses
  it consistently rather than building a new renderer.

## See Also

- Parent vision: [agent-fabric](../agent-fabric/README.md)
- Sibling visions: [picker](../picker/README.md) ·
  [mux-companion](../mux-companion/README.md) ·
  [plugins/agent-dispatch/tasks-pane-ux](../plugins/agent-dispatch/tasks-pane-ux/README.md)
  (the precedent this vision follows for a pivot's UX overhaul) ·
  [plugins/agent-codespaces](../plugins/agent-codespaces/README.md) ·
  [plugins/agent-containers](../plugins/agent-containers/README.md) ·
  [remote-interactive-sessions](../remote-interactive-sessions/README.md) ·
  [venue-parity](../venue-parity/README.md)
- Child visions: none (leaf)
- Reality docs: see above

## Provenance

- **2026-09-21 (later)** — Operator specified the precise row grammar:
  columnar line one, free-form `"[mark] <durable title> - <transient
  activity>"` line two, with the activity (not the title) the first thing
  truncated. Added as the "Row grammar" concept and threaded through the
  Codespaces/Containers sections and a new `transient-truncates-first`
  behavior.
- **2026-09-21** — Conceived from an operator request to overhaul the
  under-served Codespaces/Containers pivots. Initially drafted as if
  neither pivot existed; corrected after re-grounding against the real
  `pivots/agent-codespaces.json`/`pivots/agent-containers.json` manifests
  and `pool.py`/`_cmd_fleet` source, which show both pivots already
  registered but asymmetric in fidelity (Codespaces rich/columnar,
  Containers thin/badge-only) — reframed as an alignment + completion
  effort (parity, dropped-field fixes, the missing agent-bridge
  live-session join) rather than new construction.
