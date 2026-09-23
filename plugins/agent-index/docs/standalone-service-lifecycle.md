# agent-index standalone lifecycle note

This note records the standalone lifecycle shape implemented for `agent-index`
now that the host service no longer depends on `agent-dispatch` companion
supervision.

## Decisions

- **Service runtime:** the light service keeps using the plugin's versioned
  runtime slots under `~/.agent-index/versions/<version>`. `install`/`update`
  rebuild only that light slot, then cut traffic over with the existing zdd
  active/passive protocol.
- **Durable engine runtime:** the torch + embedding-model stack stays in the
  durable engine home (`~/.agent-index/engine/.venv`). Routine service
  `install`/`update` never rebuild or restart it. The explicit `engine` /
  `engine-update` verbs remain the only provisioning/update path for that stack.
- **Tier 1 default:** the default contract is user-mode self-supervision. The
  installer verbs `install`, `update`, `start`, and `ensure` all converge on the
  same local start path instead of delegating to another daemon. `update`
  preserves in-flight work by using the existing `agent_index deploy` cutover.
- **Tier 2 default durable autostart:** the same stable launcher path is now
  registered automatically on install/update/ensure so the host comes back after
  the next login without any manual step. Windows uses HKCU Run entries
  (`agent-index`, `agent-index-engine`) with no elevation; POSIX uses
  systemd --user units when available. These wrappers do not own version
  selection.
- **Tier 3 opt-in (Windows):** `register-tasks` remains available as an
  explicit per-machine upgrade for operators who want Scheduled Task features
  such as pre-login start, missed-trigger recovery, or task-owned restart
  policy. Choosing that tier supersedes the matching HKCU Run entries rather
  than stacking both.
- **Session start:** session hooks still publish guidance, and now also run a
  cheap `ensure` safety net. The hook never provisions a runtime; it only
  health-checks the installed host runtime and starts it when absent.
- **Installation cells:** namespaced installation cells build and reconcile the
  host runtime with the same local slot/cutover primitives as legacy mode. They
  remain self-contained and do not require `agent-dispatch`.

## Consequences

- `agent-index start` / `serve` once again run the service directly in the
  current interpreter.
- Installer `install` / `update` now manage the service themselves instead of
  leaving the host unavailable without dispatch.
- The default host install path now also registers durable autostart without
  elevation, so a plain install/update survives the next interactive login.
- The durable engine remains decoupled from ordinary service updates, so a
  routine service bump does not rebuild torch or reload the model.
