# Native GitHub author metadata

Back to the [CLI parity inventory](cli-parity-inventory.md) and
[canonical effort](README.md#authorreviewer-service-perspectives).
Coordination: #10923.

**Contract reviewed in #13195; finite source qualification below.** This slice closes part
of the PR-domain
[author publication and metadata intent](../../../visions/plugins/agent-pull-requests/README.md#complete-author-publication-and-metadata)
through the existing Worktrees/provider owner. It follows
[version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md)
and the [author custody boundary](author-publication.md#ownership-and-exclusions).
It does not introduce another forge transport or retire legacy commands.

## Baseline and selected boundary

Worktrees exposes explicit-target `pr-update`; the dedicated native
client forwards `update` after checking the selected runtime's
`agent-worktrees.pr-author-capabilities`, version 1. Its `metadata-update`
operation selects the Azure DevOps metadata owner or the GitHub title/body
owner. The GitHub owner preserves the source marker supplied at PR creation;
raw description replacement outside this operation can still remove it.

The existing operations supply this boundary without another prerequisite command:

| Surface | Selected form |
|---------|---------------|
| Worktrees owner | `pr-update --repo <registered-target> --number <positive-number> [--title <text>] [--body <text> \| --body-file <utf8-file>] --json` |
| Dedicated native frontend | Existing `update` with the same target, number and field options, plus its existing selected `--worktrees-command` |
| Optional delivered policy | Existing `--config` and `worker-policy-config` support; invalid or mismatched policy never falls back to ambient configuration |
| Capability | Existing target-qualified `metadata-update` boolean, true for enabled Azure DevOps and GitHub bindings |

Azure DevOps retains its current reviewer, work-item, publication-label and
metadata semantics. The initial GitHub slice supports title and description
only. Reviewer/work-item/origin/label requests are explicit pre-effect refusals
on this path, not ignored options or permission to try a different transport.
At least one supported text field is required. Unknown options, invalid target
selectors, empty titles and invalid UTF-8 files refuse before a forge write.
An empty description is intentional, subject to retaining required source
metadata.

The managed caller retains its existing admission and PR/resource obligations.
Resolve provider, endpoint, account and enabled policy from the actual registered
target, not the caller's default repository or a same-number tracked PR.
An explicitly selected worker policy retains its existing target checks.
No target checkout is required by the metadata operation.

## Preserve source, do not reassign it

A description update must retain the selected PR's original source attribution.
It must not regenerate attribution from the updating caller, change the frozen
publication mode because current configuration differs, or treat a matching
marker as authentication or ownership.

The existing source-attribution owner supplies the composition and privacy
rules. Preserve a valid published source marker and its optional root/encrypted
fields rather than replacing it with a new caller marker. If recovery is needed,
use only an unambiguously matched original tracked PR's frozen policy and source;
otherwise refuse the description action before mutation with a specific repair
diagnostic. Missing or ambiguous source data is not an anonymous opt-out.
An established anonymous PR must retain that policy rather than acquire the
updater's source identity.

Conflicting caller-supplied markers, duplicate or malformed source markers and
unsupported provenance must not silently overwrite the original identity.
Fenced, quoted, indented and inline examples are authored content, not authority
to select a source. Do not delete examples to manufacture a valid marker.
Preserve the authored Markdown and exact Unicode/newline bytes apart from the
existing documented source-marker composition. Verify the resulting composed
description, not the uncomposed input. A title-only update leaves the entire
description untouched, including any independently repairable metadata defect.

This slice prevents attribution loss through the managed update path. It does
not make arbitrary raw forge edits safe, rewrite creation input files, repair
historical workflow checks, or change `refresh_source_attribution`'s pushed-head
comment behavior. Those are distinct remediation paths.

## Effects, readback and uncertainty

Before mutation, obtain authoritative same-target PR identity, lifecycle and the
requested current fields through the existing GitHub provider transport.
Malformed or mismatched identity and terminal state refuse before writing.
Native drafts remain editable; metadata editing does not mark them ready.

Write only the requested title/description fields to that existing PR. Preserve
source/base refs, draft state, reviewers, labels, votes, holds, consent and
completion settings. Do not create or push, request review, comment, resolve
threads, merge, register a second watcher, settle claims or finalize.
Capability reporting grants none of the required effect authority.

Read before writing and verify after dispatch. An already-satisfied requested
field is confirmed unchanged, not rewritten. Reconcile a lost write response
by authoritative readback without automatically replaying it. Partial
confirmation remains partial; later actions do not run after an unresolved
earlier action. The GitHub `agent-worktrees.pr-metadata`, version 1, per-action result carries selected
`repo`, positive `number`, `provider`, URL, `publication_retained`, `ok` and
`actions.title` / `actions.description`:

| Status | Meaning |
|--------|---------|
| `not_attempted` | Admission, applicability, source preparation or evidence refused before that field's write |
| `confirmed` | Authoritative same-target readback matches the composed requested value; `changed` distinguishes a write from an already-satisfied field |
| `failed` | An authoritative response establishes rejection without an uncertain accepted effect |
| `unknown` | A write may have applied, or post-dispatch target/lifecycle/value evidence cannot confirm it |

Only complete confirmation returns `ok: true` and exit 0. Other outcomes remain
nonzero with actionable diagnostics and known PR identity retained. A subprocess
failure is not by itself proof of a rejected or unapplied write. A timeout,
malformed result or lost owner response at the native frontend stays unconfirmed.
Observe the same selected PR before another intentional attempt; do not retry
blindly or fall back to raw `gh pr edit`.

The API does not provide an atomic compare-and-swap for this operation. Preflight
and readback do not exclude intervening participant edits, guarantee future
state, or pin a metadata update to a reviewed code candidate. Do not advertise
those guarantees or add an unrelated exact-head requirement to title/body edits.

## Expansion and qualification

Expand and qualify the actual Worktrees/provider operation before advertising
GitHub support, then use the existing capability-gated native frontend.
Package versions, newer payload help and a different executable do not prove
support. Old callers retain their semantics on new owners; new callers on old
or rolled-back owners refuse this optional GitHub operation before dispatch.
The existing Azure DevOps metadata path remains usable. No deprecation or
removal occurs here.

- [x] Qualify the actual explicit parser, claimant, target/account/endpoint
  resolution and provider transport with synthetic open/draft/terminal,
  wrong-target, malformed and unsupported-option controls.
- [x] Exercise the real native frontend-to-owner path with title-only,
  description-only and combined updates, quote-heavy multiline Unicode body
  files, empty descriptions, already-satisfied fields and unrelated-field
  preservation. Prove zero create/push/comment/consent/review effects.
- [x] Prove source preservation with an updating caller different from the
  original source, frozen modes, root/encrypted metadata, anonymous policy,
  missing/conflicting/duplicate/malformed markers and literal examples.
  Recovery may refuse when original source is unavailable; it must not invent it.
- [x] Inject pre-write refusal, authoritative rejection, lost response,
  partial success, failed/mismatched readback and concurrent edits. Retain
  uncertainty and selected identity; prove no automatic replay.
- [x] Cover old/new callers, selected owners, rollback, additive capability
  fields, strict invalid arguments and delivered-policy refusal through a real
  process seam. Preserve the existing Azure DevOps and tracked-attribution tests.
- [ ] Record isolated source, native-platform, released, installed and separately
  authorized live-provider evidence distinctly. HTTP author mutation hosting,
  Gitea metadata and complete CLI retirement remain parent acceptance, not this
  finite source leaf.

## Journal

### 2026-11-17 - Reviewed contract and isolated source qualification

Contract #13195 merged as `ebbc42ad46` after current-head Copilot approval,
required green checks and normal managed Maintainer completion. The finite
implementation retains the existing target/config/provider owners and leaves
the full parent Plan and Validation Plan open.

The isolated source packet passes 102 Worktrees metadata/author cases, including
the existing Azure DevOps path. The final focused GitHub subset passes 48 after
adding post-write identity/lifecycle/value and open-literal-boundary controls.
The native frontend packet passes 108 with five Windows-only cases skipped on
Linux, including a real subprocess/file-forwarding boundary. The combined
frontend/parser/target-resolver/provider fixture uses synthetic forge responses.
The final resolver-specific control also passes through the actual explicit
worker-target resolver, rather than a replacement resolver response.
All runs use the bounded supervisor and offline test-isolation containers with
verified cleanup; these are not live-provider or installed-runtime results.

Missing original attribution, including anonymous descriptions, refuses body
editing in this finite implementation. No local frozen-record recovery is
implemented. Existing published raw/codename/root/encrypted markers are retained,
not regenerated from current policy. CommonMark parsing preserves literal
examples, and composition refuses when an open literal boundary would hide the
retained marker. The frontend retains the selected PR and unknown action state
after a lost or malformed GitHub owner response. Other provider and parent
acceptance remains open.
