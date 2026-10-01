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

**In dev**, most consumers' `pyproject.toml` reference this library through a
`uv`-editable canonical pointer (`vendor-pointer-generalization` effort,
Phase 1), so there is no per-plugin dev copy to keep in sync. `agent-worktrees`
is the deliberate exception: it keeps this full local copy so a staged,
non-editable install never depends on a throwaway passthrough source tree.
**At release**, `tools/materialize_main.py` rewrites any pointer-form
consumers into the same real, promoted copy shape present here -- non-editable,
so a published plugin installs a self-contained source tree.
`customizing-copilot`'s `installing-plugins` skill script resolves this
library's `state.py` by reading its own local copy directly, without ever
importing the `plugin_activation` package -- it has no `pyproject.toml` of
its own, so it is not a `uv`-editable-convertible consumer at all. It keeps
a `src-passthrough` `VENDOR_POINTER.json` pointer copy under
`plugins/customizing-copilot/libs/plugin-activation/` (no real `src/` tree
of its own): `tools/materialize_main.py` expands that pointer at release
time exactly like any other, since it discovers pointer copies by scanning
`plugins/*/libs/<lib>/VENDOR_POINTER.json` directly, independent of any
consuming `pyproject.toml`.
