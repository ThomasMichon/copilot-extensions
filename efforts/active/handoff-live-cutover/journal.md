# Journal - handoff-live-cutover

Dated, append-only running log of the effort.

Part of the [handoff-live-cutover effort](README.md).

## 2026-07-10 — Phase 5 (cont.): complete-on-consume via `agent-dispatch consume` (dev17/dev22)
- **Operator probe:** "For handoffs, we expect them to be completed the moment
  they are consumed." A queue check proved the gap: the private facility
  repo's lane was
  clean, but the copilot-extensions lane held a **stale `proposed` handoff**
  (`857aa604`, a 2026-07-07 "extension dispatch test") — never completed. Root
  cause: completion depended on the *resume path*; dev16's `payload --raw` seed
  loaded the brief but never completed the task, so hand-paste/non-cutover
  resumes lingered.
- **Fix shipped (agent-dispatch `0.1.0-dev22`, context-handoff `0.1.0-dev17`,
  pushed to copilot-extensions `main`, deployed on the primary facility dev host):**
  - **New `agent-dispatch consume <id>` verb** — idempotently drives
    `approve → claim → start → complete` and prints the payload. One command =
    load + consume. Already-terminal/unclaimable tasks just re-print (no error);
    identity/lane auto-resolved from CWD. Two tests (parser + end-to-end
    idempotency vs. a real coordinator); full suite 145 passed.
  - **Seed rewired** to `agent-dispatch consume <id>`; **`continue_handoff` no
    longer pre-completes** at cutover — completion is owned by the successor's
    consume, so completion == consumption on every path and a never-consumed
    handoff stays claimable.
  - Docs synced (both SKILLs + agent-dispatch README + extension comments).
- **Live-verified** on the production coordinator: a throwaway proposed task,
  `consume` → payload printed → `completed` (`result_ref: consumed:<worktree>`)
  → idempotent re-consume. **Stale `857aa604` abandoned** (`--permit`). Both
  lanes now clean.

