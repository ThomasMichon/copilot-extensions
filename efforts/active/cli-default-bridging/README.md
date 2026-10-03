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
  the same CLI-mode surface, in Phase 3 (operator/contributor review) as of
  2026-09-30. This effort's Phase 1 work should be checked against that
  review's outcome once it lands, since it may adjust CLI-mode's
  parameter/behavior surface.
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
      **Landed** (contributor buy-in in issue #4702's comments; implemented
      across PR #4658 — merged, forwarded-route takeover fix — and PR #4913
      — merged, the CLI-session-alignment diff split out of #4658 at the
      maintainer's request). Concretely changes the surface Phase 1/2 below
      build on:
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
        (daemon/OS-assigned host port, opt-in) and shared, OS-released
        `keeper_holds` (per-scope holds over one shared per-container/host
        forward) — infrastructure-adjacent, not a CLI-mode behavior change
        Phase 1 needs to react to directly, but worth knowing about if
        Phase 1's extension needs its own port/connection bookkeeping per
        venue.

### Phase 1 — User-global remote-driver extension

- [ ] Design a standalone, marketplace-installable extension distinct from
      `extensions/agent-bridge/extension.mjs` — installed once per
      machine/image, not per-repo/per-worktree, giving baseline drivability
      (attach to the live event stream, send/steer, abort) to any `copilot`
      process that launches with it present, independent of whether
      agent-bridge itself is installed.
- [ ] Scaffold and install it on at least one local machine, one CodeSpace,
      and one container, proving launch-time presence without an
      agent-worktrees or agent-bridge install in the venue itself.
      (Phase 0 update: `agent-ssh` now also has an attached-by-default
      `copilot <host>` CLI entry point — PR #4913 — so an SSH-reachable
      machine is a now-reachable fifth venue candidate alongside the four
      named validation tracks; fold it into "a local machine" or add it
      explicitly when scoping this item.)

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
3. `agent-bridge-cli-session-alignment`'s Phase 3 review landed —
   contributor (`namankanakiya`) buy-in recorded in issue #4702, implemented
   across merged PRs #4658 (forwarded-route takeover fix) and #4913
   (CLI-session-alignment diff, split from #4658 at the maintainer's
   request). This changes CLI-mode's surface in three ways relevant here,
   now folded into Phase 1 above: `agent-ssh` gained an attached-by-default
   `copilot <host>` entry point (a fifth reachable venue); a new atomic
   unclaimed-only reservation-release primitive (protocol v20,
   `CLI_MODE_UNCLAIMED_RELEASE`) is a relevant precedent for Phase 2's
   driver-exclusivity design (though it is itself still launch-time, not
   live-session arbitration); and `agent-codespaces`' new dynamic-forward/
   keeper-hold infrastructure is adjacent context, not a required Phase 1
   change.

Before binding effort-focus and starting Phase 1 execution, the
`## Participants`/`## Coordination` tables still need a real participant and
host named (not a placeholder) — `effort-focus bind` refuses while they're
unfilled. Flagged to the operator rather than invented.
