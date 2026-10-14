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
  durable engine home (`~/.agent-index/engine/versions/<version-or-repair>`). Routine service
  `install`/`update` never rebuild or restart it. The explicit `engine` /
  `engine-update` verbs remain the only provisioning/update path for that stack.
  Construction uses a fresh, immutable slot with isolated import checks before
  completion and marker-only activation. A selected dependency failure is fatal,
  reclaims only its owned incomplete candidate, and preserves the prior engine.
  Explicit updates use distinct repair generations rather than mutating a healthy
  venv. Interpreter selection uses the canonical completion-gated resolver;
  legacy `.venv` remains selected until activation publishes `current-version`
  or `last-known-good`. Staging the resolver or completing a candidate alone
  does not migrate selection; failed first activation preserves the legacy engine.
- **Tier 1 default:** the default contract is event-driven user-mode convergence. The
  installer verbs `install`, `update`, `start`, and `ensure` all converge on the
  same local start path instead of delegating to another daemon. `update`
  preserves in-flight work by using the existing `agent_index deploy` cutover.
  Repository activation uses the same effective configuration layers as the
  client, including its machine-local and bound-knowledge overlays. Detached
  service successors request Windows Job breakaway so they survive a bounded
  management process exiting; contained tests retain child ownership.
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
- **Session start:** `hooks.json` publishes guidance and invokes only the
  dependency-light metadata/binstub stamp adapter. No install script,
  provisioning, package acquisition, venv creation, service/engine startup,
  restart or autostart registration is permitted at this seam. Stamping is not
  readiness. First admitted binstub operational use provisions through the real
  runtime gate after repo opt-in and explicit role/setup intent; it never
  silently chooses a host. Direct installer invocation via `<repo> update`
  remains an explicit deployment path. `ensure-service` compatibility scripts
  remain no-ops.
  The shared Python stamp transaction pins every root ancestor with no-follow
  directory handles, validates file identity/type and creator ownership,
  and keeps the installer's existing `.publication.lock` inode open without
  truncation throughout metadata publication. POSIX uses nonblocking `fcntl`
  locking even without the `flock` executable. Publication roots and metadata
  must be owned by the effective user and reject group/other write permission;
  pinned-root permissions are rechecked before each file operation. Ordinary
  read-only group/other access remains permitted. Ancestors must be owned by the
  effective user or root and disallow shared namespace mutation; trusted sticky
  ancestors such as `/tmp` remain usable, but are never accepted as a publication
  root. Ancestor ownership and permissions are rechecked before file operations;
  existing deployment directories are never silently chmodded.
  Windows rejects reparse points
  using opened handles, denies delete sharing to pin ancestors, checks owner
  SIDs and retains the installer's publication mutex. Windows accepts the token
  user or an enabled `SE_GROUP_OWNER` group (never mere membership, a disabled
  group or a deny-only group), including the default Administrators ownership
  used by elevated creators. The opened root and files must have a non-null
  DACL with no mutation grants outside those creator identities, SYSTEM,
  Administrators and OWNER RIGHTS. Read-only and inherit-only grants do not
  authorize mutation; unsupported ACE types fail closed. This does not elevate,
  change ACLs or admit an ordinary shared writable directory. Only genuine contention
  is a successful no-op; unsafe paths and I/O/lock errors fail explicitly.
- **Installation cells:** namespaced installation cells build and reconcile the
  host runtime with the same local slot/cutover primitives as legacy mode. They
  remain self-contained and do not require `agent-dispatch`.
  Cell/slot installer actions invoke the coordinator from the user profile,
  outside both the payload and the invoking directory, with isolated Python
  and no inherited `PYTHONPATH` or `PYTHONHOME`. Reserved coordinator exit
  codes propagate unchanged.

## Consequences

- `agent-index start` / `serve` once again run the service directly in the
  current interpreter.
- Installer `install` / `update` now manage the service themselves instead of
  leaving the host unavailable without dispatch.
- The default host install path now also registers durable autostart without
  elevation, so a plain install/update survives the next interactive login.
- The durable engine remains decoupled from ordinary service updates, so a
  routine service bump does not rebuild torch or reload the model.

Logon startup and an explicit `ensure` action are not continuous crash
supervision. A host that requires unattended restart needs a configured
persistent lifecycle authority (for example the optional OS-supervised tier).
Do not describe a logon entry or a successful cutover as that authority.
