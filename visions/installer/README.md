# Installer & Configurator — Vision

- **Subject:** The **Worktree Manager** (a.k.a. the **Installer & Configurator**) —
  the standalone, out-of-plugin app that bootstraps a bare machine into a working
  copilot-extensions harness, remains the durable surface for configuring,
  validating, updating, and repairing it, and serves as the **optional worktree-
  and agent- control-plane** (picking, launching, and managing agent sessions) for
  those who want it. "Worktree Manager" is the product name for this one app across
  all three of its roles.
- **Scope:** branch (application intent; configuration detail lives in its child)
- **Status:** Active
- **Last revised:** 2026-10-23
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
current**: it is fetched by the one-line bootstrap and, when automatic
maintenance is explicitly adopted, **auto-updates itself**, out-of-band from any
Copilot session — precisely so the one piece that
*wraps* session launches can stay up to date without needing a session to update
it (a plugin cannot reliably update the very launcher that spawns it).
Automatic installation is off by default. An explicit user choice or a trusted
consuming harness's setup can adopt it for the addressed user or installation.

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
  adjust machine-local controls. Combined repository, knowledge, and plugin
  settings remain read-only, with their provenance visible. Its
  [configuration workbench](configurator/README.md) hosts plugin-owned panels
  and explicit requests for source changes. It is the same app as the installer,
  entered in its ongoing mode.
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
  its own when automatic maintenance is adopted**, out-of-band from any
  Copilot session, so the launcher never depends on a session to refresh it.
- **Presets** — a shareable configuration bundle a `<repo>-harness` plugin
  carries **as part of its own payload** (related-repo declarations, venue/
  CodeSpace product defaults, and more), so adopting the plugin preconfigures
  the whole work arrangement at once rather than assembling each piece by
  hand. A preset is adopted the same way the plugin itself is — no separate
  reference-resolution step to repeat. It carries only portable, repo-neutral
  defaults; account/identity configuration stays repo-owned and always wins
  (the existing provider-config-is-the-floor precedence,
  [`docs/patterns/codespace-repo-provenance.md`](../../docs/patterns/codespace-repo-provenance.md)),
  never shipped in the plugin bundle itself.

## Features

### repo-plugin-enablement
Shows a repository's effective marketplace and plugin enablement, including
inherited declarations and machine-local overrides. The Configurator adjusts
only supported machine-local enablement controls; it does not edit a
repository's versioned plugin settings. An explicit source-change request can
route a desired declaration change to the repository's own agent-driven
contribution flow, as defined by the
[configuration workbench](configurator/README.md).

### machine-and-connectivity-config
Helps author the **minimal multi-machine / SSH configuration** the harness needs to
reach beyond one box — the machine inventory and connectivity the control-plane's
machine-scoped views (and cross-machine dispatch) build on. Without at least this
minimum, a machine switcher has nothing to switch between; standing up that baseline
is part of making the harness turnkey and is the **source** the control-plane's
per-machine scope derives from. It authors the *minimum* to make multi-machine
legible and coordinates with (never replaces) a dedicated connectivity/SSH-mesh
layer where one is present. Direct adjustments are strictly machine-local;
versioned topology and knowledge-repository settings are shown read-only.

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
install, not only at bootstrap. Its
[configuration workbench](configurator/README.md) combines read-only effective
configuration with machine-local controls and provider-owned backstage panels.

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
The app is also the phased destination for **Terminal Fragment handling**
(which launch targets a machine's terminal app — Windows Terminal, Tabby,
... — carries a profile for, and mirroring that selection into real
terminal-app fragments): a machine-local concern relocating here the same way
Mux/AHP already did. **Per-project binstubs are a different, unrelated
artifact and are explicitly out of scope for this relocation** — deploying
and repairing the PATH launcher scripts a registered project resolves to
remains agent-worktrees' own responsibility; only the terminal-app *profile*
selection model and its fragment-mirroring CLI verbs move here. No
lightweight plugin carries or assumes any of these dependencies.

