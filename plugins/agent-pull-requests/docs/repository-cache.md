# Repository cache source contracts

The repository-cache milestone extends the separately packaged PR service's
existing observation and ReplayStore owners. Its source contracts are opt-in;
the connector profile, one-shot waits, reports and workspace routes retain their
existing defaults. The explicit standalone local profile now exposes supported cache queries and
continuing subscriptions through this same foreground owner. See
[local operation](../../../services/pr/LOCAL.md). Source support and synthetic
qualification do not establish release, installation or live target acceptance.

## Native inventory and credentials

The selected Worktrees executable implements:

```text
agent-worktrees pr-watch inventory --contract --json
agent-worktrees pr-watch inventory owner/repository --config /config/repos.yaml --credential-command '["/bin/credential-reader","--config","/config/reader.json"]' --max-pages 4 --max-pulls 200 --seconds 60 --json
```

`inventory --contract` is passive. The executing receiver declares
`agent-worktrees.pr-inventory/v1`, GitHub/Gitea and per-request observation
credentials. An older executable refuses the new verb; callers must not infer
support from package versions or silently substitute an ambient account.
Inventory requires explicit native repository configuration and typed existing
read credentials. It neither enrolls accounts nor calls a credential issuer.
Inventory loads only the named file's explicit `repos.<name>.pr` binding, with
required `provider`, `api_base` and exact `repository` fields. It does not load
machine/global/registry/in-repository overlays or add an ambient active project.
For example:

```yaml
repos:
  selected:
    pr:
      provider: github
      api_base: https://github.com
      repository: owner/repository
```

GitHub configuration selects the web origin (for example
`https://github.com`); its typed credential binding selects the corresponding
REST base (`https://api.github.com`). Gitea selects its configured HTTPS base.

Each scan accepts at most four 50-item pages and 200 members by default, with
explicit maximums of 20 pages and 1000 members. An empty page proves completion:
short pages alone do not, because servers may clamp page sizes. Capped, malformed,
repeated or unavailable pages retain earlier accepted members and report
`partial`; failure before a valid page reports `unavailable`. Empty complete
inventory is distinct from unavailable inventory. Provider errors never become
terminal PR facts. Output is bounded per page; subprocess capture and the outer
native invocation retain their existing limits. Queries will not invoke this
command; only the single observation owner will.

Typed Gitea headers pass through curl stdin, not process arguments. Both native
inventory providers use byte-bounded caller-owned subprocess capture, and final
HTTP 401/403 responses abort observation as retryable credential loss.

Credential-backed native snapshots now accept Gitea as well as existing GitHub
and Azure DevOps bindings. The Gitea read path renews and validates the typed
provider/endpoint/repository/observe binding before every request. It cannot
use that credential object for a forge mutation. Credential loss is not
retried as an unrelated transient provider failure.

## Storage and backend identity

`RepositoryIdentity` binds namespace, provider, canonical HTTPS endpoint,
executing account and exact repository. Its complete key, not PR number or
account/repository labels, isolates configured targets. An internal observation
account key is derived from that full identity; the trusted native argv and
credential owner still select the actual account.

The concrete storage owner is **ReplayStore**, using the existing owner envelope
and operation lock, atomic commit and writer custody. Fresh explicit
`ReplayStore(..., repository_cache=True).provision()` adds
`repository_cache_version: 1` alongside existing replay and conversation fields.
Current facts, continuing registrations and ordered events occupy that one
bounded extension, not a second database. Subscriber baselines and conversation
history are not repurposed as current-state projection.

The extension reserves 16 MiB, 16 repositories, 400 retained current/terminal
summaries per repository, 128 continuing registrations and 4096 events.
Unresolved delivery must apply backpressure rather than being evicted; terminal
summaries are count-bounded, not full diff or chat archives. Actual observation
and continuing-delivery semantics are added by their owning implementation.

An unselected/prior reader rejects an adopted extension; a selected reader
refuses missing/unknown extension layouts for repository operations. Existing
unselected storage remains supported. There is no automatic production
migration, reset or deletion, nor rollback of adopted data into a prior reader.
Rollback means retaining the compatible reader/state or selecting a separately
fresh root, never discarding original replay/effect identities.
The continuing profile positively provisions `repository_cache_version:2`,
retaining immutable request intent, delivered high-water and original ACK-event
identity. Version-1 cache readers and selected version-2 readers refuse each
other's retained layouts rather than migrating/recreating them. Existing
unselected watch/report/workspace defaults remain unchanged.

## Configured observation and current-state projection

The existing `ReplayObserver` accepts repository-qualified native bindings and
discovers their configured open population even when ReplayStore contains no
registered waits. It invokes only the selected native inventory/snapshot
processes, including the trusted explicit native configuration. An unsupported
selected inventory receiver refuses rather than substituting an ambient source.

One cycle unions those configured targets with existing registered waits.
The existing four-reader executor shares each account-qualified PR read across
overlapping interests. At most 32 configured-only snapshot targets join each cycle,
with a rotating cursor so unread members are not permanently hidden behind the
first page; existing legacy-wait admission remains unchanged. Inventory remains
bounded by the native page/member/time limits. A finite cycle's supplied expiry
and checkpoint govern every persistence boundary; a stale cycle cannot publish
after authority withdrawal, and the executor still joins actual native cleanup.

