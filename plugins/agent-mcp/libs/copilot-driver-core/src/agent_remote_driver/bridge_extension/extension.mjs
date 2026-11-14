// agent-bridge live-session registration extension
//
// Owned by Copilot Driver Core; the agent-bridge SDK adapter activates it in every
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
import { open } from "node:fs/promises";
import { join } from "node:path";
import { homedir } from "node:os";
import {
  InFlightMessages, MainTurn, PendingInputNotice, REPLAY_MAX_BYTES, adoptSessionId, consumedMessageId,
  drainControls, drainInbox, isSubAgentEvent, serializedRegister,
  transcriptTail,
} from "./delivery.mjs";
import { firstLoadThisSession } from "./announce.mjs";
import { makeBridgeEndpoint } from "./bridge-endpoint.mjs";
import {
  metadataRefresher, processIdentity, refreshRegistrationMetadata, resolveMachineAsync, resolveMetadataAsync,
} from "./metadata.mjs";
import { machineLookup } from "./machine-context.mjs";
import { createRetainedInbox, retainedDeliverySelected } from "./retained-delivery.mjs";
import { createHeadedDriverSession, createRemoteDriverSession, isRemoteDriverSource } from "./remote-driver.mjs";
import { PendingQuestions, QUESTION_TYPES, pollQuestions } from "./questions.mjs";
import { DeliveryAttention, publishDeliveryAttention } from "./delivery-state.mjs";

