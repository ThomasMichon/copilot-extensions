---
visions:
  - visions/agent-fabric
  - visions/session-hosting
---

> **Migrated from a private facility repo** (`efforts/active/handoff-live-cutover`) on
> 2026-09-13, as part of reconciling handoff/cutover effort tracking into the
> repo that actually owns the mechanism. Original frontmatter referenced that
> repo's vision `visions/change-pipeline`; updated above to this repo's
> `visions/agent-fabric` and `visions/session-hosting`, which cover the same
> intent. Content below is otherwise verbatim, including its checklist state
> at time of migration. **A trailing `†` marks a reference to that private
> repo's own issue tracker — not resolvable here.** See the sibling effort
> `efforts/active/handoff-cutover-lifecycle-journal` for the observability
> layer built on top of this mechanism.

# handoff-live-cutover — Automatic Live-Cutover Handoff

- **Slug:** `handoff-live-cutover`
- **Repo:** copilot-extensions (migrated 2026-09-13 from a private facility repo, which
  originally hosted this as facility wiring) — the `context-handoff` +
  `agent-worktrees` plugins
- **Branch(es):** _historical source branch omitted (private identifier)_
- **Created:** 2026-07-10
- **Status:** Active — core complete (MVP + Phase 5 live cutover / continue_handoff / agent-dispatch consume); only tmux/fleet stretch validation remains (#2261†/#2262†).
  The automatic live-cutover handoff works end-to-end on psmux/Windows:
  `/handoff-continue` stored the handoff as an agent-dispatch task, spawned a
  successor in a new window of the same mux session, cut the operator over, and
  retired the old session cleanly with no orphans — the successor claimed the
  task and finished the effort (see the Journal). MVP code shipped + deployed on
  the primary facility dev host: the `handoff-cutover` command (agent-worktrees dev165), the
  context-handoff live-cutover flow with `/handoff-continue` (context-handoff
  dev13–dev15), and the picker "Open into a CLI session" action (agent-worktrees
  dev165 + agent-dispatch dev21). All four sub-issues (#2250†–#2253†) closed.
  **Umbrella #2249† held OPEN for Phase 5** — a resume-pickup **efficiency +
  complete-on-consume** refinement (issue #2346†): the successor now gets a
  **one-command** self-loading seed (`agent-dispatch consume <id>`) that both
  loads the brief and marks the handoff completed the moment it is picked up —
  replacing the terse claim prompt that forced multi-turn discovery
  (context-handoff **dev17** + new agent-dispatch **`consume`** verb **dev22**,
  shipped + deployed on the primary facility dev host, live-verified). **Stretch (non-blocking):**
  live tmux pass on WSL / another facility host / a secondary facility host; gaps #2261† (reconciler plugin-bootstrap)
  / #2262† (Windows coordinator SSH cold-start).
- **Umbrella issue:** #2249†
- **Sub-issues:** #2252† (A — dead-path cleanup) ·
  #2250† (B — `handoff-cutover` command) ·
  #2251† (C — extension live-cutover flow) ·
  #2253† (D — picker "Open into a CLI session") ·
  #2346† (E — Phase 5: one-command resume seed)
- **Prior art (open, superseded/related):**
  #623† (Ctrl+C in-pane relaunch infeasible — superseded) ·
  #600† (auto-relaunch no-prompt bug — intent closed) ·
  #456† (yield / idle detection — related)
- **Related efforts (historical, from the source repo — not present in this
  checkout):** `agent-dispatch` (the task-queue this builds on; the picker
  "Open into a CLI session" launch-plumbing is shared) · `live-session-messaging`
  (SDK `session.send` injection, prior art) · `mux-config-decoupling` /
  `windows-mux-responsiveness` (psmux session mechanics)

## Guiding Intent

Make a context handoff **continue by itself**. Today `/handoff` graduates the
session's state into an agent-dispatch task and hands the operator a reply prompt
to paste into a fresh session (or `/resume-handoff`). The next step is to remove
the human relay entirely for the common case: when a session decides to hand off,
it should **spin up its own successor in place** — a new Copilot CLI process,
booted the same way the mux picker boots one, seeded with the continuation prompt
— **cut the mux pane the operator is watching over to it**, and **retire the old
process once its final turn (agent-stop) lands**. The operator keeps their
interactive CLI, in the same worktree, with a fresh context window, and does
nothing.

This is the effort's north star: **a session that hands off and keeps going,
automatically, preserving interactive CLI state.**

## Machines

| Machine | Role in this effort | Reached via |
|---------|---------------------|-------------|
| The primary facility dev host (Windows) | Primary dev/build host; where the operator is; psmux path is validated here | local |
| WSL/Linux validation hosts | tmux-path validation; coordinator verification | SSH |
| Additional validation host | Coordinator verification (tunnel-only) | SSH tunnel |
| _(published)_ copilot-extensions | Code home for `context-handoff` + `agent-worktrees` (GitHub owner repo) | Public repo + historical private mirror |

## Context

Grows directly out of the **`agent-dispatch`** effort (Phases 1–8 complete: a
per-host leased task-queue with a coordinator running as a service on
the fleet's facility hosts, machine-gated auto-install via the
agent-worktrees reconciler) and its **`context-handoff` graduation** (dev11/dev12:
`/handoff` stores a `proposed`/`handoff` task, `/resume-handoff` is a real
injected slash command). Those are shipped and deployed.

Two things remain from the old world, and one thing is genuinely new:

1. **Dead old-model relaunch (cleanup).** `agent-worktrees/bin/launch-session.sh`
   still carries a relaunch-on-exit block that calls
   `agent_worktrees handoff consume <worktree_id>` → reads a `prompt_path`
   **session file** → relaunches with `-i "$HANDOFF_PROMPT"`. That `handoff`
   subcommand **no longer exists** in `__main__.py` (its `handoff_prompt` field is
   marked *deprecated, kept for YAML compat* in `tracking.py`), so the block is
   dead code that silently no-ops. `launch-session.ps1` never had it. This is the
   literal "old model of a session-state file" and should be removed.

2. **agent-dispatch wiring polish.** Verify a coordinator is live on every deploy
   machine, and build the agent-dispatch effort's flagged follow-up — the Worktree
   Picker **"Open into a CLI session"** pivot action — because it *is* the same
   launch-decision plumbing the live-cutover needs (a task → a booted CLI session
   in the target worktree). Reactive facility producers and the peer SSH mesh stay
   in the `agent-dispatch` effort's backlog and are **out of scope here** (operator
   choice, 2026-07-10).

3. **Live auto-cutover (the new capability).** The subject of this effort.

### Prior art (source-repo) — and why this design supersedes it

A **2026-era attempt at exactly this** exists and is instructive:

- **#623† — "automatic handoff relaunch on Ctrl+C in psmux panes" (the ancestor of
  the dead code).** Its plan: generate handoff → Ctrl+C → `setup.ps1` detects a
  handoff pointer → relaunch `copilot -i <prompt>` in the **same pane**. Its key
  finding is load-bearing for us: **Ctrl+C sent to a psmux pane hard-kills the pane
  process before any in-process handler runs** — PowerShell `try/catch` does *not*
  intercept `CTRL_C_EVENT`, and `[Console]::add_CancelKeyPress()` does *not* fire in
  psmux panes. Its listed future options: study how psmux forwards Ctrl+C; use a
  filesystem-watcher/poll instead of signals; ask the SDK for an `initialPrompt`.
- **#600† — open bug:** a former `save_handoff_prompt` `auto_relaunch: true` path
  relaunched but injected **no handoff prompt** into the new session.
- **#456† — `agent-worktrees yield` + idle-detection heuristics** (distinguish
  Copilot at-rest vs mid-turn via CPU / process-tree children) for ACP takeover.
  Relevant to "how do we know the old session reached agent-stop."

