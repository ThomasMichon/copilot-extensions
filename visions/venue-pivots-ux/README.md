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
  `plugins/agent-worktrees/src/agent_worktrees/claims_cli.py` (the existing
  per-worktree claim ledger — `pr`/`codespace`/`container` claim kinds) ·
  `plugins/agent-worktrees/src/agent_worktrees/claims_rank.py` (the
  implemented shared claims-pecking-order module) ·
  `plugins/agent-worktrees/src/agent_worktrees/claim_kinds_registry.py`
  (the implemented `.d/` drop-in registry) ·
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
pivot (see the following three sections for the full detail on each):

- **Durable subject/theme title** — a declared checkout intent when one
  exists, else the claiming worktree's own task title (already resolved by
  the picker's `_worktree_title_map`, the same value Codespaces'
  `worktree_title` column already carries), else the venue's own stable
  identity (repo + branch, or the fleet/devcontainer spec name) as a last
  resort.
- **Transient current activity** — the most recent entry in the venue's
  accumulating snagged-signal stream: agent-bridge's live-session beat
  today, a detected auto-claimed PR or other externally-observable event as
  that capability lands; absent entirely (graceful-absence, not a
  placeholder) when nothing has been snagged yet.
- **Relation/mark** — a single glyph naming which of these signals is
  present and most relevant right now (driving-worktree link vs. an
  orphaned-lock warning vs. a live vs. idle session) — reusing whatever
  glyph vocabulary the Worktrees/Tasks panes already use for an equivalent
  distinction, not a new one invented for this pivot.

### Checkout intent vs. venue identity — <title> is declared, not derived

A CodeSpace's durable identity is its **source repo** (and branch); a
container's is its **devcontainer/fleet spec**. Both are already
first-class, line-one facts (repo grouping, `fleet` column) and stay there
— they are *what the venue is built from*, not *why it is checked out right
now*. `<title>` is a different thing: the **checkout intent** declared when
a worktree borrows/creates the venue for a purpose ("reproduce #4021 on a
clean box", "spike the new auth flow") — the same kind of intent a task's
own charter already records for a worktree. When an explicit intent exists
it is the durable title; absent one, the title falls back to the driving
worktree's own task title, and only then to the venue's bare identity
(repo/branch or fleet name) as a last resort — never conflating "what this
venue is" with "what it's for right now."

### Snagged activity — an accumulating stream, agent-bridge is one source

`<activity>` is not limited to agent-bridge's live-session beat. As a
Copilot session actually works inside a venue, the fabric already snags
values along the way — a live-session progress beat is one instance of
that, but so is a detected git push, a newly opened PR, a completed CI run,
or any other externally-observable signal the venue's own activity
produces. This vision treats the *transient activity* half of line two as
the most recent entry in that accumulating stream, however it was snagged,
not as a hardcoded read of one specific field. Phase 1 grounds it in
agent-bridge's `latest_progress` (the one source that exists today); the
PR-auto-claim capability below is the first non-agent-bridge source, and the
row should be built so a future snagged signal slots in the same way.

### Auto-claiming a PR from its own pushed branch (odsp-web / ADO)

`agent-worktrees` already has a general claim ledger keyed by worktree
(`claims_cli.py`'s `claims add <kind> <ref>`, with `pr` and `codespace`/
`container` themselves already valid claim kinds) — **claims are not a new
store this vision invents**; the row's claims-list is the *same* ledger a
Task or Worktree row already reads, joined through whichever worktree is
currently driving the venue (derive-never-duplicate, same as the
title/activity cross-links). What is genuinely new: for odsp-web, most
Codespaces exist to push ADO topic branches that become PRs. Detecting that
a venue's own activity just produced a new PR from a pushed branch, and
auto-journaling it (`claims add pr <ref>`) on the driving worktree, means
the PR shows up in the claims-list **without** the operator or a registrar
manually claiming it — the natural, expected outcome of "I pushed a branch
from this box" becoming visible with no extra step. This is scoped to
odsp-web's ADO-backed flow to start; the detection mechanism (watching for
a push→PR transition) is new work, but the claim it produces rides the
existing ledger unchanged.

