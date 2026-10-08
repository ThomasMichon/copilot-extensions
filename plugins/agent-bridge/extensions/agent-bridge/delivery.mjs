// Pure helpers for rendering and admitting a delivered agent-bridge inbox
// message into a Copilot CLI session. Split out of extension.mjs (which does
// a top-level `await joinSession(...)` and so cannot be imported directly by
// a test) so this logic is unit-testable without the Copilot SDK.

// Render an incoming envelope as an ATTRIBUTED, ANSWERABLE agent-message. The
// wrapper mirrors the runtime's own system markers (<system_reminder> /
// <system_notification>) so a cooperating agent parses it as authoritative
// structure: it can tell peer traffic from operator input, see who sent it, and
// reply with the agent-bridge session catalog's exact argv[0] plus
// `send <reply-to> "..."`.
// Attribute values are escaped; the body is left literal (trusted single-
// operator mesh) for readability.
export function escAttr(v) {
  return String(v == null ? "" : v)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// A non-`prompt` kind (D2) asks only for a terse out-of-band acknowledgement --
// it must NOT be treated as new work. The guidance line makes that explicit to
// the receiving agent so a status ping doesn't spawn a task.
export const KIND_GUIDANCE = {
  notify: "This is a NOTIFY (informational). No reply or new work is expected; " +
    "acknowledge only if useful.",
  "status-check": "This is a STATUS-CHECK. Answer tersely using the exact " +
    "argv[0] from the agent-bridge session command catalog with " +
    "`send <reply-to> \"...\"`; do NOT treat it as new work or start a task.",
};

// A status-check with no reply-to (e.g. from the bridge's browser UI, which is
// not a session) has nowhere to `send` to: answer in the turn itself, where the
// sender is watching.
export const STATUS_CHECK_INLINE_GUIDANCE = "This is a STATUS-CHECK with no " +
  "reply-to. Answer tersely in your reply right here; do NOT treat it as new " +
  "work or start a task, and carry on with what you were doing.";

// An ordinary ("prompt" kind) message with a reply-to still needs the same
// explicit routing hint as notify/status-check: `from` is the sender's
// display label, not guaranteed to be a resolvable send target by itself.
// Without this, a receiving agent naturally tries `send <from> "..."` first
// (reply to whoever it looks like the message came from) and only `reply-to`
// is guaranteed to resolve back through the bridge.
export const PROMPT_REPLY_GUIDANCE = "To reply, use the exact argv[0] from " +
  "the agent-bridge session command catalog with `send <reply-to> \"...\"` " +
  "-- `from` is a display label, not necessarily a valid send target.";

// The runtime's SendRequest.source provenance tag this delivery always uses.
// Must carry the `agent-` prefix (any non-empty suffix is accepted) so the
// runtime's own admission logic (apply_public_send_admission) never defaults
// this send to `source: "user"` -- an unmarked delivery would then be
// indistinguishable from the real operator's own live keystrokes at the
// contention/steering layer, exactly the collision
// docs/delegation-contract.md flags as "experimental until single-stream
// admission is proven".
export const DELIVERY_SOURCE = "agent-bridge";

export function renderDeliveredPrompt(msg) {
  const kind = msg.kind && msg.kind !== "prompt" ? String(msg.kind) : null;
  const attrs = [`from="${escAttr(msg.sender || "unknown")}"`];
  if (msg.reply_to) attrs.push(`reply-to="${escAttr(msg.reply_to)}"`);
  if (typeof msg.id === "number") attrs.push(`msg-id="${msg.id}"`);
  if (kind) attrs.push(`kind="${escAttr(kind)}"`);
  const body = String(msg.body ?? "");
  const text = kind === "status-check" && !msg.reply_to
    ? STATUS_CHECK_INLINE_GUIDANCE
    : kind
      ? KIND_GUIDANCE[kind]
      : msg.reply_to && PROMPT_REPLY_GUIDANCE;
  const guidance = text ? `\n\n(${text})` : "";
  return `<agent-message ${attrs.join(" ")}>\n${body}${guidance}\n</agent-message>`;
}

// Build the exact options object pollInbox() passes to session.send() for one
// delivered message. Centralized so every delivery path (today: the inbox
// poller) is guaranteed to carry an explicit, non-"user" source.
export function buildDeliveredSendOptions(msg) {
  return {
    prompt: renderDeliveredPrompt(msg),
    displayPrompt: `Message from ${msg.sender || "peer"} (via agent-bridge)`,
    source: DELIVERY_SOURCE,
  };
}

const MSG_ID_ATTR = /<agent-message\b[^>]*\bmsg-id="(\d+)"/;

// The bridge message id a `user.message` event carries, when it is one of our
// deliveries (the rendered envelope names it), else null.
export function consumedMessageId(event) {
  if (event?.type !== "user.message") return null;
  const data = event.data || {};
  if (data.source !== undefined && data.source !== DELIVERY_SOURCE) return null;
  const match = MSG_ID_ATTR.exec(String(data.transformedContent ?? data.content ?? ""));
  return match ? Number(match[1]) : null;
}

// Messages handed to session.send() that the CLI has not yet recorded as a
// user.message. session.abort() discards the CLI's pending input -- queued and
// steering alike (verified live) -- so an interrupt must re-send these after
// its abort, or they are silently lost.
export class InFlightMessages {
  constructor(limit = 50) {
    this.pending = new Map();
    this.limit = limit;
  }

  sent(msg) {
    this.pending.set(msg.id, msg);
    while (this.pending.size > this.limit) {
      this.pending.delete(this.pending.keys().next().value);
    }
  }

  observe(event) {
    const id = consumedMessageId(event);
    if (id !== null) this.pending.delete(id);
  }

  // The unconsumed messages older than `id`, oldest first, removed from tracking.
  takeBefore(id) {
    const out = [...this.pending.values()]
      .filter((m) => m.id < id)
      .sort((a, b) => a.id - b.id);
    for (const m of out) this.pending.delete(m.id);
    return out;
  }
}

// Whether an SDK session event belongs to a sub-agent rather than the main
// agent: the event envelope's ``agentId`` (absent for the root agent and
// session-level events), plus the legacy ``data.agentId`` /
// ``data.parentToolCallId`` markers the runtime also recognizes.
export function isSubAgentEvent(event) {
  if (!event || typeof event !== "object") return false;
  if (event.agentId) return true;
  const d = event.data;
  return !!(d && typeof d === "object" && (d.agentId || d.parentToolCallId));
}

const MAIN_TURN_START = new Set(["user.message", "assistant.turn_start"]);
// ``assistant.idle`` fires when the main agent's loop goes idle even while
// background sub-agents keep running; ``session.idle`` only once all of them
// are done too. Either one means the main turn is over.
const MAIN_TURN_END = new Set(["assistant.idle", "session.idle"]);

// Tracks whether the session's MAIN turn is running, from the root agent's own
// events only -- a background sub-agent's activity never counts. Starts idle:
// the extension joins as the session starts, and a missed start only degrades
// an interrupt to an immediate (steering) send.
export class MainTurn {
  constructor() {
    this.running = false;
  }

  observe(event) {
    if (isSubAgentEvent(event)) return;
    const type = event?.type;
    if (MAIN_TURN_START.has(type)) this.running = true;
    else if (MAIN_TURN_END.has(type)) this.running = false;
  }
}

// Translate the bridge's persisted per-message urgency into SDK actions.
// Missing/unknown values intentionally degrade to "queue" so older bridge rows
// and future additive values never drop a message on the floor.
//
// ``mainTurnRunning`` (default true: abort when unknown) decides whether an
// interrupt aborts first. session.abort() also cancels every running
// sub-agent, background ones included, so with no main turn to interrupt an
// abort would only kill those -- the message is sent immediately instead.
export function deliveryPlan(msg, { mainTurnRunning = true } = {}) {
  const options = buildDeliveredSendOptions(msg);
  if (msg?.delivery === "steer") {
    return { abortFirst: false, options: { ...options, mode: "immediate" } };
  }
  if (msg?.delivery === "interrupt") {
    // Immediate (after the abort, when there is one), so it is not queued
    // behind messages that were enqueued before it.
    return { abortFirst: !!mainTurnRunning, options: { ...options, mode: "immediate" } };
  }
  return { abortFirst: false, options };
}

// Session controls (polled from /controls, never delivered as a prompt).
export const SESSION_MODES = ["interactive", "plan", "autopilot"];

// What to do for one control row: {action: "set-mode", mode} for a mode change
// the CLI knows, else {action: "skip", reason} (acked so it doesn't loop).
export function controlPlan(msg) {
  if (msg?.kind !== "control:set-mode") {
    return { action: "skip", reason: `unknown control ${msg?.kind}` };
  }
  const mode = String(msg.body ?? "").trim();
  if (!SESSION_MODES.includes(mode)) return { action: "skip", reason: `unknown mode ${mode}` };
  return { action: "set-mode", mode };
}

// Whether session.rpc.mode.set's result means the mode took effect.
export function modeApplied(result) {
  return !!result && result.modeApplied !== false;
}

// Follow the conversation this extension serves. Its id comes from SESSION_ID
// or the first event; a resume in the same process then switches the events to
// the resumed conversation's id. Registering that id while the placeholder is
// still live is what lets the bridge fold the placeholder into it (its claim,
// handle and queued messages), so a change is adopted, never ignored. Returns
// true when the caller must register the (new) id.
export const REJECTED_RETRY_MS = 5 * 60 * 1000;

export function adoptSessionId(state, eventSessionId, now = Date.now()) {
  if (!eventSessionId || eventSessionId === state.sessionId) return false;
  // An id the bridge refused for this process (see serializedRegister) is
  // tried again only after a while, not on every event.
  const retryAt = state.rejectedIds && state.rejectedIds.get(eventSessionId);
  if (retryAt !== undefined && now < retryAt) return false;
  state.sessionId = eventSessionId;
  state.registered = false;
  return true;
}

// One registration at a time (initial, heartbeat, and the one a rename kicks
// off all share it). Each request reads the id when its turn comes, and is
// ready only if that is still the current id: a rename while it was in flight
// registers the new id first. Serialized, a late request for the old id can't
// land after the new one and fold the alias back. ``register.close()`` stops
// admitting registrations, drains the one in flight, and resolves to every id
// this process registered (newest first) -- what shutdown must deregister: a
// DELETE sent before a pending registration lands would leave it behind.
//
// ``post(id)`` resolves to true (registered), "rejected" (the bridge refused
// this process for that id: its row belongs to another incarnation, e.g. a
// crashed predecessor's expired row awaiting purge) or false (unreachable,
// retried by the next heartbeat). On a rejection after a rename, the process
// keeps serving under the id it did register -- inbox, controls and events
// go on -- and the refused id is retried after REJECTED_RETRY_MS. A rejection
// of the only id it serves revokes it: delivery stops (``registered`` false)
// and shutdown no longer deregisters it; a later heartbeat re-registers it if
// the other incarnation's row goes away.
//
// ``ready`` (optional promise) holds every registration until it settles --
// the session's metadata (machine, worktree): a placeholder registered
// without it can't be recognized as the predecessor once a resume renames it,
// so its handle and history would never follow the resumed session. Only
// registration waits on it; ``close()`` stops the wait (nothing posted then).
export function serializedRegister(
  state, post, onRegistered = () => {}, { now = Date.now, ready = null } = {},
) {
  let chain = Promise.resolve();
  let closed = false;
  let stopWaiting;
  const closing = new Promise((resolve) => { stopWaiting = resolve; });
  let lastOk = null;
  const posted = [];
  const once = async () => {
    if (ready) await Promise.race([ready, closing]);
    for (;;) {
      const id = state.sessionId;
      if (!id || closed) return false;
      const result = await post(id);
      if (result === true) {
        lastOk = id;
        if (!posted.includes(id)) posted.push(id);
      } else if (result === "rejected") {
        (state.rejectedIds ||= new Map()).set(id, now() + REJECTED_RETRY_MS);
        // The id is another incarnation's -- whether or not it is still the
        // current one: this process no longer serves it, and shutdown must not
        // delete that row (even if an earlier, id-only registration of it was
        // accepted).
        if (posted.includes(id)) posted.splice(posted.indexOf(id), 1);
        if (lastOk === id) lastOk = null;
      }
      if (state.sessionId !== id) continue; // renamed meanwhile
      if (result === "rejected") {
        if (lastOk) {
          state.sessionId = lastOk; // keep a usable handle; refresh it, then ready
          continue;
        }
        state.registered = false; // stop inbox, control and event delivery
        return false;
      }
      const ok = result === true;
      if (ok && !state.registered) {
        state.registered = true;
        onRegistered(id);
      }
      return ok;
    }
  };
  const register = () => (chain = chain.then(once, once));
  register.close = async () => {
    closed = true;
    stopWaiting(); // a registration still waiting on ``ready`` never posts
    await chain.catch(() => {});
    return [...posted].reverse();
  };
  return register;
}

// Deliver one batch of this session's bridge inbox into the CLI, then ack it.
// ``sid`` is the id the poll started under, used for both the fetch and the
// ack: a rename while a delivery is awaited must not redirect the ack to an id
// whose registration hasn't committed (or was refused) -- the original queue
// would keep the message and deliver it twice. A rollover that does commit in
// between is followed by the bridge's alias forwarding. Best-effort and
// ordered: the batch stops at the first send failure (unacked redelivers).
//
// ``mainTurn`` (a MainTurn) says whether an interrupt has a main turn to abort;
// without one, every interrupt aborts first (the historical behavior).
export async function drainInbox(sid, { getJson, post, session, inFlight, mainTurn, log = () => {} }) {
  const base = `/api/v1/live-sessions/${encodeURIComponent(sid)}`;
  const data = await getJson(`${base}/messages`);
  const messages = data?.messages;
  if (!Array.isArray(messages) || messages.length === 0) return [];
  const delivered = [];
  let interrupted = false;
  for (const msg of messages) {
    if (!msg || typeof msg.id !== "number") continue;
    try {
      const plan = deliveryPlan(msg, { mainTurnRunning: mainTurn ? mainTurn.running : true });
      // One interrupt per batch: a second interrupt in the same batch must not
      // cancel the turn the first one just started.
      const abort = plan.abortFirst && !interrupted;
      if (msg.delivery === "interrupt") interrupted = true;
      if (abort) {
        try {
          await session.abort();
        } catch (e) {
          log(`abort failed before message ${msg.id}: ${e.message}`);
        }
        // The abort discarded every message still pending in the CLI;
        // re-send those (oldest first) so the interrupt loses nothing.
        for (const earlier of inFlight.takeBefore(msg.id)) {
          await session.send({ ...deliveryPlan(earlier).options, mode: "immediate" });
          inFlight.sent(earlier);
        }
      }
      await session.send(plan.options);
      inFlight.sent(msg);
      delivered.push(msg.id);
    } catch (e) {
      log(`deliver failed for message ${msg.id}: ${e.message}`);
      break;
    }
  }
  if (delivered.length > 0) await post("POST", `${base}/messages/ack`, { ids: delivered });
  return delivered;
}

// Apply this session's claimed controls (a mode change) through the CLI's RPC
// and report each outcome. The poll claims each control (returned once; its
// requester can no longer withdraw it), and every claimed control gets an
// outcome -- applied or rejected -- so the bridge's ``POST /mode`` reports what
// really happened; acked under ``sid``, the id it was claimed under (see
// ``drainInbox``). An older bridge without /controls answers 404 (null).
export async function drainControls(sid, { getJson, post, session, log = () => {} }) {
  const base = `/api/v1/live-sessions/${encodeURIComponent(sid)}`;
  const data = await getJson(`${base}/controls`);
  const controls = data?.messages;
  if (!Array.isArray(controls) || controls.length === 0) return;
  const applied = [];
  const rejected = [];
  for (const c of controls) {
    if (!c || typeof c.id !== "number") continue;
    const plan = controlPlan(c);
    if (plan.action === "skip") {
      log(`control ${c.id} rejected: ${plan.reason}`);
      rejected.push(c.id);
      continue;
    }
    try {
      const result = await session.rpc.mode.set({ mode: plan.mode });
      if (modeApplied(result)) {
        log(`control ${c.id}: mode set to ${plan.mode} (from ${c.sender || "bridge"})`);
        applied.push(c.id);
      } else {
        log(`control ${c.id}: mode ${plan.mode} not applied (${result?.status || "no status"})`);
        rejected.push(c.id);
      }
    } catch (e) {
      log(`control ${c.id}: mode ${plan.mode} failed: ${e.message}`);
      rejected.push(c.id);
    }
  }
  const ack = `${base}/controls/ack`;
  if (applied.length > 0) await post("POST", ack, { ids: applied, applied: true });
  if (rejected.length > 0) await post("POST", ack, { ids: rejected, applied: false });
}
