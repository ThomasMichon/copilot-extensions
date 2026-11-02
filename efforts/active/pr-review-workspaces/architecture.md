# PR Review Workspaces - Architecture Proposal

Parent contract: [PR Review Workspaces](README.md).

This is an **agent-recommended design proposal**, informed by the operator's
selected ADO-first scope and `agent-pull-requests` ownership. Exact command
names, schemas, database layout and service routes remain proposed, not
implemented interfaces.

## Ownership and reuse

`agent-pull-requests` owns the PR-domain client/service capability. Its existing
service is the candidate host for a shared caching database and its existing
observation/registration owner remains the only watcher. Worktrees retains
source workspace, repository identity, publication and resource obligations.
Credential providers retain issuance and renewal. Reviewer policy remains with
the caller/repository; the PR toolset does not become a personal rubric engine.

Reuse versioned snapshots, full-thread reads, provider/account resolution and
operation receipts. Expand the successor's domain seam and migrate thin
compatibility clients in stages. Do not create a circular wrapper dependency
or copy a second forge client alongside the old one.

The existing PR service and its lifecycle groundwork are not proof that this
cache, workspace API or submission protocol is already supported. Actual
running capabilities must be discovered before a client invokes them.

## Three different kinds of state

| State | Owner and meaning | Recovery |
|-------|-------------------|----------|
| Upstream PR facts | Forge authority | Re-observe through the configured provider |
| Observation cache and local read view | PR service/cache owner; client materializes a generation | Rebuildable, with explicit freshness and completeness |
| Submission intent and receipts | Existing PR operation owner | Durable; retain pending/confirmed/unknown effects across cache rebuild |

A physical database may host both cache and operation tables, but their
retention, backup and recovery contracts must remain distinct. Cache eviction,
resynchronization or schema repair must not erase uncertain effects or make
them eligible for a new submission.

The drive/task/effort ledger references operation receipts. It is not another
posting journal. The cache and review-plan contents do not grant permission.

## Logical representation

Key a PR by canonical provider, hosting endpoint, project/repository identity
and PR number, retaining the selected account/principal scope. A number or
display name alone is not identity. Separate scopes even within one operator's
service; possession of cached bytes does not grant another account access.

Each immutable generation references these normalized sections:

- PR identity, author, description and source/target refs.
- Head, source iteration, iteration target and authoritative common range.
- Complete changed paths, old/new blobs, renames and stable change-tracking IDs.
- All-status discussions, comments, edits/deletions and iteration anchoring.
- Reviewer votes, approval events and actual voting restrictions.
- Applicable policy/eligibility facts and completion-state presence/values.
- Section capabilities, observation intervals, completeness/errors and the
  exact source workspace reference.

Draft review decisions remain local work separate from upstream facts.
Represent unavailable evidence explicitly; absence is not a false value or an
empty successful collection. A compact, readable materialization is the
reviewer's normal input, not hundreds of provider-response files. Bounded raw
diagnostic capture may be retained privately by its normal owner.

Publish a current-generation pointer atomically under one local cache writer.
This is a local consistency guarantee, not a claim that the forge supplied an
atomic snapshot across all endpoints. Observation intervals and drift remain
visible.

## Load and source attachment

An initial load resolves the configured target/account and obtains bounded,
complete normalized facts, discussions and range evidence. It may attach source
through the existing managed workspace owner, but metadata readability and
source readiness remain separate results.

Prepare exact reviewed head and common baseline in a shallow Git workspace.
Retain previous immutable refs; do not merge onto today's target tip. Verify
the complete path/blob mapping and classify actual prepared blobs before
claiming semantic source readiness. Missing optional remote binary metadata
is unknown, not automatically a text blob or an author defect.

The PR service need not host a full product checkout or run product builds.
Source stays in the review venue; the shared cache holds identities/manifests
and references to the selected source attachment.

## Incremental synchronization

Sync is pull-only. It never submits drafts, changes a verdict or closes a
thread. Return old/new generation, observed times, changed sections and
decision invalidations in a compact machine-readable result.

Use provider cursors, conditional reads or equivalent deltas where supported.
Otherwise perform bounded section refresh; do not pretend every provider has
a universal delta API. A cursor reset or unsupported optimization is visible.
Shared service observation should coalesce compatible reads rather than add a
poller for every reviewer.

