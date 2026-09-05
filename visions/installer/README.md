# Installer & Configurator — Vision

- **Subject:** The **Worktree Manager** (a.k.a. the **Installer & Configurator**) —
  the standalone, out-of-plugin app that bootstraps a bare machine into a working
  copilot-extensions harness, remains the durable surface for configuring,
  validating, updating, and repairing it, and serves as the **optional worktree-
  and agent- control-plane** (picking, launching, and managing agent sessions) for
  those who want it. "Worktree Manager" is the product name for this one app across
  all three of its roles.
- **Scope:** leaf (concrete component; links its sibling capability visions)
- **Status:** Active
- **Last revised:** 2026-09-04
- **Reality docs:** [`docs/install-contract.md`](../../docs/install-contract.md) ·
  [`docs/architecture.md`](../../docs/architecture.md)

## Purpose & Intent

The friction this vision exists to abolish: a Copilot plugin is **inert until a
session launches**. Its installer — the code that actually creates binstubs,
runtimes, services, and config — does not run at delivery time. A user who
adopts copilot-extensions but never tells their agent to run the setup flows is
left with plugins "installed" yet **nothing on the machine built**: no core
runtime, no binstubs, no turnkey system. Bootstrap that relies on an agent
*remembering* to run install steps is bootstrap that silently fails — and the
failure is invisible until the user tries to use a command that was never
created.

The north star is a **single, obvious, out-of-plugin entry point** that takes a
bare machine to a working harness in one turnkey flow, and then **stays** as the
durable surface for keeping that harness healthy. It is delivered **outside the
plugin pipe** — its own payload, fetched and run directly — precisely because
the thing that must *guarantee* the plugins' prerequisites cannot itself be one
of those inert plugins. It is the one piece that must work *before* the plugins
do.

Crucially, this awareness runs **one way and carries no dependency**: the
installer *knows about* the plugins — their prerequisites, their configuration,
and "what to do" to make each ready — but **no plugin is built to depend on the
installer, and the installer is never a runtime dependency of a plugin**. Every
plugin remains independently installable and fully functional on its own; the
installer is a **knowing outsider** that sets things up, not a layer anything is
wired to.

Two roles at first, a third as the harness grows — **one app**:

- **Installer** (first run): from nothing to a working core — prerequisites, the
  user-global ground runtime for Copilot, and a first adopted harness repo —
  with no agent in the loop.
- **Configurator** (ongoing): the standing, **programmatic, non-agentic** surface
  for inspecting and adjusting the harness — doctoring, config management,
  plugin/prerequisite validation, **plugin updating and cross-plugin alignment**,
  and repo discovery/registration — reachable as the natural thing that happens
  when the harness is invoked with no project context.
- **Control-plane** (optional): the app is also the **optional worktree- and
  agent- control-plane** — the interactive front door for **picking, launching,
  and managing** worktree-backed agent sessions through whichever compatible
  **session-host provider** the user selects, with visual decision aids for
  choosing where and how to launch. A terminal multiplexer is one optional
  provider capability, not the control-plane's universal execution model. This
  is where the Worktree Picker lives. It is **optional** in the
  strongest sense: the plugins carry the in-session tools an agent needs to do
  its job and **provision and manage themselves — daemons included — with this
  control-plane entirely absent**. The control-plane makes the fleet *legible and
  launchable by a human*; it is never a prerequisite for the plugins to function.

Because it is the surface a user runs directly, the app also **keeps itself
current**: it is fetched by the one-line bootstrap and thereafter **auto-updates
itself**, out-of-band from any Copilot session — precisely so the one piece that
*wraps* session launches can stay up to date without needing a session to update
it (a plugin cannot reliably update the very launcher that spawns it).

Success is **turnkey**: a newcomer runs one line, answers a few prompts, and has
a coherent, self-consistent harness; and thereafter has one visual place to see
and fix how it is wired.

## Concepts & Components

