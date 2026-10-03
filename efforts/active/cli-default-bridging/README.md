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
| _(to be filled in before execution begins)_ | | |

## Coordination

- **Topology:** independent per-phase PRs, same as `agent-bridge-cli-mode-sessions`.
- **Host (owns PRs):** _(to be named before execution begins)_.
- **Delegates:** _(to be named before execution begins)_.
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

- [ ] Check `agent-bridge-cli-mode-sessions`' still-open Validation Plan item
      ("two concurrent CLI-mode allocation attempts for the same cwd resolve
      through the existing single-current-session-per-worktree gate") — this
      is launch-time allocation, distinct from Phase 2's driver-exclusivity
      below (an already-running session), but confirm the distinction holds
      before building on it.
- [ ] Note (don't block on) `agent-bridge-cli-mode-sessions`' open Phase 5
      items (docs/architecture updates, vision closure) and open bug #1167 —
      neither blocks this effort's work, but Phase 5's doc updates should be
      cross-checked against whatever this effort changes.
- [ ] Check `agent-bridge-cli-session-alignment`'s Phase 3 review outcome
      once it lands; adjust Phase 1 below if it changes CLI-mode's
      parameter/behavior surface.

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