| Observed transition | Required treatment |
|---------------------|--------------------|
| No material change | Reuse unchanged sections; no review credit or effects |
| Author reply, comment edit/deletion or disposition | Surface discussion delta even without a source push |
| New source head/iteration | Invalidate stale decision eligibility and anchors; retain prior review/history |
| Target advances with unchanged authoritative common range | Do not automatically discard source analysis; refresh other changed facts as needed |
| Partial page/read failure | Preserve known evidence and expose incomplete/stale sections, never empty success |
| New head observed but source preparation fails | Show the pending head and unavailable source; do not present old source as current |
| Interrupted or concurrent publication | Retain complete immutable generations and serialize/CAS current-generation publication |

Freshness is fact- and operation-specific. Immutable pinned source does not
need downloading again merely because reviewer or policy facts need a fresh
observation. Actual named consumer gates remain requirements until their
owning contract changes through review.

## Explicit review submission

The reviewer produces an explicit plan bound to the reviewed generation,
head and iteration: selected comments, permitted owned-thread updates and a
named verdict. Caller/repository authority is resolved independently; a plan
file cannot self-authorize effects.

The operation performs fresh identity, scope, revision, pusher, discussion and
voting checks required for that action. It preserves completion settings and
other reviewers' state. Merge readiness, reviewer-vote authorization and
human-only actions remain different predicates; a blocked merge is not by
itself a blocked review submission.

Acquire appropriately scoped typed credentials for every request and page
through the existing provider. Observation-only material does not become a
review-write credential. No tokens/headers are stored in the workspace, and
selected-source failure does not silently select an ambient account.

Persist submission intent before effects. Reuse actual provider idempotency
where available and retain platform IDs plus readback. A multi-action batch is
not assumed atomic:

- Confirmed actions stay confirmed.
- Unattempted actions remain unattempted.
- Ambiguous actions remain unknown and reconcile before retry.

If the provider cannot atomically condition a write on the reviewed head, expose
the real pre/post bracket and any race. Never report that several comments and
a vote committed as one atomic review transaction without provider support.
Natural author-owned completion lifecycle changes are observations, not an
instruction to restore or change merge settings.

## Reviewer loop and interim adapter

One reviewer loads/synchronizes the workspace, reads exact source and its
caller-selected guidance, analyzes the meaningful delta, selects a review and
uses the explicit submission operation. Required independent finding challenges
remain separate from forge transport; removing the ADO interpretation split
does not silently remove that requirement.

An interim ADO agent can execute only these same two requests: update the feed,
or submit this explicit accepted review. It should not synthesize another
evidence contract or decide that missing transport evidence means ineligibility.

The service owns PR observation/cache and operation state, not task assignment,
rubric, consent, review leadership or a new queue. Existing clients and the
Worktrees compatibility surface compose the same domain contract.

## Service and native modes

The service-backed cache is the preferred sharing point for multiple reviewers.
Its writer authority, quiescence, durable registrations, namespace isolation and
activation follow the existing PR-service owner and lifecycle contracts.

An explicitly selected finite native path may remain useful without a daemon.
It must use the same domain behavior and operation-recovery authority rather
than become a second implementation. An unavailable service cannot silently
move an in-flight mutation to another backend or assume its pending journal
is absent. Pure cached reads can report their retained generation and staleness.

No service installation, upgrade, fleet placement or activation follows merely
from accepting this design.

## Architecture decisions to resolve before implementation

The following are **agent-recommended decision points**, not unrecorded scope:

1. Whether the existing watch-state store gains cache/operation tables or an
   adjacent store under the same authoritative owner; SQLite is a candidate,
   not an operator-selected requirement.
2. The generation/cursor and section-completeness wire contract, retention and
   bounded resynchronization behavior.
3. Explicit native/service selection and capability discovery, including
   supported mixed-version behavior and rollback.
4. The staged migration from the current Worktrees provider facade into the
   successor domain without circular imports, wrapper cycles or two writers.
5. Submission request identity, provider-supported deduplication/readback and
   the exact recovery contract for partial/unknown actions.

## Delivery and validation

Reconcile the existing visions first, then agree the above contracts and
admit source slices separately: normalized ADO load/sync and cache; exact
source attachment; explicit submission/reconciliation; reviewer integration.

Qualify one eligible ADO review end-to-end before expanding lanes. Exercise
renames/deletions/binaries, omitted pagination fields, pushes during reads and
writes, reply-only updates, credential expiry between pages, cache rebuild,
interrupted publication and lost responses after accepted actions. Preserve
existing observation/registration and lifecycle consumers.

Automatically measure load, sync, preparation, analysis, submission and
readback phases. A partial fetch, prepared source, draft review or rendered
report is not a completed review. The detailed acceptance checklist remains in
the [parent effort](README.md#validation-plan).
