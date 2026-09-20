# agent-bridge CLI-Mode Sessions

- **Slug:** `agent-bridge-cli-mode-sessions`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `main`
- **Created:** 2026-09-19
- **Status:** Active
- **Vision:** [`visions/remote-interactive-sessions`](../../../visions/remote-interactive-sessions/README.md)
  (leaf, child of [`visions/agent-fabric`](../../../visions/agent-fabric/README.md))
  — realizes *Session Host CLI mode*, *CWD-keyed discovery*, *symmetric venue
  launch*, and the *opt-in-not-ambient-default* / *bind-dont-self-register* /
  *one-host-per-cwd-lane* behaviors. Also advances
  [`visions/session-hosting`](../../../visions/session-hosting/README.md)'s
  existing `interchangeable-session-hosts` / `capability-honest-control`
  features by making a CLI-hosted session in a remote venue a fully
  interchangeable peer of a local one.
- **Related (independent, not a dependency):**
  [`visions/host-resource-providers`](../../../visions/host-resource-providers/README.md)
  — provisioning local capabilities to a coordinated session. Deliberately
  out of scope here (see *Guiding Intent* and *Request*).
- **Related effort (complementary, not overlapping):**
  [`agent-bridge Session Discovery`](../agent-bridge-session-discovery/README.md)
  (#2530) — that effort lets a caller **find an already-running** session
  across machines/repos. This effort is about **creating and binding** a new
  CLI-mode session; the two compose (a CLI-mode session, once running, is
  discoverable through #2530's primitives like any other) but neither depends
  on the other.
- **Deferred out (tracked separately, not blocking):**
  [#2971](https://github.com/ThomasMichon/copilot-extensions/issues/2971) —
  a represented interactive session's `ask_user`/elicitation remaining
  unanswerable remotely (read-only, take-over-only). See Phase 1's journal
  entry for why this isn't pursued here.

## Guiding Intent

Give agent-bridge a mode where it can **natively create and drive a standard,
muxed, interactive Copilot CLI session** — reusing its existing Session Host,
reattach, and multi-observer coordination mechanics as-is, rather than a
parallel execution protocol. This is deliberately an **explicit, per-request
capability an operator opts into for one session**, not a default shape
delegated/headless work should ever pick up automatically.

**Standing design constraint (carries through every phase):** the CLI-side
extension (`extensions/agent-bridge/extension.mjs`) must remain **short-lived
and event-based** — quick, non-blocking actions on a timer or event handler
that immediately delegate any real work to agent-bridge's daemon or a Session
Host process. It must never hold open a long-running connection, watch, or
blocking operation *in the extension-host process itself*: doing so risks the
extension-host locking its own plugin directory (observed as a real failure
mode; agent-bridge's current posture avoids it and must keep avoiding it).
This directly shapes Phase 2/3: the CLI extension's cwd-keyed discovery lookup
must be a fast, one-shot read/query at startup, not a persistent watch: actual
connection and lifecycle management for the bound Session Host lives in
agent-bridge's daemon or the Session Host process, never held open inside the
CLI extension.

## Context

Two known, already-documented gaps currently block treating a CLI-driven
interactive session as a first-class coordinated peer
(`plugins/agent-bridge/docs/delegation-contract.md`):

- *"A registered CLI extension polls a durable inbox and may inject a
  `session.send` turn. Prompt control is experimental until single-stream
  admission is proven."* — a represented interactive peer's `send` can
  currently race/split-stream against other admission sources.
- *"`input_required` ... Boundary is durable; answerability is live and
  becomes `unknown_after_restart` when it cannot be recovered."* — a
  CLI-native `ask_user`/elicitation prompt is not reliably routable to
  whichever client is actually attached.

Separately, the coordination layer's own CLI extension currently resolves
which daemon to bind to **ambiently** rather than through an explicit
assignment — workable for the common case (one daemon, one machine) but with
no answer for "which Session Host does *this* muxed CLI process belong to"
once more than one is in play (a remote venue, a pre-allocated host waiting to
be claimed).

This effort was scoped after reviewing an alternative proposal (a
bridge-owned "native execution" PTY protocol with its own terminal-replay
format, writer/observer/takeover model, and host-resource RPC) against what
already exists: ACP's existing multi-observer coordination, the existing
Session Host's reattach/retirement mechanics, and the fabric's existing
[`single-current-session-per-worktree`](../../../visions/agent-fabric/README.md#single-current-session-per-worktree)
invariant (at most one current session per working directory, already
enforced) — which together make a cwd-keyed discovery assignment sound
without inventing new machinery. See
[`visions/remote-interactive-sessions`](../../../visions/remote-interactive-sessions/README.md)
for the full reconciliation and the resulting north star.

## Request

> Fix up remote-drive of a CLI session as a mode for regular agent-bridge —
> not something most agents should reach for by default, but available
> per-request by the operator. Decouple this entirely from providing more
> local resources/tools to the remote session, which is a separate concern.

## Plan

### Phase 1 — Fix the blocking ACP gap

Prerequisite correctness work, useful independent of CLI mode itself: a
represented interactive peer should already be reliable before it becomes the
mechanism CLI mode binds through.

- [x] Fix `send` single-stream admission for a represented interactive peer so
      an injected `session.send` turn cannot race or split-stream against
      another admission source. Add the regression the delegation contract's
      "experimental until proven" note is waiting on.
      **Done** — traced the actual root cause against
        `copilot-agent-runtime`'s send-admission path
        (`session_send_dispatch.rs::apply_public_send_admission`,
        `SendRequest.source` in the generated API): a `session.send()` call
        with no explicit `source` defaults to `source: "user"` for an
        immediate/visible send, making a bridge-delivered inbox message
        indistinguishable from the operator's own live keystrokes at the
        contention/steering layer — the actual collision the "experimental"
        note describes. Fixed by tagging every delivery with the runtime's
        own already-documented `agent-<agent-id>` provenance convention
        (`source: "agent-bridge"`). Extracted the pure options-building logic
        into `extensions/agent-bridge/delivery.mjs` (mirroring
        context-handoff's core-module split, since `extension.mjs`'s
        top-level `joinSession()` makes it untestable directly) and added
        `tests/delivery.test.mjs`. Version bumped to `0.4.0-dev502`.
        Preserves the short-lived/event-based extension posture: `pollInbox`
        remains the same periodic, quick, non-blocking timer callback; the
        added `delivery.mjs` import is a plain static ES module with no I/O
        or watches of its own — no new extension-host directory-lock risk.
- [x] ~~Make `ask_user_request`/elicitation answerability durable and
      correctly routed to whichever client is currently attached~~ —
      **deferred, not fixed here.** Investigation
      (`plugins/agent-bridge/src/agent_bridge/live_representation.py`,
      `@github/copilot-sdk`'s `onUserInputRequest`/`onElicitationRequest`)
      found the current read-only/take-over-only behavior for a represented
      interactive session's `ask_user`/`permission.requested` is not a fixable
      routing bug: `joinSession()`-registered handlers only intercept requests
      from the *extension's own* contributed tools (proven by the existing
      `onPermissionRequest: approveAll` registration, which explicitly does
      not touch the operator's own tool calls), not the live session's real
      `ask_user` flow. That read-only fallback was adopted because SDK-level
      propagation for an extension-*joined* (not extension-*created*) session
      couldn't be gotten working previously — not derived from a
      first-principles safety requirement, so it's worth revisiting, just not
      as part of this effort. Filed
      [ThomasMichon/copilot-extensions#2971](https://github.com/ThomasMichon/copilot-extensions/issues/2971)
      to track a future revisit. Current behavior (operator reconnects and
      takes over) is acceptable to live with meanwhile and already matches
      this vision's `degrade-to-direct-reconnect-honestly` Behavior.
- [x] Phase 1 complete: the send-admission fix landed; the elicitation item is
      knowingly deferred (#2971), not blocking. CLI mode (Phase 2+) may
      proceed.

### Phase 2 — Session Host CLI mode + cwd-keyed discovery

- [x] **Design refinement, discovered during implementation:** for the
      local, single-machine case, no client-side discovery-file mechanism is
      needed. The CLI extension already resolves and sends `worktree_id`
      with every registration (`resolveMetadata()` →
      `agent-worktrees get worktree-dir`); the daemon now correlates that
      against a pending reservation **entirely server-side, atomically, at
      registration time** — satisfying §opt-in-not-ambient-default and
      §bind-dont-self-register with zero `extension.mjs` changes. A
      client-side discovery artifact remains the right mechanism once a
      *remote* venue's own daemon differs from the host's — that is Phase 4's
      concern, not duplicated here. The vision's "CWD-keyed discovery"
      concept is realized as **worktree-id-keyed reservation + server-side
      correlation** for the local case, consistent with the fabric's own
      `single-current-session-per-worktree` invariant (worktree, not raw
      filesystem path, is the actual identity unit).
- [x] Add a CLI mode to Session Host allocation: the coordination layer
      allocates and prepares a host **without** spawning a `copilot --acp`
      child, leaving it waiting to be claimed.
      **Done** — a new `cli_mode_reservations` table (schema v17→v18) records
      an explicit, operator-initiated reservation for a worktree's next
      CLI-mode session, created via `agent-bridge live-sessions cli-mode
      reserve --worktree-id ID` or `POST
      /api/v1/live-sessions/cli-mode-reservations/{worktree_id}` — before any
      CLI process exists. `create_cli_mode_reservation` is atomic and
      one-per-worktree (§one-host-per-cwd-lane): a not-yet-expired
      reservation refuses a second create; an expired one (claimed or not) is
      silently reclaimable.
- [x] ~~Add a durable, host-local mapping from a working directory to its
      currently assigned Session Host address~~ — superseded by the
      refinement above: the mapping is the `cli_mode_reservations` row
      itself, keyed by `worktree_id`, read entirely server-side.
- [x] ~~Update the coordination layer's CLI extension to resolve its own cwd
      against that mapping at startup~~ — not needed for the local case (see
      refinement above); `extension.mjs` is unchanged. Still applies to
      Phase 4's remote-venue case, where the extension genuinely needs to
      resolve a different daemon.
- [x] Enforce allocate-before-launch ordering and one-host-per-cwd-lane,
      reusing the existing single-current-session-per-worktree gate rather
      than a new one.
      **Done** — `register_live_session` attempts
      `claim_cli_mode_reservation` after its existing atomic upsert succeeds;
      claiming is additive and never blocks or reverses that decision. A
      second registration for the same worktree (e.g. a successor session)
      registers normally but does not steal an already-claimed reservation
      (`claim_cli_mode_reservation`'s `claimed_by_session_id IS NULL` guard).
- [x] Confirm a CLI-mode-bound session is indistinguishable from a
      directly-spawned one to every consumer above the host boundary (same
      reattach, retirement, and observation code paths; no forked protocol).
      **Done** — `cli_mode` is purely an additional, honest marker on the
      existing `live_sessions` row (`LiveSessionInfo.cli_mode`); no new
      session type, lifecycle, or protocol was introduced. All existing
      reattach/observation/messaging routes are unchanged and apply
      identically regardless of `cli_mode`.

### Phase 3 — Opt-in local launch surface

- [x] Add an explicit, per-request CLI/API surface to start a CLI-mode
      session locally first — proves the mechanism end-to-end before adding
      any remote venue.
      **Done, redesigned once** — `agent-bridge live-sessions cli-mode
      launch --worktree-id ID [--seed TEXT] [--driver LABEL]
      [--verify-timeout N]`. First cut (superseded, see journal) ran a bare
      foreground `copilot` subprocess with no reattach at all. Corrected
      after operator pushback ("no point moving to venues until we get
      session-host and all that jazz working first"): `_launch_cli_mode_session`
      now reserves the worktree, then hands off to **`agent-worktrees
      embody`** — the existing DETACHED, mux-wrapped (tmux/psmux),
      resume-aware, cross-platform interactive-`copilot` launch path a human
      already gets from the ordinary worktree/mux flow — instead of
      reinventing a second one. `embody` never attaches itself, so the call
      returns promptly and the launched session survives the invoking
      process exiting; an operator attaches with `tmux`/`psmux
      attach-session -t wt-<id>` directly. No Session Host pre-spawn, no new
      terminal/execution protocol — this is literally the same launch path
      the local worktree/mux flow already uses, matching the vision's own
      language ("rides... the same way the local worktree/mux launch path
      already does").
- [x] Confirm this is purely additive: no existing headless/ACP-driven default
      path changes behavior when CLI mode is never requested.
      **Confirmed** — the new code path lives entirely in a new helper +
      a new `cli-mode launch` argparse branch; `_connect_via_session_host`,
      `spawn_local`, `resolve_local_launch`, and every ACP-driven route are
      untouched. Full suite (post-redesign): 2585 passed (5 new), 28
      skipped, the same 3 pre-existing unrelated failures confirmed present
      without this change too (bisected via `git stash`).
- [x] Mark a CLI-mode-bound session with its durable human-attended marker
      (for later recovery/observation guidance honesty).
      **Already covered by Phase 2** — `live_sessions.cli_mode` is set by the
      daemon's own claim-on-registration logic regardless of how the CLI
      process was started (by hand or via this new `launch` verb), so no
      separate marking step was needed here.

### Phase 4 — Symmetric venue launch

- [ ] Extend `agent-codespaces` and `agent-containers` to offer the same
      CLI-mode launch shape — prepare the venue, allocate the paired CLI-mode
      Session Host, start a standard muxed CLI process bound to it — over the
      existing venue-parity SSH transport and auth-relay back-channel. No new
      venue-specific transport.

### Phase 5 — Docs and vision closure

- [ ] Update `plugins/agent-bridge`, `agent-codespaces`, and
      `agent-containers` docs/architecture for the new mode and discovery
      mapping.
- [ ] Confirm the realized behavior against
      `visions/remote-interactive-sessions`; no vision revision expected
      (this closes existing intent) unless implementation surfaces a genuine
      should-be gap.

## Validation Plan

- [x] A represented interactive peer under concurrent `send` pressure never
      produces a split-stream turn (regression covering the Phase 1 fix) —
      covered by `tests/delivery.test.mjs`.
- [ ] ~~A CLI-native `ask_user`/elicitation prompt reaches whichever client is
      currently attached, including across a reconnect~~ — deferred with the
      Phase 1 item; tracked in
      [#2971](https://github.com/ThomasMichon/copilot-extensions/issues/2971),
      not this effort's validation surface.
- [ ] A locally-launched CLI-mode session reattaches, is observed by a second
      client, and is retired using the exact same code paths as an ordinary
      directly-spawned Session Host session — no CLI-mode-specific behavior
      divergence.
      **[x] Closed with a live clean-room pass, using the redesigned
      `embody`-backed launch** — see the 2026-09-20 "Phase 3 redesign:
      genuine mux/reattach via `agent-worktrees embody`" journal entry. A
      real, genuinely interactive `copilot` process launched via `cli-mode
      launch` inside a disposable Docker clean room ran in an actual
      **detached tmux session** (`wt-<id>`), registered against the
      pre-existing reservation, was observed live via `tmux capture-pane`
      (proving a real, reattachable TUI — not a fire-and-forget subprocess),
      showed `cli_mode: true` to a second independent `agent-bridge
      live-sessions list` call while still live, and cleanly tore down (both
      the bridge registration `DELETE` and the tmux session itself) on
      `/exit`. `test_cli_mode_reservations.py` (23 cases) and
      `test_cli_mode_launch.py` (5 cases, fakes) cover the deterministic
      layer underneath.
- [ ] Two concurrent CLI-mode allocation attempts for the same cwd resolve
      through the existing single-current-session-per-worktree gate (reuse,
      hand-off, or sunset) rather than racing.
- [x] With CLI mode never requested, an existing headless/ACP-driven session's
      behavior is provably unchanged (no regression from the Phase 2/3
      additions) — confirmed: full pytest suite (568 passed, 2 pre-existing
      unrelated failures) shows no regression, and
      `test_registration_with_no_reservation_is_ordinary` explicitly proves
      an ordinary registration is byte-for-byte unaffected when no
      reservation exists.
      **Reconfirmed with Phase 3's additions in place** — full suite: 2584
      passed (4 new from `test_cli_mode_launch.py`), 28 skipped, the same 3
      pre-existing unrelated failures (bisected via `git stash` to confirm
      they reproduce identically without this change).
- [ ] A CLI-mode session launched via `agent-codespaces`/`agent-containers`
      binds, reattaches, and is observed identically to the local case, over
      the existing venue-parity transport.

## Proposal

Realized for the local case: worktree-keyed reservation + server-side claim
at registration time (Phase 2), plus an opt-in local launch verb that
automates reserve-then-run for a real interactive `copilot` process (Phase
3). Still pending: Phase 4's remote-venue discovery mechanism and its
symmetric venue-launch surface (needed once a venue's own daemon differs
from the host's).

## Journal

### 2026-09-20 — Phase 3 redesign: genuine mux/reattach via `agent-worktrees embody`

Operator pushback after Phase 3's first cut and its live validation: "no
point moving to venues until we get session-host and all that jazz working
first." Right call — the first `_launch_cli_mode_session` ran a **bare
foreground `subprocess.run`** with inherited stdio: if the invoking terminal
died, the session was just gone. No reattach, no mux, despite the vision's
own language calling the launched process a "muxed" CLI process and saying
CLI mode should ride "the same way the local worktree/mux launch path
already does." The prior live validation (previous journal entry, below)
only "worked" because I manually wrapped the test in `tmux` myself, outside
the actual code path — that was validating my test harness, not the shipped
mechanism.

Investigated how `agent-worktrees` already solves exactly this problem for
ordinary local sessions: **`agent-worktrees embody --worktree-id <id>`**
(`plugins/agent-worktrees/src/agent_worktrees/__main__.py`'s `cmd_embody`) is
the existing programmatic, agent-facing entry point — spawns (or resumes) a
**DETACHED** `wt-<id>` mux session (tmux on POSIX, `psmux` on Windows via
`sessions._mux_bin`), running the SAME launch command a human gets from the
worktree/mux flow, and never attaches itself. The bridge-side truth (Copilot
self-registers via `extension.mjs`) and the mux-side truth (`wt-<id>` exists)
are two independently-verifiable facts joined only by worktree id — exactly
the shape Phase 2's reservation already assumes.

Redesigned `_launch_cli_mode_session` to reserve, then shell out to `embody`
(`--worktree-id`, `--json`, `--driver` default `"cli-mode"`, optional
`--seed`, `--verify-timeout`) instead of spawning `copilot` directly. This is
a ~10-line change that deletes an entire (wrong) execution path rather than
adding one: no bridge-side mux/attach logic, no cross-plugin Python import
(kept the existing shell-out convention other cross-plugin calls in this
harness already use) — `embody` owns 100% of the mux mechanics, resume
semantics, and cross-platform behavior. Reattach needs zero new bridge
surface: an operator runs `tmux`/`psmux attach-session -t wt-<id>` directly,
exactly as the ordinary worktree/mux flow already documents.

Rewrote `tests/test_cli_mode_launch.py` (5 cases: reserve-then-embody
argv/flags, seed/driver/verify-timeout forwarding, 409 conflict propagation
without ever invoking `embody`, non-zero embody exit code, non-JSON embody
stdout tolerance) — all fakes, no real `agent-worktrees`/`copilot`. Full
suite: 2585 passed (5 new, net +1 over the prior cut), 28 skipped, same 3
pre-existing unrelated failures (re-bisected). `module-size-baseline.json`
widened again for `__main__.py` (6837 → 6865).

**Re-ran the live clean-room validation against the redesign** (fresh
container, same recipe as before) and hit a real, useful surprise:
`agent-worktrees`'s own **self-provisioning-on-first-use** path is
deliberately "lean" — `bash scripts/install.sh provision` explicitly logs
"tools only, no launcher/hooks" and skips `deploy_wrappers` (the step that
deploys `launch-command.sh`/`default-setup.sh` into
`~/.agent-worktrees/scripts/`). `embody` failed with a plain "No such file
or directory" until the **full** `bash scripts/install.sh install` ran once
for the project (deploys wrappers, hooks, session scripts). This is not a
bug in this effort's code -- it's a real prerequisite of `agent-worktrees`'s
own lean/full install split that a from-scratch clean-room recipe has to
satisfy explicitly; a normally-onboarded harness would already have run full
`install`. Noted here so a future clean-room scenario for this flow bakes it
in rather than rediscovering it.

With the full install present, the redesigned `launch` produced a **real
detached tmux session** (`wt-<id>`), confirmed live via `tmux capture-pane`
(an actual rendered Copilot TUI, "agent-bridge live-session extension
loaded"), claimed the reservation, showed `cli_mode: true` / `driven_by:
"cli-mode"` to an independent `agent-bridge live-sessions list` call, and on
`/exit` both deregistered from the bridge (`DELETE`) **and** tore down the
tmux session itself (`no server running` -- the session was truly gone, not
merely detached). This is the validation Phase 3 actually needed: reattach
is real, not simulated by the test.

### 2026-09-19 — Phase 3 live validation (Docker clean room, superseded design)

**Superseded by the redesign above** -- this entry validated the FIRST cut
of `_launch_cli_mode_session` (bare foreground subprocess), which has since
been replaced by the `agent-worktrees embody`-backed design. Kept for
history; the finding about `copilot -p` not loading extensions still holds
and was the reason this session used `tmux` manually below -- exactly the
gap the redesign closes properly.

Ran the genuinely live check the unit tests couldn't: a disposable clean-room
container (`tools/clean-room`'s `base` image, built with the internal npm
feed since this machine is governed), our worktree mounted **read-write** at
`/harness` (a `:ro` mount breaks an editable `uv pip install`'s
`egg_info` step — a clean-room-mount nuance, not a product bug),
`copilot plugin marketplace add /harness` (a local **directory** marketplace
source — `copilot plugin install` confirmed "loaded live from /harness...
nothing was copied", so this ran our actual worktree code, not a stale
published build), then `agent-bridge`+`agent-worktrees` installed and
provisioned (needed `UV_INDEX_URL`/`UV_DEFAULT_INDEX` pointed at this
machine's internal PyPI proxy, mirrored from `pip config list`, for the same
reason the README documents for any governed box).

Registered a scratch repo, created a real worktree, then ran:
```
agent-bridge live-sessions cli-mode launch --worktree-id <id> --cwd <worktree>
```
**First attempt used `copilot -p "..." --allow-all-tools` for speed and
showed NO registration at all** — traced to a real, useful finding: `copilot
-p` (the one-shot prompt mode) does not load CLI extensions the way a genuine
interactive session does (`extension.mjs`'s own header already says as much
for ACP mode; empirically the same holds for `-p`). This is not a Phase 3
regression — headless `-p` was never CLI mode's target shape — but it means
`-p` is a bad stand-in for validating this specific mechanism. Corrected by
driving a **real interactive session inside `tmux`** (a genuine PTY,
confirmed via `tmux capture-pane` rendering copilot's actual TUI).

Result: the interactive `copilot` process registered itself completely
ordinarily (no code change, no special-casing); `agent-bridge live-sessions
cli-mode status` showed the reservation `claimed by <session-id>`; a second,
independent `agent-bridge live-sessions list` call (simulating a second
observer) showed `"cli_mode": true` for the live session; `/exit` inside the
TUI cleanly `DELETE`d the session (confirmed in `agent-bridge.log`) — the
exact same register/observe/deregister path any ordinary interactive session
uses, with zero CLI-mode-specific divergence. This closes the previously-open
Validation Plan item honestly, with real evidence rather than a claimed pass.

Container torn down after (`docker rm -f`); nothing persisted outside the
disposable clean room.

### 2026-09-19 — Phase 3: opt-in local launch surface (superseded design)

**Superseded by the 2026-09-20 redesign above** (bare foreground subprocess
→ `agent-worktrees embody`-backed genuine mux/reattach). Kept for history.

Added `agent-bridge live-sessions cli-mode launch --worktree-id ID [--cwd
DIR] [-- extra copilot args]`. Deliberately the thinnest possible surface: a
testable `_launch_cli_mode_session(client, worktree_id, cwd, ...)` helper
calls the existing `create_cli_mode_reservation` (so a 409-active conflict
surfaces exactly like plain `reserve`), then runs a standard `copilot`
process in the foreground with **inherited stdio** at `cwd` (reusing
`transport._find_copilot`/`_wrap_batch_for_windows` for executable
resolution, matching the existing local-spawn path's own Windows batch-file
handling). No Session Host is pre-spawned and no new terminal/execution
protocol is introduced — the already-existing, byte-for-byte-unchanged
`extension.mjs` ambient self-registration and the daemon's Phase 2
server-side claim are what actually bind the resulting session. This verb is
pure automation of two manual steps (`cli-mode reserve`, then run `copilot`)
an operator could already perform by hand; after `copilot` exits it queries
and prints the final reservation state so an operator can see the claim in
one place. `copilot_args` (after a literal `--`) get forwarded so a resumed
or profiled launch is possible.

Considered and rejected: pre-spawning a real Session Host wrapping the plain
interactive `copilot` child (mirroring the headless
`_connect_via_session_host` path byte-for-byte). Rejected because a Session
Host's own `_spawn_child` pipes the child's stdio (`stdin=PIPE`,
`stdout=PIPE`) for a program-driven ACP reader, not a human terminal — making
it actually interactive would need a new raw-terminal relay/protocol, which
the vision's own Non-Goals section explicitly rules out ("Not a new
execution or terminal protocol"). The realized design instead reuses exactly
the one thing that already IS reattachable/multi-observer/retirable without
any new protocol: the `live_sessions` row itself, via the same registration
path every interactive session already goes through.

`tests/test_cli_mode_launch.py`: 4 cases (reserve-then-run wiring incl. cwd,
extra-args forwarding, 409 conflict propagation without ever spawning
`copilot`, and non-zero exit code surfacing) using a fake client and a fake
process runner — no real HTTP server or `copilot` process needed. Full
agent-bridge suite: 2584 passed (4 new), 28 skipped, and the same 3
pre-existing, unrelated failures from before this change (confirmed
identical via `git stash`/re-run bisection) —
`test_bootstrap_check_reconcile_opt_in.py`'s two cases and
`test_service_dynamic_recovery.py`'s one case, all a pre-existing lambda/
signature mismatch unrelated to CLI mode. `module-size-baseline.json`
widened for `__main__.py` (6743 → 6837 lines) per the shrink-only-baseline
convention, following Phase 2's own precedent.

Remaining honestly open for Phase 3's own validation surface: a truly live
run (a human, or a manual pass) actually launching a real `copilot` process
through this verb and confirming reattach/second-observer/retire all work
identically to a headless session — this is not something a unit test should
fake end-to-end, since the entire point of CLI mode is a real interactive
terminal. Left open in the Validation Plan rather than claimed closed.

Next: Phase 4 (symmetric venue launch — `agent-codespaces`/`agent-containers`
offering the same launch shape over the venue-parity SSH transport, which is
where the client-side discovery mechanism deferred from Phase 2 becomes
necessary).

### 2026-09-19 — Phase 2 (local case): CLI-mode Session Host reservations

Implemented the local half of Phase 2. Key design refinement discovered
during implementation (recorded in the vision's own provenance): the
original "cwd-keyed discovery file" sketch turned out to be unnecessary for
the single-machine case. The CLI extension already resolves and sends
`worktree_id` with every `/api/v1/live-sessions` registration; the daemon now
correlates that against a pending `cli_mode_reservations` row **entirely
server-side, atomically**, at registration time. This satisfies
§opt-in-not-ambient-default and §bind-dont-self-register with **zero
`extension.mjs` changes** — the short-lived, event-based extension posture
constraint didn't even come into play for this phase's local slice, since
nothing needed to be added there at all. A client-side discovery step remains
correctly deferred to Phase 4 (remote venues, where the extension genuinely
needs to resolve a different daemon).

Followed the existing `worktree_ownership`/`reserve_worktree_ownership`
(#2912) primitive's atomic-SQL idiom as precedent, but did not reuse it
directly — it's keyed by an already-existing ACP `sessions.id`, while a
CLI-mode reservation is made *before* any session exists. Built a parallel,
analogous `cli_mode_reservations` table instead (schema v17→v18):
`create_cli_mode_reservation` (atomic, one-per-worktree, expired rows
silently reclaimable), `claim_cli_mode_reservation` (atomic,
idempotent-safe), `get_`/`release_cli_mode_reservation`. Wired
`register_live_session` to attempt a claim after its existing atomic upsert
succeeds -- additive, never blocking or reversing that decision -- and added
`live_sessions.cli_mode` as the durable, honest marker
(`LiveSessionInfo.cli_mode`). New CLI surface: `agent-bridge live-sessions
cli-mode {reserve,status,release} --worktree-id ID`; new routes: `POST/GET/
DELETE /api/v1/live-sessions/cli-mode-reservations/{worktree_id}`.

`tests/test_cli_mode_reservations.py`: 23 cases (reservation
creation/expiry/replacement, atomic claim-on-register including idempotent
re-registration and a non-claiming second/successor session, and the three
new routes plus the register route's `cli_mode` marker) -- all pass. Full
agent-bridge suite: 568 passed (23 new), 2 pre-existing unrelated failures
(confirmed via the same bisect method as Phase 1), 2 skipped. Version bumped
to `0.4.0-dev503`. `module-size-baseline.json` widened for the three files
this grew (`__main__.py`, `client.py`, `models.py`) per CONTRIBUTING.md's
shrink-only-baseline convention; an unrelated, pre-existing
`picker_tui/engine.py` baseline violation (untouched by this change) remains
and is not this effort's concern.

Next: Phase 3 (opt-in local launch surface -- a CLI/API verb that actually
starts a muxed CLI process bound to a reservation, proving the mechanism
end-to-end).

### 2026-09-19 — Phase 1 closed: elicitation fix deferred, not pursued

Investigated whether the represented interactive session's read-only,
take-over-only `ask_user`/`permission.requested` behavior
(`live_representation.py`) was a fixable routing bug. Traced
`@github/copilot-sdk`'s `onUserInputRequest`/`onElicitationRequest`
(`joinSession`/`ResumeSessionConfig`) and confirmed, using agent-bridge's own
existing `onPermissionRequest: approveAll` registration as proof, that
`joinSession()`-registered handlers only intercept requests from the
*extension's own* contributed tools — never the live session's real
conversation. Operator direction: the current read-only/take-over-only
behavior was adopted historically because propagating elicitation through the
SDK for an extension-*joined* session couldn't be gotten working at the time,
not derived from a first-principles safety requirement — so it's a known,
acceptable-for-now limitation worth revisiting later, not a bug to chase
inside this effort. Filed
[#2971](https://github.com/ThomasMichon/copilot-extensions/issues/2971) to
track a future revisit and stopped pursuing it here. **Phase 1 is complete**:
the send-admission fix landed (previous entry); this item is knowingly
deferred. Phase 2 (Session Host CLI mode + cwd-keyed discovery) is next.

### 2026-09-19 — Phase 1 first fix: send single-stream admission

Traced the "experimental until single-stream admission is proven" gap to its
actual mechanism using a local `copilot-agent-runtime` checkout:
`session_send_dispatch.rs::apply_public_send_admission` defaults an
immediate/visible `session.send()` with no explicit `source` to
`source: "user"`. `agent-bridge`'s inbox-delivery poller
(`extensions/agent-bridge/extension.mjs::pollInbox`) called `session.send()`
with no `source` at all, so every delivered peer message was silently
admitted as if the operator had typed it — indistinguishable from real
keystrokes at the contention/steering layer. `SendRequest.source`'s own
generated-API doc comment already documents the fix: `agent-<agent-id>` is a
first-class, non-`user` provenance tag (`is_agent_source` /
`getAgentMessageSourceAgentId` on the runtime side accept any non-empty
suffix). Tagged every delivery with `source: "agent-bridge"`, extracted the
previously-inline rendering/options logic into a new, directly-testable
`delivery.mjs` (the existing `extension.mjs` cannot be imported by a test
because of its top-level `await joinSession(...)`, the same reason
context-handoff already splits its extension into a core module + entrypoint),
and added `tests/delivery.test.mjs`. Bumped `agent-bridge` to `0.4.0-dev502`
(plugin.json + pyproject.toml + marketplace.json). Full `pytest` suite for
agent-bridge: 549 passed, 2 pre-existing unrelated failures (confirmed via a
`git stash` bisect — `test_bootstrap_check_reconcile_opt_in.py`, a bash/nohup
environment quirk unrelated to this change), 2 skipped. New `node --test`
suite: 5/5 passed. `check-version-bump`, `check-version-consistency`,
`check-docs-consistency`, and `check-module-size` all pass.

Still open in Phase 1: the `ask_user`/elicitation routing durability fix.

### 2026-09-19 — Kickoff

Effort created from a PR-triage session that compared an alternative
bridge-owned "native execution" PTY proposal against reusing agent-bridge's
existing ACP/Session Host mechanics. Split the resulting vision into
`remote-interactive-sessions` (this effort's target) and the independent
`host-resource-providers` (local-capability provisioning, explicitly out of
scope here) at operator direction, since driving a CLI-mode session and
provisioning local resources to a coordinated session are separable concerns
that should not gate each other. Noted the complementary (not overlapping)
relationship to the existing `agent-bridge-session-discovery` effort (#2530):
that effort finds already-running sessions; this one creates and binds new
CLI-mode ones.
