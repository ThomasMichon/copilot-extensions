# Pattern: uniform-runtime-resolution

**Serves:** the deploy contract ([`install-contract.md`](../install-contract.md))
§*Immutable-versioned layout*; effort `uniform-runtime-resolution` (#765).
**Exemplars:** `libs/versioned-runtime` (`resolve_python`, `resolve-runtime.sh`).

## Problem

A versioned-runtime plugin's interpreter is spawned from many places — a
`~/.local/bin` binstub, a systemd user unit or scheduled task, a service/daemon
launcher, a git hook, and an agent shelling the binstub via a skill. If each site
resolves the interpreter its own way, the copies drift: one binds the
`current-version` marker, another the retired `venv`/`.venv` link (a reparse
point Windows RedirectionGuard blocks, WinError 448), another guesses the newest
slot, another falls through to a PATH `python3`. The same service can then be
launched under **different slots mid-swap** — or the **system interpreter** — which
is exactly the divergence the single-instance model exists to prevent.

## Standard approach

**Exactly one resolution, marker-only, reachable identically from every caller.**

- **Python callers** use `versioned_runtime.resolve_python(root)` — the canonical
  three-tier resolution: `current-version` marker → `last-known-good` → newest
  **complete** slot. `activate()` stamps `last-known-good` atomically alongside
  the marker so the fallback always has the last-active version to prefer.
- **Shell callers** (binstubs, hooks, service launchers) source the canonical,
  service-parameterized `resolve-runtime.sh` / `resolve-runtime.ps1`
  (`AGENT_RT_ROOT="$HOME/.<svc>"` → `AGENT_RT_PY`), fanned out byte-identically to
  every plugin and embedded from one template into self-contained binstubs.
- **Agents/skills** inherit uniformity for free: skill-directed calls go through
  the binstubs, which now all resolve identically.

Binding rules that make it real:

- **Junction-free, on every OS.** Resolution never traverses a `venv`/`.venv`
  link. On Windows the marker + version-pinned binstubs *are* the mechanism (no
  junction); on POSIX the marker is resolved directly too (the stable-link is
  retired).
- **Never a PATH python.** An unresolved runtime returns *nothing*
  (`resolve_python` → `None`, `AGENT_RT_PY` empty) so the caller **degrades
  deliberately** — self-provisions, or no-ops — rather than silently binding the
  system interpreter. Finding a python to *build* the venv (bootstrap/uv) is a
  different, legitimate act and is not a launch.
- **One source of truth.** The three-tier order lives once (in the primitive and
  the shared shell resolver); a launch site copies the resolver, never the logic.
  `tools/check-runtime-resolution.py` guards against re-divergence.
- **Boot tracing has two channels.** Every launch now appends one durable JSONL
  record per phase transition to that plugin's own local log, with the existing
  `COPILOT_EXTENSIONS_BOOT_TRACE=1` stderr stream retained unchanged as an
  additional live-debug channel.
- **Completion is necessary; consumer readiness may be stricter.** The shared
  resolver rejects slots without a valid completion marker. A consumer whose
  launch contract includes an import/readiness probe must apply that probe to the
  resolved candidate before dispatch and treat failure as unresolved. It must not
  weaken or reimplement the three-tier ordering to add the probe.

The canonical phase names are:

- Binstubs / payload launchers: `shim-start`, `resolver-loaded`,
  `provision-start`, `provision-end`, `dispatch`
- Runtime resolvers: `resolver-marker-start`, `resolver-marker-result`,
  `resolver-slot-result`

### Boot-trace channels and durable log schema

The durable channel is **on by default** and records every phase whether or
not `COPILOT_EXTENSIONS_BOOT_TRACE` is set -- with one narrow, named
exception: see the `installationContext: required` caveat below, where nine
plugins' own inner dispatchers do not yet consume the forwarded shim-start
timestamp and so do not yet emit a durable `shim-start`/`dispatch` record.

- **Generated payload shims + canonical resolvers** append JSONL to
  `~/.<plugin>/logs/boot-trace.jsonl`.
- **`agent-worktrees`' bespoke launchers/resolvers** append to their existing
  durable activity log at `~/.agent-worktrees/logs/activity.jsonl` so boot
  timing sits beside other worktree lifecycle events.
- **Opt-in stderr remains unchanged.** Setting
  `COPILOT_EXTENSIONS_BOOT_TRACE=1` still emits the original
  `::boot-trace:: plugin=... phase=... t=<epoch-ms> ...` lines to **stderr**
  only. This is additive: durable logging still happens, and the stderr extras
  keep their existing `source=... result=... version=... path=...` vocabulary.
- **`installationContext: required` plugins defer `shim-start`/`dispatch`
  durable writes to their own inner, installation-context-aware dispatcher.**
  The outer generated dispatcher shim (`dispatcher-posix.tmpl`/
  `dispatcher-powershell.tmpl` -- the only templates these plugins select)
  structurally cannot know whether the active root is the legacy one or a
  resolved namespaced context, so it never writes these two phases itself;
  it only computes/stderr-emits `shim-start`'s timestamp and forwards it via
  `COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS` for the inner dispatcher to
  log once it has resolved the genuinely active root. `agent-worktrees`'
  own bespoke inner launcher (`invoke-payload-runtime.sh`/`.ps1`) does this;
  the other nine `installationContext: required` plugins' own inner
  dispatchers (each plugin's own bespoke `scripts/runtime-gate.*`, not a
  shared/vendored file) do not yet consume the forwarded env var, so those
  plugins simply do not yet emit a durable `shim-start`/`dispatch` record --
  the same "not yet implemented" state every other unimplemented phase is
  already in for those plugins, not a regression or a misrouted/lost record.

Durable records reuse the activity-style event shape:

```json
{
  "ts": "2026-09-22T14:30:10-07:00",
  "event": "boot_trace",
  "plugin": "agent-example",
  "phase": "resolver-marker-result",
  "t_ms": 1790112610123,
  "pid": 12345,
  "host": "host-name",
  "source": "resolver",
  "resolution_source": "current-version",
  "result": "hit",
  "version": "1.2.3"
}
```

Notes:

- `source` in the durable JSON means the **emitter** (`shim`, `resolver`, or
  `launcher`), matching existing activity-log naming.
- Resolver-specific tier data uses `resolution_source` instead of `source`
  because `source` is already part of the durable event schema.
- Dispatch records omit resolver-only fields and instead carry
  `path=fast|provisioned` in JSON as `"path": "fast"` / `"provisioned"`.
- Logging is best-effort and fail-open. The shell-level launch path tries to
  create the target log directory, but any failure (missing parent, permissions,
  disk full, malformed environment) is swallowed and must never block the real
  dispatch.

### Retention / rotation

- **`agent-worktrees`** reuses the existing `activity.jsonl` rolling-retention
  policy from `plugins/agent-worktrees/src/agent_worktrees/activity.py`:
  pruning begins once the log exceeds 512 KiB and keeps a 7-day window.
- **All other plugins' new `logs/boot-trace.jsonl` files are currently
  append-only and unrotated.** That is intentional for this first landing so
  the earliest shell-only phases can persist without spawning another process or
  entangling the shared service-lifecycle logs (for example `zdd`'s
  `lifecycle.log`) with a second event schema. A follow-up should add bounded
  retention for `boot-trace.jsonl` itself, ideally reusing each plugin's
  existing local log-management story rather than inventing a new one per
  surface.

### Gotchas this pattern encodes

- **The durable runtime is exempt.** A heavy engine's own venv *outside* the
  versioned tree (`~/.<svc>/engine/.venv`, see
  [`durable-vs-versioned-runtime`](durable-vs-versioned-runtime.md)) is a
  separate, intentional runtime and keeps its explicit venv — annotate it with
  `# runtime-resolution: allow` if a launch line names it.
- **Bootstrap python ≠ launch python.** `command -v python3` to *create* the venv
  is fine; a service *launched* under `python3` is the violation.
- **Self-contained binstubs.** A binstub may run before anything is deployed
  (confined-host self-provision), so it embeds the canonical snippet rather than
  depending on a not-yet-deployed resolver file.
- **A relocation must be swept everywhere, not just at the cutover site.** The
  same divergence this pattern guards against for interpreters recurs for any
  companion **file** (a shared launcher script, a binstub template) that moves
  from one plugin's install tree to another's. copilot-extensions#3433/#3454
  is the concrete case: `worktree-manager-control-plane/phase-3b-mux-
  relocation.md` moved `launch-session.{sh,ps1,cmd}` out of agent-worktrees'
  `~/.agent-worktrees/bin/` and into Worktree Manager's own versioned install
  — but three separate hand-rolled references to the retired path survived
  the cutover PR untouched: a Windows CMD wrapper's own sibling-resolution,
  the Picker "refresh" relaunch step's self-relaunch, and a Windows-host
  installer's WSL cross-boundary binstub generator. Each one degraded
  silently (a wrong-exit-code launch failure, a swallowed relaunch, a broken
  binstub) rather than failing loudly at the cutover. Before closing a
  relocation, `grep` the **entire repo** for the retired path literal — not
  just the call sites the cutover author already knew about — and prefer
  resolving through the new owner's own single resolution helper (as this
  pattern requires) over hand-copying a path anywhere a second time.

## Rationale

One resolution method means a binstub, a service unit, a hook, and an agent can
never bind different slots for the same service, a mid-swap can't strand a stale
slot, Windows launches never touch a junction, and no service ever comes up under
the wrong interpreter. It is the launch-path complement of the immutable-slot
model: one marker names the truth, and everyone reads it the same way.

## See Also

- Contract: [`install-contract.md`](../install-contract.md) §*Immutable-versioned layout*
- Related: [`durable-vs-versioned-runtime`](durable-vs-versioned-runtime.md) ·
  [`runtime-self-provisioning`](runtime-self-provisioning.md) ·
  [`cross-platform-parity`](cross-platform-parity.md)
- Hub: [`docs/patterns/`](README.md)
