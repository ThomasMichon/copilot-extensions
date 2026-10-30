# Next Slice: Authorized ADO Evidence Acquisition

Parent contract: [PR Review Workspaces](README.md).
Prerequisite representation: [read generations](read-generation-slice.md).

This agent-recommended continuation extends the source-only candidate without
introducing a daemon, cache writer or effectful review path. The amendment must
clear review before implementation.

## Reuse and scope

The existing Worktrees ADO reader owns HTTPS requests, renewable typed
observation credentials, request/page deadlines and bounded collection reads.
Extend that owner with a finite evidence exporter instead of adding a second
ADO HTTP implementation to the successor plugin. The successor remains the
generation/normalization owner; the provider exporter emits its versioned raw
evidence envelope without importing the successor.

The exporter requires explicitly selected endpoint, repository and a trusted
credential-command binding with `observe` permission. It must not accept an
ambient token/account fallback. Obtain the actual current principal through
the provider's fixed identity endpoint using that same credential binding;
caller-supplied principal text cannot stand in for observed identity. PR
metadata supplies canonical project/repository IDs, matched to the requested
PR number. The credential owner retains issuance, renewal and revocation.

## Finite operation

Read metadata, current principal, iterations, the exact selected iteration's
common-baseline changes, all-status discussions and policy **evaluations**.
Reviewer and completion values come from the observed PR object. Policy
configurations are not evaluation results. Preserve every section's actual
observation interval and explicit completeness/errors.

Reuse existing bounded transport. Add only the iteration-change pagination
form it does not yet support: `changeEntries` with explicit continuation
metadata. Missing, malformed or repeated continuation evidence is incomplete,
not an empty terminal page. Credentials renew for each request/page; auth loss
aborts the operation rather than publishing a deceptively complete partial
result. There is no automatic retry under another account or backend.

Bracket acquisition with fresh metadata and iteration reads. Publish newly
observed head/iteration evidence even when changes were collected for the prior
range; retain their original revision binding so the generation builder exposes
incompleteness instead of relabeling old paths. Preserve local observation
intervals; this bracket is not an atomic forge snapshot or server-clock proof.

The finite caller chooses its operation budget within the transport owner's
supported bounds. No consumer-specific bridge timeout becomes provider policy.
Record acquisition duration directly. Exhausted byte/record/time budgets
produce explicit partial/failure outcomes, never empty success.

## Source entry point and compatibility

Expose an explicitly source-selected read-only module entry point alongside the
owning provider. It declares its exact evidence contract/capabilities before
read invocation, uses strict CLI parsing and prints one JSON value. Existing
snapshot/watch/thread commands retain their syntax and semantics. No installed
peer is assumed to implement the new entry point; missing support is unavailable,
not permission to choose an ambient executable.

This is provider-first expansion. The successor's ordinary installed client does
not automatically emit new peer syntax. A later admitted consumer can select a
capable owner and compose exporter output with the existing generation builder;
old clients keep their existing paths. No retirement or deployment is included.

## Acceptance

- [x] Fixed-target identity and PR scope checks reject wrong identities before
  publishing a generation; no credential bytes appear in output/errors.
- [x] Synthetic reader/transport controls cover complete acquisition, explicit
  empty sections, unavailable policies, missing/repeated change pagination,
  credential expiry/loss, and finite budget exhaustion.
- [x] Pushes during acquisition expose the newer head/iteration and retain old
  changes as incomplete; completion settings and other reviewers remain reads.
- [x] Exercise exporter output through the actual generation builder and
  source-selected parser, without a live backend or runtime installation.
- [ ] Publish through normal source gates. Separately coordinate authorized
  read-only live consumer feedback; source tests do not establish live access
  or a completed ADO review.

## Still outside this slice

Service-backed caching/CAS, exact Git source attachment, submission intent and
unknown-write reconciliation retain their parent-effort obligations. This
exporter neither evaluates review eligibility nor grants posting authority.
