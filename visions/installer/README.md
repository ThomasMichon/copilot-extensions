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
- **Last revised:** 2026-11-12
- **Reality docs:** [`docs/install-contract.md`](../../docs/install-contract.md) ·
  [`docs/architecture.md`](../../docs/architecture.md)

## Purpose & Intent

The friction this vision exists to abolish: delivered Copilot knowledge and
connectors are not an installed backend. Applications, commands, services and
configuration must be prepared through their independent installation owners,
before an agent needs them. Enabling a plugin must not construct those runtimes
from its payload or make a session responsible for remembering a hidden setup
step. Missing setup remains visible rather than failing only when a command is
first used.

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
installer, and the installer is never a runtime dependency of a plugin**.
Connector content remains independently deliverable, and each backend has a
complete standalone installation path without Copilot or Manager; the
installer is a **knowing outsider** that sets things up, not a layer anything is
wired to. Backend implementations and their installation inputs are distributed
outside plugin payloads.

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
  strongest sense: plugins supply in-session connection and knowledge surfaces,
  while independently installed components supervise their own declared lifetime
  with **this control-plane entirely absent**. The control-plane makes the fleet *legible and
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
  in-session connector never has to run it, and each required backend has a
  complete independent installation path without it.
- **Plugin updating & cross-plugin alignment** — keeping the installed plugin
  set **current and mutually consistent**: updating plugins and their user-global
  runtimes, and checking that the versions, shared contracts, and configuration
  across plugins **line up** (no drift, no half-upgraded set), offering to
  reconcile what has fallen out of alignment. This complements — never replaces —
  each component's independent installation and reconciliation owner.
- **Self-update** — because it is fetched and run directly (not through the
  plugin pipe) and *wraps* session launches, the app **keeps itself up to date on
  its own when automatic maintenance is adopted**, out-of-band from any
  Copilot session, so the launcher never depends on a session to refresh it.
- **Supplementary installables** - adopting repositories and their effective
  plugin selections contribute tools, services and applications to the same
  installation system. The [supplementary-installables vision](supplementary-installables/README.md)
  defines that extension boundary without another builder or lifecycle owner.
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

### authoritative-runtime-replacement

This is the durable completion contract for installation, explicit/manual
updates and repair. Installer bug fixes, implementation efforts and current
work evaluate their objectives and acceptance against it; an incremental
compatibility mechanism must not redefine the goal.

- **Explicit installation is authoritative.** An authorized user or agent's
  manual update replaces runtime code, old central-daemon generations, stale
  installer transactions, launchers and applicable service/task registrations
  within the selected installation. It is not factory reset. Configuration,
  credentials, repositories, worktrees, sessions and application databases
  survive.
- **The incoming installation stands on its own.** Acquire authentic, admitted
  source, construct fresh immutable runtime generations, start the incoming
  central services and publish their current-version selections and invocation/
  service registrations. Reuse, old-runtime/footprint recovery or migration and
  zero-downtime handoff are best-effort optimizations, not prerequisites.
  Durable-state compatibility or necessary state-preserving application-data/
  schema migration is required before admitting incoming mutable serving work
  and is part of safe readiness; it does not depend on old-runtime permission.
  Missing old monitor
  capabilities, unknown legacy layout/custody/provenance, maintenance banners,
  stale locks, transaction records or queue fences cannot veto this explicit
  replacement. Preserve prior attempts' real outcomes without requiring their
  recovery protocol to grant the incoming installation permission.
- **Success means incoming readiness and complete selection.** Every requested
  incoming runtime is ready, required central services are actually running
  that generation, and all applicable current-version pointers, routes,
  launchers and service/task registrations select it. Accepted/queued work,
  candidate construction, Manager-only success or a pointer without serving
  proof is not whole-installation success. Report real acquisition, build,
  authentication, permission, I/O and incoming-health failures honestly; never
  activate a failed candidate or call a partial result complete.
- **Retirement is independent of success.** Outgoing central daemons detect
  that they are no longer current, close admission, attempt handoff/drain and
  clean up their own obsolete runtime registrations. Admission closure and
  transfer/exclusion of conflicting work may precede incoming serving readiness;
  neither waits for installation completion. An uncooperative predecessor is
  handled through the incoming owner's bounded transition, not an indefinite
  old-lease wait. Final predecessor exit and cleanup remain independent.
  The installer does not
  wait for or fence success on their drain, exit or cleanup. It may launch
  one finite asynchronous fallback for the addressed replacement, after a
  grace period longer than expected cutover, to force-retire remaining
  outgoing central daemons and obsolete central-service schedules. This is
  optional, not a periodic resident reaper or another installer.
