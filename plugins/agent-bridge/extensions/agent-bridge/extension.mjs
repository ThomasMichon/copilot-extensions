// agent-bridge live-session registration extension
//
// Bundled with the agent-bridge plugin; loaded automatically in every
// *interactive* Copilot CLI session (extensions do NOT load in ACP-mode
// sessions -- those are bridge-owned and tracked natively). This extension
// makes an interactive CLI session a first-class citizen of the fabric by
// registering it with the local agent-bridge, so the bridge can later
// represent and message it. Phase 1: registration + heartbeat only.
//
// HOT-POTATO DISCIPLINE (load-bearing): a session.on(...) handler runs on the
// CLI's own event loop. It must NEVER block or await slow work (network I/O,
// session.send). Handlers here do only fast, synchronous, in-memory
// bookkeeping and return immediately; all bridge I/O happens off the event
// loop -- on a decoupled heartbeat timer, fire-and-forget with .catch(). This
// is the same shape the context-handoff extension uses (observe on the
// handler, act on the idle/timer boundary).
//
// BEST-EFFORT: if the local bridge is absent (e.g. a machine without
// agent-bridge) or unreachable, the extension degrades silently -- the CLI
// session runs exactly as before; it simply is not represented. It never
// throws into the CLI.

import { existsSync, readFileSync } from "node:fs";
import { join, basename } from "node:path";
import { homedir, release } from "node:os";
import { execFileSync, execSync } from "node:child_process";
import { approveAll } from "@github/copilot-sdk";
import { joinSession } from "@github/copilot-sdk/extension";

// --- Constants ---
const HEARTBEAT_MS = 30_000; // refresh liveness (updated_at) every 30s
const HTTP_TIMEOUT_MS = 4_000; // bridge is local; keep it snappy
const FLUSH_MS = 1_000; // drain the represented-event queue to the bridge every 1s
const MAX_QUEUE = 1_000; // bounded buffer; drop OLDEST on overflow (honest reduced fidelity)
const FLUSH_BATCH = 250; // max events POSTed per flush
const INBOX_POLL_MS = 2_000; // poll the bridge for messages to deliver every 2s
// Progress nudge (Phase 7 Slice 7c): OPT-IN. Gently prompt an OPERATOR-driven
// session to post a one-line status beat for the cockpit. **Default OFF** --
// enable per session with AGENT_BRIDGE_PROGRESS_NUDGE=1 (or on/true/yes). A
// per-session injected turn proved too noisy in aggregate across a fleet of
// operator sessions, so it no longer fires unless the operator asks for it. Never
// fires for an agent-driven session (it reports via `agent-dispatch progress`).
const NUDGE_CHECK_MS = 120_000; // evaluate whether to nudge every ~2 min
const NUDGE_MIN_INTERVAL_MS = 1_800_000; // at most one nudge per ~30 min (gentle)
const NUDGE_QUIET_MS = 8_000; // only nudge when the session has been quiet this long
const NUDGE_MIN_TURNS = 3; // require >= this many completed turns since last nudge
const NUDGE_DEFAULT_ON = /^(1|true|on|yes)$/i.test(
  process.env.AGENT_BRIDGE_PROGRESS_NUDGE || "",
);
// Delivery (Phase 2 write path) is SEAMLESS by default. This is a
// single-operator, multi-agent mesh: the operator owns every agent and the
// transport (localhost bind + operator-secured SSH + local bearer token), so a
// message reaching the session that should have it needs no per-session opt-in.
// `/peer` is an optional MUTE (not a gate); AGENT_BRIDGE_DELIVERY=0 (or
// off/false/no) starts a session muted.
const DELIVERY_DEFAULT_ON = !/^(0|false|off|no)$/i.test(
  process.env.AGENT_BRIDGE_DELIVERY || "",
);
// SDK event types we represent (Phase 5). Everything else is intentionally not
// forwarded -- the bridge-side translator maps exactly this subset into its
// event vocabulary; keeping the whitelist here avoids buffering noise.
const REPRESENT_TYPES = new Set([
  "user.message",
  "assistant.message",
  "assistant.reasoning",
  "tool.execution_start",
  "tool.execution_complete",
  "assistant.usage",
  "session.usage_info",
  "assistant.turn_end",
  "permission.requested",
]);
const CONFIG_DIR = process.env.AGENT_BRIDGE_CONFIG_DIR
  ? process.env.AGENT_BRIDGE_CONFIG_DIR
  : join(homedir(), ".agent-bridge");

