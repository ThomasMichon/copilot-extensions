# Existing-owner HTTP thread and diff reads

Back to the [CLI parity inventory](cli-parity-inventory.md) and
[canonical effort](README.md).

**Contract reviewed in #11665; finite optional source merged in #11668;
installed adoption remains pending.**
The bounded source claim is #11664 under #6007.
The [selected native owner source](native-report-owner.md), merged in #11686
under #11680, supplies finite GitHub.com account/endpoint/budget composition
without claiming installed or live-provider support.
This source slice closes part of
[one service contract](../../../visions/plugins/agent-pull-requests/README.md#cli-through-one-service-contract)
using the existing authenticated Gateway connector and PR-service request owner.
It follows the
[version-skew pattern](../../../docs/patterns/service-contract-evolution.md).
Provider/account, credential, source-control, storage and lifecycle owners remain.

## Baseline and selected boundary

The [native thread](native-thread-read.md) and
[native diff](native-diff-read.md) frontends preserve the existing Worktrees
reports without a checkout, watcher or body archive. Their reports are not
principal-bound review generations. A successful native invocation does not
identify the effective forge principal, prove a current candidate or authorize
review submission.

The existing Gateway serves replay, attention and review workspace operations.
Workspace load/sync acquires a scoped generation and publishes through the
existing cache/CAS and conversation owners. Its ADO-first `NativeReadHandler`
uses the finite evidence exporter, typed renewable credentials and observed
`connectionData.authorizedUser.id`. `OwnerReadHandler` provides a separate
protected-owner generation embedding. Neither is an interchangeable generic
thread/diff transport. Do not feed a raw report into either generation builder
or attach its Gateway account label as observed native-principal evidence.

Add one optional **existing-owner report embedding**, a thin client and source
CLI facade to the existing Gateway. Do not add a forge client, listener, daemon,
credential issuer, installed-owner loader or persistent report store.
The first slice selects trusted synchronous in-process acquisition callbacks;
it does not configure executable paths or credentials from HTTP/CLI JSON.
Ordinary native `threads`, `diff`, tracked `pr-diff`, workspace operations and
standalone watch hosting remain unchanged.

## Actual owner and caller binding

A trusted host supplies a fixed report binding for an already admitted
`(Gateway principal, target)` and its existing `ConsumerScope`. The binding
retains the configured provider, endpoint, repository and account selection,
an owner context reference, a live checkpoint and a finite synchronous
collector. Construction or deserialization of those labels does not authorize
anything. A request cannot supply a callback, owner, endpoint, account,
credential, configuration file, command or alternate repository.

The embedding belongs inside an existing authorized read pipeline. Its resolver
must establish the actual selected target/account/endpoint through that owner's
normal configuration and credential gates **before acquisition**. The live
checkpoint preserves that binding, caller permission and withdrawal/replacement
checks through acquisition and response publication. An account or endpoint
change refuses rather than reading another binding and discovering the mismatch
afterward. The collector reuses the established Worktrees report/provider path
and its credentials; it must not fall back to ambient auth, borrow a different
account or mint new access to fill the interface.

Gateway admission and `ReplayBinding` still bind the caller, service slot,
namespace, consumer, account and repository. Validate the embedding against the
configured scope and recheck the current binding before returning a report.
These are configured routing/authorization facts, **not** an observed provider
principal. The response profile is `owner_executed_report/v1`, not either review
evidence acquisition profile. Do not populate `principal_id`, generation IDs,
source attachments, review eligibility or write capabilities from this path.
Any future native-command adopter must separately qualify its real target and
account selection; forwarding a report with a matching repo name is insufficient.

## Optional wire and client

Use the existing Gateway handler, `ServiceCaller`, request lifetime and
task/drain owner. The additive protocol is
`agent-pull-requests.report-read/v1`, served as authenticated JSON POST requests:

| Route | Request inside the existing binding envelope |
|-------|---------------------------------------------|
| `/v1/read/<target>/capabilities` | `{}` |
| `/v1/read/<target>/threads` | positive integer `number`, finite positive `budget_seconds` |
| `/v1/read/<target>/diff` | positive integer `number`, finite positive `budget_seconds` |

The envelope contains exactly `binding` and `request`. Unknown request fields,
booleans in integer positions and nonfinite numbers refuse before collection.
The request binding contains the exact existing `ReplayBinding` fields plus
`report_scope`, the caller's selected provider/endpoint/account/repo mapping.
Compare both before owner resolution or collection, and retain those expectations
through publication. This prevents a remapped endpoint/provider with otherwise
unchanged replay labels from being read before the client discovers a mismatch.
Responses keep the exact existing `ReplayBinding` and their separate report
scope; the replay and workspace protocols are not changed.
Capabilities come from the actual configured executing handler and identify
this protocol, the report profile, supported operations and the existing
request/response byte limit. They describe the embedding, not universal provider
support: an ADO diff can still return the native unsupported report.
Absent embedding returns unsupported without provider IO. An explicitly
selected client discovers this contract through the existing connector before
issuing a report request; cache selection only for that selected live binding.
Old receivers, missing/invalid declarations and provider rollback give a
truthful optional-feature refusal, with no native or different-forge fallback.

A valid read response contains the protocol, exact `ReplayBinding`, operation,
requested number, selected report scope/profile, local observation interval,
`effects: none`, the original collector exit code and the **whole valid native
report**. The interval is not atomic-snapshot or server-clock evidence.
Validate the expected scope, operation, number, profile and report at both
boundaries. Retain the owner exit independently from the report; do not make a
successful-looking body hide an unsuccessful acquisition.

Reuse `agent-worktrees.pr-threads` v1 and
`agent-worktrees.pr-diff-read` v1 validation from the native clients:
exact repo/PR, nonempty provider matching the selected provider, integer version,
boolean support, boolean-or-null completeness, string error and the corresponding
thread-object list or diff string. Preserve additive and nested fields, unknown
completeness, candidate-change evidence, partial reports and exact UTF-8 text,
including CRLF/bare CR. Neither a body length nor an empty collection proves
completeness or current-candidate freshness.

HTTP 200 means a valid report exchange, including valid provider refusal/error
reports; it does not mean acquisition succeeded. Return a nonzero client outcome
if the owner exit is nonzero, support is false or the report has an error.
Malformed or missing reports are unavailable, never invented empty successes.
Keep existing 400 input, 403 permission, 409 binding mismatch, 404 unsupported
and 503 unavailable distinctions. Sanitize transport/owner failures without
echoing credentials, raw helper diagnostics or malformed report bodies.

The source CLI facade follows the existing workspace host-injection pattern:
`report capabilities|threads|diff`, with bounded request JSON for reads.
Its host is supplied only by the actual embedding, never loaded from request
data. Without a host it reports unavailable and does not discover an ambient
service or executable. It emits the full validated exchange and a truthful
exit status. Existing native command syntax and output remain unchanged.
This is a source embedding facade, not ordinary installed HTTP client migration.

## Budgets, custody and retention

Use the smaller of caller and actual owner's finite work budgets. Check caller
expiry, request lifetime, selected owner and deadline before acquisition, at
the owner's existing read checkpoints and before response publication.
The collector receives the remaining deadline/checkpoint and owns its actual
transport or child-process settlement; a wrapper timeout is not cancellation
or a cleanup receipt. Cancellation/withdrawal prevents publication and later
work, but the Gateway keeps its original task until synchronous acquisition
and any owned cleanup settle. Draining may time out rather than falsely release
that custody. No background retry, second dispatch or abandoned worker is added.

The complete encoded v1 exchange must fit the current caller-aware handler/report
limit of 32 KiB. The underlying channel's separate 8 MiB response ceiling does
not qualify this handler path. Validate before response construction and enforce the same
limit at the client. A larger native report is explicitly unavailable over
this finite wire; do not truncate its diff, drop additive fields, relabel it
partial, change the physical frame limit or persist chunks to make it fit.
The [size-qualified response proposal](bounded-report-response.md) treats the
required optional handler expansion and larger report profile separately.
Large-report transport is a remaining parity obligation, not delivered here.
Native readers retain their separate existing limits.

Report bodies exist only for the current bounded exchange. Do not write them
to replay/conversation events, generation caches, telemetry, diagnostics or
durable receipts. Reading must not register subscriptions, advance cursors,
ACK attention, allocate source, submit/resolve comments, vote, requeue, merge,
alter claims or reconcile tracked lifecycle. The preexisting store/connector
remains its owner; this feature performs no provisioning/adoption.

## Plan and validation

Review this contract before implementation. Then deliver one optional source
embedding and its client/facade through the existing Gateway. Default hosts
advertise no support until an actual owner is supplied. No live credentials,
retained-host enrollment, installation, rollout or legacy retirement is implied.

- [x] Exercise the actual authenticated Gateway/SDK request and source CLI seams
  using new isolated fixtures, distinct caller/target bindings and the real
  Worktrees thread/diff report builders with synthetic GitHub, Gitea and ADO IO.
  Prove ADO unified-diff refusal and keep this distinct from live-provider proof.
- [x] Prove actual owner target/account/endpoint selection before collection;
  reject cross-scope caller labels, owner replacement/withdrawal, expired
  callers and request overrides without another read or ambient fallback.
- [x] Preserve complete nested/additive reports, lossless text, false/null
  completeness, candidate changes, valid provider errors and owner-exit/body
  disagreement; reject malformed/mismatched reports and over-limit exchanges.
- [x] Exercise disconnect, deadline exhaustion and delayed collector/cleanup
  settlement through the existing task/drain owner. Verify no retained body,
  cache/journal/cursor write or lifecycle/mutation operation occurs.
- [x] Exercise old client/new receiver, new client/unsupported old receiver,
  current receiver and supported rollback through the real request/parser seam.
  Keep strict CLI parsing and existing native/workspace/watch controls.
- [x] Record finite source/platform evidence separately from installed hosting,
  large-report transport, authenticated native-command adoption, live account/
  provider qualification and full HTTP/CLI parity. Return to the canonical Plan
  and Validation Plan rather than closing the parent on this source leaf.

### Finite source evidence

The optional `ReportReader`/`ReportBinding`, current `WatchGateway` routes,
`ReportClient` and source-only `report_host` facade are implemented under
#11664. Contained Linux qualification passed 210 cases with five Windows-only
native CLI cases skipped. The focused run combines `test_report_read.py`,
`test_report_transport.py`, `test_report_providers.py`, `test_native_cli.py`,
`test_workspace_native_only.py`, `test_watch_gateway.py` and `test_cli.py`.

The actual TLS issuer/PoP/SDK/channel fixtures exercise authenticated exchange,
policy withdrawal, cancellation, channel loss and delayed cleanup under the
original Gateway task. Separate real Worktrees report/provider fixtures use
synthetic GitHub/Gitea/ADO IO; ADO diff remains explicitly unsupported, and
raw thread candidate-change fields remain intact. None of these fixtures
qualifies live credentials or an installed native-command binding.

Fatal lint, install-contract, headless-launch, module-size and documentation
consistency checks passed. Version presentation moved to the existing CLI
output owner to keep command routing within its existing module-size cap.
The repository-wide enforced typecheck could not complete its
preflight because the unrelated `lib-config-migrate` checker environment is
not prepared; no passing whole-repository typecheck is claimed.
The source implementation merged in #11668. The parent effort,
large-report transport, authenticated native-command adopter,
installed/retained hosting, Windows and live-provider acceptance remain open.

Every in-flight exchange is fenced by its discovery epoch
before an older transport/status/validation failure can change selection.
The same live owner/deadline checkpoint crosses the worker-to-coroutine
handoff and the shielded task-to-handler handoff, running immediately before
publication. The focused report tests cover stale discovery failures after a
newer success and deadline/owner withdrawal while either coroutine resumption is delayed,
including withdrawal in the actual task-completion callback.
The focused report suite passes 62 cases. Endpoint/provider replacement
between discovery and reading refuses at
admission before resolving the replacement owner or collecting a report.
