# Guarded retained installer lifecycle

Back to the [effort](README.md). **Prospective, agent-recommended design.**
Requires the complete [Bridge custody specialization](custody-contract.md).
No command or lifecycle effect is authorized by this proposal.

## Owner and proposed source surfaces

The current installer/controller owns restoration. The retained payload is a
verified input and executable target, never the installer/controller to execute.
Do not select old code and invoke its `deploy` implementation.

| Surface | Owned implementation slice |
|---------|----------------------------|
| `plugins/agent-bridge/scripts/retained_runtime.py` | Strict capture/inspect/record validation, pinned record construction and invocation-scoped retention coordination. |
| `plugins/agent-bridge/scripts/retained_runtime_controller.py` | Current-controller adapter composing qualified Bridge custody, retained executable/target version, publication proofs and lifecycle result readback. |
| `plugins/agent-bridge/scripts/install.sh`, `install.ps1` | Dedicated restore argument/dispatch branches and strict wrapper/platform publication helpers. Never alias restore to ordinary update. |
| `plugins/agent-bridge/src/agent_bridge/venue_cli.py`, `serving_reconcile.py`, `serving_candidate.py` | Explicit internal retained-target injection; keep current-controller native/route checks, expected target identity and non-forcing cleanup. Preserve ordinary deploy defaults. |
| Bridge state compatibility, passive runtime and lifecycle owners | Target declaration and actual qualified passive preparation, as assigned in [custody-contract.md](custody-contract.md). |
| Existing canonical installation-context, versioned-runtime and installer-engine primitives | Reuse receipt validation, management/publication custody, `retention_custody`, `CopyPublication` and owned recovery. Any necessary shared change is a separately reviewed, tightly coupled integration dependency, never an edit to installed/vendored copies alone. |

Use existing generic owner APIs, not private installation paths or an ambient
same-named command. Proposed helpers do not introduce a resident updater,
parallel scheduler or independent lifecycle registry.

## Immutable record and installation admission

Bind a strict versioned record to canonical installation/source identity,
execution environment/principal, namespace/install/activation receipt
generations where applicable, exact manifest bytes, payload assembly,
completed slot and explicit target executable/state contract.
Keep source and marketplace identities distinct:

- Marketplace retention requires attributable immutable snapshot/provenance,
  matching plugin/package/completion evidence and unchanged payload/slot bytes.
  A replaceable installed-plugin path alone is not immutable provenance.
- Prepared source retention requires the canonical source receipt, exact
  commit/tree/assembly/runtime-generation association and validated materialized
  bytes. Raw or dirty checkouts, editable installs and source receipts borrowed
  from another assembly refuse; do not mislabel them as marketplace artifacts.
- Validate legacy installation ownership explicitly through existing mode/source
  authority. Root-path equality alone cannot substitute for provenance. Missing,
  retired, ambiguous or foreign legacy ownership is unsupported, not a new root
  to initialize.
- Record/payload/slot/receipt paths must be ordinary and canonically contained.
  Reject linked/reparse metadata, hard-linked mutable records, unsafe parents,
  duplicate/unknown JSON fields, stale generations and changed physical identity.
  Executable indirection or dependencies outside the assembly need actual
  immutable native-image qualification or an unsupported result; hashing a
  symlink's target text does not verify the executing image.

Use `validate_context_receipt`, slot ownership/completion validators and
`runtime_publication_lease`/`borrow_runtime_publication` for the modes they
actually support. No synthetic owner flag bridges an unsupported mode.
Validate target state compatibility through the target-owned declaration and
the current closed inventory, not a copied manifest claim.

Capture/inspect prove only their stated evidence. They do not grant restoration,
freeze mutable durable state, qualify historical captures or assert native
platform support. Restoration selects compatible code against current state;
it never copies a captured database over the current ledger or rewinds epochs.

## Serialization and continuing retention custody

Resolve source/governance with current-controller code. Follow existing
installation lock order: namespaced management locks where applicable, installer
transaction serialization, then short publication/Bridge owner critical sections.
Borrow an already held management owner through its existing ticket validation;
never release its locks or recursively reacquire them under another identity.

Acquire canonical `retention_custody(root, version, payload_hash=...)` for every
rollback/target slot whose lifecycle can still depend on it. Revalidate after
acquisition. Pin the full validated record in an owned private, non-linked file
before child launch; the original caller file must not redirect the child to
another unleased generation. Pin current-controller provenance separately from
retained-target provenance.

