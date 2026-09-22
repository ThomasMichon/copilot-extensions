# Venue Pivots UX (Codespaces & Containers) — Vision

- **Subject:** The Worktree Manager's **Codespaces** and **Containers**
  pivots — bringing the fabric's remote-venue surfaces to the same
  presentation discipline the Worktrees pane (and, since its own overhaul,
  the Tasks pane) already established.
- **Scope:** leaf (concrete component; child of
  [agent-fabric](../agent-fabric/README.md), sibling of
  [picker](../picker/README.md))
- **Status:** Draft
- **Last revised:** 2026-09-21
- **Reality docs:**
  `worktree-manager/src/worktree_manager/production_picker/picker_tui/engine.py`
  (`WorktreesView`, `TasksView`, `_TASK_PHASE_PALETTE`) ·
  `worktree-manager/src/worktree_manager/production_picker/picker_tui/pivots.py`
  (`RegisteredPivot`, `Column`, `PivotAction`) ·
  `plugins/agent-codespaces/src/agent_codespaces/` (`status.py`, `lease.py`,
  `pool.py`, `worktrees.py`) ·
  `plugins/agent-containers/src/agent_containers/` (`fleet.py`,
  `lifecycle.py`, `resolver.py`) ·
  `plugins/agent-bridge/src/agent_bridge/models.py` (`LiveSessionInfo`,
  `LiveSessionVenue`) ·
  `efforts/active/picker-venue-pivots/README.md`

## Purpose & Intent

The Worktrees pane earned its discipline the hard way, and the Tasks pane
followed it: declarative columns, a state-derived colour palette, compact
status markers, a rich per-row action menu. The fabric's two **remote venue**
providers — CodeSpaces and local dev containers — power an increasing share
of where an operator's agents actually run, but the Picker gives them no
comparable home today: no registered pivot surfaces a CodeSpace or an
agent-shaped container as a first-class row at all. An operator managing a
fleet that spans local worktrees, CodeSpaces, and containers has one
disciplined view (Worktrees), a second now-disciplined view (Tasks), and two
venues visible only through raw CLI output (`agent-codespaces list`,
`docker ps`) or not at all.

This vision brings **Codespaces** and **Containers** into the Picker as two
new registered pivots, sharing one presentation grammar: a **two-line row**
(compact identity/status/claims on the first line, a descriptive string on
the second) that the Worktrees/Tasks panes are already converging on as the
picker's house style. Both pivots exist to answer the same question the
Worktrees pane answers for local checkouts — "what is this venue doing for
me, is it healthy, and can I get into it" — for a venue whose agent may be
running somewhere other than this machine.

Both pivots are scoped to the venues that matter for **agent embodiment**:
a CodeSpace or container that is (or could be) hosting a driven Copilot
session, cross-linked to whichever local worktree is currently driving it and
to whatever agent-bridge knows about the live session inside it. Containers
used as disposable runtime/validation sandboxes by an agent's own tool calls
are deliberately out of this surface's frame — see Non-Goals.

## Concepts & Components

### The two-line row — one grammar, three pivots

Worktrees, Tasks, and now Codespaces/Containers converge on the same row
shape: **line one** carries compact identity (id), a state-palette-coloured
status, a key-status marker (the venue's own most important secondary
signal — see below), and a claims summary; **line two** is a single
descriptive string giving the human-readable "what is this" context a bare
id/status line can't. A generic two-line renderer (extending the column-fit
work already landed for declarative pivots) means neither pivot invents its
own layout — they inherit the same fit/shrink/priority behavior the Tasks
pivot proved out.

### Codespaces: repo-first identity, worktree cross-link, live-session join

