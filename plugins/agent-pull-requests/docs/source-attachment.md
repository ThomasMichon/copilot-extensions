# Exact local source attachment

The [reviewed source slice](../../../efforts/active/pr-review-workspaces/source-attachment-slice.md)
has a source-library embedding, not an installed command, fetcher or service.

The normal workspace/resource owner first selects a **writable owned repository**
and its account/repository scope. It retains existing checkout-class, resource
custody, auth/input gates and current-owner checks. A bare source path, scope
dictionary or capability result is not authorization. Do not select an arbitrary
clone or a reference-class checkout for ref publication.

```python
from agent_worktrees.pr_review_source import LocalReviewSource
from agent_pull_requests.source_attachment import attach_source

source = LocalReviewSource(
    selected_repository_root, existing_workspace_reference, selected_scope,
    checkpoint=existing_owner_checkpoint,
)
attachment = attach_source(
    generation, source, budget=owner_operation_budget,
    max_paths=20000, max_blob_bytes=1024 * 1024,
    max_total_blob_bytes=16 * 1024 * 1024, max_output_bytes=8 * 1024 * 1024,
)
```

These are actual finite Git/IO limits, not delegate permissions or read counts.
Git must support explicit `--no-lazy-fetch`; unsupported Git fails honestly,
without trying another backend or acquiring missing objects. Prepare exact
head/common objects through the existing source owner before invoking this API.
There is no checkout, working-tree read, merge, build or dependency restore.

`attach_source` requires complete, consistent PR/iteration/change evidence, not
complete discussions or policies. It returns a separate versioned envelope
bound to the original generation ID: `ready`, `incomplete`, or `unavailable`.
The original generation, its observation times and metadata completeness are
unchanged. No envelope is a review-eligibility or posting-authority grant.

The Worktrees reader verifies exact common/head commits, full raw tree changes,
native path coverage and any supplied blob IDs. It reads actual bounded blobs,
checks Git object hashes and preserves exact bytes rather than normalizing
newlines. UTF-8/NUL-free blobs are text; other blobs are binary. Zero-byte files
remain existing text blobs. Deleted sides are absent; symlinks and gitlinks are
explicit, never followed or expanded. Renames retain separate old/new tree paths
and their native change-tracking/original-path relationship.
An original/rename path must resolve to an actual common-side object, not another
added path. Tree lookups are argv-batched and unique blob sizes/contents use Git's
batch protocols; physical process/checkpoint overhead does not grow per path.

The source manifest includes workspace reference, revision, path/object/mode/size
classification, original native changes, completeness/error codes and measured
elapsed time. It does not embed blob contents or local filesystem paths.
Captured stderr is not echoed into source failures.

Only after complete mapping verification does the owner atomically create or
verify deterministic immutable head/common refs. Conflicting refs are never
force-moved. Prior ranges remain retained; cache rebuild has no ref-retirement
authority. The existing workspace/resource lifetime remains authoritative.
Symbolic retained refs are refused, including dangling symbols. The transaction
uses no-dereference updates and readback repeats the symbolic-ref check, so
another ref cannot redirect publication outside the source namespace.
Internal Git commands disable repository hooks through Worktrees' existing
mechanical-operation hook policy, so a reference-transaction hook cannot add
effects. Git's long-path option is scoped to these commands, not persisted as
machine or repository configuration.

Ref IO failure or timeout may leave same-range refs present. The result remains
incomplete with `effects: local_refs_possible`; retry the same range and verify
its same deterministic refs, not a new identity or forced replacement. Actual
owner permission failures and unconfirmed process cleanup propagate unchanged.

Existing generation comparison invalidations still apply: new source
head/iteration/common evidence cannot relabel a previous source manifest.
Discussion-only or target-only changes with the same authoritative source range
do not themselves change those retained immutable objects. This initial API
revalidates the selected range on every call; it does not claim automatic source
attachment reuse, eviction or installed-owner adoption.

For file/directory transitions, an actual tree container at a path is not a file
side. The manifest retains absent file sides plus the complete separately mapped
child paths; it does not misclassify the container as a missing blob.
