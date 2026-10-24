# S3a/b amendment: selected admission, full publication

[Effort](README.md) · [S3 design](codespaces.md) · #7949

## Explicit decision

**Decision: `selected_then_full_publication`.**

**Later design proposal:** [durable inventory and push-assisted
freshness](inventory-freshness-plan.md) explicitly revisits inventory and
control-publication freshness. It is not a silently active relaxation of this
contract. Functional optimization against this older schedule is paused while
the successor's storage, grace/action policy and owned transport/ACP contracts
are reviewed and selected. Credential/provider safety and atomic owner fences
remain requirements; no unconditional provider-reauth loop is proposed for a
status-only local cache read.

The user explicitly selected the following semantics: each observation uses
fresh selected-account/source checks; full live account/configuration/credential
authority is checked before actionable publication. An unrelated candidate
change may therefore allow observational work, including a connection wake,
before the result is rejected at publication. This is the accepted tradeoff,
not a claim that the old whole-set-at-every-effect semantics are unchanged.

This amendment changes only that S3a/b authority boundary. It does not authorize
weaker selected-account authentication, implicit account fallback, new services,
credential providers/caches/ledgers, S3c transport borrowing, S4 transcript
changes, live CodeSpace probes, or installed rollout. #8143 remains a draft
implementation until the causal fix passes its current review and CI gates.

## Acquisition and immutable wave context

Select due holds before authority or inventory work. Zero due observations
produce zero remote-observation work; local owed-push debt remains independent.

Acquire one source-qualified account inventory per due wave. Its immutable
context names source/installation/namespace, candidate accounts and selection
provenance, effective configuration, credential-source observations, owner
generation and monotonic observation/deadline values. Preserve per-account
success, error, row identity and completeness. A 50-row limit response is not
complete absence; duplicate ownership, partial/error sources, unknown state
and missing evidence never grant wake or release authority.

Credentials remain private, ephemeral effect inputs, not durable inventory
fields or diagnostic values. A source-owned read result may pass values that
were already read within one boundary into its immediate consumers. It must
not pretend that such reuse provides freshness at a later boundary.

## Selected admission at every true effect

Before each wake-capable config fetch, connection, direct SSH execution or
retry:

1. Establish that this owner still owns the relevant held work. Recheck owner
   generation and deadline after awaits and before effect admission.
2. Read the current selected installation/source context, explicit account
   selection and all configuration that affects that selected account/source.
   A producer must identify this dependency scope; a consumer may not guess a
   partial file list or ignore a selected-source configuration change.
3. Obtain a **fresh selected credential** through the existing owning provider,
   preserving provider precedence and failure behavior. An opaque credential
   cannot be declared unchanged without the required current read.
4. Compare the selected authority with the wave's selected observation and
   verify its bounded Available proof. Changed, unavailable, partial, expired
   or unowned evidence suppresses the effect. Never silently choose another
   account or wake a venue known stopped.
5. Use the read result only for that admitted effect. Retry admission repeats
   the selected checks; it does not reuse the previous attempt's authority.

A read-only command can still have a wake-capable transport. Inert local
lookups, parsing and owned cleanup are not extra authentication boundaries.
Cleanup drains only owned work and cannot be withheld because authentication
became unknown.

Unselected account/config/credential changes do not by themselves invalidate
selected admission before they are detected by full publication validation.
Once a full check detects change or uncertainty, stop admitting new work from
that wave, drain/cancel owned work safely and suppress its unpublished results.
Previously admitted observational work may finish, but cannot become actionable
under the rejected wave. Reacquisition is explicit and counted, never a hidden
per-venue refresh or lifetime extension.

## Full actionable publication

Collect only completed observations. A publication batch contains **at most
four venues and sixteen tenant verdicts**, and never waits to fill or for an
in-flight slow venue. Complete batches may progress while other venues remain
in flight.

Before any batch becomes actionable, freshly read **the full live candidate,
configuration and credential authority**, with completeness/error information,
and compare it with the wave. Changed or unknown authority rejects the batch;
an unchanged merged value backed by an indeterminate provider is not fresh
proof. A smaller process count caused by rejected observations is not success.

Heartbeat and confirmed-only release remain generation-fenced under the
existing owner's atomic mutation boundary. Validate current owner/deadline and
each expected tenant generation; stale observations cannot extend or release a
replacement. No caller-side check-then-heartbeat substitutes for that lock.
Existing caller/None semantics, leases and timeout bounds remain unchanged.
Any necessary batch helper stays source-owned, uses the existing lock, and
requires explicit agreement before editing a seam owned elsewhere.

A mirror launch is a **separate effect**, not a free consequence of publication.
It requires selected admission and cannot outlive its admitted proof merely
because it was queued earlier. No borrowed manager or transcript implementation
change is included here; report a dependency if enforcing that rule reaches an
unadmitted shared seam.

## Ownership and validation gates

This contract does not transfer another worker's files. In particular, preserve
the #5761 namespace-discovery owner (`rattling-circuit`) and the canonical
`account_candidates.candidates()` ordering, source reads and concurrency
behavior. Absence of a release marker or matching open PR is **not** release
evidence.

The read-result seam needed to expose already-read candidate routing/config
inputs must be agreed with that owner; do not duplicate its algorithm in the
observer to avoid ownership. Fresh task/forge checks are required before edits.
If that owner still prevents the exact seam, return the dependency instead of
implementing it unilaterally. A helper in a newly discovered shared owner also
requires an exact proposal before editing.

No performance pass is implied by this decision:

- Compare complete actual-callgraph cold, warm-due and unchanged-not-due
  0/1/10-venue workloads, CLI-credential and provider-keyring variants, with and
  without bridge proof. Count all classified discovery/auth/list/page/retry/
  config/SSH requests and provider API calls, not just listing/mux subwork.
- Exercise account/namespace/selected-config/credential changes, unselected
  changes rejected at publication, provider errors, partial/limit listings,
  expiry, owner/tenant replacement and cancellation. Preserve slow-venue
  independence and actual owned-child cleanup.
- Total classified work must not regress against the matched baseline.
  Preserve every raw frame and expected/completed observation count.
  Failed, partial, opaque or omitted work is not a passing comparison.
- Keep current deadlines and health observations. No larger bound, suppressed
  health or dropped observation may manufacture a pass.
- Run scoped regressions, install/lint/source guards and normal review/CI
  before source merge. Release and installed qualification remain separate;
  neither a contract merge nor a passing fixture grants rollout authority.

## Amendment gate

This document records the explicit user decision for normal source review
before implementation. Existing inventory/mux batching and atomic tenant
generation fences remain the foundation. The original measurements and
blockers in draft #8143 remain evidence, not an acceptance receipt.
