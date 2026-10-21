# Managed runtime deployment - implementation boundaries

Parent: [Source-Managed Runtime Installs](README.md).

This is the parent effort's Phase 3 implementation and validation map.
The reviewed request separates runtime publication from Copilot plugin delivery;
it does not disable plugin hooks or make Manager a runtime dependency of
standalone plugins. No policy is installed or enabled by this plan.

## Preserve the existing owners

Installation-mode governance owns legacy versus namespaced selection,
activation and transfer. Command binding owns source-qualified installation
identity. Canonical runtime construction/publication owns immutable candidates,
redirects and rollback. Manager orchestrates an explicitly selected install;
the [maintenance slice](maintenance.md) owns recurring invocation.

Managed deployment is an additional authority decision at those existing
publication boundaries, not another installation mode, source of actual-root
truth, supervisor or installer. The temporary bootstrap killswitch is not this
policy: it covers a different trigger and legacy scope.

The concrete reading path is the
[install contract](../../../docs/install-contract.md#installation-mode-governance),
[payload invocation contract](../../../libs/payload-invocation/README.md) and
[runtime primitive](../../../libs/versioned-runtime/README.md).

## Required behavior

| Situation | Required result |
|-----------|-----------------|
| No managed-deployment opt-in | Preserve ordinary standalone bootstrap, first-use and explicit installer behavior. |
| Managed installation, healthy compatible runtime | Plugin hooks and commands continue using its selected installed generation; plugin-version drift does not copy payloads, construct slots or replace redirects. |
| Managed installation, runtime absent or incompatible | Return a precise installation-owned readiness/recovery action without silently provisioning from the plugin payload. Non-deployment hook behavior still executes. |
| Explicit Manager-owned update | Pass the selected scope and pinned source through the same canonical installer, ownership, readiness, activation and retention path. An environment-variable hint alone is not deployment authority. |
| Invalid or unsupported deployment policy | Refuse new publication explicitly rather than falling back to self-provisioning; preserve validated installed operation and independent hook behavior where their contracts allow it. |
| Another source, marketplace or installation | Preserve foreign ownership. Managed deployment never authorizes an ambient same-named command, root or source. |

The gate covers both session bootstrap and indirect first use, including
deferred stamping, native installer helpers and generated payload invocation.
Checking only the session-start entry point is insufficient. Conversely,
returning early from an entire hook or extension can suppress guidance,
registration or other non-deployment behavior the operator explicitly retained.

## Implementation seams - agent-recommended

- Resolve policy against the actual installation and existing source/plugin
  identity before runtime or redirect writes. Preserve explicit opt-out,
  malformed-policy and absent-policy distinctions; do not repurpose the binary
  installation-mode policy.
- Trace one complete bootstrap-to-publication and first-use path before
  partitioning native adopter changes. Put shared decisions in the owning
  canonical helper rather than maintaining independent per-plugin policy
  parsers.
- Keep plugin-content invocation identity distinct from runtime artifact source.
  A marketplace adapter must be able to reach its explicitly managed runtime
  without requiring identical delivery origins, while existing command-binding
  and installation-transfer checks still reject foreign owners. Do not remove
  source-fingerprint checks as a shortcut.
- Preserve thin adapters and declared capability/version-skew readiness.
  Neither a successful handshake nor a compatibility failure grants runtime
  deployment permission.
- Keep current role selection, deferred construction, transaction locks,
  candidate health and failure preservation intact. Policy changes do not
  authorize starting unused services, resetting source or rewriting live slots.

## First implementation trace - Worktrees

Pre-implementation evidence at `600c0efcae`; these are integration points,
not a claim that a deployment gate already exists.

| Path | Publication or retained behavior |
|------|----------------------------------|
| `plugins/agent-worktrees/scripts/hook_client.py` | Writes command-catalog/session guidance before lifecycle work. Native first install may invoke `command_binding.py --deferred-runtime --run-lifecycle` directly, bypassing `bootstrap-check.ps1`. |
| `plugins/agent-worktrees/scripts/bootstrap-check.ps1` | First install stamps a snapshot/redirect. The older installed lib-copy reconcile branch directly replaces package files, build metadata and deployment manifest, outside the runtime slot publisher. |
| `plugins/agent-worktrees/scripts/invoke-payload-runtime.ps1` | Resolves a healthy runtime before provisioning. A miss commits to provisioning before root/lock creation, then invokes namespaced install or legacy stamp plus snapshot provision. |
| `plugins/agent-worktrees/scripts/install.ps1` | Legacy lifecycle admission precedes staging. Deferred stamp still publishes snapshots, payload pointers and global redirects; it is not a read-only action. |
| `libs/payload-invocation/command_lifecycle.py` | Validates live borrowed tickets and holds source-qualified ownership through native mutation. Managed deployment must compose with this admission, not waive its source checks. |

The Python hook's legacy fallback separately invokes project hooks, session
registration, nudges and hygiene checks alongside deployment-capable bootstrap
and provision checks. A gate must preserve those independent behaviors and the
guidance writer. Gating only the bootstrap script, whole hook, slot completion
helper or environment-variable self-provisioning switch misses at least one
of the traced boundaries.

## Shared decision under implementation

The canonical installation-context helper now has a read-only
`resolve_runtime_deployment` entry point and `deployment-status` diagnostic
action. Native parity and publication-path callers are being integrated; the
presence of this helper alone is not enforcement.

Its installation-local `runtime-deployment.json` record lives at the actual
runtime root returned by existing mode governance. It does not choose a root,
change installation mode, select runtime source, activate a generation or grant
an explicit update authorization. A record has exact `schema`, `version`,
`owner` and `managed` properties. Version 1 uses schema
`copilot-extensions.runtime-deployment`; `owner` matches the existing
source fingerprint, plugin, mode, runtime root and environment tuple.

Absent policy retains standalone behavior. A matching `managed: false` is an
explicit standalone choice; `managed: true` denies plugin-triggered publication.
Unreadable, malformed, linked, unsupported or foreign records deny publication
with an explicit diagnostic and recovery action. Policy permission is only one
conjunct: callers still owe installation governance, command ownership,
construction and activation admission. No environment boolean grants permission.

This root-local record keeps scope tied to the already selected installation,
including a first installation that has only its policy present. It is not a
second registry of runtime roots or an extension of the binary mode policy.
Explicit management will compose with the existing source-qualified lifecycle
lease rather than treating this read-only decision as a management permit.

## Reconciler caller integration

The first caller slice gates each runtime-needed `build_plan` action after
marketplace payload decisions and the existing monotonic version guard.
It uses that plugin's governed root and owning helper, not the Worktrees
installation's policy as a fleet-wide switch. `apply_plan` repeats the check
immediately before default or custom execution and rejects a changed
payload/root/receipt. An older standalone helper remains usable when there
is no policy; a present policy without capable canonical support refuses
publication with a diagnostic.

Real helper fixtures cover legacy policy states, independent targets,
policy changes between preview and execution, active-cell versus legacy-root
scope, healthy-runtime reuse, unsupported helpers and unauthenticated hints.
Existing unsupported cell-installer readiness remains independent of deployment
permission; the policy does not invent an installer capability.

This is not a completed publication boundary. Native installers, launcher-run
plans, session bootstrap and indirect first use still need their own admission
at the actual mutation seam, including explicit management authorization.
Those remaining paths and positive retained-hook assertions keep Phase 3 open.

### Launcher producer admission

Worktrees' `pre-launch` planner checks its actual governed installation before
emitting a stale-runtime installer action. Allowed actions carry the runtime
phase and owning payload/root/context metadata used by execution admission.
Other repository bootstrap services retain their existing independent actions.
Already-current runtimes do not require publication permission.

Background staging continues marketplace refresh and fingerprinting in managed
mode. Only the runtime-application request is suppressed, with the canonical
reason and recovery diagnostic retained in its status. Payload changes and
venv-version drift are checked independently; drift repair cannot override a
deployment refusal. These checks prepare new plans, not authorize execution of
old ones. Native launcher consumers must still revalidate cached actions at
execution; this producer slice does not close that boundary.

## Source-qualified lifecycle admission

The shared legacy lifecycle now composes the canonical deployment decision
before lock/reservation writes, after acquiring the existing lock and whenever
a live lease asserts ownership. Direct manifest publication performs the same
admission even without an enclosing installer. Worktrees' existing native
legacy admission therefore refuses managed `install`, `update`, `provision`
and deferred `stamp` before self-staging.

An explicit management caller requests `--managed-deployment` on
`--run-lifecycle`. The existing lock owner records its immutable purpose;
borrowed native children authenticate that purpose through the live
source-qualified token rather than trusting an environment boolean. A borrowed
plugin lease cannot become management, invalid/unsupported/foreign deployment
policy remains blocking, and teardown cannot become publication.

This slice does not wire Manager source selection or installation-cell installer
admission. Their actual mutation gates remain required.

## Cell installer and retained-backend admission

Worktrees' native cell installers now consult the canonical deployment decision
after validating the context/root/generations and before self-staging. They
repeat admission before runtime-input writes. Status remains a non-publication
operation rather than requiring a deployment permit.

The Python lifecycle makes the same decision at entry and each governance
recheck, including the existing genesis/install lock boundary, candidate
handover and helper callback validation. A direct retained-backend invocation
does not bypass policy. Retained bootstraps include the canonical command-binding
and lifecycle companions so legacy management can authenticate its existing live
publication-purpose lease without importing source code.

Explicit managed-cell installation now composes the canonical
[Python publication custody API](../../../libs/installation-context/README.md#explicit-runtime-publication-custody-python).
Worktrees' native staging, backend construction, retained candidate handover
and native callbacks authenticate and borrow the same live genesis/install
owners. A context receipt or staging environment flag alone remains
insufficient; namespaced installs are not coerced into legacy command binding.
Management purpose pins the original native process birth as well as its PID;
an exited, unreaped or reused process cannot retain publication authority.
Standalone and managed flows acquire namespace custody before service cutover.
Explicit modes retain the same governance owner through nested settlement and
release cutover before releasing the namespace, avoiding an overlap cycle with
an outer management owner.
Settlement revalidates after blocking cutover acquisition and preparation,
before helper or selector publication, including callers without a native
frontend. A wait cannot extend an expired management owner's authority.
Manager source selection and other caller/launcher gates remain open.

## Native bootstrap and early first-use admission

Worktrees' native bootstrap now probes its own invoked payload's canonical
installation helper before deferred stamping or the older lib-copy replacement.
It does not use the old deployment manifest's source hint as policy authority
or wrap that old manifest in a newer command-binding schema. Older deployed
hooks without the native helper retain standalone behavior only when no
deployment policy exists; a present policy requires capable explicit management.
The gate is outside the legacy reconciliation error handler, so refusal and
recovery diagnostics cannot disappear into that compatibility path.

The payload-local first-use dispatchers retain their healthy fast path. A
runtime miss probes the actual governed root before committing to provisioning,
boot-trace writes, root creation or lock creation. It repeats the probe under
the existing provision lock after checking for a now-available runtime. Both
native adapters validate the decision's type and governed policy path, including
canonical home aliases and active-cell versus legacy-root scope.

An unattributed standalone payload can have a null mode/root in the canonical
diagnostic result. Bootstrap composes `probe-legacy` admission rather than
accepting all null modes; first use retains its already-admitted simple legacy
fallback and uses `legacy.root`. Explicit mode policy, ownership tombstones and
receipt safeguards are not relaxed.

The bespoke resolver's `read-only` trace mode lets bootstrap inspect a selected
runtime without writing diagnostics into the installation. First-use resolution
uses `ready` trace mode: it buffers durable resolver events and emits them only
after a complete selected interpreter is found. Missing-runtime refusal
therefore leaves even an existing policy-only root unchanged, while healthy
dispatch retains its marker/slot trace phases. Normal resolver callers keep
their existing tracing behavior.

Focused native regressions exercise real policy adapters and inventory all
product writes on refusal. They separately preserve standalone first stamping
and legacy lib-copy replacement, healthy dispatch, and policy changes at the
held provision lock. A hook-composition regression runs the independent
project, registration, nudge and hygiene siblings even when the bootstrap
component returns a deployment refusal. These fixtures do not claim the
remaining assembled-use, installer-cell, launcher-plan or version-skew gates.

## Validation before claiming Phase 3 complete

- [ ] Exercise the real session-bootstrap and indirect first-use paths with
  absent, standalone, managed, malformed and unsupported policy on both native
  platforms, scoped to the addressed installation.
- [ ] Inventory runtime slots, selected/last-known-good markers, snapshots,
  redirects and deployment manifests before and after managed plugin actions;
  assert no payload publication or redirect replacement.
- [ ] Assert retained non-deployment hook behavior positively, not merely a
  successful process exit or absence of writes.
- [ ] Verify a healthy selected runtime remains callable and a missing or
  incompatible runtime returns the declared readiness outcome without repair.
- [ ] Prove explicit Manager installation still performs the canonical update,
  and an unauthenticated caller hint cannot bypass the gate.
- [ ] Cover source-qualified command binding and foreign ownership negatives
  for both legacy and namespaced contexts without weakening current governance.
- [ ] Exercise newer-plugin/older-runtime and older-plugin/newer-runtime
  combinations at declared capability boundaries, then repeat assembled-use
  and native readiness evidence required by the parent effort.
