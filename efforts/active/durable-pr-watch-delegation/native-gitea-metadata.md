# Native Gitea author metadata

Back to the [CLI parity inventory](cli-parity-inventory.md) and
[canonical effort](README.md#author-publication-and-metadata-convergence).
Coordination: #10923; GitHub source baseline: #13199.

**Contract reviewed in #13225; source qualification below.** This closes part
of the PR-domain [author metadata intent](../../../visions/plugins/agent-pull-requests/README.md#complete-author-publication-and-metadata)
through the existing Worktrees/provider owner, following
[version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md)
and the [author custody boundary](author-publication.md#ownership-and-exclusions).
No new forge client, service, credential path or legacy-command retirement is
proposed.

## Selected boundary and owner

Expand existing explicit-target `pr-update --repo <registered-target>
--number <positive-number> [--title <text>] [--body <text> |
--body-file <utf8-file>] --json` and the dedicated native frontend's existing
`update` adapter. Reuse its selected `--worktrees-command`, target-qualified
`agent-worktrees.pr-author-capabilities`, version 1, and optional delivered
`--config`/`worker-policy-config` selection.

The Gitea implementation belongs behind `pr_metadata` and the existing
`GiteaProvider` HTTP owner. Resolve endpoint, repository, enabled policy and
account from the selected registered target, not the caller's repository or
an ambient token. Retain the current claimant and publication/resource
obligations; a local target checkout is unnecessary. Do not repair general
PR operations, cache warming, workflow checks or tool runtimes.

Only title and description are supported by this slice. Reviewer, work-item,
publication-origin and publication-label selections refuse before mutation;
they are not silently ignored or sent through another transport. Require a
supported field, nonempty title, valid UTF-8 body input and a positive PR
number. Empty description is deliberate, subject to source preservation.
Keep existing Azure DevOps and GitHub semantics unchanged.

## Source and native draft semantics

Reuse the existing [source-preserving metadata contract](native-github-metadata.md#preserve-source-do-not-reassign-it)
and shared `attribution_body` implementation rather than creating a second
marker parser. Preserve the selected PR's valid original marker, including its
root/encrypted fields, without adopting the updating caller or current
publication mode. Missing original source, including anonymous descriptions
without recoverable frozen source, refuses body editing in this finite slice.
Malformed, duplicate, unsupported or conflicting markers refuse too.
Literal Markdown examples remain authored content, not source authority.
Preserve exact authored Unicode/newline content under the shared composition
rules. Title-only editing leaves the entire body untouched even when source
repair is independently needed.

Gitea derives native draft state from title prefixes. A title edit must not
silently become a draft/ready operation or release a configured WIP policy.
Use authoritative observed draft state and the target's recognized title-prefix
policy to admit an edit preserving those semantics; refuse unsupported or
contradictory prefix evidence before that title write. Do not automatically
strip or insert prefixes. Verify native draft state after writing as well as
the requested title. Body-only editing remains available when safe title
semantics cannot be established. Test configured prefixes and incomplete
server evidence explicitly rather than assuming every installation uses the
same defaults.

## Effects, confirmation and compatibility

Read authoritative same-target PR identity, active lifecycle and required fields
through the existing Gitea transport. Refuse mismatched repository/PR identity,
malformed required values and closed/merged PRs before writing. Native drafts
remain editable under the title rules above.

Send only selected title/body fields to the existing PR endpoint. Preserve
source/base refs, draft/WIP state, reviewers, labels, votes, holds, consent and
completion settings. Do not create, push, comment, request review, resolve
threads, merge, register another watcher, settle claims or finalize.

Use the existing `agent-worktrees.pr-metadata`, version 1, per-action result
contract with `provider: gitea`, explicit repo/number, retained publication
identity, URL, `ok` and only the selected title/description actions.
The [existing status meanings](native-github-metadata.md#effects-readback-and-uncertainty)
apply: `not_attempted`, `confirmed` with boolean `changed`, `failed` for
established rejection, and `unknown` for potentially accepted but unconfirmed
effects. Confirm an already-satisfied field unchanged without rewriting.
Check before each write and verify afterward; stop after an unresolved action,
retaining earlier confirmed actions. Source drift cannot silently reassign
body provenance. A lost write response may be reconciled by readback, never
by automatic replay. An uncertain readback, malformed owner result or frontend
timeout remains nonzero/unconfirmed with the selected PR retained.

The forge has no atomic metadata compare-and-swap here. Preflight/readback
cannot prevent all intervening participant edits, guarantee future state or
bind a text edit to a reviewed code candidate. Do not add a code-head pin or
claim those stronger guarantees.

Expand and qualify the provider first, then advertise target-qualified
`metadata-update` and delivered-policy support for enabled Gitea bindings.
Extend the native frontend's existing strict per-action validation to this
provider; do not infer support from package versions or newer payload help.
Old callers retain existing behavior on new owners. New callers on old or
rolled-back owners receive the existing pre-effect unsupported outcome for
this optional operation. No automatic runtime update, raw forge fallback,
new prerequisite probe, deprecation or removal is introduced.

## Validation plan

- [x] Exercise the real explicit parser, claimant, target/config/account/endpoint
  resolution and Gitea transport with synthetic active/draft/terminal,
  wrong-target, malformed and unsupported-option controls.
- [x] Qualify title-only, body-only, combined, empty body, already-satisfied and
  quote-heavy multiline Unicode body-file inputs; assert that only selected
  fields are written and unrelated PR state/claims remain untouched.
- [x] Reuse source-preservation controls for distinct updater/source, original
  raw/codename/root/encrypted markers, missing/anonymous/conflicting/malformed
  source and literal examples. Prove real composed-body validation.
- [x] Prove draft/WIP preservation with default and configured prefixes,
  contradictory/missing server evidence and unsafe title transformations.
- [x] Inject pre-write refusal, authoritative rejection, lost response,
  partial confirmation, concurrent source changes and failed/mismatched
  readback; retain uncertainty and prove no automatic write replay.
- [x] Exercise the native frontend/owner process seam, strict action validation,
  mixed versions, additive fields, rollback and delivered-policy refusals.
  Preserve the existing Azure DevOps/GitHub regression packets.
- [ ] Record isolated source and native-platform qualification separately from
  release, installation and authorized live-provider mutation. No live write,
  credential/account probe, service deployment or HTTP mutation hosting is
  authorized by this proposed source contract.

## Journal

### 2026-11-18 - Reviewed contract; isolated implementation

Contract #13225 merged as `7c095883fe` after current-head Copilot approval,
all required checks and normal managed Maintainer completion. Implementation
uses the existing Gitea HTTP owner and shared source parser, retaining the
selected account, optional worker policy, claimant and per-action outcomes.
Native frontend validation extends the existing source-preserving result
contract to Gitea; it does not add another forge client or fallback.
Isolated source validation remains distinct from release, installation,
native-platform execution and authorized live-provider mutation.

The bounded supervisor and offline test-isolation containers pass 159
Worktrees metadata/author cases, including existing GitHub and Azure DevOps
regressions, and 130 frontend cases with five Windows-only skips on Linux.
The frontend controls run for both GitHub and Gitea, including the actual
subprocess/body-file seam and malformed, partial, missing-capability and
rolled-back-owner outcomes. A composed parser/target/account/provider fixture
reaches actual Gitea curl serialization with synthetic responses.

Each combined changed-title/body operation uses seven provider requests;
an already-satisfied repeat uses three reads and no write. These are isolated
work-count controls, not live-forge latency or native cold/warm measurements.
Whole-file strict typing passes Linux and Windows analysis for all seven
changed Python files. Source lint and install-contract/documentation guards
pass. No existing positively admitted unit-source binding changed.
