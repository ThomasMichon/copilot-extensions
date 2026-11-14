import { randomUUID } from "node:crypto";
import { isSubAgentEvent } from "./delivery.mjs";

export const QUESTION_TYPES = [
  "elicitation.requested", "elicitation.completed",
  "user_input.requested", "user_input.completed",
];

const METHODS = {
  elicitation: "handlePendingElicitation",
  user_input: "handlePendingUserInput",
};

export class PendingQuestions {
  constructor() {
    this.reset(null);
    this.acks = new Map();
    this.capability = null;
  }

  reset(sessionId) {
    this.sessionId = sessionId;
    this.generation = randomUUID();
    this.pending = new Map();
    this.tools = new Map();
    this.seen = new Set();
  }

  remember(requestId) {
    this.seen.add(requestId);
    if (this.seen.size > 512) this.seen.delete(this.seen.values().next().value);
  }

  observe(event, sessionId) {
    if (sessionId !== this.sessionId) this.reset(sessionId);
    if (!sessionId || isSubAgentEvent(event)) return;
    const data = event?.data || {};
    if (event.type === "tool.execution_start") {
      this.tools.set(data.toolCallId, data.toolName);
      if (this.tools.size > 128) this.tools.delete(this.tools.keys().next().value);
    }
    if (["abort", "session.idle", "assistant.idle"].includes(event.type)) {
      this.pending.clear();
    }
    if (event.type === "tool.execution_complete") {
      this.tools.delete(data.toolCallId);
      for (const [id, question] of this.pending) {
        if (question.tool_call_id === data.toolCallId) this.pending.delete(id);
      }
    }
    if (!QUESTION_TYPES.includes(event.type) || typeof data.requestId !== "string"
      || !data.requestId || data.requestId.length > 512) return;
    if (event.type.endsWith(".completed")) {
      this.pending.delete(data.requestId);
      this.remember(data.requestId);
      return;
    }
    if (this.seen.has(data.requestId)) return;
    this.remember(data.requestId);
    const family = event.type.split(".")[0];
    const agentQuestion = this.tools.get(data.toolCallId) === "ask_user"
      && !data.elicitationSource && (data.mode === undefined || data.mode === "form");
    for (const [id, question] of this.pending) {
      if (data.toolCallId && question.tool_call_id === data.toolCallId) this.pending.delete(id);
    }
    this.pending.set(data.requestId, {
      family, request_id: data.requestId, tool_call_id: data.toolCallId || null,
      message: String(data.message ?? data.question ?? "").slice(0, 16000),
      requested_schema: data.requestedSchema || null,
      choices: data.choices || null, allow_freeform: data.allowFreeform !== false,
      answerable: agentQuestion,
      reason: agentQuestion ? null : "only agent-initiated ask_user questions are answerable",
    });
    if (this.pending.size > 32) this.pending.delete(this.pending.keys().next().value);
  }

  snapshot(session, identity) {
    return {
      ...identity, generation: this.generation,
      requests: [...this.pending.values()].map((question) => {
        const supported = typeof session.rpc?.ui?.[METHODS[question.family]] === "function";
        return {
          ...question, answerable: question.answerable && supported,
          reason: supported ? question.reason : "SDK pending-question operation is unavailable",
        };
      }),
    };
  }

  async apply(control, session, { isCurrent = () => true, now = Date.now } = {}) {
    const body = control?.body;
    const ack = (state, detail) => ({
      generation: body?.generation || "", request_id: body?.request_id || "", state, detail,
    });
    if (!body || body.session_id !== this.sessionId || body.generation !== this.generation
      || !isCurrent() || !Number.isFinite(body.expires_at) || now() / 1000 >= body.expires_at) {
      return ack("rejected", "native session, generation or answer deadline changed");
    }
    const question = this.pending.get(body.request_id);
    const method = METHODS[body.family];
    if (!question?.answerable || question.family !== body.family
      || typeof session.rpc?.ui?.[method] !== "function") {
      return ack("rejected", "the exact answerable native question is no longer pending");
    }
    const content = body.content;
    if (!content || typeof content !== "object" || Array.isArray(content)
      || !["accept", "decline", "cancel"].includes(body.action)) {
      return ack("rejected", "invalid question response");
    }
    let response;
    if (body.family === "user_input") {
      if (body.action !== "accept" || Object.keys(content).length !== 1
        || typeof content.answer !== "string"
        || (!question.allow_freeform && !question.choices?.includes(content.answer))) {
        return ack("rejected", "user_input requires an allowed text answer");
      }
      response = {
        requestId: body.request_id,
        response: { answer: content.answer, wasFreeform: !question.choices?.includes(content.answer) },
      };
    } else {
      response = { requestId: body.request_id, result: { action: body.action, content } };
    }
    // Reserve before yielding. The native RPC arbitrates against the terminal;
    // neither a duplicate control nor a lost acknowledgment retries the answer.
    this.pending.delete(body.request_id);
    try {
      const result = await session.rpc.ui[method](response);
      const outcome = result?.success === true
        ? ack("accepted", "native pending-question RPC accepted the response")
        : result?.success === false
          ? ack("rejected", "native request was already answered, cancelled or superseded")
          : ack("unknown", "native RPC returned no acceptance result");
      if (outcome.state === "accepted") {
        try {
          await session.log?.(
            `agent-bridge question answer accepted: ${body.family} ${body.request_id}; ` +
            `sender=${String(control.sender || "unknown").replace(/[\r\n]/g, " ").slice(0, 512)}`,
          );
        } catch { /* the durable control outcome remains authoritative */ }
      }
      return outcome;
    } catch {
      return ack("unknown", "native answer outcome is unknown; inspect the terminal, do not replay");
    }
  }
}

export async function pollQuestions(sid, {
  questions, identity, session, request, isCurrent = () => true,
  endpointKey = () => "", now = Date.now, log = () => {},
}) {
  if (!isCurrent() || questions.sessionId !== sid) return;
  const key = endpointKey();
  if (!questions.capability || questions.capability.key !== key
    || now() - questions.capability.checkedAt >= 30_000) {
    const health = await request("GET", "/health");
    questions.capability = {
      key: endpointKey(), checkedAt: now(),
      supported: health?.capabilities?.live_question_answers === 1,
    };
  }
  if (!questions.capability.supported) return;
  const base = `/api/v1/live-sessions/${encodeURIComponent(sid)}/questions`;
  for (const [id, entry] of questions.acks) {
    const original = `/api/v1/live-sessions/${encodeURIComponent(entry.sid)}/questions`;
    try {
      await request("POST", `${original}/answers/${id}/ack`, entry.ack);
    } catch (error) {
      if (![404, 409].includes(error?.status)) throw error;
      log(`question answer ${id}: original audit record unavailable; no native answer replay`);
    }
    questions.acks.delete(id);
  }
  const result = await request("POST", `${base}/poll`, questions.snapshot(session, identity));
  for (const control of result?.controls || []) {
    if (!Number.isSafeInteger(control?.id) || control.id <= 0) continue;
    const ack = await questions.apply(control, session, { isCurrent });
    questions.acks.set(control.id, { sid, ack });
    await request("POST", `${base}/answers/${control.id}/ack`, ack);
    questions.acks.delete(control.id);
  }
}
