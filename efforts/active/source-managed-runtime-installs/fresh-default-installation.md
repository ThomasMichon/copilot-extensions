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
