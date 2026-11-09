# Selected native report-owner adoption

Back to the [existing-owner report contract](owner-report-read.md) and
[canonical effort](README.md).

**Contract reviewed in #11681; finite optional source merged in #11686 under
#11680. Installed hosting and live-provider acceptance remain separate.**
This agent-recommended slice closes part of
[one service contract](../../../visions/plugins/agent-pull-requests/README.md#cli-through-one-service-contract).
It follows the existing
[version-skew pattern](../../../docs/patterns/service-contract-evolution.md).
Provider, credential, configuration, Gateway and process owners remain.

## Source findings and finite choice

`pr_read_context.read_report` uses the resolved target's provider and endpoint,
then `account_token_for_slug`. That credential helper intentionally retains
legacy ambient authentication when the selected GitHub account is active,
absent or an unconfigured derived-owner hint. `resolve_token` can also continue
after an empty or failed configured helper. Those semantics remain valid for
their existing callers; they do not establish a service report binding.

The report builders already preserve the native result. However, the generic
provider runner inherits environment and uses a separate timeout per child.
GitHub thread reads ignore `api_base` and call a relative GraphQL route.
Wrapping `read_report` in matching callback labels would therefore leave
account, endpoint and enclosing-budget selection unqualified.

Select one optional **GitHub.com-first in-process owner adapter**, supplied by
the existing trusted host, that produces the current `ReportBinding`.
Reuse Worktrees target/account resolution, report builders, GitHub provider
semantics and caller-owned process/cleanup machinery. Extend their owning read
seams only where needed; do not build a parallel forge implementation, generic
command executor, credential service, daemon or installed-host loader.
Gitea, ADO and Enterprise-host adoption remain explicit unsupported selections
for this adapter, not invitations to use a different credential or native path.
Existing native reports for those providers remain available unchanged.

## Native selection and acquisition

The host supplies its already authorized target and source configuration owner,
not an HTTP-selected config path or executable. Resolve the exact target through
the existing one-shot configuration/related-target owner and strict repository
registry. Refuse ambiguous, unavailable or changed target policy; never inherit
an unrelated active project's provider or endpoint.

For this finite adapter, require an explicitly configured repository account or
account-map entry and the GitHub.com endpoint. Do not select an account from the
repository owner hint or active CLI login. A missing configured endpoint may use
the provider's documented GitHub.com default, but never ambient `GH_HOST`.
Malformed or non-GitHub.com endpoints refuse before credentials are acquired.
The host's report scope uses the provider's canonical `github.com` authority.
Configured account equivalence follows the existing case-insensitive account
rules without changing the report protocol's exact scope comparison.

Acquire that selected account's existing GitHub CLI-store credential explicitly,
including when it is the active account. Reuse the strict selected-account and
authentication-observation owners; adapt their source seams rather than calling
the legacy ambient-fallback helper. Configured token commands, token environment
bindings, missing credentials and failed lookups refuse this finite adapter.
Do not run an interactive login, mint new access or consult another account.

Before any body-bearing report read, authenticate the same selected credential
against the pinned GitHub.com identity route and verify the returned login
matches the selected account. Credential source labels alone are insufficient.
Keep that credential fixed for the finite report; expiry, rejection or rotation
requiring replacement fails the exchange rather than silently switching.
The next independent exchange may acquire and verify new material.
This account check is a routing prerequisite, not a review generation:
retain the existing report profile and add no principal, write or eligibility
claim to its wire response.

Pin every provider operation to the selected endpoint and credential. Remove
inherited token, host, repo, debugging and alternate-transport overrides;
relative routes, CLI config routing, extensions or ambient login must not
redirect the read. Qualify the actual GraphQL and unified-diff process seams,
including the CLI's possible auxiliary requests, rather than only asserting
an endpoint label. Preserve the existing thread normalization and exact diff
text; this slice does not upgrade the providers' completeness guarantees.

## Lifetime and integration

Expose only an optional source embedding through the existing report reader.
No new HTTP fields, route, CLI selector or report protocol generation is needed.
The host still owns caller admission, target mapping and withdrawal. Resolve and
recheck native target/account/endpoint selection under that live owner before
credential acquisition, before each provider request and before publication.
Refuse replacement instead of executing the newly selected binding.

All configuration, account, credential, identity and report work consumes one
finite enclosing request budget, with the original live checkpoint. Reuse the
caller-owned process primitive and bounded stdout/stderr capture; no per-stage
timeout reset, abandoned worker or background retry. Configuration/credential
helpers that cannot meet that boundary are unavailable, not exempt.
Withdrawal or disconnect prevents later requests and publication while the
original Gateway task retains custody until every owned child and cleanup
settles. A delayed cleanup is not a successful drain.

Keep the current full-report, original-exit, byte-limit and sanitized-failure
contract. Do not retain tokens, identity responses or report bodies in a cache,
journal, telemetry or diagnostic exception. Capability support comes from this
actual configured adapter; it is not evidence that an installed host adopted it
or that live-provider qualification passed. Missing/rolled-back adapter support
refuses only this optional path, with no second dispatch or native fallback.

## Plan and validation

Review this proposal before runtime expansion. Then implement and qualify the
finite source adapter; ordinary native commands and default hosts keep their
current behavior. New isolated fixtures, not operator credentials, prove:

- [x] Actual target/configuration and strict configured-account resolution;
  neutral CWD, distinct caller/target, ambiguity, account-map changes, wrong
  provider/endpoint and rejected legacy credential bindings before acquisition.
- [x] Actual selected-store acquisition and authenticated account matching,
  including active-account mismatch, missing/empty/failed credentials, hostile
  ambient routing and credential replacement without fallback or body reads.
- [x] Existing Worktrees thread/diff builders through the actual scoped provider
  and process seams; complete nested/additive reports, lossless text, unknown
  completeness, native failures and owner-exit disagreement remain truthful.
- [x] One budget and live binding across configuration, credential and provider
  stages; withdrawal, timeout, disconnect and delayed cleanup retain original
  task custody without later requests or publication.
- [x] Optional old/new/rollback behavior through the real Gateway/client seam;
  unsupported providers, default-host refusal, unchanged native/workspace/watch
  controls and no retained report/credential data or mutation.
- [x] Record source and platform evidence separately from installed hosting,
  live GitHub credentials, Gitea/ADO/Enterprise, large-report transport and full
  CLI/HTTP acceptance. Return to the parent rather than closing it on this leaf.

### Finite source evidence

The optional `NativeGitHubReport` composes actual configuration, strict
repository-account selection, explicit CLI-store acquisition, authenticated
login matching and pinned existing-provider reads under one original request
budget and cleanup task. It adds no installed loader or default-host selection.

Contained Linux qualification includes the final 97-case target/committed-policy
packet and the 4,665-case actor-free packet at the approved implementation head.
Earlier overlapping source packets cover 88 report/Gateway/TLS cases and 22
actual native-adapter cases. These use new fixture state and synthetic
credentials/provider responses, not an operator account or retained host.
Current-head review and required CI passed before the source merge.

Strict acquisition refuses unavailable policy, failed registry-derived anchors,
duplicate keys, unresolved committed Git state and unusable policy links.
Configured account spelling is retained without borrowing sibling identities.
Both publication handoffs retain off-loop selection and original cleanup
custody, followed by a lightweight final deadline/permission/binding check.
Provider errors remain native reports; unavailable checkpoints and transport
cannot be normalized into HTTP success.

Native Windows execution, installed packaging/hosting, live GitHub credentials,
Gitea/ADO/Enterprise, large-report transport and complete CLI/HTTP acceptance
remain at their canonical parent gates. See the
[source merge journal](journal/2026/11.09%20Native%20report%20owner%20source.md)
and [canonical Plan/Validation](README.md).
