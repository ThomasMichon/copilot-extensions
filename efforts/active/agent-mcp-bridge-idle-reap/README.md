# agent-mcp bridge idle self-reap ("kill the recurring leak dead")

- **Slug:** `agent-mcp-bridge-idle-reap`
- **Repo:** copilot-extensions
- **Branch(es):** `worktree/lambda-core-win-20260923-030014-3867` (this worktree); may split to a fresh worktree per phase
- **Created:** 2026-09-23
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Vision:** `visions/plugin-services` §`work-coalescing-singleton`, §`process-count-scales-with-services-not-sessions` — reality has one `agent-mcp bridge` process (+ its stdio-heavy upstream child, e.g. `bunx gitea-mcp`) per **sub-agent delegation**, unbounded and never reclaimed for the life of the top-level session, directly violating "process count scales with services, not sessions/invocations." **Vision-closing.**
- **Umbrella issue:** [tmichon/aperture-labs#3876](https://gitea.michon.ski/tmichon/aperture-labs/issues/3876) (bug — the reap gap itself; 3 field-evidence comments, WSL 2026-07-31/08-18/08-22 + Windows 2026-09-23)
- **Related:**
  [tmichon/aperture-labs#3877](https://gitea.michon.ski/tmichon/aperture-labs/issues/3877)
  (proposed warmth-daemon attach — complementary, reduces per-instance heaviness
  but doesn't bound instance *count* on its own) ·
  `efforts/active/mcp-to-cli-migration` (aperture-labs — the longer-horizon fix:
  migrating sub-agents off MCP frontmatter entirely removes the bridge, but is a
  large multi-phase migration not yet complete for `gitea`/`home-assistant`,
  the two agents actually observed leaking tonight) ·
  #411, #1041, #588, #2347, #2960 (prior art / compounding issues, see #3876 body)

## Guiding Intent

A per-sub-agent-delegation `agent-mcp bridge` process (and, for `stdio` bridges,
its heavy upstream child — a `bunx/npx gitea-mcp` node process) is a
**leaked resource with no bound**: it is spawned fresh on every `task()`
delegation to an MCP-backed sub-agent, and nothing today ever reclaims it once
that sub-agent's own work is done. Confirmed *not* self-inflicted by
`agent-mcp` itself — `Bridge.run()` (`plugins/agent-mcp/src/agent_mcp/bridge.py`)
already exits cleanly on stdin EOF **and** on a parent-death watchdog
(`agent_mcp.watchdog`). The gap is entirely upstream of `agent-mcp`: the
Copilot CLI's sub-agent MCP client never closes that bridge's stdio pipe when
the sub-agent finishes — it only tears down MCP sessions when the whole
top-level session ends. We do not control that CLI-side teardown path, so
waiting for it to be fixed leaves this leak unbounded indefinitely.

This effort's operator directive is **"kill it dead"** — stop the recurring
symptom now, from the one side we *do* control (`agent-mcp` itself), rather
than a) waiting on an upstream CLI fix we can't drive, or b) only reducing
per-instance cost (the #3877 warmth daemon) without bounding instance count.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| lambda-core (Windows) | Author + validate the fix; primary reproduction host (tonight's evidence) | this worktree |
| lambda-core (WSL) | Cross-platform validation (the original #3876 reproduction host) | `wsl -d Ubuntu` from lambda-core |

## Coordination

- **Topology:** single-phase, single worktree/branch (small, bounded fix).
- **Host (owns the PR):** lambda-core (Windows).
- **Delegates:** none expected; escalate to a split worktree only if Phase 2's
  design needs its own reviewed sub-PR before the fix lands.
- **Handoff:** a fresh session resuming this effort reads this README's Plan
  checklist and Journal, picks up the next unchecked item.

## Context

**Evidence trail (full detail lives in tmichon/aperture-labs#3876, not
duplicated here — read it before touching code):**
- 2026-07-31 (WSL): 14 bridges (7 `gitea.mcp.yaml` + 7 `vei.mcp.yaml`) in one
  65-min session, one pair per sub-agent delegation, all idle/0% CPU, no live
  sub-agent process remaining. Manual `TERM` reaped them cleanly (confirmed
  pure orphans, no stragglers).
- 2026-08-18 (WSL): 5 live sessions, 66 total MCP processes (32 `agent_mcp
  bridge` + 13 `node gitea-mcp` + 13 `bunx gitea-mcp`); one 8.8h session held
  ~10 bridges aged 5min-7.5h.
- 2026-08-22 (WSL): reconfirmed 4 days later; crucially established every
  bridge was parented to a **live** process (not a classic dead-parent
  orphan) — the reap trigger needed is **sub-agent completion**, not parent
  death. Also established the *harm* is process-count/duplicate-heavy-upstream,
  not raw per-bridge RSS (shared runtime pages inflate a naive RSS sum).
- 2026-09-23 (Windows, this session): 136 `conhost.exe`+`cmd.exe` + 140
  `python.exe` on a host with 3 terminal tabs open. Root cause confirmed via
  `CommandLine` inspection (not inferred): `agent-mcp bridge --config
  <....mcp.yaml>` processes for `gitea.mcp.yaml`/`vei.mcp.yaml`/
  `home-assistant.mcp.yaml`, in 11 spawn-time clusters matching 11 `task()`
  delegations over 3.5h, none reaped. Windows pays double: the `.CMD` shim
  interposes an extra `cmd.exe` layer, so each orphan costs 2x`cmd.exe` +
  up to 2x`conhost.exe` + 2x`python.exe` vs. the flatter WSL chain.

**Code-level root-cause boundary (confirmed by reading `bridge.py` this
session, not guessed):** `Bridge.run()` already has both defenses #3876's own
"suspected location" section proposed (stdin-EOF close, parent-death
watchdog via `agent_mcp.watchdog.install_parent_death_watchdog`) — so the
existing code is not missing the *general* orphan case. The specific gap is
that a sub-agent finishing does **not** close its bridge's stdin (the pipe
stays open, held by the still-live top-level `copilot` process) and does
**not** kill the parent process (same reason) — so *neither* existing
defense fires. This is why #3876's 2026-08-22 evidence explicitly found every
leaked bridge parented to a **live** process.

## Request

Operator (verbatim, this session): *"Write all this down in a mini-effort or
in the bug, then do a handoff to tackle this. 'Kill it dead' (the recurring
issue)."*

## Plan

### Phase 1 — Confirm the fix surface (design, no code yet)
- [x] Read `bridge.py` + `watchdog.py`; confirm existing EOF/parent-death
  defenses and why neither fires for this case (done this session, see
  Context above).
- [x] Decide the self-reap trigger: an **idle timeout** (no JSON-RPC traffic
  for N minutes -> bridge exits itself), mirroring `agent-mcp serve`'s
  existing `--idle-timeout` concept (currently `bridge`-side has no
  equivalent at all — confirmed via `grep idle_timeout` in `config.py`,
  zero matches).
  **Decision (read `serve.py` directly, not guessed — it already solves this
  exact class of problem for `WarmPool`/`Server`):**
  - **"Idle" is dual-gated, mirroring `serve.py`'s `_maybe_idle_evict`**
    (`self._attached > 0 or self.pool.size > 0` guards its elapsed-time
    check): the bridge's main loop measures elapsed time since the last
    stdin line was read off the queue (i.e. since the last client message
    arrived — `queue.get()` unblocking), **but only honors that elapsed time
    when `BridgeSession` reports zero in-flight dispatch tasks.**
    `BridgeSession.submit()` already tracks in-flight work in `self._tasks`
    (a `set[asyncio.Task]`, drained in `aclose()`) — exposing that as a
    `has_pending` property gives the exact authoritative liveness signal
    `serve.py` uses, so a slow upstream response (e.g. a long-running tool
    call) can never be reaped mid-flight even if it outlasts the idle
    window. This directly resolves the PR #3406 Copilot-review finding that
    a fixed inactivity timer alone is not authoritative liveness — the
    dispatch-task-count gate *is* the authoritative signal, exactly as
    `serve.py`'s attached/warm-pool counts already are for that daemon.
    Last-upstream-response time is deliberately **not** the clock source: a
    bridge with zero in-flight tasks and a quiet stdin has no live client to
    respond to, regardless of when its last upstream reply happened.
  - **Default duration: 300s (5 min)**, matching `serve.py`'s own
    `_DEFAULT_IDLE_TIMEOUT = 300.0` exactly, for the same reasoning
    documented there and so a single mental model covers both idle knobs in
    this plugin. Tunable via a `BridgeConfig` field + CLI flag / env var
    (Phase 2), consistent with `serve`'s existing knob naming.
- [x] Confirm this is safe for a bridge mid-`stdio`-upstream-child lifecycle
  (does self-exit cleanly tear down the `bunx/npx gitea-mcp` child the same
  way stdin-EOF/parent-death shutdown already does today via
  `session.aclose()`?). **Confirmed by design, not just inspection:** the
  idle self-reap fires from the *same* `run()` loop as the existing
  stdin-EOF/parent-death paths, breaking out of the `while True` loop into
  the identical `await session.aclose()` call already used by both — no new
  teardown path, no divergent cleanup. Because the dispatch-task gate above
  guarantees zero in-flight tasks before the idle branch can fire, `aclose()`'s
  `asyncio.gather(*self._tasks, ...)` drain is a no-op on the self-reap path
  (nothing to drain), and it proceeds straight to `pipeline.aclose()` /
  `transport.end_input()` / `transport.aclose()` — the same upstream-child
  teardown (e.g. `bunx gitea-mcp`) already exercised today.

### Phase 2 — Implement the idle self-reap
- [x] Add an idle-timeout watch to `Bridge.run()`'s main loop (alongside the
  existing stdin-EOF/parent-death queue-wakeup mechanism) that pushes the
  same `None` sentinel after N seconds of no `queue.get()` activity.
- [x] Config surface: a `BridgeConfig.idle_timeout` field (default 300s,
  non-positive disables) parsed from the bridge YAML's `idle_timeout:` key,
  overridable via the `AGENT_MCP_BRIDGE_IDLE_TIMEOUT` env var when the config
  doesn't set it explicitly. No new `bridge` CLI flag: unlike `serve`
  (invoked directly by a human/script), `agent-mcp bridge` is always spawned
  by the copilot CLI's own MCP client from an `.mcp.yaml` frontmatter file,
  so the config field is the natural knob (a CLI flag would need frontmatter
  changes to reach anyway).
- [x] Log a clear, greppable line on idle-self-reap (distinct from the
  existing EOF/parent-death shutdown logs) so a future investigation can
  tell *why* a bridge exited from its own log line, not just that it did.
- [x] Unit tests: idle timeout fires after N seconds of no traffic; is reset
  by traffic; does not fire while a request is in-flight; `session.aclose()`
  still runs on the idle path (upstream child cleanly torn down).

### Phase 3 — Validate against tonight's exact reproduction
- [x] Windows: reproduced the `Get-CimInstance Win32_Process` evidence style
  from tmichon/aperture-labs#3876's 2026-09-23 comment against the *fixed*
  code. Spawned a real `agent-mcp bridge` subprocess (`AGENT_MCP_NO_MULTIPLEX`,
  the classic in-process bridge path #3876 evidence targeted) over a stdio
  upstream that itself spawns a live descendant child (mirroring the
  `bunx gitea-mcp` shape), with `idle_timeout: 3`. `Get-CimInstance
  Win32_Process` confirmed both the bridge PID and its descendant present
  before the idle window, then **fully absent** afterward, with zero
  operator intervention (bridge exit code 0). Scratch probe files were
  removed after use (not committed).
- [x] WSL: same live reproduction on the original 2026-07-31/08-18/08-22
  host (`wsl -d Ubuntu`), using its own venv build of the fixed `agent-mcp`
  and `ps -o pid,ppid,cmd` (matching #3876's WSL evidence style) instead of
  `Get-CimInstance`. Same result: bridge PID + upstream child both present
  before the idle window, both gone after, confirming the fix holds
  cross-platform as expected (the self-reap logic is platform-agnostic
  Python; only the launch-chain shape differs).

### Phase 4 — Land + close the loop
- [ ] PR through copilot-extensions' normal review/version-bump flow.
- [ ] Comment on tmichon/aperture-labs#3876 with the fix version, close it.
- [ ] Cross-reference from #3877 (still open — the idle self-reap bounds
  instance *count*; #3877's warmth daemon is the separate, still-valid fix
  for per-instance upstream duplication) and from
  `efforts/active/mcp-to-cli-migration` (aperture-labs) noting this is a
  stopgap, not a substitute for eventually migrating `gitea`/`home-assistant`
  off MCP frontmatter entirely.

## Validation Plan

- [x] New unit tests (Phase 2) pass in the `agent-mcp` plugin suite: the
  4 new tests (1 `has_pending` unit test + 3 e2e idle-reap subprocess tests
  in `tests/test_bridge_idle_reap.py`) pass, and the full `agent-mcp` plugin
  suite (565 passed, 34 skipped) is unaffected, via
  `python tools/run-plugin-tests.py agent-mcp`.
- [x] Live reproduction (Phase 3) on both Windows and WSL shows bridges
  self-reaping within the configured idle window with zero operator
  intervention, matching the exact evidence commands already used in
  tmichon/aperture-labs#3876 (`Get-CimInstance Win32_Process` on Windows;
  `ps` process-tree inspection on WSL) -- see Phase 3 above for the
  specific evidence.
- [x] No regression: a bridge actively relaying traffic (a live, in-progress
  sub-agent delegation) is never reaped mid-use --
  `test_in_flight_request_is_never_reaped_mid_dispatch` exercises this
  directly with an idle_timeout shorter than the in-flight call's duration.

## Proposal

Implemented as designed in Phase 1 (dual-gated idle self-reap: elapsed time
since the last client message AND zero in-flight `BridgeSession` dispatch
tasks), landed as PR ThomasMichon/copilot-extensions#3406.

## Journal

### 2026-09-23 — Kickoff
- Effort created directly following a live "what's eating our RAM" facility
  investigation (unrelated CPU/memory/disk-I/O telemetry sweep, tmichon/
  aperture-labs#7467/#7479/#7483) that surfaced this as the actual top
  Windows-host process/RAM consumer tonight — 136 conhost/cmd + 140 python.exe
  processes traced via `CommandLine` inspection to `agent-mcp bridge`
  invocations, not (as first assumed) a Copilot CLI defect. Confirmed the
  aperture-labs-tracked bug (#3876) already existed with WSL-side evidence
  from three prior sessions (07-31, 08-18, 08-22); added the fresh
  Windows-side reproduction as comment #125604 on that issue before starting
  this effort.
- Read `bridge.py`/`watchdog.py` directly (not guessed): confirmed the
  existing stdin-EOF and parent-death defenses are real and correct for the
  *general* orphan case, but neither fires here because a sub-agent
  finishing doesn't close the bridge's stdin or kill its (still-live) parent
  process — narrowing the fix to a *new* idle-timeout self-reap rather than
  a bug in the existing defenses.
- Operator directive: "kill it dead" — this effort scopes to the bounded,
  in-our-control fix (idle self-reap), explicitly not the larger
  mcp-to-cli-migration architecture change or the #3877 warmth-daemon
  (both remain valid, complementary, separately-tracked work).
- Handing off to a fresh session to execute Phase 1's remaining design
  question (idle-timeout trigger semantics + default duration) through
  Phase 4 (landed + #3876 closed).

### 2026-09-23 — Effort PR opened; blocked on unrelated pre-existing CI break
- Opened PR #3406 (this effort's plan, submitted for review per the
  `planning-efforts` skill's gate before Phase 2 code starts). Along the
  way, caught and fixed a genuinely pre-existing, unrelated version-consistency
  drift on `main` (`worktree-manager` `__init__.py` lagging `pyproject.toml`
  by one dev increment) as a trivial inline fix.
- **PR #3406 is currently blocked on a second, separate pre-existing `main`
  breakage** this PR did not cause and is out of scope to fix blind:
  `libs/peer-launch/tests/test_packaging.py::test_converted_codespaces_paths_have_no_unexplained_sibling_launches`
  fails on `main` itself (an `agent_codespaces/__main__.py` ambient-PATH
  resolution the peer-launch packaging test flags), failing CI on every PR
  regardless of content. Filed as tmichon/aperture-labs#7491 (not fixed here
  -- needs agent-codespaces-specific judgment on the correct fix shape).
- **Next session:** check #7491/CI status first -- if `main`'s CI is green
  again (someone else fixed it, or it was itself fixed upstream), rebase/
  re-run this PR's checks and proceed to merge before starting Phase 2. If
  still red, Phase 2 implementation work can still proceed locally
  (uncommitted/on this branch) while the PR waits, but do not force-merge
  around a genuinely broken CI gate.
