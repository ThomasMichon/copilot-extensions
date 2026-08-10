# Installer & Configurator — Vision

- **Subject:** The **Installer & Configurator** — the standalone, out-of-plugin
  app that bootstraps a bare machine into a working copilot-extensions harness
  and then remains the durable surface for configuring, validating, and
  repairing it.
- **Scope:** leaf (concrete component; links its sibling capability visions)
- **Status:** Active
- **Last revised:** 2026-08-10
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

Two identities, one app:

- **Installer** (first run): from nothing to a working core — prerequisites, the
  user-global ground runtime for Copilot, and a first adopted harness repo —
  with no agent in the loop.
- **Configurator** (ongoing): the standing, **programmatic, non-agentic** surface
  for inspecting and adjusting the harness — doctoring, config management,
  plugin/prerequisite validation, and repo discovery/registration — reachable as
  the natural thing that happens when the harness is invoked with no project
  context.

Success is **turnkey**: a newcomer runs one line, answers a few prompts, and has
a coherent, self-consistent harness; and thereafter has one visual place to see
and fix how it is wired.

## Concepts & Components

- **The one-line bootstrap** — the published, top-of-README entry point (the
  familiar single-command `curl … | bash` / `iex …` shape) that fetches and runs
  the installer directly, independent of any plugin or prior harness tooling. It
  is the front door the inert-plugin failure mode requires.
- **Prerequisite layer** — the step that ensures the machine's foundational tools
  exist (a terminal multiplexer, a Python runtime, the Python package/venv
  manager, and kin), installing what is missing and **pausing for a restart when
  it changes the environment** (PATH, shell integration) so later steps run
  against a ready machine.
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
- **Presets** — shareable, **Git-referenced** configuration bundles a user can
  pull in to preconfigure a whole work arrangement at once (related repos,
  account/identity config, venue/CodeSpace settings), rather than assembling each
  by hand. A preset is a portable starting point, resolved by reference.

## Features

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

### knows-the-plugins-without-coupling-to-them
The installer holds an **external, declarative understanding** of each plugin —
its prerequisites, its configuration, and what must be done to make it ready —
and reaches in to set things up and keep them healthy. The coupling is
strictly **one-way and dependency-free**: nothing reaches back. No plugin
imports, calls, or requires the installer, and the installer never appears on
any plugin's dependency graph. A plugin installed and run with the installer
never present behaves exactly the same; the installer's role is to *guarantee*
the plugins' prerequisites and interop, never to be a thing they are wired to.

## Non-Goals / Boundaries

- **It is not a plugin, and must not be delivered through the plugin pipe.** It
  must not depend on a plugin being installed or a session being active to run.
  (Stated as a negative deliberately: this app must never be folded back into the
  inert plugin-delivery path that motivates its existence.)
- **It is not agentic.** It does not embed or require an AI agent and does not
  interpret free-form intent; it is a programmatic tool.
- **It does not replace per-plugin installers or own their runtimes.** It
  *orchestrates and guarantees* the harness's real install flows and each
  plugin's prerequisites; it does not reimplement plugin runtime logic. And the
  relationship carries **no dependency in either direction**: a plugin never
  requires the installer to be present, and the installer never becomes a link
  in a plugin's dependency chain — it is a knowing outsider, not a layer in the
  graph.
- **It is not the service model or the coordination fabric itself.** How
  installed runtimes expose and reach one another belongs to the plugin-services
  vision; how agents coordinate belongs to agent-fabric. This app **ensures those
  are set up** — it is not those systems.

## See Also

- Parent vision: none (top-level capability)
- Sibling visions: [plugin-services](../plugin-services/README.md) (the service
  model it makes real) · [agent-fabric](../agent-fabric/README.md) (the fabric
  whose turnkey adoption it enables)
- Reality docs: [`docs/install-contract.md`](../../docs/install-contract.md) ·
  [`docs/architecture.md`](../../docs/architecture.md)

## Provenance

- **2026-08-10** — Conceived from the operator's diagnosis that plugin delivery
  leaves code **inert until a session launches**, so users who adopt the suite
  without running the setup flows never get the core (binstubs, runtime, config)
  built — surfaced concretely by a downstream user whose binstubs and Windows
  Terminal fragments were never created. Framed as the turn-key counterpart to
  the command-surface / mesh usability push. Mined from that conversation.
