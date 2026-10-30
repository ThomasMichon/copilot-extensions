# Exact local source attachment

Parent: [PR Review Workspaces](README.md). This realizes the reviewed
[source ownership and attachment intent](architecture.md#load-and-source-attachment),
without making the PR service another checkout, credential or build owner.

## Selected first source slice

Worktrees gains a source-library operation over an **already selected,
owner-controlled local Git object repository**. The embedding workspace/resource
owner resolves repository and workspace identity through its normal input,
repository, checkout-class and resource-custody pipeline before constructing the
adapter. No caller-supplied path, scope label or capability result authenticates
that selection. There is no new issuer, universal provenance verifier or
delegate-specific permission gate.

The existing source owner prepares/fetches objects through its normal authority;
this slice neither clones nor fetches anything, follows a ref to today's tip,
checks out files, restores dependencies, builds, nor contacts a backend.
An unavailable head/common commit remains unavailable. A missing object in a
partial clone must not trigger Git's implicit network fetch.

The source adapter is implemented in Worktrees, not as a second Git subsystem
inside the PR plugin. It reuses existing repository-environment isolation,
Git tree parsing and caller-owned bounded process execution. The PR-domain
attachment function accepts that adapter through a callable seam, validates the
generation's scope/revision, and interprets its source result. No service,
watcher, operation ledger or installed command is introduced.

## Request, output and consistency

The operation takes the exact generation scope, source head, authoritative common
commit, iteration and native change records. Source readiness requires complete
PR/iteration/change evidence and consistent head/iteration/common binding; it
does not require complete discussions/reviewers/policies or merge readiness.
An honest metadata generation may remain incomplete while its source is ready.

The selected owner supplies its canonical local root, opaque existing workspace
reference, exact selected scope and ordinary current-owner checkpoint. Recheck
that checkpoint around each physical Git operation and ref publication. Original
owner permission failures propagate unchanged; an incomplete source result is
not a backend denial.

Enumerate the complete common-to-head raw tree diff, with full object IDs,
literal NUL-delimited paths, rename detection disabled, no external diff or
text conversion. A rename retains both old/new path entries and its native
`originalPath`/change-tracking relationship; do not infer rename pairs from
similar content. Compare the native changed-path coverage with the local diff.
Ambiguous folder coverage, invalid paths, missing entries, contradictory supplied
blob identities or an incomplete mapping cannot claim complete source.

Resolve each changed entry at both exact commits, retaining mode, type, object ID
and size. Read actual bounded blob bytes, not working-tree files or remote
`isBinary` guesses. Classify NUL-containing or non-UTF-8 blobs as binary, ordinary
UTF-8 blobs as text, and symlinks/gitlinks explicitly as non-text entries without
following or expanding them. Empty/deleted sides remain absent, not empty text
blobs. Hash actual blob bytes with the repository's Git object format before
accepting their recorded IDs.

Return a versioned source manifest containing scope, immutable source revision,
opaque workspace reference, old/new entries and native path relationships,
completeness/errors, operation-produced timing and retained ref identities.
Do not return credentials, an editable source tip, a product build result or
review authorization. Source contents stay in the selected workspace/object
repository; cached manifests contain identities and classifications, not blobs.

The PR-domain function returns a separate versioned attachment envelope bound
to the input generation ID. **Do not rewrite the immutable metadata generation
or invent a new metadata observation time.** Existing comparison invalidations
still determine whether a prior source attachment is reusable. Scope, head,
iteration/common changes prevent relabeling old source; target-only movement
with unchanged source range does not by itself discard pinned source.

## Retention and failure ordering

After complete mapping/blob verification, the source owner pins head/common under
a deterministic review-source Git ref namespace scoped by workspace/scope/range.
Use one Git ref transaction, creating missing refs or verifying existing exact
values; never force-move a conflicting ref. This writes only the selected source
repository's refs through its existing owner, never the worktree checkout or
index. A read-only/reference-class repository must not be selected for this
writer operation.

Retain previous immutable refs. This slice adds no ref eviction or resource
release: the existing workspace/resource lifetime remains authoritative, and
attachment metadata is not a new resource claim or permission to delete one.
If a ref response is lost, the same deterministic refs permit exact readback;
an existing conflicting value refuses. A failed/uncertain ref transaction does
not become a ready attachment, even if some same-range refs are visible.

The operation has explicit finite caller-selected elapsed, path-count,
per-blob, total-blob and captured-output budgets. Exceeding one returns incomplete
source evidence, never a partial-ready mapping or hidden fallback. These are
actual Git/IO contracts, not limits on a delegate or the operator's work.
Subprocess failure/timeout/capture overflow must retire its owned process tree
through the existing process utility; no new background helper remains.

## Acceptance and rollout boundary

- [ ] Synthetic real Git repositories prove exact common/head, additions,
  edits, deletion, rename coverage, text/binary, symlink/gitlink classification,
  and complete native-to-local path/blob mapping without checkout/build/fetch.
- [ ] Wrong scope/range, incomplete changes, missing objects, unsupported
  paths/folders, contradictory IDs and budget exhaustion cannot claim readiness.
- [ ] Same-range retries preserve immutable refs, conflicting refs refuse,
  prior ranges remain retained, and owner permission errors remain original.
- [ ] Exercise the actual Worktrees reader through the PR-domain attachment
  envelope; metadata-only discussion incompleteness does not block ready source.
- [ ] Run contained source tests, relevant existing controls and source gates;
  drive contract and implementation PRs through assessed review/CI/merge.

Actual owner adoption, source fetching/credential use, product checkouts,
real backend qualification and runtime rollout remain with their current
owners and are not exercised by this source-only worker. A local manifest and
retained refs alone do not complete the parent end-to-end review objective.