- **The one-line bootstrap** — the published, top-of-README entry point (the
  familiar single-command `curl … | bash` / `iex …` shape) that fetches and runs
  the installer directly, independent of any plugin or prior harness tooling. It
  is the front door the inert-plugin failure mode requires.
- **Prerequisite layer** — the step that ensures the machine's foundational
  tools and the prerequisites of the user's selected session hosts exist,
  installing what is missing and **pausing for a restart when it changes the
  environment** (PATH, shell integration) so later steps run against a ready
  machine. A terminal multiplexer is required only when the user selects a host
  that depends on one.
- **Core install** — driving the harness's **own** real install flow (not a
  reimplementation) so the **core actually exists**: the user-global ground
  runtime for Copilot, its binstubs, and the baseline services every other layer
  builds on.
- **Harness-repo adoption** — bringing a first control/harness repo under
  management: an existing local checkout, a remote URL plus a chosen source root
  to clone into, or a deliberate skip — then adopting/registering the result so
  the system has something to drive.
- **Repo discovery & registration** — surfacing repos the machine already has
  (or a work arrangement expects) and offering to register them, so the mesh is
  populated without hand-editing registries.
- **The configurator surface** — a standalone, **non-agentic** visual wizard that
  presents the harness's real state (installed plugins and their prerequisites,
  machine/config, registered repos and accounts) and lets a human browse and
  adjust it. It is the same app as the installer, entered in its ongoing mode.
- **The optional control-plane (Worktree Picker & host selector)** — the
  interactive front door for the fleet of worktree-backed agents: viewing,
  joining, resuming, creating, and **launching** agent sessions through
  discovered providers, with visual decision aids for choosing *where and how*
  to launch before paying the cost. The Worktree Picker's role,
  guarantees, and interaction promises are defined by the
  [picker](../picker/README.md) vision; this app is where that surface is
  **delivered and kept current**. It is **optional**: a user who only wants the
  in-session plugin tools never has to run it, and the plugins are fully
  self-sufficient without it.
- **Plugin updating & cross-plugin alignment** — keeping the installed plugin
  set **current and mutually consistent**: updating plugins and their user-global
  runtimes, and checking that the versions, shared contracts, and configuration
  across plugins **line up** (no drift, no half-upgraded set), offering to
  reconcile what has fallen out of alignment. This complements — never replaces —
  each plugin's own ability to provision and reconcile itself.
- **Self-update** — because it is fetched and run directly (not through the
  plugin pipe) and *wraps* session launches, the app **keeps itself up to date on
  its own**, out-of-band from any Copilot session, so the launcher never depends
  on a session to refresh the launcher.
- **Presets** — shareable, **Git-referenced** configuration bundles a user can
  pull in to preconfigure a whole work arrangement at once (related repos,
  account/identity config, venue/CodeSpace settings), rather than assembling each
  by hand. A preset is a portable starting point, resolved by reference.

## Features

### repo-plugin-enablement
Sets a **repo's own enabled-plugin configuration** — declaring the marketplace(s)
and the plugins a repo turns on for the agents that run in it — by writing the
repo-level settings the Copilot CLI **reads but does not itself manage**. Enabling
the harness's plugins for a newly-adopted repo is a Configurator gesture, not a
hand-edit; the app owns this write-surface precisely **because the plugin pipe
won't** touch a repo's own plugin config. It merges rather than clobbers, chooses
from the plugin set it already knows, and is the natural completion of adopting a
repo.

### machine-and-connectivity-config
Helps author the **minimal multi-machine / SSH configuration** the harness needs to
reach beyond one box — the machine inventory and connectivity the control-plane's
machine-scoped views (and cross-machine dispatch) build on. Without at least this
minimum, a machine switcher has nothing to switch between; standing up that baseline
is part of making the harness turnkey and is the **source** the control-plane's
per-machine scope derives from. It authors the *minimum* to make multi-machine
legible and coordinates with (never replaces) a dedicated connectivity/SSH-mesh
layer where one is present.

### one-line-bootstrap
A single published command takes a bare machine into the installer without any
pre-installed harness tooling.

