// Agent-Worktrees Live-Pulse Extension for Copilot CLI
//
// Passively derives a per-worktree "live activity pulse" from the agent's own
// assistant.intent event stream and flushes it to a small sub-status sidecar
// that the Worktree Picker reads to render a dim, expiring live line. It also
// gently nudges an operator-driven session, between turns, to keep its DURABLE
// worktree disposition current via `agent-worktrees status` (the 7c/#2591
// ambient-legibility nudge, converged onto the worktree status core).
//
// The pulse itself requires ZERO agent cooperation and NEVER sets the durable
// disposition (follow_up) -- that is the agent-asserted register, written only
// via `agent-worktrees status`. The two registers are kept strictly separate:
// the pulse is derived here from an observable event; the disposition is
// asserted by the agent (which the nudge merely reminds it to do).
//
// Signal: the `assistant.intent` session event (data.intent: string,
// ephemeral: true -- NOT written to events.jsonl, so this sidecar is the sole
// on-disk source). `agentId` is present only for sub-agents; we filter to the
// ROOT agent so a worktree's pulse reflects its own driver, not a delegate.
//
// The sidecar (`substatus.json`) lives beside context-handoff's `context.json`
// in the session-state dir; the picker maps it to a worktree by the session's
// cwd, exactly as it already does for the context-usage sidecar.

import { existsSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { approveAll } from "@github/copilot-sdk";
import { joinSession } from "@github/copilot-sdk/extension";

// Coalesce bursts of intent updates: flush at most once per this interval.
// A long turn re-reports intent repeatedly; the trailing-edge flush keeps the
// sidecar within a few seconds of live without a write per event.
const FLUSH_THROTTLE_MS = 2500;

// --- Ambient disposition nudge (7c convergence, #2591) ---
// The vision's "the fabric gently prompts a brief in-band status"
// (agent-fabric §progress-is-legible-not-just-liveness), for the DURABLE
// disposition. Where the pulse (above) passively shows "what it's doing now"
// for free, this gently reminds an OPERATOR-driven session to keep its worktree
// DISPOSITION current via `agent-worktrees status` -- the high-signal "does this
// still need me" register the pulse can't provide. It targets the worktree
// status core (single home), converging the operator-status nudge OFF the
// ephemeral agent-bridge live-session progress beat (the retired 7c-2 nudge).
//
// Delivery: an **immediate steering interjection** (session.send mode
// "immediate") injected INTO a running turn -- so the agent does the status
// update in-band, before continuing, then resumes as normal (not a separate
// nudge turn). It fires on either cadence: every Nth turn (checked at
// turn-start), or once a single turn has run long enough -- but ONLY when
// meaningful work has accrued since the last nudge/status update, so it never
// nags a session that is idle or just chatting. Ambient, not opt-in (per the
// vision): ON by default, env-disable. At most once per turn.
const NUDGE_ON = !/^(0|off|false|no)$/i.test(
  process.env.AGENT_WORKTREES_STATUS_NUDGE || "",
);
const NUDGE_EVERY_TURNS = 10;                 // nudge about every N root-agent turns
const NUDGE_LONG_TURN_MS = 5 * 60_000;        // ...or once a single turn runs this long
const NUDGE_MIN_WORK = 3;                      // require >= this many tool runs of real
                                              // work since the last nudge (never nag a
                                              // session that has just been chatting)
// A steered / embodied / delegate session (driven_by set) self-reports through
// its own channel; only operator-driven sessions get the ambient nudge.
const NUDGE_OPERATOR_ONLY = !process.env.AGENT_BRIDGE_DRIVEN_BY;

// A tool run counts as "meaningful work" for the nudge guard, and the agent
// running `agent-worktrees status` (write mode) resets the guard -- so a fresh
// status update quiets the nudge until real work accrues again.
const STATUS_WRITE_RE = /agent-worktrees\s+status\b[\s\S]*(--summary|--follow-up|--resolved)/;

const state = {
  sessionId: null,
  lastIntent: null,
  lastIntentAt: null,   // ISO 8601 of the most recent root-agent intent
  idle: false,          // true once the turn ends (pulse greys in the picker)
  flushTimer: null,     // pending throttled flush handle
  dirty: false,         // an intent arrived since the last flush
  turnCount: 0,         // completed root-agent turns (nudge cadence)
  lastNudgeTurn: 0,     // turnCount at the last nudge (or status update)
  workSinceNudge: 0,    // tool runs of real work since the last nudge/status update
  turnRunning: false,   // a root-agent turn is in flight
  nudgedThisTurn: false,// guard: at most one nudge per turn
  longTurnTimer: null,  // fires if the current turn runs past NUDGE_LONG_TURN_MS
  turnsSinceNudge: 0,   // completed root-agent turns since the last nudge (7c)
  lastNudgeAt: 0,       // epoch ms of the last disposition nudge
};

// Persist the sub-status sidecar the picker reads. Best-effort: never throws
// into the event loop. Writes into the live session's own state dir; a missing
// dir means there's nothing meaningful to associate the pulse with, so skip.
function persistSubStatus() {
  try {
    const sid = state.sessionId;
    if (!sid || !state.lastIntent) return;
    const dir = join(homedir(), ".copilot", "session-state", sid);
    if (!existsSync(dir)) return;
    const payload = {
      sessionId: sid,
      intent: state.lastIntent,
      updatedAt: state.lastIntentAt,
      idle: state.idle,
    };
    writeFileSync(join(dir, "substatus.json"), JSON.stringify(payload), "utf-8");
    state.dirty = false;
  } catch {
    // Best-effort; the picker simply omits the live line when absent/unreadable.
  }
}

// Throttled flush: coalesce a burst of intents into at most one write per
// FLUSH_THROTTLE_MS. The trailing edge always flushes so the final intent of a
// burst lands. session.idle flushes out-of-band (below) for prompt finality.
function scheduleFlush() {
  if (state.flushTimer) return;   // a flush is already pending
  state.flushTimer = setTimeout(() => {
    state.flushTimer = null;
    if (state.dirty) persistSubStatus();
  }, FLUSH_THROTTLE_MS);
  // Don't keep the process alive for a pending pulse flush.
  if (typeof state.flushTimer.unref === "function") state.flushTimer.unref();
}

// Render the disposition nudge as a <system_reminder> so the agent parses it as
// authoritative structure (the runtime's own system-marker convention). It is
// deliberately soft: update ONLY if the disposition materially changed, keep it
// one line, do the update, then resume the work in progress.
function renderDispositionNudge() {
  return (
    "<system_reminder>\n" +
    "[agent-worktrees] Worktree legibility checkpoint: before you continue, if " +
    "this worktree's disposition has materially changed, post a ONE-LINE update " +
    "so the operator can see where it stands in the picker + cockpit -- run:\n" +
    '  agent-worktrees status --summary "<what this is / where it left off>" ' +
    "[--follow-up | --resolved]\n" +
    "Use --follow-up if it still needs attention (un-pushed / undeployed / temp " +
    "state, or an offered follow-up); --resolved if it's done. If nothing " +
    "material changed, skip it. Then resume exactly what you were doing.\n" +
    "</system_reminder>"
  );
}

// Inject the disposition reminder as an immediate steering interjection INTO the
// running turn, so the agent does the status update in-band and then continues.
// Fires at most once per turn, for operator-driven sessions only, and ONLY when:
//   * meaningful work has accrued since the last nudge/status update
//     (>= NUDGE_MIN_WORK tool runs) -- never nag a session that has just been
//     chatting or is idle; and
//   * a cadence trigger fired -- either ~every NUDGE_EVERY_TURNS turns, or the
//     current turn has itself run long (`longTurn`).
// Best-effort; never throws into the event loop.
function maybeNudgeDisposition(longTurn = false) {
  if (!NUDGE_ON || !NUDGE_OPERATOR_ONLY) return;
  if (state.nudgedThisTurn || !state.turnRunning) return;
  if (state.workSinceNudge < NUDGE_MIN_WORK) return;   // no meaningful work -> no nudge
  const cadence = state.turnCount - state.lastNudgeTurn >= NUDGE_EVERY_TURNS;
  if (!cadence && !longTurn) return;
  state.nudgedThisTurn = true;
  state.lastNudgeTurn = state.turnCount;
  state.workSinceNudge = 0;
  try {
    session.send({
      prompt: renderDispositionNudge(),
      displayPrompt: "Status checkpoint (worktree legibility)",
      mode: "immediate",
    }).catch(() => {});
  } catch {
    // Best-effort; a failed nudge is never worth disrupting the session.
  }
}

const session = await joinSession({
  onPermissionRequest: approveAll,
});

state.sessionId = session.sessionId ?? null;

// The live pulse: capture the root agent's current intent. Sub-agent intents
// (event.agentId set) are ignored -- the worktree pulse reflects its own
// driver, not a delegated sub-agent's inner monologue.
session.on("assistant.intent", (event) => {
  if (event.agentId) return;              // sub-agent -- not the worktree driver
  const intent = event.data?.intent;
  if (typeof intent !== "string" || !intent.trim()) return;
  state.lastIntent = intent.trim();
  state.lastIntentAt = new Date().toISOString();
  state.idle = false;
  state.dirty = true;
  scheduleFlush();
});

// Turn lifecycle drives the nudge cadence. On each root-agent turn start, count
// it, arm a long-turn timer, and check the every-N-turns cadence (the work guard
// inside decides whether to actually inject). On turn end, stand the timer down.
session.on("assistant.turn_start", (event) => {
  if (event.agentId) return;              // sub-agent turn -- not the driver
  state.turnCount++;
  state.turnRunning = true;
  state.nudgedThisTurn = false;
  if (state.longTurnTimer) clearTimeout(state.longTurnTimer);
  state.longTurnTimer = setTimeout(() => {
    // A single turn has run long -- nudge mid-turn (work guard still applies).
    maybeNudgeDisposition(true);
  }, NUDGE_LONG_TURN_MS);
  if (typeof state.longTurnTimer.unref === "function") state.longTurnTimer.unref();
  // Every-N-turns cadence: inject early in the turn so the status update rides
  // in-band and the agent then resumes this turn's work.
  maybeNudgeDisposition(false);
});

session.on("assistant.turn_end", (event) => {
  if (event.agentId) return;
  state.turnRunning = false;
  if (state.longTurnTimer) {
    clearTimeout(state.longTurnTimer);
    state.longTurnTimer = null;
  }
});

// Tool runs are the "meaningful work" signal that gates the nudge. A completed
// tool run counts as work; the agent running `agent-worktrees status` (write
// mode) instead RESETS the guard -- a fresh status update quiets the nudge until
// real work accrues again, so we never nag without work in between.
session.on("tool.execution_start", (event) => {
  if (event.agentId) return;
  try {
    const args = JSON.stringify(event.data?.arguments ?? "");
    if (STATUS_WRITE_RE.test(args)) {
      state.workSinceNudge = 0;
      state.lastNudgeTurn = state.turnCount;
      state.nudgedThisTurn = true;        // don't also nudge in the same turn
    }
  } catch {
    // Best-effort; a parse miss just means we don't credit a status update.
  }
});

session.on("tool.execution_complete", (event) => {
  if (event.agentId) return;
  state.workSinceNudge++;
});

// On idle, flush the final intent immediately and mark the pulse idle so the
// picker greys it: a turn just finished, nothing is actively in flight. The
// intent text is retained (last thing the agent did) and ages out on its own.
session.on("session.idle", () => {
  state.idle = true;
  if (state.lastIntent) persistSubStatus();
});