## 2026-07-10 — Phase 5: one-command resume seed (context-handoff dev16)
- **Operator observation:** the just-validated cutover was correct but the
  successor's *pickup* was inefficient — the terse `Claim and act on the handoff
  <id>` seed made it discover everything (tool-search, full agent-dispatch skill
  load, `show`/`payload`/`approve`/`claim`/`start`) before working. The old file
  model was leaner. Decision: **give the successor the exact command that yields
  payload + helper framing in one go** (operator steer), and hold umbrella #2249†
  open as **Phase 5** rather than closing it.
- **Shipped (context-handoff `0.1.0-dev16`, pushed to copilot-extensions `main`,
  deployed on the primary facility dev host):**
  - `save_handoff_prompt` task-mode seed → file-model parity: single line, ASCII,
    self-loading — *"…Load your full brief by running: `agent-dispatch payload
    <id> --raw` ; then continue: <topic>."* No skill load, no claim ceremony.
  - `continue_handoff` spends the baton at cutover (`approve→claim→start→
    complete`, `cutover:<sid>`) so the deterministic-successor task doesn't
    linger as claimable `proposed`.
  - Verified `payload --raw` resolves post-`complete` (content-addressed blob,
    status-independent) on task `e2b00624`.
  - SKILL.md + extension comments synced; lockstep bump plugin.json +
    marketplace.json.
- **Tracked as #2346†** (sub-issue E under #2249†). Acceptance is the *next* real
  handoff (single `payload --raw` pickup; source task ends `completed`).
- **Housekeeping:** the dispatch task `e2b00624` (this validation run) was
  completed (`resumed:2f0bde38-validated`). Its 15-min lease expired mid-work and
  the coordinator correctly requeued it once (attempts=2) before the final
  complete — a clean demonstration of lease recovery, not a bug.

## 2026-07-10 — ✅ LIVE CUTOVER VALIDATED (psmux, Windows) — effort complete
- **The acceptance test passed live.** After reloading the session to pick up
  context-handoff dev15, the operator kicked a real `/handoff-continue`. The
  full chain worked with zero human relay:
  - **Storage = task, not file.** The handoff stored as agent-dispatch task
    `e2b00624ddc74f74b80dafe249addaaf` (`source: context-handoff`, `payload_ref:
    blob:…`, label `handoff`, targeted at this worktree). The dev15 `.cmd`/`runCli`
    fix held — no silent file fallback. This is also the first time task-mode
    storage has ever worked on Windows.
  - **Successor spawned + operator cut over.** A fresh Copilot booted in a **new
    window `@20` / pane `%22`** of the *same* `historical-source-worktree`
    psmux session; window `@20` became active (operator now watching the successor).
  - **Old session retired cleanly.** The originating session `5a848431` (its resume
    id matches the task `dedup_key handoff-5a848431-…`) exited at agent-stop via the
    double-Ctrl-C retire; its pane `%1` fell back to the pwsh shell showing the
    Copilot exit/`Resume` screen. **No orphan copilot, pane, or lease** — verified
    via `psmux list-panes -a` and per-pane `capture-pane`.
  - **Successor consumed the task + finished the effort** (this entry): claimed
    `e2b00624…` from the queue, confirmed the cutover, updated this README, and
    closed umbrella #2249†.
- **Result:** the north star — *a session that hands off and keeps going,
  automatically, preserving interactive CLI state* — is proven on psmux/Windows.
  MVP is validated. Remaining items (tmux/Linux live pass; #2261†/#2262†) are
  stretch/follow-up, not blockers.

## 2026-07-10 — First live-cutover attempt: found + fixed a Windows `.cmd` bug (dev15)
- **Attempted the first real live cutover** (operator: "try kicking the handoff").
  `continue_handoff` returned "cutover unavailable"; `save_handoff_prompt` also
  fell back to a **session file** despite a healthy coordinator. Both were the
  **same root cause**, diagnosed (not assumed):
  - **Node's `execFileSync` cannot spawn the Windows `.cmd` binstubs**
    (`agent-worktrees.cmd`, `agent-dispatch.cmd`) — `CreateProcess` won't run a
    batch file without a shell, so every call threw `ENOENT`. Proven empirically:
    `execFileSync("agent-dispatch",["health"])` → ENOENT, but
    `execSync("agent-dispatch health")` and `execFileSync(...,{shell:true})` → OK.
  - Consequence: `dispatchHandoff`/`agentWorktreesGet` failed → save silently
    degraded to a file; `runHandoffCutover` failed → `continue_handoff` reported
    "not under mux." The mux command itself was **fine** (a shell `--dry-run`
    returned `ok:true` with the seed reconstructed perfectly).
- **Fix (context-handoff `0.1.0-dev15`, extension-only — works against the
  already-deployed command):** added a cross-platform `runCli(bin,args)` helper —
  on win32 route through `execSync` with each arg quoted for cmd.exe
  (`quoteWinArg`), elsewhere keep `execFileSync`. Routed all six
  agent-worktrees/agent-dispatch calls through it. Verified the seed round-trips
  through cmd.exe intact (spaces, colon, backslashes, `#`, `()`). Shipped +
  deployed on the primary facility dev host.
- **This also explains** why task-mode storage never worked on Windows before
  (always fell back to file) — same `.cmd` bug in `dispatchHandoff`. dev15 fixes
  that too.
- **Status:** fix deployed; the running session must reload to pick up dev15
  (extensions load at session start), then the kick should complete the live
  cutover. The mechanism is proven; only the in-session extension is stale.

## 2026-07-10 — Tool-shape realignment: split save + `continue_handoff` (operator steer)
- **Operator clarified the intended shape** (and questioned `/handoff-continue`):
  a live handoff should be *the agent prepares the handoff → creates the task →
  calls an extension tool that kicks the cutover*. My first cut merged store +
  cutover into a `continue_live` flag on `save_handoff_prompt` and leaned on the
  slash command as the trigger — the operator wanted the **tool** to be the
  actuator, as two explicit calls. Resolved (operator choices): **split tools**,
  **explicit seed threading**, **keep `/handoff-continue`** as a convenience.