The executing native inventory declaration must additionally expose a finite
snapshot request/capture budget. Typed GitHub/Gitea snapshots cap native
credential-backed requests at 64 per snapshot and capture each provider response
at no more than 1,048,584 bytes (including Gitea's status suffix). This bounds
nonterminating review-page responses before another request or unbounded capture,
without changing legacy token/default or unrelated provider behavior. The
declaration is selected from the actual executable; a prior receiver missing
these bounds refuses only the selected repository feature. Inventory adds at
most four forge pages per configured repository; queries add zero forge reads.
These are worst-case source bounds, not production rate-limit qualification or
authority to exercise them against real targets.

`RepositoryProjection` uses ReplayStore's existing owner transaction, operation
lock and atomic replacement. Normalized current facts and corresponding
repository events commit together. Cache-only bounded reads do not call the
forge. Current facts preserve unknown mergeability/check completeness and
include lifecycle/head, review identities/state, normalized failed checks and
native observation fingerprints—not report, diff or chat bodies.

Coverage is `unknown`, `complete`, `partial` or `unavailable`, with observation
and last-complete timestamps. Failed/capped inventory retains known membership.
A complete omission emits selection departure, never an inferred closure.
Bounded native reads confirm terminal facts or preserve departed stale facts.
Cross-provider/endpoint snapshot mismatches cannot replace last-confirmed facts.
That identity fence runs before either the repository projection or an
overlapping one-shot wait can consume the read. Settled native probe/inventory
timeouts become unavailable coverage while existing waits continue. A native
cleanup failure is not a settled timeout: the read-owner thread retains the exact
cleanup object and joins its existing retry primitive before reporting loss.
Authority withdrawal remains distinct and prevents later publication.
Qualification and inventory coverage are separate: a failed contract probe
suppresses all reads through that configured binding, including overlapping
waits, until bounds are confirmed. Unrelated legacy waits continue. A qualified
inventory outage can still refresh retained members through its bounded native
snapshot interface. Only the selected projection reads are retained for the
publication batch; legacy-only snapshots are released after ordinary observation.
Already-acquired configured wait results are projected even outside the rotation
window. Only configured-only acquisition consumes the 32-read allowance.
Projection batches contain at most 32 normalized results, not full raw snapshots;
each batch commits its current facts and corresponding events atomically.
Recorded observation times conservatively identify the cycle start rather than
overstating freshness after a delayed batch.
Existing unrelated legacy waits are serviced before repository acquisition,
under the same cycle custody and concurrency bound; qualified waiting work
precedes configured-only work. Reentry retains the last native facts but marks
them `selection_reentry_unconfirmed` until a bounded read refreshes them.
Selection entry does not publish an old terminal summary as new current evidence.
Passive qualification is now separate from provider inventory: once bounds are
confirmed, already-admitted configured waits are read before inventory pages.
Their bounded normalized evidence is retained across inventory and projected
for every known cached PR, including a departed terminal summary absent from
the current inventory plan. No duplicate acquisition is charged to that wait.
The transient prepared bound is the existing at-most-1,024 wait targets times
64 KiB normalized facts, plus at most 32 configured-only results; raw results
remain limited to the four-reader phases. These phases are sequential under one
observer cycle and writer, not additional pollers.

Initial membership and unknown-to-known evidence use baseline events. Later
members use entry events; semantic facts produce change or confirmed terminal
events; coverage and departure remain separately typed. Submitted-time/cursor
refreshes alone produce no PR-change event. Event sequence and IDs are durable.
Current facts, event identities and coverage survive restart. The fixed current
summary/event capacities apply backpressure rather than erasing unsettled state.

This remains the internal observation/projection seam. Supported local clients
use the bounded HTTP handler and never read the state file directly. Continuing
registration/replay/ACK/cancel metadata shares this same transaction owner;
subscriber traffic never becomes another forge poller.

## Local ingress selection

`RepositoryConfig.parse` is passive; only explicit foreground serve opens ingress.
The selected protocol is `agent-pull-requests.repository-cache/v1`. The local
profile uses HTTP over a private Unix-domain socket below the dedicated state
root, with trusted numeric Linux `SO_PEERCRED` UID-to-principal bindings and
explicit principal/target/consumer mappings. Requests cannot supply executable,
credential, account or Gateway identity. Linux is the initial supported local
transport; other platforms refuse this profile rather than claiming equivalent
authentication. The existing connector profile remains portable.

The SDK's `LocalCallerContext` and `LocalServiceRequest` deliberately differ from
Gateway channel-derived context. They reuse the canonical request lifetime and
response budget semantics without fabricating a fleet, Gateway or placement
permit. Local reachability alone grants no permission. The selected foreground profile enforces repository-only routes, eight admitted
requests, bounded bodies/results, disconnect, expiry and the existing drain.
It constructs no connector or Gateway credential references. The full actual
foreground-process vertical remains a separately recorded synthetic source
qualification, never inferred from passive parsing or healthy readiness.
