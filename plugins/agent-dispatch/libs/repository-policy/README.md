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

For an unprovisioned legacy guest, `repository_policy.guest_probe` supplies
`build_guest_policy_probe()`: Python source plus a stdin ZIP containing this
canonical resolver, PyYAML's portable source and both license notices. Run the
source with `python3 -I -S -c <source> <actual-checkout>`, supplying the ZIP on
stdin. It evaluates current guest bytes with the same resolver and YAML loader,
without guest site packages, runtime installation or checkout writes. Its private
temporary archive is removed on success and refusal. Python must support the
library's normal minimum version; missing prerequisites fail visibly.

This is a provisionless preflight read, not a launch grant or installed receiver.
It refuses an explicit `COPILOT_EXTENSIONS_CONTEXT`: the selected installation's
receiver must validate its own marketplace context and overlays, never silently
downgrade to legacy policy. Callers must still retain the fresh installed-owner
check before actual managed launch. An installed but incompatible or failing
receiver is not permission to substitute this reader.

The fresh-reader subprocess controls are explicit T3 tests, not automatic
unit-only CI. Run them from the repository root with
`python tools/run-plugin-tests.py repository-policy --test-file tests/test_repository_policy.py --allow-explicit-tiers`.
The consumer's allow/deny preparation control runs with
`python tools/run-plugin-tests.py agent-codespaces --test-file tests/test_hosting_policy.py --allow-explicit-tiers`.
These disposable controls exercise isolated Python without site packages; they
do not qualify a live CodeSpace, remote SSH transport or installed-host rollout.

In development, consumers use a UV editable pointer to this canonical library.
Release materialization includes a standalone local copy in each consumer.
Source-only allocation, Git/PR workflows, saved handoffs and externally hosted
code review do not request hosting permission.
