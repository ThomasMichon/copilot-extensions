# Source-Managed Runtime Installs

- **Slug:** `source-managed-runtime-installs`
- **Repo:** copilot-extensions
- **Branch(es):** independent, serial per-slice PRs against `dev`
- **Created:** 2026-10-16
- **Status:** Active
- **Vision:** [Plugin Service Model](../../../visions/plugin-services/README.md#managed-runtime-deployment), [Runtime Assembly](../../../visions/plugin-services/runtime-assembly/README.md#selected-development-source-assembly), [Installer](../../../visions/installer/README.md#independent-runtime-source-selection), [Clean-room Validation](../../../visions/clean-room-validation/README.md#independent-source-installation)
- **Umbrella issue:** #7028
- **Sub-issues:** #3905 (existing source-materialization gap), #7029 (source selection and immutable generations), #7030 (deployment boundary and adapters), #7758 (durable installation execution driver)
- **Future objective:** #7031 (exclusive local plugin-content sourcing; outside this runtime-only campaign)

## Goal

Fleet Manager is an independently selected application, not a dependency of
`agent-worktrees`. The bound knowledge repository opts a target machine into
Fleet Manager by its assigned role (for example, controller). Installing
Worktrees, requesting a Python extra, or running on the planning machine must
not select Fleet for the target. Selection alone does not start a service.
Use the bound knowledge repository's Fleet service-role bindings as the
installation opt-in. A binding selects its target machine, not its provider's
host. The planner and its read-only explanation must agree on the target,
source configuration and matching roles. Invalid or unreadable required
policy is unresolved, not evidence that the app is disabled.
Worktrees consumes an available OpenSSH profile as `ssh <machine>`. It does
not choose an SSH driver, configure a route, or install Fleet to connect.
An SSH profile may use `agent-ssh` as its `ProxyCommand`; direct profiles
work without it. Profile and driver setup remain outside Worktrees.

Recover Bridge's potential agent catalog from independent registered and provider
sources, distinguishing discovery from verified reachability. Preserve surviving
mux sessions and session hosts across version changes: reattach only to the
verified original; never create a replacement as a side effect of recovery.

Complete the source-managed runtime installation and wheel-first acceptance
for an addressed inventory of installed services and tools, including Manager,
through small, independently validated contributions. Select immutable,
promotion-produced application footprints from Main, pinned Dev or an explicitly
frozen WIP-on-Dev snapshot while retaining the existing
global installation, configuration, databases, sessions and source ownership.
Keep Copilot content delivery independent and establish one consented, bounded
source-maintenance flow. Source merges do not qualify native lifecycle, release
or installed adoption.

The [Manager-owned app-footprint amendment](app-footprint-transition.md) is the
current installation model: move generic install/update management out of
Worktrees and into Manager; promotion produces wholly-copyable app directories,
including vendored in-process code and local versions for local promotion.
Manager orders separately installed IPC dependencies, whose implementation is
never imported by consumers. Slot setup stages without selecting; full setup
establishes the version-neutral app/global commands and actual readiness.
Complete Worktree Manager's install and update using only this new path before
broader app conversion. The new flow uses the common application hierarchy,
allowing legacy and new footprints to coexist until explicit global-command
activation. Durable state and configuration remain outside version slots;
exclusively version-owned, disposable state is confined to a slot-local `state/`
directory and is never needed across updates. The eventual common repository
namespace is standing direction, not a bulk live migration in this slice.
The [verbatim direction](inception-transcript.md#manager-owned-app-footprints-and-local-promotion)
is retained; #12905 tracks delivery without reassigning existing owners.
The reference scope includes Manager-owned orchestration, promotion-produced
Main/pinned-Dev/frozen-WIP footprints, complete Manager install/update
qualification and reviewed, CI-passing, merged delivery. Capture-only work is a
completed milestone, not that implementation's completion gate. Host/fleet
rollout, credentials/access changes and unrelated app conversion are excluded.

The bootstrap owns the **whole update** through a finite orchestration tool
delivered with Manager, using only tools from that installation. It reconciles
the control/knowledge repository pair's plugins, identifies every required
application/service and builds the update roster. Worktrees is a roster
consumer, not an orchestration prerequisite or a project-engine fallback.
Per-application setup/construction and service lifecycle retain their canonical
owners; the bootstrapper invokes orchestration rather than duplicating them.

Operator clarification, verbatim:

> So, I expect the bootstrap to run the *whole update*. It should only use tools from the Manager install to reconcile plugins from the harness+knowledge repo pair, identify the set of all required apps/services, and then build the roster of updates. The bootstrapper runs a tool delivered with Manager to handle orchestration only.

> We're basically just piggybacking on manager to obtain some of its vendored tools, to reduce the amount of code that needs to come with the bootstrap

Manager supplies the vendored tooling so bootstrap remains small; a running
Manager daemon is not required for this finite orchestration entry.

The bootstrap retains target source/ref resolution from registered projects and
their configuration or the machine-side override, reusing the existing owners
and selection rules before obtaining Manager-carried tooling. It does not
silently replace that selection with Main or depend on an operational old
application runtime to discover it.

Operator clarification, verbatim:

> Though the bootstrap will have to resolve the target copilot-extensions checkout ref from the registered projects and their config, or the machine-side override.

> Because we need to determine what version of Manager to install first

That selection determines the incoming Manager version itself: resolve the
target first, install the selected Manager, then invoke its delivered finite
orchestration tool. Installing latest/Main Manager before resolving the target
does not satisfy this ordering.

For the [source-pinned staging lane](app-footprint-transition.md#pinned-checkout-staging),
the consuming repository selects a Git ref; one local checkout at its resolved
commit is the sole first-party source for Manager, apps, services and plugin
payloads until an explicit advance. Stage Manager first, then extend the same
contract to all apps extracted from plugins. Rehearse successive Main
promotions side by side using the upstream published wheels. A stage is not
an activation: no daemon, live task/status store, command redirect or marketplace
change. Runtime-state migration is later work.

Bootstrap must accept an explicit build through the environment on both fresh
install and update, alongside environment overrides for the source repository
or a caller-selected local source folder. These select installation inputs,
not merely the transport used to fetch the bootstrap script.

A previously installed commit can be selected again. Validate and re-stage
its retained builds without disturbing the current installation; full setup
then uses the normal activation path to make the requested builds current.

Fresh installation and update share the same incoming bootstrap, Manager-first
application construction and repository-derived required closure. The
[installation convergence map](installation-convergence.md) connects existing
efforts and owners to that outcome. Complete the required set's staging before
activation; existing generations affect compatibility/state transfer, not a
separate legacy construction route. This alignment changes no completed credit,
source custody, operational hold or deployment authority.

The [managed-Python amendment](managed-python.md) adds per-project interpreter
declarations and `uv`-owned acquisition/environment construction, including the
pre-Python bootstrap foundation. All application launchers use `uv` from PATH;
none assumes an installed Python location or resolves Python from PATH.
This supersedes that earlier setup assumption, not compiler-free execution,
process safety or package admission. #12775 owns the bounded architecture/plan
revision; existing implementation and deployment owners retain the open work.

The [repository-suite association adapter](repository-suite-association.md)
defines the separate consuming-repository/configuration boundary before shared
caller changes. It reuses Manager's existing configuration owner and the
declaration reader, without absorbing ordinary update, construction, independent
source policies or content delivery. The explicit configuration provider merged
in #13012; declaration observation is its next separate adapter slice. Neither
source delivery is installed association or convergence evidence.

The [adopter bootstrap configuration slice](bootstrap-toolchain-configuration.md)
puts early toolchain/feed configuration under the standard finite installer.
An adopter supplies data, not a prerequisite initialization script. Apply the
selected configuration before the first applicable Python/pip/uv or Node/npm
consumer while preserving independent source, credential and application owners.
It extends the prerequisite boundary under #13409; it does not replace #13119's
native source-profile feed-selection work or #12905's application conversion.

The [current lifetime clarification](update-service-lifetimes.md) is part of
this Goal: ordinary updates are foreground-owned and cancellable, while accepted
service setup/start and its retained process/runtime tree have independent
ownership. Preserve the complete installation goal and all other acceptance
rows; a documentation merge does not qualify this new behavior.

The [legacy first-use receiving proposal](legacy-first-use-foreground.md)
maps fixed `provision -Wait` to the ordinary foreground contract. Its finite
foundation construction and provisioning follow the caller, with honest
interruption and recovery rather than manufactured completion when an ordinary
receipt is missing. Explicit background execution and independently accepted
service setup/start retain their separate contracts. Reviewed receiving-owner
intent precedes producer and test migration; existing source, custody, native
qualification and deployment holds remain unchanged. The operator selection
is retained [verbatim](inception-transcript.md#legacy-first-use-foreground-mapping).

The [latest construction and cutoff direction](fast-construction-and-outgoing-cutoff.md)
requires the existing quick-copy, pure UV/wheel-backed environment and simple
per-app setup path without extra fingerprint rejection gates during installation.
Use kill-by-default service replacement: one bounded reliable handoff attempt,
then force-kill the outgoing service and its daemons; without reliable handoff,
kill the outgoing version first. Locks, handoff/schema failures and drain delays
must not block incoming startup. Existing owners retain the per-app audit,
ordinary caller, execution and deployment boundaries.

**Finite-scope amendment: reviewed in #11708.** The consolidated
Plan and global Validation Plan below replace duplicated acceptance inventories,
not the operator's original request or later refinements. The
[scope and evidence dispositions](finite-scope.md) retain the prior public
checklists, delivered credit, independent objectives and reasons for consolidation.
No item is newly passed, no tracker is closed, no custody changes and no native
or installed operation is authorized by this proposal. The effort remains Active.

## Completion charter

**Kind:** completion.

**Root goal:** the complete outcome in [Goal](#goal), including its construction,
managed-Python, lifetime, cutoff and finite-scope amendments. The addressed
installation reaches its required incoming ready/current state while preserving
configuration, data, sessions and source custody; completing a fixture or PR is
not this end state.

**Root source:** the existing reviewed Goal and [Request](#request). Original
operator wording remains verbatim in Request and its linked refinement records;
this charter does not replace, paraphrase or expand those instructions.

**Acceptance:** resolve the direct [Plan](#plan) and global
[Validation Plan](#validation-plan) against attributable source, native,
containing-release and authorized installed evidence. Preserve independently
owned adopter conditions and every current approval, hold and effect boundary.
Do not recursively revive retired historical checklists.

**Finalization criterion:** mark the effort Done only after every direct Plan
and global Validation item is resolved or explicitly transferred to a named
tracked objective. Record the actual accepted outcomes, settle owned obligations
and use the normal worktree finalization flow; a merged child is insufficient.

**Now:** the selected legacy first-use foreground receiving contract cleared
review in #13605. Its finite receiving writer is reconciled through the existing
execution coordination. Expand receiving support before caller/test migration,
including honest interruption/recovery across admission and start. The existing
legacy-consumer qualification and Dispatch contribution retain their own
source/native gates, original bounds and current holds; neither a non-durable
launch acknowledgement nor a missing ordinary receipt establishes completion.

**Return:** after this bounded receiving/caller migration, select the next unmet
criterion from the complete Plan and global Validation Plan, retaining the
addressed-suite, staging-barrier, managed-Python, lifetime and authorized-adoption
obligations.

## Guiding Intent

The [foreground-update/service-startup amendment](update-service-lifetimes.md)
adds the current cancellation and lifetime requirements to this complete
effort, without narrowing its existing Goal. Ordinary updates remain
caller-controlled; accepted service setup/start, daemon trees and required
retention have independent ownership. The associated-owner matrix and L1-L6
retain the cross-effort delivery/validation obligations and all existing holds.

The governing completion target is
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement):
explicit/manual update is independent of old recovery/ZDD, custody/provenance
and stale installer fences; success is all requested incoming versions ready
and all current selections updated. The
[latest cutoff amendment](fast-construction-and-outgoing-cutoff.md) replaces the
older preferred-start-first/long-grace policy with kill by default and at most
one bounded reliable handoff attempt. Obsolete artifact/registration cleanup
remains independent. Preserve sessions/keepers and user state;
recheck then-current generation and role at cleanup. This supersedes conflicting
legacy acceptance language below and in the linked transition/execution/app
plans, not historical evidence or existing source ownership. Bounded manual
continuation applies within the addressed installation; foreign scope remains
protected. No installed adoption is claimed by this plan amendment.

**Contributor entry:** the [fast-install and cutover guide](../../../docs/patterns/fast-application-install-and-cutover.md)
defines each app's required boundaries and qualification. The
[ideal sequence, per-app status and contribution handoff](wheel-first-contributors.md)
separate released support from retained/unmerged work and let independent app
contributors coordinate through this same effort and #7028. Manager/Worktrees
are the released first pair; remaining main services come next, with supported
old/new coexistence. Index/Logger do not gate those independently releasable slices.

**Ideal flow:** trusted source/promotion inputs -> Manager first -> repo-derived
wanted apps -> bounded parallel staging -> app/optional whole-toolset readiness
-> simple app setup and bounded outgoing cutoff -> selected incoming startup
-> verified serving result -> independent obsolete-artifact cleanup
and retained last-good recovery. Background preparation and cheap subsequent
launches complete the target; they are not all implemented by the first pair.

Backing checkouts remain source inputs, but every source-install footprint is
prepared by a promotion flow producing a main-equivalent snapshot and matching
prebuilt wheels. Consume the existing cached-source-wheel-assembly effort's
UV-native, pyproject-strict factory rather than extending a competing raw-source
installer. Every app separates candidate staging/readiness from active
publication and launch; the current cutoff amendment governs incumbent termination.
The [current refinement](inception-transcript.md#promotion-mediated-backing-checkouts-and-app-staging)
governs the wheel-first transition, not a claim that the historical source lane
or installed fleet has already migrated.

Separate Copilot plugin delivery from service-runtime deployment without
disabling ordinary hook behavior. An opted-in installation can select its backing
source independently, consume its promoted snapshot and wheels, retain a stable release or exact pin, and
iterate through immutable generations without waiting for marketplace payload
updates or rewriting a live environment.

Prove the source install on a disposable container before attempting a native
Windows runtime-only pilot and completing the adopter's installed service/tool
transition. Keep the dedicated source healthy and current through the existing
maintenance owner and canonical install flow. The [wheel-first extension](wheel-first-transition.md) proposes independently
maintained runtime installation as the ordinary suite path, with plugins as
connectors rather than installation sources. Its initial reviewed proposal is an intent
checkpoint merged before implementation, not a claim that the
earlier optional-source deployment has already migrated or that native
service-supervision ownership changes.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Implementation coordinator | Owns the plan, integration, public PRs and completion gate | Managed source worktree |
| Manager maintenance implementer | Owns the bounded background-maintenance enrollment, lifecycle and ownership-handover slice | Managed source worktree; public coordination on #7028 |
| Installation execution implementer | Owns durable suite-update requests and exclusive transaction execution, separately from recurring enrollment | Managed source worktree; public coordination on #7758 |
| Linux clean-room runner | Reproduces and verifies actual source installation and activation | Existing Docker clean-room rig; WSL is a supported host |
| Windows validation runner | Verifies native installer, resolver and non-deployment hook behavior | Isolated Windows fixtures, then an explicitly authorized runtime-only pilot |
| Independent reviewer | Reviews each coherent proposal or implementation change | Repository PR review |
| Wheel-first transition coordinator | Owns the reviewed proposal, prioritized independently releasable app slices and whole-flow qualification | #7028 and #9688; app assignments/evidence in the contributor matrix; existing custodians and separate deployment ownership remain |
| Fresh recovery dispatcher implementer | Owns the bounded fresh-bootstrap/selected-source composition and disposable entry qualification | #7028; source handle `greased-latch`; existing application, footprint and execution owners retain their scopes |
| Native application consumer implementer | Owns the bounded standard-environment discovery, command/readiness and cache-adapter slice, not the remaining application portfolio | #7030; source handle `weathered-diode`; [bounded plan](native-application-consumers.md) |
| Bootstrap configuration implementer | Owns early declarative adopter configuration and its standard bootstrap/setup consumers | #13409; [bounded plan](bootstrap-toolchain-configuration.md); existing prerequisite, feed, source-profile and app-footprint owners retained |
| Protected installation implementer | Owns the bounded protected native bootstrap/admission source contract through existing installation/runtime libraries | #13504; [bounded plan](protected-native-installation.md); Manager, provider/profile and live installation owners retained |

## Coordination

The [Manager-owned footprint direction](app-footprint-transition.md) and #12905
amend the target before app refactoring. The capture merged in #12908; the
subsequent bounded implementation assignment selects Worktree Manager itself
as the reference through complete install/update and merged delivery. Preserve
promotion/vendoring (#3654), execution (#7758), component and native owners,
existing source reservations and current holds; reconcile actual source
overlaps before edits. Vault remains illustrative. Historical bounded app
releases do not establish this reference under the new contract. No installed
resource custody or rollout authority transfers with the assignment.

The [protected native installation handoff](protected-native-installation.md)
adds a bounded #13504 source/admission slice through the existing installer
and runtime libraries. It preserves #12905's complete Manager reference and
#12900's provider/profile persistence work. Its incoming trust boundary is not
an incumbent-provenance veto, a competing installer or live elevation authority;
review the proposal before implementation.

The [reviewed scope amendment](finite-scope.md) consolidates the parent completion
gate. Historical #2822/#4010/#4015/#4058/#5353 intake and the
[retained keeper/selector record](retained-source-selector.md) remain public
provenance and requirement sources, not a second recursive checklist. Their
necessary installation outcomes map to the direct Plan and global Validation
below; unrelated investigation or broader capability acceptance remains with its
existing objective. Normal review must accept this disposition before a gate is
retired. This proposal closes no tracker and supplies no supersession receipt.

Preserve the same implementation, source/slot, artifact, application, adopter,
native, deployment, maintenance and #7758 execution custodians and all actual
source/resource reservations. In particular retain the maintenance WIP, the
#8545/#356 acquisition/setup seam, #5472/#7029 source and generation ownership,
Bridge's existing lifecycle/routing owners, and #2822's candidate-health and
verified retained-good protections. Closed trackers are historical provenance,
not live claim tokens. No new implementer, blanket acknowledgment, claim expiry,
incident retry, custody transfer or release of resources is created.

Consume #9290's promotion/UV-native factory, #9453's profile inputs, #8949's
retained-read support and #5851's source isolation through their existing owners.
Keep #11121 and its source history intact until combined replacement coverage
is verified; its ten-review-round cap is unchanged. An individual replacement
merge is not blanket supersession, release or installed acceptance.

- **Topology:** serial, coherent PRs against `dev`; this plan clears review before implementation.
- **Host (owns PRs):** Implementation coordinator.
- **Delegates:** bounded evidence, implementation or validation scopes only; no shared mutable git ownership.
- **Handoff:** the next owner reads this README and the current slice evidence before continuing. A merged PR resolves only its actual Plan and Validation Plan items.
- **Existing owners:** preserve #3654 (vendor pointers), #5472 (immutability), #4876 (artifact admission), #5851 (source isolation), and #6812/#6820 (update outcomes/sequencing). Reuse their mechanisms; do not fork their campaigns.

Independent, non-overlapping app slices may proceed concurrently, including
contributors from other operators. The [contributor handoff](wheel-first-contributors.md#contributing-from-another-agent-or-operator)
defines discovery/claim, evidence and publication steps. Existing ownership
and review gates remain; this is not a second effort or a global merge queue.

**Qualification decision (2026-11-06):** where should the remaining MCP native
case run when the shared host has a live candidate whose ownership is unknown?
The default is the same existing case on an admitted isolated Windows lane at
the final integration PR tree, retaining its resource envelope and the #9716
refusal. Verify lane support first; otherwise record CI capacity as a blocker.
Do not add another fixture family or repeat the unchanged local case. The
single integration PR, review and release follow the approved existing-case
slice; production cutover remains separately approved.

## Context

Manager has independent runtime-source policy and owned acquisition diagnostics,
separate from self-update configuration, plus a shipped trusted canonical toolkit
that prepares plugin and standalone Manager payloads. The normal source-backed
suite caller now qualifies the exact executing engine, acquires one source
commit/tree and prepares its runtime portfolio before either update stage.
Fresh legacy suite repair now retains that same independent service selection
through native engine recovery and subsequent suite preparation. Targeted and
non-repair bootstrap service-source paths still refuse rather than silently
deploying marketplace code. Plugin bootstrap paths still need complete
publication-boundary qualification.
The collected updater now preserves Containers' existing dependency profile
through installed-owner target planning, native construction and selected
source-qualified currentness, separately from the original source pin. Its
native same-root witnesses do not qualify the complete eligible portfolio or an
adopter's production data/serving transition.
Worktrees now retains its original global command owner through collected
prepared-source updater execution, native publication and installed source-unavailable
restoration. Those bounded providers do not qualify an adopter's actual
data/service cutover. The engine now
advertises and accepts a complete prepared-source suite packet through its
existing inventory and durable update process boundary; Manager's normal caller
uses that capability without coupling its independent self-source or replacing
the existing driver. Immutable Manager recovery and actual adoption remain open.

Promotion composes versions and expands development vendor pointers into a
self-contained `main` tree. Raw-dev non-editable installs have an existing
tracked gap (#3905). Canonical runtime helpers already provide immutable
completion, activation, retention and claim-owned editable development
generations (legacy interactive behavior). The
[Immutable Dev Slot effort](../immutable-dev-slot/README.md) replaces that
editable target with frozen, non-editable source-hashed generations under
#5472. This campaign remains the canonical deploy-from-Dev home and extends
the same building blocks, not a second installer or mutable environment.

The [source-owned release vision](../../../visions/release-pipeline/README.md)
keeps the verified development commit as the release identity. Generated main
commits, materialized payload digests, package versions, profiles and dependency
locks describe different facts; no new competing artifact authority is needed.

## Request

**Manager-owned footprints (2026-11-16), settled summary:** move generic install
management out of Worktrees into Manager; promotion produces wholly-copyable app
directories; local pinned-Dev/WIP promotion handles vendoring and local version
assignment before orchestration; shared runtime dependencies are separately
installed and invoked through neutral binstubs/IPC, never imported. Stage
dependencies before consumer full setup and complete one exclusively new-flow
install/update exemplar before broader refactoring. The
[successive operator turns are preserved verbatim](inception-transcript.md#manager-owned-app-footprints-and-local-promotion);
the [receiving plan](app-footprint-transition.md) separates recommendations from
requirements. #12905 remains the implementation tracker after this capture.

**Per-project Python (2026-11-16):** the
[verbatim request and implementation plan](managed-python.md) require `uv` to
manage each project's declared Python version, independent of system/user PATH.
This is a plan amendment, not an installed migration or a takeover of existing
implementation/native owners.

**Incremental delivery, verbatim:**

> Split now; preserve #11121 and prepare the smallest first draft slice

The existing effort remains the canonical goal. Keep #11121 and its source
history intact while preparing replacements; do not close or supersede it
until the combined replacement diff is verified. Preparing a draft does not
authorize another review request, merge, native attempt or installed rollout.

**Clean-break wheel/uv migration (2026-11-03), settled summary:** the
first-party-wheel/uv flow need not remain backward-compatible with the legacy
installation system. A public README-linked force-fresh install script lets
operators recover from arbitrary old versions without a working legacy updater.
Compatibility remains required within the new system afterward. The
[verbatim direction](inception-transcript.md#clean-break-wheeluv-migration)
and [migration acceptance](wheel-first-transition.md#legacy-cutover-and-future-compatibility)
define the boundary; this is not authority to execute a host wipe or discard
configuration, durable state, sessions or protected connectivity.

**Authoritative replacement and completion (2026-11-03), verbatim:**

> install is considered a success once all the *new* versions are up and running, and all the current-version pointers are updated to the incoming version. Outgoing versions are expected to self-retire and clean up after themselves when they detect they are not current; we can kick one async task from the install flow to force-kill all remaining outgoing daemons and schedules after a longer grace period than we expect cutover to take. Note that we don't kill session-keeping processes like session-host/etc, just the central daemons

The [canonical installer contract](../../../visions/installer/README.md#authoritative-runtime-replacement)
captures the settled manual-update, state-preservation and mutation-time
current-generation/central-role boundaries. This plan amendment does not
authorize installed rollout or claim that current installers already conform.

**Contributor-ready fast-install/cutover documentation (2026-11-02), summary:**
link the exact per-app support guide, record the ideal end-to-end sequence even
where unimplemented, and maintain each app's progress so other agents/operators
can contribute bounded updates. The [verbatim capture and app matrix](wheel-first-contributors.md)
also retain the core-service priority and independently releasable old/new
coexistence refinement. This changes documentation and coordination, not rollout
authority or the parent completion gate.

**Manager-controlled automatic updates (2026-11-02), operator refinement:**
put the existing automatic-update control in Manager's Configuration /
Installation & Updates. Automatic invocations use `<repo> update --auto`;
the setting may inhibit those requests, but not explicit user or agent
`<repo> update` calls, which retain normal re-entrancy protection.
The [verbatim request and bounded follow-up](automatic-update-control.md)
preserve the distinction from general maintenance vetoes, recurring-worker
enrollment and separately authorized rollout.

**Git-addressed local promotion (2026-11-01), settled summary:** when local
configuration selects Dev installation, `<repo> update` uses local promotion
and reuses complete outputs for the unchanged selected Git hash. Locally
sourced installs do not increment package versions; official Main releases
remain the version authority. Different source hashes may share package
versions without sharing installed code. The
[verbatim agreement](inception-transcript.md#git-addressed-local-promotion-without-version-increments)
refines the wheel-first proposal, not the current installer implementation.

**Promotion-mediated source inputs and app staging (2026-10-31), settled summary:**
stay out of the existing cached-source-wheel-assembly effort's UV-native,
wheel-first and pyproject-strict implementation. Preserve install-from-any-backing-
checkout as a source choice, but require promotion to produce every source-install
footprint as a main-equivalent snapshot with matching prebuilt wheels.
Every app must split staging/install preparation from activation.
`install --stage` and normal `install` are proposed command spellings: staging
loads and qualifies the candidate venv without active publication; normal
installation updates binstubs and version redirects, launches the now-current
version, and lets the outgoing version sunset after that startup.
The [verbatim refinement](inception-transcript.md#promotion-mediated-backing-checkouts-and-app-staging)
is authoritative. Existing owners, data, approvals and provider-version gates
remain; no alternate builder, cache or project-declaration model is introduced.

**Intentional footprint evolution (2026-10-31), operator refinement:**
make schema changes deliberately, retain backward compatibility when practical,
and permit obsolete support to retire rather than carrying an unlimited chain.
The [verbatim refinement](inception-transcript.md#intentional-footprint-schema-evolution)
and [bounded proposal](footprint-contract-evolution.md) retain the original
disposable-inventory and incremental-recognition goals. This source rework
does not authorize host cleanup, native execution or rollout.

**2026-10-30 — wheel-first independent suite, settled summary:** promotion
produces complete copyable uv projects and their associated first-party wheels;
the upstream installer establishes Manager first, then repo-derived wanted
services. Preparation, application-owned readiness and activation are separated
where needed, with optional atomic local selection and last-good rollback.
Plugin loading stops runtime installation/stamping and keeps bounded guidance.
Whole-system install/change/reinstall/cutover/recovery is proven in Windows
containers before an addressed manual host deployment. The automatic-update
hold is not cleared by this work.

The [verbatim request and subsequent scope summary](wheel-first-inception.md),
[reviewable transition](wheel-first-transition.md),
[measured performance](wheel-first-performance.md) and
[generic recovery inputs](wheel-first-recovery-cases.md) preserve requested
scope separately from agent-recommended implementation choices. Existing
retained families and their custody remain; this extension does not supersede
another contributor's open PR or silently waive an old admission check.

**Runtime bytecode policy (2026-10-30):** the operator selected `external-cache`
(verbatim response) rather than disabling bytecode writes. **Selected option,
summary:** keep Python bytecode caching, but place it outside immutable runtime
slots in durable runtime state. This does not authorize moving or deleting
existing caches or changing installed systems.

**I/O-only installation refusal boundary (2026-10-30), summary of operator direction:**
only actual I/O errors, such as file locks or insufficient write permissions,
may block applying an explicitly requested installation. Old layout,
recognition, receipt, ownership-metadata and compatibility disagreements are not
extra permission gates. The [verbatim clarification](inception-transcript.md#io-only-installation-refusal-boundary)
is authoritative; the [fresh-default slice](fresh-default-installation.md)
records the proposed installation/readiness outcome separation. Existing live
operation approvals are unchanged.

**Independent repair acceptance (2026-10-29), summary of operator direction:**
install and run the complete V1 suite, then force-install V2 in repair mode
without requiring a prior-install existence check or uninstalling, deleting,
unregistering or touching V1. Applying and starting V2 briefly leaves both
generations running. Replace the binstubs and update task registrations; after
V2 starts, V1 should recognize that it is no longer current and shut down on
its own. The installer should need little or no old-owner handling.
The [verbatim request](inception-transcript.md#independent-v1-to-v2-repair-acceptance)
is authoritative. The operator explicitly confirmed that this lifecycle applies
to **ordinary updates and repair/force-install**. The test uses repair to prove
independence from the incumbent; it does not authorize a live-host operation.

**Immutable development alignment (2026-10-22), operator verbatim:**

> So, mutable dev slot should probably be superseded by "immutable dev slot", and the "deploy from dev branch" effort. Basically, always deploy using an immutable venv, tagged with the hash of the source. Just set the new dev slot as current and handle cutover normally by giving the incoming version a start command and letting the old version shut off. We should endeavor to support baton-passing both ways; a version knows it's "old" simply because it's "not current", not via any ordering relationship between the active and owner versions.

> Great. Update relevant visions and efforts to align; close out and abandon superseded efforts and off-course work.

**Accepted alignment:** retain this effort's clean merged Dev source, Manager,
maintenance, driver and installed-adoption owners/gates. All deployments use
immutable venvs identified by complete source and admitted build inputs.
The incoming owner starts independently before the outgoing owner retires;
full identity inequality, not version ordering, identifies the displaced owner.
Reverse baton-passing prefers compatible health-gated drain, but neither a
matched handoff nor installer-driven old-owner shutdown is a prerequisite for
publishing and starting the selected replacement. The 2026-10-29 clarification
above governs ordinary updates and repair. This documentation revision neither
restarts completed Phase 2 work nor claims remaining cutover/adoption proven.

**Historical best-effort follow-on (2026-10-22), summary:** the operator permits outgoing
daemons to quick-terminate their owned scope, and the selected newcomer to
force-terminate an identity-verified predecessor when handoff cannot complete.
Clients tolerate transient network-like outages/stream cut-offs; child daemons
tolerate power-loss-like interruption and recover retriable durable work.
A breaking development contract must not veto replacement. The
[canonical verbatim capture](../immutable-dev-slot/README.md#request) preserves
the complete operator input. Existing source/maintenance/driver and native
acceptance owners remain; no live shutdown or rollout is authorized here.
The later 2026-10-29 clarification supersedes its predecessor-force-termination
allowance for ordinary updates and repair/force-install. All retained families
and linked source slices below use incoming startup followed by outgoing-owner
self-retirement for those operations. Historical intake text cannot restore a
force-termination acceptance route; explicit stop/uninstall or separately
authorized stuck-writer recovery remains a distinct operation.

**Settled request summary:** add a local gate only around service payload
publication and installed runtime redirect updates; retain all other hook
behavior and ordinary Copilot marketplace delivery. Add an independent managed
runtime source supporting main, stable/exact older pins and merged dev.
Materialize dev dependencies and install immutable, hash-qualified,
non-editable generations from clean merged source, never worktrees or dirty
checkouts. Keep adapters thin and version-skew compatible. Begin with a
clean-room container on WSL, then prove a native Windows runtime-only pilot.

The operator confirmed the slug and explicitly selected **runtime-only** for
the Windows pilot. Local plugin-content sourcing is long-term intent tracked
separately in #7031, with exclusive source replacement rather than shadowing.

**Wholesale same-installation cutover clarification (2026-10-24), summary:**
replace Main/release code with immutable merged-Dev install slots in the
**existing global installation**, for every installed service/tool including
Manager. Existing global binstubs and service registrations select the Dev
generation; that generation uses the **same existing runtime configuration,
databases and durable state** that the Main installation used. This is not a
custom-root/cell deployment, a parallel installation or a data-copy migration.
Installation-cell/custom-root capability is separate work, not a prerequisite
for this wholesale transition. The [verbatim clarification](inception-transcript.md#wholesale-same-installation-cutover)
preserves all three operator statements. Exact native change/rollback consent,
candidate health and data-format compatibility remain required.

**Teardown-readiness priority (2026-10-25), summary:** prepare intentional
per-machine tool outages before further installed transition. The existing
installation-independent-sessions owner (#8669) retains wrappers, supported
removal and actual process-survival qualification. The bounded companion #9022
adds normally projected conduct guidance and a retained local notice identifying
the maintenance owner, with native Copilot 30-minute standby polling and no
supporting-service dependency. This does not activate a hold, replace native
survival evidence or authorize host restart/removal.

**Disposable-service alignment (2026-10-25), summary:** noncritical supporting services,
including Manager's replaceable tooling, must be disposable while durable work and independently
hosted Copilot sessions survive. One retained per-machine **YAML** maintenance
banner both vetoes automatic updates/reprovisioning and explains missing
`agent-*` catalog tools to agents. Normally projected conduct guidance prohibits
self-repair during an explained hold and uses one native scheduled prompt every
30 minutes until applicable clearance. This extends the earlier JSON-only
source companion; it is not an already-implemented updater veto. The
[reviewed-contract proposal](maintenance-banner.md) carries binding details,
compatibility and consumer validation under #9040, preserving the existing
maintenance, driver and #8669 survival owners. The
[verbatim request](inception-transcript.md#disposable-services-and-yaml-maintenance-banner)
is the authoritative capture; filename and parser/admission safeguards marked
agent-recommended are proposed realization, not additional operator requests.

**Critical-connectivity correction (2026-10-25), summary:** spare the actual
machine-to-machine SSH host/client/proxy/tunnel/Gateway dependency closure from
supporting-suite uninstall/reinstall, including forced removal over SSH.
Treat that closure like critical Copilot/Mux/session-host substrate and audit
each route instead of assuming that agent-ssh provisioning code serves requests.
Credential relays are explicitly disposable. The [source audit and retention
contract](connectivity-retention.md), tracked by #9048, distinguishes required
transport from provisioning and agent credential brokerage; the
[verbatim capture](inception-transcript.md#critical-connectivity-exemption)
retains both operator messages. No live transport or uninstall action is authorized.

**Execution follow-through summary:** the requested outcome covers all installed
services and tools, including their management tools, rather than ending at a
successful canary. A regular flow must keep the dedicated merged-dev source
healthy and updated and rerun canonical installation at a bounded interval,
using the selected maintenance owner. The
[maintenance slice](maintenance.md) proposes the portable integration and
validation; it must clear review before implementation.

**Maintenance ownership revision (2026-10-20):** automatic suite installation
remains off by default, but an explicitly run trusted consuming-harness setup
may opt in for the user or local installation. Manager owns that background
capability. Windows enrollment uses current-user Autorun for a headless
maintenance process rather than requiring Task Scheduler registration. A
harness's approved setup can enable maintenance after installing Manager.
An external update controller remains an exclusive alternative. Agent Machines
retains general machine/dependency convergence rather than another concurrent
suite-update loop. The operator selected **implement-and-adopt**, following
review of this ownership amendment; source-backed installation prerequisites
and adopter-specific change gates remain intact.

The [inception record](inception-transcript.md) retains the operator's words,
with private host identifiers redacted. Implementation choices below marked
**agent-recommended** are not additional operator requirements.

**First-installation decision (2026-10-22):** the operator selected
`driver-foundation`: "Cover first installation too: establish a standalone
driver foundation before plugin provisioning". Python-free standalone genesis
is not exempted from the execution-owner objective. The foundation must remain
independent of Manager and other plugins. The detailed packaging, bootstrap
admission and adapter sequence below are **agent-recommended implementation
work**, not additional operator-selected mechanisms.

**Historical execution-owner clarification (2026-10-22):** the operator required one
background installation driver, not merely independent per-installer locks.
Ordinary callers submit durable requests; waiting observes the operation and
does not become its installer. Direct execution is reserved for supervised
debugging under the same exclusive authority. The operator directed:
"Do what you need to do to get unblocked that doesn't involve waiting on
someone else's PR". The [execution-driver slice](execution-driver.md) therefore
proceeds against the existing canonical updater, independently of open staging,
watchdog and recurring-maintenance changes, without superseding their owners.

The later [foreground-update/service-startup clarification](update-service-lifetimes.md#request)
supersedes the ordinary-background-worker-only requirement above, not its
single-writer, request-history or delivered source credit. Direct foreground
execution is no longer merely a debugging exception in the intended contract;
this amendment does not claim that migration is already implemented.

**Repair evidence clarification (2026-10-23), summary:** audited, request-bound
pre-launch recovery is allowed, but an explicit operator order to repair,
clean-install or force-reinstall does not require historical cleanup evidence.
Operator authorization is distinct from native-confirmed settlement. Preserve
the actual prior failure, addressed installation scope, one active writer and
unrelated services/data; do not require the broken owner to approve recovery.
The [request record](inception-transcript.md#explicit-repair-authorization-and-legacy-pre-launch-audit)
retains the exact words. This approves source implementation, not a concrete
repair run on a host.

**Fresh-default upstream orchestration (2026-10-28), summary:** the global
project binstub intercepts `<repo> update` and routes it to
`worktree-manager update --project=<repo>`. Unless explicitly overridden by
the supported environment seam, it runs the upstream GitHub Main one-line
bootstrap. That orchestrator reads local Dev, marketplace-cell and test
overrides and constructs the selected installation in its assigned slot.
Its baseline is a fresh installation. Recognizers positively qualify valid
existing footprints for incremental/ZDD reuse; a missing match selects a
mostly fresh install that silences the old version, clears known disposable
runtime state, retires known processes/tasks and replaces non-config globals.
Old receipts establish ability to reuse incrementally, not permission to
update. Config, durable state and previously protected execution/connectivity
remain intact. The [verbatim direction](inception-transcript.md#fresh-default-upstream-installation-orchestration)
supersedes receipt-as-permission assumptions in the earlier normal/repair
design. Realization still uses the existing canonical installation owner,
not a parallel updater, and proceeds through review before implementation.
The [fresh-default slice](fresh-default-installation.md) reconciles the routing,
footprint inventory and execution-driver work without replacing their owners.

**Recognizer-manifest refinement (2026-10-28), summary:** expected runtime
footprints have a maintained manifest serving two distinct roles: enumerate
the disposable artifacts safe to reset, and describe markers plus supported
version/contract ranges that positively qualify incremental/ZDD updating.
Changes to installed artifacts also update that manifest and its footprint
deltas/migrations, analogous to product schema upgrades. The
[verbatim refinement](inception-transcript.md#recognizer-manifests-and-footprint-migrations)
retains the operator's wording. Manifest recognition is not an update-permission
receipt; unknown compatibility selects fresh installation within declared
disposable scope while preserving config and durable data.

## Scope Approvals

The [Manager/Worktrees native consumer slice](manager-worktrees-native-consumer.md#scope-approval)
retains the operator's explicit reviewed, fixture-only Windows qualification
approval. That approval left production rollout, maintenance enrollment and
installation-survival testing paused, with original owners and bounds unchanged.
That is the original fixture approval's limit, not a universal interpretation
of the subsequent [release/install direction](fast-construction-and-outgoing-cutoff.md#intake-and-ownership).
The later direction permits release/installation with existing host-update
ownership. Reconcile the addressed target and unresolved effects through those
owners; it does not reassign the separate per-app audit or implicitly resume
other protected work.

## Plan

- [ ] Decouple Worktrees' `fleet-ssh`, `fleet-review`, development dependency
  and `application-sources` metadata from the Fleet distribution. Preserve
  explicitly configured SSH and review behavior through the provider's
  supported interface, without importing Fleet from another app's environment
  or relaxing review authorization. Select standalone Fleet from the target's
  knowledge-owned role policy, not from Worktrees or an ambient host role.
  Move outbound-only enrollment and reconnect to the explicitly selected
  connectivity provider before removing its current Worktrees entry. Preserve
  the enrolled identity and existing state; provider absence must not cause
  re-enrollment, state loss, or a fallback connection.

**Reviewed consolidated acceptance (#11708).** Existing checked foundations below retain only their
recorded evidence; the open rows are finite work, not new completion claims.
The [disposition map](finite-scope.md) identifies replaced gate families and
preserves the complete pre-amendment record.

### Installed portfolio recovery and reversal

- [x] Recover a recorded Dispatch wake through a separately installed Bridge
  (#13581). With the queue withheld, the original wake receipt survived both
  installed candidate directions on Windows. An ambiguous admission stayed
  unknown until the original journal returned, with no second prompt. This
  qualifies historical admission only, not discovery or a live session route.
- [ ] Qualify [cold discovery and survivor-only reattachment](shadow-observation.md#bridge-discovery-from-zero)
  independently of receipt recovery. Start with an empty Bridge database and
  an original mux session absent from it. Compare inventory and verified routes
  separately; count prohibited effects on success and failure.

### Manager-owned application footprints

The [receiving sequence and validation](app-footprint-transition.md) are the
current direction; clear review of the capture before app refactoring.
Existing checked rows below retain only their historical scope.

- [ ] F1: Move generic install/update orchestration under Manager, independent
  of a working Worktrees installation, using canonical execution/activation
  machinery and each app version's setup entry.
- [ ] F2: Have the existing promotion owner produce whole-copy app footprints
  from Main, pinned Dev and explicit frozen WIP, including embedded-code
  vendoring, local output versions and declared IPC dependency components.
- [ ] F3: Qualify the selected Worktree Manager reference's real
  installation and update using only this new path, including applicable
  actual dependency staging/IPC readiness, new application-root coexistence,
  separated durable/disposable state and neutral app/global commands.
  No legacy-installer fallback or historical partial witness counts.
- [ ] F4: After F3 passes, convert additional apps through bounded existing-owner
  slices; preserve independently required native and authorized installed gates.

### Incremental source-install integration

- [ ] B1: Implement and qualify [early adopter toolchain configuration](bootstrap-toolchain-configuration.md)
  through the standard bootstrap/setup owners, without adopter initialization
  scripts or changed no-configuration defaults; review this contract before code.

- [ ] N1: Review, implement and qualify the bounded
  [protected native installation handoff](protected-native-installation.md)
  through existing installation/runtime owners, preserving ordinary defaults,
  independent incoming trust and separately authorized native/adoption gates.

- [x] S1: Complete the next independent deadline and Git-isolation source
  increments, keeping each bug class, affected canonical copies and focused
  regressions together under the existing owners. Deadline acquisition merged
  in #12195; canonical local composition, Main-version seeding and Git isolation
  merged in #12219. These source increments do not qualify installed adoption.
- [ ] S2: Verify combined replacement coverage against preserved #11121 source
  before superseding its implementation; retain its history and ten-review-round
  limit. A small diff or passing review alone is not replacement completeness.
  The original #11121 instead merged with its history preserved and no
  supersession; [source and bounded native credit](journal/2026/11.13%20canonical-application-integration.md)
  does not close combined whole-inventory or installed acceptance.

### Phase W — Wheel-first independent suite (proposal extension)

- [ ] Reconcile and review the [fast-construction/cutoff amendment](fast-construction-and-outgoing-cutoff.md#receiving-plan-and-validation---agent-recommended), then realize and qualify it through the retained constructor, ordinary-caller, execution and lifecycle owners; consume the separate per-app audit without duplicating it.
- [x] Qualify and release source-bound HTTPS feed/artifact redirects without
  mandatory CDN-origin enumeration, preserving explicit destination restrictions,
  exact file hashes, feed policy, credentials and bounded acquisition custody.
  The shared acquisition slice is coordinated on #7028 with #4876; it does not
  take Bridge/MCP application work or authorize host rollout.
- [x] Review and merge the [upstream intent checkpoint and W0–W4 plan](wheel-first-transition.md#plan-and-acceptance) before implementing the new direction. #9623 lands the intent; implementation and installation/fleet acceptance remain open.
- [x] Capture the [ideal sequence, app matrix and contributor handoff](wheel-first-contributors.md), linked to the shared [application support guide](../../../docs/patterns/fast-application-install-and-cutover.md); documentation does not qualify unfinished apps.
- [x] Release the bounded Manager/Worktrees fast-install slice (#9990, Main promotion #10024), preserving supported legacy paths for the rest. Whole-system bootstrap and rollout remain open.
- [ ] W1: Finish the standalone force-fresh front door and Manager-first
  consumption of complete promotion-produced snapshots, wheels and profiles;
  derive the wanted inventory from the selected repository declarations without
  enabling unselected roles. Document the supported public recovery invocation
  without depending on healthy legacy tooling or introducing another
  constructor/cache/publisher.
  Fresh and update entries use that same path, retaining the bootstrap
  environment override, explicit source/ref and repository context, and
  Manager-owned dependency/downstream-installable discovery.
  The [bounded native consumer qualification](manager-worktrees-native-consumer.md)
  tests this real path without claiming whole-inventory or productive adoption.
- [ ] W2: Integrate stage/readiness and activation through existing app adapters
  for the declared installed inventory. Retain the selected activation strategy;
  qualify coordinated toolset selection only where that strategy is used.
  Stage the complete required set before activation begins, without live
  initialization, live service activation, handoff or data migration. Private
  candidate readiness retains its existing passive contract, with live work
  admission closed and no live publication or shared-data migration. Use the same
  construction for absent and existing installations; activation owns
  compatibility, one bounded reliable handoff where supported and forced
  outgoing-service cutoff under the current amendment. Do not retain repeated
  fingerprint/rejection gates in construction/setup or extend old drain budgets.

### Phase 0 - Review the intent and plan

- [x] Land the vision amendments and this canonical proposal through repository review.
- [x] Confirm issue/slice ownership and preserve the existing dependency campaigns.

### Phase 1 - Reproduce direct-dev installation in the clean room

- [x] Extend the existing Tier-P rig with a credential-free, source-install scenario using a recorded clean merged dev commit.
- [x] Reproduce #3905 through an actual non-editable installer path and record the first classified failure.
- [x] Establish observable source identity, runtime generation, installed command and source-checkout isolation assertions.

### Phase 2 - Assemble immutable merged-source generations

- [x] Reuse canonical source materialization for the complete declared vendor closure without modifying the input checkout.
- [x] Add source-qualified non-editable generation identity while preserving package versions, profiles, dependency policy and completion evidence.
- [x] Make installer, runtime resolver and installed command redirects agree on the exact selected generation on Linux and Windows.
- [x] Prove identical reuse, distinct same-package-version commits, source removal, interrupted build and failed-candidate preservation.

### Phase 3 - Separate plugin-triggered service deployment

- [ ] P3: Complete managed publication admission and thin-adapter/readiness
  integration for the inventory's actual bootstrap and first-use paths. Preserve
  ordinary Copilot content refresh, non-deployment hooks, healthy dispatch and
  standalone defaults; do not turn payload drift into runtime replacement.

The [deployment-policy map](deployment-policy.md) and
[native application consumer evidence](native-application-consumers.md) retain
their bounded delivered credit, not a requirement to repeat every historical
adopter/platform combination.

### Phase 4 - Integrate Manager-owned runtime source selection

- [ ] P4a: Finish independent Manager/service/plugin source policies, preserving
  main tracking, stable/exact pins and opted-in merged Dev as source capabilities.
  Resolve the selected clean merged commit once per run and retain it across
  retries. Do not require an adopter to exercise Main or an older pin unless its
  chosen installation or recovery route uses that selection.
- [ ] P4b: Route the declared installed inventory, including Manager, through the
  canonical prepared-source update and recovery path at its existing global
  roots, command owners, configured roles/profiles and configuration/database
  identities. Preserve Git-addressed unchanged-output reuse and distinct-source
  immutable replacement without local package-version increments; keep current
  footprint contracts/deltas consistent with the outputs actually changed.

### Phase 4a - Manager-owned opt-in background maintenance

- [ ] P4aM: Complete one default-off, explicitly consented Manager maintenance
  owner or its exclusive external-controller alternative, using the existing
  setup seam and installation driver. Keep source health/sync/install bounded,
  use the selected installed Manager after updates, and preserve the declared
  native headless lifetime and current-user Windows Autorun boundary.

### Phase 4a.1 - Manager automatic-update controls

- [x] Review the automatic-control intent and validation plan before implementation (#10056).
- [x] Implement the applicable existing automatic-update control and independent
  maintenance veto in Configuration / Installation & Updates with truthful
  scope, persistence, errors and an actionable ON/OFF interaction.
- [x] Implement canonical automatic software-update routing after Manager startup and before
  worktree creation/resume through `<repo> update --auto`, preserving
  origin through queued execution and retaining the existing single executor.
- [x] Prove that disabling automatic updates blocks only automatic requests;
  explicit user/agent and selected-component updates retain their ordinary
  path and re-entrancy guards without requiring force.
- [x] Qualify UI cancellation/persistence, queued policy changes, both launcher
  adapters and supported mixed-version behavior; update owner docs and land
  the bounded source through normal review and required CI.

These are #10103's bounded source controls, not recurring enrollment or installed
adoption. Other completed source slices remain credited in the
[evidence map](finite-scope.md#retained-delivered-credit).

### Phase 4b - Durable installation execution driver

- [ ] P4bY: Through existing bootstrap, shared-engine and application owners,
  implement the [managed-Python setup sequence](managed-python.md):
  shell-native `uv` foundation, declared managed interpreter before helpers,
  project-specific environments and every app launcher's PATH-`uv` dispatch
  without guessed Python paths, PATH Python or ordinary-launch acquisition.
  Preserve all independent holds.
- [ ] P4bD: Complete the addressed installation's ordinary update, explicit
  repair and independent cold-foundation entry through #7758's one
  transaction owner: ordinary execution is foreground/cancellable, while
  explicitly background execution and independently accepted service startup
  retain their declared lifetimes. Do not require preexisting Python,
  Manager or a plugin runtime for foundation establishment. Preserve durable
  outcomes, execution-mode exclusivity and bounded manual continuation through
  stale/unsupported old state; do not replay uncertain work or require old-runtime
  approval.
  The [legacy first-use foreground receiving contract](legacy-first-use-foreground.md)
  specializes this open row; receiver support precedes caller/test migration.
- [ ] P4bP: Connect the inventory's remaining actual installation publishers to
  that owner, preserving canonical native options, publication admission, healthy
  use and exact source identity. Do not make unrelated cold-cell/custom-root,
  unused-plugin or every-platform recipe adoption a prerequisite.

### Phase 5 - Prove assembled use and native Windows readiness

- [ ] P5a: Complete the requested assembled clean-room and whole-system
  Windows-container qualification for the declared inventory and selected source
  route, reusing supported isolated lanes and existing bounded evidence.
- [ ] P5b: After native prerequisites and separate target approval, complete the
  runtime-only transition for the addressed installed inventory in the same
  global installation and data. Consume applicable existing session,
  connectivity, removal and maintenance-hold prerequisites; retain all
  separately required adopter survival/standby acceptance with its owners.

### Completion and scope control

- [x] C1: Obtain normal review of this proposed scope amendment before retiring
  a prior acceptance gate; preserve original request/refinement capture and
  record dispositions without marking retired criteria passed or closing issues.
  Reviewed in #11708; this is documentary scope-review credit only.
- [ ] C2: Resolve the direct Plan and global Validation against attributable
  source, release and authorized installed evidence. Keep explicit remaining
  adopter holds visible; neither paper consolidation nor a source merge makes
  the effort Done or releases another owner's obligation.

## Validation Plan

- [ ] Build and stage Worktrees without a Fleet distribution or source input.
  Inspect its wheel metadata, including extras. With the same Worktrees build
  and two target-role assignments, only the opted-in target should request the
  separately staged Fleet app. A known nonmatching role excludes Fleet;
  unreadable required policy is unresolved, not a grant. Exercise optional
  SSH and review callers both without their provider and with an explicitly
  configured provider. No live setup, role reassignment or service start.
  Exercise the same Worktrees operation with a direct SSH alias and a
  proxy-backed alias: only the destination changes in Worktrees' invocation.
  An SSH failure must propagate without attempting driver setup or falling
  back to a different route.
  Also exercise an opted-in target with no Worktrees installed or selected:
  the target role alone must request Fleet. Upgrade and reverse selection
  from a previously selected Fleet extra must preserve enrollment identity
  and existing state without implicitly installing or removing Fleet.
  At each transition verify reconnection with the original fixture key when
  the provider is available. With it absent, verify no new key, enrollment,
  state rewrite or fallback connection.

The [legacy first-use foreground controls](legacy-first-use-foreground.md#validation-plan-agent-recommended)
specialize P4bD and L1-L6 with finite caller custody, truthful interruption,
the admission/start kill window, compatible peers and separately accepted
service work. They remain implementation and qualification obligations, not
credit from this proposal or from an ordinary contained diagnostic.

The [protected native handoff controls](protected-native-installation.md#validation-plan)
add incoming trust/custody, pre-write refusal, substitution, finite outcome and
mixed-version cases. Inert source fixtures do not qualify privileged native
protection, deployment or provider adoption.

The [bootstrap configuration cases](bootstrap-toolchain-configuration.md#validation-plan)
add first-consumer ordering, native configuration precedence/preservation and
no-configuration compatibility. They are implementation and real-consumer gates,
not credit from publishing this plan.

The [app-footprint acceptance cases](app-footprint-transition.md#validation-and-completion-agent-recommended)
add Manager independence, Main/local-Dev/WIP footprint parity, local version
assignment, stage isolation, ordered IPC dependencies and one complete new-flow
install/update exemplar. They remain open; documentation is not conversion or
rollout evidence.

The [managed-Python acceptance cases](managed-python.md#validation-and-completion)
extend cold-foundation and addressed-application qualification below. They remain
open: no-Python/old-Python PATH, differing project versions, offline reuse and
missing-version failures, acquisition-free ordinary reads/launch, and native
process/OS-policy evidence are not established by this plan revision.

All rows below remain open until their complete stated scope is evidenced.
Use prior source/native records at their actual revision, venue and effect
bounds; add only missing, directly relevant qualification through already
authorized owners and lanes. Linked historical checklists are evidence and
independent objectives, not recursively imported completion gates.

- [x] V1: Review the finite scope and requirement-to-evidence dispositions against
  the unchanged Guiding Intent and original/later request record. Preserve every
  necessary installation outcome and separately owned adopter condition without
  converting documentary retirement into a technical pass. Review merged in
  #11708; technical rows below remain open.
- [ ] V2: Record a finite addressed inventory of installed commands/services,
  roles/profiles, global roots, configuration/data and relevant protected
  dependencies. No uninstalled optional role, every runtime root or unrelated
  architecture/provider becomes required merely by appearing in an app matrix.
- [ ] V3: Qualify authentic selected source, exact per-run selection,
  promotion-produced complete non-editable inputs, unchanged-input reuse and
  distinct-source identity. Explicit pinned-Dev/frozen-WIP local promotion
  produces a footprint and local versions; it never installs a mutable checkout
  or claims official release. Retain clean merged-source requirements for
  unattended Dev deployment and refuse substituted/unfrozen inputs. Preserve
  independent source-policy capabilities;
  Main/older-pin adopter qualification is required only when the chosen route
  actually uses it. Installed use must not depend on the backing source remaining.
- [ ] V4: On the inventory's actual supported plugin/command paths, show managed
  payload refresh and first use do not publish runtime replacements, while
  non-deployment hooks and healthy commands remain usable. Exercise the
  relevant capability/skew boundary or report its precise readiness outcome.
- [ ] V5: Show stage-only preparation leaves active selection/commands/
  registrations unchanged; activation makes each requested incoming runtime
  ready and all applicable current selections effective. Verify real installed
  command/serving identity, current footprint coverage and representative
  existing configuration/data.
  Qualify coordinated atomic selection only if the selected strategy uses it.
  Inventory complete copied projects/locks/wheels/resources/local commands and
  environments, not just package receipts. Compare fresh/existing entry
  construction and prove a failed required stage prevents activation of the
  addressed set, while no live data initialization or migration runs in staging.
- [ ] V6: Qualify the selected ordinary-update and repair routes with independent
  incoming startup and truthful manual/automatic classification. Old recovery,
  unknown provenance, stale fences and predecessor cleanup cannot veto explicit
  installation. Preserve single-writer/state compatibility; report incoming
  build/authentication/permission/I/O/health failures honestly. Record outgoing
  supersession/self-retirement after incoming startup separately, without making
  predecessor exit the incoming-success witness. If an optional finite retirement
  fallback is adopted, qualify its central-only, native-identity,
  then-current-selection and keeper protections; otherwise it is not required.
- [ ] V7: Retain applicable failed-candidate, exact installed CLI health and
  verified previous-good recovery evidence. Cover the actual route's failure/
  retry and state-compatible recovery choice, with explicit partial/unknown
  outcomes; do not mandate mutation of a completed legacy venv, arbitrary old
  protocol compatibility or an exhaustive historical corruption matrix.
- [ ] V8: Verify the required cold entry and actual transaction owner with
  the [L1-L6 cancellation and service-handoff evidence](update-service-lifetimes.md#validation-and-completion),
  relevant native concurrency evidence, one writer and durable
  attributable results. Reuse qualified foundation components; an accepted
  request, recipe fixture or process spawn alone is not completed installation.
- [ ] V9: Observe one unchanged regular maintenance cycle reusing the selected
  outputs and one advancing regular cycle using the canonical installer.
  Retain applicable existing failure, consent, native headless lifetime and
  single-owner evidence, including continued use of the selected Manager after
  update. No indefinite operation/soak or new full support matrix is required.
- [ ] V10: Retain container-before-host ordering and native Windows evidence for
  the actual selected inventory/path before any approved pilot. Keep source,
  containing Main, fixture/native qualification, installed selection and serving
  results distinct; no synthetic new root/database substitutes for same-global
  adoption, and no new native attempt is authorized by the plan.
- [ ] V11: Consume the existing owners' applicable protected-session,
  connectivity and hold acceptance before claiming the corresponding adopter
  milestone. An adopter's explicit current/fresh-connection and original-session
  survival through supporting-code removal, or required native standby, remains
  owed where declared; this source scope amendment neither withdraws it nor
  claims another owner's acceptance.
- [ ] V12: Verify required source review/CI, actual merge and containing Main
  promotion for each delivered slice, plus combined replacement coverage before
  superseding #11121. Journal actual bounded outcomes and remaining approved
  adoption gates without claiming release/live state from a PR or transferring
  custody through a checklist disposition.
  #11121 has current source review/CI, actual merge and containing Main credit
  (#12353); remaining slices and adoption gates stay open in the latest journal.

## Proposal

The [wheel-first extension](wheel-first-transition.md) cleared its initial
upstream intent review before implementation. That checkpoint is not
application acceptance. Qualified app/integration slices now clear review
independently under the [core-service refinement](wheel-first-contributors.md#request-capture);
the requested installed outcome remains required regardless of PR count.
This proposed amendment consolidates acceptance into the direct Plan and global
Validation Plan above. The linked W0–W4 and older slice checklists retain design
and evidence context rather than importing another complete gate. Native
ownership and the explicit container-before-host requirement are unchanged.

Keep deployment policy, source selection and serving-generation selection
separate. Reuse the canonical materializer, runtime construction/retention and
admission paths. Runtime-source selection does not modify marketplace
definitions, introduce a second supervisor, or claim release promotion.

**Agent-recommended:** keep the Manager's own recovery/update source on a
known-good selection while canarying service runtimes; treat this as an
independent configuration choice rather than forcing the Manager and suite
onto one branch.

## Journal

- 2026-11-18: [Atomic bootstrap-write headroom](journal/2026/11.17%20manager-footprint-consumer.md#merged-atomic-write-prerequisite)
  merged in #13182 after exact-head approval, required Linux unit/coverage CI
  and independent disposable Windows qualification of the unchanged runtime.
  Its source workspace is finalized. F1-F3 source/profile construction and
  cold bootstrap remain open; no release or installed rollout is inferred.
- 2026-11-18: [Manager source-app staging and complete identity/resource closure](journal/2026/11.17%20manager-footprint-consumer.md#merged-source-app-staging)
  merged in #13149 after exact-head approval, required Linux unit/coverage CI
  and all nine external bootstrap/composition jobs. Its source workspace is
  finalized. Full source-profile construction, installed-origin qualification
  and cold bootstrap remain open under F1-F3; no rollout is inferred.
- 2026-11-17: [Promotion-produced Manager source project](journal/2026/11.17%20manager-footprint-consumer.md#promotion-produced-manager-source-project)
  merged in #13121 after exact-head approval, required CI and real Linux
  production-composition qualification. Its source worktree is finalized;
  source-app setup/construction and cold bootstrap remain open under F1-F3.
- 2026-11-17: [Prepared Manager install/update and recovery](journal/2026/11.17%20manager-footprint-consumer.md#merged-prepared-reference-source-and-next-footprint-slice)
  merged in #13069 after exact-head approval, required CI and final-runtime
  installed Windows repair/repeat qualification. Its source worktree is
  finalized; Main source apps, equivalent local outputs and cold bootstrap
  remain the next F1-F3 acceptance, not completed by the wheel-carrier witness.
- 2026-11-17: [Finite Manager-owned application consumer](journal/2026/11.17%20manager-footprint-consumer.md)
  merged in #12930; F1-F3 and the complete Manager reference remain open.
- 2026-11-17: [Frozen Manager footprint producer](journal/2026/11.17%20manager-footprint-consumer.md#prepared-footprint-producer)
  merged in #12965 after exact-head approval and required PR-event CI; the source
  worktree is finalized. Real relocated Windows staging/construction passed;
  release emission, full Manager install/update and F1-F3 remain open.

### 2026-11-16 - Manager reference and clean-break state boundaries

The operator selected Worktree Manager itself for complete new-flow install and
update, including Manager-owned management, promotion-produced footprints and
reviewed/CI-passing merged delivery. The
[subsequent verbatim amendments](inception-transcript.md#manager-reference-and-clean-break-layout)
adopt the common app hierarchy, side-by-side legacy/new footprints until command
cutover, durable state/configuration outside slots, and a narrow slot-local
`state/` exception for exclusively version-owned disposable data. Repository
namespace migration remains long-term. The receiving plan and governing vision
carry these boundaries; #12905 coordinates the assignment. No implementation
acceptance, source-owner transfer or host rollout is claimed by this amendment.

### 2026-11-16 - Copyable app footprints and Manager ownership

Preserved the [successive operator requests verbatim](inception-transcript.md#manager-owned-app-footprints-and-local-promotion)
and proposed the [receiving plan](app-footprint-transition.md), tracked by #12905.
The direction assigns generic installation management to Manager, materialization
and local versions to promotion, and shared runtime dependencies to separately
installed neutral-command/IPC components. It requires one complete new-flow
component before broad conversion. No implementation, native execution,
installed activation or ownership transfer is claimed by this capture.

### 2026-11-16 - Managed-Python architecture and setup direction

Captured the request and all-launcher refinement in
[managed-python.md](managed-python.md), coordinated by #12775. The proposed
vision/invariant and plan amendment replace PATH-Python dependence with
project-declared Python selected/provisioned by PATH `uv`, without assumed
interpreter locations. Existing source and native credit, implementation
owners, holds and rollout boundaries remain unchanged; technical acceptance
stays open.

- [2026-11-15 - Fast construction and bounded service cutoff reviewed](fast-construction-and-outgoing-cutoff.md#journal)
- [2026-11-15 - Manager/Worktrees qualification accepted; selected-slot fixture prerequisite merged](manager-worktrees-native-consumer.md#journal)
- [2026-11-13 - Foreground update and service-start lifetime clarification](journal/2026/11.13%20foreground-update-service-startup.md)

- [2026-11-13 - Original canonical application integration merged](journal/2026/11.13%20canonical-application-integration.md)
- [2026-11-12 - Composition delivered and complete preparation integration](journal/2026/11.12%20complete-preparation.md)
- [2026-11-11 - Bounded public metadata acquisition delivered](journal/2026/11.11%20bounded-metadata-acquisition.md)

### 2026-11-06 - Integration review deadline and CI corrections

PR #11121's public metadata fetch now interrupts its owned response socket at
the caller's header/body deadline, including continuously streamed status/header
lines, body bytes and partial chunk framing. Its HTTP/HTTPS response parser
starts that bound before header parsing, not only after `urlopen` returns.
Each bounded watchdog is canceled and joined on exit. Containers' operating
documentation now distinguishes version-one target/profile declarations from
the required V2 complete-application packet and selected-application currentness.

The launch census records the two adopted native installer drivers and the
Manager recovery spawn's current function. The unit manifest binds the exact
canonical Python assets materialized by the existing Manager-provider test;
unit effect restrictions are unchanged. The Windows nonzero-CIM-owner fixture
explicitly selects and witnesses the CIM fallback instead of depending on the
native token-owner path failing for a synthetic PID.

Contained deadline/selection controls passed 79 cases; packaged-provider and
Windows CIM controls passed 86 cases, with cleanup verified in both runs.
After reconciling the branch with current Dev and extending the same deadline
controls to actual trickled status/header parsing plus an on-time response,
the combined selection passed all 168 cases with verified cleanup.
The next CI attempt exposed two admitted files whose local Windows CRLF bytes
differed from their canonical LF Git blobs. Their source bindings now use the
committed bytes; exact-byte verification and unit effect restrictions remain
unchanged. No host Git configuration or unrelated source was rewritten.
The response now owns one watchdog from construction through close, including
redirect-handler reads and read-error settlement; HTTP error responses also
close that owner. Actual 301/302/303/307/308 streaming redirects, ordinary
streaming/framing, successful redirects and HTTP rejection are covered with
explicit watchdog cleanup checks. The focused feed/collector packet passes
101 cases, Manager identity controls pass 11 and payload-local toolkit controls
pass three, with verified cleanup.

The payload guard exposed an independent coupling from source acquisition
through read-only payload discovery into Manager's installer adapter. Resource
discovery now lives in the existing runtime-identity owner, with compatibility
exports retained. The canonical generator synchronizes the Worktrees admission
toolkit; all thirteen payload manifests, the four source AST guards and thirteen
install contracts pass. The coverage verifier now names a bounded set of tests
missing production call coverage without changing acceptance. Identifying and
correcting the actual full-packet coverage failure remains required; focused
assertion success is not accepted full-packet evidence.
An explicit Linux diagnostic run on that published head identified all four
missing call contexts: the two portfolio-CLI cases and two elapsed-budget
refusals call already admitted source-preparation modules that were omitted
from production measurement. Both modules are now explicit coverage targets;
no case, byte binding or effect restriction is removed. Those four functional
controls pass, as do fourteen profile-association controls. Post-reconciliation
Worktrees inventory/copy controls pass 63 cases with three explicit platform
skips, with verified cleanup. Full exact-head Linux packet verification remains
the acceptance gate. Historical journal count phrases now describe their
recorded adopter selection rather than claim today's complete plugin roster.
The broader historical workflow test selection still encounters the
[previously recorded non-enrolled recipe gap](https://github.com/ThomasMichon/copilot-extensions/issues/1303#issuecomment-6627995884)
and [retired-workflow fixture failures](https://github.com/ThomasMichon/copilot-extensions/issues/6237#issuecomment-6282693571).
Those results are not a full-portfolio pass or authority to restore retired
required jobs. Exact-head independent review, required CI, source merge,
promotion, the outstanding isolated MCP capacity gate and separately authorized
installed adoption remain open. No host runtime or production state changed.

### 2026-10-30 - Proposed combined-trigger admission case

Added unchecked direct #5353 Plan/local AND actual global Validation and
continuing same-owner coordination. Existing no-spawn/adoption/serialization
credit and current fresh-default policy remain; the historical combined
path needs attributable controlled qualification/disposition. No current
outage, live retry, implementation, accepted receiving delivery, source
closure, execution appointment or resource release is claimed.

### 2026-11-03 - Configured-feed redirect acquisition

- The bounded shared acquisition slice in source handle `creaky-gauge` closes
  the Runtime Assembly vision's approved-source/feed-neutral acquisition intent.
  It does not introduce another resolver, installer, proxy or trust registry.
- The candidate follows configured-feed metadata and exact selected artifact
  HTTPS redirects without a required CDN list. Nonempty destination restrictions
  remain enforced. Public PyPI metadata substitution, HTTP downgrade and
  cross-origin credential forwarding remain refused.
- Native acquisition identity binds the delivery contract and explicit origins,
  so older policy evidence cannot silently authorize changed retrieval semantics.
  Canonical helper copies are synchronized through the existing generator.
- Contained native TLS, acquisition-policy and budget regressions passed
  140 cases with settled cleanup. A fresh Linux container using a relayed
  configured feed completed both Manager and Worktrees native profiles and
  relocated `install.sh --stage` entries without destination enumeration.
  Exact input identities, installed distribution versions, unchanged UV locks,
  absent Copilot inventory and no activation were checked; the guest was removed.
- The first stage fixture incorrectly compared a declared development version
  with its normalized installed spelling. That failed attempt remains separate;
  the corrected run compares equivalent package versions, not arbitrary strings.
- #10269 merged after current-head approval with no findings and required CI.
  Promotion #10279 released the fix to Main
  `c5921f14082c41a48923dc1d2b2ee4bf0d6c74fa`; the canonical acquisition,
  native-feed and profile helper blobs match the reviewed source fix.
- The unchanged complete Linux unit packet passed 3,940 cases within its
  original bounds. The full Windows packet reached its outer time limit;
  that lane is not qualified and its limit was not increased.
- A new fresh-container run executed an immutable export of that actual Main
  commit with the retained first-party inputs. Both complete profiles and
  relocated native stage entries passed again, with unchanged locks, matching
  installed package versions, absent Copilot inventory and no activation.
  Exact-instance cleanup and unchanged watched host state were verified.
- The bounded redirect acquisition item is complete. Full installation,
  serving/adopter transition, remaining applications and the parent campaign's
  other Plan and Validation items remain open under their existing owners.
  No installed host runtime or production configuration was changed.

### 2026-11-03 - Automatic-update controls qualified and merged

#10103 merged the bounded Phase 4a.1 implementation after exact-head native
functional qualification, independent approval and required CI. The
[qualification record](automatic-update-control.md#source-gate-closed-2026-11-03)
keeps the failed 300-second attempt distinct from the separately authorized
passing attempt. Phase 4a recurring maintenance, Manager-first setup and
installed/native-lifecycle acceptance remain open under their existing owners.

- [2026-11-01 - asynchronous Worktrees predecessor drain](journal/2026/11.01%20asynchronous-installer-drain.md). Bounded #9919 source adoption; complete construction, other adapters and installed acceptance remain separate.

### 2026-11-02 - Contributor-ready fast-install and cutover map

The operator requested an exact per-app support guide, the ideal end-to-end
sequence and app tracking suitable for other agents/operators. The
[contributor handoff](wheel-first-contributors.md) captures that request
verbatim, the core-service priority refinement and a separately labeled
implementation organization. Its eleven-step target remains distinct from
today's environment-only staging and independently released app slices.

The [guide](../../../docs/patterns/fast-application-install-and-cutover.md)
records copyable UV projects, source/profile admission, stage/setup/activation,
stable front doors, resident ownership, failure/commit boundaries and native
qualification. The matrix covers the 21 current runtime roots and separately
tracks the SSH Manager dependency, which has no independent app entry point.
Manager/Worktrees have the bounded #9990/#10024 release evidence; Bridge/MCP
remain in active qualification and other app scopes retain their owners.
No runtime, source producer, installed system, maintenance hold or deployment
authority changed.

Documentation consistency, runbook references, canonical effort structure and
the new guide/matrix's exact local paths/anchors pass. Runtime unit, clean-room
and live-venue reruns are inapplicable to this documentation-only slice; the
matrix retains their outstanding application gates instead of implying a pass.

- [2026-11-02 - composed source association](journal/2026/11.02%20composition-association.md). Canonical Main-seeded snapshot/profile association and native copied-application CWD correction; complete reuse and whole-flow acceptance remain open.
- [2026-11-02 - historical selector publication](journal/2026/11.02%20selector-publication.md). Bounded Manager I/O-only correction; whole-flow and installed acceptance remain open.
- [2026-11-01 - finite source merge and separately prepared non-UI validation](../lean-runtime-dependencies/journal/2026/11.01%20non-ui-process-safety.md). Phase 4a/4b, native replacement and installed adoption remain open.

### 2026-10-28 - Fresh-default installation and recognizer manifests

The operator changed the installation baseline: upstream fresh construction,
with positive recognition selecting incremental/ZDD optimization rather than
permission to update. Both requests are retained verbatim in the inception
record. The installer vision and existing execution/deployment plans now carry
that direction and a [bounded implementation/validation proposal](fresh-default-installation.md).

Source research identified the existing global bootstrap interception,
immutable-slot/receipt/removal helpers and durable update driver as integration
owners. A complete disposable-footprint manifest, producer/delta completeness
guard and old-process/registration silence remain implementation obligations,
not delivered behavior. The prior normal/repair admission prose is reconciled;
no separate updater, signing protocol or new blanket permission gate is added.
Existing #7758, maintenance, removal and critical-connectivity owners remain.
Review precedes new orchestration implementation; no host operation is authorized.

#9487 merged as `5963bf78d5ed2d96217d843e03fb524b206dfbac` after genuine
exact-head Copilot approval with no open findings and all required checks
passing. Documentation consistency, three effort/vision structure checks and
diff checks passed. The review gate closes; manifests, deltas, routing,
fresh/incremental execution and native installed acceptance remain unfinished.
The next bounded slice extends the existing finite uninstall inventory into
the maintained footprint contract, rather than adding a second cleanup list.

### 2026-10-28 - Direct retained keeper and source selector

The complete #7028/#7029 original/refined/inherited acceptance is retained
through the direct Plan/Validation annex and this continuing Coordination.
The bounded read-only study checked full current contracts and actual current
receiving rows, crediting delivered assembly, policy/acquisition/preparation,
SDK and explicitly recorded native component witnesses at their finite tiers.

The named #184/#403, maintenance/setup-holder and exact Logger role/six-command
details remain explicit rather than disappearing into general portfolio rows.
Existing implementation, transaction, source/resource and native custodians
are unchanged. Normal review, actual merge and complete current receiving
readback precede any supported #7029 tracker consolidation. #7028 stays open;
no code, test, native, service, source claim or deployment action occurred.

### 2026-10-28 - First maintained footprint provider

The first source slice extends the existing finite uninstall inventory rather
than introducing another cleanup owner. Agent MCP's version-2 profile contains
one current reset inventory, append-only historical snapshots/deltas, separate
layout recognizers and explicit process/control/registration/shared-environment/
preserved-state descriptors. Its native remover consumes the same profile.
The source guard covers complete producer trees, including new files, and
compares published history with the source baseline.

Bounded qualification passed 151 shared removal controls (three platform skips),
124 canonical packaging controls (two skips), and four native MCP
removal/restamp/provenance controls (one skip). Native removal/restamp exercised
both Windows PowerShell and PowerShell with only the process census mocked.
The installer contract passed for all 13 then-current runtime plugins. The packaging fixture
now supplies the Manager package manifest needed by the complete-payload
validator and the new canonical support file. No deadline was enlarged after
the initial combined batch timed out; its owned cleanup was verified and the
failing fixtures were isolated and corrected.

This is a data provider and finite-inventory convergence, not complete fresh
orchestration or old-owner silence. Effect resolution/retirement, global
bootstrap routing, driver integration, the remaining suite/Manager manifests,
cross-platform assembled entry points and actual installed survival/adoption
remain open under the same owners and authorization gates. No live cleanup,
runtime update, service action or maintenance hold was performed.

#9499 merged as `6fa86779c07f38db08a6db656efb26249b993989` after exact-head
Copilot approval and all required checks passed. Both duplicate promotion-version
findings are fixed and resolved. The final source passes 32 focused footprint
controls, the 13-plugin install contract and the required 2,619-test unit packet.
Generated packaging metadata is excluded consistently with materialization;
only promotion-owned source-version literals are normalized, leaving surrounding
producer code covered. The next slice separates Main bootstrap transport from
independently selected Manager/service runtime sources at the existing global
update interceptor and canonical bootstrap. The broad footprint and fresh-default
Plan/Validation rows remain open.

### 2026-10-28 - Bootstrap transport and runtime-source separation

The existing global update interceptors now select Main bootstrap transport
independently of Manager's runtime `[source]` policy. Explicit bootstrap repo/ref
environment overrides affect only the initial acquisition. The canonical
bootstrap owns the strict local source reader and forwards the ordinary update
through its existing installer/driver path; the old explicit-source bootstrap
form remains supported. Service-runtime policy stays separate, including the
shared `[runtime_source]` table, and relative local sources are anchored before
staging changes the working directory.

Isolated Windows controls exercise real local-Git acquisition and the assembled
global/installed-project front doors in Windows PowerShell and PowerShell Core.
They retain exact source pins, original arguments/exit status, malformed-policy
refusal before effects, old explicit callers and broken-runtime independence.
The existing optional native update-diagnostics workflow now includes these
front-door families and the POSIX equivalents rather than widening required PR
CI. Native POSIX execution, review/merge and the parent's complete construction/
retirement/survival gates remain uncredited until independently observed.
No installed runtime, maintenance hold, service or protected process was changed.

#9507 merged as `cf87d5c8e2559a72f2a413a69d417ea7f10d6676` after exact-head
Copilot approval with no findings, all required checks and native workflow
`40811339020` passed. The final unit packet passed 2,619 tests (12 deselected).
Native entry-point families passed 109 controls on Linux (70 platform skips)
and 80 on Windows (99 platform skips), including actual local-Git acquisition.
The first native run exposed fixture-only context and default-encoding
assumptions; explicit temporary working-directory and UTF-8 reads fixed them,
and the complete workflow rerun passed on both platforms. A metadata-triggered
required-check transition was awaited through the canonical PR watcher before
the managed merge. This is merged source, not a running-system rollout.

The next integration connects footprint reuse/no-match decisions to the existing
canonical construction and execution owner, initially for one addressed installed
owner. Missing reuse evidence must select fresh construction without discarding
its finite reset inventory or requiring a separate repair command. Actual effect
settlement, one-writer custody, durable/config/protected preservation and the
remaining manifest portfolio retain their independent parent acceptance gates.

The first construction integration now passes MCP's maintained footprint into
the existing attached native constructor, inside its original installer
transaction. No-match and occupied unusable targets select an absent immutable
replacement generation without rewriting the incumbent; a compatible selected
same-input replacement remains reusable. Source identity stays independent of
the physical generation. Local regression qualification passed 127 tests,
with three native-POSIX FIFO cases explicitly unqualified on Windows; lint,
module-size and install-contract checks passed. The optional native
update-diagnostics lane now includes the same constructor controls on Linux
and Windows. The required local unit packet passed 2,619 tests (12 deselected)
after refreshing only the changed runtime source fingerprint in its existing
admission manifest; node inventory and effect restrictions are unchanged.
Native execution, provider checks and review/merge remain pending.
This is not effect-reset, corrupt command-ownership recovery,
old-owner silence or complete fresh-install qualification.

### 2026-10-28 - MCP footprint-driven construction qualification

#9512 merged as `864dbfac12f42189b6632a365892ab7b8da1ebc5` after the
current-head Copilot review reported no open findings and all required checks
passed. Native workflow `40824809607` passed against
`682966d55a352bf8562cf2174ea615399adfd812`: the combined MCP constructor,
identity, readiness and serializer controls passed 48 tests on Linux
(7 platform skips) and 38 on Windows (17 platform skips).
The required local unit packet passed 2,619 tests (12 deselected);
121 footprint/environment controls also passed.

Review corrections preserve exact repair identities, avoid blocking completion
reads and unusable fallback targets, retain physical generation identity in
publication/readiness, and align reuse admission with the resolver's 64 KiB
marker bound. Invalid installed UTF-8 metadata selects non-reuse without
swallowing genuine storage errors. Actual admission and native constructor
controls preserve both a healthy current slot and a damaged non-current desired
slot while completing a distinct repair generation.

The complete parent Plan and Validation Plan remain Active. The next bounded
integration is fresh-path command publication through missing or corrupt old
deployment metadata under the existing lifecycle/execution owner, preserving
real foreign/edited command refusals and durable/protected state. Reset
execution, identity-bound displaced effects and reactivation prevention, the
remaining manifest portfolio, macOS and assembled public-entry/live survival
acceptance remain open. No host rollout, maintenance hold, service or protected
process was changed; merged source is not deployed-state proof.

### 2026-10-29 - Fresh-path metadata and command-owner recovery

#9533 merged as `318809886f29d17a9e533f340d61c9edd44552d7` after an
exact-head Copilot approval with no active findings and required CI success.
Native workflow `40836786937` passed on
`cbbdeb6aad583147975468d3ece234a3a79a668b`: command-metadata recovery,
pending ownership and teardown controls passed 37 tests on each platform
(two platform skips each); MCP construction passed 52 tests on Linux
(seven skips) and 40 on Windows (19 skips); prepared-source reconstruction
passed 48 tests on each platform. The required local unit packet passed
2,619 tests (12 deselected). The broader matcher/removal/lifecycle packet
passed 904 tests with verified bounded-runner cleanup.

Malformed ordinary deployment bytes now differ from genuine storage/identity
errors. Missing/corrupt receipts no longer block construction or supply false
incremental evidence; failed recovery retains its pending source owner.
Review fixes reuse Index's existing launcher matcher with the resolved home
and preserve intact incumbent publisher paths as literal matching data across
payload relocation. Edited/foreign commands and explicit foreign ownership,
policy and cell evidence still refuse mutation. The Linux teardown fixture
now extracts the actual top-level preflight rather than an earlier `elif`.

The parent remains Active. The next bounded integration is the maintained
MCP reset inventory's actual known-runtime/effect settlement through the same
installer and execution owner, with durable/protected preservation and
displaced-owner/registration silence proved independently of candidate markers.
Remaining manifests, assembled public entry points, macOS, all-tool/Manager
same-data transition and live session/connectivity survival remain open.
No host rollout, maintenance hold, service or protected process was changed;
merged source does not establish released or installed state.

### 2026-10-29 - Confirmed MCP incumbent-process settlement

#9541 merged as `78a5d11f7652c2c3949d5d5c59883de28f7187e5` after
Copilot approved the exact final head and required CI `40883025652` passed.
The description-triggered source-attribution check was observed passing before
the managed merge. The final metadata-only commit updates one existing unit
source digest without changing the 2,619-node inventory, coverage targets,
effect restrictions or 300-second packet budget.

Native workflow `40881548874` passed on the unchanged runtime/test bytes at
`470086a1b9a8f7e7dca716b71a4f0fed184c1b74` on Windows and Linux.
Linux passed all 48 MCP retirement controls and all 17 native pause/custody
controls without skips, including main-thread exit before census and during
executable observation, opaque task names, rollback and actual caller-group
death. Earlier full Windows broker qualification `40869467283` passed the
unchanged nested caller-Job death witnesses and contained negative, retaining
original targets and observing custodian kernel exit without desktop effects.

Fresh MCP fallback now confirms original-birth root/descendant exit through
native handle/pidfd custody, preserves protected/current/excluded scope, and
cannot turn an explicit no-reap override into successful settlement. Review
corrections cover disappeared/reused identities, same-birth protected exec,
stable all-thread pauses, owner-death rollback, dead-main-thread census races,
kernel-task recognition, opaque task names and available PowerShell warnings.
Only the bounded process Plan/Validation items above are complete.

The parent remains Active. The next bounded integration is actual reset of
MCP's maintained disposable control/runtime inventory and displaced-code
reactivation routes through the same native installer/execution owner.
Durable/configuration/unknown/protected preservation, the remaining manifest
portfolio, assembled public entry points, macOS, all-tool/Manager same-data
transition and live session/connectivity survival remain open.
No host rollout, maintenance hold, service or protected process was changed;
merged source is not released or installed-state proof.

### 2026-10-29 - Reviewed bounded MCP control-reset ordering

#9551 merged as `8deff0bd1b4336d236468821d2af1dcd0fcb6c84` after Copilot
approved the exact proposal head without inline findings and all required
checks passed. The source worktree reconciled to that verified squash boundary
without replaying post-merge commits.

The next implementation follows the reviewed finite control-home reset,
existing cutover/serve/routing custody and selected-generation retry-consistency
contract. Its Plan and Validation items remain open until implementation and
native qualification merge. Old-code collection, complete reactivation
prevention and every installed/portfolio/survival acceptance gate remain open.
No deployment, maintenance hold or host cleanup is authorized by this proposal.

### 2026-10-29 - Qualified MCP control reset and retry consistency

#9553 merged as `f39fdc789e1406c52506b78f1c8b1520c684ccc3` after Copilot
approved exact head `181ae83d858e130ffcfe6dc3de75fd3cab73737a` with no active
issues. Required checks, including source attribution after the final
description edit, passed before managed merge. The source worktree reconciled
to the verified squash boundary without replaying post-merge commits.

Native workflow `40904358897` passed on Windows and Linux at that exact head.
The retirement/reset packets passed 79 and 82 tests respectively, including
Windows dead-routing recovery rather than skipping it; the construction/native
adapter packets also passed. The combined local reset/footprint packet passed
83 tests with three POSIX-only socket-type skips.

Fresh reset now preserves winning owners, socket-home overrides, live routing
and sidecar endpoints, lock identities and durable/foreign content. Windows
timeouts require a successful exclusive endpoint bind before absence is
established. Routing ports/PIDs and duplicate fields follow the actual client
parser. Fresh construction cannot reuse the recorded deployment generation
even when its old slot is absent, so completion/activation without successful
integration cannot make the retry incremental.

Only the bounded control-reset Plan and Validation items close. The next
agent-recommended proposal addresses declared displaced code and restart
routes, including the constructor's own predecessor-retention handoff.
Old-code silence, the remaining manifest portfolio, assembled entry points,
macOS, same-existing-data all-tool/Manager adoption and live session/transport
survival remain open. No installed cleanup, rollout, service stop, maintenance
hold or protected-process action ran; a source merge is not deployed-state proof.

### 2026-10-29 - Reviewed MCP displaced-code retirement

#9556 merged as `08d062c05d79de789c67123fa2c38d14915215f2` after Copilot
approved exact proposal head `c64211c5fe3354860e38e66979975bf1bc287562`
without active findings. Required source and metadata checks passed before
managed merge. The source worktree reconciled to that verified squash boundary.

The next implementation follows the reviewed finite executable/restart-route
inventory, attached predecessor-retention handoff and canonical generation/
publication custody. Keep the candidate retained and independently owned
leases intact; qualify actual removal, preserved scope and interrupted fresh
retries on Windows/Linux. The displaced-code Plan and Validation items remain
open until that implementation and qualification merge. All parent portfolio,
installed adoption and live survival gates remain open; this proposal grants
no concrete host cleanup or rollout.

### 2026-10-29 - Independent incoming startup for ordinary and repair updates

#9601 merged as `230c7b1012e6e6767d1fbe6dc4541e73019d7470` after genuine
Copilot approval of exact head `6284c967bf8a7acc7c99b4ffed5cab03148e08cc`,
with no active findings and all required checks passing. The one review
finding exposed inherited force-termination acceptance clauses; the correction
reconciles the complete retained normal/repair interpretation instead of fixing
only the newly added test row. Effort/vision structure and whitespace checks
passed. The source worktree pulled forward to the actual squash boundary and
the complete current ROOT README was read back.

The operator's decisive V1-to-V2 test and explicit normal-and-repair scope now
govern replacement: apply/start the incoming generation independently, replace
current commands/update task registrations, observe brief runtime overlap, then
prove the outgoing owner recognizes supersession and retires itself. Mandatory
installer-driven old-code/process retirement is superseded as an update
prerequisite. Its unpublished prototype is retained separately, not deployed
or credited as the new lifecycle implementation. Explicit uninstall and
separately authorized recovery retain their own scopes.

The next stretch builds the contained real Manager/suite acceptance for both
ordinary update and repair/force-install, then extends the existing incoming
startup and supersession owners to make it pass. Those Plan and Validation
items remain open. The parent stays Active with its complete portfolio,
same-existing-data adoption and live all-code-absent session/connectivity
survival gates intact. No host install, task mutation, service stop, maintenance
hold or rollout occurred.

The installed-suite candidate also exposed Dispatch's copied global admission
helper without its required Python SDK. Both installers now publish the full
helper file set, and Bash's publication journal covers those same dependencies.
An actual publisher/copied-helper regression failed with the hosted SDK error
under both PowerShell hosts before the correction; the focused launcher packet
then passed 8 tests with 1 platform skip. The Bash stamp control is skipped on
the Windows host, so native POSIX qualification remains open. Installer,
documentation, headless, size and lint guards pass; no hosted suite or installed
adoption result is inferred from this bounded repair.

The bootstrap guard failures tracked by #9699 came from administrator-trusted
fixture links, not a missing enforcement flag. Restricted same-user creation
now retains the original guard/physical-path assertions and restores thread
identity on failure. The complete local bootstrap packet passes 36 tests with
9 legitimate skips (7 POSIX-only, 2 file-symlink prerequisites); native POSIX
and those unprivileged file-symlink effects are not credited. Updated resident
fixture controls pass 28 tests with the 2 hosted acceptance cases deselected,
including real Manager subscriber transfer and incumbent log retention.
Whole-suite normal/repair qualification and installed adoption remain open.

The Codespaces candidate now starts the selected process independently of a
running task, advertises passive readiness separately from serving work, and
holds native work custody through cleanup. Independent review caught a lost
lease-less predecessor exclusion across passive restarts; its durable native
birth record and genuine lease-less restart/supersession controls resolve that
named finding. The owner packet passes 165 tests with 1 skip, coordinator
verification passes 8 new barrier controls, and native state-preserving removal
passes 45 selected tests. Shared teardown validation now preserves foreign
commands without granting publication, and native installer diagnostics retain
stderr framing and line endings. The lifecycle packet records 88 passing cases
and 18 platform skips; its two fixture-omission failures pass after restoring
the actual removal function. Exact-head installed-suite qualification, not an
aggregate component count, remains the next acceptance gate.

Hosted run `41146273383` at `24abb8b041` fails both preparation stages before
replacement: the fixture called `ensure_owner_running` without its required
config argument. Both final settlement stages succeed with no remaining tasks.
The caller now passes the existing supported `None` argument; a focused
execution of the actual preparation snippet covers that contract, and the
bounded fixture packet passes 29 tests with the 2 hosted cases deselected.
No service API is changed, and Bridge's earlier failure remains unproven.

Hosted run `41148076239` at `7ddefb7184` completes the normal Manager update
with exit 0, then fails the fixture's unsupported assumption that Machines'
stdio-safe command emits a resolver trace. Its real fast path selects the
current slot directly. The fixture now uses its exact installed version output
and the actual command-only Windows producer contract. Repair preparation
reaches a successful baseline update but exceeds the outer 900-second stage
budget before completing the independent engine setup. Preparation now has a
bounded 1800-second budget; exercise, assertions and settlement remain separately
bounded. Both failed jobs settle with no errors or remaining tasks.

The corrected fixture/coordinator packet passes 41 tests with 2 hosted cases
deselected. Bridge watchdog diagnostics pass 18 tests and retain PID, timestamp,
failed probe and terminal reason before replacement log setup; the fixture also
retains the existing application diagnostic stream. This is diagnostic evidence,
not a fix or waiver for Bridge's early exit 70. Codespaces' observed predecessor
exit before successor creation remains a separate readiness-ordering defect.
Neither mode has qualified the complete start-first contract, and installed
adoption and the parent Plan/Validation remain open.

The Codespaces candidate now separates selection demotion from process
retirement. Demotion stops new work admission; a fresh principal/birth-verified
beacon of the different selected successor permits incumbent cleanup and exit.
The native process control starts V2 before V1 drains, and retains the existing
lease-less predecessor exclusions. Its custody packet passes 176 tests with
1 skip. This closes the named source-level ordering defect, not the still-open
exact-head hosted suite or installed-adoption gate.

Run `41153778636` at `1d80d4f3d2` confirms normal Codespaces successor readiness
before clean predecessor exit. Normal update returns 0, but its fixture next
incorrectly requires an MCP resolver trace; MCP shares Machines' command-only
direct-slot fast path. Repair reaches replacement, then the controlled delivery
fixture rejects the required payload-only harness because it admits only the
runtime roster. Delivery now uses the complete canonical catalog and records
newly installed payloads without admitting unknown sources.

Bridge's preserved watchdog diagnostics identify repeated Windows socket
allocation error 10048, not evidence that the server stopped serving.
The fixture's 20 ms route-metadata polling opened a fresh listener-probe TCP
connection on every read. Metadata observation now omits that redundant
connection; authenticated health and native birth/exit checks remain required.
No watchdog policy, socket limits or host settings change.

Normal settles cleanly; repair leaves birth-verified Index and Vault V1
processes alive after native task cleanup. Both installer stop functions had
tested their prospective payload-version interpreter, which was not installed
after payload refresh alone, and skipped the real shutdown command. Explicit
stop now resolves the installed selection. Native callee controls reproduce
both omissions before correction and verify the selected interpreter afterward.
The combined fixture/coordinator packet passes 48 tests with 2 hosted cases
deselected; all 11 focused failures pass after correction. Full hosted
qualification and final settlement remain open.

Run `41158624541` at `6a3fa4881b` completes both real update operations with
exit 0. In both modes, all seven replaced residents become ready before their
incumbents exit 0, the independent engine remains healthy in the same process,
and final settlement has no errors or remaining tasks. Qualification still
fails on the fixture's unsupported scheduled-task resolver assumption.

Bridge's real marker-resolving task launcher now supports a read-only
`-ResolveOnly` observation before any startup effects. Native broker tasks use
their actual installed V2 entrypoint, not the PowerShell resolver shape:
verification loads bounded owned registration, checks exact XML action/root/
principal agreement and the selected prefix, then executes the existing
`--check-runtime` observation through the task interpreter's console companion
without bytecode writes. Unknown shapes and old/changed registrations still
refuse. Both new positive controls fail before correction; the updated bounded
fixture/coordinator packet passes 52 tests with 2 hosted cases deselected.
This source correction still requires exact-head hosted qualification.

Run `41162149444` at `76e883b0d2` again completes both updates with exit 0,
all seven successor-ready observations before predecessor exit 0, the same
healthy independent engine and clean final settlement. The fixture next rejects
Dispatch's custom marker-resolving coordinator launcher before reaching the
formal assertions. Both generated Dispatch coordinator and supervisor launchers
now expose the same read-only selected-interpreter observation before startup
effects, with native controls covering current selection, last-known-good and
unresolved selection without file changes. The task-routing proof remains
required; this is not a hosted-suite pass.

Run `41165376625` at `d7d0601416` completes preparation and replacement in
both modes, including all actual task-routing probes, the seven ready-before-exit
replacements, the retained healthy engine and clean settlement. Formal assertions
now expose changed V1 sealed-content evidence in four provider slots in both modes
and Manager in normal mode. The report has no changed-path metadata to identify
the cause, so bounded before/after hashes and native file metadata are now retained
without weakening comparison. The normal exclusive-work probe also ran after
releasing the fixture subscriber; final routing/work checks now remain inside the
existing live consumer-demand scope. The updated local fixture packet passes
54 tests with the 2 hosted-only cases deselected. Full qualification remains open.

Run `41167899886` at `b9f363e9d1` establishes held Manager work custody in both
modes while preserving successful replacement and clean settlement. Exact
retention diagnostics identify only added Python bytecode files, with no changed
or removed sealed files and no truncated samples. Normal adds 120 Worktrees,
1 Bridge, 3 Codespaces, 1 Dispatch, 4 embedded Index-server and 2 Manager cache
files; repair adds the same Bridge/Codespaces/Dispatch/Index files. The retention
assertion remains failed. The operator selected external caching rather than
disabling it; the next source slice follows the actual managed command, engine
and resident launch paths and must qualify cached imports outside immutable
slots without moving existing data or exempting generated files.

The external-cache source slice now routes actual Python command, resident and
nested-server launches through owned durable cache prefixes, preserving isolation
and explicit read-only bootstrap controls. Cold/warm parent controls pass for
Bridge (14), Codespaces (14), Index (68 with 1 platform skip) and Dispatch (16).
The shared implementation also qualifies the required-context Worktrees Python
dispatch and Manager resolver. These are local source controls, not a new hosted
whole-suite pass or installed rollout.

The broader Companion/repair packet initially hid ten explicit-tier cases.
Using the supported `--allow-explicit-tiers` path yields 43 passes, 2 platform
skips and four real repair failures. The Companion environment leak is corrected
through PowerShell's `Env:` provider, retaining existing credential-scrubbing
assertions. The repair fixture no longer uses an escaping dependency path hook
and materializes complete canonical payload dependencies before copying them.
The subsequent contract trace corrects that diagnosis: public Manager updates
already acquire canonical driver custody before internal repair, whereas those
four fixtures invoked the internal path without its enclosing owner. They now
run under a real isolated Manager transaction and authenticated participant.
The construction fence unwinds its private stop signal inside canonical custody,
then exits nonzero outside it; no package construction or fake repair success is
permitted. The combined explicit repair, Companion and custody packet passes
84 tests with 2 platform skips, including an unowned-repair refusal control.
Monitor interpreter and retained-launch controls pass 19 tests without an
unverified executable path hook. Generated payloads, installer helpers and
vendored-library checks are in sync. An operator-authorized separate one-line
full-dispatch mapping repair passes 78 dispatch/publication-label tests.
These remain local source results, not hosted cache-retention qualification,
merge, rollout or completion of the Plan and Validation Plan.

Hosted run `41186916815` qualifies exact source `e99370d60a` in separate
normal and repair Windows jobs. Repair passes; normal preparation, exercise
and settlement pass, but strict retention still fails on two added Manager-slot
bytecode files, with no changed or removed originals. The canonical driver's
private executor starts an isolated interpreter without cache routing before
loading its participant and Windows process helper. That hop now uses the
existing driver-owned durable cache root, without changing custody or product
arguments. A real queued cold/warm executor regression and settlement controls
pass 36 tests with 3 skips and verified cleanup. Canonical archive regeneration
finds no additional derived changes. Both complete hosted modes must qualify
the corrected head before this draft becomes ready; no acceptance assertion is
weakened and no live rollout is authorized.

At corrected head `adebd56e6b`, hosted run `41189935743` passes normal mode:
all 14 original slots have zero added, changed or removed files. Repair's
preparation, exercise and settlement pass, but strict retention finds 145
added files in the old Worktrees slot; the bounded path sample is truncated.
Sampled modification times precede repair bootstrap startup, so the writer
cannot be assigned to that installer without more evidence. Diagnostic-only
fixture changes now compare preparation-end and pre-exercise state to the
original seal and record scoped runtime process launch flags, never argument
values. Their 56 local tests pass with the two hosted evidence assertions
deselected and cleanup verified. No baseline is resealed, no assertion is
relaxed, and the underlying retention discrepancy remains open.

Diagnostic head `d8feab8525` passes both complete hosted modes in run
`41192837539`, including strict original retention and clean settlement.
Preparation-end and pre-exercise comparisons are unchanged, and the observed
Worktrees resident carries cache-prefix and no-bytecode launch flags. The
earlier 145 additions do not reproduce; that pass alone does not identify
their writer. A separate source regression proves that Worktrees' frozen
Manager engine callbacks still omit cache routing and create bytecode beside
their source. Direct Manager, status-link and fast-Picker handoffs now reuse
the existing cache-aware callback command with Worktrees' durable root.
The cold/warm real child and affected callback/Picker controls pass 101 tests
with 156 deselected and verified cleanup. Callback interpreter, argument,
environment and custody contracts remain unchanged apart from the cache flags.
The new exact head still needs both complete hosted modes and actual review;
the historical writer is not claimed to be uniquely attributed.

The [October 31 review-correction record](journal/2026/10.31%20review-corrections.md)
captures the reconciled source history, all six fixes, actual bounded local
results and the remaining exact-head Windows/POSIX/review gates. No local result
or earlier hosted head substitutes for qualification of the integrated changes.

The [November 1 qualification correction](journal/2026/11.01%20replacement-qualification.md)
records the new review fixes and the dependency-free Index activation diagnosis.
The corrected integrated head still requires exact-head whole-suite acceptance
and actual review; the earlier passing packets do not close those gates.

### 2026-11-01 - Qualified independent suite replacement

#9623 merged as `7a522f7fd890a6497706462796576f3ea3585839` after the
exact-head Copilot review recognized all three final corrections as resolved,
with no new inline findings, and all required checks passed. Both complete
Windows modes at `dac675ad9972` pass preparation, exercise, strict assertions
and final settlement in run `41323397699`. The
[qualification record](journal/2026/11.01%20replacement-qualification.md)
retains the actual evidence and its limits.

Only the contained independent ordinary/repair replacement and external-cache
source qualification close. The same PR also lands the wheel-first intent
checkpoint, including promotion-mediated backing checkouts and every-app
staging/activation; none of those proposed command/provider flows is credited
as implemented. The full parent README was read back and the source worktree
reconciled to the verified squash boundary without replaying its merged commits.

The next stretch consumes the existing #9290 promotion/UV-native,
pyproject-strict provider through the W1 Manager/application boundary. Keep
construction, wheel caching, profiles and publishing with their existing
owners; qualify provider support before sending proposed stage/source flags.
Complete portfolio/platform acceptance, maintenance, actual same-existing-data
adoption and live all-code-absent session/connectivity survival remain open.
No host installation, cleanup, service operation, maintenance clearance or
rollout occurred.

### 2026-11-01 - Worktrees installation and resident retirement separation

#9919 / #9935 implement the bounded Worktrees incoming-start-first slice.
The [source and qualification record](journal/2026/11.01%20asynchronous-installer-drain.md)
retains the resident-owned custody, review corrections and source-process proof.
Review and required CI still gate merge; parent acceptance and rollout remain open.

The [dated journal](journal.md) retains the complete implementation, qualification and review history. The Plan and Validation Plan above remain the completion gate.