### Driving-worktree signal — not a boolean column; the existing `worktree` cross-link already carries it

An early draft of this vision proposed a dedicated `driven` boolean stat
column. Operator review correctly rejected it: it's a lot of real estate to
spend on a yes/no, and it's **redundant** — the pivot's existing `worktree`
cross-link column is already non-blank exactly when a worktree is driving
the venue. No second column is needed to say the same thing twice.

What *is* missing is the finer-grained signal underneath that link: is the
driving worktree's session actually **live** right now, or just present
but idle? The Worktrees pane already answers this exact question for
itself with its own compact `sess`/`live` column — a narrow (4-character),
multi-valued indicator (a pulsing "●" glyph when a mux session is live,
`PROC`/`LOCK` for other states), not a boolean. This vision's Codespaces
and Containers pivots reuse that **same column** — same key, same "live"
header, same width — rather than inventing a new one: `"LIVE"` when
agent-bridge reports an active session, `"IDLE"` when the worktree is
driving but no session is live (`IDLE` already exists in the picker's own
`state` palette), or blank when nothing is driving at all. One column, one
already-established vocabulary, reused instead of duplicated.

The row's action menu keys off the same signal: **view the driving
worktree's own Worktrees-pivot entry**, or **open its Worktree Status
card** directly — the same destination agent-dispatch's Tasks pane is
already building toward for its own embodied-task→worktree drill-in
(`visions/plugins/agent-dispatch/tasks-pane-ux`'s Worktree Status card) —
offered whenever the session column reads `LIVE` or `IDLE`, omitted
entirely (graceful-absence) when it's blank.

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

### Claims — one ledger, read through the driving-worktree cross-link

Claims are **not a new store this vision invents**: `agent-worktrees`
already journals a per-worktree claim ledger (`claims_cli.py`'s
`claims add <kind> <ref>`), with `pr` and `codespace`/`container`
themselves already valid claim kinds. The row's claims-list is that same
ledger, read through whichever worktree is currently driving the venue —
the identical derive-never-duplicate cross-link that resolves the durable
title, not a second claims rendering. A CodeSpace or fleet container with
no driving worktree simply has no claims to show (graceful-absence); one
with a driving worktree shows exactly what that worktree's own ledger
already carries, the "1-2 prominent inline, full graph on drill-in"
convention the Tasks pane's Prominent-Artifacts feature already
established. See "Auto-claiming a PR" above for the one new *producer* of
claim entries this vision adds — the ledger and its rendering are unchanged.

### Claims pecking order — one shared prominence ranking, everywhere

A worktree's claim ledger can carry several entries at once (a PR, an
issue, a claimed CodeSpace, a child worktree...), but line one's claims
slot only has room for the "1-2 prominent" the existing Tasks-pane
convention already limits itself to — so *something* has to decide which
1-2 win. This vision fixes that as one **shared prominence ranking**,
usable identically by every pivot that shows a claims-list (Worktrees,
Tasks, Codespaces, Containers), not a value each pivot picks for itself.
The ranking's organizing principle is **human-mappable, quick-find first**:
an entity a human can recognize and act on by its own external identity
(a PR number, a bug number) outranks one that is only ever meaningful
inside the fabric itself. Starting order (operator-supplied, expected to be
tuned as real usage surfaces exceptions):

1. Active PR
2. Active bug/issue
3. Active effort
4. Active bridge (agent-bridge agent/session)
5. Active CodeSpace/Container
6. Active child worktree
7. Active machine SSH session
8. Active dispatch task

Dispatch tasks rank last deliberately: a task id is essentially never an
independently-referenceable identity outside this fabric the way a PR or
bug number is, so even an active one is the least "human-mappable, quick
find" thing to lead with. This is a **starting, explicitly tunable**
ordering, not a frozen spec — the value this vision adds is fixing that
exactly one such list exists and is shared, not the specific order of any
one entry.

