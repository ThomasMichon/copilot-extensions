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

Callers locating one sibling may also pass `plugin_names=("customizing-copilot",)`.
Only requested names incur source resolution, but all matching marketplace
identities remain visible so ambiguity is never resolved by choosing the first.
Report authority applies within the requested names; reconciliation preserves
excluded prior entries while still removing authoritatively absent sources for
selected names. Excluded `enabledPlugins` entries do not affect filtered
authority. Selected entry types, whole-document reads and shape, and shared
marketplace definitions retain their existing validation. Omitting the filter
retains full-roster behavior.

**In dev**, most consumers' `pyproject.toml` reference this library through a
`uv`-editable canonical pointer (`vendor-pointer-generalization` effort,
Phase 1), so there is no per-plugin dev copy to keep in sync.
`customizing-copilot` keeps a full local copy instead: its standalone
`installing-plugins` skill script resolves `state.py` directly by file path
and has no consuming `pyproject.toml` surface the `uv`-editable form could target.
`agent-worktrees` uses canonical editable references on dev; its standalone
release receives real local copies during materialization.

**At release**, `tools/materialize_main.py` rewrites any `uv`-editable
consumers into the same real, promoted copy shape present here -- non-editable,
so every published plugin installs a self-contained source tree.
