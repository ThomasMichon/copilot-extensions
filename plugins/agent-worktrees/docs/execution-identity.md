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