**Implemented (2026-09-21):** `agent_worktrees.claims_rank`
(`rank_claims`/`format_claim`/`summarize_claims`) is the real module —
pure functions over `ResourceClaim`-shaped entries (objects or plain
dicts), no I/O, fully unit-tested (`tests/test_claims_rank.py`). Grounded
against the real claim-kind vocabulary while building it:
`claims_cli._claims_add`'s `valid_kinds` today is only
`{worktree, codespace, container, ssh, workdir, pr, task}` — "bug"/
"issue", "effort", and "bridge" are **not yet claimable kinds** at all. The
module ranks whatever kind is actually present in a ledger (so it degrades
gracefully today, showing only PR/CodeSpace/container/worktree/task
claims); adding a "bug"/"issue" claim kind is the odsp-web PR auto-claim
concept's own prerequisite, not something this module does on its own.

### Claim-kind extensibility — a `.d/` drop-in registry, mirroring pivots

The pecking order's tiers name kinds this repo cannot yet produce a claim
for at all (bug/issue, effort, bridge). Rather than hardcoding every future
kind into `claims_rank`'s own table as each becomes claimable, this vision
adopts the **same cross-plugin contribution pattern the Picker's own pivot
system already uses**: a plugin drops one small file declaring what it
contributes, a discovery layer finds every installed plugin's drop-ins, and
every consumer picks up the merged result identically — no plugin ever
edits another plugin's file, and no consumer hardcodes a fixed plugin list.

Concretely (implemented, 2026-09-21):
`agent_worktrees.claim_kinds_registry` scans every installed plugin's own
`<plugin_root>/claim-kinds/*.json` (`{"kind", "priority", "label"?}`) and
merges the contributions onto `claims_rank.DEFAULT_PECKING_ORDER` — a
plugin can override an existing tier's priority or declare a brand-new
kind, and `claims_rank` itself never scans a filesystem to find out (stays
pure; the registry module does the I/O and hands it a plain mapping). This
is a **deliberately lighter** drop-in contract than the pivot registry's
own — no identity verification, no legacy-manifest migration, no separate
materialized-runtime-directory step — because a claim-kind declaration
carries no executable command to spoof; that machinery's entire reason to
exist doesn't apply to a bare priority integer and an optional label.

The practical payoff: when a future plugin (or this vision's own odsp-web
PR auto-claim work) needs a `bug`/`issue` claim kind to actually exist, it
does not touch `claims_rank.py` at all — it ships its own
`claim-kinds/bug.json` declaring the kind, its priority, and how it should
be labeled ("bug"), and every claims-showing pivot picks it up identically
the next time it resolves the effective pecking order.

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

### driving-worktree-session-and-nav
No separate "driven" boolean column — the existing `worktree` cross-link
already signals that. A narrow, multi-valued session column (reusing the
Worktrees pane's own `sess`/`live` column: `LIVE`/`IDLE`/blank) carries the
finer-grained liveness signal underneath it. The row's action menu gains
"view driving worktree" (jump to its Worktrees-pivot entry) and "worktree
status" (open its Worktree Status card directly), offered whenever that
column reads `LIVE` or `IDLE` — new on both pivots, mirroring
agent-dispatch's own Tasks→Worktree drill-in direction.

### codespace-pr-auto-claim
For odsp-web's ADO-backed flow, a CodeSpace's own pushed topic branch that
becomes a PR is auto-journaled (`agent-worktrees claims add pr <ref>`) onto
the driving worktree's existing claim ledger — no manual claim step, no new
claim store, just a new producer feeding the ledger the claims-list column
already reads.

### claims-pecking-order
Every pivot showing a claims-list (Worktrees, Tasks, Codespaces,
Containers) selects its "1-2 prominent" entries from one shared
prominence ranking (PR > bug/issue > effort > bridge > CodeSpace/container
> child worktree > machine SSH > dispatch task, tunable), not a
per-pivot ad hoc choice.

### claim-kind-dropin-registry
Any installed plugin can contribute a new claimable kind (and its
pecking-order priority/label) via a `claim-kinds/*.json` drop-in, mirroring
the Picker's own pivot-contribution pattern — no consuming pivot or the
ranking module itself hardcodes a fixed plugin list.

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

### one-ranking-many-consumers
The claims pecking order is defined **once**, in one place a claim-showing
pivot's rendering code calls into (not copy-pasted per pivot and not
recomputed differently by Worktrees vs. Tasks vs. Codespaces vs.
Containers). Tuning the order is a single-point change that every
consuming pivot picks up identically.

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
- **Not a new claim store or claim-rendering surface.** The claims-list
  reads `agent-worktrees`' existing per-worktree claim ledger through the
  driving-worktree cross-link; this vision adds one new *producer* (the
  odsp-web PR auto-claim) but no new storage or rendering path.
