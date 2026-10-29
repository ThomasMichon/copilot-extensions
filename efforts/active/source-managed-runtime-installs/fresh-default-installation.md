# Fresh-default installation and footprint recognition

Parent: [Source-Managed Runtime Installs](README.md), Phases 4 and 4b.
Coordination: #7028 and #7758; existing source, maintenance and execution owners
retain their scopes.
Intent: [fresh-default upstream installation](../../../visions/installer/README.md#fresh-default-upstream-installation)
and [recognizer manifests](../../../visions/installer/README.md#recognizer-manifests).

## Required direction

The global-install binstub intercepts `<repo> update` and routes
`worktree-manager update --project=<repo>` to the canonical upstream bootstrap.
The default is the published GitHub Main one-line installation flow
(`irm`/`iex` on PowerShell, its supported shell equivalent on POSIX), unless
explicitly overridden through the supported environment seam. The bootstrap
reads the applicable local Dev/source, marketplace-cell and test overrides,
resolves them once, and orchestrates installation into the assigned slot.
Manager self-source, service-runtime source and plugin-content source remain
independent choices.
The environment override for bootstrap transport is separate from those
runtime choices: existing `[source]` configuration remains effective when the
Main orchestrator reads local policy, not an implicit pre-bootstrap redirect
away from Main.

Fresh installation is the baseline. A positively recognized compatible
footprint permits incremental/ZDD installation as an optimization. Missing,
old, corrupt or incompatible reuse evidence selects the mostly fresh path,
not an update refusal or a demand for a separate repair flag. Receipts prove
the ability to reuse; they do not grant permission to update.

The fresh path clears known disposable runtime state, retires known managed
processes and scheduled/startup registrations, and replaces non-config
generated globals so the old version cannot serve or reactivate. Preserve
configuration, the same durable data, unrelated installations and the protected
session/connectivity substrate. Do not equate an unknown incremental layout
with permission to recursively remove its entire root.

The [verbatim requests](inception-transcript.md#fresh-default-upstream-installation-orchestration)
and [manifest refinement](inception-transcript.md#recognizer-manifests-and-footprint-migrations)
are authoritative. This proposal changes source contracts only; it does not
authorize a concrete host cleanup, rollout, service stop or maintenance hold.

## Existing seams and bounded changes - agent-recommended

The existing global `agent-worktrees-update-entry.ps1` / `.sh` interceptors
now fetch the Main Manager bootstrap before old-runtime resolution.
`WORKTREE_MANAGER_BOOTSTRAP_REPO` / `WORKTREE_MANAGER_BOOTSTRAP_REF` explicitly
override that transport; the bootstrap itself reads local `[source] repo/ref`
policy through its internal `--bootstrap-update` mode. Existing explicit
`--repair-update` dispatch remains supported. Runtime-source configuration does
not redirect Main transport. Continue through this same bootstrap and addressed
installation, not another updater or a silently selected new root.

The canonical installers already have immutable completion/current-selection
markers, source-qualified receipts and selective runtime-removal primitives.
Those are useful inputs, but they are not yet a complete reset inventory.
`libs/update-driver` retains durable requests, native kernel custody, foundation
construction and outcomes. Extend these owners rather than running a second
native installer, creating another queue, or using the broad reset scripts.
The [deployment-policy boundary](deployment-policy.md) still distinguishes an
explicit management update from plugin-triggered publication.

### One manifest, two decisions

Define a versioned declarative footprint contract at the existing installation
owner. Keep its two decisions separate:

1. **Reset inventory:** installation-relative artifact classes, generated
   global projections, runtime/process identities and registration descriptors
   that are disposable. Record durable/protected exclusions. Resolve each
   descriptor against the addressed installation; a plugin name, process name
   or stale PID alone cannot select a deletion or termination target.
2. **Incremental compatibility:** expected markers, supported footprint and
   lifecycle contract ranges, and the historical transitions needed to reuse
   an existing installation. A failure here selects fresh construction using
   the reset inventory; it does not invalidate that inventory wholesale.

An unrecognized artifact is preserved unless it falls within an explicitly
declared disposable class. A required foreign/edited command collision or an
unretirable owner has a precise failure outcome; neither justifies deleting
unrelated content or a generic "unrecognized install cannot update" response.
Use existing provenance, custody and path protections without adding manifest
signing, renderer handshakes or a second installation-permission receipt.

Every installed-footprint change updates the manifest and its applicable
deltas in the same change. A delta records introduced, changed and retired
artifacts and supported predecessor layouts; configuration/data migrations
remain with their existing owners. Historical compatibility can select a
reviewed incremental transition or fresh replacement. A maintained old reset
descriptor may remain valid even when its incremental contract is unsupported.

### One transaction, two construction paths

Resolve the exact source, installation and desired target once. The existing
caller-independent driver holds exclusive custody across acquisition,
construction, old-owner settlement, selection and integration. Concurrent
same-target callers share/coalesce; different targets queue or follow the
existing desired-target policy. Neither path bypasses maintenance holds,
managed-publication restrictions or the authorization of the requested scope.
These checks do not depend on the old installation's health.

Build the selected immutable candidate before disruptive retirement where
possible, without modifying a completed slot. Recognition chooses reusable
inputs and compatible handoff; otherwise use fresh construction and bounded
retirement of declared old effects. Settle the prior installer and its owned
children before allowing the new writer to mutate. Invalidate stale runtime
control and reactivation state, not durable request/failure history or
application data. Corrupt old receipts cannot veto that supported fresh path;
unsettled real process effects cannot be relabeled successful cleanup either.

Use the same root and authority when establishing or recovering the driver.
Do not require the broken runtime to approve replacement, a kernel restart,
a new cell, a copied database or manual queue surgery. Preserve pending work
with its original outcome/source semantics; requesting a new install is not
silently replaying an uncertain old operation.

Success requires selected-runtime health and silence of every displaced
declared installer/daemon and restart route. If construction, scoped settlement
or integration fails, retain an attributable failure and describe any loss of
service; do not claim readiness, silence or an untouched healthy predecessor
unless actually established.

### Bounded MCP control-state reset - agent-recommended

The next implementation slice clears declared disposable MCP control state,
not the entire installation. It closes the reset portion of the installer
vision above using the existing immutable constructor, native process
settlement and execution driver. Incremental/ZDD remains a separate path.
Old-code collection and complete reactivation prevention remain required
follow-on work, not consequences inferred from a clean control directory.

The current native installers activate a completed candidate, attempt cutover,
then settle displaced processes on the fresh path. Cutover can already start
the selected daemon. Do not append indiscriminate cleanup after that sequence:
it could unlink the new owner's endpoint. Instead:

1. Construct and health-check the immutable candidate through the existing
   owner. Preserve construction completion as build evidence, not proof of
   integrated reset or serving readiness. Fresh installation skips the
   installer's best-effort ZDD attempt; recognized incremental installation
   retains its existing handoff. Retain health-gated current selection before
   the existing displaced-process census; selection is not reset completion.
2. Resolve the addressed control home using the same socket-home semantics as
   `ipc.default_socket_path()` and `Server._config_dir()`. It can differ from
   the code installation root. Validate that scope independently; never
   substitute the install root or treat an arbitrary supplied directory as
   disposable. Preserve unknown/foreign state and refuse unsafe parent
   indirection before effects.
3. Hold the existing `zdd.CutoverLock` for that home across fresh settlement
   and reset so an orchestrated passive start cannot race cleanup. Reuse
   the confirmed original-birth process settlement without extending its
   protected/current/excluded scope. After settlement, acquire the ordinary
   `SingleInstance(home, service="agent-mcp-serve")` lease nonblocking.
   A normal on-demand start may win first: report incomplete reset without
   unlinking control files or terminating that selected current owner.
   A free lease alone does not establish that an older lease-less passive
   owner has exited; retain native settlement and reject uncertain ownership,
   including a current passive owner left by a cutover completed before lock
   acquisition. Do not kill or clean through that owner.
4. While holding both guards, acquire the existing deadline-bounded
   `routing.routing_lock` for routing-state mutation. Reset only the finite
   declared socket, endpoint, generation-socket and control-token classes and
   routing state attributable to this home. Confirm displaced-owner exit
   before removing its state. Missing/corrupt contents are not an old-receipt
   veto, but a live foreign owner or ambiguous scope is not stale evidence.
   Remove a declared socket link itself without following its target; reject
   other unsafe file types, shared-file aliases and redirected ancestors.
   Never unlink the lease, cutover or routing lock files: replacing a held
   lock inode would let another writer bypass custody.
5. Release guards on every success/failure path. Continue generated-global
   publication and final deployment metadata only after mandatory reset
   succeeds. MCP stays on-demand; reset does not invent an always-running
   service. A post-reset current daemon may publish fresh state normally.
   A failure reports the actual selected/build/serving state and retained
   predecessor metadata, not success or automatic rollback to a dead owner.

Use the existing deployment record's `runtime_version` together with
`current-version` and construction completion to recognize an integrated
layout. Reject missing, malformed or inconsistent selected-generation
evidence for reuse, selecting fresh construction instead. In particular,
activation followed by failed reset/global publication must not let an old
deployment record plus a new completion marker turn the retry incremental.
This strengthens consistency of existing evidence; it introduces no
installation-permission receipt, repair flag or approval from the old runtime.
Do not publish the new deployment record early to make recognition pass.

Append the next footprint revision after published revision 4, with the
changed producers, reset descriptors and preserved coordination files.
Do not rewrite historical snapshots. Keep configuration, durable application
and driver data, logs and unknown artifacts outside this reset. Do not invoke
whole-uninstall or recursively clear the runtime root as an implementation
shortcut. Retained old slots, pinned global routes and any other declared
reactivation effects still need their own bounded retirement qualification
before the parent old-version-silence requirement is complete.

Qualification must cover actual native Windows/Linux fresh adapters with
missing/corrupt evidence, distinct code/control roots and interrupted retries;
finite scope and byte/data continuity; normal-start and cutover contention;
lease-less passive uncertainty; preserved lock identity; caller failure and
guard release; and successful on-demand serving after reset. Negative cases
must show no deletion of a winning current owner's state or foreign targets,
not merely a nonzero return. Exercise the recognized incremental path as a
positive control. Source fixtures do not satisfy the separately authorized
installed survival, complete portfolio or same-existing-data adoption gates.

### Bounded MCP displaced-code retirement - agent-recommended

The next slice retires MCP's declared old executable footprints and restart
routes during fresh fallback, under the existing native installer/execution
owner. Control reset alone does not establish that old code cannot run again.
Review this proposal before implementation. It authorizes no concrete host
cleanup, rollout, maintenance hold or protected-session action.

Keep the completed, health-gated selected candidate and its retention custody.
The attached constructor currently retains healthy predecessor current/LKG
references as well as that candidate until integration ends. Hand off only
its own predecessor retention before admitting their removal; do not release
the candidate, break another owner's lease or use caller death as permission.
Keep this cooperation in the same finite constructor and paired shell
adapters, not a second installer, supervisor or permission receipt.

Append the next maintained footprint revision after published revision 5.
Declare finite displaced immutable generation/snapshot classes, legacy
`.venv`/`venv` code and exact known vendored tool/helper entries. Preserve the
selected candidate and aliases it actually needs, configuration, databases,
materialized fleets, logs, unknown content and protected coordination.
Known disposable classes may be removed without an old deployment receipt;
unknown incremental compatibility must not invalidate their reset inventory.
Foreign/redirected scope, mutable-dev ownership or unconfirmed consumers
requires a precise preserved/incomplete outcome, not arbitrary reclamation.
Do not recursively clear the runtime root or follow a removed alias's target.

Reuse canonical generation construction/retention and selector-publication
custody in its existing lock order. Revalidate the finite inventory, actual
current/LKG selection and native process scope under custody before effects.
Activation's best-effort LKG write is not proof that no selector still names
old code. Preserve current/protected/excluded consumers and all independent
construction/retention owners; confirm that displaced native owners remain
settled without extending the reaper's authority. Contention or an unsafe
recognized artifact leaves retirement incomplete, never a successful fresh
installation. Ordinary age-based, best-effort GC is not this mandatory step.

Publish or validate the selected generated global commands/resolvers through
their existing command-lifecycle owner, and retire declared stale selectors
and restart routes without removing current projections or durable operation
history. MCP declares no service registration today; do not invent one.
Whole-uninstall is unsuitable after activation because it also removes the
new candidate, current globals and deployment metadata.

Publish the final deployment record only after fresh effect retirement and
global integration succeed. Interrupted retirement/publication must remain
fresh on retry and report the actual selected/build/serving state, not claim
an untouched predecessor, silent old code or an automatic safe rollback.
Verify absence of every declared displaced executable/restart route and actual
selected CLI/on-demand service health before reporting this bounded slice done.

Qualification must exercise the actual attached retention handoff, both native
Windows/Linux fresh adapters and the unchanged incremental path. Cover missing
or corrupt old metadata; finite old generations/snapshots and legacy venvs;
exact alias removal without following targets; selection/inventory races;
caller loss and interrupted deletion/publication; competing construction,
retention or publication owners; and byte preservation for the selected
candidate, configuration/data, foreign/unknown and protected scope. Prove that
declared retired code/routes are absent and cannot be invoked through their
old entries, while selected commands and on-demand serving still work.
Keep macOS, the complete manifest portfolio, assembled public entry points,
same-existing-data adoption and live all-code-absent session/connectivity
survival as separate pending parent gates.

## Implementation sequence - agent-recommended

1. Review and merge this revision of the existing vision and effort before
   changing orchestration. Reconcile the older normal/repair admission prose
   and preserve #7758, maintenance, removal and connectivity ownership.
2. Introduce the smallest shared footprint schema, historical delta format and
   producer-completeness validation in the canonical installer owner. Start
   with one complete installed owner, including its non-file effects, then
   extend the declared suite/Manager portfolio without claiming early parity.
3. Route global update interception and upstream bootstrap through one
   override/target resolver. Preserve diagnostics and read-only operations,
   independent source choices, exit/input semantics and standalone installation.
4. Integrate both incremental and fresh plans with the same execution driver
   and existing native cleanup/cutover primitives. Retire receipt-as-permission
   branches only with tests for their fresh replacement behavior.
5. Qualify the complete installed inventory and actual same-data/native
   consumers. Keep isolated fixtures, released source, authorized rollout and
   live survival evidence distinct.

## Validation plan

- Exercise the actual global project binstub through Manager/bootstrap with a
  missing/broken old runtime. Check default upstream Main, explicit environment
  override, existing local Dev/source/cell/test selections, precedence,
  malformed overrides, exact pinned input and independent source choices.
- Run cold, recognized-current, historical-compatible, incompatible,
  marker-missing and marker-corrupt fixtures. Missing reuse evidence must
  reach fresh installation, not fail at an old-receipt gate.
- Capture artifacts and registration/process effects from the real installer
  producers on supported platforms. Make an added/changed/removed footprint
  without its manifest/delta update fail the contract check; cover historical
  transitions and supported contract ranges rather than testing JSON shape alone.
- Prove exact disposable reset scope and byte/data continuity for configuration,
  databases, unrelated/foreign content and protected sessions/connectivity.
  Include path indirection, edited global commands and unknown artifacts.
- Observe real identity-bound old-process/child retirement and scheduled,
  startup and maintenance reactivation prevention. A successful new marker
  alone does not establish old-version silence.
- Exercise concurrent callers, active and abandoned installers, caller loss,
  corrupt control records, interrupted native effects and same-boot recovery
  through the existing driver. Prove one actual writer, retained pending/failure
  history, precise failures and no replay/alternate-root workaround.
- Qualify incremental/ZDD and fresh fallback independently on native Windows,
  Linux and supported macOS shells, then their assembled public entry point.
  The parent same-global-installation, all-tool/Manager transition and
  all-code-absent session/SSH/Gateway survival remain separately authorized
  live acceptance gates.
