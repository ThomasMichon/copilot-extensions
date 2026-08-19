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
