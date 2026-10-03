// agent-remote-driver -- user-global remote-driver SDK extension.
//
// Realizes visions/cli-default-bridging's "remote-driver extension (user-
// global, not bridge-bundled)" feature: a standalone, marketplace-installable
// extension distinct from agent-bridge's own `extensions/agent-bridge/`
// (which is narrower by design -- reporting on and lightly steering sessions
// a human already launched). This extension's only job is the floor
// capability: "can this session be driven end-to-end" -- attach to the live
// event stream, send/steer, abort -- with NO dependency on agent-bridge, or
// any other coordination plugin, being installed in this venue. See
// efforts/active/cli-default-bridging/README.md, Phase 1.
//
// Launch-time presence (load-bearing, see the vision's
// extension-presence-is-launch-time-only behavior): this extension must be
// wired into the launch command (marketplace install + enablement in the
// USER-GLOBAL ~/.copilot/settings.json, or an equivalent venue-injection seam
// such as agent-codespaces' `codespacePlugins`/config.d provenance drop-in --
// see docs/patterns/codespace-repo-provenance.md) so it loads from session
// creation, never retrofitted into an already-running session after the
// fact. A post-hoc join cannot retroactively gain pre-tool-use hook or
// ask_user/elicitation routing parity -- this module does not attempt that.
//
// HOT-POTATO DISCIPLINE (load-bearing, same rule agent-bridge's own
// extension and context-handoff's extension both follow): the session.on(...)
// handler below runs on the CLI's own event loop and must never block or
// await slow work. It does only a bounded, synchronous enqueue; a decoupled
// timer drains the queue and fans events out to SSE subscribers.
//
// NO DRIVER-EXCLUSIVITY ARBITRATION YET: this extension accepts /send,
// /steer, and /abort from any holder of the discovery token with no
// generation/claim-style primitive preventing two concurrent drivers from
// racing the same session. That is Phase 2's scope (mux-native-driver-
// exclusivity) -- a real, open gap, not silently papered over here.

import { mkdirSync, writeFileSync, unlinkSync, chmodSync } from "node:fs";
import { approveAll } from "@github/copilot-sdk";
import { joinSession } from "@github/copilot-sdk/extension";
import { createDriverServer } from "./driver-server.mjs";
import { discoveryDir, descriptorPath, generateToken, buildDescriptor } from "./discovery.mjs";

const FLUSH_MS = 250; // drain the forwarded-event queue to SSE subscribers
const MAX_QUEUE = 2_000; // bounded buffer; drop OLDEST on overflow (honest reduced fidelity)

function extLog(msg) {
  try {
    process.stderr.write(`[agent-remote-driver] ${msg}\n`);
  } catch {
    /* ignore */
  }
}

const state = {
  sessionId: process.env.SESSION_ID || null,
  descriptorWritten: false,
  pendingEvents: [],
  listeners: new Set(),
  flusher: null,
};

function enqueue(event) {
  state.pendingEvents.push(event);
  if (state.pendingEvents.length > MAX_QUEUE) state.pendingEvents.shift(); // drop oldest
}

function flush() {
  if (state.pendingEvents.length === 0) return;
  const batch = state.pendingEvents;
  state.pendingEvents = [];
  for (const event of batch) {
    for (const listener of state.listeners) {
      try {
        listener(event);
      } catch {
        /* a single bad listener must not break the fan-out for the rest */
      }
    }
  }
}

function subscribe(listener) {
  state.listeners.add(listener);
  return () => state.listeners.delete(listener);
}

function writeDescriptorIfReady(port, token) {
  if (state.descriptorWritten || !state.sessionId) return;
  try {
    const dir = discoveryDir();
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    const descriptor = buildDescriptor({
      sessionId: state.sessionId,
      pid: process.pid,
      port,
      token,
      cwd: process.cwd(),
    });
    const path = descriptorPath(state.sessionId);
    writeFileSync(path, JSON.stringify(descriptor, null, 2), { mode: 0o600 });
    try {
      chmodSync(path, 0o600); // belt-and-suspenders on platforms where writeFileSync's mode is umask-adjusted
    } catch {
      /* best-effort; not fatal on platforms without POSIX perms (e.g. Windows) */
    }
    state.descriptorWritten = true;
    extLog(`discovery descriptor written: ${path}`);
  } catch (e) {
    // Best-effort, matching agent-bridge's own degrade-silently posture: a
    // session this extension cannot register as drivable still runs exactly
    // as it would without the extension present.
    extLog(`failed to write discovery descriptor: ${e.message}`);
  }
}

function cleanupDescriptor() {
  if (!state.descriptorWritten || !state.sessionId) return;
  try {
    unlinkSync(descriptorPath(state.sessionId));
  } catch {
    /* best-effort */
  }
}

// --- Extension ---
const session = await joinSession({
  // This extension registers no tools of its own, so no permission request
  // is ever routed to it; approveAll is a proven, inert default (matches
  // agent-bridge's own extension and talk-mode).
  onPermissionRequest: approveAll,
});

const token = generateToken();
const driverServer = createDriverServer({
  getSessionId: () => state.sessionId,
  getPid: () => process.pid,
  token,
  send: ({ content, mode }) => session.send({ content, mode }),
  abort: () => session.abort(),
  subscribe,
});
const port = await driverServer.listen();
writeDescriptorIfReady(port, token);

state.flusher = setInterval(flush, FLUSH_MS);
if (typeof state.flusher.unref === "function") state.flusher.unref();

for (const sig of ["exit", "SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    cleanupDescriptor();
  });
}

// Observe-only, non-blocking (hot-potato): enqueue every event verbatim --
// unlike agent-bridge's own REPRESENT_TYPES whitelist, a driving consumer
// needs the full stream, not a curated subset. No I/O, no await here.
session.on((event) => {
  try {
    if (!state.sessionId && event?.sessionId) {
      state.sessionId = event.sessionId;
      writeDescriptorIfReady(port, token);
    }
    enqueue(event);
  } catch {
    /* never let an observe-only handler throw into the CLI */
  }
});
