# Prepared cross-repository guard facts

The cross-repository write guard does not discover related repositories from a
tool event. Both the standalone command-hook policy and the resident pre-tool
policy read the same small JSON projection:

```text
<selected-registry-root>/guarded-anchors/<sha256(canonical-caller-root)>.json
```

The selected installation owns this directory. A different installation's map,
or a map for another caller checkout, is not a fallback. Linked caller worktrees
have their own caller keys; their policy is not unioned with other callers.
Without an explicit installation context, the supplied `AGENT_HOME` selects the
legacy root for both the map and live break-glass grants.

## Publication

Managed related-index writes (including add, remove and primary), registry
writes, launch preparation, session startup and initial resident preparation
publish the view. Registry/related changes refresh every already-prepared
caller in the selected installation. Each caller is acquired independently
using its registered project configuration, its own effective plugin activation,
and the existing ordered related/knowledge overlays. Unavailable local
registrations do not become guessed checkout paths.
Configuration and activation acquisition share the configuration owner's
`AGENT_HOME`. Each publication uses a fresh, request-scoped configuration cache
and captures suppressed YAML/binding failures; a degraded loader result cannot
be published as complete. Optional presence observations must be positively
absent, not merely unreadable or changed during acquisition. Missing stable
owner-input provenance remains a documented qualification limit, not evidence
that a readable value is a reusable snapshot.
Launch/session preparation updates only that caller's view, not every prepared
worktree. Initial resident preparation and explicit refresh cover prepared views.
An ordinary related-index mutation does not require registering its checkout.
It refreshes prepared callers and may prepare a positively registered caller;
an unregistered implicit caller is not enrolled. Uncertain registration for a
new implicit caller is advisory, not a source-write failure. Explicit refresh
and failures of already-prepared views retain their error contracts.

For out-of-band configuration, binding, plugin or filesystem changes, run:

```text
agent-worktrees refresh-guards --repo <caller-checkout> --json
```

This also retries other prepared views. Preparation is the other explicit
refresh boundary. There is no watcher, TTL, periodic recomputation, hook RPC
refresh, discovery fallback, service startup or new daemon.
Successful JSON command output is version 1 and reports the publication count
and generation; a failed refresh exits nonzero with its diagnostic on stderr.

Writers hold the existing cross-process record lock across acquisition and
publication, and use unique temporary files with atomic replacement. No
snapshot is computed before taking that writer lock. Managed source writes
share the lock and withdraw existing views before changing source bytes.
Registry locking precedes publication locking, including when a registry-owned
operation calls the writer reentrantly.
Prepared-view inventory uses failure-visible directory enumeration, not a glob
that can suppress listing errors. Failed enumeration prevents source mutation
and cannot report a successful empty refresh.
Removed policy entries disappear on the next managed push.
Before rebuilding, old views are atomically withdrawn without deleting their
caller scopes. An interrupted refresh can therefore retry the complete prepared
inventory. A failed acquisition leaves an
unavailable tombstone, preserves the caller for a later retry, and reports a
publication failure rather than retaining obsolete protection. Other callers'
independently successful publications remain usable. A partially failed source
write retains unavailable scope records for retry and propagates the original
failure. Windows readers permit share-delete; the existing atomic writer's
bounded retries cover transient replacement conflicts while a read finishes.
Automatically rediscovered callers whose registration was removed are retired;
an explicitly requested unregistered caller still reports an error.
Snapshots and tombstones retain the caller's `source_project` registration
binding. Removal of that named registration remains distinguishable from an
unrelated unavailable checkout; unrelated acquisition failures cannot keep a
removed caller poisoning later publications.
Transient checkout or registered-anchor metadata failures retain the caller's
retry scope and report unavailable acquisition. An unreadable identity is not
proof that registration or the checkout disappeared.
All map readers reject nonregular or indirected inputs before opening, with a
nonblocking POSIX open and post-open type check to cover replacement races.
The map-store directory itself must be a real directory, not a symlink or
Windows junction; publication cannot follow it into another installation.
If the shared reader itself is unavailable, a managed source write refuses
before mutation rather than destroying the caller inventory or reading unsafe
inputs. Hook consumption with a missing reader still fails open.
An unreadable declared guarded checkout also makes its caller view unavailable,
not complete empty. A nonlocal registration or positively absent checkout is
distinct from an unreadable local path.

## Reader contract

Version 1 uses schema `agent-worktrees.guarded-anchors`, canonical `source_root`
and `registry_root` bindings, the caller's filesystem Git identity, a producer
generation, and `inventory_authority: complete`. The producer also retains its
caller registration name as `source_project` for retry/retirement bookkeeping.
Each guarded row contains its name, delegate, canonical local `path`, locus and
Git identity. New publications also retain the validated registry spelling in
`registered_path`, so shell targets recognize a registered symlink/junction
alias and its physical checkout. Older rows without that field remain readable.
Row validation checks lexical shape and canonical identity consistency without
probing every guarded path; current Git identity is acquired only for the caller
and relevant event targets. A valid complete empty list
means no guarded local anchors were observed. Missing, foreign, malformed,
oversized, unsupported-version, incomplete or otherwise unusable maps fail open;
they never cause acquisition or an infrastructure-shaped deny.

Git identity uses bounded filesystem metadata reads, including ordinary `.git`
directories, relative/absolute gitdir pointers, linked worktree `commondir`,
bare repositories and the nearest nested repository/submodule. No Git
subprocess, object scan, index/config read or RPC is needed. Missing common Git
directories and unreadable or nonregular markers yield unavailable identity;
they do not fall through to a containing repository.
Git pointer reads remove only the line terminator and format separator, not
meaningful spaces in a repository/common-directory path.
A nested repository does not inherit its containing checkout's guard. Direct
write tools match the repository common-directory identity, including linked target worktrees.
Shell and direct-write evaluation resolve each distinct target identity only
once per event, not once per guarded row; no identity result is retained across
events. Shell handling retains the existing command-position/write-target heuristic; this
change does not introduce a shell parser or extend its syntax coverage.

Current kill switches, policy modes and selected-installation `allow-edits.json`
grant expiry remain live; permission decisions are never precomputed. The
separate anchor-write guard is unchanged.

## Observation and deployment limits

`observed_at` and `qualification: managed-observation` describe an explicit
managed acquisition, **not** stable owner-input freshness. The existing
`AcquisitionReport.reusable=False` and missing full configuration/input-frontier
closure are not promoted into dependency proof. Out-of-band changes require
explicit refresh/preparation; the reader neither checks those inputs nor
pretends a timestamp proves them unchanged.

The stdlib reader `guarded_anchor_map.py` deploys alongside its importer through
all existing installer/publication paths and participates in deployment drift
reconciliation. Mixed older/newer payloads must be reconciled normally; absent
reader/map facts do not authorize a foreground installation. Source delivery,
release inclusion and installed activation remain separate validation lanes.
