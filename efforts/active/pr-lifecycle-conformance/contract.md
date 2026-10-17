# Normalized lifecycle contract

Parent: [PR Lifecycle Conformance](README.md).
Evidence: [provider inventory](provider-inventory.md).

**Agent-recommended design for review, not implemented behavior.** Extend the
existing `PRProvider`, `pr-*` results and reviewer recipe. Do not add another
forge client, credential store, PR lifecycle or dispatch state machine.
Existing command names and result fields remain compatible; new structured
fields are additive and explicitly versioned.

## Target, candidate and evidence

Every structured result identifies the addressed provider, canonical endpoint,
repository and PR. Preserve native repository IDs where available alongside
the addressed slug; absence is explicit. Do not resolve an unavailable foreign
target to the caller's checkout, provider or credentials.

Candidate evidence includes source repository/ref, head commit, base ref and
base commit. Unknown fields carry a reason, not a fabricated empty identity.
A candidate-bound operation states which fields it requires and refuses when
they are unavailable. Head-only authority must not silently become authority
for a different base or source repository.

Provider IDs are opaque strings scoped to target and object kind. Numeric IDs
can be serialized losslessly as strings; display indexes cannot be used as
mutation identities. Preserve provider-specific review semantics: an Azure
current vote is not an ordered GitHub-style review history, and Gitea review
comments are not evidence of native resolvable threads.

Observation timestamps state their provenance: provider clock or local
observation clock. Local time never substitutes for missing provider-clock
authority. Draft, holds, consent, review evidence, checks and mergeability
retain their own known/unknown state.

Keep observed check rollups separate from required merge-policy evidence.
All observed checks can be green while a required context has not appeared.
Required-check readiness must account for missing contexts and provider/app
identity, not merely the checks returned by a status-list operation.

Disposition is `open`, `merged`, `abandoned` or `unknown`. A successful consent
request or accepted asynchronous completion request is not a merged result.
Only authoritative terminal observation settles the corresponding obligation.

## Outcomes and capabilities

The envelope carries `schema_version`, target, candidate/evidence, operation
kind and an outcome. Proposed outcomes:

| Outcome | Meaning |
|---------|---------|
| `succeeded` | The requested read or mutation has the stated authoritative evidence |
| `unsupported` | A named provider capability is absent; no mutation was attempted |
| `incomplete` | Some requested read evidence is unavailable or partial |
| `stale_candidate` | Required candidate authority no longer matches; no authorized stale write may be initiated |
| `forbidden` | The resolved actor lacks authority for the requested object/action |
| `not_found` | The addressed object is authoritatively absent; do not retarget |
| `invalid_cursor` | The cursor cannot safely continue this read; explicit restart instructions are supplied |
| `unresolved` | A mutation may have happened, but cannot safely be confirmed or replayed |
| `transient_error` | A classified retryable failure, with mutation replay separately governed |
| `permanent_error` | A non-retryable input, transport or provider failure |

Error detail includes a stable code, actionable explanation and retry/recovery
requirements without secrets or response payload leakage. Failure to fetch a
supported capability is not `unsupported`; unknown is not false. Existing
human-readable errors remain visible during migration.

Capabilities describe operations and guarantees, not just method presence:
full/incremental collection reads, replies, edits, resolve/reopen, conditional
candidate writes, operation identity and authoritative reconciliation.
Typed unsupported disclosure is honest behavior but does not satisfy a
required workflow step in #7051.

## Complete and incremental reads

Each requested collection reports its own `complete` evidence and continuation
state. A partial collection may be returned for diagnosis, but never as a
complete empty/successful read. Exhaust all pages for a full read, including
nested comments. Failed nested reads make the parent collection incomplete.

Read identities retain review, thread and comment IDs, actor, created/updated
revision, parent/reply association and candidate/position association where
the provider supplies them. Distinguish outdated positions, deletion and
unavailable history; do not infer tombstones from absence on one partial page.
Edits keep the same object identity. Synthetic groupings explicitly identify
their derivation and unsupported mutations.

