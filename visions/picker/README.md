# Worktree Picker — Vision

- **Subject:** The **Worktree Picker** — the interactive terminal front door
  through which an operator views, manages, joins, and creates the
  worktree-backed agents of a project.
- **Scope:** leaf (concrete component; child of the agent-fabric vision)
- **Status:** Active
- **Last revised:** 2026-07-25
- **Reality docs:**
  [`plugins/agent-worktrees/docs/picker.md`](../../plugins/agent-worktrees/docs/picker.md) ·
  [`plugins/agent-worktrees/docs/architecture.md`](../../plugins/agent-worktrees/docs/architecture.md) ·
  [`plugins/agent-worktrees/docs/worktree-lifecycle.md`](../../plugins/agent-worktrees/docs/worktree-lifecycle.md)

## Purpose & Intent

The Picker is the **front door** to a project's fleet of worktree-backed agents.
Registering a repo in the worktree system gives it a bare binstub; running that
binstub with no arguments opens the Picker. From there the operator sees which
agents exist and what they are doing, **resumes** or **joins** one, **creates** a
new one, and manages the fleet — before any expensive launch is paid for.

Its north star is **informed, low-regret operator decisions**. Spinning up or
resuming an agent is *costly* — relaunching the Picker takes a moment, and
standing up a Copilot session takes longer still — so the Picker's whole reason
to exist is to let the operator decide **correctly the first time**, with enough
status in front of them that they rarely launch into the wrong worktree, on the
wrong machine, only to back out. The Picker earns its place by making the
*decision* cheap even though the *action* is not.

It is also the fabric's **coherent context anchor**. An operator drowning in
near-identical terminal sessions must be able to glance at the Picker (and the
multiplexer that wraps it) and know, unambiguously, **which project, which
machine, and which versions** this surface represents. Confusing one terminal
session for another is a class of error the Picker exists to abolish.

Finally, the Picker is the fabric's **unified presentation surface**. It is
shipped by the ground layer (agent-worktrees) but is *not* only about worktrees:
every fabric layer an operator installs — coordination, delegation, connectivity,
venue providers, the vault, the model-context bridge — finds a **home** in the
Picker. The more of the fabric a user adopts, the richer the Picker's capability
becomes, without the base ever getting heavier for someone who adopted less.

## Concepts & Components

- **The front-door surface.** A single interactive entry — the bare project
  binstub — that opens onto the whole worktree lifecycle. There is one obvious
  way in, and it is the same for every registered project.
- **Pivots (top-level views).** The Picker is organized around a small set of
  top-level pivots. **Worktrees** is the home view; other fabric layers
  contribute their own pivots (Bridges, Dispatch, CodeSpaces, Containers, …) and
  a right-aligned **Configuration** region hosts settings-shaped surfaces
  (Profiles, SSH, MCP, …). Pivots are the Picker's expression of the fabric's
  layered composition: each installed layer lights up its own home.
- **Venue / machine scope.** Within a pivot, the operator scopes the view across
  the fabric's machines (and other venues) so the whole multi-machine fleet is
  legible from one place, not one terminal per host.
- **Fleet rows.** The entries within a pivot — worktrees, tasks, bridges — each
  carry an at-a-glance **status block** derived from the owning fabric layer:
  lifecycle state, sync position, the agent-asserted **disposition** and the
  passively-derived **activity pulse** (both owned and defined by the
  [agent-fabric](../agent-fabric/README.md) vision; the Picker renders, never
  redefines, them).
- **Action affordances.** The means by which the operator *acts* — buttons for
  view-scoped actions, a per-row action sub-menu, and dialogs for anything
  consequential or multi-step. Browsing and acting are visibly different kinds of
  thing.
- **The context header.** The always-present statement of *where you are*:
  project, machine/venue, and the versions of the surfaces in play — coordinated
  with the multiplexer wrapper so the two never disagree about the current
  context.
- **The launch handoff.** The informed transition out of the Picker into a
  worktree's Copilot session: it states plainly *which* worktree, on *which*
  machine/environment, is about to be entered, and that a session will be spun
  up.
- **The programmatic substrate.** Everything the Picker shows is obtainable from
  the underlying CLI's machine-readable (`--json`) verbs. The Picker is a faithful
  *renderer* of that data, not a separate source of truth.

## Features

