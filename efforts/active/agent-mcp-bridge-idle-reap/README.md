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
- [ ] Decide the self-reap trigger: an **idle timeout** (no JSON-RPC traffic
  for N minutes -> bridge exits itself), mirroring `agent-mcp serve`'s
  existing `--idle-timeout` concept (currently `bridge`-side has no
  equivalent at all — confirmed via `grep idle_timeout` in `config.py`,
  zero matches). Needs: is "idle" measured from last `session.submit()` call,
  last upstream response, or both? Default duration (propose starting at
  the same order of magnitude as a typical sub-agent task, e.g. 5-10 min,
  tunable via config/env, consistent with `serve`'s existing knob naming).
- [ ] Confirm this is safe for a bridge mid-`stdio`-upstream-child lifecycle
  (does self-exit cleanly tear down the `bunx/npx gitea-mcp` child the same
  way stdin-EOF/parent-death shutdown already does today via
  `session.aclose()`?).

### Phase 2 — Implement the idle self-reap
- [ ] Add an idle-timeout watch to `Bridge.run()`'s main loop (alongside the
  existing stdin-EOF/parent-death queue-wakeup mechanism) that pushes the
  same `None` sentinel after N seconds of no `queue.get()` activity.
- [ ] Config surface: a `BridgeConfig` field + CLI flag / env var, following
  existing `bridge` config conventions in `config.py`.
- [ ] Log a clear, greppable line on idle-self-reap (distinct from the
  existing EOF/parent-death shutdown logs) so a future investigation can
  tell *why* a bridge exited from its own log line, not just that it did.
- [ ] Unit tests: idle timeout fires after N seconds of no traffic; is reset
  by traffic; does not fire while a request is in-flight; `session.aclose()`
  still runs on the idle path (upstream child cleanly torn down).

### Phase 3 — Validate against tonight's exact reproduction
- [ ] Windows: reproduce the `Get-CimInstance Win32_Process` counts from
  tmichon/aperture-labs#3876's 2026-09-23 comment before the fix; confirm
  bridges self-exit within the configured idle window after their owning
  sub-agent finishes, with no live sub-agent traffic; confirm process counts
  drop back down without operator intervention.
- [ ] WSL: same validation on the original 2026-07-31/08-18/08-22
  reproduction host, confirming the fix holds cross-platform (the actual
  self-reap logic is platform-agnostic Python; only the `.CMD`-shim launch
  chain is Windows-specific and unaffected by this fix).

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

- [ ] New unit tests (Phase 2) pass in the `agent-mcp` plugin suite.
- [ ] Live reproduction (Phase 3) on both Windows and WSL shows bridges
  self-reaping within the configured idle window with zero operator
  intervention, matching the exact evidence commands already used in
  tmichon/aperture-labs#3876 (`Get-CimInstance Win32_Process` on Windows;
  `ps aux --sort=-%mem` / process-age inspection on WSL).
- [ ] No regression: a bridge actively relaying traffic (a live, in-progress
  sub-agent delegation) is never reaped mid-use.

## Proposal

_Pending — Phase 1's open design questions (idle-timeout trigger semantics,
default duration) need resolving before Phase 2 code starts._

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
