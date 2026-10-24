# Resident registered-repository map

```text
agent-worktrees repos map --json
```

This read-only command returns the whole registered repository map built by
the existing agent-worktrees resident. It does not start a daemon, parse the
registry, resolve repository roots inline, discover directories, or clone
anything. It uses the selected installation's authenticated control route,
with a one-second RPC budget and a 4 MiB response bound. The CLI remains on
the metadata-reader path rather than loading the eager command cluster.

## Contract

A successful response has `repository_map_version: 1`,
`inventory_authority: complete`, the native `platform`, a `repos` list and a
`projection`:

- `scope`: the selected installation's canonical runtime/state root.
- `generation`: the admitted map owner's generation.
- `revision`: the producer's publication sequence.
- `source_revision`: the strict registry read's exact content revision.
- `observed_at`: the observation's Unix timestamp.
- `quality`: `current`.

Each row contains its registered `name`, `class`, remote/default-branch,
tags, contribution reference, account/Copilot preferences, resolved account,
agent/visibility declarations and platform paths. Account names are
non-secret configuration, not credentials or authentication readiness.
`registration: ready` adds the current platform's absolute, existing local
`path`. `registration: unavailable`, `path: null` and `reason` preserve an
unavailable entry without discarding healthy peers.

The producer reads the strict registry once, resolves its entries in process
through the same registered-root helper used by registered-only point
resolution, and checks the source revision before publication. Invalid or
duplicate registry data never becomes a complete empty map. No source-root
discovery fallback participates.

Publication is immutable to readers. The resident refreshes the map through
its ordinary producer sweep, including root-availability changes without
registry edits. A failed refresh retains last-good data only as explicitly
advisory/stale. Observations older than 60 seconds are stale too. The CLI
refuses these as current inventory and exits 2, as it does for an absent,
unready, incompatible, draining or wrong-scope resident. Consumers must retain
the unavailable state rather than substituting an empty registry or starting
one resolver process per entry.

## Lifecycle and authority

The map reuses the resident's election, authenticated route, health, admission
and drain lifecycle. In-flight reads remain accounted until transport
completion. Drain closes map admission; a late producer result cannot reopen
it or publish into a replacement generation. Map demand contributes a bounded
30-second linger to the existing idle-exit policy, not a separate service.
If a transient route read closes only map admission, the resident's
current-owner recovery path reopens it even when the Worktrees roster remains
ready. Explicit drain and shutdown still prevent readmission.

This is a browsing projection, not execution or mutation permission. A
read-only/reference class stays read-only; consumers must still perform the
owning operation's point-of-use class, path, baseline and consent checks.
Changing a registry does not retroactively authorize work using an old map.

## Consumers and validation

Workers template discovery reads this map once through the existing CLI/JSON
boundary. Dispatch does not import the Worktrees domain runtime or read its
registry. Missing map support makes optional repository template discovery
partial while healthy built-in/plugin/user-local sources remain usable.

Focused producer and native transport tests:

```text
python tools/run-plugin-tests.py agent-worktrees --allow-explicit-tiers --test-file tests/test_repository_map.py --test-file tests/native/test_repository_map_native.py
```

For the real cross-distribution catalog lane, prepare Dispatch with the normal
contained runner and set `REPOSITORY_MAP_DISPATCH_PYTHON` to that prepared
interpreter before the command above. Without it the cross-distribution case
is explicitly skipped, not counted as consumer proof. That lane uses fresh
isolated state, a real local control server, the actual Worktrees CLI and the
actual Dispatch catalog; no operator service or worker is activated.