### front-door-entry
Running a registered project's bare binstub opens the Picker, and the Picker is
the single obvious entry to the entire worktree lifecycle — view, join, resume,
create, and fleet management all reachable from it. A programmatic path exists
for automation, but the human's default door is this one, and it is uniform
across every project.

### decision-support-before-cost
The Picker presents enough status **upfront** — per fleet member and in
aggregate — that the operator can make the launch/resume/create decision without
first paying the cost of launching. Because the action it gates is expensive, the
Picker treats *surfacing the right information to decide* as its primary job, not
an afterthought.

### explicit-launch-target
Whenever the Picker is about to kick an agent off into a worktree, it makes the
**target unambiguous**: which worktree, on which machine and environment, and the
fact that a session will be created or resumed. The operator never launches
unsure of where the agent will land.

### consequential-vs-browsing-clarity
The Picker visibly distinguishes **browsing** (free, reversible, no side effects)
from **consequential** actions (creating, destroying, launching, mutating fleet
state). Consequential actions read as consequential; navigation and inspection
never masquerade as them.

### coherent-context
The Picker, together with the multiplexer that wraps it, coherently represents
the current context — project, target machine/venue, and the app/plugin versions
in play — so an operator with many similar terminal sessions open can always tell
*which one this is* and *what it targets*.

### at-a-glance-multi-machine
The fleet across **all** the fabric's machines is legible from a single Picker,
with strong at-a-glance state, so multi-machine coordination is painless rather
than a per-host chore. Where a fleet member lives is a scoping detail, not a
reason to open another terminal.

### plugin-pivot-extensibility
Every fabric layer an operator installs finds a **home** in the Picker — as a
top-level pivot, as additional per-worktree actions or waypoints, or as a
Configuration-region section — discovered without the Picker having to hard-code
that layer. The capability set **grows with adoption**: the more of the fabric a
user leverages, the richer the Picker becomes, and a user who adopted less is
never burdened by pivots for layers they don't have.

### programmatic-parity
Anything visible in the Picker is derivable from the underlying CLI's
machine-readable verbs, and any of the Picker's state could be reproduced from the
same data. The UX adds no state that a `--json` consumer could not also obtain —
the Picker is a view over programmatically-accessible truth, never a privileged
one.

### auditable-testable-rendering
The Picker's rendered output is **capturable and inspectable outside an
interactive session**. Any state it can present can be exported as a
human-auditable **screenshot** for review, and its rendered **character grid** is
obtainable programmatically so automated tests can assert *what the operator would
see* — focus, selection, status blocks, color-as-semantics, and scroll
affordances — without a person watching. Testability is a first-class property of
the front door, not an afterthought: the Picker's visual and interaction promises
are only real if they can be verified, so the means to verify them is part of the
subject, not external to it.

## Behaviors

### keyboard-first-navigation
The Picker is navigable **entirely and legibly from the keyboard**. Arrow keys
and Tab move between regions and widgets as the default, with an alternate mapping
only where one is *obvious* for a widget. Focus, selection, and interactive-widget
state are always visually clear. Keyboard shortcuts are welcome, but only when
they are obvious or their hint is shown — the Picker favors visible affordances
(buttons, focus regions, dialogs) over hidden macro-shortcuts the operator must
memorize.

### semantic-visual-language
The Picker's visual design carries meaning. Color conveys **semantic** status
(not decoration) within a coherent, recognizable palette; distinct kinds of
information are cleanly separated; and scroll position and scrollability are
always evident so the operator knows when more lies off-screen. The look is
consistent enough that a returning operator reads state by color and layout
before reading words.

### confirm-before-create-or-destroy
The Picker never creates or destroys fleet state without an explicit
confirmation. Reversible browsing and inspection require none; anything that
brings a worktree/agent into being or tears one down is confirmed first. Surprise
is not a valid outcome of a keypress.

### render-derive-not-own
The Picker owns no fabric state of its own. It **renders and derives** from the
state each fabric layer owns, at read time — an expression of the fabric's
derive-don't-duplicate rule at the presentation surface. When it shows a
worktree's lifecycle, a task's status, or an agent's pulse, that truth belongs to
the owning layer; the Picker is its faithful mirror.

### live-not-snapshot
What the Picker shows reflects **live** derived state, not a frozen snapshot: it
refreshes on demand, re-scans for newly-contributed layer surfaces, and surfaces
pending changes (such as a staged runtime update) rather than silently painting
stale data. When it cannot show something fresh, it says so rather than implying
currency it lacks.

