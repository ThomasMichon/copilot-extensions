# Repository policy

`agent-repository-policy` supplies one fresh, read-only hosting-policy resolver
for independent managed-agent plugins. Import `repository_policy`; do not import
a sibling plugin's runtime.

```python
from repository_policy import AgentHostingError, require_agent_hosting

policy = require_agent_hosting(execution_directory, marketplace_id=marketplace_id)
```

Call immediately before managed adoption or authoring creation/resume effects,
including receiving execution boundaries. `AgentHostingError` means refusal,
never permission to try a weaker launch path. The returned policy records
provenance; it is not a transferable permission receipt or a cached launch grant.
It does not read machine-local project exposure, adopt projects, allocate
worktrees, launch processes, write state or alter existing sessions.

The resolver finds the nearest Git checkout from the actual execution directory,
or reads an explicitly supplied standalone directory. It reads current checkout
bytes, not a machine-local override or a cached/default-branch config. Candidate
order is canonical `.copilot-extensions/agent-worktrees/config.yaml`, then
`.agent-worktrees/config.yaml`, then `.agent-worktrees.yaml`. Only the first
present base applies. A selected marketplace overlay under
`.copilot-extensions/agent-worktrees/marketplaces/<marketplace-id>/config.yaml`
may add a denial; it cannot cancel the base repository's denial. Other
marketplaces do not participate.

`expose_agent` must be a boolean when present. Missing policy or `true` means no
repository veto, not adoption or launch permission. Explicit `false` vetoes
managed hosting irrespective of stale local exposure or explicit targeting.
Unreadable files, malformed YAML and malformed explicit values refuse visibly.
The library has no caller-purpose or review bypass.

Process-boundary callers can use `validate_hosting_result(payload)` to require
the complete `agent-policy check --json` success shape: integer version `1`,
literal `allowed: true`, repository and source provenance, no denial and no
error fields. A bare policy object's `to_dict()` is not an admission answer.
Validation checks the answer's shape; it never replaces a fresh check against
the receiver's actual execution checkout.

In development, consumers use a UV editable pointer to this canonical library.
Release materialization includes a standalone local copy in each consumer.
Source-only allocation, Git/PR workflows, saved handoffs and externally hosted
code review do not request hosting permission.
