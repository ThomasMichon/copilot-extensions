# Explicit native reviewer request

Back to the [CLI parity inventory](cli-parity-inventory.md) and
[canonical effort](README.md).

**Contract reviewed in #11645; finite native source merged in #11647.** This finite
slice closes part of the PR-domain
[provider-neutral surface](../../../visions/plugins/agent-pull-requests/README.md#first-class-provider-neutral-pr-surface)
through the existing Worktrees/provider owner. It follows
[version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md)
and the [author custody boundary](author-publication.md#ownership-and-exclusions).
Source qualification is not installed acceptance or permission to write.

## Baseline and proposed boundary

Existing Worktrees `pr-nudge [worktree]` selects the active tracked PR,
reconciles terminal state and requests its configured automated reviewer.
Unbound or unmapped reviewers and unsupported providers are normal no-ops.
Its GitHub provider uses `requested_reviewers`, not an issue comment or
`@copilot` mention. API acceptance does not guarantee another review.
The dedicated native PR client has no corresponding command.

Do not overload that tracked selector with explicit-target semantics. Add the
optional **`reviewer-request`** operation to the existing
`agent-worktrees.pr-author-capabilities`, version 1, target-qualified map:

| Surface | Proposed form |
|---------|---------------|
| Worktrees explicit target | `pr-author-nudge --repo <registered-target> --number <positive-number> --json` |
| Dedicated native client | `nudge --repo <registered-target> --number <positive-number> --worktrees-command <selected-runtime> --json` |
| Optional delivered policy | Existing native `--config` selection with `worker-policy-config` support |
| Compatibility path | Existing tracked `pr-nudge [worktree]` retains its syntax, defaults, output, exit behavior and no-op meaning |

The initial declaration is true only for the selected, enabled GitHub binding;
it means this boundary can request a supported reviewer or report its
unbound/unmapped no-op. Azure DevOps and Gitea remain unqualified and do not
advertise this optional operation. Their existing tracked no-ops remain usable.
Absent or non-true support refuses the new operation before dispatch, without
disabling other operations. This deliberate explicit-path refusal is not
claimed equivalent to a tracked unsupported-provider no-op.

Resolve provider, endpoint, account and enabled policy from the actual target,
never from a same-number tracked PR or the caller's default repository.
Preserve existing claimant admission without requiring a target checkout.
Reject missing/invalid selectors, tracked selectors and unknown options
strictly. Explicit invalid or mismatched policy must not fall back to ambient
configuration. Reviewer selection comes only from the target's existing
`pr.reviewer` binding; there is no arbitrary reviewer or comment-body option.

## Effects and outcomes

For a mapped reviewer, obtain authoritative same-target PR identity, lifecycle
and draft evidence before requesting. Missing or inconsistent evidence, a
terminal PR or a draft refuses before mutation. An unbound/unmapped reviewer
is a known no-op and need not fetch credentials or probe a forge.

Perform at most one review-request call through the existing provider owner.
Do not duplicate its transport in the frontend. The payload must not create
comments, mention a coding agent, change draft state, holds, consent, votes,
metadata, source, completion settings or merge state. Do not remove unrelated
reviewers, resolve discussions, create a PR, settle a claim or finalize.
Capability reporting grants none of the required effect authority.

The new explicit result is `agent-worktrees.pr-review-request`, version 1,
with exact `repo`, positive `number`, selected `provider`, `ok`, `supported`,
`requested` and `actions.review_request.status`:

| Status | Meaning |
|--------|---------|
| `not-attempted` | Admission, applicability, evidence or contract support refused before the request |
| `no-op` | The capable selected owner establishes no configured/mapped reviewer; no request was made |
| `accepted` | The provider authoritatively accepted the one request; not evidence of a queued, running, fresh or successful review |
| `failed` | An authoritative response establishes rejection without acceptance |
| `unknown` | A request may have been accepted but its response or result is unavailable, malformed or inconsistent |

Only `accepted` and `no-op` return `ok: true` and exit 0.
Only `accepted` returns `requested: true`; `no-op` reports `supported: false`.
Refusals before provider resolution retain the selected PR identity and may
use a null provider; attempted outcomes require a valid selected provider.
Preserve actionable diagnostics and do not infer a known rejection from a
generic subprocess failure or nonzero wrapper exit.

The native client validates the versioned result and selected identity. A
lost response, timeout or malformed/mismatched owner result after dispatch is
unknown, not success or a harmless no-op. Do not retry automatically, try an
alternate transport or replay a legacy command. Observe the same PR before
deciding whether another intentional request is warranted; absence of a new
review does not prove that the earlier request was rejected. Existing status
and watch owners remain responsible for actual review evidence.

The preflight is not atomic compare-and-swap. Another participant can update
or close the PR while the request is in flight; this operation neither pins
the future review to a head nor grants a verdict or merge permission.

## Expansion and qualification

Expand the actual Worktrees/provider owner and its declaration first, then
conditionally enable the dedicated native client against that selected
runtime. Package versions or a newer payload cannot establish support.
Old callers retain the tracked baseline on new owners. New callers on old or
rolled-back owners receive the declared pre-effect optional-feature refusal.
No legacy syntax is deprecated or retired here. HTTP mutations, installed
consumer migration and three-provider parity remain later acceptance.

- [x] Qualify the explicit parser, claimant, target policy and actual provider
  with mapped, unbound/unmapped, unsupported, terminal, draft, malformed and
  wrong-target controls; preserve the existing tracked nudge regression suite.
- [x] Exercise the actual native frontend-to-Worktrees/provider path with a
  synthetic forge and prove a single request, exact configured reviewer and
  no comment, source, consent, vote, hold or completion side effects.
- [x] Cover authoritative rejection, lost response, nonzero subprocess,
  malformed/mismatched owner output and timeout; preserve honest uncertainty
  and prove no retry, fallback or duplicate publication.
- [x] Exercise old/new callers, old/new selected runtimes, rollback, additive
  capability fields, strict invalid arguments and delivered-policy refusal.
  Include a real process boundary, not only argv-forwarding assertions.
- [ ] Record isolated source and native-platform qualification separately from
  authorized live-provider, installed and HTTP evidence; leave parent
  acceptance open until its own complete matrix is satisfied.

The finite isolated Worktrees/provider/legacy packet passes 398 cases.
The final request-owner subset passes 33, overlapping that packet and adding
four explicit target/policy refusal cases. Reserved worker bindings require
their explicit marker before policy resolution. The coupled
request/author/readiness/worker-policy packet passes 180 cases, including eight
marker-refusal controls across discovery and author operations. The native frontend packet passes
116 with five Windows-only cases skipped on Linux, including an actual
subprocess for capability and worker-policy forwarding. Synthetic provider
responses exercise the actual frontend, parser, target resolver and GitHub
transport owner; no live request is made. Cleanup is verified. Windows,
live-provider, installed, HTTP and broader CLI parity remain open.
