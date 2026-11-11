# Phase 5 - Explicit personal private knowledge creation

- **Status:** Contract reviewed in #12004; receiver qualified in source,
  native provider and consumer acceptance remain open.
- **Parent:** [Configuration workbench](phase-5-configuration-workbench.md).
- **Coordination:** #1419 Track 3 KO1/KO5/KO6; bounded source slice
  `shiny-actuator`.
- **Vision:** [Knowledge selection](../../../visions/installer/configurator/README.md#knowledge-selection-and-portable-defaults).

## Outcome and existing owners

Offer a deliberate create choice when a selected harness needs a knowledge
repository and the operator chooses a new hosted repository. The native
repository/provider owner creates only an explicitly named personal, private
GitHub repository and establishes its initial default-branch deletion and
non-fast-forward safeguards. Manager presents the owner's preview, confirmation,
progress and receipt; it does not become a credential or policy owner.

This closes part of the existing knowledge-selection intent. It is not a
requirement to use GitHub, create a hosted repository or authenticate a personal
account during public foundation installation. Existing local-only,
acquire-existing, register-local and bind-existing choices remain available.
A conventional or synchronized suggestion never authorizes creation.

The [local registration](phase-5-local-knowledge-registration.md),
[provider preflight](phase-5-knowledge-provider-preflight.md) and
[checkout acquisition](phase-5-knowledge-checkout-acquisition.md) contracts keep
their current effects and confirmations. Creation does not clone, register,
bind, allocate a pair/worktree, launch or make New ready. #356/#8545 retain
acquisition/setup/trust; #10844 retains authentication presentation. Existing
first-model, retrofit, overlays, rejected-push recovery and cleanup owners keep
their complete acceptance.

## Proposed native owner boundary

Add separate `knowledge-create-preview` and `knowledge-create-apply` operations
to the existing `repos configuration` namespace. Reuse its target identity,
fresh preview token, strict parser and owned finite worker rather than widening
acquire or register. The existing proposal reader accepts only version 1,
machine-local set/reset operations and renders that scope literally; it cannot
truthfully confirm hosted creation unchanged.
Expand that same reader with a distinct version-2 remote-repository proposal,
including an explicit `create` operation and correctly rendered remote effects.
Keep version-1 machine-local semantics and parsing unchanged. Do not label
creation machine-local merely to pass the old validator or build a second modal.
The exact desired GitHub HTTPS repository identity is an explicit input;
visibility is fixed private, not an optional public default.

The native catalog advertises the optional action only from a capable executing
owner. Publish an additive `knowledge_repository_creation: 1` capability through
the existing runtime-owned repository inventory before any consumer emits the
new syntax. A newer payload cannot establish support in an older selected
runtime. Keep the existing standalone operations and missing-capability behavior.

Preview is a non-mutating observation. It identifies the selected project,
installation, machine, requested owner/name and configured account provenance;
verifies that account through the existing named GitHub CLI credential owner;
and explains the exact remote effects, limitations and supported continuation.
It performs no sign-in, credential write, repository creation, policy change,
Git download or local registration/binding write.

Selection uses the canonical registry's explicit repository/owner/default
account policy, never a publisher namespace or global active-account guess.
Require a configured selection, verify the returned identity, and require the
explicit personal namespace to be that verified user. Organization destinations
and other providers are unsupported by this bounded operation; do not infer
organization authority from a writable token or substitute another destination.
Missing or mismatched credentials remain actionable without ambient retry,
token copying, scope escalation or a global account switch.

The apply operation revalidates the proposal's project, installation, registry,
selection and requested target before effects. Confirmation defaults to Cancel.
The proposal must identify creation, the provider-created initial README/commit,
and the later default-branch safeguards as remote mutations, not merely a
machine-local configuration save.

### Creation and policy are distinct stages

Use the provider's
[authenticated-user creation endpoint](https://docs.github.com/en/rest/repos/repos#create-a-repository-for-the-authenticated-user)
with explicit private visibility and initial README creation. This establishes
the provider's initial default branch without a local commit, push or arbitrary
default-branch mutation. Qualify the exact returned repository identity,
personal owner and private visibility; obtain the actual default branch and
repository identity from the provider, not a local `main` assumption.

Never adopt, reconfigure or overwrite an already-existing repository through
this create operation. A duplicate-name response is not a successful creation
receipt. A missing/ambiguous metadata read is not proof of absence or permission
to modify an existing repository. Provider denial or creation limits do not
permit a public fallback, another account, organization, host or repository name.

After confirmed creation, inspect applicable default-branch protection through
the existing provider policy-read owner. Recognize equivalent active deletion
and non-fast-forward protection idempotently; do not duplicate or replace it.
If protection is missing, add only a
[repository branch ruleset](https://docs.github.com/en/rest/repos/rules#create-a-repository-ruleset)
for that default branch, containing `deletion` and `non_fast_forward`, active
enforcement and no bypass actors. Do not add a PR requirement, linear-history
requirement, update restriction, agent-usable bypass or all-branches condition.
Ordinary direct/fast-forward writes and independently unprotected feature
branches must remain usable under otherwise applicable repository policy.

Re-read the exact repository and effective rules before reporting safeguards
verified. A disabled/evaluate rule, bypassable exception, partial page, denied
policy read, branch/identity drift or unsupported private-repository plan is not
protection proof. Do not upgrade an account's plan or weaken the safeguard to
make the action succeed. Existing/adopted repositories receive inspection and
recommendations through their existing owner, not automatic policy mutation.

### Partial effects, cancellation and recovery

Creation and protection are not an atomic provider transaction. Keep selected
target, stage, observed repository/default branch, whether creation is confirmed
or uncertain, and separately observed safeguard status in a sanitized receipt.
Report complete creation only after exact private identity and required
protections are verified; never equate a successful POST or exit zero with
protected, registered, bound or task-ready.

Make at most one creation request per apply attempt. If a timeout, cancellation,
lost/malformed response or unconfirmed cleanup can follow acceptance, report an
unknown outcome and stop. Do not automatically replay creation, rename the
request, delete the repository, lower visibility/privacy, or roll back policy.
The same applies independently to an uncertain protection mutation.
Read-only inspection is the supported first recovery step; a confirmed created
repository and a missing safeguard remain distinct facts. Any subsequent
protection repair needs its own current observation and explicit confirmation,
not replay of the create action or takeover of an unrelated repository.
Deliver that bounded continuation as separate `knowledge-protect-preview` and
`knowledge-protect-apply` owner operations for the confirmed newly created
repository identity. Use the same version-2 remote scope with a
`protect-default` operation. Its receipt reference locates prior effects, not
authorization: independently verify the current account, repository identity,
private visibility, default branch and policy before a new confirmation.
Unknown prior creation must first be reconciled read-only, not promoted by a
caller-supplied receipt into permission to reconfigure a different repository.

All provider children, policy pages and final verification share one finite
aggregate deadline and existing owned cleanup. Bound captured output and
pagination; retain actual nonzero/timeout/output/cleanup failures without
returning credentials, API bodies or raw backend diagnostics. No resident
process, background enrollment or provider retry loop is proposed.

## Configuration composition and compatibility

Reuse the current version-1 form, input preview, default-Cancel confirmation,
progress/cancellation and originating-scope refresh owner. Reading the catalog,
opening/cancelling the form or refreshing performs no creation or policy write.
Explain when a configured account or supported protection owner is unavailable;
do not turn an optional action into a mandatory personal-login gate.

Follow [service-contract evolution](../../../docs/patterns/service-contract-evolution.md):
expand the shared proposal reader for version 2 and native provider with its
selected-runtime declaration before enabling the contribution. The existing
form manifest remains version 1; the nested owner proposal has its own version.
A new Manager and older provider preserve the existing operations without
inventing creation. An older Manager encountering a new remote proposal must
reject its unsupported version before confirmation/apply, not accept its
effects as a local set. Missing, malformed or rolled-back support withholds
only the optional operation, preserves existing acquisition/registration/binding,
and never strips flags or replays another operation. Test both rollback
directions and the actual no-apply behavior, not just descriptor acceptance.
No old command changes its effects or loses strict argument rejection.

After a successful receipt, the normal next steps remain separately confirmed
checkout acquisition, local registration and binding. A failed or uncertain
attempt still refreshes its originating scope and preserves its receipt;
refresh is not automatic retry or success. Required application/knowledge
admission remains with the existing New owner.

## Dependency-ordered delivery and validation

- [x] Review this proposal before runtime or consumer implementation; #12004.
- [ ] Deliver the standalone native preview/apply owner, capability and honest
  complete/partial/unknown outcomes, including separately confirmed protection
  completion, without changing existing operations.
- [x] Expand the existing shared proposal reader for explicit version-2 remote
  create/protect scope; retain the exact version-1 local baseline and prove
  unsupported versions refuse before apply rather than creating another modal.
- [ ] Connect the optional real Configuration form through that selected owner;
  preserve confirmation, current-source revalidation, receipt and refresh.
- [ ] Cover configured exact/default policy, nonpersonal destinations, missing
  selection/store, mismatch, existing target, private identity, server-chosen
  branch, denial/unsupported plan and target/registry drift before effects.
- [ ] Cover equivalent classic/ruleset safeguards, incomplete or stale policy,
  active no-bypass default-only creation, and preservation of direct/fast-forward
  and unaffected feature-branch semantics.
- [ ] Cover failure/cancellation/timeout before and after each mutation,
  malformed/lost receipts, one-attempt behavior, no automatic deletion/replay,
  bounded sanitized output and fatal retained cleanup custody.
- [ ] Prove the real native CLI/provider and production Manager form/process
  boundaries with synthetic private fixtures. Compare complete local registry,
  binding, Git/configuration and credential-sentinel bytes; remote effects are
  fixture-owned, never performed against an operator repository.
- [ ] Cover old/new callers/providers, absent/malformed support, rollback,
  default Cancel, unchanged acquisition/register/bind behavior, and the complete
  separately confirmed consumer sequence through existing New admission.
- [ ] Run required source checks, exact-head review and CI before merge.
  Record unit, native/simple integration and fresh-fixture coverage separately;
  any native Windows launch-path change retains the repository's genuine
  window/focus and exact-cleanup qualification contract.
- [ ] Keep source, actual provider enforcement, installed UI/runtime,
  first-restarted-model, cold onboarding and receiving evidence separate.
  Live creation/protection or destructive branch trials require a separately
  authorized disposable target; source publication grants no such effects.

## Retained acceptance

#1419 keeps full KO/KOV and K/PF acceptance, including actual first-model and
decline behavior, arbitrary existing-task pairing, overlays, provider recovery,
state writes, cleanup and receiving. Other providers remain explicitly
unsupported by this bounded creator rather than falsely protected.
This plan does not implement authentication acquisition, repository setup,
runtime replacement, installation, deployment or retirement; those preserve
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
and their existing owners. No installer selections, user state or sessions are
changed by this proposal.

## Journal

### 2026-11-10 - Shared remote-effect preview receiver

The bounded reader prerequisite passes all 74 affected controls, including
the production Textual form and actual synthetic owner CLI. Both remote
operations render their real scope; default Cancel, unknown-version refusal,
target/context freshness and the version-1 local set/reset baseline remain
covered. The two new parser controls fail on the original reader.
Fixture remote effects are local markers in the private test tree, not
GitHub operations or protection enforcement.

Scoped Python lint, all 13 install contracts, documentation consistency,
local links and whitespace checks pass. The changed Manager project remains
pending typing admission. The optional local enforced probe passes eight
platform checks before an unrelated unprepared checker stops it; that is
not a full typing pass. Required exact-head CI and review remain independent.
No launch primitive changes, native desktop qualification, cold installation,
installed-runtime observation, live provider effect or rollout is included.
The native creation/protection owner and its real consumer sequence stay open.

### 2026-11-10 - Standalone native creation and protection owner

The standalone source owner implements separate create, read-only inspect and
protect-default operations, with selected-runtime inventory capability
`knowledge_repository_creation: 1`. It does not expose a Configuration catalog
action or consumer yet. Named configured account selection, personal-private
identity, initial provider README/branch, complete no-bypass safeguard reads,
one-attempt creation and independently confirmed protection use the existing
credential and finite caller-owned process boundaries.

All 94 selected controls pass, including the actual native CLI, worker and
synthetic credential/provider subprocess, separately confirmed protection
repair after denial, timeout/descendant cleanup, cancellation, stale receipt
serialization and affected legacy parser/set/reset/registration/binding
contracts. Native source, configuration and credential sentinels remain
unchanged apart from the operation's own receipt/lock evidence. These fixtures
simulate remote effects in private local state, not GitHub enforcement.

An earlier broad selection exceeded its unchanged 300-second group budget;
it is not a full-suite pass. The final affected-contract selection preserves
those deadlines. Worktrees remains pending for typing admission: its optional
whole-project probe stops on duplicate setup modules, not a typing pass.
Exact-head required CI and code review remain independent publication gates.
Live creation/protection, installed/receiving evidence and the optional real
Configuration consumer remain open; no launch primitive or rollout changes.

The recovery controls additionally cover runtime update/rollback, interrupted
repair from a prior successful receipt and failed read-only observations.
Six controls fail on the original implementation; all 44 native-owner cases
then pass with the corrections. Durable receipt lookup now uses stable
installation/project/repository identity while previews retain runtime
freshness. Repair remains incomplete until safeguards are reverified, and
inspection failures preserve original creation evidence plus any available
current metadata. These fix concrete recovery failures, not just review shape.

The final identity controls compare inspection with the confirmed original
repository ID/default branch and normalize repository slugs without folding
branch names. The complete provider, credential and native-owner selection
passes 91 cases, including case-equivalent protection repair and paginated
policy reads. Original creation and current observations remain distinct when
the provider identity or default branch drifts.

The policy packet additionally accepts either pagination query order while
retaining exact source/branch, unique parameter, page-size and next-page
validation. All 33 provider cases pass, including reordered links and rejection
of duplicate or unrelated query parameters; the native/credential baseline
above remains unchanged.
