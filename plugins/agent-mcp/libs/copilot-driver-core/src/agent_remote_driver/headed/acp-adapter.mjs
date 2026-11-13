import { randomUUID } from "node:crypto";

class AcpError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

export function createHeadedAdapter(session, { emit, cwd, startupPrompt = null, observeState = null }) {
  const sessionId = session.sessionId;
  if (typeof sessionId !== "string" || !sessionId || typeof emit !== "function") {
    throw new TypeError("Headed SDK binding requires its native session and output");
  }
  let pending = null;
  let sending = false;
  let controlled = true;
  let closed = false;
  let waiting = false;
  let nativeActive = startupPrompt !== null;
  const startupExpected = startupPrompt !== null;
  let startupObserved = false;
  let startupIdle = false;
  let scheduled = false;
  let events = [];
  const seenInputs = new Set();
  const sources = new Set();

  function snapshot() {
    return {
      controlled, closed, pending: Boolean(pending), sending,
      outstanding: nativeActive || sources.size !== 0, waiting,
    };
  }

  function inputStatus() {
    const available = !closed && controlled && !waiting;
    const readiness = (ready) => !available ? "unavailable"
      : !ready ? "pending" : pending || sending ? "busy" : "ready";
    return {
      sessionId,
      startupSeed: !startupExpected ? "not-requested" : startupObserved ? "observed" : "pending",
      startupTurn: !startupExpected ? "not-requested"
        : startupIdle ? "idle-observed" : startupObserved ? "active" : "pending",
      nativeSend: readiness(!startupExpected || startupObserved),
      prompt: readiness(!startupExpected || startupIdle),
    };
  }

  function report() {
    if (observeState) observeState(snapshot());
  }

  function finish(error) {
    const operation = pending;
    if (!operation) return;
    pending = null;
    if (error) operation.reject(error);
    else operation.resolve({ stopReason: operation.cancelled ? "cancelled" : "end_turn" });
    report();
  }

  function observe(event) {
    if (closed) return;
    if (event?.type === "session.error" || event?.type === "session.shutdown") {
      controlled = false;
      finish(new AcpError(-32001, "Native session outcome is unavailable"));
    }
    if (event?.type === "session.idle" && sources.size === 0) {
      nativeActive = false;
      if (startupObserved) startupIdle = true;
    }
    if (event?.type === "user.message" &&
        ![event.agentId, event.data?.agentId, event.data?.parentToolCallId].some(Boolean)) {
      if (event.id && seenInputs.has(event.id)) return;
      if (event.id) {
        seenInputs.add(event.id);
        if (seenInputs.size > 512) seenInputs.delete(seenInputs.values().next().value);
      }
      if (sources.delete(event.data?.source)) {
        if (pending) pending.started = true;
      } else if (startupPrompt !== null && event.data?.content === startupPrompt) {
        startupPrompt = null;
        startupObserved = true;
      } else {
        startupPrompt = null;
        controlled = false;
        finish(new AcpError(-32001, "Native input took control; explicit owner reconciliation is required"));
      }
    }
    if (events.length >= 2_000) {
      controlled = false;
      finish(new AcpError(-32001, "Native observation overflow; control is unavailable"));
      report();
      return;
    }
    events.push({ event, operation: pending, started: Boolean(pending?.started) });
    if (!scheduled) {
      scheduled = true;
      setImmediate(flush);
    }
    report();
  }

  function flush() {
    scheduled = false;
    if (closed) return;
    const batch = events;
    events = [];
    for (const { event, operation, started } of batch) {
      if (!operation || operation !== pending || !started) continue;
      if (event?.type === "assistant.message_delta") {
        emit({
          jsonrpc: "2.0", method: "session/update",
          params: { sessionId, update: {
            sessionUpdate: "agent_message_chunk",
            content: { type: "text", text: event.data?.deltaContent ?? "" },
          } },
        });
      } else if (event?.type === "session.idle") {
        pending.idle = true;
        if (pending.accepted && !pending.cancelling) finish();
      }
    }
  }

  const unsubscribe = session.on(observe);

  function requireSession(params) {
    if (params.sessionId !== sessionId) throw new AcpError(-32602, "The bound native session is required");
  }

  function requireControl() {
    if (closed || !controlled || waiting) throw new AcpError(-32001, "Headed control is unavailable");
  }

  function requireStartup(turn = false) {
    if (startupExpected && (!startupObserved || (turn && !startupIdle))) {
      throw new AcpError(-32002, "Native startup input is not ready; no prompt was dispatched");
    }
  }

  async function prompt(params) {
    requireSession(params);
    requireControl();
    requireStartup(true);
    if (pending || sending) throw new AcpError(-32001, "A native prompt is already in flight");
    if (!Array.isArray(params.prompt) || params.prompt.length === 0 ||
        params.prompt.some((part) => part?.type !== "text" || typeof part.text !== "string")) {
      throw new AcpError(-32602, "This headed binding supports text prompts only");
    }
    const content = params.prompt.map((part) => part.text).join("");
    if (!content) throw new AcpError(-32602, "A nonempty prompt is required");
    const source = `agent-remote-driver-${randomUUID()}`;
    const completion = new Promise((resolve, reject) => {
      pending = {
        resolve, reject, started: false, accepted: false, idle: false,
        cancelled: false, cancelling: false,
      };
    });
    const operation = pending;
    sources.add(source);
    report();
    // Install the completion handler before an SDK send can synchronously emit.
    const result = completion.then((value) => value);
    result.catch(() => {});
    try {
      await session.send({ prompt: content, mode: "enqueue", source });
      if (pending === operation) {
        operation.accepted = true;
        if (operation.idle && !operation.cancelling) finish();
      }
    } catch {
      controlled = false;
      finish(new AcpError(-32001, "Native send outcome is unknown; it will not be replayed"));
    }
    return result;
  }

  async function nativeSend(params) {
    requireSession(params);
    requireControl();
    requireStartup();
    if (pending || sending) throw new AcpError(-32001, "A native prompt is already in flight");
    if (Object.keys(params).some((key) => !["sessionId", "prompt", "mode", "displayPrompt"].includes(key)) ||
        typeof params.prompt !== "string" || !params.prompt ||
        !["enqueue", "immediate"].includes(params.mode) ||
        (params.displayPrompt !== undefined && typeof params.displayPrompt !== "string")) {
      throw new AcpError(-32602, "Native send requires literal text and an explicit supported mode");
    }
    if (sources.size >= 512) {
      controlled = false;
      throw new AcpError(-32001, "Native input observations are incomplete; control is unavailable");
    }
    const source = `agent-remote-driver-${randomUUID()}`;
    sources.add(source);
    sending = true;
    nativeActive = true;
    report();
    try {
      const messageId = await session.send({
        prompt: params.prompt, mode: params.mode, source,
        ...(params.displayPrompt === undefined ? {} : { displayPrompt: params.displayPrompt }),
      });
      if (!controlled || closed || typeof messageId !== "string" || !messageId) {
        throw new Error("Native acceptance is unconfirmed");
      }
      return { messageId };
    } catch {
      controlled = false;
      throw new AcpError(-32001, "Native send outcome is unknown; it will not be replayed");
    } finally {
      sending = false;
      report();
    }
  }

  async function dispatch(method, params) {
    if (method === "initialize") {
      if (params.protocolVersion !== 1) throw new AcpError(-32602, "ACP protocol version 1 is required");
      return {
        protocolVersion: 1,
        agentInfo: { name: "copilot-driver-headed", version: "1" },
        agentCapabilities: { loadSession: false, promptCapabilities: {} },
        _meta: {
          nativeSessionId: sessionId, binding: "existing-native-session",
          automaticIdleRetirement: "unavailable", historyRestoration: "unavailable",
          nativeSend: { version: 1, modes: ["enqueue", "immediate"], receipt: "native-message-id" },
          inputReadiness: { version: 1, method: "copilot-driver/input-status" },
        },
      };
    }
    if (method === "copilot-driver/input-status") {
      requireSession(params);
      return inputStatus();
    }
    if (method === "session/load") {
      requireSession(params);
      if (params.cwd !== cwd || !Array.isArray(params.mcpServers) || params.mcpServers.length) {
        throw new AcpError(-32602, "The existing workspace and unchanged native MCP configuration are required");
      }
      throw new AcpError(-32601, "Native history restoration is not qualified by this headed binding");
    }
    if (method === "session/prompt") return prompt(params);
    if (method === "copilot-driver/native-send") return nativeSend(params);
    if (method === "session/cancel") {
      requireSession(params);
      requireControl();
      if (pending) {
        const operation = pending;
        operation.cancelled = true;
        operation.cancelling = true;
        try {
          await session.abort();
          operation.cancelling = false;
          if (pending === operation && operation.idle && operation.accepted) finish();
        } catch {
          controlled = false;
          finish(new AcpError(-32001, "Native cancellation outcome is unknown"));
        }
      }
      return {};
    }
    throw new AcpError(-32601, "Operation is unsupported by this bound headed session");
  }

  return {
    sessionId,
    authWait() {
      waiting = true;
      report();
      return snapshot();
    },
    async handle(message) {
      const request = message && typeof message === "object" && !Array.isArray(message);
      const hasId = request && Object.hasOwn(message, "id");
      try {
        if (!request || message.jsonrpc !== "2.0" || typeof message.method !== "string" ||
            (hasId && typeof message.id !== "string" && !Number.isSafeInteger(message.id)) ||
            !message.params || typeof message.params !== "object" || Array.isArray(message.params) ||
            (!hasId && message.method !== "session/cancel")) {
          throw new AcpError(-32600, "Invalid JSON-RPC request");
        }
        const result = await dispatch(message.method, message.params);
        return hasId ? { jsonrpc: "2.0", id: message.id, result } : null;
      } catch (error) {
        return hasId ? {
          jsonrpc: "2.0", id: message.id,
          error: { code: error instanceof AcpError ? error.code : -32603,
            message: error instanceof AcpError ? error.message : "Native SDK operation failed" },
        } : null;
      } finally {
        report();
      }
    },
    close() {
      closed = true;
      controlled = false;
      events = [];
      unsubscribe();
      finish(new AcpError(-32001, "Headed connection closed; no operation will be replayed"));
      report();
    },
  };
}
