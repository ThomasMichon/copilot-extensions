# CLI-Default Bridging

- **Slug:** `cli-default-bridging`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `dev`
- **Created:** 2026-10-02
- **Status:** Draft
- **Vision:** [`visions/cli-default-bridging`](../../../visions/cli-default-bridging/README.md)
  (leaf, cross-cutting; contests `remote-interactive-sessions`'
  `opt-in-not-ambient-default` stance pending this effort's validation) —
  realizes the vision's `default-promotion-requires-proof` gate: user-global
  remote-driver install, launch-time extension presence, mux-native driver
  exclusivity, the blocked-interaction escalation ladder, and the four named
  validation tracks.
- **Related effort (foundation, not a dependency):**
  [`agent-bridge-cli-mode-sessions`](../agent-bridge-cli-mode-sessions/README.md) —
  already builds and validates CLI-mode as an **opt-in, human-attended**
  capability: send single-stream admission (Phase 1), Session Host CLI mode +
  cwd-keyed discovery (Phase 2), the opt-in local launch surface (Phase 3),
  and symmetric venue launch via `agent-worktrees copilot` /
  `agent-codespaces copilot` / `agent-containers copilot` (Phase 4), the last
  validated end-to-end against a real container and a real private-downstream
  CodeSpace. This effort does not repeat that work — it builds the next layer
  on top of it. Two loose ends from that effort are relevant prerequisites
  here (see Phase 0).
- **Related effort (foundation, not a dependency):**
  [`agent-bridge-cli-session-alignment`](../agent-bridge-cli-session-alignment/README.md) —
  a parallel consistency/alignment pass (evidence-gathered across
  `agent-bridge`, `agent-codespaces`, `agent-containers`, `agent-ssh`) over
  the same CLI-mode surface. Its own Phase 3 checklist (operator review,
  share-for-feedback) remains unchecked as of this writing, but the
  contributor feedback and implementation it was gathering buy-in for have
  already landed (issue #4702's comments; merged PRs #4658, #4913) — see
  Phase 0 below for why that is enough to act on here regardless of the
  sibling effort's own checkbox state.
- **Related issue (explicitly deferred there, reopened here for the
  agent-driven case):**
  [#2971](https://github.com/ThomasMichon/copilot-extensions/issues/2971) —
  a represented interactive session's `ask_user`/elicitation remaining
  unanswerable remotely (read-only, take-over-only). `agent-bridge-cli-mode-sessions`
  deferred this as acceptable for its **human-attended, operator-opted-in**
  scope (a human can always attach and answer directly). It is not acceptable
  for this effort's scope — genuinely headless, agent-driven sessions have no
  human to take over — so Phase 3 below builds the escalation ladder this
  case actually needs.

## Guiding Intent

`agent-bridge-cli-mode-sessions` and `agent-bridge-cli-session-alignment`
already proved that a real, muxed, interactive `copilot` session can be
created, reattached, observed, and gracefully ended through agent-bridge's
existing Session Host / CLI-mode mechanics — validated end-to-end against a
local container and a real CodeSpace. What they deliberately did **not**
build is the next layer: making that same mechanism usable as agent-bridge's
and agent-dispatch's **default**, unattended, agent-to-agent driving surface
— not an operator explicitly opting one session into CLI mode, but an agent
creating, fully controlling, and cleanly ending a mux-hosted session with no
human ever attending it.

This effort exists to build and prove that next layer, closing the specific
gaps the `cli-default-bridging` vision named: a user-global remote-driver
extension (not bundled into agent-bridge), driver-exclusivity arbitration for
an already-running session (distinct from the existing launch-time
allocation gate), the blocked-interaction escalation ladder for the cases
that today only work because a human is attending, and the validation
evidence the vision's `default-promotion-requires-proof` behavior requires
before any sibling vision's opt-in-only language changes.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| ThomasMichon | Sole participant; drives all phases, owns PRs | local worktree |

## Coordination

- **Topology:** independent per-phase PRs, same as `agent-bridge-cli-mode-sessions`.
- **Host (owns PRs):** ThomasMichon.
- **Delegates:** none at present.
- **Handoff:** a completed phase lands as its own reviewed PR before the next
  phase starts, per this repo's normal effort flow.

## Context

Direct source inspection in the research session that produced the
`cli-default-bridging` vision established several concrete, previously
unverified facts (full citations in the vision):

- Extensions do not auto-load in ACP mode today, and no CLI flag, env var, or
  ACP wire parameter can change that (`src/cli/acp/server.ts` has zero
  references to "extension" anywhere).
- `--plugin-dir` already works identically in ACP and ordinary interactive
  startup — the gap is specifically ambient, launch-time extension auto-load,
  not plugin/skill/MCP composition generally.
- A joined SDK extension gets real streamed tool-call/tool-result events and
  a working `session.abort()`; the genuine SDK-level gap is session
  **creation** (locked to the inherited `SESSION_ID`) and `ask_user`/
  elicitation routing (fixed at session creation; legacy `ask_user` has no
  decline shape at all, structured elicitation has only a terminal,
  non-deferrable one).
- **ACP itself already has native session create/terminate** (`session/new`,
  `session/close`) that Session Host already relies on. Agent-bridge's own
  CLI-side extension (`extensions/agent-bridge/extension.mjs`) does not have
  an equivalent today, because it was built for reporting on and lightly
  steering sessions a human already launched — not for full end-to-end
  drive. A `/clear`/`/exit` TTY command bridge is the concrete way to close
  that specific, narrow gap for a mux-driven session.

Separately, `agent-bridge-cli-mode-sessions`' own Journal already proves the
*mechanical* side of CLI-mode sessions end-to-end: a real, genuinely
interactive `copilot` process launched inside a disposable Docker clean room,
a trusted container, and a real private-downstream CodeSpace, each in a
detached tmux session, observed live via `tmux capture-pane`, discoverable
through `live_sessions`, and cleanly torn down on `/exit`. That effort's own
design constraint — the CLI-side extension stays short-lived and
event-based, delegating real work to agent-bridge's daemon or a Session
Host, never holding a long-running connection open in the extension-host
process itself — carries forward unchanged into this effort's Phase 1 work.

## Request

> Naman's work in copilot-extensions has actually built this out quite a
> bit, and we helped too. So agent-bridge *can* drive full muxed CLI
> sessions end-to-end right now; it's just missing some pieces. We're going
> to fill those in. In a handoff, let's plan out an effort to get started.

## Plan

_(The phases below are agent-recommended, derived directly from the
`cli-default-bridging` vision's named gaps and validation gate — the
operator's own request was "fill in the missing pieces... plan out an
effort," without independently specifying phases or ordering. Confirm scope
and ordering before Phase 0 work begins.)_

### Phase 0 — Confirm the foundation this effort builds on

- [x] Check `agent-bridge-cli-mode-sessions`' still-open Validation Plan item
      ("two concurrent CLI-mode allocation attempts for the same cwd resolve
      through the existing single-current-session-per-worktree gate") — this
      is launch-time allocation, distinct from Phase 2's driver-exclusivity
      below (an already-running session), but confirm the distinction holds
      before building on it.
      **Confirmed.** The open item is scoped to `register_live_session` /
      `cli_mode_reservations` (schema v17→v18): an explicit,
      operator-initiated, worktree-id-keyed reservation claimed atomically at
      *registration/launch* time, reusing the existing
      `single-current-session-per-worktree` gate so a second registration for
      the same worktree cannot steal an already-claimed reservation. That is
      entirely about who gets to **launch** a CLI-mode session for a given
      cwd/worktree before one exists yet. Phase 2 below is a different
      question — arbitrating who may **drive** a session that is already
      running — with no existing mechanism closing it today (raw mux allows
      multi-attach by default). The distinction holds; Phase 2 is not
      redundant with this item.
- [x] Note (don't block on) `agent-bridge-cli-mode-sessions`' open Phase 5
      items (docs/architecture updates, vision closure) and open bug #1167 —
      neither blocks this effort's work, but Phase 5's doc updates should be
      cross-checked against whatever this effort changes.
      Noted, not blocking. #1167 (Windows SSH dispatch landing at the wrong
      cwd after resolver bootstrap failure) is squarely that effort's own
      CWD-keyed discovery scope, not this effort's.
- [x] Check `agent-bridge-cli-session-alignment`'s Phase 3 review outcome
      once it lands; adjust Phase 1 below if it changes CLI-mode's
      parameter/behavior surface.
      **The substance this checkbox cares about has landed; the sibling
      effort's own Phase 3 checklist has not been marked done and this item
      does not claim otherwise.** Evidence: contributor
      (`namankanakiya`) buy-in and a detailed per-finding response recorded
      in issue #4702's comments, implemented across merged PR #4658
      (forwarded-route takeover fix) and merged PR #4913 (the
      CLI-session-alignment diff, split out of #4658 at the maintainer's
      request). `agent-bridge-cli-session-alignment/README.md`'s own Phase 3
      items ("Operator reviews the drafted findings/proposal", "Share with
      the reviewed contributor for feedback") remain unchecked there — that
      effort's own completion is out of scope here and not altered by this
      file. What matters for *this* effort is only that CLI-mode's
      parameter/behavior surface already changed as a result, so Phase 1/2
      below must build on the new surface rather than the one described
      when this effort was drafted:
      - `agent-ssh` now has an **attached-by-default** `copilot <host>` CLI
        entry point (previously `--detach`/`--stop`-only, no attach mode),
        and `"ssh"` was added to agent-bridge's `--cli` routing table
        (`_CLI_MODE_VENUE_BINSTUBS`). Phase 1's validation-track list below
        ("a local machine, a CodeSpace, a trusted container, and at least
        one Dev Box image") should add an SSH-reachable machine as a fifth,
        now-reachable venue, or explicitly fold it into "a local machine".
      - A new HTTP protocol **v20** route, `CLI_MODE_UNCLAIMED_RELEASE`,
        gives an atomic *unclaimed-only* reservation release (client never
        sends it to a pre-v20 daemon). This is still a **launch-time**
        reservation primitive (same `cli_mode_reservations` table as above),
        not a driver-exclusivity mechanism for an already-running session —
        but its atomic-claim/compare-and-delete shape is a directly relevant
        precedent to study when designing Phase 2's driver-exclusivity
        primitive.
      - `agent-codespaces` gained dynamic `--forward 0:PORT` local forwards
        (daemon/OS-assigned host port, opt-in) and an Owner beacon carrying
        a process-birth identity (`owner_identity`). Separately, a shared,
        OS-released `keeper_holds` primitive (per-scope holds over one
        shared forward) was added to `ssh-manager`, used by both
        `agent-containers` and `agent-ssh` — not `agent-codespaces`. None of
        this is a CLI-mode behavior change Phase 1 needs to react to
        directly, but worth knowing about if Phase 1's extension needs its
        own port/connection bookkeeping per venue.

### Phase 1 — User-global remote-driver extension

- [x] Design a standalone, marketplace-installable extension distinct from
      `extensions/agent-bridge/extension.mjs` — installed once per
      machine/image, not per-repo/per-worktree, giving baseline drivability
      (attach to the live event stream, send/steer, abort) to any `copilot`
      process that launches with it present, independent of whether
      agent-bridge itself is installed.
      **Done** — `plugins/agent-remote-driver/` (`defaultEnabled: false`
      pending validation). Design: the extension is entirely self-contained
      — no dependency on an agent-bridge daemon, registry, or protocol. On
      join it starts its own loopback-only, bearer-token-authenticated HTTP
      server (`driver-server.mjs`: `GET /health`, `GET /events` as SSE,
      `POST /send`, `POST /steer`, `POST /abort`) and writes a per-session
      discovery descriptor to `~/.copilot/remote-driver/sessions/<id>.json`
      (`discovery.mjs`) that any external process — agent-bridge, a bare
      script, a human's curl — can read to find and drive it. Event
      forwarding follows the same hot-potato discipline as agent-bridge's
      own extension (bounded in-memory queue, decoupled flush timer) but
      forwards the full event stream rather than a curated whitelist, since
      a driving consumer needs more than a representation subset. For
      **launch-time presence** (the vision's load-bearing requirement): a
      local machine/Dev Box enables it in the **user-global**
      `~/.copilot/settings.json`; a CodeSpace/container gets it via the
      already-existing `codespacePlugins`/venue-injection seam documented in
      `docs/patterns/codespace-repo-provenance.md` — no new install
      mechanism was invented, this effort reuses what `agent-codespaces`
      already provides. `driver-server.mjs` and `discovery.mjs` are
      deliberately decoupled from `@github/copilot-sdk` so they are directly
      unit-testable (16 passing `node --test` cases) without the real SDK;
      `extension.mjs` is the thin SDK-coupled glue. Explicitly out of scope
      here (named, not silently absent): driver-exclusivity arbitration
      (Phase 2) and the blocked-interaction escalation ladder (Phase 3) — the
      README documents both gaps rather than implying this extension closes
      them.
- [ ] Scaffold and install it on at least one local machine, one CodeSpace,
      and one container, proving launch-time presence without an
      agent-worktrees or agent-bridge install in the venue itself.
      (Phase 0 update: `agent-ssh` now also has an attached-by-default
      `copilot <host>` CLI entry point — PR #4913 — so an SSH-reachable
      machine is a now-reachable fifth venue candidate alongside the four
      named validation tracks; fold it into "a local machine" or add it
      explicitly when scoping this item.)
      **Partially done.** Local-machine install validated in this worktree:
      plugin loads conceptually correct per the SDK extension-loading
      contract (`extensions/<plugin-name>/extension.mjs` auto-discovery, the
      same mechanism `context-handoff`'s extension already uses), and its
      HTTP surface is validated end-to-end against a real `http.Server`
      instance (not a mock) in `tests/driver-server.test.mjs`. **Not yet
      done:** an actual live `copilot` session with this extension enabled,
      and installs on a CodeSpace/container venue — both require live
      venues this slice did not stand up. Left as the explicit next step
      before Validation Plan item (a) can close.

### Phase 2 — Driver exclusivity arbitration for mux-hosted sessions

- [ ] Design a generation/claim-style primitive for "exactly one driver at a
      time" against an **already-running** mux session — the gap Session
      Host's `host_index.py` already closes for ACP, and raw mux does not
      close by default (multi-attach is its normal behavior).
- [ ] Validate two concurrent driving attempts against the same live session
      cannot corrupt it (interleaved/garbled input, lost turns, or a split
      stream).

### Phase 3 — Blocked-interaction escalation ladder

- [ ] Wire `onPreToolUse` pre-decide policy for known-safe/known-unsafe tool
      calls, closing ordinary tool-permission friction before any prompt
      fires.
- [ ] Implement the best-guess synthetic-answer convention for legacy
      `ask_user` (no native decline shape exists; craft the answer text
      itself, e.g. "proceed with your best reasonable assumption and flag
      it").
- [ ] Implement decline/cancel handling for structured elicitation
      (`onElicitationRequest`) where the SDK allows it.
- [ ] Implement the TTY command bridge (`/clear`, `/exit`) for
      session-lifecycle parity with ACP's native `session/new`/`session/close`.
- [ ] Decide and document, explicitly, how this effort's escalation ladder
      relates to #2971: whether it closes #2971 for the agent-driven case
      specifically while #2971 stays open for the human-attended case, or
      whether the two converge.

### Phase 4 — Full headless end-to-end drive validation

- [ ] Validate agent-bridge (or agent-dispatch) can create, fully drive
      (send, observe tool calls/results, steer, abort), and gracefully end a
      mux-hosted session with no human attending at any point in the flow.
- [ ] Run the same task side-by-side through an equivalent ACP+Session-Host
      flow, for direct comparison.

### Phase 5 — Default-promotion decision

- [ ] Bring Phase 1-4's validation evidence back to the `cli-default-bridging`
      vision's Provenance.
- [ ] If validation holds without unacceptable regression: update
      `remote-interactive-sessions`' and `cli-default-bridging`'s vision
      status/language accordingly, and update `plugins/agent-bridge` /
      `plugins/agent-dispatch` docs to reflect the new default.
- [ ] If validation does not hold: record exactly which track failed and
      why, and leave every contested vision's current language untouched.

## Validation Plan

Directly mirrors the `cli-default-bridging` vision's
`default-promotion-requires-proof` behavior — all four tracks must close
without unacceptable regression before Phase 5 can promote anything:

- [ ] **(a) Cross-venue extension-injection reliability** — the Phase 1
      remote-driver extension is present and functional at launch on: a
      local machine, a CodeSpace, a trusted container, and at least one Dev
      Box image.
- [ ] **(b) Driver-exclusivity** — two concurrent driving attempts against
      the same live mux-hosted session cannot corrupt it (Phase 2).
- [ ] **(c) Blocked-interaction escalation** — all rungs of Phase 3's ladder
      exercised against real tool calls and at least one genuine
      `ask_user`/elicitation case, with no silent hang anywhere in the
      chain.
- [ ] **(d) Side-by-side ACP comparison** — the same task completed through
      both this mechanism and an equivalent ACP+Session-Host flow, compared
      directly (Phase 4).

## Proposal

_Pending — this effort is in Draft; Phase 0 confirmation and operator review
come before execution begins._

## Journal

### 2026-10-02 — Kickoff

Effort created from the `cli-default-bridging` vision (merged same day) and
the operator's own confirmation that `agent-bridge-cli-mode-sessions` and
`agent-bridge-cli-session-alignment` already built and validated CLI-mode
sessions as a working, opt-in, human-attended capability — correcting this
effort's premise away from "whether full drive is achievable at all" toward
"what's still missing to make it agent-bridge's/agent-dispatch's validated
default." Status remains Draft pending the review gate before any Phase 1
work starts.

### 2026-10-02 — Phase 0 complete

Confirmed all three Phase 0 checks (see Plan above for full detail):

1. The still-open `agent-bridge-cli-mode-sessions` Validation Plan item is
   genuinely launch-time allocation (`cli_mode_reservations`,
   worktree-id-keyed, claimed at registration), not a stand-in for Phase 2's
   driver-exclusivity-on-an-already-running-session question — the
   distinction holds and Phase 2 is not redundant.
2. Bug #1167 and the open Phase 5 docs items are noted, non-blocking, and
   squarely that effort's own scope.
3. `agent-bridge-cli-session-alignment`'s review-gathering substance landed
   — contributor (`namankanakiya`) buy-in and a detailed per-finding
   response recorded in issue #4702's comments, implemented across merged
   PRs #4658 (forwarded-route takeover fix) and #4913 (CLI-session-alignment
   diff, split from #4658 at the maintainer's request). That sibling
   effort's own Phase 3 checklist ("Operator reviews...", "Share with the
   reviewed contributor...") is still unchecked in its own README — this
   entry does not mark it done, only notes that the surface it was gating
   already changed. Three changes relevant here, now folded into Phase 1
   above: `agent-ssh` gained an attached-by-default `copilot <host>` entry
   point (a fifth reachable venue); a new atomic unclaimed-only
   reservation-release primitive (protocol v20, `CLI_MODE_UNCLAIMED_RELEASE`)
   is a relevant precedent for Phase 2's driver-exclusivity design (though it
   is itself still launch-time, not live-session arbitration); and
   `agent-codespaces` gained dynamic-forward/owner-identity changes, while a
   separate `keeper_holds` primitive landed in `ssh-manager` shared by
   `agent-containers`/`agent-ssh` (not `agent-codespaces`) — adjacent
   context, not a required Phase 1 change.

Before binding effort-focus and starting Phase 1 execution, the
`## Participants`/`## Coordination` tables still need a real participant and
host named (not a placeholder) — `effort-focus bind` refuses while they're
unfilled. Flagged to the operator rather than invented.

### 2026-10-02 — Phase 1 (design + initial implementation)

Landed the Phase 1 design decision as working code rather than a prose-only
design doc: `plugins/agent-remote-driver/`, a new, standalone, payload-only
plugin (`defaultEnabled: false` — unvalidated, per `default-promotion-requires-proof`).
Key design choice: no shared daemon or registry dependency at all — the
extension is entirely self-contained (its own loopback HTTP server + a
per-session discovery-file convention), so "independent of whether
agent-bridge itself is installed" is true by construction, not by
convention. Launch-time presence reuses `agent-codespaces`' existing
`codespacePlugins`/venue-injection seam and the ordinary user-global
`~/.copilot/settings.json` — no new install mechanism invented for this.

16 `node --test` cases pass (`tests/discovery.test.mjs`,
`tests/driver-server.test.mjs`), exercising the HTTP surface against a real
`http.Server` with a fake driver (no SDK dependency needed for that layer).
Updated `README.md` and `docs/architecture.md` plugin counts/tables and
`.github/plugin/marketplace.json` (new entry + `metadata.version` bump);
added a changefile.

**Not yet done, left as this effort's next slice:** an actual live `copilot`
session with the extension enabled end-to-end, plus CodeSpace and container
installs — Validation Plan item (a) needs all of local + CodeSpace +
container + Dev Box, and only the local-machine design/unit-test layer is
covered so far. Phase 2 (driver-exclusivity) and Phase 3 (escalation ladder)
remain explicitly open and are named as such in the plugin's own README
rather than silently assumed solved.

### 2026-10-02 (cont'd) — Fleet-hygiene hardening (operator-requested)

Operator flagged a real gap before this lands further: the design so far
proved correctness for *one* session, not for a **fleet** of parallel
sessions on the same machine (agent-dispatch workers, several worktrees,
several CodeSpaces/containers). Added `extensions/agent-remote-driver/registry.mjs`
and `bin/list-sessions.mjs`:

- Collisions were already structurally impossible (session-id-named
  descriptors, OS-assigned ephemeral ports) — the real risk was
  **accumulation**: a crashed session never runs its own cleanup, orphaning
  its descriptor. Fixed with self-healing reaping: every new session sweeps
  the shared discovery directory at its own startup — no dedicated reaper
  daemon.
- Bare pid-liveness isn't trustworthy alone (pid reuse after a crash) — added
  a heartbeat (`updatedAt`, refreshed every 30s). `isStale` reaps on EITHER
  signal alone (a dead pid, OR a live pid whose heartbeat has gone stale) —
  not "both required," which would under-reap a hung process with a dead
  heartbeat. A true cross-platform birth-time check (mirroring
  `agent-codespaces`' Connection Owner `owner_identity` pattern) is named as
  a follow-up, not yet built.
- A snapshot-then-act TOCTOU (the owning session can refresh its heartbeat
  between a sweep's staleness snapshot and its delete) is closed by
  `reapIfStillStale`: re-read and re-validate staleness immediately before
  unlinking, never act on the original snapshot alone. Similarly, the
  heartbeat's own on-disk rewrite is now atomic (temp file + rename, see
  `discovery.mjs`'s `writeDescriptorAtomic`) so a concurrent reader never
  observes a half-written descriptor mid-heartbeat. Caught in PR #5036's own
  review round, fixed in the same PR.
- A `SIGINT`/`SIGTERM` handler that only unlinks and returns leaves Node's
  default termination suppressed (a registered listener disables it) — the
  process stays alive with the heartbeat timer still armed, which would
  simply recreate the descriptor the handler just removed. Fixed to match
  `context-handoff`'s established pattern: remove the listener, then
  re-raise the identical signal so the OS's default disposition actually
  terminates the process. Also caught in PR #5036's review.
- `bin/list-sessions.mjs` is the one aggregation entry point: sweep + list +
  bounded-concurrency (max 8 in-flight) `/health` confirmation in a single
  pass, so a fleet controller never re-implements its own per-session
  scan/probe loop (the actual "process-bombing"/connection-storm risk at
  fleet scale).
- `driver-server.mjs` now caps concurrent `/events` subscribers
  (`MAX_SSE_CLIENTS = 16`, `503` past the cap) and `extension.mjs`'s own
  `listen()` is retried a bounded 3 times on a bind race before degrading
  silently — never an unbounded retry loop. A crash
  (`uncaughtException`/`unhandledRejection`) now runs descriptor cleanup
  before exiting.

20 new `node --test` cases (36 total for the plugin, all passing), covering
real spawned-and-exited child processes for pid-liveness, a real
`http.Server` for the SSE connection cap, and filesystem-backed sweep/reap
scenarios including a 25-session synthetic fleet. README's *Fleet hygiene*
section documents the design and the explicit limitation (pid-reuse edge
case) rather than claiming it fully closed.

### 2026-10-03 — Second review round: closing the TOCTOU for real, plus three more fixes

PR #5036's Copilot review round caught that the first-pass TOCTOU fix
(re-read-then-unlink) still had its own narrower race: the owner's heartbeat
rename could land between the re-read and the unlink. Closed properly with
an atomic claim: `reapIfStillStale` now does `renameSync(path, claimPath)`
first (atomic on both POSIX and Windows — whichever write wins the instant
wins outright, with no window where a concurrent writer and the reaper can
act on the same path), revalidates staleness against the claimed copy
(immune to further races since nothing else references `claimPath`), and
either deletes it or renames it back if the owner's write actually won.

Three more real findings, all fixed:
- `writeDescriptorAtomic` leaked its temp file (which carries the bearer
  token) on a failed rename — now cleaned up in all cases, with the
  original error still surfaced.
- A legacy-descriptor compatibility gap: a future rolling update where an
  already-running OLDER session was never taught to write `updatedAt` would
  have its still-alive descriptor reaped purely for predating a protocol
  change. `isStale` now treats a descriptor with NO `updatedAt` key at all
  (not just one present-but-garbage) as pid-liveness-only. Cannot occur
  today (every descriptor this version writes always carries the field) —
  pure forward compatibility, not a current bug.
- `reapIfStillStale`'s unlink-failure reporting was wrong: any unlink error
  (including a genuine permission/I/O failure) was reported as
  `removed: true`. Now only ENOENT (already gone) counts as a successful
  reap; other failures are reported honestly.

Also extracted the SDK-free process lifecycle (bounded listen-retry,
signal-cleanup-then-reraise, the heartbeat/flush interval timer) out of the
SDK-coupled `extension.mjs` into `lifecycle.mjs` — the review's direct ask
("add coverage... extracting this lifecycle would allow deterministic
tests without loading joinSession()"). 13 new tests exercise it with fully
injected dependencies (a fake process-like object for signals, a fake
timer scheduler) — no real OS signals or real waiting required.

Running test count, for the record (the PR's own description previously
mis-stated this round's delta — corrected there too): 36 after the initial
fleet-hygiene commit → 48 after the first review-fix commit (atomic write +
TOCTOU-v1 + signal fix + their tests, +12) → 61 after this round (+13,
TOCTOU-v2 via atomic-claim, legacy-descriptor compatibility, unlink-failure
reporting, the `lifecycle.mjs` extraction and its tests). Comments that
referenced "PR #5036" directly were reworded to stay timeless per review
feedback; the PR's own description now carries the required Documentation
impact statement.

### 2026-10-03 (cont'd) — Third review round: backpressure bound + two more hardening fixes

- **SSE backpressure.** `MAX_SSE_CLIENTS` bounds client *count* but not
  memory on its own — a single slow/non-reading client still lets Node
  queue every forwarded event in its response buffer indefinitely. Added
  `MAX_SSE_BUFFERED_BYTES` (2MB): a client whose buffered backlog exceeds it
  is disconnected outright rather than allowed to keep growing.
- **`writeDescriptorAtomic` write-failure cleanup.** The prior fix only
  cleaned up the temp file when the *rename* failed; `writeFileSync` itself
  can fail (ENOSPC/EIO) after already creating or partially writing the
  file. Both paths now share one cleanup-then-rethrow block.
- **`listDescriptorFiles` silently treated any `readdirSync` failure as an
  empty fleet** — including EACCES/EIO, a genuine "the registry is
  inaccessible" failure distinct from ENOENT's "no fleet yet." Now
  propagates anything other than ENOENT; `bin/list-sessions.mjs` catches
  that specifically and exits 1 with a clear message instead of falsely
  reporting zero sessions.

3 new `node --test` cases this round (64 total for the plugin, all
passing).

### 2026-10-03 (cont'd) — Fourth review round: a real "failed reap exposes stale as live" bug, plus sidecar sweeping

Two more findings, both genuine:

- **`reapIfStillStale` exposed a known-stale descriptor as live on a failed
  claim.** When the atomic claim-rename itself failed for a reason other
  than ENOENT (a permission/I/O error), the code fell back to returning the
  original pre-claim snapshot — which had already been judged stale. That
  snapshot then flowed into `sweepStale`'s `kept` list, so `listLive()` and
  `bin/list-sessions.mjs` would report (and probe) a definitely-dead
  endpoint as if it were real. Fixed: a failed claim (other than ENOENT) now
  returns `descriptor: null` — reported nowhere, neither removed nor live,
  an honest "could not act on this entry" rather than a false positive.
- **Orphaned write/claim sidecars were never swept at all.** Neither a
  `.tmp` file (an interrupted `writeDescriptorAtomic`) nor a `.reap-claim.*`
  file (an interrupted `reapIfStillStale`) is a bare `.json` descriptor, so
  the main sweep loop never looked at them — a crash at exactly the wrong
  instant would leave one of these credential-bearing files on disk
  forever. Added `sweepOrphanedSidecars`, now folded into every `sweepStale`
  call: removes a sidecar once its owning pid is confirmed dead, leaves it
  alone while the owner is still alive (may genuinely be mid-operation).

7 new `node --test` cases this round (71 total for the plugin, all
passing), including making `reapIfStillStale`'s rename function injectable
specifically so the failed-claim branch is deterministically testable
cross-platform, without relying on inconsistent POSIX/Windows permission
APIs.

**Explicitly accepted, not chased further:** the review's repeated
"heartbeat lifecycle lacks automated test coverage" finding. The *generic*
retry/signal/timer mechanics are now fully tested in `lifecycle.mjs`
(round 2's extraction) — what remains untested is the specific wiring
inside `extension.mjs` itself (e.g., "does the heartbeat tick actually call
`writeDescriptor` with the current port/token"), which is entangled with
that module's top-level `await joinSession(...)` side effect on import.
Fully isolating it would mean restructuring `extension.mjs` into an
importable `main()` that doesn't execute on module load — a larger change
than this PR's scope, and not proportionate for a plugin that is
`defaultEnabled: false` and has not yet been run against a single real
`copilot` session (that gap is already named, separately, as this effort's
next slice). Tracked here rather than re-attempted a fourth time.

### 2026-10-03 (cont'd) — Fifth review round: sidecar reaping used the wrong PID

A genuine bug in round 4's own fix: `sweepOrphanedSidecars` checked the
sidecar's JSON **content** pid for liveness, but a `.reap-claim.*` sidecar's
content is the CLAIMED (already-judged-stale) target session's own
descriptor — its pid is *expected* to be dead. Checking that content pid
made every live, currently-reaping claimant's own in-flight claim file look
"owned by a dead pid" and get deleted out from under it mid-operation.
Separately, a genuinely mid-write `.tmp` file (unparseable content) was
being treated identically to an orphan regardless of whether its writer was
alive. Fixed: `sweepOrphanedSidecars` now parses the OWNER pid directly from
the filename (`SIDECAR_RE`'s capture groups) for both sidecar shapes, never
from content — a `.tmp`'s filename pid already happened to match its writer
by construction, but a `.reap-claim.*`'s filename pid (the claimant) and
content pid (the claimed target) are deliberately different, and only the
filename one is the correct liveness signal either way.

Also fixed in this round: the SSE-backlog regression test's read loop had
no bound of its own — a real regression (server stops disconnecting
over-backlogged clients) would have hung the test process instead of
failing it; raced each read against a 2s deadline. Removed "(Nth review
round)" wording from changefile comments (durable release metadata should
describe the technical change, not the review process). Clarified the
README's dead-entry-persistence wording: a dead entry's physical removal
genuinely waits for a sweep to run; seeing one persist with no sweep
triggered in between is expected, not evidence of a bug on its own.

5 new `node --test` cases this round (73 total for the plugin, all
passing), explicitly covering the filename-vs-content pid mismatch for both
sidecar shapes.
