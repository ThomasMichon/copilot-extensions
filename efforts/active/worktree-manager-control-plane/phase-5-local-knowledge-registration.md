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
operation must explicitly omit that pin: its only mutation is registration.

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

1. Require a native stateless harness that needs an external knowledge binding
   and is currently unbound. Preserve a valid local or inherited choice.
   Malformed/unreadable binding or composition is an explicit refusal.
2. Resolve and validate the chosen path as an actual local Git root, including
   supported bare anchors. Refuse a descendant, linked task worktree,
   harness self-selection or an unavailable/ambiguous path.
3. Read origin and local repository identity through sanitized, noninteractive,
   caller-owned Git reads. No fetch, `ls-remote`, hook or authentication helper
   is needed. A local-only checkout is supported; provider identity and landing
   policy remain unobserved, not inferred from that absence.
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

Return the actual persisted registration and the supported next step: refresh
and select the existing binding action. Do not bind automatically or report
New-ready. A lost result requires readback through the ordinary catalog before
retry; an already-present registration is not permission to overwrite it.
Preserve write/cleanup failure and uncertain completion instead of replaying.

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
  registry entry and normal owned lock sidecars may change. Specifically prove
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

The implementation reuses the native compare-before-replace registry
transaction directly. It preserves unknown registry metadata and never calls
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
Only the new registry entry and the two normal owned lock sidecars change.
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
with their original cleanup reserves and the existing registry lock. Each of
the three local Git reads has its own four-second cap within that aggregate.
The catalog adds no Git/provider read for this action. No deadline, liveness
assertion, process containment or source-safety check is relaxed.

These are contained source and fresh-private-home consumer results, not an
installed desktop, complete bootstrap, first-restarted model, provider consent
or receiving result. No installer behavior changes; a full fresh-OS provisioning
lane and live external operations are not run under this source-only slice.
Review, required CI and independently authorized rollout remain separate.