### prerequisite-provisioning
Detects and installs the foundational prerequisites, and **prompts to restart**
when a change requires it before continuing.

### core-install-via-real-flow
Installs the user-global ground runtime and baseline services by driving the
harness's **own** install flow, so the deployed core matches what the harness
expects rather than a parallel reimplementation that can drift.

### first-harness-repo-adoption
Offers three paths for the initial harness repo — adopt an existing local
checkout, clone a remote URL into a chosen source root, or skip — and registers
the result.

### repo-discovery-and-registration
Discovers candidate repos already present (or named by a work arrangement) and
offers to register them into the mesh.

### visual-configurator
A standalone visual wizard to view and adjust the harness — plugins &
prerequisites, machine/config, repos & accounts — usable long after first
install, not only at bootstrap.

### bare-invocation-launches-configurator
Invoking the harness with no project and outside any repo opens the
configurator, making the standing surface reachable by the most natural gesture.

### optional-worktree-agent-control-plane
The app **optionally** serves as the worktree- and agent- control-plane: the
interactive front door (the Worktree Picker) for viewing, joining, resuming,
creating, and **launching** worktree-backed agent sessions, with **session
management supplied by the selected execution host** and **visual decision
aids** for choosing where and how to launch. This surface is **additive and
optional** — the plugins
provide the in-session tools agents use and are fully functional without it — and
its role/guarantees are owned by the [picker](../picker/README.md) vision; this
app is where it is delivered and kept current. The interactive Picker opens by the
**single most natural gesture — a bare, no-args invocation** of a project's front
door; **every** other invocation routes programmatically to the plugins' own CLIs,
whether or not it is interactive. Execution is provider-neutral: the app
currently drives both the **TMux/PSMux presentation layer** and the **AHP
session backend** — composable, not exclusive, choices — for launch, resume,
and reattach, and may add ACP, SDK, App, or third-party hosts alongside them.
No lightweight plugin carries or assumes any of these dependencies.

### plugin-updating-and-alignment
Keeps the installed plugin set **current and mutually consistent** — updates
plugins and their user-global runtimes, and checks that versions, shared
contracts, and configuration across plugins **align**, offering to reconcile
drift or a half-upgraded set. It **complements** each plugin's own
self-provisioning/self-reconciliation rather than replacing it: a plugin still
keeps *itself* healthy alone; the configurator is the place a human can see and
true-up the *whole set* at once.

### self-updating
The app **keeps itself current on its own** — fetched by the one-line bootstrap
and thereafter auto-updating out-of-band from any Copilot session. Because it is
the surface that *wraps* session launches, it must never depend on a session (or a
plugin) to update the launcher.

### health-doctoring-and-validation
Inspects the live install for drift and breakage — missing prerequisites,
stale or broken binstubs, unmet plugin prerequisites, mis-registered repos — and
offers to repair, so a machine can be brought back to turnkey without an agent.

### git-referenced-presets
Ingests shareable presets by Git reference to preconfigure related repos,
accounts, and venue settings for a specific work arrangement in one step.

## Behaviors

### out-of-plugin-delivery
The app is delivered as its **own payload**, fetched and run directly — never
gated behind the plugin pipe, and never dependent on a Copilot session having
launched.

### non-agentic-and-programmatic
Every installer/configurator action is deterministic and runs without an AI
agent in the loop. A human (or a script) drives it; it never needs a session to
interpret intent.

### idempotent-and-re-runnable
Re-running the installer or configurator converges the machine to the intended
state without harm — already-satisfied steps are recognized and skipped, and
partial or broken installs are healed rather than duplicated.

### restart-aware
When a step changes the environment such that continuation would be unsafe, it
**stops and asks for a restart** rather than proceeding against a stale
environment.

### legible-and-consent-driven
It shows what it found and what it will do before acting; destructive or
scope-widening steps (installing, cloning, registering) are prompted, not
assumed — the user stays in control.