Incremental cursors are opaque, versioned and bound to target, collection,
candidate scope and provider watermark/continuation semantics. A cursor from
another PR, provider, collection or incompatible candidate cannot be reused.
Expiration and malformed cursors return `invalid_cursor` with full-read
restart instructions, not a silently reset or empty result.

Incremental results include changed records and any authoritative tombstones.
If the provider cannot supply change history, expose a documented comparison
against a complete retained snapshot, or report the capability unsupported.
Do not advertise provider-native edit history that does not exist.

Pagination is not a transactional snapshot by itself. Validate candidate and
provider revision consistency across traversal; a moved candidate or unstable
page set returns incomplete evidence and a safe continuation/restart action.
Diff identity binds source and base candidates, not just a PR number.

The existing `Baseline`/`pr-watch` transition cursor keeps its current meaning.
It must not masquerade as a discussion/diff incremental cursor.

## Candidate-fenced, recoverable mutations

Mutation intent binds target, action, expected candidate, internally resolved
actor, caller-supplied operation ID and payload digest. Reusing an operation
ID for a different target/action/candidate/payload is a conflict, not a new
write. Keep receipt/recovery evidence under the existing publication ownership
boundary; do not invent a second task/session model.

Before publication, obtain the evidence required by the action and refuse a
known stale candidate. Use the provider's native conditional mutation and
commit/iteration binding where available. A preflight read alone is **not**
proof of an atomic unchanged-head guarantee. Adapters must name the actual
guarantee and return an explicit unsupported/unresolved limitation when the
required fence cannot be enforced. Transport-level tests must establish the
chosen provider mechanism before its capability is advertised.

Re-observe after publication and report the actual resulting object/candidate.
If the head moves after an accepted write, retain the published object's
identity and its now-stale authority; never claim that the write did not occur.
It must not authorize a verdict or merge for the replacement candidate.

Persist intent before sending. Recover uncertain sends through authoritative
provider read-back: known result ID first, then an exact durable operation
identity scoped to target, actor and candidate. Identical text, the newest
comment, or a single page is not operation identity. Multiple matches or
incomplete evidence remain unresolved.

An absent object immediately after a timeout is not proof of non-publication:
the original request may still commit. Retry only with proof that it was not
sent, a provider-enforced idempotency/conditional identity, or other
authoritative evidence that rules out the earlier request completing later.
Otherwise block replay and preserve pending responsibility. Do not promise
exactly-once writes based on local journaling or text markers alone.

Desired-state label operations can be idempotent without making review/comment
creation idempotent. A vote plus optional comment is a multi-effect operation;
retain both results and reconcile partial completion independently.

Discussion edits enforce the provider's authenticated actor/ownership rules.
Resolve/reopen targets exact durable thread IDs; an explicit all-threads
operation is separate from selected-ID mutation. Deleted/forbidden objects,
partial results and unavailable reopening remain explicit. No new elevated
authority, reviewer merge bypass or conflict-resolution worker is introduced.

## Lifecycle composition and acceptance

Keep submitter refresh/rebase/publication and consent in their existing
owners. A conflicting rebase returns responsibility to the submitter.
Preserve registered foreign-repository addressing and internally brokered
credentials; adapter transport details never become agent obligations.

Use existing blocking observation and durable dispatch wait/resume. Pending
responsibility retains target, candidate, operation receipt and applicable
cursor across restart. Resume observes and reconciles before another mutation;
it does not blindly replay a completed turn.

Shared cases traverse every real adapter's request/response boundary as well
as the mock. Cover complete/nested pagination, edits/replies/deletion,
resolve/reopen addressability, candidate changes, actor refusal, invalid
cursors, non-unique text, lost responses, late completion and interrupted
recovery. Include green observed checks with a missing required context;
neither the observation cursor nor an approval may authorize that merge.
Preserve existing author-side commands on Windows and Linux/WSL.

Each real provider then runs the ordinary CLI-based multi-round scenario:
inspect, discuss, request changes, revise, address discussion, re-review,
consent and verify merge; separately prove abandoned disposition. At least
one external transition per provider suspends/resumes through the existing
dispatch contract, including restart without duplicate mutation. Unsupported
required steps keep acceptance open. Deterministic fixtures require no live
credentials, production writes or deployment authorization.
