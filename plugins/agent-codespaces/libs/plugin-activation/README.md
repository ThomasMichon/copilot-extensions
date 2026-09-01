# agent-plugin-activation

Provides the canonical strict state operations for Copilot plugin inventory and
activation:

- source-qualified installed inventory inspection;
- user-global and repository activation inspection;
- exact, dry-run-first removal of user activation without uninstalling inventory;
- activation snapshots and restoration around inventory bootstrap; and
- duplicate-key, malformed-shape, and wrong-type rejection before mutation.

It also resolves the machine-wide effective plugin set used by attributed
drop-in registries:

- user-global `~/.copilot/settings.json` plus its local override;
- every project adopted in `~/.agent-worktrees/projects.yaml`, joined to the
  current-platform checkout in `repos.yaml`;
- repo identity verified through Git top-level and normalized `origin` remote;
- strict settings/registry reads whose uncertainty cannot become authoritative
  removal;
- local marketplace roots must converge before an exact installed marketplace
  payload may take precedence;
- marketplace containment plus exact marketplace/plugin identity at the
  selected root; and
- tri-state and per-source decisions that use the shared drop-in reconciliation
  semantics.

The result maps canonical `name@marketplace` sources to one active root, retains
prior values when registry or source evidence is indeterminate, and returns
structured findings for missing, mismatched, or ambiguous evidence. Consumers
decide how often to refresh and how to render findings.