Before any lifecycle effects, establish an invocation-scoped continuing
custodian through the current controller/native launch owner. It holds real
retention custody independently of the caller shell, acknowledges its exact
transition/slot/native identity and survives the relevant caller Job/process
cleanup. It is not a new general service or a time-based lease file.
On POSIX, use the canonical borrowed-descriptor protocol where supported;
on Windows, establish an independently held native lease and accepted owner
before release. Do not assume a PID/environment marker proves either transfer.

An unsettled child result keeps the custodian, leases and transition evidence.
A successor first acquires and verifies its own real custody, accepts the
original obligation, then permits predecessor release. No timeout, tool return,
owner-label change or failed cleanup releases unresolved retention automatically.
Observation must report the current custodian and recovery action.

Unexpected custodian death is not successful handoff. Persist recovery-needed
state and require owner re-establishment before another lifecycle attempt.
An existing GC path must preserve slots still demanded by that unresolved
transition even when a crash releases every kernel handle; otherwise this
platform/path is unqualified. Implement any necessary protection through the
canonical retention/cleanup owner, not a second GC or a claim that a file is a
surviving kernel lease. The exact shared primitive change, if required, needs
its owner's reviewed integration before positive restoration can be enabled.

## Convergence ordering

1. **Read-only preflight.** Validate immutable record, current controller,
   installation, target contract, participant coverage, current route/native
   predecessor and ownership of every prospective publication destination.
   Unsupported/forwarded/protected/foreign owners refuse before closure or
   publication. Do not generate credentials, repair stale cutovers or start an
   inactive installation to manufacture a predecessor.
2. **Acquire and close.** Hold lifecycle serialization and continuing retention
   custody; pin input; close Bridge admission through its existing DB owner;
   settle/reconcile or accept qualified custody transfers. Verify strict
   inventory and actual target compatibility again under the closed epoch.
3. **Stage only owned artifacts.** Stage wrappers/resolvers, payload redirects,
   pinned service/supervisor configuration, selectors and manifest with existing
   created-object/publication proofs and exact prior-state evidence. Reject
   foreign edits before the first replacement. Every launcher that could race
   the transition must honor its epoch; an unsupported old launcher blocks.
4. **Prepare retained target passively.** Current controller explicitly launches
   the retained executable and verifies actual worker/native birth, target
   version/assembly, installation and passive compatibility. No rebuild,
   dependency resolution, migration, shared-resource adoption or old-controller
   execution. Qualified passive preparation cannot dequeue or start work.
5. **Commit through existing route/native owners.** Recheck predecessor,
   admission certificate, target and staged-output ownership at publication.
   Compose routing promotion, qualified resource adoption and existing
   host/native handoff without overlapping exclusive writers. Any temporary
   cross-artifact inconsistency must be explicitly gated by the transition;
   no caller may bypass it through a stale wrapper or native supervisor.
6. **Finish native and installation convergence.** Retire only the exact
   predecessor after the qualified non-forcing boundary and native exit
   confirmation. Finish owned wrapper/platform/selector publication and verify
   actual platform readback. Publish the success manifest only after the
   complete set and serving target agree. Capture original provenance as
   evidence, but do not copy an old success timestamp as the new outcome.
7. **Settle and release.** Recheck actual serving route, executable/assembly,
   readiness, platform integration, manifest, participant custody and absence of
   unresolved publication. Destination admission opens only under the accepted
   new epoch and compatibility contract. Close the transition and release
   custody only after complete settlement.

This is a journaled multi-owner transaction, not an atomic rename of the whole
installation. Exact interleavings with existing promotion/resource owners must
be reviewed and exercised before enabling the path. Do not retire the
predecessor merely to bypass a failed prerequisite.

## Platform obligations and partial failure

For Linux, validate the attributed user unit and effective pinned target, retain
enablement/mask policy, publish only owned bytes and check daemon-reload/readback
results. Do not silently enable a disabled unit, start it or fall back to direct
launch. No-systemd layouts need their own qualified existing launch owner, not a
success-shaped skip.

For Windows, validate task action, working directory, principal/logon mode and
the owned supervisor/resolver chain. Preserve an existing version-stable task
where compatible; reconcile owned supervisor bytes and prove effective target
resolution. Missing/foreign tasks, protected S4U changes or insufficient rights
refuse rather than re-registering, elevating or running a task as recovery.
Headless launch and actual Job-cleanup survival require native evidence.

A failure before effects reports unchanged state only if readback proves it.
After closure or publication, report closed/blocked, compensated, partial or
recovery-needed with exact owned artifacts and continuing custody. Use existing
ownership-checked compensation; never overwrite a foreign replacement.
Published candidates that may have accepted work need qualified settlement
before cleanup. Preserve pre-existing drains and all original failure evidence.
No force retirement, stop/start fallback, history deletion, slot rebuild or GC
is an acceptable substitute for restoration.