- **Refactored (context-handoff `0.1.0-dev14`, agent-worktrees `1.5.3-dev167`):**
  - `save_handoff_prompt` drops `continue_live`; it just stores (task/file) and
    returns the reply prompt **plus a `HANDOFF_SEED:` line** (single
    responsibility again).
  - New **`continue_handoff(seed)`** tool is the explicit "kick the flow"
    actuator: spawns the seeded successor in a new `wt-<id>` window, cuts over,
    arms self-retire. Safe no-op off-mux. Seed is threaded **explicitly** (agent
    passes the `HANDOFF_SEED` from save).
  - `/handoff-continue` kept; now drives generate → compose → `save_handoff_prompt`
    → `continue_handoff(seed=…)`. It is a **convenience trigger, not the
    mechanism** — the tools are.
  - `agent-worktrees mux_retire_pane` double-Ctrl-C gap **0.5s → 0.6s** (a single
    Ctrl-C does little; Copilot's clean quit needs ~600 ms spacing — operator
    confirmed).
  - Reconciled agent-worktrees version files to dev167 (a concurrent dev166 landed
    an inconsistent bump: pyproject dev166 but plugin.json/marketplace dev165).
  - Gates green (node --check, install-contract, docs-consistency, ruff). Deployed
    on the primary facility dev host (context-handoff tools live next session; the retire-gap venv
    change lands on next picker launch).

## 2026-07-10 — Picker "Open into a CLI session" landed (#2253†)
- **Slice D (#2253†) landed on `main`** (agent-worktrees `1.5.3-dev165`,
  agent-dispatch `0.1.0-dev21`). The **discover** half of
  produce→resume→**discover**: a `proposed`/`handoff` agent-dispatch task can be
  opened straight into a CLI session in its target worktree from the Worktree
  Picker's **Tasks** pivot.
  - `engine.py`: new **`open-cli`** internal pivot verb → `_open_worktree_cli(wid)`
    resolves the worktree row by stable id across loaded machines, builds the
    standard **resume decision**, and exits the picker (using the picker's own
    launch-decision plumbing — **not** a subprocess, so a remote worktree still
    routes through the normal SSH handoff). The opened session consumes the
    handoff via `/resume-handoff`. Unknown/absent id is a reported no-op.
  - agent-dispatch **pivot manifest** declares the "Open into a CLI session"
    internal action (`kind: internal`, `verb: open-cli`) as the primary Tasks
    action.
  - 2 hermetic picker tests; picker + pivot suites green (**128 passed**); ruff +
    install-contract + docs-consistency clean. Closes the agent-dispatch effort's
    flagged "Open into a CLI session" follow-up.

## 2026-07-10 — Phase 3 landed + validation-safety finding (harness hazard)
- **Slice C (#2251†) landed on copilot-extensions `main`** (context-handoff
  `0.1.0-dev13`): `save_handoff_prompt` gains `continue_live`; on opt-in it stores
  the handoff as usual, derives the successor **seed** (= the normal short reply
  prompt), shells `agent-worktrees handoff-cutover --seed … --old-pane $TMUX_PANE`
  to spawn+cut over, and arms self-retire; `session.idle` retires the old pane on
  agent-stop. New **`/handoff-continue`** slash command is the human opt-in.
  Graceful fallback to store-and-reply off-mux. SKILL.md documents it. `node
  --check`, install-contract, docs-consistency all clean.
- **The MVP is code-complete on `main`:** `handoff-cutover` command (dev161/162) +
  extension flow (dev13). A session can now hand off *and continue* via
  `/handoff-continue`.
