# Compact forge-comment identity

Tracking: #11128 under #6007.

Back to the [canonical effort](README.md). This is an **agent-recommended,
review-first contract**, not executing support or permission to rewrite live
comments. It extends
[compact comment provenance](../../../visions/harness-guidance/README.md#compact-forge-comment-provenance)
and the existing Author/Reviewer scope without replacing unfinished B3/A2,
provider, retention, CLI or installed acceptance.

## Accepted intent

Apply the operator's configured AI-attribution preferences and include
worktree-source/codename metadata on all agent-authored issue and PR comments,
replies, inline comments and review bodies going forward. Retain one stable
comment GUID and a separate GUID for each intentional create/edit; retries
reuse their original identities. Keep comments compact, avoiding redundant
attribution and metadata clutter. Prefer suppressing unchanged visible
attribution relative to a preceding comment where that is both valid and
reliable, not by weakening required disclosure.

Bodyless native votes/verdicts retain their native action semantics; do not
invent a filler comment just to carry comment metadata.

This generalized capture preserves the selected scope and identity lifecycle.
The design and sequencing below are recommendations, not new effect approval.

## Existing owners, separate meanings

- AI attribution retains audience/disclosure policy, sparse operator profiles
  and visible signature composition. Hidden metadata does not satisfy visible
  disclosure and is not inserted into templates that prohibit raw HTML.
- Worktrees retains actual source identity, assigned codenames, root provenance,
  privacy/anonymous policy and public-safe or encrypted source representation.
  Reuse its attributable preparation boundary, not a copied identity resolver
  or a new credential lookup. Missing required identity is explicit.
- The current publisher and protected intent/receipt owner retain final content,
  native effects, actor/target policy, unknown outcomes and retry custody.
  The current PR store/acquisition owns any synchronization projection.
  No parallel effect ledger, database, queue, watcher or forge client is added.

Ordinary comment-origin metadata must have its own versioned identity, distinct
from the existing PR-source marker. A reviewer's comment cannot become a
PR-origin refresh, transfer a PR claim or replace the author's worktree merely
because it carries source metadata. Existing managed PR-origin refreshes keep
their own contract.

## Compact prepared content

Compose the configured visible signature and one bounded hidden metadata line
before final preview, approval and immutable intent admission. Retain only the
necessary source reference/codename and the two public correlation identifiers;
do not repeat full worktree records, receipt payloads, histories or decorative
banners. Hidden HTML is still public through the API. Raw private machine,
worktree/session identifiers and paths cannot leak into public contributions;
privacy and anonymous choices remain effective.

Make preparation idempotent. Replace only this mechanism's own outer marker
when intentionally preparing an edit; never accumulate copies or strip quoted
examples, code fences, another author's marker or authored body content.
Malformed, duplicate, foreign or over-budget owned metadata refuses before an
effect rather than guessing or silently truncating. Do not post a second,
metadata-only comment.

Visible attribution follows the existing owner's effective profile and actual
audience policy. Do not append a second signature convention. Predecessor-based
suppression is only an optional presentation choice where that policy permits
it and existing complete, current, same-thread context suffices. Missing,
concurrent, stale or differently threaded context cannot hide a required notice.
Do not add a forge query solely to avoid repeating a short prefix. A comment
viewed independently must retain every disclosure its policy requires.

## Logical comment versus intentional write

Allocate and retain identities through the existing owner's admitted intent
before a first native attempt. One logical comment retains its comment GUID
through edits. A new reply is a new comment. Each intentional create or edit
gets a new write GUID; a retry, lost response, restart or ownership handoff of
that same pending write retains both GUIDs, its prepared bytes and original
source snapshot. Recovery cannot silently redecorate it as a new write.

An intentional later edit retains the comment GUID and records its new write
identity/source through the same owner. Source metadata describes the producer
of that write, not a newly invented PR owner. An explicit ordinary edit of a
known legacy owned comment can prepare metadata as part of its newly approved
body; this is not authority to scan/backfill existing comments or repair them
after publication without an independently permitted edit.

Keep the final prepared body covered by existing content/candidate/approval
checks. Do not generate IDs or change signatures after approval. Repeated
preparation of the same selected write must not mint another identity.
Where a current strict persisted or wire contract cannot represent required
bindings, expand the owning contract through review before enabling the
consumer; do not smuggle fields into an old strict schema.

## Synchronization and native proof

GUIDs correlate intended writes with observed native objects. They do not prove
authorship, permission, delivery, exactly-once effects or an echo. Bind a
confirmed mapping to the actual provider/endpoint/account/repository, issue or
PR/thread/comment, native actor and receipt/version through the existing owner.
Scope is not supplied by an untrusted comment body.

Quoted, copied, duplicate or wrong-target tags, partial pages, missing markers,
legacy comments and provider stripping/normalization remain explicit unknown
or conflict cases. Do not merge two native objects, attribute another author's
post, resend an uncertain effect or mark attention handled because GUIDs match.
The existing native object/version/content readback and original selected
operation remain authoritative. Preserve immutable captures, newer handled
frontiers and interest-specific echo qualification.

Any retained mapping is bounded metadata under the existing retention owner,
not another comment-body archive. Missing provenance stays unavailable rather
than being reconstructed from today's worktree or an equal-content comment.

## Reviewable delivery and exits

1. **C0 - Reviewed intent:** land the vision and this contract before new
   preparation, persistence or publishing behavior.
2. **C1 - Shared preparation:** extend the existing AI-profile and source
   owners with a finite pure preparation seam and compatibility declarations.
   Prove exact final content, compactness, privacy and repeat/edit identity.
   A pure helper is not all-publisher adoption or database synchronization.
3. **C2 - Actual publication consumers:** inventory issue/PR comments, replies,
   inline comments and review bodies across author/reviewer CLI, supported
   SDK/MCP consumers and the three providers. Compose the shared preparation
   through their real publication/intent paths. Each entry is explicitly
   delivered, legacy-compatible, unsupported or unqualified; guidance alone
   cannot claim that every publisher enforces it.
   The [bounded source inventory](comment-publication-inventory.md) records
   current owner paths and explicit preapproved-body composition qualification.
   Review the [existing-owner intent binding](comment-intent-binding.md) before
   admitting structured preparation or changing strict storage/receiver shapes.
4. **C3 - Observation/receipt correlation:** qualify actual preparation ->
   original native attempt -> retained outcome -> acquisition/mapping, including
   lost results and copied tags, through the existing owners.
   The [finite acquired-observation refinement](comment-observation-correlation.md)
   separates original confirmation, cached current-content correlation and
   original-version proof without adding a mapping database or native query.
5. **C4 - Adopter qualification:** update consumer guidance and qualify supported
   selected installations/providers separately. A source merge does not enroll
   a host or authorize live feedback, credentials, deployment or backfill.

Preserve independently installed peers and old baseline operations. Prefer
existing opaque-body operations where their semantics suffice; negotiate any
new syntax against the exact executing receiver, with explicit unsupported or
rollback results and no effectful probe/fallback. A missing optional preparer
cannot silently claim that required metadata was applied.

## Validation obligations

Focused source fixtures must exercise:

- All selected comment surfaces; configured disclosure/profile selection;
  public/private/anonymous source modes and missing identity; exact approved
  prepared bytes and native body preservation.
- Repeated preparation, create/reply/edit, original and fresh write GUIDs,
  lost replies/restart/handoff, legacy owned edits, final body/metadata byte
  bounds and no extra metadata-only publications.
- Same versus different predecessor/thread/author, unavailable or racing
  context and standalone display; no waived mandatory disclosure or new query
  merely to suppress a short signature.
- Spoofed/copied/quoted/malformed/duplicate markers, wrong account/target,
  partial observations, native actor/version/content mismatch and unknown
  outcomes; no false DB merge, PR-owner change, resend or handled/echo progress.
- Actual current/prior preparer and publisher boundaries, strict schema refusal
  and supported rollback. Keep pure/unit, contained source integration, native
  platform, live provider and installed evidence distinct.

No retrospective live-comment rewrite, deployment, new identity key or
credential, retained process/container, resource release or ownership takeover
is authorized by this source plan.
