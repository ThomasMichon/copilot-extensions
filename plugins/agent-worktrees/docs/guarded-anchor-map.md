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

## Publication

Managed related-index writes (including add, remove and primary), registry
writes, launch preparation, session startup and initial resident preparation
publish the view. Registry/related changes refresh every already-prepared
caller in the selected installation. Each caller is acquired independently
using its registered project configuration, its own effective plugin activation,
and the existing ordered related/knowledge overlays. Unavailable local
registrations do not become guessed checkout paths.
Launch/session preparation updates only that caller's view, not every prepared
worktree. Initial resident preparation and explicit refresh cover prepared views.

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
Removed policy entries disappear on the next managed push.
Before rebuilding, old views are withdrawn. A failed acquisition leaves an
unavailable tombstone, preserves the caller for a later retry, and reports a
publication failure rather than retaining obsolete protection. Other callers'
independently successful publications remain usable. A partially failed source
write retains unavailable scope records for retry and propagates the original
failure. Windows readers permit share-delete; the existing atomic writer's
bounded retries cover transient replacement conflicts while a read finishes.

## Reader contract

Version 1 uses schema `agent-worktrees.guarded-anchors`, canonical `source_root`
and `registry_root` bindings, the caller's filesystem Git identity, a producer
generation, and `inventory_authority: complete`. Each guarded row contains its
name, delegate, local path, locus and Git identity. A valid complete empty list
means no guarded local anchors were observed. Missing, foreign, malformed,
oversized, unsupported-version, incomplete or otherwise unusable maps fail open;
they never cause acquisition or an infrastructure-shaped deny.

Git identity uses bounded filesystem metadata reads, including ordinary `.git`
directories, relative/absolute gitdir pointers, linked worktree `commondir`,
bare repositories and the nearest nested repository/submodule. No Git
subprocess, object scan, index/config read or RPC is needed. A nested repository
does not inherit its containing checkout's guard. Direct write tools match the
repository common-directory identity, including linked target worktrees. Shell
handling retains the existing command-position/write-target heuristic; this
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
