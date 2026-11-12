# Driver-aware filters and owned population subscriptions

Back to the [canonical effort](README.md); bounded implementation claim #12159
under #6007. Existing #11783 repository-cache, provider/native, credential,
Gateway, replay and lifecycle owners retain their boundaries.

## Goal and accepted direction

Build a common way to express PR filters with driver-specific evaluation:
repository; from users/groups; assigned to users/groups; age; tags; and
draft/published. Separate reusable filter definitions from independently owned
subscription demand. Named definitions and dynamic inline callers use the same
registration, observation and delivery owner.

The operator accepted this model and requested implementation. This source
assignment does not authorize deployment, live credential enrollment, shared
service changes, real review actions or retained-resource disposition.

## Intent and existing seams

This closes the PR vision's
[selected populations](../../../visions/plugins/agent-pull-requests/README.md#explicit-sets-and-selected-pr-populations)
and [caller-driven connections](../../../visions/plugins/agent-pull-requests/README.md#caller-driven-backend-connections).
The small vision expansion states reusable definitions, driver semantics and
demand lifetime; implementation detail belongs here.

Compose the existing `RepositoryIdentity`, `ReplayObserver`, native Worktrees
provider/observation credentials, `RepositoryProjection`, continuing
`RepositorySubscriptions`, local/Gateway request lifetimes and standalone
foreground host. Queries never become forge pollers. Provider selection never
comes from untrusted executable/credential arguments. This is not a second
watch daemon, database, credential issuer or Dispatch queue.

M1 supports configured GitHub/Gitea repositories and a Linux local ingress.
Native snapshots already support Azure DevOps. This expansion supplies an
actual driver selection seam and ADO filtered population path, not an assertion
that a package version or passive descriptor proves installed support.
Existing default profiles, one-shot waits and prior storage stay supported
unchanged when this feature is unselected.

## Filter contract

Expressions are versioned, bounded, declarative data. Start with `all`/`any`
composition and leaves for the six families; no arbitrary code, provider
query string or executable is accepted. An ordinary field collection is OR
within that field, AND across fields. Reject empty/ambiguous expressions,
unknown keys, duplicate members, excessive depth/width and malformed values.
Repository selection must remain bounded by the authenticated caller's
configured provider/endpoint/account/repository authority, including `any`
branches. A filter narrows access; it never creates it.

Each executing driver declares supported fields/operators and exact meanings.
Common names do not erase provider differences: ADO assignments initially mean
reviewer assignments, not work-item ownership; group selectors use provider
identity/group semantics, not an email suffix or a locally assumed team.
User/group namespaces are explicit. Existing trusted membership resolution is
renewed and has coverage/freshness; missing, truncated or unavailable
membership cannot be interpreted as an empty group.

Age explicitly selects creation age or last-activity age, with bounded duration
comparisons. Evaluation uses the observation cycle's captured time. Time alone
can cause population entry/departure without a new provider head, and requires
scheduled reevaluation. Tags identify native labels/tags with explicit any/all
matching. Draft/publication is an explicit state selection; do not silently
inherit review admission, recency, self-review or actionability policy.

Use three-valued evaluation: match, nonmatch, unknown. Capability validation
rejects an unsupported required predicate before registration/acquisition;
runtime incomplete facts produce unknown coverage, not false nonmatch or a
broadened match. `all` and `any` retain their ordinary three-valued composition.
Push predicates down to a provider only where semantics are equivalent;
evaluate residuals against bounded current facts through the driver.

## Definition and demand ownership

Adopter configuration may hold named filter sets. A definition is inert until
an authorized owner subscribes. At registration, resolve its revision to
immutable canonical expression data and retain that intent with the original
request ID. Later definition edits never silently change an existing watch;
explicit replace/renew semantics must distinguish intent changes from retry.
Personal rosters, endpoints and named sets stay outside public source examples.

An admitted emitter or arbitrary CLI caller can register a supported expression
or named definition through the same service front door. The authenticated
principal and configured consumer mapping identify its owner, never a body
field claiming ownership. Finite expiry, original request identity, renew,
inspect and cancel are explicit and retry-safe. Renewal cannot revive cancelled
demand, steal another registration or reset its baseline/ACK position.

Startup configuration may sponsor subscriptions through this same owner. The
explicit configured sponsor retains responsibility across host restart;
there is no unowned permanent sweep triggered merely by a definition file.
Startup sponsorship does not rely on an interactive agent staying alive.
Dynamic demand requires renewal; loss of a caller eventually expires its
interest without service restart.

The observer acquires the union of active, authorized demand. Overlapping
subscriptions coalesce provider reads but have independent membership,
baselines, event delivery, cursors and ACKs. Expiry/cancellation removes only
that owner's acquisition demand. Other interests continue; unsettled delivered
events and original receipts retain their established reconciliation lifetime.
The host must not start a later read for withdrawn demand; an already-started
read retains its original cleanup owner and cannot publish for withdrawn
authority.

## Population and storage semantics

Bound provider pages, members, native requests, response bytes, concurrent
readers and cycle time through the existing owners. Prefer bounded
author/reviewer-selected discovery for ADO rather than a whole-forge scan.
Deduplicate provider PR identity across overlapping user/group queries.
Incomplete discovery retains earlier accepted membership with partial or
unavailable coverage; it cannot establish departure or closure.

Return membership entry, semantic change and departure separately from
authoritative terminal state. An age/tag/draft/reviewer change can remove a PR
from one selection while another still observes it. No permission to vote,
merge or emit a task follows from membership or a cache event.

Filtered state must be positively selected with an explicit compatible storage
generation. Reuse ReplayStore transaction/writer custody, original event IDs,
bounded retention/backpressure and durable replacement. Do not silently
migrate prior retained state, strip extension fields, or let old readers
ignore required filter semantics. Supported rollback retains a compatible
reader/state or separately fresh root; no automatic deletion is included.

Supported CLI and authenticated service routes expose filter capabilities,
query/baseline, subscribe, inspect, renew, events, ACK and cancel. Current
running capabilities, not source presence, admit the new syntax. Old clients
retain their unfiltered behavior; new clients against unsupported owners
refuse before effect and never fall back to a broader sweep or process waiter.

## Implementation plan

- [ ] Land this contract/vision amendment through normal independent review and
  required CI before runtime implementation.
- [ ] Add the bounded expression parser, canonical immutable definition
  resolution, capability validation and three-valued driver evaluation.
- [ ] Extend the existing native provider seam with bounded selected-population
  acquisition and initial ADO semantics; preserve read credential ownership and
  existing GitHub/Gitea/default paths.
- [ ] Extend the existing replay/projection/subscription owner with positively
  selected filter demand, independent membership, original-ID retry, finite
  renewal, cancellation and pending-delivery protection.
- [ ] Compose startup sponsorship and authenticated dynamic CLI/service
  consumers through the same selected observer, without per-filter pollers.
- [ ] Land runtime slices with documentation, focused qualification, normal
  review/CI and changefiles; record release separately from rollout.
- [ ] Record the remaining adopter-specific host, credential, population and
  live-event acceptance in the existing parent rather than claiming source
  controls complete it.

## Validation plan

- [ ] Parser and actual CLI controls reject malformed/unknown/oversized
  expressions, unsupported leaves and out-of-scope nested selections.
- [ ] Synthetic driver controls exercise all six filter families, users/groups,
  explicit assignment semantics, age boundaries without source changes,
  group-resolution loss and incomplete provider pages.
- [ ] Real native process fixtures preserve selected configuration/credential
  scope and bounded capture; no ambient fallback or arbitrary query execution.
- [ ] Existing-store tests prove two overlapping consumers share reads but
  retain independent membership/events/ACK; cancellation/expiry/renewal and
  restart never reset original intent or lose delivered unsettled identities.
- [ ] Actual foreground host and supported CLI qualify inert definitions,
  startup sponsorship, dynamic subscribe/query/events/ACK/cancel, provider
  outage, scope refusal and joined drain using synthetic provider data.
- [ ] Prior/current client and storage controls prove explicit unsupported
  outcomes and unchanged unselected defaults, including rollback refusal.
- [ ] Required static/type/packaging checks and each changed surface's selected
  tests pass; independent current-head review and required CI precede merge.

## Completion and remaining adoption

Source completion requires all implementation and validation items above, with
actual supported consumer evidence, not an unwired library. Native Windows
local ingress, real service placement, existing production-state migration,
provider enrollment, live traffic and review/Dispatch automation remain
separately authorized acceptance; no synthetic fixture passes those gates.
The canonical parent remains Active until its own full Plan/Validation resolves.
