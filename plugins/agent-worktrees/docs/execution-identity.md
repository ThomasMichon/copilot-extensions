# Configured execution identity

`agent-worktrees get execution-identity` is a read-only, versioned configuration
contract for optional consumers that must distinguish execution authority from
ordinary machine naming. CWD/`--project` targeting and an explicit `--session-id`
follow the existing `get` targeting rules.

```json
{
  "schema": "agent-worktrees.execution-identity",
  "version": 1,
  "status": "ready",
  "configured": true,
  "source": "project-machine-config",
  "project": "sample",
  "machine": "sample-native",
  "platform": "windows"
}
```

The existing configuration loader supplies provenance from its actual precedence:
the machine-local project configuration (including its drop-ins), then global
machine configuration, then detection. Provenance is captured in memory during
that load, not read from another YAML field or persisted as a second setting.
A detected hostname never supplies this contract's authority, even when its text
matches a canonical registry key.

`ready` requires explicit configured-machine provenance and the actual platform.
When the source registry is scoped, it shares the existing registered-current-
space validation used by ownership mutations. An unscoped or absent source
registry can still report the explicit configured identity; consumers must
qualify that key against their own execution-space topology and actual platform.
This read does not change mutation policy or migrate any ownership record.
Repository allocation and record mutation still require their own effect-side
checks; a positive identity response is not blanket mutation authorization.

`unavailable` is successful capability discovery without a configured binding:
the project is unregistered or no machine is configured.
It carries a reason and no machine key. `refused` is a compatible rejection of an
invalid configured binding, including ambiguous keys or platform mismatch, and
exits nonzero.
An explicitly present empty, null, nonstring or whitespace-only machine value
is invalid, not an absent binding. This stricter readonly certification does
not change the ordinary configuration loader's existing fallback behavior.

Consumers must recognize the schema/version and positive configured provenance.
An unknown command/schema/version is not authority. The older `get machine`
scalar remains unchanged and is not a substitute for this stronger contract.
No sibling is required to install or operate Worktrees.

## Versioned tracking identity

Tracking readers also recognize an explicitly versioned physical/environment
tuple. This is record metadata, not the configured-authority response above:

```yaml
machine_identity_version: 1
machine: sample-host
environment: wsl
execution_machine: custom-guest-space
platform: wsl
```

`machine` names the physical host. `execution_machine` supplies the one-term
owner in qualified worktree, session, pair, controller and resource references.
The same fields are retained in exact-record, list, lineage and resident-row
JSON. `platform` must agree with `environment`; missing version metadata keeps
the original legacy machine/platform interpretation. Unsupported, partial or
conflicting explicit tuples are errors, never fallback execution authority.

The typed tracking accessor preserves the exact historical machine label for
legacy records. Ordinary saves do not migrate their format or alter a versioned
identity. Concurrent changes to environment, execution owner or identity version
invalidate a loaded writer even when its physical host is unchanged. Mutation
still requires the receiving project's shared registry and actual execution
platform; sharing a physical host does not make native and guest owners equal.
Versioned records require an actual receiving-registry entry even when an
unregistered execution spelling would match the legacy configuration fallback.
Cleanup, including forced removal, applies that tuple check before any missing,
empty or unscoped registry fallback retained for historical records. Resolved
controller navigation and strict resource-owner scans use the execution owner;
malformed explicit tuples make the scan incomplete rather than borrowing the
physical host or disappearing behind an owner filter.
Publication privacy guards check both physical and execution names. Terminal
pair receipts preserve the exact legacy identity shape; versioned receipts
retain the full tuple while reciprocal references use the execution owner.
The `run` JSON recognizer also uses that owner; invalid explicit tuple output
is reported and leaves the pending resource claim unsettled.

### Source-only producer opt-in

`_create_worktree_core` has an internal `identity_mode="tuple-v1"` opt-in;
omitting it retains `legacy`. It derives the physical/environment/owner tuple
from the existing receiving registry and actual platform, then revalidates it
before allocation. Missing or conflicting local execution evidence refuses.
Callers cannot supply arbitrary tuple metadata through this mode.