// --- State ---
const state = {
  sessionId: process.env.SESSION_ID || null,
  registered: false,
  base: null, // resolved bridge base url (http://127.0.0.1:PORT)
  token: null, // bearer token
  meta: null, // resolved session metadata (machine/cwd/worktree/...)
  heartbeat: null, // interval handle
  flusher: null, // represented-event flush interval handle
  pendingEvents: [], // bounded queue of raw SDK events awaiting flush
  flushing: false, // guard against overlapping flushes
  inboxPoll: null, // delivery inbox poll interval handle
  deliveryEnabled: DELIVERY_DEFAULT_ON, // /peer MUTE toggle (delivery on by default)
  delivering: false, // guard against overlapping inbox drains
  lastEventAt: 0, // advanced by the observe-only event handler
  turnsSinceNudge: 0, // completed turns since the last progress nudge (7c)
  lastNudgeAt: 0, // when we last injected a progress nudge
  nudgeTimer: null, // progress-nudge interval handle
  nudgeEnabled: NUDGE_DEFAULT_ON, // operator-session progress nudge (7c)
  nudging: false, // guard against overlapping nudges
};

function extLog(msg) {
  // Best-effort stderr log (captured by the CLI's extension log).
  try {
    process.stderr.write(`[agent-bridge-ext] ${msg}\n`);
  } catch {
    /* ignore */
  }
}

// --- Portable CLI runner (Windows binstubs are .cmd -> need a shell) ---
function runCli(bin, args, cwd) {
  try {
    if (process.platform === "win32") {
      const line = [bin, ...args.map((a) => `"${String(a).replace(/"/g, '""')}"`)].join(" ");
      return execSync(line, { cwd, timeout: 8000, encoding: "utf-8" }).trim();
    }
    return execFileSync(bin, args, { cwd, timeout: 8000, encoding: "utf-8" }).trim();
  } catch {
    return null;
  }
}

// --- Bridge discovery (files written by the local daemon) ---
function resolveBaseUrl() {
  const host = "127.0.0.1";
  // 1) active.json routing table (zero-downtime redeploy port flips live here).
  try {
    const p = join(CONFIG_DIR, "active.json");
    if (existsSync(p)) {
      const data = JSON.parse(readFileSync(p, "utf-8"));
      const port = data?.active?.port;
      if (port) return `http://${host}:${port}`;
    }
  } catch {
    /* fall through */
  }
  // 2) static config.yaml port.
  try {
    const p = join(CONFIG_DIR, "config.yaml");
    if (existsSync(p)) {
      const m = readFileSync(p, "utf-8").match(/^\s*port:\s*(\d+)/m);
      if (m) return `http://${host}:${m[1]}`;
    }
  } catch {
    /* fall through */
  }
  // 3) platform default: a host is 9280; only a WSL guest (which shares the
  //    Windows host's TCP port namespace) uses 9281. The discriminator is
  //    "am I a WSL guest?", not "am I non-Windows?" -- bare-metal Linux is 9280.
  const isWsl =
    process.platform === "linux" &&
    (!!process.env.WSL_DISTRO_NAME || /microsoft|wsl/i.test(release()));
  return `http://${host}:${isWsl ? 9281 : 9280}`;
}

