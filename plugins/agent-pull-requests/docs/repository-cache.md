# Repository cache source contracts

The repository-cache milestone extends the separately packaged PR service's
existing observation and ReplayStore owners. Its source contracts are opt-in;
the connector profile, one-shot waits, reports and workspace routes retain their
existing defaults. These foundational source interfaces do not yet advertise
repository-cache support from a running foreground host.

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

## Local ingress selection

`RepositoryConfig.parse` is a passive source parser, not a listening host.
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
permit. Local reachability alone grants no permission. The foreground host
integration must enforce read-only repository routes, eight admitted requests,
bounded bodies/results, disconnect, expiry and drain; parsing alone is not
evidence of that runtime integration.