New session and bridge names use the selected physical machine and environment;
a physical name ending in a platform-like suffix is not truncated. Names
that cannot form a portable path and Git-reference component refuse before
allocation reservation or carving, including path separators, drive qualifiers,
traversal and invalid characters. The tuple codec also rejects NUL/control-line
metadata. Identity metadata is not permission to create a nested ID. System IDs
keep their existing `sys-...` form. Tracking, JSON and paired worktree records
share the typed tuple; qualified companion references use its execution owner,
including anchor companions. Completed creation-key replay retains its stored
representation even if the caller selects the other supported mode. The
authoritative creation path validates the retained record against the receiving
execution space before returning it; a sibling environment cannot reuse the key
to obtain an allocation owned by the other environment.

`launch_request.execute` exposes the same internal legacy-default opt-in.
Its existing locked admission writer derives and retains a complete allocation:
the exact ID, branch, checkout, project namespace and original owner
representation. The core consumes that value rather than reformatting a
timestamp/suffix using current defaults, and revalidates it under the allocation
lock. New launch receipts also retain the record's creation nonce and original
owner/pair evidence. Retry and status refuse a replaced, retired, missing or
conflicting allocation; an already-started creation never automatically carves
again. Writer protocol version 6 prevents older handlers from consuming this
new evidence. Receipt owner metadata is either fully versioned or wholly
legacy: partial tuple fields refuse rather than invoking a legacy fallback.
Old receipt formats remain readable and are not backfilled.

New creation-key receipts retain the same original owner/pair evidence alongside
their existing nonce. Completed replay preserves the original result and refuses
changed relationships; legacy receipts retain their prior nonce and identity
checks without being rewritten. Explicit creation-tail repair uses the retained
tuple and exact pair stub, not a fresh allocation. A proven compatible legacy
registry alias keeps its original record and reciprocal-reference spelling.
Replacement of the retained nonce or owner during repair refuses.

`load_or_create_anchor_record` and `_adopt_linked_worktree` expose the same
internal opt-in for genuinely new records. The existing if-absent record lock
selects the winner before deriving a tuple. Derivation checks the receiving
project, tracking namespace and execution configuration; anchor admission also
checks the configured anchor, and external adoption keeps Git's actual anchor,
linked-checkout path, ID and branch as authority. It does not rename an externally
created checkout to match prospective naming. Opt-in derivation errors propagate
instead of falling back to a legacy write. Existing records, including a
competing creation winner, return with their original identity and bytes and
without requesting a new tuple from the registry. Newly created records retain
their supplied tracking destination for subsequent path-implicit saves,
including legacy anchor callers using a nondefault tracking directory.

The timestamp/suffix-only `allocation` input still cannot admit a new tuple;
the complete admitted value is required. These source APIs are not CLI flags,
settings or rollout switches. Ordinary create, anchor admission and externally
adopted checkout producers still emit legacy records. Installed one-term
equivalence and reader acceptance stay gated
before ordinary-default activation. No historical ID, path, stored reference
or session is rewritten.

### Source addressing equivalence

The JSON `resolve` entry point uses the same declared selection for `--new`,
read-only `--worktree-id ... --dry-run` lookup and ordinary resume. A declared
`<machine>-wsl` shorthand, physical machine plus `--environment wsl`, exact
custom execution key and configured alias select the same local guest where
the registry permits them. The selector does not change `Config.machine`:
tuple creation still derives from the registered current execution owner,
and resume independently checks the retained record's authority.

Exact registered keys retain precedence over an inferred legacy spelling,
including when the key names a foreign target that must produce a remote plan.
Ambiguous declared aliases, invalid topology collisions and conflicting
environments refuse before local allocation or resume writes. A physical-host
match never makes a sibling environment's retained record mutable.
Linux has no inferred legacy `-linux` address: such a spelling must itself be
declared as a key or compatibility alias. A generated worktree ID ending in
`-linux-...` is not execution authority.

`test_tuple_resolve_equivalence.py` drives these actual source entry points,
injecting the existing opt-in only at the new-producer call boundary. It checks
tracking YAML, JSON, short-ID lookup, read-only preview, resume mutation,
retained nonce/identity and remote plans across Windows/WSL/Linux metadata
fixtures. It does not activate ordinary tuple creation or claim installed or
native-platform acceptance.