- **Validation-safety finding (important).** A scripted live psmux mutation test,
  run **from inside the operator's attached session**, disrupted that session
  (stray Escape/Ctrl-C). Root cause: a *third-party* process doing
  `new-window`/`select`/`send-keys` against a **second** session on the same
  psmux server bled window-selection + keystrokes into the **attached** client.
  **This is a harness hazard, not a product bug** — the real feature creates the
  successor in the **same** `wt-<id>` session the operator is attached to and
  retires the OLD session's **own** pane (its `$TMUX_PANE`), so no cross-session
  bleed. Rule captured: **never run live mux-mutation tests from an attached agent
  session.** The dangerous scratch script was deleted; full-flow validation is
  deferred to the first real (operator-initiated) `/handoff-continue` or a
  detached scratch server. Read-only check confirmed `mux_active_pane` returns
  None for an absent session (no current-pane fallthrough).

## 2026-07-10 — Phase 2 landed: `agent-worktrees handoff-cutover` command
- **Slice B (#2250†) landed on copilot-extensions `main`** (agent-worktrees
  `1.5.3-dev161`). The command + mux primitives that spawn a seeded successor and
  cut over:
  - `sessions.py`: `build_mux_new_window_argv` (pure, platform-aware — `env -u`
    identity strip + pane-wrapper on tmux, direct on psmux; `-e` env propagation;
    `-P -F #{pane_id}`), `mux_active_pane`, `mux_new_window`, `mux_retire_pane`.
  - `__main__.py`: `cmd_handoff_cutover`. **Spawn** mode reconstructs the launch
    cmd (`_build_launch_cmd`) + trailing `-i <seed>` (interactive seeded first
    turn — never `-p`; **no** `--resume`, so a fresh context) and opens+selects a
    NEW window in `wt-<id>`, returning the OLD pane id. **Retire** mode
    double-Ctrl-Cs a given pane. Graceful exits 2/3/4 so the extension can fall
    back.
  - Registered subparser + handler; added `handoff-cutover` to launch-session
    `DIRECT_COMMANDS` (sh + ps1). 15 hermetic tests; suite **891 passed, 6
    skipped**; ruff + install-contract clean. Verified live on Windows: `--help`
    renders, retire-mode against a bogus pane returns `already-gone` (psmux
    invoked correctly).
