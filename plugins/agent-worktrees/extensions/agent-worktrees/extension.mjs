// Agent-Worktrees Live-Pulse Extension for Copilot CLI
//
// Passively derives a per-worktree "live activity pulse" from the agent's own
// assistant.intent event stream and flushes it to a small sub-status sidecar
// that the Worktree Picker reads to render a dim, expiring live line.
//
// This is the *pulse* half of the worktree status core (the low-signal/fast
// register): it requires ZERO agent cooperation. It NEVER sets the durable
// disposition (follow_up) -- that is the agent-asserted register, written only
// via `agent-worktrees status`. The two registers are kept strictly separate:
// the pulse is derived here from an observable event; the disposition is
// asserted by the agent.
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

const state = {
  sessionId: null,
  lastIntent: null,
  lastIntentAt: null,   // ISO 8601 of the most recent root-agent intent
  idle: false,          // true once the turn ends (pulse greys in the picker)
  flushTimer: null,     // pending throttled flush handle
  dirty: false,         // an intent arrived since the last flush
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

// On idle, flush the final intent immediately and mark the pulse idle so the
// picker greys it: a turn just finished, nothing is actively in flight. The
// intent text is retained (last thing the agent did) and ages out on its own.
session.on("session.idle", () => {
  state.idle = true;
  if (state.lastIntent) persistSubStatus();
});
