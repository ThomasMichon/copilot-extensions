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
    : kind && KIND_GUIDANCE[kind];
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

// Translate the bridge's persisted per-message urgency into SDK actions.
// Missing/unknown values intentionally degrade to "queue" so older bridge rows
// and future additive values never drop a message on the floor.
export function deliveryPlan(msg) {
  const options = buildDeliveredSendOptions(msg);
  if (msg?.delivery === "steer") {
    return { abortFirst: false, options: { ...options, mode: "immediate" } };
  }
  if (msg?.delivery === "interrupt") {
    // Immediate after the abort, so it is not queued behind messages that
    // were enqueued before it.
    return { abortFirst: true, options: { ...options, mode: "immediate" } };
  }
  return { abortFirst: false, options };
}