- **Key design refinement (from the codebase):** `graceful_quit_mux_session`
  already establishes that **Copilot CLI's native clean quit is a *double*
  Ctrl-C** (~300-800 ms apart), not `/exit`. So retire uses a **pane-targeted
  double-Ctrl-C** (robust mid-state, code-parallel to the existing helper),
  superseding the locked "/exit-then-C-c". Because it must target the OLD pane
  specifically (the session's active pane post-cutover is the successor), it's a
  new pane-scoped helper, not the session-scoped one.
- **Gotcha (filed as a session note):** `Set-Content -Encoding UTF8` in **Windows
  PowerShell 5.1** prepends a UTF-8 BOM; that BOM on `marketplace.json` failed the
  copilot-extensions **pre-push hook** (`check-docs-consistency.py` does
  `json.loads(read_text("utf-8"))`). The 6 "push rejected" attempts were this
  local hook, **not** a server race. Fixed by stripping BOMs
  (`UTF8Encoding($false)`); use the `edit` tool for JSON bumps next time.

## 2026-07-10 — Phase 1 progress: dead-path cleanup landed; coordinators verified
- **Slice A (#2252†) landed on copilot-extensions `main`** (agent-worktrees
  `1.5.3-dev160`): removed both dead `handoff consume`/`prompt_path` relaunch
  blocks from `launch-session.sh`, the dead `handoff` `DIRECT_COMMANDS`
  passthrough (`.sh` + `.ps1`), and the inert `handoff_prompt` field from
  `WorktreeRecord` (`tracking.py`) + its test constructions. Legacy YAML carrying
  `handoff_prompt:` still loads (key ignored) — kept as regression fixtures.
  Suite green (**876 passed, 6 skipped**), ruff clean, install-contract OK. This
  retires the literal "old model of a session-state file" handoff remnant.
- **Coordinator verification (facility):**
  - **the primary facility dev host (Windows):** `health` ok, `0.1.0-dev20` ✓
  - **the primary facility dev host (WSL):** `health` ok, `0.1.0-dev20` ✓
  - **a secondary facility host:** coordinator **client now installed** (2026-07-10): the
    agent-dispatch **plugin was absent** (the facility's own machine-gated
    update reconciler
    did **not** auto-install it — a real gap vs. the effort's dev20 claim), so it
    was installed manually (`copilot plugin install agent-dispatch@copilot-extensions`
    → `install.ps1 install`): venv + `agent-dispatch.cmd` binstub + picker pivot +
    the `agent-dispatch` Scheduled Task, all created. **But the coordinator is not
    yet serving:** the task is `AtLogOn` + "run whether logged on or not" and
    **never fires over a non-interactive SSH session** (`LastTaskResult 0x41303`,
    "has never run"); it will start on the next **interactive logon** to a secondary facility host
    (or when driven by a secondary facility host's own agent). **Two gaps for the agent-dispatch
    effort:** (1) the machine-gated reconciler doesn't install the plugin payload
    on a machine that never had it (#2261†);
    (2) the Windows coordinator can't be cold-started over SSH
    (#2262†).
  - **a third facility host:** SSH timed out (tunnel-only; presumed offline). Re-verify
    when reachable.
- **Next (core build):** the `agent-worktrees handoff-cutover` command (#2250†)
  then the extension flow (#2251†). Note: live validation of the cutover will
  spawn a successor pane and retire the current one — it **disrupts the operating
  session's own mux pane**, so it needs a deliberate test window (a scratch
  worktree, or operator awareness), not an incidental run.

## 2026-07-10 — Effort carved; design locked with the operator
- Investigated current state across both plugins: **context-handoff is already
  graduated onto agent-dispatch** (dev11/dev12, installed == source, no deploy lag);
  a coordinator answers `health` on the primary facility dev host. The only true "old model" leftover
  is a **dead** `handoff consume`/`prompt_path` relaunch block in
  `launch-session.sh` (subcommand removed from `__main__.py`).
- Traced the mux boot flow: `_build_launch_cmd()` (`agent_worktrees/__main__.py`)
  builds the child command; `launch-session.{ps1,sh}` run it via
  `psmux/tmux new-session` in a `wt-<id>` session; an existing handoff-on-exit path
  already uses `-i "<prompt>"` on a **fresh** session. Live-cutover generalizes this
  to a **concurrent** successor pane + old-session retirement.
- Locked design with the operator (see § Locked design decisions): new effort,
  opt-in, new-pane-same-session, `/exit`-then-`C-c` via mux `send-keys`, both
  platforms; wiring polish = dead-path cleanup + coordinator verification + picker
  "Open into a CLI session"; reactive producers + peer mesh stay in `agent-dispatch`.
- Clarified the operator's `/exit` concern: the extension does **not** call a slash
  command — termination is `send-keys` at the mux layer, so `/exit`⏎ (clean) with a
  `C-c` fallback is achievable, driven by the old extension's own `session.idle`
  (agent-stop) after the cutover is armed.

## 2026-07-26 — a third facility host coordinator verification (tunnel-only)
- Field terminal online (housekeeping sweep). The **agent-dispatch coordinator is
  verified running on a third facility host**: plugin + runtime + coordinator all on **dev76**,
  restarted to a clean dynamic bind (`127.0.0.1:63107`, nothing on 9847); the local
  client discovers it via the rendezvous file (`agent-dispatch health` → `status:
  ok`). This satisfies book2's "Coordinator verification (tunnel-only)" row in the
  Machines table — the dispatch substrate handoffs ride on is confirmed live here.
  (Full detail recorded in `durable-service-transport`'s 2026-07-26 entry.) Remaining
  effort work is the tmux/fleet stretch validation (#2261†/#2262†), unaffected by this.

## See Also

- [README.md](README.md) — the effort's shared contract (status, plan, validation)
