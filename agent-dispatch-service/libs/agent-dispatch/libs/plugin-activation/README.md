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
- local marketplace roots must converge, and each scope selects its live
  directory root before falling back to an exact installed payload;
- marketplace containment plus exact marketplace/plugin identity at the
  selected root; and
- tri-state and per-source decisions that use the shared drop-in reconciliation
  semantics.

The result maps canonical `name@marketplace` sources to a backward-compatible
preferred root plus explicit live roots grouped by activation scope. This lets
mixed global and project scopes retain installed-only providers without allowing
an installed payload to shadow a project-local directory that the host loads
live. It retains prior values when registry or source evidence is indeterminate
and returns structured findings for missing, mismatched, or ambiguous evidence.
Consumers decide how often to refresh and how to render findings.
Callers needing only machine-global scope can pass
`resolve_active_plugins(include_projects=False)`: this preserves global
identity verification but skips registered-project discovery and Git checks.
The default still aggregates all scopes.

**In dev**, most consumers' `pyproject.toml` reference this library through a
`uv`-editable canonical pointer (`vendor-pointer-generalization` effort,
Phase 1), so there is no per-plugin dev copy to keep in sync. The two
deliberate exceptions keep this full local copy instead:

- `agent-worktrees`, so a staged non-editable install never depends on a
  throwaway passthrough source tree; and
- `customizing-copilot`, whose standalone `installing-plugins` skill script
  resolves `state.py` directly by file path and has no consuming
  `pyproject.toml` surface the `uv`-editable form could target.

**At release**, `tools/materialize_main.py` rewrites any `uv`-editable
consumers into the same real, promoted copy shape present here -- non-editable,
so every published plugin installs a self-contained source tree.