### onboards-from-empty-gracefully
On a bare machine the app is a coherent **onboarding home**, not a broken console.
Its surfaces present **what is missing and the action to provision it** (core, first
repo, machines, a repo's plugins) rather than dead-loading, and the interactive
control-plane lands **setup-first** until the fabric exists — shifting to the fleet
view once an engine and an adopted repo are present. A **non-interactive** bare
invocation yields a legible status + next-steps instead of forcing the TUI. (The
Picker's per-pivot empty-state behavior is owned by the [picker](../picker/README.md)
vision; this is the app-level commitment that first-run is guided, never a wall.)

### knows-the-plugins-without-coupling-to-them
The installer holds an **external, declarative understanding** of each plugin —
its prerequisites, its configuration, and what must be done to make it ready —
and reaches in to set things up and keep them healthy. The coupling is
strictly **one-way and dependency-free**: nothing reaches back. No plugin
imports, calls, or requires the installer, and the installer never appears on
any plugin's dependency graph. A plugin installed and run with the installer
never present behaves exactly the same; the installer's role is to *guarantee*
the plugins' prerequisites and interop, never to be a thing they are wired to.

### control-plane-is-optional-plugins-are-self-sufficient
The worktree/agent **control-plane** (picker and provider-backed session launch)
is a convenience layer, not a foundation. With it absent, every plugin still
**provisions and manages itself — its runtime *and* its daemons — and exposes the
in-session tools an agent needs**, driven by the plugins' own self-provisioning
model (see [plugin-services](../plugin-services/README.md)). The control-plane
makes the fleet *legible and launchable by a human*; it never becomes a
precondition for a plugin — or an agent using that plugin's tools — to function.
This is the same one-way, dependency-free rule as *knows-the-plugins*, applied to
the launcher role.

### self-maintaining-out-of-band
The app **keeps itself installed and current on its own** — via the direct
one-line bootstrap and its own auto-update — **out-of-band from any Copilot
session and from the plugin pipe**. It never relies on a session, an agent, or a
plugin to install or refresh the launcher; a plugin, conversely, never relies on
the app to keep *itself* current.

## Non-Goals / Boundaries

- **It is not a plugin, and must not be delivered through the plugin pipe.** It
  must not depend on a plugin being installed or a session being active to run.
  (Stated as a negative deliberately: this app must never be folded back into the
  inert plugin-delivery path that motivates its existence.)
- **It is not agentic.** It does not embed or require an AI agent and does not
  interpret free-form intent; it is a programmatic tool. As the optional
  control-plane it **launches and manages agent sessions**, but it is itself a
  deterministic, human- (or script-) driven surface — orchestrating agents is not
  the same as being one.
- **Its control-plane role is optional and additive, never a foundation.** The
  picker / provider-backed session-launch surface is a convenience for a human
  running the fleet. The plugins provide the in-session tools agents use and
  **self-provision and self-manage — daemons included — with this app absent**;
  the app must never become a prerequisite for a plugin (or an agent using its
  tools) to work. (The launcher-half of the worktree runtime relocating *into*
  this app must not smuggle in such a dependency.)
- **It does not replace per-plugin installers or own their runtimes.** It
  *orchestrates and guarantees* the harness's real install flows and each
  plugin's prerequisites, and can **update the set and true-up cross-plugin
  alignment**; it does not reimplement plugin runtime logic, and each plugin still
  keeps *itself* provisioned and reconciled alone. The relationship carries **no
  dependency in either direction**: a plugin never requires the installer to be
  present, and the installer never becomes a link in a plugin's dependency chain —
  it is a knowing outsider, not a layer in the graph.
- **It is not the service model or the coordination fabric itself.** How
  installed runtimes expose and reach one another belongs to the plugin-services
  vision; how agents coordinate belongs to agent-fabric. This app **ensures those
  are set up** — it is not those systems.

## See Also

- Parent vision: none (top-level capability)
- Sibling visions: [plugin-services](../plugin-services/README.md) (the service
  model it makes real; the source of plugins' self-sufficiency without this app) ·
  [picker](../picker/README.md) (the Worktree Picker — the control-plane's
  interactive surface, delivered and kept current by this app) ·
  [agent-fabric](../agent-fabric/README.md) (the fabric whose turnkey adoption it
  enables) · [session-hosting](../session-hosting/README.md) (the plural
  execution-provider boundary the control-plane selects and presents)
- Reality docs: [`docs/install-contract.md`](../../docs/install-contract.md) ·
  [`docs/architecture.md`](../../docs/architecture.md)

## Provenance

- **2026-08-10** — Conceived from the operator's diagnosis that plugin delivery
  leaves code **inert until a session launches**, so users who adopt the suite
  without running the setup flows never get the core (binstubs, runtime, config)
  built — surfaced concretely by a downstream user whose binstubs and Windows
  Terminal fragments were never created. Framed as the turn-key counterpart to
  the command-surface / mesh usability push. Mined from that conversation.
- **2026-08-12** — Broadened from "installer + non-agentic config wizard" to also
  be the **optional worktree- and agent- control-plane**: the Worktree Picker,
  session management, terminal multiplexing, and visual launch-decision aids move
  *into* this app, alongside a **plugin updater + cross-plugin alignment** role
  and explicit **self-update**. Reciprocal to making the plugins (agent-worktrees
  included) lightweight and **self-provisioning/self-managing — daemons and all —
  with this control-plane absent**. Rationale (operator): the app installs and
  updates **independently of any Copilot session** (via `curl … | bash` /
  `iex (irm …)` + its own auto-update), so it is the correct home for a launcher
  that *wraps* session launches and cannot update itself from within the sessions
  it spawns; meanwhile the plugins carry the in-session tools agents use and must
  stand alone. The one-way, dependency-free boundary is preserved and extended to
  the launcher role. Mined from the operator's direction during the plugin
  self-provisioning rollout.
- **2026-08-13** — Named the app the **Worktree Manager** (operator decision), and
  sharpened two control-plane facts: the interactive Picker opens **only on a bare,
  no-args invocation** (every other invocation routes programmatically to the
  plugins' CLIs), and **terminal multiplexing is an optional capability this app
  provides** — the lightweight plugins detect it and fall back to non-muxed when it
  is absent, so the heavy/invasive mux dependency never lands in them. Mined from the
  operator's Phase-6 design decisions (DQ6/DQ7/DQ9). The detailed installer flows,
  the bare-invocation binstub seam, and the never-break migration live in the
  dotfiles `installer-configurator` effort.
- **2026-08-14** — Added two Configurator write-surfaces and a first-run behavior,
  mined from a **clean-room pilot** of the golden path (upstream `worktree-manager`
  bootstrap → `setup` → adopt → enable plugins): `§Features/repo-plugin-enablement`
  (the app writes a repo's own `.github/copilot/settings.json` `enabledPlugins` +
  marketplace — the config the Copilot CLI reads but **does not manage**),
  `§Features/machine-and-connectivity-config` (author the minimal machine/SSH config
  the control-plane's Machines scope derives from), and
  `§Behaviors/onboards-from-empty-gracefully` (a bare machine gets a setup-first,
  guided onboarding home — surfaces show what's missing + the action, never a dead
  load). The pilot also surfaced install-side prerequisites for this to work:
  #540 (`setup` checkout discovery) and #541
  (unconditional tool-binstub deploy); the new surfaces are #543 / #544, and the
  Picker first-run behavior is #542. Umbrella #352; Phase 3/4 #356/#357.
- **2026-09-04** — Generalized the optional control-plane from owning terminal
  multiplexing to selecting and presenting pluggable session-host providers.
  TMux/PSMux-backed Copilot CLI remains one optional host alongside ACP, SDK,
  App, and third-party rigs; plugins and durable worktree state assume none of
  them.
- **2026-09-04** — Named the app as the current, near-term owner of **both**
  the Mux presentation layer and the AHP session backend (previously
  implemented inside agent-worktrees as an internal config branch). The two
  compose rather than exclude each other: an AHP-hosted session may still be
  Mux-wrapped for terminal access. Tracked by #2062.