- **Retirement is role- and generation-scoped.** Session hosts, session keepers,
  session-owned Bridge/agent processes, foreground clients and protected
  connectivity are never fallback targets. A central service's name does not
  authorize killing its session children. Revalidate native process identity,
  explicit central-service role, installation scope and the *then-current*
  generation/registration at the actual mutation boundary. Never kill a
  reselected generation, a later successor or an unrelated process, nor use a
  generic interpreter-name sweep. Unprovable cleanup is reported and skipped,
  not converted into an installation veto or fictitious exit proof.
- **Only automatic update is suppressible.** KillSwitches and maintenance
  inhibition govern background scheduled, sessionStart and Manager-launch
  automatic updates, with classification preserved end to end. They never
  suppress an explicit/manual update or ordinary use of a healthy installation.
  Explicit replacement does not clear those automatic preferences.

Incoming source authenticity and the user's authority over the addressed
installation remain required. These are not provenance tests against the old
installation. Concrete conflicting writes may be coordinated internally by
bounded wait/join or replacement so the explicit update proceeds; preserving an
old serialization protocol is not a goal or admission requirement. Neither a
recorded nor a running installer may create persistent denial, an indefinite
queue fence or a separate operator-recovery prerequisite. The incoming installer
owns that bounded continuation within the addressed scope, without broadly
terminating unrelated processes. Actual OS I/O/permission failures remain
truthful failures; an old owner/custody record is not such a failure.

