import { randomUUID } from "node:crypto";
import { isSubAgentEvent } from "./delivery.mjs";

// Joining mid-turn does not establish that an earlier question is absent.
export class DeliveryAttention {
  constructor() {
    this.reset(null);
  }

  reset(sessionId) {
    this.sessionId = sessionId;
    this.generation = randomUUID();
    this.complete = false;
    this.pending = new Map();
    this.capability = null;
    this.lastPublished = null;
  }

  observe(event, sessionId) {
    if (sessionId !== this.sessionId) this.reset(sessionId);
    if (!sessionId || isSubAgentEvent(event)) return;
    const type = event?.type;
    const data = event?.data || {};
    if (type === "session.idle" || type === "assistant.idle") {
      this.pending.clear();
      this.complete = true;
      return;
    }
    const family = {
      "elicitation.requested": "question", "user_input.requested": "question",
      "permission.requested": "permission",
    }[type];
    if (family && typeof data.requestId === "string" && data.requestId) {
      this.pending.set(data.requestId, family);
      if (this.pending.size > 64) {
        this.pending.delete(this.pending.keys().next().value);
        this.complete = false;
      }
    }
    if (["elicitation.completed", "user_input.completed", "permission.completed"].includes(type)) {
      this.pending.delete(data.requestId);
    }
  }

  snapshot(identity, muted) {
    return {
      ...identity, generation: this.generation, complete: this.complete, muted,
      pending: [...new Set(this.pending.values())].sort(),
    };
  }
}

export async function publishDeliveryAttention({
  sid, attention, identity, muted, request, isCurrent = () => true,
}) {
  if (!sid || attention.sessionId !== sid || !isCurrent()) return;
  const body = attention.snapshot(identity, muted);
  const fingerprint = JSON.stringify(body);
  if (attention.lastPublished?.fingerprint === fingerprint
      && Date.now() - attention.lastPublished.at < 6_000) return;
  if (!attention.capability || Date.now() - attention.capability.checkedAt >= 30_000) {
    const health = await request("GET", "/health");
    attention.capability = {
      supported: health?.capabilities?.live_delivery_observation === 1,
      checkedAt: Date.now(),
    };
  }
  if (!attention.capability.supported || !isCurrent()) return;
  await request(
    "POST", `/api/v1/live-sessions/${encodeURIComponent(sid)}/delivery-state`,
    body,
  );
  if (isCurrent()) attention.lastPublished = { fingerprint, at: Date.now() };
}