- **Not a general ADO/CI event pipeline.** PR auto-claiming is scoped to
  the specific push→PR transition odsp-web's ADO flow produces; this vision
  does not attempt to generalize to every possible externally-observable
  signal a venue could produce.
- **Not sole authority over the claims pecking order elsewhere.** This
  vision proposes and consumes the shared ranking, but the Worktrees and
  Tasks panes' own prominent-artifact selection predates it
  (`plugins/agent-dispatch/tasks-pane-ux`'s Prominent Artifacts feature) —
  aligning those panes onto this ranking is a cross-vision coordination
  item, not something this vision can unilaterally mandate onto another
  plugin's owned surface.

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

- **2026-09-21 (latest+4)** — Operator proposed a `.d/` drop-in system for
  claim-kind metadata, mirroring the Picker's own pivot-contribution
  pattern, so any plugin can declare a new claimable kind + priority
  without editing `claims_rank.py`. Implemented as
  `agent_worktrees.claim_kinds_registry` (scans installed plugins'
  `claim-kinds/*.json`, merges onto `claims_rank.DEFAULT_PECKING_ORDER`),
  with `claims_rank` itself gaining an optional `pecking_order=`/
  `label_overrides=` parameter so it stays pure/I/O-free while still
  consuming plugin contributions. 12 new tests, all passing (28 total
  across both modules). Added as the "Claim-kind extensibility" concept
  and `claim-kind-dropin-registry` feature.
- **2026-09-21 (latest+3)** — Implemented the shared claims-pecking-order
  module for real: `agent_worktrees.claims_rank` (pure functions over
  `ResourceClaim`-shaped entries, 13 passing unit tests). Grounded against
  the real claim-kind vocabulary while building it — `bug`/`issue`,
  `effort`, and `bridge` are not yet claimable kinds in `claims_cli`'s own
  `valid_kinds` — recorded as an explicit gap the module degrades
  gracefully around rather than papering over.
- **2026-09-21 (latest+2)** — Operator reviewed the rendered screenshots and
  flagged the `driven` column as too much real estate for a boolean,
  pointing at the Worktrees pane's own compact, multi-valued `sess`/`live`
  column as the right model. Dropped `driven` entirely (redundant with the
  already-present `worktree` cross-link column) and replaced it with that
  same narrow session column (`LIVE`/`IDLE`/blank), reusing its exact
  key/header/width rather than inventing a new indicator.
- **2026-09-21 (latest+1)** — Operator supplied a cross-pivot claims
  prominence ranking (PR > bug > effort > bridge > CodeSpace/container >
  child worktree > machine SSH > dispatch task — human-mappable/
  quick-find first, dispatch tasks penalized for lacking an externally
  referenceable id), explicitly tunable. Added as a shared "Claims pecking
  order" concept and `one-ranking-many-consumers` behavior; flagged as a
  cross-vision coordination item with `plugins/agent-dispatch/
  tasks-pane-ux`'s own pre-existing Prominent Artifacts feature rather than
  something this vision can unilaterally mandate there.
- **2026-09-21 (latest)** — Operator refined the title/activity model:
  `<title>` is a declared checkout intent, distinct from the venue's own
  repo/spec identity (already line one); `<activity>` is an accumulating
  "snagged" signal stream, not limited to agent-bridge. Added the
  odsp-web PR auto-claim capability (a CodeSpace's own pushed ADO branch
  auto-journals its resulting PR onto the driving worktree's existing
  claim ledger — clarified that claims are that same existing
  `agent-worktrees` ledger, not a new store) and a reserved
  driving-worktree mark/navigation slot (view the driving worktree, or its
  Worktree Status card, mirroring agent-dispatch's own direction).
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