This contract is intent, not a claim that every installer already realizes it.
The [architecture adoption boundary](../../docs/architecture.md#replacement-lifecycle-target-and-current-adoption)
and [install contract](../../docs/install-contract.md#authoritative-replacement-and-completion)
distinguish implemented mechanisms from remaining work.

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

The caller may optionally select a Git repository for adoption in that same
attended transaction. The installer establishes its core toolkit first, then
acquires the selected repository through Git's configured credential owner and
hands off to explicitly trusted, repository-owned setup. Repository adoption
alone does not change the Manager's own installation or update source; an
explicit repository-suite source operation is a separate choice. Omitting
repository selection preserves the standalone Manager journey.

The neutral installer does not absorb the selected repository's organization,
private knowledge, machine inventory or connectivity policy. Authentication
remains with its provider, including a genuine interactive opportunity when
needed; access failure is not permission to substitute another account or source.
An unfinished repository stage remains explicit without discarding a completed
Manager installation or claiming full adoption.

The upstream entry establishes Manager first from a trusted staged source and
complete installation inputs, independently of Copilot, installed plugin
payloads, an old runtime command or interactive shell initialization. Default
selection follows live upstream Main; explicit locally promoted installation
snapshots and admitted source checkouts/commits remain supported. Last-known-good
Main ancestors retain their own release identity.

### staged-repository-onboarding

The public foundation is available before authentication for a selected private
harness: foundational tools, Manager and the core applications needed to carry
the setup journey do not depend on access to that harness. Repository selection
is explicit caller input, never a private repository baked into the installer.

After the selected repository's own access requirements are satisfied, its
declared configuration determines the additional applications and services to
prepare. Complete that selected dependency closure through its installation
owners before normal task use; unrelated optional capabilities are not silently
enabled. Public acquisition, repository access, installed application readiness,
provider authentication and usable task readiness remain distinguishable stages.

The resulting project front door is usable from a fresh terminal with the
intended tool and interpreter environment, not merely from the installer's
temporary PATH. Manager can guide remaining attended authentication and
configuration before the first task without requiring an agent session to
finish onboarding. An early visual handoff is not an installation-complete claim.

### legacy-force-fresh-installation

The repository README exposes a standalone force-fresh installation script for
operators stranded on arbitrary versions of the pre-wheel installation system.
It reaches the first-party-wheel/uv installation flow without requiring a
working old Manager, launcher, installer protocol or particular legacy version.
Legacy metadata is useful diagnostic input, not a prerequisite for applying the
addressed fresh installation.

The operation replaces addressed legacy runtime code and registrations while
preserving configuration, durable data, active work and protected session and
connectivity dependencies. Its intended scope and actual effects are legible;
unrelated installations and unknown external resources are not blanket cleanup
targets. Installation success, outgoing retirement and unresolved remnants
remain distinct outcomes under the authoritative replacement contract.

This is the explicit clean break into the new installation model. Subsequent
generations within that model maintain their declared compatibility and
supported recovery behavior; the migration path is not a permanent excuse to
break future callers or erase user state.

### harness-selected-continuity-bootstrap

A harness README can compose the standard upstream setup with a portable
selection of its harness/worktree configuration context. That context declares
the expected user-owned continuity source; an adopter does not need a copied
installer or machine-specific setup command to express the arrangement.
Standalone installation remains available without a harness, continuity store
or Gateway.

For a selected integrated fleet, setup consumes the user's already-established
authenticated access to that source to obtain Gateway discovery and the
delegated authority needed to provision this machine. Source/provider selection,
current credentials and enrollment remain with their existing owners.
Access to public configuration or a similarly named local folder is not
bootstrap authority. An already-authorized source does not trigger a redundant
human ceremony; an actual provider or enrollment challenge stays explicit.

Cloud synchronization, private repositories and other protected user-state
providers compose through the same contract without a mandatory storage or
identity vendor. A managed machine can authenticate outbound to its Gateway
before SSH is available; an existing authorized SSH provisioning path remains
usable. Initial reach, source access, enrollment and complete connectivity have
separate, truthful outcomes. Setup does not create a session or expand fleet
administration merely because it selected a harness.

### prerequisite-provisioning
Detects and installs the foundational prerequisites, and **prompts to restart**
when a change requires it before continuing.

### core-install-via-real-flow
Establishes Manager through the real upstream installation owner, then derives
wanted local tools and services from a selected repository or an explicitly
addressed registered-repository set. Complete per-application uv inputs and
associated wheel closures are prepared with bounded parallelism, without
inventing a second installer or using plugin payloads as runtime source.

Setup owns completion and readiness; preparation can remain separate from
activation. An operator-selected atomic local set admits every incoming member
before coordinated selection, preserves the last good set and supports
state-compatible rollback. Independent components remain usable without a
mandatory Manager or group transaction.

### owner-scoped-independent-recovery

The same trusted self-install entry remains reachable when an old launcher,
environment or redirect is broken. Read-only doctor, inventory and native
transaction evidence precede a disruptive recovery. Positively owned incomplete
candidates can be isolated and reconstructed fresh without wiping durable
configuration/data or unrelated installations.

Stable global command redirects and declared consumer registrations survive
source staging and plugin removal. A running legacy launcher is not rewritten
inside its own execution stream. Unknown incumbent ownership limits reuse and
cleanup, not explicit runtime replacement under
[authoritative-runtime-replacement](#authoritative-runtime-replacement).
An inaccessible destination or unrelated installation remains outside that
authority; never escalate privilege or adopt a different source to hide the
failure. A recorded or running old installer instead receives bounded internal
coordination/replacement, not a persistent refusal or another approval demand.

Raw self-install and rollback remain usable without the old Manager, tool
commands, updater queue or services being replaced. Core source/toolchain
construction comes before repository adoption and its authentication.
Within the new model's support window, old installed wrappers/connectors either
remain compatible or receive a supported, authorized refresh before activation.
The pre-wheel migration uses the standalone force-fresh path instead of requiring
legacy wrapper compatibility. Installed payload files are not patched ad hoc.

Manual rescue authority is distinct from automatic-update admission. It does
not clear another maintenance owner or a global automatic hold, and never grants
unaddressed fleet rollout. Incoming native readiness, routing selection,
preserved state and predecessor retirement are observed separately.

### prepared-independent-maintenance

One admitted owner prepares updates after foreground Manager load or on an
optional long cadence. A later worktree/session launch can select a fully
prepared toolset without first-use environment rebuilding. The caller waits
for admitted construction/start outcomes, not complete background draining.

Plugins remain bounded discovery and guidance connectors, not runtime
installation triggers. Prepared reconciliation/instruction/hoist selections
keep fresh worktrees correct: current managed bodies reach model discovery
before execution, with reviewed fallbacks only when current bodies are absent.
An optional once-per-session prepared-update notice does not create another
maintenance loop or claim activation.

Repository-derived service selection respects role, locus, provider and
installation ownership. Missing or incomplete declarations never imply
automatic uninstall. Automatic inhibition leaves ordinary healthy launch/use
available. Manual replacement preserves an unknown prior attempt's outcome
without making its settlement a prerequisite for the new installation.

Only an explicitly automatic update request is inhibited by the update switch.
Manual update without the automatic flag, raw self-install, setup/install-source,
doctor/list and ordinary launch still reach their normal owner. A launch's
skipped automatic preparation is not a launch failure. This distinction never
removes incoming source or privilege requirements. Legacy owner metadata and
unsettled-effect records do not become manual-update admission policy; concrete
write coordination must make bounded progress under the authoritative
replacement contract rather than preserve an incumbent veto.
The [configuration workbench](configurator/README.md#installation-and-updates)
makes automatic-update policy visible and adjustable, with a clear distinction
between allowing an automatic request and enrolling a recurring controller.

### ordinary-update-completion

A directly requested update owns its recovery and completion: it performs a
fresh installation, or joins an equivalent already-running fresh installation
and waits for that installation's verified result. An accepted request,
historical success or completed Manager-only bootstrap is not completion of a
whole-toolset update. Joining preserves the fully resolved target, requested
scope and options; a different target requires its own execution.

Interrupted attempts retain their original outcome and effects evidence. The
normal update entry attempts supported reconciliation and recovery but can
establish fresh incoming execution without cooperation from the broken
installation. Authorization is distinct from native cleanup proof: retain old
uncertainty without replaying it or relabeling it successful. An unavailable
legacy recovery boundary is diagnostic evidence, not a veto on the requested
replacement. Incoming source, integrity and privilege remain required;
concrete write coordination does not confer an incumbent veto. Success follows
[authoritative-runtime-replacement](#authoritative-runtime-replacement), never
queue release alone.

### setup-invocation-authorizes-dependencies
A user or authorized agent directly invoking the trusted harness setup flow
authorizes installation of the dependencies required by its selected
configuration, including the installer and selected plugins. The flow carries
that authority through the real component installers rather than asking for
another approval of each package or resolved lock. Existing source restrictions,
integrity, seasoning, revocation and installation-ownership checks still apply;
dependency authorization does not select additional optional capabilities or
grant unrelated activation and background execution.

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
requests launch, resume and reattach from the selected provider. The
[Copilot Driver](../plugins/agent-remote-driver/README.md) owns direct-Copilot
Session Host and TMux/PSMux wrapping; Manager is its client, not another mux
implementation. Native AHP, SDK, App and third-party providers remain available,
with backend and presentation selected as composable rather than exclusive axes.
The app is also the phased destination for **Terminal Fragment handling**
(which launch targets a machine's terminal app — Windows Terminal, Tabby,
... — carries a profile for, and mirroring that selection into real
terminal-app fragments): a machine-local presentation concern distinct from
the Driver's execution mechanics. **Per-project binstubs are a different, unrelated
artifact and are explicitly out of scope for this relocation** — deploying
and repairing the PATH launcher scripts a registered project resolves to
remains agent-worktrees' own responsibility; only the terminal-app *profile*
selection model and its fragment-mirroring CLI verbs move here. No
lightweight plugin carries or assumes any of these dependencies.

### plugin-updating-and-alignment
Keeps the installed plugin set **current and mutually consistent** — updates
connector content and independently installed runtimes, and checks that capabilities, shared
contracts, and configuration across plugins **align**, offering to reconcile
drift or a half-upgraded set. Runtime installation is independently invoked;
plugin discovery does not construct or reconcile it. Each independently
installed component keeps its declared daemon healthy without requiring Manager.
The configurator presents the whole set and requests work from its owner.
On a host that adopts the integrated
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

### repository-selected-suite-source
An explicitly selected consuming repository can declare the suite source it
intends to use: an immutable commit, symbolic Git selection or source-repository
override. The trusted upstream bootstrap reads available local declarations
before acquiring that suite, without executing the adopter's setup or requiring
an installed application to interpret its intent. Requested and resolved
identities remain distinguishable, with one immutable selection per operation.

An unavailable repository or absent selection can establish the canonical
current-Main foundation first, explicitly reporting the unresolved choice.
Invalid policy and failures of an admitted source remain failures. Later
authorized update converges to the resolved intended source even when it is
older, without relying on monotonically increasing package versions.

The existing runtime, Manager self-source and Copilot content owners consume
the selection only for their explicitly addressed domains. Reading policy,
adopting a repository or opening a checkout alone neither retargets a shared
installation nor rewrites independent source settings. Qualified retained-good
history stays bound to its consuming repository and immutable source, never an
untested moving alias or implicit rollback authority.

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

The [shared local maintenance banner](../plugin-services/disposable-runtimes/README.md#shared-maintenance-banner)
vetoes automatic updates, including Manager self-update and already queued
background work at effect admission. Manager and an external controller consume
that same hold; neither keeps a competing banner or bypasses it by calling an
installer directly. Clearing it does not enroll maintenance or override an
independent disable. Manager itself remains disposable while the retained banner,
configuration and durable work survive, except for any actual critical
machine-management transport closure that must remain independently available.
Complete, forced and purge removal/reinstall exclude that closure rather than
sever the SSH or Gateway path used to manage the host. Agent credential relays
may be removed; they are not automatically protected transport.

### fresh-default-upstream-installation
An ordinary project update reaches the installed global front door and the
Manager's canonical upstream bootstrap without depending on the old runtime
being healthy. The published Main bootstrap is the default; explicit local
source, development, installation-scope and test overrides remain effective
through one attributable selection.
The bootstrap's transport override is distinct from its runtime selections;
choosing a Dev runtime does not implicitly replace the Main orchestrator.

The bootstrap orchestrates installation into the assigned immutable slot.
Its baseline is a fresh installation, not permission to reuse whatever happens
to be present. Positive recognition of a compatible existing footprint enables
incremental construction and zero-downtime replacement. Missing or incompatible
recognition selects a mostly fresh installation within the same requested
scope; it does not veto updating or demand an extra repair flag.

Ordinary updates and repair/force-install publish and start the incoming
generation independently of the old version's existence, health or cooperation.
Construction does not require uninstalling, deleting or unregistering the old
runtime first. Current binstubs and applicable task registrations select the new
generation. The incoming owner starts first and attempts handoff with the outgoing
owner. The predecessor recognizes both that announcement and its independently
observable staleness. Close/transfer or exclude conflicting work admission as
needed before incoming serving readiness; do not make that transfer depend on
installation completion or indefinite old-lease release. Once all requested
incoming readiness and current selections are established, declare completion
without waiting for final predecessor exit or cleanup. Outgoing self-retirement
and self-cleanup continue independently.
An optional single finite asynchronous fallback may retire remaining outgoing
central daemons and obsolete central-service schedules after a longer grace
period, under [authoritative-runtime-replacement](#authoritative-runtime-replacement).
Do not introduce a periodic resident retirement reaper.
Brief overlap is expected, without duplicate exclusive work ownership.
Missing handoff support or failed drain does not veto the new installation or
require another force/repair flag; interrupted work and unconfirmed cleanup
remain explicit.
The replacement owner releases only the outgoing owner's endpoint reservations.
Predecessor cleanup failure never rolls back a healthy new generation;
rollback is reserved for failure of the incoming generation itself to start.

Replacement preserves configuration, durable application data, unrelated
installations and protected session/connectivity dependencies. Readiness,
current routing and confirmed predecessor exit are distinct observed facts.
Incoming routing/admission prevents duplicate exclusive work without making
predecessor exit or old custody evidence an activation prerequisite. Unknown
ownership is not permission to kill. Actual incoming readiness failures remain
explicit partial outcomes, never claims that an unready service is healthy.
Explicit uninstall or independently scoped historical reclamation is a separate
operation, not a prerequisite for updating.

### installation-refusal-boundary
Incumbent inspection may block applying an explicitly requested installation
only for actual I/O failures, such as file locks or insufficient write permissions.
Unknown incumbent layouts,
missing or contradictory metadata, failed recognition and old-version contract
disagreements do not confer a veto. They select fresh installation rather than
permission checks against the incumbent.
Independent source, integrity and installation-authority requirements retain
their scopes; incumbent recognition cannot supply or veto that authority.

Installation and runtime readiness are separate outcomes. Validation and
diagnostics describe what was installed and whether it works; they must not
silently become additional permission to install or claim healthy operation
that was not established. Recognizers govern safe reuse, migration and scoped
cleanup, not installation admission.

### recognizer-manifests
Each installed footprint has a maintained declarative manifest with two
independent responsibilities: identify the disposable artifacts the installer
may reset, and describe the markers and supported version/contract ranges that
permit incremental or zero-downtime updating. An incremental-compatibility
receipt is evidence of ability to reuse, never permission to install.

Changes to installed artifacts carry the corresponding current manifest and
supported footprint transitions, just as a product schema change carries its
migrations. Schema and mandatory semantic changes are intentional; useful
backward compatibility is maintained where practical, and obsolete support can
retire through an explicit upgrade and rollback decision. Source-only changes
do not require another runtime footprint revision or an unlimited snapshot
history. Compatibility does not impose a permanent sequential migration chain
or make an unsupported old footprint veto independent fresh installation.
Validation detects omitted artifacts, unsupported incremental reuse and unsafe
reset boundaries. The inventory includes generated commands, runtime state and
process or registration effects rather than only package files. Durable data
and protected execution/connectivity remain outside disposable reset scope.

### on-demand-historical-cleanup
An explicit on-demand cleanup capability finds and reclaims obsolete installed
footprints across the selected components: lingering outmoded daemons, unneeded
scheduled tasks or service registrations, superseded binstubs and other
disposable artifacts left by replaced versions. Maintained historical detectors
remain available independently of a current installer's incremental recognizers,
so retiring an optimization does not erase the ability to identify older
leftovers. Detection uses attributable ownership and artifact evidence, not
process names, age or a matching path alone.

Inspection produces a reviewable scope and explains uncertain or preserved
items. Explicit apply revalidates current selection, ownership, native process
identity and artifact/registration contents immediately before acting. It never
targets the current owner, unrelated installations, separately owned sessions,
protected connectivity, edited foreign artifacts, configuration or durable data.
Failed or unconfirmed cleanup stays explicit and retryable through its existing
resource owner. This is not a new resident reaper or periodic sweep, and neither
running it nor proving historical cleanup is required for an ordinary update.
The [historical cleanup pattern](../../docs/patterns/historical-installation-cleanup.md)
keeps detector support, current resource authority and observed outcomes separate.

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
normal path without depending on the broken installation to grant permission.
For a directly requested update, a running old installer receives bounded
wait/join or replacement within the addressed scope, not persistent refusal,
indefinite queuing or a separate operator-recovery prerequisite. Coordination
prevents concrete conflicting writes; preserving old serialization is not an
installation requirement.

The execution owner has a standalone installation foundation available before
connector use, including first installation without an existing Python runtime.
Explicit component setup can establish that foundation without requiring
Manager, a Copilot plugin, or an independently administered always-on coordinator.
Foundation construction has one bounded, recoverable installation owner; it is
not an exception that lets plugin loading become runtime provisioning. Once
ready, explicit installation publication follows the same durable request
contract as later updates. Missing, incompatible or interrupted foundation state
is handled by that same installation authority, including fresh establishment
or supported recovery, never by an independent competing installer.

Equivalent same-target requests debounce and share the active operation's
observable result; different-target requests wait or queue rather than race.
An addressed update has at most one waiting successor. A newer accepted update
replaces the older waiting selection, retaining an attributable superseded
outcome instead of running stale queued installer code. This does not cancel
the active update, replay uncertain work, or discard subordinate component
installation. Independent installations retain independent selections.
Automatic requests cannot displace an explicit user's waiting selection or
inherit its authority; a superseded request never reports installation success.
One authoritative desired-version selection supplies normal installation
targets, keeping automatic advancement monotonic instead of allowing competing
callers to cause accidental rollback. Deliberate repair or rollback selection
remains explicit. Pending, running, interrupted, blocked and completed outcomes
remain attributable and observable. Recovery does not mistake a failed prior
execution for success or silently replay it.
Incoming construction and publication coordinate concrete conflicting writes
internally. Missing, stale or concurrent legacy custody does not become an
admission prerequisite; the authoritative replacement contract requires
bounded manual continuation, not a persistent live-writer refusal.

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
than introducing another installation driver. Standalone components retain their
independent installation and recovery paths; adopting this suite-level owner
does not make Manager a mandatory plugin dependency.

### normal-and-repair-installation
Normal background installation limits trigger fan-out and converges on the
authoritative desired version. Same-version requests debounce; a different
version waits or queues behind the active installation. Fresh installation is
the default convergence path; recognizers enable incremental/ZDD optimization.
Absent reuse evidence, stale control records or an incompatible old runtime do
not require a separate force/repair request merely to complete the selected
update. The same execution owner preserves exclusive installation custody; competing
callers never become concurrent writers. That serialization does not require
outgoing service-runtime teardown before incoming startup.
Blocking acquisition, I/O, permission or execution failures leave explicit
failed-candidate state without activating a failed version. Compatible handoff
is preferred, not a veto on bounded cold replacement.

Explicit force/repair installation gives any intact authorized installer a
bounded way to request disruptive recovery or an explicit replacement target.
It is not the switch that enables the normal fresh fallback. Agents request
operator approval before a concrete disruptive host operation. Once replacement
is authorized, ordinary and repair installation use the same lifecycle:
prefer start-first/self-retirement, falling back to bounded identity-checked
cold takeover when the old owner cannot cooperate or settle. No extra repair
mode is needed solely because graceful cutover is unsupported. An old runtime
cannot veto construction or activation merely by lacking the new handoff contract.
An active old installer is handled by bounded internal wait/join or replacement
under the explicit update's authority, not a second operator-recovery ceremony
or indefinite refusal. Prevent concrete conflicting writes and repeated
installer kill loops without making the old serializer a prerequisite.

An explicit operator order to repair, clean-install or force-reinstall is
sufficient authorization for that recovery mode. Historical cleanup proof,
an audited pre-launch failure or cooperation from the broken installer is not
an additional prerequisite. Audited pre-launch recovery remains available as
a separate path, not a mandatory ceremony before repair. Status distinguishes
operator-authorized recovery from native-confirmed settlement and preserves
the prior failure without turning it back into installation authority.

A successful installation has all requested incoming readiness and current
selections. Old generations may still be finishing independent retirement;
their confirmed exit/cleanup is not that success gate. Current launch, routing
and maintenance selections prevent stale reactivation. Eventual retirement
leaves only the incoming central generation serving, without targeting protected
session processes. Inert rollback artifacts need not be physically erased.
The repair preserves application data and unrelated installations;
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
best-effort pass as success. Costly preparation precedes short publication and
local integration; installation application obeys the
[incumbent I/O-only refusal boundary](#installation-refusal-boundary). Runtime health,
compatibility and convergence remain separately observed outcomes, not
additional installer admission gates. Verified progress survives retry;
transient I/O failures receive safe retry and mitigation. Diagnostics stay
explicit and durable without manufacturing readiness. Existing lifecycle
authorities, package governance and rollback retain their own scopes.

### reliable-working-footprint
Install, update and repair retain responsibility for reaching a verified usable
footprint. Slow delivery is an experience problem, not a reason to fail work
that continues making progress. A partially prepared environment, selected but
inoperable generation or reported success without usable commands and services
does not satisfy installation.

Genuine failure, detected hangs, interruption and cancellation preserve or
restore an operable footprint rather than stranding the device. Preparation
does not destroy the healthy incumbent before a replacement is ready; recovery
continues through the existing installation authority. Keeping a healthy prior
generation preserves operability but does not misreport the requested update
as complete. Source, integrity and ownership requirements remain intact.

### progress-renewed-hang-protection
Installation supervision uses renewable inactivity protection, not a fixed
total-runtime ceiling that terminates progressing work. Lower-level stdout or
stderr activity is an accepted indication of progress. Each layer reports or
relays progress so useful work remains visible through nested installation
flows, without corrupting machine-readable results.

A layer's quiet allowance outlasts the expected duration of its non-reporting
atomic work. Genuine no-progress hangs remain detectable and recoverable;
supervisors do not manufacture keepalives that conceal a stalled worker.
Stopping hung work preserves native cleanup, cancellation and working-footprint
recovery rather than abandoning installation state.

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
resolves independently installed tools, while each runtime's own installation
and supervision owner remains sufficient without Manager. Missing backends
remain explicit rather than being installed by plugin loading
(see [plugin-services](../plugin-services/README.md)). The control-plane
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

Out-of-band automatic self-maintenance carries the explicit automatic-update
classification and honors its hold. Manual setup/update is not vetoed solely
by that hold, but still respects actual installation authority, live native
ownership and unsettled effects.

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
  running the fleet. Plugins provide bounded in-session connection/knowledge
  surfaces, and independently installed runtimes supervise their own daemons
  with this app absent;
  the app must never become a prerequisite for a plugin (or an agent using its
  tools) to work. (The launcher-half of the worktree runtime relocating *into*
  this app must not smuggle in such a dependency.)
- **It does not duplicate per-component installation or supervision owners.** It
  coordinates the real application install flows and their prerequisites, and
  can update the selected set and true-up compatibility. It does not reimplement
  runtime logic or turn plugin loading into installation; independent component
  setup remains available. The relationship carries **no
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
