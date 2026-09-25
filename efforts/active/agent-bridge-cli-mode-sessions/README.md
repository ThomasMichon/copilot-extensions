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
- **Related design input (picker-side, deferred implementation):**
  [`picker-venue-pivots` Phase 5 design note](../picker-venue-pivots/phase5-new-venue-embody-design.md)
  — records the venue-picker contract for **New codespace / New container**
  (target-info prompt -> provider provision call -> hand off to the existing
  venue `copilot` path). That note is intentionally design-only there; this
  effort owns the reusable CLI-mode session-launch machinery the handoff
  should land on.
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
- [x] ~~Course correction (2026-09-20), reopens this phase's discovery
      design~~ — **superseded same-day, see below.** Proposed promoting a
      claimed reservation into `host_index`; a closer look at
      `host_index.py` found it's specifically the daemon's map of processes
      *it spawned* (dialable port, `host_pid`/`child_pid` liveness) — not a
      fit for a self-registered session. `live_sessions` (which CLI mode
      already correctly uses) is the right existing mechanism; see the
      corrected finding under Phase 4 and the vision's newest Provenance
      entry for what's actually still missing (cross-venue network
      reachability + reattach metadata, not a second registry to unify with).

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

- [x] **Venue-prep checklist established, from a real venue, not a guess.**
      Checked a live, currently-running operator CodeSpace (an
      Ubuntu 24.04 devcontainer) directly rather than assuming: `copilot` is
      present (devcontainer convention — `nvm`-installed, v1.0.86), as are
      `git`/`node`/`python3`/`uv`. **`tmux` is NOT present**, though `apt`
      has it (`tmux 3.4-1ubuntu0.1`) and passwordless `sudo` works.
      `agent-worktrees`/`agent-bridge` binstubs are absent too, matching the
      already-known "lean, tools-only self-provisioning" pattern (Phase 3's
      journal) — not remote-specific.
      Fixed the `tmux` half now, ahead of the rest of Phase 4, since it's a
      real gap independent of which venue provider gets built first: added
      `agent_worktrees.sessions.ensure_mux_available()` (best-effort,
      POSIX-only, `apt-get`/`dnf`/`yum`/`apk` via `sudo -n` or direct-as-root,
      silent-safe), gated behind an **explicit `--ensure-mux` opt-in** on
      `embody` (never ambient — an operator running a BYO terminal/session
      manager, e.g. Herdr, on their own machine must never have tmux
      installed underneath them by an ordinary `embody` call; see the
      2026-09-20 "Gate `ensure_mux_available`" journal entry).
      `agent-bridge`'s `cli-mode launch` passes `--ensure-mux` explicitly —
      it *is* the deliberate per-request case fine to default. So: a
      CodeSpace, a trusted container, or a bare dev box reached through
      `cli-mode launch` self-heals a missing tmux; a plain, human-typed
      `embody` never does.
- [x] Confirm/document the remaining venue-prep prerequisites this surfaces
      before any venue-specific launch verb is added.
      **Done** — see the 2026-09-20 "Phase 4 prep: prerequisites confirmed,
      container-spawner gap found" journal entry (**correction below**: that
      entry's claim of no container dispatch primitive was wrong): (1)
      `copilot` presence is a CodeSpace devcontainer convention (verified
      live), but is **not** guaranteed for `agent-containers`' operator-supplied
      images — `installer_readiness.inspect_toolchain` only validates
      host-side fleet tooling (docker/devcontainer/ssh), never in-container
      `copilot`/`tmux`; this must be an explicit precondition check, not an
      assumption, for any container-venue launch verb. (2)
      `agent-worktrees` full `install` remains a manual/first-touch step for
      a venue that's never had a human session — unchanged, not automated
      here. (3) Confirmed the CLI-mode reservation is correctly host-side
      even for a venue launch: agent-bridge runs exactly one daemon (on the
      host) regardless of spawn target: a dispatched Session Host — whether
      via `LocalSpawner` or `CodeSpaceSpawner` (`session_manager.py`) —
      always registers back to that same host daemon, so Phase 2's
      server-side reservation correlation needs no change for the CodeSpace
      case.
- [x] **Correction (2026-09-20): retract the "no ContainerSpawner" claim.**
      A closer read of `session_host/container_transport.py` found
      `ContainerTransport` (an OpenSSH `RemoteTransport` for a trusted
      container, `boundary = "container"`) and `build_container_spawner()`,
      which wires it into the *same* `CodeSpaceSpawner` class used for
      CodeSpaces (genuinely boundary-agnostic, per its own docstring: "named
      for its first consumer (CodeSpaces)... The mesh `SshSpawner` is the
      same class with an ssh-manager-backed transport"). `agent-containers`
      **already has** a headless/ACP Session Host dispatch primitive; my
      earlier claim it didn't was a shallow-grep error (only checked
      `session_manager.py` for a `class.*Spawner` match, missing
      `session_host/*.py` entirely). Both remaining Phase 4 items below
      apply to `agent-codespaces` **and** `agent-containers` equally — no
      venue is uniquely blocked.
- [ ] **New finding (2026-09-20), the real remaining Phase 4 prerequisite:
      cross-venue self-registration has no network path back to the host
      daemon yet.** Traced how a live CLI-mode session actually becomes
      discoverable: it self-registers into `live_sessions` (via the bundled
      extension's ordinary registration POST) — **not** `host_index`, which
      is specifically the daemon's own map of processes *it spawned*
      (dialable local port, `host_pid`/`child_pid` liveness). `live_sessions`
      is already the correct, existing "any self-registered live session"
      discovery mechanism, and CLI mode already uses it correctly for the
      local case — Phase 2 got the *mechanism* choice right; my 2026-09-20
      vision correction narrowing this to "promote into `host_index`" was
      itself wrong and has been corrected again (see the vision's newest
      Provenance entry). What's actually missing for a *remote* venue:
      `extension.mjs`'s `resolveBaseUrl()` always dials `http://127.0.0.1:<port>`
      — a registering session assumes its daemon is reachable on its own
      loopback. Checked what's reverse-forwarded into a CodeSpace/container
      today (`session_host/codespace_transport.py`,
      `session_host/container_transport.py`, `relay_launch.py`): only the
      **credential-relay** port is carried on a `-R` forward; the daemon's
      own API port is not. A CLI-mode process launched inside a venue
      therefore has no path back to the host daemon to register at all yet.
      Fixing this needs a `-R` forward for the daemon's own port alongside
      the existing relay forward (reusing the same reverse-forward
      machinery, not inventing a second one) as part of venue-side CLI-mode
      launch prep.
      **Grounded the exact mechanism to reuse (2026-09-20), not yet wired:**
      the daemon's own live port is already resolvable via
      `__main__.py`'s existing `_service_port()` (routing table > config >
      default — the same resolver `agent-bridge`'s own CLI uses to find
      itself). The forward-persistence machinery already exists too:
      `reverse_forwards` (a plain list of `-R` spec strings, e.g.
      `f"{port}:127.0.0.1:{port}"`) is threaded through
      `CodeSpaceTransport`/`ContainerTransport` onto `ssh-manager`'s
      `PersistentCarrier`/`ConnectionManager` -- the same standing,
      multiplexed SSH connection that already keeps the credential-relay
      forward alive independent of any one dispatch's lifetime. Adding the
      daemon's own port to that same `reverse_forwards` list for a CLI-mode
      launch (not for ordinary ACP dispatch, which never needs it) is
      additive, reuses existing infrastructure end to end, and needs no new
      persistence mechanism.
