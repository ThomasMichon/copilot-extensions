# Phase 5 - Confirmed local knowledge registration

- **Status:** Contract reviewed in #11920; native and Configuration source qualification in progress.
- **Parent:** [Configuration workbench](phase-5-configuration-workbench.md).
- **Coordination:** #1419 Track 3; bounded source slice `shiny-actuator`.
- **Vision:** [Knowledge selection](../../../visions/installer/configurator/README.md#knowledge-selection-and-portable-defaults).

## Outcome and existing boundary

Let an unbound harness user register an already-existing local knowledge
checkout from Repositories Configuration, then use the existing separately
confirmed binding action. This closes part of the reviewed local-selection
intent; it is not repository creation, acquisition or a new binding engine.

The [current native contribution](../../../plugins/agent-worktrees/docs/repositories-configuration.md)
already binds a present canonical knowledge/worktree registration through
`harness-knowledge`. Its existing preview/apply transaction must retain that
promise. The separate binding skill can register local checkouts, but Manager
does not yet offer that step when no companion is registered.

The native registry remains the only registration authority. Extend its
existing registration writer and serialization, not Manager-side YAML or a
second inventory. Ordinary `repos add` retains its existing behavior, including
its separately documented best-effort Git credential pin. This new confirmed
operation must explicitly omit that pin. The only new policy/state is the
registration; its existing owner must also withdraw and republish the derived
protection facts, rather than leave previously prepared callers falsely complete.

The forced project-config preservation repair #7611 and installed-lifecycle
qualification #11864 are independent. This slice does not edit their writers,
fixtures or claims, and does not take #1419's retrofit/overlay/cleanup tracks.

## Native contract

Add distinct `repos configuration knowledge-register-preview` and
`knowledge-register-apply` operations. Both require the explicit context project,
chosen new repository name and absolute existing checkout path. Accept an
explicit default-branch name and optional nonsecret account mapping as
registration metadata; neither is an observed server-policy or credential result.
Do not silently treat the current feature branch as the remote default.

Use the existing version-1 Configuration preview envelope. Its stable target
is the selected project's local-knowledge-registration action, not an arbitrary
resource chosen by a returned label. Bind its token to normalized field values,
the exact native installation/machine/project, current registry bytes, selected
harness/binding inputs and the local Git identity inspected below.

Preview and catalog are bounded reads with no lock/state-directory creation,
network call, credential inspection or Git configuration write:

1. Require a canonically registered worktree-class native stateless harness
   that needs an external knowledge binding and is currently unbound. Preserve
   a valid local or inherited choice.
   Malformed/unreadable binding or composition is an explicit refusal.
2. Resolve and validate the chosen path as an actual local Git root, including
   direct and converted-clone bare anchors. Refuse a descendant, linked task worktree,
   harness self-selection or an unavailable/ambiguous path.
3. Read origin and local repository identity through sanitized, noninteractive,
   caller-owned Git reads. No fetch, `ls-remote`, hook or authentication helper
   is needed. A local-only checkout is supported; provider identity and landing
   policy remain unobserved, not inferred from that absence.
   Resolve included Git configuration and effective origin rewrites consistently
   with the binding owner before URL-safety and identity validation.
4. Require the exact new name and checkout to be unregistered. Refuse canonical
   name/case, local-path and provider-identity collisions rather than replacing
   or reclassifying an existing entry. Existing registrations use their normal
   existing-binding/repair owners.
5. Show name, canonical checkout, knowledge class, default-branch/account
   metadata and all effects. Registration neither proves account usability nor
   authorizes a push. Missing/invalid metadata refuses before the write.

Apply requires explicit confirmation and a fresh token. Under the existing
bounded registry lock, re-read every relevant input and refuse a changed
proposal before invoking the owner registration writer. Persist only the new
`class: knowledge`, non-agent local registration and its reviewed metadata,
preserving all unrelated registry content. Do not write a project config,
credential pin/helper, binding pointer, plugin overlay or repository source.
Use the existing guarded managed-write boundary for derived protection maps.
Withdraw prepared views before persistence and refresh their actual coverage
afterward. Bound every publication-authority, registry and publication lock by
the same remaining execution deadline, preserving the cleanup reserve.
Committed-policy inspection, revalidation and guard-fact construction use that
deadline too; neither an independent probe budget nor an earlier cached snapshot
may replace it.

Return the actual persisted registration and the supported next step: refresh
and select the existing binding action. Do not bind automatically or report
New-ready. A lost result requires readback through the ordinary catalog before
retry; an already-present registration is not permission to overwrite it.
Preserve write/cleanup failure and uncertain completion instead of replaying.
Post-persistence publication failure is nonzero/incomplete with an explicit
committed registration; unknown persistence remains unknown. Never report a
failed publication as registration rollback or healthy guard coverage.

## Manager composition and compatibility

Reuse version-1 rows, forms, provider preview, explicit confirmation, existing
owned progress/result screens and originating-scope refresh. No new modal,
daemon, catalog, secret field or Manager import of the native domain package.

The unchanged native `catalog` operation supplies an optional registration
action row only when this executing owner supports the operation and the
selected harness is eligible. A new row marker enables only its explicit
manifest action; ordinary repository rows do not acquire a registration action.
This additive owner response, not payload/package version or action spelling,
establishes optional support.

An older provider's catalog lacks the row/marker, so a newer manifest must not
emit new syntax. Older manifests retain their bind-existing behavior. An old
Manager that cannot execute provider previews supplies no valid token and
apply must refuse before effects. Provider rollback hides only this optional
operation; preferences, inspection and existing binding remain usable.

## Dependency-ordered delivery and validation

- [x] Review this owner contract before adding executable caller syntax.
- [x] Implement the standalone native preview/apply and registration-only
  transaction, preserving all ordinary registration behavior.
- [x] Connect the existing Configuration contribution conditionally; prove
  collected values, preview, cancel, confirmation, result and refresh through
  the real native CLI boundary.
- [x] Cover ordinary and bare local repositories, local-only/provider origins,
  explicit metadata, malformed paths/configuration/registry, self/descendant/
  task-worktree selection, name/path/identity collisions and post-preview
  drift. A lock waiter must recheck the winning state.
- [x] Compare complete fixture Git/config/source trees before/after browse,
  preview, cancel, refused apply and successful registration. Only the exact
  registry entry, normal owned lock sidecars and canonical derived guard facts
  may change. Specifically prove
  no credential pin, branch/ref, binding, overlay or worktree mutation.
- [x] Prove ordinary registration still pins when appropriate; new/old
  provider/manifest/Manager and supported rollback paths retain their promised
  behavior. Keep genuine failure/cleanup outcomes and fixed aggregate budgets.
- [ ] Run required source checks, review and CI before merge. Record native
  fixtures, first-touch private-home evidence, installed UI and separately
  authorized receiving independently.

## Retained parent gates

This is one local-registration partial under #1419 KO1/KO2, not their complete
acceptance. Remote clone, confirmed repository creation/protection, actual first
restarted model/decline, shared provider landing-policy preflight/recovery,
general existing-task pairing and cleanup remain with their current owners.
No live registration, binding, authentication, installation, service operation,
rollout or resource allocation is authorized by this proposal.

## Source qualification

The implementation reuses guarded managed publication around the native
compare-before-replace registry transaction. It preserves unknown registry metadata and never calls
ordinary registration's credential-pinning follow-up. Neither `repos add` nor
the binding payload changes. The selected catalog emits a distinct optional
action row; no registration marker appears on ordinary repository rows.

Final focused native controls pass 17 cases, including real ordinary/bare CLI
transactions, replay/old-host refusal, input drift, remote identity collision,
linked task and indirect metadata refusal, literal branch validation, shared
owned Git budget and context isolation, and a real registry-lock waiter behind
an ordinary native preference write. An additional real CLI case passes with
remote/account metadata and complete Git/config preservation.

The full Manager Repositories Configuration file passes all 23 cases. The new
production form drives the real native CLI through browse, collected values,
preview, default Cancel, confirmed apply, receipt and originating-scope refresh.
Only the new registry entry, normal owned lock sidecars and canonical derived
guard-publication artifacts may change.
The native catalog remains unbound after registration. The actual manifest
condition withholds the action from a legacy row lacking its marker.

The earlier broader native file had 146 passes and one failure in the new
concurrency test: its snapshot tried to read an actively held Windows lock.
The corrected control excludes only those exact two owned sidecars while
locked and still compares every other fixture byte. It passes in the final
focused packet; that earlier whole packet is not relabeled as a pass.

Five existing ordinary-registration pin controls also pass. Qualification
exposed two fixtures still relying on namespace-as-login inference retired by
merged #11751. They now declare their synthetic account explicitly and keep
the active-account observation synthetic. This updates the fixtures, not the
account resolver or ordinary credential-pinning implementation.

Native reads share one 18-second aggregate and apply one 26-second aggregate,
with their original cleanup reserves and existing lock owners. Each local Git
read has its own four-second cap within that aggregate: three for local-only
anchors and four when resolving an effective origin.
The catalog adds no Git/provider read for this action. No deadline, liveness
assertion, process containment or source-safety check is relaxed.

These are contained source and fresh-private-home consumer results, not an
installed desktop, complete bootstrap, first-restarted model, provider consent
or receiving result. No installer behavior changes; a full fresh-OS provisioning
lane and live external operations are not run under this source-only slice.
Review, required CI and independently authorized rollout remain separate.

## First-review corrections

The five findings on #11929 are addressed through the existing owners:

- Registration uses guarded managed publication. An already-prepared independent
  caller receives the new knowledge posture; a failed post-commit refresh leaves
  its view unusable and returns a nonzero, explicitly committed partial result.
  Pre-write publication refusal is separately structured and does not claim
  persistence.
- Catalog availability uses the same context-project binding-name check as
  preview. Generic preference inspection/editing keeps its broader name grammar.
- Physical Git metadata can be the root or its nested `.git`, supporting both
  direct bare repositories and actual clones converted to bare. Common-directory,
  root and indirection checks remain.
- Origin discovery processes Git includes and resolves the effective native
  remote URL before URL-safety and canonical-identity validation.
- One optional budget reaches publication authority, registry and publication
  locks, including nonblocking native thread/sidecar acquisition. Existing
  unbudgeted callers retain their prior behavior and lock order.

The shared native packet passes 212 cases, with one Windows-unavailable FIFO
control skipped. It includes the full guard producer and registry transaction
files plus the selected registration controls. The five real positive layout/
origin cases additionally pass with strict file-and-directory write boundaries.
Three prepared-caller controls pass for success, pre-write refusal and committed
publication failure. Contention controls hold each of the three actual locks
beyond a short execution budget and require the native future itself to finish.

The Manager file's 22 other controls pass. Its new real consumer passes after
the write-boundary assertion was corrected to distinguish absent paths from
existing empty directories; the earlier failed packet is retained as a failure.
No application assertion or deadline was relaxed.

Only the changed registry-transaction source record is refreshed in the positive
unit manifest. Node inventory, effects, coverage declarations and budgets remain
unchanged. Required exact-source unit CI and review still qualify the pushed
revision independently.

## Second-review corrections

The next review confirms all five original findings are resolved and identifies
two remaining gaps. Catalog availability and native preview/apply now require
the binding owner's exact worktree context class. The existing policy-resolution
scope accepts an optional absolute deadline, narrows an enclosing scope without
extending it, and isolates operation-local snapshots. Both native operations
and budgeted guard-fact construction pass their remaining deadline through
that scope; existing callers without the option keep their previous behavior.

The focused packet passes all 43 cases, including three unsupported context
classes, nested deadline/snapshot restoration and real caller-owned slow-policy
processes before and after registration persistence. Four earlier policy-fixture
failures occurred while staging LF files under inherited Windows conversion
settings. A fixture-local Git option now preserves the exact supplied bytes;
the failed packet is not counted as a pass and no host Git setting changes.

The affected shared packet passes 191 cases, with one Windows-unavailable FIFO
control skipped. It includes the full guard producer, native registration
controls and existing policy-budget/snapshot controls. Only the changed
policy-resolution source record is additionally refreshed in the positive unit
manifest; no node, effect, coverage or budget declaration changes.

The real Manager registration form additionally passes against this updated
native owner: Cancel performs no registration, Confirm persists it, and refresh
retains the unbound state. Scoped F/E9 lint, documentation consistency and all
13 install contracts pass. Review and exact-head CI remain pending; these
source results do not authorize or qualify an installed runtime update.