export async function activateBridgeExtension({ joinSession, pluginRoot }) {
  // --- Constants ---
  const HEARTBEAT_MS = 30_000; // refresh liveness (updated_at) every 30s
  const HTTP_TIMEOUT_MS = 4_000; // bridge is local; keep it snappy
  const FLUSH_MS = 1_000; // drain the represented-event queue to the bridge every 1s
  const MAX_QUEUE = 1_000; // prioritize root-input evidence; report all-receipt overflow
  const SEEN_EVENT_CAP = 4_096; // bounded set of recent event ids for redelivery dedup
  const FLUSH_BATCH = 250; // max events POSTed per flush
  const INBOX_POLL_MS = 2_000; // poll the bridge for messages to deliver every 2s
  // Delivery (Phase 2 write path) is SEAMLESS by default. This is a
  // single-operator, multi-agent mesh: the operator owns every agent and the
  // transport (localhost bind + operator-secured SSH + local bearer token), so a
  // message reaching the session that should have it needs no per-session opt-in.
  // `/peer` is an optional MUTE (not a gate); AGENT_BRIDGE_DELIVERY=0 (or
  // off/false/no) starts a session muted.
  const DELIVERY_DEFAULT_ON = !/^(0|false|off|no)$/i.test(
    process.env.AGENT_BRIDGE_DELIVERY || "",
  );
  // Selected only by an explicit consumer opt-in. Unsupported retained delivery
  // must hold the original work, never fall through to the legacy ACK path.
  const HEADED_CLIENT = process.env.COPILOT_DRIVER_HEADED_CLIENT;
  delete process.env.COPILOT_DRIVER_HEADED_CLIENT;
  const RETAINED_INBOX = retainedDeliverySelected(process.env.AGENT_BRIDGE_RETAINED_INBOX)
    || Boolean(process.env.AGENT_BRIDGE_REMOTE_DRIVER_ROOT) || HEADED_CLIENT !== undefined;
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
    ...QUESTION_TYPES,
    "session.compaction_start",
    "session.compaction_complete",
  ]);
  const CONFIG_DIR = process.env.AGENT_BRIDGE_CONFIG_DIR
    ? process.env.AGENT_BRIDGE_CONFIG_DIR
    : join(homedir(), ".agent-bridge"); // marketplace-isolation: allow legacy-compatibility

  // --- State ---
  const state = {
    sessionId: process.env.SESSION_ID || null,
    nativeSessionId: process.env.SESSION_ID || null,
    registered: false,
    base: null, // resolved bridge base url (http://127.0.0.1:PORT)
    token: null, // bearer token
    meta: null, // resolved session metadata (machine/cwd/worktree/...)
    heartbeat: null, // interval handle
    flusher: null, // represented-event flush interval handle
    pendingEvents: [], // bounded queue of raw SDK events awaiting flush
    eventSessions: new WeakMap(),
    receiptOverflow: false,
    flushing: false, // guard against overlapping flushes
    seenEventIds: new Set(), // event ids already enqueued -- dedup the CLI's redelivery
    seenEventOrder: [], // FIFO of ids bounding seenEventIds (evict oldest past cap)
    inboxPoll: null, // delivery inbox poll interval handle
    deliveryEnabled: DELIVERY_DEFAULT_ON, // /peer MUTE toggle (delivery on by default)
    delivering: false, // guard against overlapping inbox drains
    inFlight: new InFlightMessages(), // delivered but not yet recorded by the CLI
    pendingInput: new PendingInputNotice(),
    mainTurn: new MainTurn(), // is the main agent's turn running (not a sub-agent's)?
    lastEventAt: 0, // advanced by the observe-only event handler
    readinessTasks: new Map(),
    questions: new PendingQuestions(),
    questioning: false,
    deliveryAttention: new DeliveryAttention(),
    observingDelivery: false,
  };

  function extLog(msg) {
    // Best-effort stderr log (captured by the CLI's extension log).
    try {
      process.stderr.write(`[agent-bridge-ext] ${msg}\n`);
    } catch {
      /* ignore */
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
    // 3) platform default: the Python client retired the former WSL +1 fallback,
    //    so every platform uses the canonical last-resort port.
    return `http://${host}:9280`;
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

  // --- Bridge I/O (off the event loop; always best-effort) ---
  // The endpoint re-resolves the address/token after a failed call (see
  // bridge-endpoint.mjs); state.base/state.token mirror it for the guards below.
  let endpoint = null;
  let retainedInbox = null;
  let headedControl = null;

  function ensureBridgeEndpoint() {
    endpoint ??= makeBridgeEndpoint({
      resolveBase: resolveBaseUrl, resolveToken, fetchImpl: fetch, log: extLog,
      timeoutMs: HTTP_TIMEOUT_MS,
    });
    state.base = endpoint.ep.base;
    state.token = endpoint.ep.token;
  }

  async function fetchBridge(method, path, body) {
    ensureBridgeEndpoint();
    const res = await endpoint.call(method, path, body);
    state.base = endpoint.ep.base;
    state.token = endpoint.ep.token;
    return res;
  }

  async function bridgeFetch(method, path, body) {
    if (!state.base || !state.token) return false;
    try {
      const res = await fetchBridge(method, path, body);
      return res.ok;
    } catch {
      return false; // bridge down/unreachable -> degrade silently
    }
  }

  // GET a JSON body from the bridge (returns parsed object, or null on any error).
  async function bridgeGetJson(path) {
    if (!state.base || !state.token) return null;
    try {
      const res = await fetchBridge("GET", path);
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  }

  async function retainedBridgeRequest(method, path, body) {
    const response = await fetchBridge(method, path, body);
    if (!response.ok) {
      const error = new Error(`bridge ${method} ${path} refused (HTTP ${response.status})`);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  // Session metadata (machine, worktree, ...), resolved off the event loop from
  // load. Registration waits for it -- never readiness: see serializedRegister.
  const lookupMachine = machineLookup(fetchBridge);
  const resolveCurrentMachine = (options) => resolveMachineAsync({ ...options, lookup: lookupMachine });
  const resolveCurrentMetadata = (options) => resolveMetadataAsync({ ...options, extensionPluginRoot: pluginRoot });
  const metadataReady = resolveCurrentMetadata({ sessionId: state.sessionId, machineResolver: resolveCurrentMachine })
    .then((meta) => {
      state.meta = meta;
      if (!meta.machine && !meta.receiving_scope_id) extLog("logical machine owner unresolved; session-only registration with bounded retries");
    })
    .catch((e) => extLog(`metadata resolution failed (degrading, session unaffected): ${e.message}`));
  // A worktree or repo the load-time lookup missed is looked up again (with
  // backoff) on heartbeats, once that first lookup settled. That lookup may wait
  // on a slow CLI, so it never holds the liveness registration: whatever it fills
  // in is registered as soon as it lands.
  let metadataSettled = false;
  metadataReady.finally(() => { metadataSettled = true; });
  const refreshMetadata = metadataRefresher(state, resolveCurrentMetadata, Date.now, { log: extLog });
  async function heartbeat() {
    if (metadataSettled) {
      refreshMetadata().then((filled) => {
        if (!filled) return undefined;
        extLog(`resolved this session's worktree (${state.meta?.worktree_id || "?"}) after registration`);
        return register();
      }).catch(() => {});
    }
    return register();
  }

  const register = serializedRegister(
    state,
    async (id, metadata) => {
      if (!state.base || !state.token) return false;
      try {
        const body = { session_id: id, ...processIdentity(), ...metadata };
        const res = await fetchBridge("POST", "/api/v1/live-sessions", body);
        if (res.ok) return true;
        if (res.status === 409) {
          const body = await res.json().catch(() => null);
          if (body?.detail?.reason === "incarnation_mismatch") {
            extLog(`bridge refused ${id} for this process (another incarnation's row); keeping the registered id`);
            return "rejected";
          }
        }
        return false;
      } catch {
        return false; // bridge down/unreachable -> the next heartbeat retries
      }
    },
    (id) => extLog(`registered live session ${id} with local bridge`),
    { ready: metadataReady, prepare: () => refreshRegistrationMetadata(state, resolveCurrentMachine) },
  );

  async function deregister() {
    for (const entry of state.readinessTasks.values()) entry.controller.abort();
    // Every id this process registered, once no registration is left in flight
    // (a rename can leave the placeholder's row and the resumed one). An id
    // folded into its successor is already gone; its DELETE is a no-op. Each
    // carries this process's identity, so a row another process registered under
    // the id meanwhile (between these DELETEs) is left alone by the bridge.
    const { pid, process_started_at: started } = processIdentity();
    const who = `pid=${encodeURIComponent(pid)}&process_started_at=${encodeURIComponent(started)}`;
    for (const id of await register.close()) {
      await bridgeFetch("DELETE", `/api/v1/live-sessions/${encodeURIComponent(id)}?${who}`);
    }
  }

  const inputReceipt = (event) => event?.data?.source === "agent-bridge"
    && consumedMessageId(event) !== null;

  function enqueueEvents(events) {
    for (const event of events) {
      if (!state.eventSessions.has(event)) state.eventSessions.set(event, state.nativeSessionId);
    }
    state.pendingEvents.push(...events);
    while (state.pendingEvents.length > MAX_QUEUE) {
      const ordinary = state.pendingEvents.findIndex((event) => !inputReceipt(event));
      if (ordinary === -1) state.receiptOverflow = true;
      state.pendingEvents.splice(ordinary === -1 ? 0 : ordinary, 1);
    }
  }

  // Ordinary representation remains best-effort. Retry actual root-input evidence
  // under its original session binding; a send reply or hook return is not evidence.
  async function flushEvents() {
    if (state.flushing) return;
    if (!state.sessionId || !state.registered) return;
    if (state.pendingEvents.length === 0) return;
    state.flushing = true;
    try {
      if (state.receiptOverflow) {
        extLog("root-input receipt buffer overflow; original delivery receipts may remain unknown");
        state.receiptOverflow = false;
      }
      const sid = state.eventSessions.get(state.pendingEvents[0]);
      let count = 0;
      while (count < Math.min(state.pendingEvents.length, FLUSH_BATCH)
          && state.eventSessions.get(state.pendingEvents[count]) === sid) count++;
      const batch = state.pendingEvents.splice(0, count);
      if (batch.length === 0) return;
      const ok = await bridgeFetch(
        "POST",
        `/api/v1/live-sessions/${encodeURIComponent(sid)}/events`,
        { events: batch },
      );
      if (!ok) enqueueEvents(batch.filter(inputReceipt));
    } finally {
      state.flushing = false;
    }
  }

  // Poll the bridge inbox and deliver pending messages into THIS session via
  // session.send (off the CLI event loop, on the poll timer). Delivery is on by
  // default; this does nothing only while the session is MUTED (/peer).
  // Best-effort and serialized; acks only AFTER session.send resolves, so an
  // undelivered message is redelivered next tick rather than lost, and the ack
  // makes redelivery a no-op on the bridge (idempotent) rather than a double
  // injection.
  //
  // buildDeliveredSendOptions (delivery.mjs) always sets an explicit
  // `source: "agent-bridge"`, not cosmetic: SendRequest.source is the runtime's
  // own provenance tag (must be `user`, `system`, `command-<id>`,
  // `schedule-<id>`, or `agent-<agent-id>` -- see the generated API docs for
  // SendRequest). Omitting it lets the runtime's admission logic default an
  // immediate/visible send to `source: "user"` (apply_public_send_admission),
  // which makes a bridge-delivered message indistinguishable from the real
  // operator's own live keystrokes at the contention/steering layer -- exactly
  // the collision "prompt control is experimental until single-stream admission
  // is proven" warns about in docs/delegation-contract.md. Tagging every
  // delivery as an explicit `agent-` source keeps it on its own,
  // correctly-arbitrated lane.
  async function pollInbox() {
    if (state.delivering) return;
    if (!state.sessionId || !state.base || !state.token) return;
    if (!RETAINED_INBOX && (!state.deliveryEnabled || !state.registered)) return;
    state.delivering = true;
    const sid = state.sessionId;
    try {
      if (RETAINED_INBOX) {
        await retainedInbox.tick(sid, { allowSend: state.deliveryEnabled && state.registered });
        return;
      }
      await drainInbox(sid, {
        getJson: bridgeGetJson, post: bridgeFetch, session, inFlight: state.inFlight,
        mainTurn: state.mainTurn, log: extLog,
        onPending: (sid, messages) => state.pendingInput.offered(sid, messages),
        pluginReadiness: true, isCurrent: () => state.registered && state.sessionId === sid
          && state.nativeSessionId === sid,
      });
    } finally {
      state.delivering = false;
    }
  }

  // Poll the bridge for session controls (a mode change: what `/autopilot on`
  // does from this terminal) and apply them through the CLI's own RPC
  // (``drainControls`` in delivery.mjs). Controls are polled apart from
  // messages, so they're never delivered as a prompt.
  async function pollControls() {
    if (state.controlling) return;
    if (!state.sessionId || !state.registered) return;
    state.controlling = true;
    const sid = state.sessionId;
    try {
      await drainControls(sid, {
        getJson: bridgeGetJson, post: bridgeFetch, session, replayHistory, log: extLog,
        isCurrent: () => state.registered && state.sessionId === sid,
        readinessTasks: state.readinessTasks, readinessOnly: RETAINED_INBOX,
      });
    } finally {
      state.controlling = false;
    }
  }

  async function pollQuestionAnswers() {
    if (state.questioning || !state.registered || state.nativeSessionId !== state.sessionId) return;
    state.questioning = true;
    const sid = state.sessionId;
    try {
      await pollQuestions(sid, {
        questions: state.questions, identity: processIdentity(), session,
        request: retainedBridgeRequest,
        endpointKey: () => state.base,
        log: extLog,
        isCurrent: () => state.registered && state.sessionId === sid && state.nativeSessionId === sid,
      });
    } finally {
      state.questioning = false;
    }
  }

  // The bridge lost this session's represented history in a restart: send it the
  // tail of the session's own transcript (control:replay-history). The bridge
  // lands it before the live events it held meanwhile, and drops duplicates.
  async function replayHistory(sid) {
    const path = join(homedir(), ".copilot", "session-state", sid, "events.jsonl");
    let text;
    const fh = await open(path, "r");
    try {
      const { size } = await fh.stat();
      const start = Math.max(0, size - REPLAY_MAX_BYTES);
      const buf = Buffer.alloc(size - start);
      await fh.read(buf, 0, buf.length, start);
      text = buf.toString("utf-8");
    } finally {
      await fh.close();
    }
    const events = transcriptTail(text, REPRESENT_TYPES);
    const ok = await bridgeFetch("POST", `/api/v1/live-sessions/${encodeURIComponent(sid)}/events?replay=true`, { events });
    extLog(`replayed ${events.length} transcript events of ${sid} to the bridge (${ok ? "ok" : "failed"})`);
    return ok;
  }

  // --- Extension ---
  const pendingInputHook = (input, invocation) => state.pendingInput.postTool(
    input, invocation, state.nativeSessionId,
    state.deliveryEnabled && state.registered && state.sessionId === state.nativeSessionId,
  );
  const session = await joinSession({
    hooks: {
      onPostToolUse: pendingInputHook,
      onPostToolUseFailure: pendingInputHook,
    },
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

  if (RETAINED_INBOX) {
    const driverRoot = process.env.AGENT_BRIDGE_REMOTE_DRIVER_ROOT;
    const deliverySession = HEADED_CLIENT !== undefined ? await createHeadedDriverSession(session, {
      declaration: HEADED_CLIENT, cwd: process.cwd(), nativePid: process.ppid, extensionPluginRoot: pluginRoot,
      ...(driverRoot ? {
        pluginRoot: driverRoot, bridgePluginRoot: process.env.AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT,
        expectedSessionId: process.env.AGENT_BRIDGE_NATIVE_SESSION_ID,
      } : {}),
    }) : driverRoot ? createRemoteDriverSession(session, {
      extensionPluginRoot: pluginRoot,
      pluginRoot: driverRoot,
      descriptorDirectory: join(process.env.AGENT_REMOTE_DRIVER_CONFIG_DIR
        || join(homedir(), ".copilot", "remote-driver"), "sessions"),
      expectedSessionId: process.env.AGENT_BRIDGE_NATIVE_SESSION_ID,
      cwd: process.cwd(),
      ownerId: process.env.AGENT_BRIDGE_DRIVER_OWNER,
      bridgePluginRoot: process.env.AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT,
    }) : session;
    if (HEADED_CLIENT !== undefined) headedControl = deliverySession;
    retainedInbox = createRetainedInbox({
      session: deliverySession, request: retainedBridgeRequest, processIdentity: processIdentity(),
      cwd: () => state.meta?.cwd, isCurrent: (sid) => state.sessionId === sid,
      canSend: () => state.deliveryEnabled && state.registered, log: extLog,
      onPending: (sid, messages) => state.pendingInput.offered(sid, messages),
      onConsumed: (sid, id) => state.pendingInput.confirmed(sid, id),
      ...(driverRoot || HEADED_CLIENT !== undefined ? { sourceAllowed: isRemoteDriverSource } : {}),
      ...(process.env.AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT
        ? { portableLaunch: process.env.AGENT_BRIDGE_PORTABLE_ATTEMPT || null } : {}),
    });
    extLog("retained inbox selected explicitly; legacy message ACKs and control effects disabled");
  }

  // Observe-only, non-blocking: the ONLY work done on the CLI event loop. Just
  // note that the session is alive and follow its session id (a missing env var,
  // or a resume that renamed the conversation). No I/O, no await -- returns immediately (hot-potato). Bridge writes
  // happen on the heartbeat timer below. Phase 5 will extend this to buffer
  // events into a bounded queue that a decoupled flusher drains to the bridge.
  session.on((event) => {
    try {
      state.lastEventAt = Date.now();
      state.inFlight.observe(event);
      state.pendingInput.observe(event, state.nativeSessionId);
      state.mainTurn.observe(event);
      if (!isSubAgentEvent(event) && adoptSessionId(state, event?.sessionId)) {
        for (const entry of state.readinessTasks.values()) entry.controller.abort();
        // A late id, or a resume that renamed this conversation: register it
        // off the event loop (the bridge folds a renamed placeholder into it).
        setTimeout(() => register().catch(() => {}), 0);
      }
      state.questions.observe(event, state.nativeSessionId);
      state.deliveryAttention.observe(event, state.nativeSessionId);
      // Represent (Phase 5): enqueue whitelisted events for the flusher. This is
      // pure in-memory bookkeeping -- NO I/O, NO await, NO translation (the bridge
      // translates) -- so the hot-potato rule holds. Bounded overflow sheds
      // ordinary representation before root-input receipt evidence.
      const type = event?.type;
      if (type && REPRESENT_TYPES.has(type)) {
        // Redelivery dedup (load-bearing): the CLI runtime delivers each
        // session.event to this handler once per live-session subscription it
        // holds for the session -- and those accrue across reconnects -- so one
        // logical event arrives N times carrying the SAME stable event.id. Left
        // unchecked, the represented stream fans a single message out to N copies
        // (the on-disk transcript is deduped by this same id and shows exactly
        // one). Drop any id we have already enqueued; fall through only for an
        // event with no id (can't dedup -> honest 1x best-effort).
        const eventId = event?.id;
        if (eventId != null) {
          if (state.seenEventIds.has(eventId)) return; // a redelivery -- skip
          state.seenEventIds.add(eventId);
          state.seenEventOrder.push(eventId);
          if (state.seenEventOrder.length > SEEN_EVENT_CAP) {
            state.seenEventIds.delete(state.seenEventOrder.shift());
          }
        }
        // Forward the id so the bridge can dedup too (defense-in-depth) and any
        // consumer has the event's stable identity.
        // The envelope's agentId marks a sub-agent's event (absent for the main
        // agent), so the bridge never mistakes background sub-agent activity
        // for the session's own turn.
        const forwarded = { type, id: eventId ?? null, data: event.data ?? {} };
        if (event.agentId) forwarded.agentId = event.agentId;
        enqueueEvents([forwarded]);
      }
    } catch {
      /* never throw out of an event handler */
    }
  });

  // --- Load-time initialization (runs once; async work off the event loop) ---
  try {
    ensureBridgeEndpoint();
    // state.meta starts unset -- metadataReady (above) fills it in the
    // background. Deliberately NOT awaited here: this whole init block must
    // finish (and the extension report ready) without waiting on any child
    // process. Registration alone waits for it (it carries this process's
    // identity, processIdentity(), and spreads `...(state.meta || {})`), so a
    // placeholder is registered with its machine and worktree and a resume
    // that renames it can fold it in. A failed lookup registers without them.

    if (!state.token) {
      extLog("no local agent-bridge auth token found; not registering (ok)");
    } else {
      // Initial registration + periodic heartbeat. The heartbeat is the liveness
      // signal (refreshes updated_at); the bridge reaps rows that go stale, so an
      // ungraceful CLI exit is handled even if deregister never runs.
      register().catch((e) => extLog(`initial register failed: ${e.message}`));
      state.heartbeat = setInterval(() => {
        heartbeat().catch(() => {});
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
        pollControls().catch(() => {});
        pollQuestionAnswers().catch(() => {});
        if (!state.observingDelivery && state.registered) {
          state.observingDelivery = true;
          const sid = state.sessionId;
          publishDeliveryAttention({
            sid, attention: state.deliveryAttention, identity: processIdentity(),
            muted: !state.deliveryEnabled, request: retainedBridgeRequest,
            isCurrent: () => state.registered && state.sessionId === sid && state.nativeSessionId === sid,
          }).catch(() => {}).finally(() => { state.observingDelivery = false; });
        }
      }, INBOX_POLL_MS);
      if (state.inboxPoll.unref) state.inboxPoll.unref();
    }
  } catch (e) {
    extLog(`init error (degrading, session unaffected): ${e.message}`);
  }

  // --- Best-effort deregister on exit (staleness reaping is the real backstop) ---
  function shutdown() {
    try {
      retainedInbox?.close();
      headedControl?.close();
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

  // Announce once per session, not once per extension-host start (announce.mjs).
  if (firstLoadThisSession(state.sessionId)) {
    await session.log("agent-bridge live-session extension loaded");
  }
}