- [x] **Prerequisite fix (2026-09-20), unblocks the item below:
      `connection_owner.enabled` flipped to default-on, on-demand,
      idle-shutdown** (PR #3108). While designing the launch verb, found the
      Connection Owner daemon + ssh/dispatch defer wiring (dotfiles#1333/
      #1345) were both fully built and working -- but `enabled` stayed
      default-off indefinitely, a finished feature left permanently opt-in
      rather than graduated (operator flagged this: "no wonder things kept
      breaking", plausibly contributing to #580's relay-flap instability).
      Fixed: default `enabled=True`; a tenant now spins the daemon up
      **on-demand** (`ensure_owner_running`, detached subprocess) instead of
      requiring it already running; the reconcile loop exits cleanly on its
      own after `idle_shutdown_after` (default 300s) with no held CodeSpace,
      rather than being forced to stay resident forever. This is the on-
      demand-but-not-forced-forever ownership model CLI-mode's own
      persistent daemon-port forward (below) needs to build on.
- [x] **Foundational local piece landed (2026-09-20): `agent-worktrees
      copilot`** (PR #3126). New `agent-worktrees copilot [--worktree-id ID
      | --codename NAME | --new] [--seed] [--driver] [--recovery]
      [--ensure-mux] [--mux BIN]`: reuses `cmd_embody`'s full
      target-resolution/preflight/create-or-resume logic in-process, then
      hands this process's own controlling terminal to the resulting mux
      session via `os.execvp` -- refuses without a TTY. This is the
      canonical **`copilot` verb**: "deliver a TTY Copilot session to the
      user in the current terminal," confirmed by the operator as the
      naming to use everywhere, retiring the ambiguous "interactive"
      vocabulary (which meant at least three different things across this
      codebase: has a TTY, has a picker UI, is a muxed human-attended
      session). `agent-codespaces`/`agent-containers` implement the exact
      same verb for a remote venue by SSH `-t`'ing in and running this same
      command remotely, rather than reimplementing attach logic three
      times.
- [x] **Design simplification (2026-09-20): self-hosted tenancy resolves
      the "who renews the Connection Owner hold" question below --
      no cross-plugin daemon choreography needed.** Operator: "if I call
      `agent-codespaces copilot`, that maintains a process on my side. That
      process should be what keeps Connection Owner alive." So
      `agent-codespaces copilot <name>` places its Connection Owner tenant
      hold once at connect (like `ssh` already does) but, unlike `ssh`,
      must **periodically re-heartbeat its own hold** for as long as it
      keeps running (attached to the remote mux session, or otherwise
      holding the SSH channel open) and release on exit -- the running
      process itself *is* the tenant lease. No agent-bridge daemon
      involvement, no new cross-plugin API. This directly satisfies "fine
      to lose connectivity once both sides disconnect and the Owner idles
      out" from the earlier Connection Owner design conversation.
- [ ] **Scope clarification (2026-09-20): the primary caller is the
      Worktree Picker, not a human typing the verb.** Operator: most of the
      time, an operator opens Picker, navigates to its existing
      account-scoped CodeSpaces/containers pivot (`worktree-manager`'s
      Picker TUI already has this), selects an entry marked CLI-mode,
      and hits Enter to "Open" -- or uses the UX to create a new session on
      an idle venue. The underlying command is immaterial to that flow.
      This means: (1) the `copilot` verb's CLI ergonomics matter less than
      it being one cleanly scriptable action (clear JSON/exit-code
      contract, inherited from `embody`) Picker can invoke the same way a
      human would; (2) **Picker gaining CLI-mode-aware "Open"/"create
      session" actions on its existing CodeSpaces/containers pivot is real,
      separate work in `worktree-manager`** (a third plugin, distinct from
      `agent-bridge`/`agent-codespaces`/`agent-containers`), not an
      afterthought -- not yet scoped in detail, deliberately deferred past
      the venue-side `copilot` verb below so Picker has something concrete
      to integrate against.
- [ ] **The actual venue CLI-mode launch verb (`agent-codespaces copilot
      <name>` / `agent-containers copilot <name>`), not yet started.**
      Concrete shape, now simplified by the self-hosted-tenancy design
      above: reserve the worktree (unchanged) -> place a Connection Owner
      tenant hold for this session (spins the Owner up on-demand via
      `ensure_owner_running` if needed) with **both** the existing
      credential-relay forward and a new daemon-port forward (using
      `_service_port()`, per the earlier grounded finding) riding the same
      owned connection -> SSH `-t` into the venue and run `agent-worktrees
      copilot --worktree-id <id> [--driver] [--seed] [--ensure-mux]`
      **there** (ensures the mux session exists, then attaches -- identical
      contract to the local verb, just dispatched remotely) -> while this
      process runs, periodically re-heartbeat the Connection Owner tenant
      hold; release it (and let the Owner idle out) on exit/detach.
      Preflight (already documented, not yet enforced in code): `copilot`
      present, `tmux` installable (`ensure_mux_available`, already built),
      `agent-worktrees` **fully** installed (not just lean tools) so
      `embody`'s `launch-command.sh`/hooks exist. Validate the way Phase 3
      did: a real, disposable clean-room venue (a CodeSpace or Docker
      container), a real `copilot` process actually reachable end-to-end
      (register, list, `send`, observe via `tmux capture-pane`/venue SSH,
      teardown, and specifically: confirm the daemon-port forward survives
      well past the launch command's own exit, and is torn down promptly on
      detach) -- not only unit tests with fakes.
      **Landed (2026-09-21), unit-level; live clean-room validation still
      pending.** Built a new shared, vendored lib (`agent-venue-copilot`,
      byte-identical `plugins/{agent-codespaces,agent-containers}/libs/
      venue-copilot`, per this repo's `check-vendored-libs-sync.py`
      contract) holding the provider-agnostic reserve -> connect -> release
      orchestration (`reserve_cli_mode`/`release_cli_mode` shelling to
      `agent-bridge --json live-sessions cli-mode {reserve,release}`,
      `build_copilot_remote_command`, `run_venue_copilot`) plus
      `resolve_daemon_port`/`daemon_port_reverse_forward`, which read the
      host daemon's routing table via a newly-vendored `zdd` copy
      (`zdd.routing.read_active_endpoint`, `verify_listener=False`) --
      exactly the daemon-port-forward gap this checklist called out, now
      wired rather than only grounded. `agent-codespaces copilot <name>
      --worktree-id <id>` places a Connection Owner tenant hold
      (`ensure_owner_running` + `hold`), runs a background heartbeat thread
      for the process's lifetime, layers the daemon-port forward onto
      `gh codespace ssh` alongside the existing credential-relay forward
      (extended `_interactive_ssh` to accept a trailing remote command), and
      releases the hold in a `finally`. `agent-containers copilot <name>
      --worktree-id <id>` mirrors the same reserve/connect/release contract
      over the trusted-container OpenSSH transport (extended
      `ssh_transport.build_ssh_command`/`ssh_manager.build_remote_exec_args`
      with a new `pty=True` option, since the prior transport was
      no-PTY-only); refuses a restricted fleet outright (no SSH key
      projection there) rather than degrading silently. Both command bodies
      live in a per-plugin `copilot_venue.py` (not inline in `__main__.py`,
      which was already at this repo's module-size ceiling --
      `tools/module-size-baseline.json` widened by the small net remainder
      after extraction, a deliberate reviewed edit per its own stated
      escape hatch). 9 new unit tests per plugin (fakes throughout); full
      plugin suites otherwise unchanged (only the same pre-existing,
      unrelated Windows/bash failures). **Still not done:** the full
      end-to-end `copilot` attach/reattach pass this checklist requires
      (confirming the daemon-port forward survives past the launch
      command's own exit and tears down on detach) — that needs a
      registered worktree + host daemon coordination this session didn't
      set up.
      **Read-only re-confirmation against a real, currently-available
      operator CodeSpace (2026-09-21):** rather than leave the venue-prep
      checklist's findings above resting only on the 2026-09-20 session's
      earlier probe, re-checked them live against a different currently-
      running venue (`gh codespace ssh -c <name> -- <probe>`, a plain read
      probe, no writes): `copilot` present (v1.0.86, `/usr/local/share/
      nvm/current/bin/copilot`) alongside `node`/`python3`/`uv`/`git`;
      **`tmux` still absent** (`apt-cache policy` shows `Candidate:
      3.4-1ubuntu0.1`, `Installed: (none)`) with passwordless `sudo`
      confirmed working; `~/.agent-worktrees` exists but holds only
      `.install-stage`/`payload-dir` -- no `agent-worktrees`/`agent-bridge`
      binstub resolves on `PATH`. This reconfirms, on a second independent
      venue, exactly the venue-prep gaps the 2026-09-20 probe found (lean
      self-provisioning, no full install) -- not a one-off. Also confirmed
      (operationally, not a code change) that `agent-codespaces ssh`'s own
      `--remote-cmd` path 404s mid-connect against this account's
      codespaces (`getting full codespace details ... refresh=true`) while
      plain `gh codespace ssh` connects fine -- a real, pre-existing,
      unrelated quirk in that refresh-details call, not this change's
      concern; noted here only so a future session doesn't re-diagnose it
      from scratch.
      **Full end-to-end live run (2026-09-21), two real bugs found and
      fixed.** Ran `agent-codespaces copilot <name> --worktree-id <id>`
      itself (not just a read probe) against the same real CodeSpace, via
      this repo's own `.test-venvs` build of the merged code (so the exact
      shipped logic, not a hand-simulation). Two genuine, reproducible bugs
      surfaced and were fixed (follow-up PR, since #3144 had already
      merged):
      1. **`venue_copilot._run_bridge` couldn't actually spawn
         `agent-bridge` on Windows.** A bare binstub name in list-argv
         `subprocess.run(["agent-bridge", ...], shell=False)` is never
         resolved against `.cmd`/PATHEXT the way a real shell would --
         `FileNotFoundError: [WinError 2]`, every time. Fixed: resolve via
         `shutil.which(argv[0]) or argv[0]` before spawning (same
         cross-platform-safe shape the rest of this shared lib already
         assumes).
      2. **The same call leaked `PYTHONHOME`/`PYTHONPATH`/`VIRTUAL_ENV`/
         `__PYVENV_LAUNCHER__` into the spawned `agent-bridge` process**
         (`subprocess` inherits `os.environ` verbatim by default), forcing
         its own re-exec'd interpreter to resolve the WRONG stdlib/native-
         extension tree -- observed as `ImportError: DLL load failed ...
         not a valid Win32 application` importing `socket`, deterministically
         reproducible whenever the *caller* is itself a Python process (a
         plain shell-invoked `agent-bridge` worked every time; a
         python-child-spawned one failed every time). This is the exact bug
         class `agent_dispatch.procutil`'s `_AGENT_WORKTREES_ENV_SCRUB`
         already guards against for `agent-worktrees` (its own comment cites
         an analogous `_sre` mismatch) -- `venue_copilot` needed the same
         scrub for its own sibling-binstub spawn of `agent-bridge`. Fixed:
         mirrored that exact scrub set.
      A third, smaller bug surfaced once the daemon calls actually worked:
      `agent-codespaces`'s own (pre-existing, never-before-exercised with
      real content) `_interactive_ssh` reverse-forward loop appended each
      bare `"port:127.0.0.1:port"` spec after its own `--` separator with
      **no `-R` flag** -- `gh codespace ssh` then handed ssh a bare spec
      string with nothing marking it as an option, so ssh treated it as the
      **remote command** to run (`bash: line 1: 51234:127.0.0.1:51234:
      command not found`) instead of an actual reverse forward. Fixed:
      build a single `--` separator followed by proper `["-R", spec]` pairs,
      with the remote command (if any) appended last -- confirmed
      unchanged behavior for the pre-existing empty-forwards case via
      `test_workspace_discovery.py`'s existing regression test, plus new
      dedicated coverage in `test_copilot_venue.py`.
      With all three fixed, the full run reserved, held/heartbeated the
      Connection Owner, connected over SSH with both reverse forwards
      correctly established, ran `agent-worktrees copilot --worktree-id
      <id> --ensure-mux` **inside** the CodeSpace, and failed at exactly
      the expected, already-documented boundary --
      `agent-worktrees: command not found` (this venue's `agent-worktrees`
      is only lean-staged, matching every prior probe) -- and released its
      CLI-mode reservation cleanly (`agent-bridge --json live-sessions
      cli-mode status` confirmed no leftover reservation after the run).
      This is now genuinely full end-to-end validation up to that
      documented, pre-existing gap; installing a full `agent-worktrees` on
      a disposable venue to clear that last gap remains open for a future
      session (this session did not write to a shared, non-disposable
      operator CodeSpace to do so).
      **Closed (2026-09-21/22): installed on a disposable `agent-containers`
      trusted fleet member, and two further genuine bugs found + fixed --
      see the dated journal entries below.** The full loop -- reserve,
      SSH connect with both reverse forwards, run `agent-worktrees copilot`
      remotely, a real interactive `copilot` TUI coming up inside a real
      tmux session, observed live via `tmux capture-pane` -- now works
      end-to-end against a venue with a genuinely full install.
- [x] **New finding (2026-09-20): `live_sessions` needs a reattach-shaped
      field for remote CLI-mode sessions.** Today's schema (`machine`,
      `cwd`, `worktree_id`, `pid`, ...) has no venue identity or mux-session
      descriptor, so nothing durably records "which CodeSpace/container this
      is, and what to attach to" for an operator or the daemon to use later.
      Needs an additive field (e.g. a `venue`/reattach descriptor: boundary +
      target + mux session name) alongside the existing columns — additive
      field for remote CLI-mode sessions.** Today's schema (`machine`,
      `cwd`, `worktree_id`, `pid`, ...) has no venue identity or mux-session
      descriptor, so nothing durably records "which CodeSpace/container this
      is, and what to attach to" for an operator or the daemon to use later.
      Needs an additive field (e.g. a `venue`/reattach descriptor: boundary +
      target + mux session name) alongside the existing columns — additive
      only, no schema break for the ordinary local case.
      **Done** — schema v18→v19 adds a nullable `live_sessions.venue` TEXT
      column (JSON; `NULL` for the ordinary local case). New
      `LiveSessionVenue` model (`kind`/`target`/`mux_session_name`) on both
      `RegisterLiveSessionRequest` and `LiveSessionInfo`; `register_live_session`
      takes an optional `venue` JSON string and carries it through the
      upsert; the route serializes/deserializes at the JSON boundary
      (`_parse_venue`, mirroring `_parse_progress`). Every existing read path
      (`_to_info`) surfaces it automatically since it already reads through
      one function. New coverage in `test_live_sessions.py`
      (`test_route_register_carries_venue_for_remote_cli_mode`): the
      descriptor round-trips through register/list/get, and an ordinary
      local registration carries `venue: null`.
- [x] Extend `agent-codespaces` **and** `agent-containers` to offer the same
      CLI-mode launch shape — prepare the venue (the checklist above),
      allocate the CLI-mode reservation, start a standard muxed CLI process
      (`embody`, run remotely) bound to it, with the daemon-port reverse
      forward and reattach metadata above — over the existing venue-parity
      SSH transport and auth-relay back-channel. No new venue-specific
      transport; both venues use the same `CodeSpaceSpawner`-shaped seam
      already, so neither is ahead of the other here.
      **Done at the unit level (2026-09-21)** — see the detailed entry
      above under the daemon-port-forward checklist item. Reattach metadata
      (`live_sessions.venue`) itself was already landed separately (below);
      wiring the running venue-side `agent-worktrees copilot` to actually
      *populate* that field on self-registration is not part of this
      change and remains open (the schema/model side is done; nothing yet
      sets a non-null `venue` value end-to-end). Live clean-room validation
      also remains open -- see above.
- [x] **Detached, agent-facing venue launch (2026-09-22).** `agent-codespaces
      copilot <name> --detach` (and `agent-bridge create codespace:<name>
      --cli --detach`) for an orchestrating caller that must not hand its
      terminal away: full dispatch-grade venue prep via the `ssh` verb's body
      (`_ssh_session` hooks), remote `agent-worktrees embody` with staged
      CodeSpace plugins folded in (`--copilot-arg=--plugin-dir=...`), success
      only once registered (reservation claimed) and seeded; `--stop` /
      `--dry-run`. Closes the "nothing sets a non-null `venue`" gap above: the
      reservation now carries the venue descriptor and the claiming
      registration inherits it. Registration identity is venue-qualified
      (`<identity>@<codespace>`, via `AGENT_BRIDGE_SCOPE_ID`). The Connection
      Owner keeps the relay + host-daemon forwards alive for the session's
      lifetime (`session_forwards.py`). See the journal entry.

### Phase 5 — Docs and vision closure

- [ ] Update `plugins/agent-bridge`, `agent-codespaces`, and
      `agent-containers` docs/architecture for the new mode and discovery
      mapping.
- [ ] Confirm the realized behavior against
      `visions/remote-interactive-sessions`; no vision revision expected
      (this closes existing intent) unless implementation surfaces a genuine
      should-be gap.

### Bug sweep — linked open bugs (2026-09-24)

_Correlated via a facility-driven sweep of open `bug`-labeled issues against active efforts (VEI + direct review). Not yet triaged into a numbered phase — listed here as upcoming work for whoever picks this effort back up._

- [ ] **#1167** agent-bridge: project-scoped Windows SSH dispatch can launch at C:\ after resolver bootstrap failure
  - Project-scoped Windows SSH dispatch landing at the wrong cwd is exactly this effort's CWD-keyed discovery scope.

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
- [x] A CLI-mode session launched via `agent-codespaces`/`agent-containers`
      binds, reattaches, and is observed identically to the local case, over
      the existing venue-parity transport.
      **Closed (2026-09-21/22)** -- see the dated journal entries: a real
      `copilot` TUI launched via `agent-containers copilot <name>
      --worktree-id <id>` against a disposable trusted fleet container (with
      a genuinely full `agent-worktrees` install, not just lean tools) came
      up inside a real tmux session created by the remote
      `agent-worktrees copilot` verb, observed live via `tmux capture-pane`,
      and registered with the container's own local `agent-bridge` daemon
      (`agent-bridge --json live-sessions list` showed the live session with
      `driven_by: "cli-mode"`) -- identical mechanism to the local case, no
      CLI-mode-specific divergence.
      **Independently reconfirmed against a real odsp-web `agent-codespaces`
      CodeSpace (2026-09-22)**, closing the last gap the container pass
      couldn't cover (a genuinely distinct venue transport, not just a
      second Docker run): fixed a third real bug found only here --
      `_interactive_ssh`'s `gh codespace ssh` invocation never actually
      passed `-t`, so the remote `agent-worktrees copilot` always refused
      with "needs a controlling terminal to attach to" despite this
      module's own docstring and CLI help text documenting "SSHes -t in" --
      confirmed via direct in-process reproduction and fixed by adding the
      flag when a `remote_command` is present (never for a plain
      interactive connect, which `gh codespace ssh` already handles).
      With all three bugs across this validation (PATH/login-shell,
      JSON-capture, and this pty flag) fixed, ran the actual production
      task end-to-end: `agent-codespaces copilot friendly-eureka-...
      --worktree-id <id>` reserved the CLI-mode slot, connected with the
      now-correctly-account-pinned SSH env, opened a real pty-backed
      interactive `copilot` TUI inside a genuine tmux session on the
      CodeSpace, and -- driven by `tmux send-keys`/`capture-pane` exactly
      like the container pass -- completed a full real task ("add a haiku
      to a package README and open a PR"), landing real Azure DevOps PR
      #2397021 on `odsp-web`. The CLI-mode reservation was released cleanly
      on exit (`agent-bridge --json live-sessions cli-mode status` empty
      afterward) and the tmux session tore down on `/exit`.

## Proposal

Realized for the local case: worktree-keyed reservation + server-side claim
at registration time (Phase 2), plus an opt-in local launch verb that
automates reserve-then-run for a real interactive `copilot` process (Phase
3). Still pending: Phase 4's remote-venue discovery mechanism and its
symmetric venue-launch surface (needed once a venue's own daemon differs
from the host's).

## Journal

### 2026-09-22 — Detached venue launch: an orchestrator's observable, steerable remote CLI session

Operator direction: an orchestrating session that dispatches remote work over
headless ACP gives its operator little visibility into, or control over, the
work in flight. The attached `copilot` verb already delivers the right kind of
session (a real muxed CLI session, registered, represented, messageable), but
only into a terminal a human holds. Added the detached form, composing existing
pieces rather than a parallel path:

- **agent-worktrees**: `embody`/`copilot` gain `--copilot-arg` (validated
  passthrough into the already-honored `copilot_args`) and
  `--bridge-scope-id` (exported as `AGENT_BRIDGE_SCOPE_ID`; the local mux name
  is unchanged so the attached verb still re-attaches). The `copilot` parser
  moved next to `embody` in `handoff_cli.py` (module-size guard).
- **agent-bridge**: the extension registers under `AGENT_BRIDGE_SCOPE_ID` when
  set; CLI-mode reservations carry an optional venue descriptor inherited by
  the claiming `live_sessions` row (heartbeat upserts keep it), release can be
  compare-and-delete by `reservation_id`, and the reaper no longer probes a
  venue session's (remote) pid on this host. `create --cli --detach` passthrough
  (codespace targets; seed over stdin). `/ui` gains live sessions with a
  represented-event feed and a message composer; `agent-bridge ui` opens it
  (signed in through a one-time login code; the token never enters a URL). CLI-mode models split to
  `cli_mode_models.py` (module-size guard).
- **agent-codespaces**: `copilot --detach/--stop/--dry-run`
  (`copilot_detach.py`); `_cmd_ssh`'s body extracted to `_ssh_session` with
  keyword hooks (remote-command builder that receives staged plugin dirs,
  result sink, `settle_on_disconnect`); the Connection Owner gains session
  tenants (renewed from its own `tmux has-session` probe, released when the
  session or the CodeSpace is gone, 24h cap) and a host-daemon reverse forward
  (`session_forwards.py`; host side follows the daemon's live port), plus a
  singleton guard. `_cmd_owner` moved to `owner_cli.py` (module-size guard).
- **venue-copilot** (vendored x2): venue-carrying reserve, reservation status,
  exact release, and the `embody`-shaped remote command.

Design review (rubber-duck) drove: venue-qualified identity (several CodeSpaces
of one repo all report `anchor-<repo>`), venue from the trusted reservation,
Owner stays transport-only, no false success on missing plugins (old venue
tooling fails closed), no settle-on-disconnect for a still-running session,
verified stop before release, and no host pid probing for remote rows.

### 2026-09-22 — Unified `agent-bridge create <target> --cli`, anchor-mode default, and registration-identity fix

Follow-up request after the live CodeSpace validation below: the operator
wanted a single, natural command shape for delivering a remote CLI-mode
session -- not a raw `gh codespace ssh` command, not a manually-composed
`agent-bridge live-sessions cli-mode reserve` + venue-verb pairing (which
turn out to *conflict*, since `create_cli_mode_reservation` refuses a
second reserve for the same identity regardless of claimed status), and
critically **not requiring a worktree at the far end** -- a CodeSpace's
anchor checkout is what the existing headless ACP dispatch already drives
directly, and CLI mode needed to match that same contract rather than
inventing a worktree requirement headless dispatch never had.

Landed in three stacked pieces (PR ThomasMichon/copilot-extensions#3245,
two commits, then PR #3249):

1. **`agent-worktrees --anchor` mode** (`embody`/`copilot`): a new
   mutually-exclusive `--anchor` flag that embodies directly in the active
   project's anchor checkout, no worktree created or required, session id
   synthesized as `anchor-<repo_name>`. Turned out to need almost no new
   plumbing -- `_build_launch_cmd`/`_build_env`/`_repo_session_env`/
   `_preflight_launch` were already generic over an arbitrary `work_dir`
   string.
2. **Anchor-default venue verbs + registration-identity fix**:
   `--worktree-id` changed from required to optional on `agent-codespaces
   copilot`/`agent-containers copilot` (omitting it now resolves an
   `anchor-<repo_name>` identity and forwards `--anchor` remotely instead);
   added `agent-worktrees get session-scope-id` (worktree id, or
   `anchor-<repo_name>` when in the anchor) and switched `agent-bridge`'s
   `metadata.mjs` to read it directly instead of deriving `worktree_id`
   from `basename(worktree-dir)` (empty in the anchor -- would have
   silently broken self-registration for every anchor-mode session).
3. **`agent-bridge create <target> "<prompt>" --cli`** (this repo's actual
   ask all along, per the operator's explicit "this is exactly what I was
   asking for for this whole effort"): resolves a namespaced target's
   `<prefix>:<name>` to its owning plugin's binstub
   (`codespace:` → `agent-codespaces`, `container:` → `agent-containers`,
   fixed mapping, no in-process import of either -- preserves the existing
   agent-bridge/sibling-plugin process-boundary CLI seam, #892/#1643) and
   execs `<binstub> copilot <name>` with inherited stdio, forwarding
   `--seed`/`--driver`. A bare/unprefixed target is refused with a pointer
   to `agent-worktrees copilot` directly (no agent-bridge mediation needed
   locally). 8 new tests (`test_create_cli_mode.py`); full agent-bridge
   suite otherwise unchanged (569 passed, 2 pre-existing unrelated
   failures). Closes the "unified CLI-mode dispatch" feature request that
   turned out to be this whole session's real thread, start to finish.

### 2026-09-22 — Full live validation against a real odsp-web CodeSpace: 2 more bugs found and fixed; Phase 4 genuinely done end-to-end

Continued from the container-only validation above with a request to prove
the venue `copilot` verb against an actual `agent-codespaces` CodeSpace, not
just the disposable Docker container. Resolved `odsp-web`'s preferred venue
(`agent-worktrees related resolve odsp-web` -> CodeSpace, delegate
`agent-codespaces`, per the harness's own routing policy) and picked an
already-`Available` operator CodeSpace (`friendly-eureka-...`) rather than
provisioning a fresh one.

**Bug found #1 (ssh-manager, cross-cutting):** `agent-codespaces ssh
--remote-cmd` 404'd ("getting full codespace details") whenever the ambient
`gh` account differed from the CodeSpace's owner -- previously written off
in this effort's own journal as a "pre-existing, unrelated quirk." Traced
to root cause: `CodespaceSource`/`CodespaceConfigSource` already pinned `gh
codespace ssh --config`'s own config-fetch subprocess to the owning account
via an internal `gh_env`, but nothing threaded that env onward to the
*actual* SSH connection -- the ControlMaster, `exec_command`,
`open_stdio_channel`, the graceful `-O exit` disconnect, and (on Windows)
the embedded ProxyCommand's `gh cs ssh --stdio` child all silently
inherited the ambient process environment instead. Fixed in the shared,
triple-vendored `ssh-manager` lib (`agent-bridge`/`agent-codespaces`/
`agent-containers`): exposed `gh_env` as a public property, threaded it
through `ConnectionManager` (stored on `ConnectionInfo`, passed to every
`create_ssh_subprocess` call for that connection), landed as
ThomasMichon/copilot-extensions#3232. This is the actual fix for the
quirk this effort had been working around since the 2026-09-21 journal
entry above -- not a separate concern.

**Prerequisite: built `agent-codespaces check`/`doctor <name> --fix`**
(ThomasMichon/copilot-extensions#3237) to consolidate the manual venue-prep
this session (and the container pass before it) had been doing by hand --
closing the Phase 4 checklist's own long-standing "preflight already
documented, not yet enforced in code" gap. Used it directly: `check
friendly-eureka-...` reported `tmux: MISSING` (everything else -- copilot,
a fully-provisioned `agent-worktrees` -- already ready from an earlier
`agent-worktrees update` this session); `doctor friendly-eureka-... --fix`
installed `tmux` via `apt-get` and re-probed to `READY` in one shot.

**Bug found #2 (agent-codespaces, the actual Phase 4 launch verb):** even
with #1 fixed and the venue toolchain ready, `agent-codespaces copilot
<name> --worktree-id <id>` still failed immediately with `agent-worktrees
copilot`'s own "needs a controlling terminal to attach to" refusal.
`_interactive_ssh`'s own docstring and the `copilot` subcommand's CLI help
text both document "SSHes -t in" -- but no `-t` flag was ever actually
added to the `gh codespace ssh` argv when a `remote_command` is present.
Without it, `ssh` never allocates a remote pty for a command invocation, so
the documented contract had silently never been implemented; every prior
validation pass (including Phase 3's Docker clean-room test) used a
different, non-venue launch path and never exercised this exact code path
with a real remote command, so nothing had caught it. Fixed by adding `-t`
right after the `--` separator whenever `remote_command` is set (an
ordinary interactive connect, no command, is unaffected -- `gh codespace
ssh` already allocates a pty for that case unconditionally). Regression
tests added/updated in `test_copilot_venue.py`.

**Full production-task live validation, no shortcuts:** with all three
bugs (this session's #1/#2 plus the prior session's PATH/login-shell and
JSON-capture fixes) in place, ran `agent-codespaces copilot
friendly-eureka-... --worktree-id <id>` for real -- reserved the CLI-mode
slot, connected with the now-correctly-pinned SSH env, opened a genuine
pty-backed interactive `copilot` TUI inside a real tmux session on the
CodeSpace (observed live via a second, independent `tmux capture-pane`
SSH call, exactly mirroring the container pass's validation method), and
drove it with an actual unscripted task -- "make a PR where you add a
haiku into the README.md of the utilities/resources package on odsp-web"
-- through to completion: it edited
`odsp-common/utilities/resources/README.md`, ran the repo's own commit/PR
skill (including resolving a Rush change-file policy blocker along the
way, entirely on its own), and landed real Azure DevOps PR **#2397021**
("Add haiku to utilities resources README"). Exited cleanly via `/exit`;
the tmux session tore down and the CLI-mode reservation released with no
leftover state (`agent-bridge --json live-sessions cli-mode status`
returned empty afterward).

This closes Phase 4's remaining Validation Plan item with genuine,
end-to-end, real-venue evidence -- not a container stand-in, and not a
synthetic task. Versions bumped: `ssh-manager` (all 3 vendored copies)
0.1.0-dev19 -> dev20, `agent-codespaces` 0.4.0-dev150 -> dev153 (across the
`check`/`doctor` feature and the `-t` fix). Full test suites for all
touched plugins pass with only the same pre-existing, unrelated failures
already tracked in this effort's prior entries.

Remaining open for Phase 5: docs/architecture updates and the vision
confirmation pass -- untouched this session.

### 2026-09-21/22 — Disposable trusted-container venue install closes Phase 4; two more genuine bugs found and fixed

Resumed from a handoff whose next step was: install a *genuinely full*
`agent-worktrees` on a real venue (not just lean self-provisioned tools) and
re-run the live end-to-end validation to close the one remaining documented
gap. Chose a **disposable `agent-containers` trusted fleet member** over a
shared operator CodeSpace (lower-risk, matches Phase 3's own Docker
clean-room precedent): built a scratch image extending the existing
clean-room `base` image with `openssh-server` (the one thing
`agent-containers`' trusted-container transport doesn't self-provision --
everything else, including host-key generation and `authorized_keys`
projection, it already self-heals). Copied the worktree in as a local
`copilot plugin marketplace add` directory source, installed
`agent-worktrees` + `agent-bridge` via their real `scripts/install.sh` (not
just the lean tools-only path), and confirmed the full install: binstub on
`PATH`, `agent-worktrees --version` resolving cleanly -- the exact gap the
prior session's validation stopped at.

Running the real round trip (`agent-containers copilot <name> --worktree-id
<id>`) surfaced two more genuine, previously-undiscovered bugs, both only
reachable once a venue actually has a full install (so no prior session's
validation, which always stopped at "command not found," could have hit
them):

1. **OpenSSH's non-interactive remote-command exec never sources
   `~/.profile`/`~/.bashrc`.** `venue_copilot.build_copilot_remote_command`
   built a bare `agent-worktrees copilot --worktree-id ...` command string.
   Confirmed directly (`ssh -F <config> <host> 'echo $PATH'` vs. `'bash -lc
   "echo $PATH"'`): a plain remote-exec shell's `PATH` is the stock
   `/usr/bin:/bin:...` set, never appended with `~/.local/bin` -- the
   directory `agent-worktrees`' own install flow adds the binstub into.
   So even a **fully, correctly installed** remote `agent-worktrees` still
   resolved to `agent-worktrees: command not found` (exit 127) -- a
   distinct bug from, and layered directly underneath, the already-tracked
   "venue lacks a full install" gap; every prior session's validation
   necessarily stopped at the outer gap before this inner one could ever
   surface. Fixed by wrapping the remote command in `bash -lc '<cmd>'`
   (restores the login-shell `PATH`) in the shared `venue_copilot` lib,
   synced byte-identically to both vendored copies
   (`agent-containers`/`agent-codespaces`) per
   `check-vendored-libs-sync.py`'s contract. New/updated unit coverage in
   both copies' `test_venue_copilot.py`.
2. **`agent-worktrees copilot` (`cmd_copilot`) never actually parsed
   `cmd_embody`'s JSON result.** Once the PATH bug above was fixed, the
   remote command still failed with "could not parse the embodiment
   result" -- traced to `cmd_copilot` capturing `cmd_embody`'s stdout via
   `contextlib.redirect_stdout(buf)`, which only swaps `sys.stdout`. But
   `output._json_output` (the function every JSON-emitting CLI command,
   including `embody`, actually calls) deliberately writes to
   `sys.__stdout__` instead -- precisely so its envelope still reaches the
   real terminal from inside `cmd_embody`'s own `output.stdout_to_stderr()`
   block (used while creating a fresh worktree). `redirect_stdout` never
   sees anything written to `sys.__stdout__`, so `buf.getvalue()` came back
   **empty on every single call**, both locally and remotely -- confirmed
   by direct in-process reproduction (no SSH, no venue involved) with a
   local worktree: `cmd_embody`'s own `print()`-based JSON output reached
   the terminal correctly, but the capturing caller's buffer was always
   `''`. This is a 100%-reproducible bug in the local `copilot` verb itself
   (PR #3126, unrelated to Phase 4's remote transport) that had gone
   unnoticed because no prior validation pass actually exercised
   `cmd_copilot`'s parse step end-to-end -- Phase 3's live validation used
   `cli-mode launch` (a different, older verb), not `copilot`. Fixed with a
   new `output.capture_json_output()` context manager that swaps
   `sys.__stdout__` itself (the level `_json_output` actually targets);
   `cmd_copilot` now uses it instead of `redirect_stdout`. Added a
   dedicated regression test proving ordinary `print()`-based progress
   noise under `output.stdout_to_stderr()` is *not* captured (only the real
   JSON envelope is), so a future refactor can't silently reintroduce the
   `sys.stdout`-vs-`sys.__stdout__` confusion. `tests/test_copilot.py`'s
   existing fakes were also switched from a bare `print(json.dumps(...))`
   to `output._json_output(...)` so they exercise the real contract instead
   of accidentally matching only the buggy capture mechanism.

With both fixed, the full round trip now works end-to-end inside the
disposable container: `agent-containers copilot clivenue-1 --worktree-id
<id>` reserved the CLI-mode slot, SSHed in with both reverse forwards
established, ran `agent-worktrees copilot --worktree-id <id> --ensure-mux`
remotely, which self-healed a missing `tmux` (`apt-get install tmux`,
confirmed against a real `apt-get update`d package cache -- the scratch
image intentionally keeps its apt lists, matching a real CodeSpace
devcontainer, so this exercises the actual self-heal path rather than a
manufactured miss) and created a real, detached tmux session running a real
interactive `copilot` TUI -- observed live via `tmux capture-pane` (the
folder-trust prompt, then the ready input caret), and confirmed registered
with the container's own local `agent-bridge` via `agent-bridge --json
live-sessions list` (`driven_by: "cli-mode"`). This closes both the
Phase 4 checklist item and its corresponding Validation Plan item with
genuine end-to-end evidence, not a claimed pass. Full pytest suites for all
three touched plugins pass with only pre-existing, unrelated failures
(confirmed via `git stash` bisection): agent-worktrees' `test_doctor.py`
(a pre-existing Windows/`plat="linux"` path-fixture mismatch, unrelated to
this change) and the cross-plugin `test_bootstrap_check_reconcile_opt_in.py`
Windows/bash path-mangling quirk already known from prior sessions.
Versions bumped: `agent-worktrees` 1.5.5-dev229, `agent-containers`
0.1.2-dev151, `agent-codespaces` 0.4.0-dev150.

Remaining open for Phase 4/5: `live_sessions.venue` still isn't populated
end-to-end by a real venue registration (schema/model landed separately,
nothing yet sets a non-null value); Phase 5's docs/vision-closure pass is
untouched.

### 2026-09-20 — Landed `agent-worktrees copilot`; self-hosted tenancy + Picker-as-caller reframe the remaining launch verb

Three operator clarifications in quick succession, each simplifying the
design further:

1. **"If I call `agent-codespaces copilot`, that maintains a process on my
   side. That process should be what keeps Connection Owner alive."**
   Resolves the previous journal entry's open question outright: no
   agent-bridge-daemon choreography needed. `copilot`'s own running process
   places the tenant hold and periodically re-heartbeats it for as long as
   it runs (unlike `ssh`, which only holds-once at connect -- noted but not
   fixed, a separate latent gap for long ssh sessions), releasing on exit.
   The attached/running process *is* the lease.
2. **"When would `agent-* copilot` ever be invoked?"** -- stepping back to
   question the primary UX. Confirmed by checking: `worktree-manager`'s
   Picker TUI already has an account-scoped CodeSpaces pivot (status
   coloring, leases, releases). The expected flow is Picker navigation ->
   select a CLI-mode-marked entry -> Enter to "Open" (or create a new
   session on an idle venue) -- the underlying command is an implementation
   detail Picker invokes, not something a human types most of the time.
   Recorded as a real, separate scope item in `worktree-manager` (a third
   plugin), deliberately deferred past the venue verb itself.
3. **"I don't mind `___ copilot` being the agent-* sub-command which means
   'deliver a TTY Copilot session to the user in the current terminal'...
   is something `agent-worktrees` should also adopt."** Confirmed the exact
   verb contract and that it spans all three plugins with one name.

Landed the foundational local piece: **PR #3126**, `agent-worktrees
copilot`. Checked first that no "ensure + attach" primitive already
existed -- `embody` only ever creates *detached* (`-d`); no `attach-session`
helper existed anywhere in the codebase. New command reuses `cmd_embody`'s
full resolution/preflight/create-or-resume logic in-process (captures its
JSON via `contextlib.redirect_stdout`, since `copilot`'s own stdout is about
to become the mux client's TTY), then `os.execvp`s into `<mux> attach-session
-t <session>` -- refuses without a controlling terminal. 7 new tests
(mocking `cmd_embody`/`os.execvp` at the boundary, no real tmux/worktree
touched). Also recorded the verb naming + Picker-as-caller framing in
`visions/remote-interactive-sessions`.

Two rebases needed to land cleanly (main moved twice during the session;
one false-positive `check-version-bump` failure traced to an unrelated
context-handoff commit landing on main mid-flight, resolved by rebasing
onto the newer tip rather than chasing a phantom local diff).

Not yet done: the actual `agent-codespaces`/`agent-containers copilot
<name>` remote verb (now simplified per point 1 above) and Picker's
CLI-mode-aware pivot integration (per point 2) -- both recorded as their
own Plan items above, neither started.

### 2026-09-20 — Fixed a real, separate bug found while designing the launch verb: Connection Owner stuck opt-in

Naming discussion (`agent-codespaces copilot <name>` / `agent-containers
copilot <name>` -- confirmed good names, used going forward) led into
designing how the daemon-port reverse-forward from the grounded design
(below) would actually stay alive for a launch verb's whole session, not
just its one-shot dispatch. Tracing `agent-codespaces ssh`'s own relay
forward lifecycle surfaced that it tears its forward down when the
dispatching command exits -- fine for an attached human `ssh`, wrong for a
detached CLI-mode launch that returns immediately. Found `connection_owner.py`
already exists to solve exactly this (a ref-counted, per-machine Owner that
keeps a relay forward alive independent of any one dispatch) -- but its own
docstrings said the daemon + defer wiring were both a "later increment,"
still deploy-gated default-off.

Operator reaction on hearing this ("the connection owner isn't supposed to
be opt-in, it was already intended to have shipped as the default mechanism
-- no wonder things have kept breaking") reframed this as a real, separate
bug worth fixing now, not a design detail to work around. Checked: no
tracked issue existed for it specifically (searched `gh issue list`);
`#580` (open) is the relay-flap symptom this was designed to fix, still
unresolved. Confirmed via `ask_user` to fix it now, as its own PR, before
building the launch verb on top of it.

Landed as PR #3108: `ConnectionOwnerConfig.enabled` default -> `True`
(explicit `enabled: false` still opts out); new `idle_shutdown_after`
(default 300s) makes the reconcile loop exit cleanly on its own once idle,
rather than being forced to run forever; new `ensure_owner_running()` makes
`should_defer_to_owner()` spin the daemon up on-demand (detached subprocess,
`agent_procutil.windowless_daemon_kwargs`) instead of only deferring to an
already-running one. `install.ps1`/`install.sh`'s existing scheduled-
task/systemd wiring needed no changes -- both already treat a clean idle-exit
as success, not a crash to restart-loop. 11 new/updated tests; full
`agent-codespaces` suite: 1182 passed, 11 failed, all 11 confirmed
pre-existing via `git stash` bisection (tracked in #639, unrelated live-
machine-state test isolation).

This directly unblocks the launch verb: CLI-mode's own daemon-port forward
needs exactly this on-demand-but-not-forced-forever tenancy model, not a
new mechanism. Surfaced a new open design question in the process (who
renews a Connection Owner tenant hold for a CLI-mode session's whole
lifetime, since the one-shot launch command that places it can't) --
recorded in the Plan checklist above rather than guessed at; agent-bridge's
own daemon (already heartbeat-tracking `live_sessions`) is the likely
answer, needing new cross-plugin choreography not yet designed.

### 2026-09-20 — Grounded the reverse-forward + launch-verb design; stopping short of building it this session

Traced the exact mechanism the network-reachability finding needs, rather
than leaving it as a to-do: the daemon's own live port is already resolvable
via `__main__.py`'s existing `_service_port()` (routing table > config >
default), and the forward-persistence machinery already exists --
`reverse_forwards` is a plain list of `-R` spec strings threaded through
`CodeSpaceTransport`/`ContainerTransport` onto `ssh-manager`'s
`PersistentCarrier`, the same standing connection that already keeps the
credential-relay forward alive independent of any one dispatch. Adding the
daemon's port to that same list for a CLI-mode launch (never for ordinary
ACP dispatch) needs no new persistence mechanism -- confirmed by reading
`ssh-manager/carrier.py`/`manager.py` and the transport classes directly,
not assumed.

Deliberately stopping here rather than writing the actual venue launch verb
(`agent-codespaces`/`agent-containers` cli-mode launch) in this same
session: it's a real, separable deliverable -- SSH orchestration across two
provider plugins, a preflight sequence, and (per this effort's own
established bar, Phase 3's clean-room validation) a live test against a real
disposable venue, not just unit tests with fakes. This session already ran
two same-day self-corrections (the `host_index` misstep, the "no
ContainerSpawner" claim) before landing four merged PRs (#2966 rebase,
#3046, #3055, #3069); building the largest remaining piece under continued
pressure risks a third correction instead of doing it right the first time
with fresh context. Recorded the grounded design as the next checklist item
so it can be executed directly rather than re-investigated.

### 2026-09-20 — Implemented `live_sessions.venue`: the reattach-descriptor half of the corrected Phase 4 plan

Landed the schema/model half of the two corrected Phase 4 findings (the
network-reachability finding is next, folded into the actual venue launch
verb since it only matters once something dispatches into a venue).

Schema v18→v19: nullable `live_sessions.venue` TEXT column (JSON; `NULL` for
the ordinary local case, matching the existing `latest_progress` pattern
rather than a new table). New `LiveSessionVenue` model
(`kind`/`target`/`mux_session_name`) added to `RegisterLiveSessionRequest`
and `LiveSessionInfo`; `register_live_session` takes an optional `venue` JSON
string threaded through the existing upsert; the route serializes at the
Pydantic/JSON boundary (`_parse_venue`, mirroring the existing
`_parse_progress`). Every existing read path goes through the one `_to_info`
function, so register/list/get/resolve all surface it for free -- no route
needed touching individually. New test
(`test_route_register_carries_venue_for_remote_cli_mode`) proves the
descriptor round-trips and an ordinary local registration stays `venue:
null`. Full local test run: `test_live_sessions.py` +
`test_cli_mode_reservations.py` (55 passed); contract hash and
`models.py`/module-size baseline refreshed for the new lines. Version bumped
to `0.4.0-dev511`.

Deliberately did **not** yet touch the reverse-forward gap (the extension
resolving `127.0.0.1`) -- that only matters once an actual venue launch verb
exists to dispatch into, so it lands together with that verb rather than as
speculative plumbing with nothing to exercise it.

### 2026-09-20 — Second-pass correction: `host_index` was the wrong unification target; the real gaps are cross-venue reachability + reattach metadata, and the "no ContainerSpawner" claim was wrong

Starting the implementation follow-up from the entry below (extend
`host_index`/`HostRecord` for CLI mode) surfaced that its target was itself
wrong, before any code changed. Checked `host_index.py` directly: it is
explicitly the daemon's map of processes **it spawned** -- `HostRecord`
carries a dialable `port` and polls `host_pid`/`child_pid` for liveness. A
CLI-mode session is never spawned by the daemon; it self-registers. Checked
where that self-registration actually lands (`routes/live_sessions.py`'s
`register_live_session` -> `db.register_live_session`): `live_sessions`, a
different, already-existing table every attended (non-daemon-spawned)
session already uses to be observable/messageable. CLI mode already writes
there correctly -- Phase 2 got the *mechanism* choice right; there was no
discovery mechanism left to unify, and the previous journal entry's
"promote into `host_index`" plan is retracted (checkbox above marked
superseded).

What's real, found by tracing two things end to end instead of assuming:

1. **No network path back for a remote CLI-mode registration.**
   `extensions/agent-bridge/extension.mjs`'s `resolveBaseUrl()` always
   builds `http://127.0.0.1:<port>` -- it assumes its daemon is reachable on
   its own loopback. Checked what's actually reverse-forwarded into a
   CodeSpace or container today (`session_host/codespace_transport.py`,
   `session_host/container_transport.py`, `relay_launch.py`): only the
   credential-relay port rides a `-R` forward. The daemon's own API port
   does not. A muxed `copilot` process launched inside a venue therefore has
   nothing to register against yet -- this, not a data-model gap, is the
   real remaining Phase 4 prerequisite for cross-venue discovery.
2. **`live_sessions` carries no reattach/venue descriptor.** `machine`,
   `cwd`, `pid`, etc. don't say which CodeSpace/container a session lives in
   or what mux session name to attach to. An additive field is needed
   before an operator or the daemon can act on a remote CLI-mode
   registration.

Also retracted a **separate**, unrelated error from last session while
re-reading the surrounding code: the "agent-bridge has no ContainerSpawner
at all" finding was wrong -- `session_host/container_transport.py` defines
`ContainerTransport` (`boundary = "container"`) and `build_container_spawner()`,
wiring it into the *same* `CodeSpaceSpawner` class CodeSpaces use (it is
already boundary-agnostic, named for its first consumer per its own
docstring). That claim came from grepping only `session_manager.py` for
`class.*Spawner` and missing `session_host/*.py` entirely. Both venues
already have the same ACP dispatch primitive; neither is ahead of the other
for Phase 4's remaining work now.

Updated the vision's Concepts/Features/Behaviors/Non-Goals back off
`host_index` language and onto `live_sessions`, added a same-day Provenance
entry superseding (not deleting) the wrong one, and rewrote this effort's
Phase 4 checklist with the two real findings above plus the ContainerSpawner
retraction. Not yet implemented: the reverse-forward addition and the
`live_sessions` schema field are the next concrete work, followed by the
actual `agent-codespaces`/`agent-containers` launch verb.

### 2026-09-20 — Course correction: no Session Host process for CLI mode, but unify discovery through `host_index`

Operator challenge, after last session's Phase 4 rebase: "agent-bridge should
use the Session Host model for every live bridge... always project a
session-host into the target ... pair the spawned copilot process with a
session-host instance" -- prompted by noticing Phase 2/3's CLI-mode
correlation (a bespoke `cli_mode_reservations` table + a boolean flag on
`live_sessions`) never actually used `host_index`, the daemon's real durable
discovery map every ACP-mode session relies on for reattach.

Explored the strong reading first ("always spawn a real Session Host, even
locally, even for CLI mode") and rejected it: a Session Host exists to give a
**headless** `copilot --acp` child two things it cannot provide itself --
survival across daemon restart/reconnect, and an ACP transport surface so the
daemon can drive a child with no human attached. A CLI-mode session already
has both, from different owners: the multiplexer (tmux/psmux) already keeps
the process alive across detach/reattach -- that's a mux's entire purpose --
and the CLI extension, already loaded inside a real interactive `copilot`
process, already registers and participates with the daemon directly (proven
by Phase 1's send-admission fix and the existing inbox-polling mechanics).
Wrapping that in a spawned Session Host would be a second, redundant
lifecycle manager for a process two other owners already keep alive and
already speak for -- exactly the "second host type" the vision's own
non-goals rule out.

The actual gap is narrower and was already implicit in the operator's
observation: Session Host's *third* job -- durable discoverability via
`host_index` -- is the one thing CLI mode never got, because Phase 2
solved worktree correlation with its own bespoke table instead. Corrected the
vision's "Session Host CLI mode" concept accordingly (see its 2026-09-20
Provenance entry): no host process spawned, but a claimed reservation
promotes into a real `host_index` registration -- a second, honest
`HostRecord` shape (`mode: "cli"`, a mux-reattach descriptor instead of a
dialable port; mux-liveness instead of `host_pid` polling) in the *same*
index, not a parallel one. This reopens part of already-merged Phase 2 (see
its new checklist item) before Phase 4 builds a second venue on top of the
un-unified version -- worth doing now rather than compounding the divergence
across three venues.

Not yet implemented: this session recorded the corrected design in the
vision + effort plan; the `HostRecord`/`host_index.py` extension and the
reservation-claim-to-registration promotion are the next concrete work.

### 2026-09-20 — Gate `ensure_mux_available` behind explicit opt-in (`--ensure-mux`)

Operator pushback on yesterday's tmux self-heal: agent-worktrees has a
long-term goal (documented already in `visions/mux-companion/README.md`:
"Going forward, the multiplexer relationship belongs to the Worktree
Manager, not to `agent-worktrees` directly") to let operators who run a BYO
terminal/session manager (e.g. Herdr) on their **own** machine opt out of
agent-worktrees owning tmux at all. Yesterday's `ensure_mux_available()` was
wired to fire **unconditionally** from `cmd_embody` -- meaning a Herdr user's
own, ordinary `embody` call would have silently `apt-get install tmux`'d
underneath them the moment tmux was absent. That's exactly the ambient
default this effort has otherwise been careful to avoid
(`opt-in-not-ambient-default`).

Clarified scope, though: the reason is about **local flexibility**, not "no
tmux ever." For a freshly-provisioned remote venue (a CodeSpace, a trusted
container) there's nothing else already managing sessions to conflict with,
so defaulting to tmux there is exactly right -- consistent with yesterday's
own CodeSpace finding.

Fixed by making the self-heal an **explicit opt-in**: `embody` grew
`--ensure-mux` (default off); `cmd_embody` only calls
`sessions.ensure_mux_available()` when that flag is set
(`getattr(args, "ensure_mux", False)`, so every existing test/caller that
doesn't know about it is unaffected). `agent-bridge`'s `_launch_cli_mode_session`
now passes `--ensure-mux` explicitly on its `embody` invocation — `cli-mode
launch` **is** the deliberate, per-request case the vision's own
`opt-in-not-ambient-default` behavior already carves out as fine to default.
An ordinary, human-typed `embody` (or any other caller) still gets tmux
missing exactly as before this whole investigation started: `mux_new_session`
fails with a plain "not found" rather than installing anything.

2 new `test_embody.py` cases (confirms `ensure_mux_available` is NOT called
without the flag, IS called with it) plus the existing 8
`test_ensure_mux_available.py` cases and 5 `test_cli_mode_launch.py` cases
(now also asserting `--ensure-mux` is present in the `embody` argv) all pass.
`module-size-baseline.json` widened again for both `__main__.py` files
(agent-bridge 6865→6873; agent-worktrees 28772→28789).

Left the deeper mux-companion/Worktree-Manager ownership-transfer question
alone -- that's a Draft-status vision scoped (so far) to status-push +
hotkey commands, not session creation, and isn't this effort's to resolve.
Noted for whoever eventually does that transfer: `embody`'s session-creation
path (and now `ensure_mux_available`) is a second caller into agent-worktrees'
direct tmux/psmux ownership, alongside Mux-bind's status-push relationship.

### 2026-09-20 — Phase 4 prep: prerequisites confirmed, container-spawner gap found

Worked the first unchecked Phase 4 item: confirming (not assuming) the three
venue-prep prerequisites the checklist named, before writing any
venue-specific launch verb.

- **`copilot` presence:** already verified live for CodeSpaces (yesterday's
  SSH check). For `agent-containers`, checked
  `installer_readiness.inspect_toolchain` directly: it only validates
  **host-side** fleet tooling (`docker`, `devcontainer`, `ssh`) against the
  configured fleet backends -- nothing inspects *inside* a running container
  for `copilot`/`tmux`. Since container images are operator-supplied
  (`containers.yaml`'s `image`/`devcontainer_path`), there is no baseline
  guarantee at all, unlike the CodeSpace devcontainer convention. Any
  container-venue CLI-mode launch must treat this as an explicit precondition
  check, not an assumption -- noted in the effort, not yet built.
- **`agent-worktrees` full install:** unchanged from yesterday's finding --
  still a manual/first-touch step for a venue that's never had a human
  session in it. Not automated as part of this confirmation pass.
- **Reservation stays host-side for a venue launch too:** checked
  `agent-bridge`'s `session_manager.py` directly rather than assuming
  symmetry. Exactly one daemon process runs on the host regardless of spawn
  target; `CodeSpaceSpawner` "bootstraps the Host inside a CodeSpace" but the
  spawned Session Host still registers back to that same host daemon (the
  only daemon that exists), the same as `LocalSpawner`. So Phase 2's
  server-side, worktree-id-keyed reservation correlation at registration time
  needs **no design change** for a CodeSpace-launched CLI-mode session --
  confirmed, not assumed.

**New finding that changes Phase 4's remaining shape:** `session_manager.py`
defines only `LocalSpawner` and `CodeSpaceSpawner` -- there is **no existing
headless/ACP Session Host spawner for containers at all** yet. Extending
`agent-codespaces` to CLI-mode launch is "add a mode to an existing working
venue seam" (the same posture as Phase 3's local case); extending
`agent-containers` would first require inventing that base dispatch
primitive from scratch, a materially bigger and more separable prerequisite
than the checklist assumed when it named both venues together. Rescoped the
remaining Phase 4 plan item: do `agent-codespaces` first; treat
`agent-containers` CLI-mode support as contingent on that base primitive
existing (possibly its own follow-on effort), not silently deferred inside
this one without saying so.

Rebased PR #2966 onto `origin/main` (17 commits, main had advanced by 29) to
land this update: resolved straightforward version-string conflicts (kept the
higher/HEAD value), a contract-registry `sha256` conflict (fixed by
recomputing the real hash post-rebase, not guessing), and a
`module-size-baseline.json` line-count conflict (fixed by widening to the
actual post-rebase line count). Skipped one now-redundant commit
(`719378ea8`, a prior hash-refresh-post-rebase this rebase superseded). Also
had to bump `agent-bridge` and `agent-worktrees` versions again --
`check-version-bump` correctly caught that the conflict resolutions left
stale version strings. Full `agent-bridge` suite post-rebase: 2612 passed, 28
skipped, the same 3 pre-existing unrelated failures. CI fully green on the
rebased PR.

### 2026-09-20 — Phase 4 prep: real-venue tmux gap, fixed with a self-heal

Before writing any venue-specific launch code, checked what a **real** venue
actually has, rather than assuming symmetry with a local dev box. SSH'd into
a live, currently-running operator CodeSpace (an existing venue
of this harness's own operator, Ubuntu 24.04 devcontainer) and checked
directly: `copilot` present (`nvm`-installed, v1.0.86) alongside
`git`/`node`/`python3`/`uv` — all devcontainer conventions. **`tmux` is
absent** (`command not found`), though `apt-cache policy tmux` shows it's
installable (`3.4-1ubuntu0.1`) and `sudo -n true` succeeds (passwordless).
`agent-worktrees`/`agent-bridge` binstubs are absent too, but that's the
already-known lean self-provisioning pattern, not remote-specific.

This means Phase 3's `embody`-backed `cli-mode launch` — which absolutely
depends on a multiplexer for its whole reattach value proposition — would
hit a bare "tmux: command not found" on the very first real venue it's
pointed at. Fixed this now, before any venue-specific verb exists, since it's
venue-agnostic (a bare Linux dev box without tmux hits the identical gap):
added `agent_worktrees.sessions.ensure_mux_available()` — best-effort,
POSIX-only, tries `apt-get`/`dnf`/`yum`/`apk` (via `sudo -n`, or directly if
already root), silent-safe (never raises, returns the current
`mux_available()` state unchanged on Windows or when nothing is installable)
— and wired one call into `cmd_embody` right before `mux_new_session`, so any
venue missing tmux self-heals on the very first `embody`/`cli-mode launch`
rather than failing raw or silently downgrading to a non-reattachable
headless launch. 8 new unit tests (`tests/test_ensure_mux_available.py`, all
`unittest.mock`-faked: already-available fast path, Windows no-op, no
package-manager/no-sudo refusal, root vs. sudo argv shape, falling through to
the next candidate after a failed attempt, and never raising on a
`subprocess` error). Full `agent-worktrees` suite green (see below);
`module-size-baseline.json` widened for `__main__.py` (28673 → 28680) and
`sessions.py` (2610 → 2669) — the pre-existing, unrelated
`picker_tui/engine.py` violation (documented in Phase 2's journal) remains
untouched and is not this change's concern.

Deliberately scoped to POSIX only. `psmux` (the Windows tmux equivalent) is
not a declared dependency of `agent-worktrees` — it's a separately-installed
tool this plugin only detects/repairs PATH for, never provisions — and
Windows self-install (winget/choco) is a meaningfully different problem with
its own UAC/interactivity constraints. Since Phase 4's actual target (a
CodeSpace or trusted container) is essentially always Linux, this is the
right first cut; a genuinely Windows-hosted remote venue is out of scope
until one actually exists.

Updated Phase 4's plan with the concrete venue-prep checklist this
investigation surfaced (`copilot` present-or-bootstrapped, `agent-worktrees`
**full** `install` having run — not just lean tools — and confirming the
CLI-mode reservation stays host-side for a remote launch too) so the next
phase of work has a grounded starting point instead of assuming venue parity
holds for a mechanism (mux) venue-parity's own vision never actually covered.

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