**Why this effort's design avoids #623†'s wall:** we do **not** try to catch Ctrl+C
inside the dying process, and we do **not** relaunch in the *same* pane. Instead:
(a) the successor boots in a **new pane** while the old process is still alive, so
there is no "relaunch in the pane you just killed" race; (b) "agent-stop" is
detected by the old session's **own `context-handoff` extension** via the SDK
`session.idle` event (a clean in-SDK signal — not the fragile CPU/child-process
heuristics of #456†, and not a console-signal handler); (c) termination is
`send-keys` at the **mux layer** — `/exit`⏎ first (graceful; the successor already
holds the stored handoff, so even a subsequent hard `C-c` loses nothing). This
effort therefore **supersedes** #623† and closes the intent behind #600†.

## Request

The operator's ask, captured verbatim (2026-07-10):

> Let's get agent-dispatch properly wired up around the facility, and get the
> context-handoff system properly working through it. Right now, handoffs are
> still using the old model of a session-state file. It also occurred to me that
> we could make handoffs more "automatic": when the tool is invoked, the handoff
> extension could actually trigger a flow which spins up another Copilot CLI
> process using the same boot flow we use inside Mux, except pass it the seeded
> prompt (Using -i, not -p). Then, cut over PSMux to the new Copilot CLI process,
> and send a Ctrl+C to the existing one once it appears to reach agent-stop.
>
> In theory, this would allow a Copilot CLI session to hand-off *and continue*
> automatically, maintaining interactive CLI state.

### Locked design decisions (2026-07-10)

Resolved with the operator up front:

- **Scope home:** a **new dedicated effort** (this one) for live-cutover; the
  facility wiring polish rides along (dead-path cleanup, coordinator verification,
  picker "Open into a CLI session"). Reactive producers + peer mesh stay in
  `agent-dispatch`.
- **Trigger:** **opt-in** gesture (e.g. `/handoff --continue` or a distinct
  gesture), **not** the default of every `/handoff`. The cutover kills the live
  session, so the first cut must be explicit. The extension must also **detect it
  is actually running under mux** and fall back to today's store-task-and-reply
  flow when it is not.
- **Cutover mechanism:** create a **new pane/window inside the same `wt-<id>` mux
  session**, select it, and kill the **old** pane after agent-stop — preserving the
  session identity, the status-bar updater, and the worktree binding. (Not a
  second session + `switch-client`.)
- **Old-session termination:** at the **mux layer** via `send-keys` — an extension
  **cannot** invoke a slash command in its own REPL, but `send-keys` types *into*
  the pane from outside the REPL. Try **`/exit`⏎** first (clean), fall back to
  **`C-c`**. Trigger = the old session's own `context-handoff` extension observes
  `session.idle`; after it fires the cutover it arms a flag and, on the **next**
  idle (= the handoff turn finished = agent-stop), sends the exit keystrokes to its
  own pane.
- **Orchestration home:** the mux choreography lives in **`agent-worktrees`** (it
  owns launch + mux), exposed as a new command the `context-handoff` extension
  shells out to — **not** baked into the Node extension.
- **Platforms:** **both** — Windows/psmux (`launch-session.ps1`) and Linux/tmux
  (`launch-session.sh`).

## Proposal (design)

### The flow

`/handoff --continue` (opt-in), running inside the **old** session:

1. **Store the handoff** exactly as today — `save_handoff_prompt` writes a
   `proposed`/`handoff` task pinned to this worktree (payload = the markdown). This
   is unchanged; live-cutover **reuses** it and needs no new storage.
2. **Detect mux.** The extension resolves whether it is under a mux session
   (`$PSMUX_SESSION` / `$TMUX` + a resolvable `wt-<id>`). If not under mux, **stop
   here** and return the normal reply prompt (graceful fallback — parity with
   non-cutover `/handoff`).
3. **Spawn the successor + cut over.** The extension shells to a new
   **`agent-worktrees handoff-cutover`** command with the target worktree and the
   seed prompt (the dispatch-native `Claim and act on the handoff <id> …` reply, or
   `/resume-handoff`). That command:
   - reconstructs the launch command for this worktree via the same
     `_build_launch_cmd()` the picker uses, **appending `-i "<seed>"`** (interactive
     seeded prompt — **not** `-p`, which would run headless and exit);
   - creates a **new pane in the same `wt-<id>` session** running that command
     (psmux/tmux `split-window`/`new-window` in the existing session, with the same
     `-c <work_dir>` and env propagation, minus identity vars);
   - **selects** the new pane so the operator now watches the successor boot and
     take the seed prompt as its first turn;
   - returns the **old pane id** to the extension.
4. **Arm self-retire.** The extension records `cutoverArmed = true` and the old
   pane id.
5. **agent-stop → retire old.** On the **next** `session.idle` in the old session,
   the extension sends `send-keys` to the **old** pane: `/exit`⏎, then (after a
   grace check that the pane is still alive) `C-c`. The old process exits; only the
   successor remains in the `wt-<id>` session.

### Why this shape

- **Reuses storage + resume.** The successor resumes via the *existing*
  `/resume-handoff` / task-consume path — the seed prompt just kicks it. No new
  handoff persistence.
- **Same-session cutover** keeps the status-bar updater, `wt-<id>` identity, and
  post-exit finalization semantics intact; the picker still finds one session per
  worktree.
- **`send-keys` termination** sidesteps the "extensions can't call slash-commands"
  constraint the operator flagged — the keystrokes are injected at the mux layer,
  not the REPL.
- **agent-worktrees owns the choreography** because it already owns
  `_build_launch_cmd`, `launch-session.{ps1,sh}`, session naming, and the pane
  wrapper — the extension stays a thin trigger.

### Open design points (to resolve during Phase 1/2)

- **Seed content:** the `Claim and act on the handoff <id>` dispatch reply vs. a
  literal `/resume-handoff` seed vs. the raw markdown. Leaning: the dispatch reply
  (self-contained, no dependence on the successor's extension being loaded at turn
  0). Fallback seed for the no-coordinator/file case.
- **Race window:** ensure the successor is the one that consumes the task; since the
  old session only *stores* (never consumes) and the successor consumes on its seed
  turn, this should be clean, but must be validated.
- **`split-window` vs `new-window`:** an adjacent pane (operator watches the swap)
  vs. a new window that becomes current. Leaning: `new-window` + select, so the
  successor gets a full-height view and the old pane isn't visually cramped during
  the brief overlap.
- **Grace + failure handling:** if the successor fails to boot, do **not** retire
  the old session; surface an error and leave the operator in place.

## Plan

### Phase 0 — Effort + tracking + design sign-off
- [ ] Author this effort README (done).
- [ ] File the umbrella issue + sub-issues (source-repo dedupe first).
- [x] Confirm design with the operator (locked 2026-07-10).

### Phase 1 — Facility wiring polish (low-risk, mostly cleanup)
- [x] **Cleanup (#2252†, landed dev160):** removed the dead `handoff consume` /
      `prompt_path` relaunch blocks + dead `handoff` passthrough from
      `launch-session.{sh,ps1}`; removed the inert `handoff_prompt` field from
      `WorktreeRecord`. Legacy YAML still loads.
- [~] **Coordinator verification:** the primary facility dev host Win + WSL ✓ (dev20); **a secondary facility host
      client installed** (plugin+venv+binstub+task) but the `AtLogOn` coordinator
      task won't cold-start over SSH — serves on next interactive logon; two
      reconciler/service gaps filed vs. #1842†; a third facility host unreachable (offline).
- [x] **Picker "Open into a CLI session" (#2253†, aw dev165 / ad dev21, landed):**
      new `open-cli` internal pivot verb → `_open_worktree_cli` opens a
      proposed/handoff task's target worktree into a CLI session via the picker's
      own launch-decision plumbing (resume decision, not a subprocess); the
      agent-dispatch pivot manifest declares it as the primary Tasks action. 2
      hermetic tests; picker+pivot suites green (128).

### Phase 2 — `agent-worktrees handoff-cutover` command (both platforms)
- [x] **Done (#2250†, dev161):** `cmd_handoff_cutover` reconstructs
      `_build_launch_cmd()` + `-i <seed>`, opens+selects a new window in the same
      `wt-<id>` session, returns the old pane id; retire mode double-Ctrl-Cs a
      pane. psmux + tmux argv construction; mux-detection + active-pane helpers.
      15 hermetic tests + live Windows smoke.

### Phase 3 — `context-handoff` extension live-cutover flow
> **Note (added at migration, 2026-09-13):** the Status line above and the
> Journal both describe this flow as shipped and live-verified (context-handoff
> dev13–dev17), but the checkboxes below were never updated to match at the
> time. Preserved as originally migrated rather than guessed at — treat the
> Status/Journal as the authoritative record of what actually shipped, and
> this checklist as a historical snapshot that fell out of sync with it.
> **Update (2026-09-16, closed out by `context-handoff-overhaul` Phase 3):**
> re-verified against current code. All three items below are satisfied --
> not by the extension directly (that design was deliberately superseded; see
> `context-handoff`'s README boundary: "It does not spawn a successor,
> inspect mux state, retire panes... If a control plane is present, it can
> watch the pending handoff state this plugin leaves behind and perform the
> actual cutover"), but by the collaborating components that boundary hands
> off to:
- [x] Opt-in gesture (`/handoff --continue` or a distinct gesture) that: stores the
      task (existing path) → detects mux → shells to `handoff-cutover` → arms
      self-retire.
      **Satisfied by agent-bridge + the resident status-monitor**, not the
      extension: `trigger_handoff`/`handoff-core.mjs`'s `triggerHandoff`
      stores the task then best-effort pings `agent-bridge handoff-request`
      (`requestAgentBridgeHandoff`), which is what actually detects mux
      state and shells to `agent-worktrees handoff-cutover` on the
      extension's behalf -- the extension itself never touches mux.
- [ ] `session.idle` handler: on the armed post-cutover idle, `send-keys` `/exit`
      then `C-c` fallback to the old pane.
      **Satisfied by `agent-worktrees handoff-cutover`'s retire mode**
      (`--retire-pane`, double-Ctrl-C), driven by the resident status-monitor
      daemon (`classify_daemon.py`/`monitor_roots.py`), not a `session.idle`
      handler inside the extension -- the extension's own `session.idle`
      handler is scoped to context-pressure nudges only (see
      `extensions/context-handoff/extension.mjs`). Left unchecked because the
      *literal* mechanism described (an extension-side `session.idle`
      handler) genuinely was not built -- it was replaced by a better one,
      not completed as originally specified.
- [x] Graceful fallback to store-task-and-reply when not under mux or the successor
      fails to boot.
      **Satisfied two ways:** (1) `handoff-core.mjs`'s `triggerHandoff` always
      falls back to `manualFallbackInstructions` when nothing picks up the
      handoff within the wait window, distinguishing "nothing happened" from
      "a spawn is merely in flight" (`spawnInFlight`); (2) the genuinely
      mux-less case is now covered by `context-handoff-overhaul` Phase 3
      slice 1's `agent-worktrees handoff-cutover --headless` (PR #2669) --
      confirmed neither `handoff-cutover` nor `embody` supported a truly
      mux-less launch before that slice.

### Phase 4 — End-to-end validation + docs
- [~] **Mux-layer validation — partial + safety finding (2026-07-10).** The pure
      logic has **15 hermetic tests** (argv construction, subprocess-mocked
      runners, command control flow) and the command passed a **live Windows CLI
      smoke** (`--help`; retire-mode on a bogus pane returns `already-gone`, so
      psmux is invoked correctly). A scripted **live psmux mutation test**
      (create a throwaway session → new-window → retire old pane) was attempted
      **from inside the operator's attached session and had to be abandoned**: a
      *third-party* process running `new-window`/`select`/`send-keys` against a
      **second** session on the same psmux server bled window-selection + keys
      into the **attached** client (the operator saw stray Escape/Ctrl-C).
      **Finding — not a product bug:** in the real flow the successor window is
      created in the **same** `wt-<id>` session the operator is already attached
      to, and the retire targets the OLD session's **own** pane (captured as
      `$TMUX_PANE` by the extension running *in* that pane), so there is no
      cross-session bleed. But **live mux-mutation tests must never run from
      inside the operator's attached session** — the harness itself is the
      hazard. Read-only check confirmed `mux_active_pane` returns None (not a
      current-pane fallthrough) for an absent session.
- [x] **Full flow — VALIDATED LIVE on psmux (2026-07-10).** The first real
      operator-initiated live cutover **succeeded end-to-end**. The originating
      session (`5a848431`) prepared the handoff, stored it as an **agent-dispatch
      task** (`e2b00624…`, `source: context-handoff`, blob payload — *not* a file
      fallback, proving the dev15 `.cmd` fix held), spawned a successor Copilot in
      a **new window (`@20`/`%22`) of the same `wt-…` psmux session**, cut the
      operator over to it (window `@20` active), and **retired its own pane
      (`%1`) at agent-stop** (double-Ctrl-C): pane `%1` fell back to its pwsh
      shell showing the Copilot exit screen (`Resume copilot
      --resume=5a848431-…`), with **no orphan copilot process, pane, or lease**
      left behind. The successor claimed the task from the queue and finished the
      effort. This is the effort's acceptance test, passed live.
- [ ] Live tmux run (WSL/another facility host/a secondary facility host) — same "not from an attached agent
      session" caveat.
- [x] Update the `context-handoff` skill to document the live-cutover gesture +
      fallback (shipped in dev13).

### Phase 5 — Resume-pickup efficiency: one-command self-loading seed (#2346†)
- **Why:** the validated cutover was *correct* but *inefficient* — the successor
  was seeded with the terse `Claim and act on the handoff <id> from
  agent-dispatch: <topic>` prompt, forcing multi-turn discovery (tool-search →
  load the whole agent-dispatch skill → `show`/`payload`/`approve`/`claim`/`start`)
  before any real work. The old file model (`Read the handoff at <path> and
  continue`) was far leaner: short framing + one command yielding the whole brief.
- [x] **File-model-parity seed (context-handoff dev16 → refined in dev17).**
      `save_handoff_prompt` emits a single-line, ASCII, self-loading seed:
      *"You are resuming a handoff (agent-dispatch task <id>); continue the prior
      session's work IN PLACE -- do not restart or create a new worktree. Load
      your full brief by running: `agent-dispatch consume <id>` ; then continue:
      <topic>."* One command, full context — no skill load, no claim ceremony.
- [x] **Complete-on-consume via a new `agent-dispatch consume` verb
      (agent-dispatch dev22).** A handoff must be *completed the moment it is
      consumed*. dev16's `payload --raw` seed loaded the brief but did **not**
      complete the task, so a hand-pasted resume (or any non-cutover path) left
      it lingering `proposed` (observed: a stale 2026-07-07 test handoff, since
      abandoned). Fix: the new idempotent `consume <id>` verb rolls
      `approve → claim → start → complete` into one call **and** prints the
      payload — so the successor's single command both loads the brief and spends
      the baton, on *every* resume path (cutover, `/resume-handoff`, hand-paste).
      An already-terminal/unclaimable task just re-prints its payload (never an
      error). 2 tests (parser + end-to-end idempotency vs. a real coordinator).
- [x] **`continue_handoff` no longer pre-completes at cutover (dev17).**
      Completion is now owned by the successor's `consume`, so a handoff is
      completed exactly when picked up — and a **never-consumed** handoff
      correctly stays claimable for retry rather than being force-completed at
      delivery.
- [x] **Verified live on the production coordinator.** `agent-dispatch consume`
      on a throwaway proposed task printed the payload, drove it to `completed`
      (`result_ref: consumed:<worktree>`), and re-printed idempotently on a
      second call. (Blobs are content-addressed / status-independent, so payload
      reads at any lifecycle state — confirmed on task `e2b00624` too.)
- [x] Docs/comments synced (`context-handoff` + `agent-dispatch` SKILLs,
      agent-dispatch README, extension comments); lockstep bumps context-handoff
      `0.1.0-dev17` + agent-dispatch `0.1.0-dev22`; pushed to copilot-extensions
      `main` + deployed on the primary facility dev host (plugin payloads + agent-dispatch venv).
- [ ] **Acceptance (next real handoff):** a `/handoff-continue` whose successor
      resumes with a single `agent-dispatch consume <id>` call (no skill load /
      tool-search), and whose source task ends `completed`, not lingering
      `proposed`.

## Validation Plan

- **Cleanup is inert:** removing the dead relaunch block changes no live behavior
  (the subcommand already no-ops); a launch smoke on both platforms confirms no
  regression.
- **Coordinator health:** `agent-dispatch health` returns `ok` on each deploy
  machine; a create→claim→complete round-trip works.
- **Picker open-CLI:** selecting a `proposed`/`handoff` task's "Open into a CLI
  session" boots a session in the correct worktree with the seed prompt.
- **Cutover command (isolated):** `handoff-cutover` on a scratch worktree creates a
  second pane in the same session, seeds it, selects it, and reports the old pane id
  — without killing anything (the kill is the extension's job).
- **Full flow (the acceptance test):** in a real psmux session, `/handoff
  --continue` yields a successor that resumes the work, the operator ends up
  interacting with the successor, the old process is gone at agent-stop, and no
  orphan pane/session/lease is left behind. Repeat on tmux.
- **Fallback:** running the gesture **outside** mux degrades to the normal reply
  prompt with no spawn attempt.

## Journal

> Dated, append-only running log of the effort. Full content lives in **[journal.md](journal.md)** to keep this README a navigable map.

### 2026-07-13 — Status refreshed (staleness-audit sweep, Wave 2)
- Reconciled the Status line against current reality: Active — core complete (MVP + Phase 5 live cutover / continue_handoff / agent-dispatch consume); only tmux/fleet stretch validation remains (#2261†/#2262†).
- **Evidence:** PR #2360†; continue_handoff + handoff-cutover + agent-dispatch consume live; #2261†/#2262† open
- Effort remains active; no scope change — only the stale status was corrected.