### graceful-capability-scaling
With only the ground layer present, the Picker is a **complete, coherent front
door** — coarse but whole. Installing a higher fabric layer *adds* its pivot,
actions, or configuration section without altering or breaking the base
experience. Capability scales with what the operator has adopted; nothing a lower
configuration relied on is removed by adding more.

### renderable-and-assertable-headless
The Picker can be instantiated **headlessly** — no live terminal, no human, no
real fleet — fed a known context (its `--json`-shaped inputs), driven to a target
state (a chosen pivot, machine scope, focused row, open dialog), and have its
resulting render **captured for assertion**. Because the Picker is a
*deterministic renderer over programmatically-accessible truth* (see
`§Features/programmatic-parity`), the same inputs yield the same grid — so its
states are **regression-guarded** by tests that compare rendered output, and any
state is reproducible as a screenshot for audit. A visual or interaction
regression is something a test can catch before an operator does.

## Non-Goals / Boundaries

- **Not the editor or the agent session.** The Picker is the front door and the
  fleet console; it **hands off** to the Copilot session and does not replace the
  operator's interactive working surface once inside a worktree.
- **Not the multiplexer.** The Picker *coordinates context with* the mux wrapper
  but is a distinct surface with a distinct job; it does not own session
  multiplexing.
- **Not a second store of fabric state.** The Picker renders and derives; it does
  not persist a competing copy of any layer's worktree, task, liveness, or
  identity state (see the fabric's derive-don't-duplicate boundary).
- **Not the owner of cross-layer semantics.** Lifecycle states, disposition and
  pulse, task records, machine reachability, and venue reach are **defined** by
  the owning fabric layers; the Picker surfaces them and must not redefine them.
- **Not a specification.** This vision fixes the Picker's *role, guarantees, and
  interaction promises*, not its wiring — it does not pin a widget framework, key
  bindings, color values, screen layout, pivot manifest format, or command
  grammar. Binding detail of that kind lives in the reality docs.

## See Also

- Parent vision: [agent-fabric](../agent-fabric/README.md) — the layered agent
  coordination fabric; the Picker is its front-door presentation surface, and the
  fabric vision owns the legibility model (disposition vs. pulse, derive-don't-
  duplicate, uniform venue reach) the Picker renders.
- Sibling context: [plugin-services](../plugin-services/README.md) — the per-host
  service model the fabric layers deploy as.
- Reality docs:
  [`plugins/agent-worktrees/docs/picker.md`](../../plugins/agent-worktrees/docs/picker.md)
  (operator walkthrough) ·
  [`plugins/agent-worktrees/docs/architecture.md`](../../plugins/agent-worktrees/docs/architecture.md)
  (pivot registry, regions) ·
  [`plugins/agent-worktrees/docs/worktree-lifecycle.md`](../../plugins/agent-worktrees/docs/worktree-lifecycle.md)
  (states the Picker renders).

## Provenance

- **2026-07-25** — Initial authoring. Back-derived from the existing Textual
  Picker (`picker_tui/`) and its operator/architecture docs, then extended with
  operator intent: the Picker as the **front door** for engaging worktree-backed
  agents; **decision-support-before-cost** (informed choice before an expensive
  launch); **consequential-vs-browsing** safety and confirm-before-create/destroy;
  **coherent-context** across confusable terminal sessions (in concert with the
  mux wrapper); **keyboard-first / semantic-visual** TUI-quality intent;
  **at-a-glance-multi-machine** coordination; **plugin-pivot-extensibility** (each
  fabric layer finds a home; richer with adoption); and **programmatic-parity**
  (everything visible is derivable from `--json` verbs). Placed as a leaf child of
  agent-fabric because the Picker is that fabric's front-door presentation surface
  and spans every layer via pivots, while the fabric vision retains ownership of
  the underlying legibility model.
- **2026-07-25** — Extended with a **testability & validation** pillar:
  `§Features/auditable-testable-rendering` and
  `§Behaviors/renderable-and-assertable-headless`. Mined from operator intent that
  the Picker's visual/interaction promises must be *verifiable* — a human-auditable
  screenshot of any state, and a programmatic character-grid render that automated
  tests assert against — and framed as a deterministic-renderer property that
  rides on `programmatic-parity` (known inputs → known grid).