### plugin-updating-and-alignment
Keeps the installed plugin set **current and mutually consistent** — updates
plugins and their user-global runtimes, and checks that versions, shared
contracts, and configuration across plugins **align**, offering to reconcile
drift or a half-upgraded set. It **complements** each plugin's own
self-provisioning/self-reconciliation rather than replacing it: a plugin still
keeps *itself* healthy alone; the configurator is the place a human can see and
true-up the *whole set* at once. On a host that adopts the integrated
coordination product, the app is a client of that product's release authority
and installer. It shows what is selected, ready, and serving, and it can
request an update. It does not keep its own record of which release the host
runs, and it is never a second updater. See
[plugin-services *one-authority-per-lifecycle-decision*](../plugin-services/README.md#one-authority-per-lifecycle-decision).

### independent-runtime-source-selection
The installation flow can maintain its own source checkout independently of
Copilot's installed plugin payloads, developer checkouts, and the app's own
self-update source. A local opt-in selects current main, an explicitly stable
or exact older release, or a clean merged development snapshot. A run resolves
that choice once and realizes the same immutable source throughout.

Source acquisition and materialization leave the source checkout unchanged.
The selected runtime continues working when that checkout is unavailable.
Source failures preserve the current healthy installation and name the
blocked selection; they never substitute an ambient checkout or newer payload.
Ordinary marketplace delivery of skills, instructions, hooks and extensions
continues independently, including their non-deployment behavior.

### consented-background-maintenance
Manager owns the optional background maintenance of an installation, including
its enrollment, observable lifetime and update outcomes. It remains off unless
the user or an explicitly run trusted consuming-harness setup opts in for the
addressed user or local installation. A harness that installs Manager can
finish its approved setup with this maintenance enabled; opening or discovering
a repository alone does not authorize background installation.

Maintenance does not depend on an open Picker, a foreground Copilot session or
the machine-dependency manager. Current-user startup does not require an
administrative registration ceremony. Its platform lifecycle remains
unobtrusive, and disabled, pending-activation, blocked and healthy operation are
distinguishable.

The maintenance controller itself stays current through a safe upgrade, not
only its invoked updater. Replacement preserves an active update, permits only
one scheduling owner and does not wait for the next user logon. A failed
replacement preserves healthy operation or reports the precise recovery need.

One owner maintains the suite. An explicitly selected external controller is
an alternative to Manager-owned maintenance, not a concurrent updater.
Ownership changes preserve the existing installation and update authorities;
general machine/dependency convergence remains independent.

### single-installation-execution-owner
An installation has one background execution owner for its complete update
transaction: acquisition, runtime construction, activation and integration.
Ordinary CLI, agent, hook and maintenance callers submit durable requests rather
than becoming independent installers. Waiting observes that same operation;
closing a caller does not abandon its execution or result.

Exclusivity means one active installation operation for the same installation,
including its subordinate installer work, not ownership of unrelated services
or agent sessions. Lock and owner records identify the live installer process
and its source. They coordinate cooperation; stale records, an absent source
or an installer that is no longer running cannot permanently veto a new
installer. A fresh intact installer can assume control through the supported
normal or explicitly approved repair path without depending on the broken
installation to grant permission.

The execution owner has a standalone installation foundation available before
plugin provisioning, including first installation without an existing Python
runtime. A plugin can establish that foundation from its own installation
surface without requiring Manager, another plugin, or an independently
administered always-on coordinator. Foundation construction has one bounded,
recoverable owner; it is not a general-purpose exception for running plugin
installers. Once ready, first-use publication follows the same durable request
contract as later updates. Missing, incompatible or interrupted foundation state
is explicit, never permission to fall back to an independent installer.

Equivalent same-target requests debounce and share the active operation's
observable result; different-target requests wait or queue rather than race.
One authoritative desired-version selection supplies normal installation
targets, keeping automatic advancement monotonic instead of allowing competing
callers to cause accidental rollback. Deliberate repair or rollback selection
remains explicit. Pending, running, interrupted, blocked and completed outcomes
remain attributable and observable. Recovery does not mistake a failed prior
execution for success or silently replay it.
Existing per-slot construction and publication custody remains in force beneath
the transaction owner.

Installation recovery preserves live-cutover expectations. An interrupted
update does not make a host, operating-system or kernel restart a prerequisite
for further installation. The same installation authority can establish safe
process and transaction settlement on the running host, retain the actual
failure and continue through an explicit recovery path without disrupting
unrelated services. Uncertain execution is never relabeled success or replayed
merely to avoid a reboot.

Explicitly supervised debugging can execute directly only while holding the
same exclusive authority. Recurring scheduling is a separate, consented
responsibility: a maintenance trigger submits to the execution owner rather
than introducing another installation driver. Standalone plugins retain their
independent installation and recovery paths; adopting this suite-level owner
does not make Manager a mandatory plugin dependency.

### normal-and-repair-installation
Normal background installation limits trigger fan-out and converges on the
authoritative desired version. Same-version requests debounce; a different
version waits or queues behind the active installation. Blocking acquisition,
I/O, permission or execution failures leave clean failed-candidate state
without activating the failed version. Compatible zero-downtime handoff is
preferred, not a veto on the selected replacement: an incompatible or stalled
old service is retired by bounded cold takeover rather than defeating the new
installation solely because handoff failed. Success still requires the new
installation to be ready.

Explicit force/repair installation gives any intact authorized installer a
bounded way to take charge of its addressed installation, including when the
previous installation, queue or owner records are corrupt. Agents request
operator approval before invoking this disruptive mode. It may briefly attempt
cooperation, then terminates previous installers and older managed service
daemons within that installation's process scope, invalidates stale ownership
and installation-control state, and converges on its explicitly selected code.
An old installer cannot veto its replacement. Competing repair callers still
converge on one active writer rather than repeatedly killing one another.

A successful repair leaves old generations inert: no older managed installer
or daemon remains serving or able to reactivate itself through stale launch,
routing or maintenance state. Inert rollback artifacts need not be physically
erased. The repair preserves application data and unrelated installations;
its approval does not authorize an indiscriminate process kill or data wipe.
Zero downtime is not guaranteed. Failures remain explicit rather than falsely
claiming that all old code is retired or the new installation is ready.

### source-backed-maintenance
An opted-in installation stays healthy and current through regular,
observable maintenance at an explicitly selected bounded cadence. The
selected maintenance owner checks its dedicated source, advances the chosen
tracking ref when appropriate and invokes the same canonical installation
flow used interactively. Exact pins remain pinned; unchanged source reuses
verified immutable generations without rewriting them.

The flow covers the adopter's installed services and tools, including the
management tools needed to maintain them, without enabling unselected roles.
Manager self-update, service-runtime source and Copilot plugin-content delivery
retain their separate choices. Dirty or unavailable source, overlapping runs
and failed candidates produce attributable outcomes while preserving healthy
installations and working edits; they never cause an implicit fallback or a
second updater.

### exclusive-plugin-content-source
An adopter can separately choose approved local-checkout delivery of Copilot
plugin content instead of marketplace delivery. This is an explicit
replacement of the effective source, never parallel activation of both copies
of the same logical plugin. Competing registrations are detected even when
their display names differ. Source transitions preserve enablement intent,
are reversible, and verify effective pickup at the supported session boundary
without duplicate hooks, extensions, skills or instructions stalling loading.
Selecting a runtime source alone never changes the plugin-content source.

### resilient-update-convergence
An update pursues a verified good, current state rather than treating one
best-effort pass as success. Cheap deterministic blockers are discovered before
expensive work; costly and failure-prone preparation precedes short,
health-gated activation, followed by inexpensive local integration. Verified
progress survives retry. Transient failures receive safe retry and mitigation;
authority, policy and integrity blockers remain explicit and durable. Existing
lifecycle authorities, package governance and rollback remain intact.

### historical-generated-command-migration
Known generated commands from earlier versions migrate or retire automatically
when installation lineage and unchanged ownership can be established. A
physical payload relocation does not by itself require manual ownership
transfer. Foreign, edited or ambiguous commands remain protected, and an
unresolved required command is never reported as synchronized.
On legacy non-cell installations, project shortcuts delegate to the installed
user-side runtime command, not a source checkout or replaceable plugin payload.
Their generated targets are repaired by ordinary install and update flows.
Installation-cell entry points remain bound to their selected cell.

### change-driven-local-integration
Terminal profiles, shortcuts and other owned local projections are reconciled
once from stable update inputs through their narrow owning surfaces, without
rebuilding runtimes or copying whole payloads merely to render a profile.
Unchanged outputs avoid writes; missing or stale outputs are repaired even
when package versions are already current.

### self-updating
The app **keeps itself current on its own** — fetched by the one-line bootstrap
and thereafter auto-updating out-of-band from any Copilot session. Because it is
the surface that *wraps* session launches, it must never depend on a session (or a
plugin) to update the launcher.

### health-doctoring-and-validation
Inspects the live install for drift and breakage — missing prerequisites,
stale or broken binstubs, unmet plugin prerequisites, mis-registered repos — and
offers to repair, so a machine can be brought back to turnkey without an agent.

### harness-plugin-onboard-presets
Ingests a shareable preset **as part of a `<repo>-harness` plugin's own
payload** — related-repo declarations, CodeSpace/venue product defaults, and
more — discovered and merged automatically, the same layered way CodeSpace
config already merges a generic `config.yaml` across every adopted repo, to
preconfigure a specific work arrangement in one step. A plugin-carried preset
is adopted the same way the plugin itself is, with no separate
reference-resolution step a human must remember to repeat. It carries only
portable, repo-neutral defaults — never account/identity configuration, which
stays repo-owned and always takes precedence over anything a plugin bundle
supplies.

## Behaviors

### out-of-plugin-delivery
The app is delivered as its **own payload**, fetched and run directly — never
gated behind the plugin pipe, and never dependent on a Copilot session having
launched.

### non-agentic-and-programmatic
Installation, inspection, and direct machine-local adjustments are deterministic
and run without an AI agent in the loop. A human (or a script) drives them.
Submitting a source-change request is also an explicit programmatic action;
the separately dispatched worker, not the Configurator, interprets and carries
out repository maintenance under that repository's authority.

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
- **It is not a repository configuration editor.** Versioned harness,
  knowledge-repository, and plugin-source settings are read-only in the
  Configurator. Machine-local overrides and explicit dispatched delta requests
  do not authorize the app to write those sources directly.

## See Also

- Parent vision: none (top-level capability)
- Child vision: [Configuration workbench](configurator/README.md)
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

- **2026-10-12** — Clarified that, on hosts that adopt the integrated
  coordination product, plugin updating and alignment is a client of that
  product's release authority and installer, not a second updater.

- **2026-09-25** — Renamed *terminal-app profile selection and fragment
  mirroring* to **Terminal Fragment handling** and added an explicit
  carve-out: per-project binstubs (the PATH launcher scripts a registered
  project resolves to) are a different, unrelated artifact and stay
  agent-worktrees' own responsibility — only the terminal-app profile
  selection model and its fragment-mirroring CLI verbs relocate here.
  Operator direction while landing Phase 3e Step 6 (copilot-extensions#3390):
  the code-level cutover surfaced real ambiguity between "terminal handling
  of every kind" and the still-owned `repair --binstubs` surface, so this
  vision is tightened to match the intended, narrower boundary. See the
  mirrored provenance entry in
  [`visions/plugins/agent-worktrees`](../plugins/agent-worktrees/README.md).
- **2026-09-23** — Generalized the control-plane's ownership of terminal
  handling one step further: beyond Mux presentation and the AHP backend
  (#2062), **terminal-app profile selection and fragment mirroring**
  (currently `agent-worktrees profiles`/`terminal-fragment`/`repair`) is also
  relocating here, in phases — the same eventual direction as
  `agent-worktrees update` becoming `worktree-manager update`. Operator
  direction while scoping copilot-extensions#3360. See the mirrored
  provenance entry in
  [`visions/plugins/agent-worktrees`](../plugins/agent-worktrees/README.md)
  and the `worktree-manager-control-plane` effort's Phase 3e.
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
