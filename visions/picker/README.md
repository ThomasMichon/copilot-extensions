# Worktree Picker — Vision

- **Subject:** The **Worktree Picker** — the interactive terminal front door
  through which an operator views, manages, joins, and creates the
  worktree-backed agents of a project.
- **Scope:** leaf (concrete component; child of the agent-fabric vision)
- **Status:** Active
- **Last revised:** 2026-09-04
- **Home:** delivered by the **Installer & Configurator** (the optional worktree-
  and agent-control-plane) — see [installer](../installer/README.md). It is an
  **optional** surface: the plugins provide the in-session tools agents use and
  are fully self-sufficient without the Picker present.
- **Reality docs:**
  [`plugins/agent-worktrees/docs/picker.md`](../../plugins/agent-worktrees/docs/picker.md) ·
  [`plugins/agent-worktrees/docs/architecture.md`](../../plugins/agent-worktrees/docs/architecture.md) ·
  [`plugins/agent-worktrees/docs/worktree-lifecycle.md`](../../plugins/agent-worktrees/docs/worktree-lifecycle.md)

## Purpose & Intent

The Picker is the **front door** to a project's fleet of worktree-backed agents.
Registering a repo in the worktree system gives it a bare binstub; running that
binstub with no arguments opens the Picker — **handed off to the Configurator's
control-plane** when it is present (with a plain help/CLI fallback when it is
not). From there the operator sees which agents exist and what they are doing,
**resumes** or **joins** one, **creates** a new one, and manages the fleet —
before any expensive launch is paid for.

Its north star is **informed, low-regret operator decisions**. Spinning up or
resuming an agent is *costly* — relaunching the Picker takes a moment, and
standing up a Copilot session takes longer still — so the Picker's whole reason
to exist is to let the operator decide **correctly the first time**, with enough
status in front of them that they rarely launch into the wrong worktree, on the
wrong machine, only to back out. The Picker earns its place by making the
*decision* cheap even though the *action* is not.

It is also the fabric's **coherent context anchor**. An operator drowning in
near-identical sessions must be able to glance at the Picker and the selected
session-host surface and know, unambiguously, **which project, which machine,
which provider, and which versions** this surface represents. Confusing one
execution context for another is a class of error the Picker exists to abolish.

Finally, the Picker is the fabric's **unified presentation surface**. It is
delivered by the **Configurator** — the optional worktree/agent control-plane
(see [installer](../installer/README.md)) — but owns no operational layer's home
itself. Every fabric layer an operator installs — **including the worktree ground
layer** — contributes its own pivot, actions, and configuration affordances
through the same presentation contract. The Manager supplies the generic shell,
interaction language, and onboarding floor; it never hard-codes a privileged
Worktrees client beside the contributed layers. The more of the fabric a user
adopts, the richer the Picker's capability becomes, without the base ever getting
heavier for someone who adopted less. And because it lives in the *optional*
control-plane, a user who wants only the in-session plugin tools need never run
it: the Picker enriches how a **human** drives the fleet; it is never a
prerequisite for the plugins or their agents to function.

## Concepts & Components

- **The front-door surface.** A single interactive entry — the bare project
  binstub — that opens onto the whole worktree lifecycle. There is one obvious
  way in, and it is the same for every registered project.
- **Pivots (top-level views).** The Picker is organized around a small set of
  contributed top-level pivots. The worktree ground layer contributes
  **Worktrees** and designates it as the ordinary fleet home when that layer is
  present; coordination and venue layers contribute Bridges, Dispatch,
  CodeSpaces, Containers, and their peers through the same contract. A
  right-aligned **Configuration** region hosts contributed settings-shaped
  surfaces (Profiles, SSH, MCP, …). No operational pivot is built into the
  Manager: pivots are the Picker's expression of the fabric's layered
  composition, and each installed layer lights up its own home.
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
  project, machine/venue, selected session-host provider, and the versions of
  the surfaces in play — coordinated with the active host so the two never
  disagree about the current context.
- **The launch handoff.** The informed transition out of the Picker into a
  worktree's Copilot session: it states plainly *which* worktree, on *which*
  machine/environment and through which provider, is about to be entered, and
  that a session will be created or resumed.
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