function resolveToken() {
  try {
    const p = join(CONFIG_DIR, "auth.yaml");
    if (existsSync(p)) {
      const m = readFileSync(p, "utf-8").match(/^\s*token:\s*(\S+)/m);
      if (m) return m[1].replace(/^["']|["']$/g, "");
    }
  } catch {
    /* ignore */
  }
  return null;
}

// --- One-time session metadata (safe to run sync at load: not a hot path) ---
function resolveMetadata() {
  const cwd = process.cwd();
  const get = (key) => runCli("agent-worktrees", ["get", key], cwd);
  let branch = null;
  try {
    branch = execFileSync("git", ["rev-parse", "--abbrev-ref", "HEAD"], {
      cwd,
      timeout: 5000,
      encoding: "utf-8",
    }).trim();
  } catch {
    branch = null;
  }
  const wtDir = get("worktree-dir");
  return {
    machine: get("machine"),
    cwd,
    worktree_id: wtDir ? basename(wtDir) : null,
    repo: get("project"),
    branch: branch || null,
    // process.pid is the extension host process -- a liveness hint, not the
    // copilot PID. The durable key is session_id; liveness is heartbeat-based.
    pid: process.pid,
    role: null,
    // D4: who is steering this session, if an agent embodied it (set by
    // `agent-worktrees embody --driver`). Surfaces the "driven by <agent>"
    // banner so a human dropping in via Neuron Forge sees who's at the wheel.
    // Absent/null for an operator-launched session.
    driven_by: process.env.AGENT_BRIDGE_DRIVEN_BY || null,
  };
}

// --- Bridge I/O (off the event loop; always best-effort) ---
async function bridgeFetch(method, path, body) {
  if (!state.base || !state.token) return false;
  try {
    const res = await fetch(`${state.base}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${state.token}`,
        "Content-Type": "application/json",
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(HTTP_TIMEOUT_MS),
    });
    return res.ok;
  } catch {
    return false; // bridge down/unreachable -> degrade silently
  }
}

// GET a JSON body from the bridge (returns parsed object, or null on any error).
async function bridgeGetJson(path) {
  if (!state.base || !state.token) return null;
  try {
    const res = await fetch(`${state.base}${path}`, {
      method: "GET",
      headers: { Authorization: `Bearer ${state.token}` },
      signal: AbortSignal.timeout(HTTP_TIMEOUT_MS),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

async function register() {
  if (!state.sessionId) return;
  const payload = { session_id: state.sessionId, ...(state.meta || {}) };
  const ok = await bridgeFetch("POST", "/api/v1/live-sessions", payload);
  if (ok && !state.registered) {
    state.registered = true;
    extLog(`registered live session ${state.sessionId} with local bridge`);
  }
}

async function deregister() {
  if (!state.sessionId) return;
  await bridgeFetch(
    "DELETE",
    `/api/v1/live-sessions/${encodeURIComponent(state.sessionId)}`,
  );
}

// Drain the represented-event queue to the bridge's ingest endpoint. Runs off
// the CLI event loop (on the flush timer, or once at shutdown). Best-effort:
// spliced events are dropped whether or not the POST succeeds, so a transient
// bridge blip gaps the live view rather than growing the buffer unboundedly --
// NF still has the on-disk transcript for durable history.
async function flushEvents() {
  if (state.flushing) return;
  if (!state.sessionId || !state.registered) return;
  if (state.pendingEvents.length === 0) return;
  state.flushing = true;
  try {
    const batch = state.pendingEvents.splice(0, FLUSH_BATCH);
    if (batch.length === 0) return;
    await bridgeFetch(
      "POST",
      `/api/v1/live-sessions/${encodeURIComponent(state.sessionId)}/events`,
      { events: batch },
    );
  } finally {
    state.flushing = false;
  }
}

// Render an incoming envelope as an ATTRIBUTED, ANSWERABLE agent-message. The
// wrapper mirrors the runtime's own system markers (<system_reminder> /
// <system_notification>) so a cooperating agent parses it as authoritative
// structure: it can tell peer traffic from operator input, see who sent it, and
// reply over the same bridge with `agent-bridge send <reply-to> "..."`.
// Attribute values are escaped; the body is left literal (trusted single-
// operator mesh) for readability.
function escAttr(v) {
  return String(v == null ? "" : v)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// A non-`prompt` kind (D2) asks only for a terse out-of-band acknowledgement --
// it must NOT be treated as new work. The guidance line makes that explicit to
// the receiving agent so a status ping doesn't spawn a task.
const KIND_GUIDANCE = {
  notify: "This is a NOTIFY (informational). No reply or new work is expected; " +
    "acknowledge only if useful.",
  "status-check": "This is a STATUS-CHECK. Answer tersely via `agent-bridge " +
    "send <reply-to> \"...\"`; do NOT treat it as new work or start a task.",
};

function renderDeliveredPrompt(msg) {
  const kind = msg.kind && msg.kind !== "prompt" ? String(msg.kind) : null;
  const attrs = [`from="${escAttr(msg.sender || "unknown")}"`];
  if (msg.reply_to) attrs.push(`reply-to="${escAttr(msg.reply_to)}"`);
  if (typeof msg.id === "number") attrs.push(`msg-id="${msg.id}"`);
  if (kind) attrs.push(`kind="${escAttr(kind)}"`);
  const body = String(msg.body ?? "");
  const guidance = kind && KIND_GUIDANCE[kind] ? `\n\n(${KIND_GUIDANCE[kind]})` : "";
  return `<agent-message ${attrs.join(" ")}>\n${body}${guidance}\n</agent-message>`;
}

// Poll the bridge inbox and deliver pending messages into THIS session via
// session.send (off the CLI event loop, on the poll timer). Delivery is on by
// default; this does nothing only while the session is MUTED (/peer).
// Best-effort and serialized; acks only AFTER session.send resolves, so an
// undelivered message is redelivered next tick rather than lost, and the ack
// makes redelivery a no-op on the bridge (idempotent) rather than a double
// injection.
async function pollInbox() {
  if (state.delivering) return;
  if (!state.deliveryEnabled) return;
  if (!state.sessionId || !state.registered) return;
  state.delivering = true;
  try {
    const data = await bridgeGetJson(
      `/api/v1/live-sessions/${encodeURIComponent(state.sessionId)}/messages`,
    );
    const messages = data?.messages;
    if (!Array.isArray(messages) || messages.length === 0) return;
    const delivered = [];
    for (const msg of messages) {
      if (!msg || typeof msg.id !== "number") continue;
      try {
        await session.send({
          prompt: renderDeliveredPrompt(msg),
          displayPrompt: `Message from ${msg.sender || "peer"} (via agent-bridge)`,
        });
        delivered.push(msg.id);
      } catch (e) {
        // Stop the batch on the first send failure; unacked messages redeliver.
        extLog(`deliver failed for message ${msg.id}: ${e.message}`);
        break;
      }
    }
    if (delivered.length > 0) {
      await bridgeFetch(
        "POST",
        `/api/v1/live-sessions/${encodeURIComponent(state.sessionId)}/messages/ack`,
        { ids: delivered },
      );
    }
  } finally {
    state.delivering = false;
  }
}

// Render the progress-nudge as a <system_reminder> so the agent parses it as
// authoritative structure (same convention as delivered peer messages). It is
// deliberately soft: report ONLY if something noteworthy changed, keep it one
// line, and never interrupt or re-plan the current work -- this is a cockpit
// heartbeat, not a task.
function renderProgressNudge(sessionId) {
  return (
    "<system_reminder>\n" +
    "[agent-bridge] Fleet legibility: if you've made meaningful progress " +
    "since your last update, post a ONE-LINE status so the operator can see " +
    "it at a glance in the cockpit -- run:\n" +
    `  agent-bridge live-sessions progress --handle ${sessionId} ` +
    '--summary "<what you are doing / just did>" [--phase <phase>]\n' +
    "Keep it to a single line (a status beat, not a summary). If nothing " +
    "noteworthy has changed, skip it. Do NOT interrupt or re-plan your " +
    "current work; this is just a heartbeat for the cockpit.\n" +
    "</system_reminder>"
  );
}

// Gently nudge an OPERATOR-driven session to post a progress beat (7c). Fires on
// the nudge timer, off the CLI event loop. Skips agent-driven sessions (they
// report via `agent-dispatch progress`), a muted/unregistered session, and any
// session that has done no work since the last nudge -- so an idle session is
// never nagged. Best-effort and serialized.
async function maybeNudgeProgress() {
  if (state.nudging) return;
  if (!state.nudgeEnabled) return;
  if (!state.sessionId || !state.registered) return;
  // Only operator-launched sessions; an agent-driven one self-reports.
  if (state.meta && state.meta.driven_by) return;
  if (state.turnsSinceNudge < NUDGE_MIN_TURNS) return;
  const now = Date.now();
  // At most one nudge per interval, and only when the session has gone briefly
  // quiet (not mid-stream) so we never inject into active output.
  if (now - state.lastNudgeAt < NUDGE_MIN_INTERVAL_MS) return;
  if (now - state.lastEventAt < NUDGE_QUIET_MS) return;
  state.nudging = true;
  try {
    await session.send({
      prompt: renderProgressNudge(state.sessionId),
      displayPrompt: "Progress nudge (agent-bridge cockpit)",
    });
    state.turnsSinceNudge = 0;
    state.lastNudgeAt = Date.now();
  } catch (e) {
    extLog(`progress nudge failed: ${e.message}`);
  } finally {
    state.nudging = false;
  }
}

// --- Extension ---
const session = await joinSession({
  // This extension registers no tools, so no permission request is ever routed
  // to it; approveAll is a proven, inert default (matches talk-mode). It does
  // NOT auto-approve the operator's own tool calls -- those stay with the CLI.
  onPermissionRequest: approveAll,

  // /peer -- optional MUTE toggle for message delivery INTO this session.
  // Delivery is ON by default (single-operator mesh; trust is the transport, not
  // a consent prompt) -- this just lets the operator silence a focused session.
  // Mirrors /talk's role as a control, not a gate.
  commands: [
    {
      name: "peer",
      description:
        "Mute/unmute agent-bridge message delivery INTO this session. Delivery " +
        "is on by default (peer/callback messages arrive as attributed user " +
        "turns); use this to silence or re-enable it.",
      handler: async (ctx) => {
        void ctx;
        state.deliveryEnabled = !state.deliveryEnabled;
        const st = state.deliveryEnabled ? "unmuted (ENABLED)" : "MUTED";
        await session.log(
          `agent-bridge peer delivery ${st}` +
            (state.deliveryEnabled
              ? " -- messages sent to this session are injected as attributed " +
                "user turns."
              : " -- incoming messages will queue but not be delivered until " +
                "unmuted."),
        );
        if (state.deliveryEnabled) {
          pollInbox().catch(() => {});
        }
      },
    },
  ],
});

// Observe-only, non-blocking: the ONLY work done on the CLI event loop. Just
// note that the session is alive and capture the session id if the env var was
// missing. No I/O, no await -- returns immediately (hot-potato). Bridge writes
// happen on the heartbeat timer below. Phase 5 will extend this to buffer
// events into a bounded queue that a decoupled flusher drains to the bridge.
session.on((event) => {
  try {
    state.lastEventAt = Date.now();
    if (!state.sessionId && event?.sessionId) {
      state.sessionId = event.sessionId;
      // Late session id -> kick a one-off registration off the event loop.
      setTimeout(() => register().catch(() => {}), 0);
    }
    // Represent (Phase 5): enqueue whitelisted events for the flusher. This is
    // pure in-memory bookkeeping -- NO I/O, NO await, NO translation (the bridge
    // translates) -- so the hot-potato rule holds. The bounded queue drops the
    // OLDEST event on overflow so a burst never grows memory without limit.
    const type = event?.type;
    if (type && REPRESENT_TYPES.has(type)) {
      state.pendingEvents.push({ type, data: event.data ?? {} });
      if (state.pendingEvents.length > MAX_QUEUE) {
        state.pendingEvents.splice(0, state.pendingEvents.length - MAX_QUEUE);
      }
    }
    // 7c: count completed turns so the progress nudge only fires after real work.
    if (type === "assistant.turn_end") {
      state.turnsSinceNudge += 1;
    }
  } catch {
    /* never throw out of an event handler */
  }
});

// --- Load-time initialization (runs once; async work off the event loop) ---
try {
  state.base = resolveBaseUrl();
  state.token = resolveToken();
  state.meta = resolveMetadata();

  if (!state.token) {
    extLog("no local agent-bridge auth token found; not registering (ok)");
  } else {
    // Initial registration + periodic heartbeat. The heartbeat is the liveness
    // signal (refreshes updated_at); the bridge reaps rows that go stale, so an
    // ungraceful CLI exit is handled even if deregister never runs.
    register().catch((e) => extLog(`initial register failed: ${e.message}`));
    state.heartbeat = setInterval(() => {
      register().catch(() => {});
    }, HEARTBEAT_MS);
    // Don't let the heartbeat timer keep the CLI process alive on shutdown.
    if (state.heartbeat.unref) state.heartbeat.unref();
    // Decoupled represented-event flusher (Phase 5): drains the queue the event
    // handler fills, off the CLI event loop, best-effort. Unref'd so it never
    // pins the process open on exit.
    state.flusher = setInterval(() => {
      flushEvents().catch(() => {});
    }, FLUSH_MS);
    if (state.flusher.unref) state.flusher.unref();
    // Delivery inbox poll (Phase 2): checks the bridge for messages to inject.
    // Always ticking; delivers unless the session is muted (/peer), so the mute
    // takes effect at runtime with no restart. Off the event loop, unref'd,
    // best-effort.
    state.inboxPoll = setInterval(() => {
      pollInbox().catch(() => {});
    }, INBOX_POLL_MS);
    if (state.inboxPoll.unref) state.inboxPoll.unref();
    // Progress nudge (Phase 7 7c): gently prompt an operator-driven session to
    // post a one-line status beat, off the event loop, unref'd, best-effort.
    // Seed lastNudgeAt so the first nudge waits a full interval.
    state.lastNudgeAt = Date.now();
    state.nudgeTimer = setInterval(() => {
      maybeNudgeProgress().catch(() => {});
    }, NUDGE_CHECK_MS);
    if (state.nudgeTimer.unref) state.nudgeTimer.unref();
  }
} catch (e) {
  extLog(`init error (degrading, session unaffected): ${e.message}`);
}

// --- Best-effort deregister on exit (staleness reaping is the real backstop) ---
function shutdown() {
  try {
    if (state.heartbeat) {
      clearInterval(state.heartbeat);
      state.heartbeat = null;
    }
    if (state.flusher) {
      clearInterval(state.flusher);
      state.flusher = null;
    }
    if (state.inboxPoll) {
      clearInterval(state.inboxPoll);
      state.inboxPoll = null;
    }
    if (state.nudgeTimer) {
      clearInterval(state.nudgeTimer);
      state.nudgeTimer = null;
    }
    // One best-effort final drain so the tail's last events aren't lost, then
    // deregister (staleness reaping is the real backstop for either failing).
    flushEvents().catch(() => {});
    deregister().catch(() => {});
  } catch {
    /* ignore */
  }
}
process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);
process.once("beforeExit", shutdown);

await session.log("agent-bridge live-session extension loaded");
