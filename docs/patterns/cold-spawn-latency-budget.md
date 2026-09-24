# Pattern: cold-spawn latency budget (measuring and minimizing per-hook, per-extension process cost)

> **Serves the vision:** `visions/plugin-services` §`process-count-scales-with-services-not-sessions`,
> §`hooks-and-callbacks-are-transient`.
> **Building on:** `work-coalescing-singleton` (the warm-daemon dispatch shape),
> `uniform-runtime-resolution` (which interpreter gets resolved), and
> `windows-background-process-launch` (how a spawn stays invisible).
> **Origin:** the Windows-only extension-host `ready-timeout` investigation
> (aperture-labs#7326) and the follow-on hygiene effort
> (`efforts/active/cold-spawn-hygiene/`, aperture-labs#7382).

## The problem

Every enabled plugin's `sessionStart` (and `preToolUse`/`postToolUse`/
`sessionEnd`) hooks, plus every enabled plugin's own extension, are launched
as **fresh OS processes at session start** — batched together, all racing the
same fixed readiness window the CLI host enforces. Two independent
measurements (facility diagnostics, Windows host, CLI 1.0.87-0) show this
budget is not evenly spent:

| Process kind | Measured cold-start | Relative cost |
|---|---|---|
| `node.exe` (extension bootstrap: a few core-module imports + one microtask hop) | ~67ms | 1x (baseline) |
| `python.exe` (bare interpreter, no imports) | ~41-48ms | ~0.6x |
| `powershell.exe -NoProfile -Command "<hook body>"` | ~210ms | ~3x |
| `pwsh.exe` (PowerShell 7) | ~290ms | ~4-4.5x |
| An already-running (warm) process answering the same work over a local IPC call | ~2ms | ~0.03x |

Two further findings compound the per-spawn cost:

- **Concurrency amplifies tail latency non-linearly.** Five simultaneous
  `node.exe` cold-spawns (mirroring the real batch of extensions the host
  launches together) mostly ran 60-110ms, but one trial hit **282ms** — while
  the in-process work measured inside each process stayed a flat ~4ms
  throughout. The variance lives entirely in Windows's own `CreateProcess`
  path under concurrent load, independent of any single process's own code.
- **`hooks.json`'s `"type": "command"` contract is PowerShell/bash-mandatory,
  not optional.** A plugin cannot declare a hook that runs directly as a
  compiled/interpreted binary the host execs itself — every hook body is
  wrapped in an inline `"powershell"` or `"bash"` script string, so the
  ~210-290ms `powershell.exe`/`pwsh.exe` tax is paid **once per declared hook
  entry**, regardless of how trivial that hook's own body is.

None of this is a defect in any single plugin's code — it is the aggregate
cost of many independently-reasonable choices (one hook entry per concern,
one extension per plugin) hitting a shared, fixed-size budget together.

## The standard approach

**Minimize hook *count* before minimizing hook *body* cost, and prefer a
native wire-protocol client over spawning a second interpreter to make an
IPC call.**

1. **Merge sibling `sessionStart` (or other same-event) hook entries within
   one plugin where safe.** Each declared entry in `hooks.json` is its own
   `powershell.exe`/`bash` spawn; a plugin with two independent-but-sequential
   session-start concerns (e.g. a bootstrap check and a session-guidance
   write) pays two ~210ms taxes where one combined script body pays one.
   Only merge when the two concerns have no reason to run under independent
   timeouts or independent failure isolation — a hook that must not be
   allowed to blot out an unrelated one's timeout budget stays separate.

2. **Route hook bodies through `work-coalescing-singleton` wherever the work
   qualifies** (cheap, idempotent, shareable across callers) — this is
   already built and battle-tested (`hook_ipc.py`, the `agent-worktrees`
   resident accelerator, `agent-mcp`'s multiplexer). A hook that is already a
   thin, correctly-bounded dispatcher to a warm daemon (per that pattern's
   `_READ_TIMEOUT_S`-bounded IPC call + inline fallback) is **not** the
   problem this pattern targets — its cost is the *process hop itself* to
   make the IPC call, addressed next.

3. **Prefer a native client over a second cold interpreter, once a warm
   daemon exists.** If a hook's body is *already* PowerShell/bash (mandatory
   per `hooks.json`), and its only remaining job is "check whether a warm
   daemon is reachable, dispatch to it, else fall back inline" — spawning
   `python.exe` just to run a thin IPC client (as `agent-worktrees`'
   `hook_client.py` does today) pays a second full interpreter cold-start
   (~41-100ms+ with real imports) on top of the already-mandatory
   `powershell.exe` tax, to do work a native PowerShell/bash TCP or named-pipe
   client could do directly. This is worth prototyping per-consumer, not
   assuming — the swap only pays off where the wire protocol is simple enough
   to reimplement natively without duplicating real logic, and it must
   preserve `work-coalescing-singleton`'s invariants (bounded wait, correct
   inline fallback) exactly.

4. **Extensions have no interpreter choice — optimize time-to-ready
   instead.** The CLI host only loads extensions as Node/`extension.mjs`
   modules; there is no "use a lighter language" lever here. The lever that
   exists is making an extension's own bootstrap reach the `ready` signal as
   fast as possible: defer any non-blocking-required work (registration
   calls, background state sync) until *after* `ready` fires, never before.
   An extension that does real work before signaling ready pays its own
   init cost **and** the shared timeout race twice as hard.

5. **Measure before merging, don't assume.** The wall-clock numbers above are
   single-machine, single-CLI-version snapshots — re-measure locally (a
   synthetic probe script + `System.Diagnostics.Stopwatch`, or the
   equivalent on POSIX) before deciding a specific hook is worth the merge or
   native-client rewrite. A hook that already routes through a warm daemon
   in the common case may not be worth touching at all.

## Anti-patterns

- ❌ Assuming "fewer lines of hook script" is the same optimization as
  "fewer process spawns" — a one-line PowerShell hook body still pays the
  full `powershell.exe` cold-start; the count of *declared hook entries*
  drives the process count, not the body's size.
- ❌ Reaching for `Start-Process -WindowStyle Hidden` for a latency-sensitive
  spawn because it "looks" like the standard hidden-launch idiom — measured
  ~2x slower than `agent_procutil`'s `CreateNoWindow` + direct
  `Process.Start()` pattern for the identical no-popup guarantee (see
  `windows-background-process-launch`).
- ❌ Rewriting a hook's *substantive* logic in PowerShell/bash "to avoid
  Python" — the language choice is a rounding error next to the warm-vs-cold
  difference (~30-140x). Chase warmth first; language second.
- ❌ Treating this pattern as a fix for the Windows extension-host
  `ready-timeout` race itself (aperture-labs#7326) — that race is inside the
  CLI host's own process, outside plugin code. This pattern reduces the
  facility's own contribution to concurrent process-creation load at the
  same moment, which is a plausible (unconfirmed) mitigation, not a direct
  fix.

## See Also

- [work-coalescing-singleton](work-coalescing-singleton.md) — the warm-daemon
  dispatch shape this pattern assumes exists before optimizing the hop into it
- [uniform-runtime-resolution](uniform-runtime-resolution.md) — which
  interpreter a spawn resolves to, orthogonal to whether a spawn happens at all
- [windows-background-process-launch](windows-background-process-launch.md) —
  how a spawn stays invisible; this pattern is about whether/how-often it
  happens at all
- `efforts/active/cold-spawn-hygiene/README.md` — the effort applying this
  pattern to specific plugins
- Hub: [`docs/patterns/`](README.md)