A CodeSpace's primary identity is **the repo it was provisioned for** — that
is the fact an operator scans for first, ahead of the CodeSpace's own opaque
name. The row's key status is drawn from `agent-codespaces`' own lifecycle
vocabulary (`status.py`'s active/recovered/prunable, `lease.py`'s
borrowed/idle, `pool.py`'s pool membership) — the same kind of palette-worthy
state the Worktrees pane already trains the operator to read.

Two cross-links complete the picture, both **derived, not duplicated**, from
data another layer already owns:

- **Driving worktree.** When a local worktree has embodied an agent into this
  CodeSpace (a CLI-mode Session Host reservation, or an ordinary headless
  spawn), that worktree's short id is the row's claim-equivalent — the same
  reverse cross-link `find_claiming_task` already performs for Tasks, joined
  here on the CodeSpace's own identity instead of a task id.
- **Remote session state.** Where agent-bridge has a live registration for a
  session hosted in this CodeSpace (`LiveSessionInfo`/`LiveSessionVenue`,
  keyed by `venue.kind == "codespace"` and `venue.target`), the row surfaces
  what agent-bridge already receives back from that session: its title,
  latest reported intent/progress, turn/liveness state, and driven-by
  identity — the same `latest_progress` beat the Tasks pane's embodied rows
  read, joined here by venue target instead of task id.

### Containers: fleet-first, agent-venue-scoped

`agent-containers` already distinguishes a **fleet** (a named pool of
repo-shaped dev containers built from one devcontainer spec, tagged and kept
warm for reuse — `fleet.py`) from an ad-hoc container any agent's tool calls
may spin up for build/test/validation. This pivot makes that distinction a
first-class UI boundary rather than an internal implementation detail: it
surfaces **fleet members** — containers that are themselves agent venues
(built from a repo's devcontainer spec, capable of hosting a driven Copilot
session, the container analogue of a CodeSpace) — and deliberately does not
attempt to be a general Docker-container browser. A generic runtime/sandbox
container an agent used and discarded is not this pivot's subject; see
Non-Goals.

A fleet-member row carries the same three signals as a CodeSpace row: repo
identity (which fleet/devcontainer spec it was built from), lifecycle key
status (warm/stopped/removed, borrowed/idle via the same lease broker
`fleet.py` already describes), and the same driving-worktree /
agent-bridge-live-session cross-links, joined on `venue.kind == "container"`.

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
works.

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

### two-line-row-grammar
Every entry in the Codespaces and Containers pivots renders as a compact
identity/status/key-status/claims first line and a descriptive second line,
sharing one generic renderer with Worktrees/Tasks rather than a bespoke
per-pivot layout.

### codespaces-pivot
A registered Codespaces pivot lists every CodeSpace the operator's account
can see, keyed by repo, with `agent-codespaces`' own lifecycle state as key
status, the currently-driving local worktree (if any) as a cross-link, and
whatever agent-bridge knows about a live session inside it (title, latest
progress/intent, liveness) surfaced inline.

### containers-pivot
A registered Containers pivot lists **fleet** members — repo-shaped,
devcontainer-spec-built containers capable of hosting a driven Copilot
session — with the same lifecycle/cross-link/live-session shape as
Codespaces. General-purpose Docker containers an agent used for build,
test, or validation are out of this pivot's frame.

### open-into-muxed-session
Selecting an existing, embodied row on either pivot attaches the operator to
that session's muxed Copilot instance over the fabric's SSH transport,
regardless of whether the venue is a CodeSpace or a fleet container.

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
"a bad or absent pivot simply doesn't appear" defensiveness.

### preview-before-implement
Every visual iteration on either pivot's row/menu/empty-state design is
validated as a **real, driven screenshot** of the actual Textual engine
(reusing the `picker-snapshot`/`tasks-preview` pattern) against a hermetic
demo data source, before any implementation PR — never a hand-drawn mockup.

## Non-Goals / Boundaries

- **Not a general Docker browser.** The Containers pivot does not aim to
  surface every container on the host, including ones agents spin up
  transiently for builds, tests, or validation runs. Only fleet members
  (agent-venue-capable, repo-shaped containers) are in frame.
- **Not a new venue-lifecycle owner.** This vision adds presentation and a
  provisioning entry point; it does not change how `agent-codespaces` or
  `agent-containers` themselves provision, lease, or reclaim venues.
- **Not a new live-session protocol.** Remote session state is read from
  whatever agent-bridge already receives back from a hosted session
  (`LiveSessionInfo`); this vision does not extend what agent-bridge itself
  collects or how a hosted session reports back.

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

- **2026-09-21** — Conceived from an operator request to overhaul the
  under-served Codespaces/Containers pivots, converging on the two-line row
  grammar already emerging on Worktrees/Tasks, and grounded against the
  actual `LiveSessionInfo`/`LiveSessionVenue` cross-venue model and
  `agent-containers`' existing fleet/generic-container split.
