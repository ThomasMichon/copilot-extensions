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
- **Tier 2 opt-in:** platform-native schedulers stay optional on top of the same
  launcher path. Windows keeps `register-tasks`; POSIX keeps the existing
  systemd-user unit support. These wrappers do not own version selection.
- **Session start:** session hooks stop publishing any dispatch companion. The
  remaining session-start behavior is guidance-only; lifecycle mutation is owned
  by explicit installer/runtime actions.
- **Installation cells:** namespaced installation cells build and reconcile the
  host runtime with the same local slot/cutover primitives as legacy mode. They
  remain self-contained and do not require `agent-dispatch`.

## Consequences

- `agent-index start` / `serve` once again run the service directly in the
  current interpreter.
- Installer `install` / `update` now manage the service themselves instead of
  leaving the host unavailable without dispatch.
- The durable engine remains decoupled from ordinary service updates, so a
  routine service bump does not rebuild torch or reload the model.