### first-run-onboarding-entry
On an **unprovisioned** machine — the fabric's ground layer (the worktree engine)
not yet installed, no machines yet configured — the front door still opens onto a
coherent **onboarding home**, not a broken console. The same bare gesture that
opens the Picker serves the newcomer: it lands **setup-first**, guiding the steps
that make the fabric real (install the core, adopt a first repo, configure a
machine, enable a repo's plugins), and it **derives its landing from context** —
setup-first while nothing is provisioned, shifting to the fleet (**Worktrees**)
home once an engine and an adopted repo exist. The door is the same one an expert
uses; only where it lands differs. Because it is the *interactive* front door, it
opens as the TUI in an interactive terminal and yields a legible textual
status/next-steps when invoked non-interactively, rather than forcing the TUI into
a non-terminal.

### decision-support-before-cost
The Picker presents enough status **upfront** — per fleet member and in
aggregate — that the operator can make the launch/resume/create decision without
first paying the cost of launching. Because the action it gates is expensive, the
Picker treats *surfacing the right information to decide* as its primary job, not
an afterthought.

### explicit-launch-target
Whenever the Picker is about to kick an agent off into a worktree, it makes the
**target unambiguous**: which worktree, on which machine and environment, which
session-host provider owns the interaction, and whether a session will be
created or resumed. The operator never launches unsure of where or how the
agent will run.

### consequential-vs-browsing-clarity
The Picker visibly distinguishes **browsing** (free, reversible, no side effects)
from **consequential** actions (creating, destroying, launching, mutating fleet
state). Consequential actions read as consequential; navigation and inspection
never masquerade as them.

### coherent-context
The Picker and the active session host coherently represent the current context
— project, target machine/venue, provider, and app/plugin versions — so an
operator with many similar sessions can always tell *which one this is*, *what
it targets*, and *which surface owns it*.

### at-a-glance-multi-machine
The fleet across **all** the fabric's machines is legible from a single Picker,
with strong at-a-glance state, so multi-machine coordination is painless rather
than a per-host chore. Where a fleet member lives is a scoping detail, not a
reason to open another terminal.

### plugin-pivot-extensibility
Every fabric layer an operator installs finds a **home** in the Picker — the
worktree ground layer included — as a top-level pivot, additional row or
view-scoped actions, waypoints, or a Configuration-region section, discovered
without the Picker hard-coding that layer. A contribution can identify the
ordinary landing surface without becoming privileged implementation inside the
Manager. The capability set **grows with adoption**: the more of the fabric a
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

The same capture seam serves **sharing**, not only auditing. Because the Picker
renders deterministically from injected data (see `§Features/programmatic-parity`),
a capture can be taken from **identity-obscured** data — real-shaped state with
its identifying particulars scrubbed — so a faithful, safe-to-publish image can be
produced without leaking private names. And because the render is driven through
the same keyboard the operator uses, a capture can span a **sequence of states**
(a scripted walkthrough — switching pivots, moving the selection, opening and
dismissing a menu), not just a single frame. One rendering path yields a test
assertion, an audit screenshot, a shareable still, and an animated walkthrough
alike.

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

### provider-neutral-composition
The Manager treats every operational layer through one contribution model.
Worktrees is not a privileged built-in data source or action path: the ground
layer contributes it just as coordination and venue providers contribute their
own surfaces. Provider identity may determine what is shown and which actions are
valid, but never which in-process presentation implementation the Manager uses.

### provenance-bound-contributions
Every contributed surface remains attributable to the exact installed provider
that owns it. The Manager invokes that provider's provenanced process boundary,
never an ambient same-named command or a runtime selected only by a bare plugin
name. Independently installed ecosystems can therefore contribute equivalent
surfaces without one installation's actions or state being silently served by
another.

### live-not-snapshot
What the Picker shows reflects **live** derived state, not a frozen snapshot: it
refreshes on demand, re-scans for newly-contributed layer surfaces, and surfaces
pending changes (such as a staged runtime update) rather than silently painting
stale data. When it cannot show something fresh, it says so rather than implying
currency it lacks.

### graceful-capability-scaling
With only the ground layer present, its contributed Worktrees surface makes the
Picker a **complete, coherent front door** — coarse but whole. Installing a
higher fabric layer *adds* its pivot, actions, or configuration section without
altering or breaking the base experience. Capability scales with what the
operator has adopted; nothing a lower configuration relied on is removed by
adding more. With no operational layer provisioned, the Manager's onboarding
floor remains coherent without pretending a missing provider's pivot exists.

### provisioning-aware-empty-states
With **no operational provider installed**, the Manager shows only its
provider-free onboarding floor: it does not synthesize a Worktrees or Machines
pivot whose owner is absent. Once a provider contributes a pivot, but its own
backing resources are incomplete — for example no configured machines or no
adopted repo — that pivot is **never a dead or blank load and never a bare
error**. It renders a **guided empty state that names what is missing and offers
the action to provision it**, so the absence becomes the next step rather than a
wall. This is the "never a silent dead end — always guide to the next correct
step" contract applied at both levels: onboarding for a missing provider,
provider-owned guidance for missing resources. The Picker never presents a
promise it cannot source; it presents the path to earning it.

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
- **Not a session host.** The Picker selects and invokes compatible providers,
  then reflects their identity and status. It does not own terminal
  multiplexing, Copilot processes, ACP sessions, SDK runtimes, or App windows.
- **Not in-process with the engine — it sits *on top of* the CLI.** The Picker runs
  as a **separate process** that reaches each layer's engine **only by invoking its
  machine-readable (`--json`) CLI verbs**, never by importing it in-process. All the
  underlying operations (creating, listing, repairing, doctoring worktrees, and the
  like) belong to the owning plugin's CLI; the Picker **calls and renders** them. A
  direct corollary: the Picker's **interactive-UI stack (its TUI framework) stays out
  of the plugin engines entirely** — so a lightweight, headless plugin never pulls a UI
  dependency, and the Picker can evolve independently. This is the runtime, process-level
  expression of *render-derive-not-own* and *programmatic-parity*.
- **Not a second store of fabric state.** The Picker renders and derives; it does
  not persist a competing copy of any layer's worktree, task, liveness, or
  identity state (see the fabric's derive-don't-duplicate boundary).
- **Not the owner of cross-layer semantics.** Lifecycle states, disposition and
  pulse, task records, machine reachability, and venue reach are **defined** by
  the owning fabric layers; the Picker surfaces them and must not redefine them.
- **No built-in operational provider.** The Manager does not carry a bespoke
  Worktrees table, worktree data model, or worktree-only launch/action path.
  Those affordances arrive from the ground layer through the same contribution
  model as every other operational surface. The Manager owns only generic
  presentation and interaction primitives plus the provider-free onboarding
  floor.
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
- Delivering app: [installer](../installer/README.md) — the **Installer &
  Configurator** is the optional worktree/agent control-plane that **delivers and
  keeps this Picker current** (out-of-band, self-updating). The Picker is an
  optional surface of that app; the plugins are self-sufficient without it.
- Execution-host sibling: [session-hosting](../session-hosting/README.md) — the
  provider-neutral launch/join/resume/cutover boundary the Picker drives but
  does not implement.
- CodeSpaces-pivot data owner: [agent-codespaces](../plugins/agent-codespaces/README.md)
  — the Picker's **CodeSpaces** pivot renders that venue's pool membership,
  per-venue state (in-use / idle / clean / stale), allocation, and budget
  headroom, which agent-codespaces **owns and defines**; the Picker renders and
  derives it (per *render-derive-not-own*), never redefines or re-stores it.
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
- **2026-07-25** — Extended `§Features/auditable-testable-rendering` so the
  capture seam also serves **sharing**: identity-**obscured** captures (real-shaped
  state, scrubbed particulars) for safe-to-publish imagery, and **state-sequence**
  captures (a scripted keyboard walkthrough) for animated demos. Mined from
  producing a README hero image from real fleet data and the wish for an animated
  pivot/selection/menu walkthrough — one deterministic render path serving test,
  audit, still, and animation.
- **2026-08-12** — **Relocated** the Picker's delivery from the agent-worktrees
  ground layer into the **Installer & Configurator** (the optional worktree/agent
  control-plane; see [installer](../installer/README.md)). Its role, guarantees,
  and interaction promises are **unchanged** — this is a home/ownership move, not a
  redefinition. The change makes the Picker an **optional** surface delivered and
  **self-updated out-of-band** by an app that installs independently of any Copilot
  session, reciprocal to making agent-worktrees lightweight and self-provisioning:
  a launcher that *wraps* session launches cannot update itself from within the
  sessions it spawns, so it belongs on the side that can. The bare project binstub
  still opens the Picker, now via a hand-off to the control-plane (with a plain
  CLI/help fallback when it is absent). Mined from the operator's direction during
  the plugin self-provisioning rollout.
- **2026-08-12** — Sharpened the boundary to a **process-level separation**: the
  Picker **sits on top of the engine CLIs as a separate process**, reaching them only
  through their `--json` verbs (never an in-process import), and its **TUI framework
  stays out of the plugin engines**. Recorded under *Non-Goals* as the runtime
  expression of *render-derive-not-own* / *programmatic-parity*. Motivated by keeping
  the agent-facing plugins lightweight and headless (no UI stack pulled into a confined
  host) while the interactive surface evolves on its own. Mined from the operator's
  Phase-6 boundary clarification; the mux/session-launch allocation (agent-bridge also
  hosts muxed sessions) is deliberately left as a downstream design question, not fixed
  here.
- **2026-08-14** — Added the **unprovisioned first-run** regime:
  `§Features/first-run-onboarding-entry` (the bare front door is also the newcomer's
  onboarding home — setup-first landing, context-derived, TUI-in-a-terminal /
  status-when-not) and `§Behaviors/provisioning-aware-empty-states` (a pivot with no
  source yet renders a **guided empty state + the provisioning action**, never a dead
  load — the zero-layer floor of graceful-capability-scaling). Mined from a clean-room
  pilot of the golden path (upstream `worktree-manager` bootstrap → `setup` → adopt →
  enable plugins): immediately after bootstrap the engine is absent (Worktrees has no
  source) and no machines are configured (the Machines switcher has nothing to switch),
  so the front door must guide provisioning rather than dead-load. Tracked as
  #542 (with #540/#541 the install-side prerequisites);
  see also #85 (advance-to-vision) and #357 (Phase-4 configurator).
- **2026-08-26** — Strengthened `plugin-pivot-extensibility` into fully
  **provider-neutral composition**: Worktrees is contributed by the ground layer
  through the same contract as every other operational pivot, rather than
  remaining a privileged Manager implementation. The Manager retains only the
  generic shell, interaction primitives, and provider-free onboarding floor.
- **2026-09-04** — Generalized launch and context coherence from one
  multiplexer-backed terminal to a selected session-host provider. The Picker
  remains the provider-neutral decision and presentation surface; CLI/mux, ACP,
  SDK, App, and third-party hosts own their own execution mechanics.
