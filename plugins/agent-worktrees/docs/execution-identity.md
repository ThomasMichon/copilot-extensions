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

The timestamp/suffix-only `allocation` input is not sufficient to admit a new
tuple: that combination explicitly refuses until complete retained admission is
integrated. This source API is not a new CLI flag, setting or rollout switch.
Ordinary create, anchor admission and externally adopted checkout producers
still emit legacy records. Complete admission/replay, one-term equivalence and
installed-reader acceptance remain gates before ordinary-default activation.
No historical ID, path, stored reference or session is rewritten.
