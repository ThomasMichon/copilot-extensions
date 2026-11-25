import { randomUUID } from "node:crypto";

class AcpError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

export function createHeadedAdapter(session, {
  emit, cwd, startupPrompt = null, observeState = null, validateSendContext = null,
}) {
  const sessionId = session.sessionId;
  if (typeof sessionId !== "string" || !sessionId || typeof emit !== "function") {
    throw new TypeError("Headed SDK binding requires its native session and output");
  }
  let pending = null;
  let sending = false;
  let selectedInput = null;
  let interrupting = false;
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
      controlled, closed, pending: Boolean(pending), sending: sending || interrupting,
      outstanding: nativeActive || sources.size !== 0, waiting,
    };
  }

  function inputStatus() {
    const available = !closed && controlled && !waiting;
    const readiness = (ready) => !available ? "unavailable"
      : !ready ? "pending" : pending || sending || interrupting ? "busy" : "ready";
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
    const rootEvent = ![event?.agentId, event?.data?.agentId, event?.data?.parentToolCallId].some(Boolean);
    if (event?.type === "session.error" || event?.type === "session.shutdown") {
      controlled = false;
      finish(new AcpError(-32001, "Native session outcome is unavailable"));
    }
    if (event?.type === "session.idle" && rootEvent && sources.size === 0) {
      nativeActive = false;
      if (startupObserved) startupIdle = true;
    }
    if (event?.type === "user.message" && rootEvent) {
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
      if ([event?.agentId, event?.data?.agentId, event?.data?.parentToolCallId].some(Boolean)) continue;
      const messageId = typeof event?.data?.messageId === "string" && event.data.messageId
        ? event.data.messageId : null;
      let text = null;
      if (event?.type === "assistant.message_delta") {
        const delta = event.data?.deltaContent;
        if (typeof delta !== "string" || !delta ||
            messageId !== null && operation.completeMessages.has(messageId)) continue;
        operation.streamedMessages.add(messageId);
        text = delta;
      } else if (event?.type === "assistant.message") {
        const content = event.data?.content;
        if (typeof content !== "string" || !content ||
            operation.streamedMessages.has(messageId) || operation.streamedMessages.has(null) ||
            messageId === null && operation.streamedMessages.size !== 0) continue;
        const identity = messageId ?? event.id;
        if (identity && operation.completeMessages.has(identity)) continue;
        if (identity) operation.completeMessages.add(identity);
        text = content;
      }
      if (text !== null) {
        emit({
          jsonrpc: "2.0", method: "session/update",
          params: { sessionId, update: {
            sessionUpdate: "agent_message_chunk",
            content: { type: "text", text },
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

  async function requireSelectedContext(turn = false) {
    const input = { cancelled: false };
    selectedInput = input;
    sending = true;
    report();
    try {
      await validateSendContext();
      recheckSelectedContext(turn, input);
    } catch (error) {
      if (error instanceof AcpError && error.code === -32800) throw error;
      controlled = false;
      selectedInput = null;
      sending = false;
      report();
      throw new AcpError(-32001, "Selected native execution context is unavailable; no input was dispatched");
    }
    // Keep the reservation until the caller installs its pending input.
    return input;
  }

  function recheckSelectedContext(turn = false, input = null) {
    try {
      requireControl();
      requireStartup(turn);
      if (session.sessionId !== sessionId) throw new AcpError(-32001, "Native session binding changed");
    } catch (error) {
      controlled = false;
      selectedInput = null;
      sending = false;
      report();
      throw error;
    }
    if (input?.cancelled) {
      selectedInput = null;
      sending = false;
      report();
      throw new AcpError(-32800, "Native input was cancelled; no input was dispatched");
    }
  }

  async function prompt(params) {
    requireSession(params);
    requireControl();
    requireStartup(true);
    if (pending || sending || interrupting) throw new AcpError(-32001, "A native prompt is already in flight");
    if (!Array.isArray(params.prompt) || params.prompt.length === 0 ||
        params.prompt.some((part) => part?.type !== "text" || typeof part.text !== "string")) {
      throw new AcpError(-32602, "This headed binding supports text prompts only");
    }
    const content = params.prompt.map((part) => part.text).join("");
    if (!content) throw new AcpError(-32602, "A nonempty prompt is required");
    if (validateSendContext) {
      const input = await requireSelectedContext(true);
      recheckSelectedContext(true, input);
    }
    selectedInput = null;
    const source = `agent-remote-driver-${randomUUID()}`;
    const completion = new Promise((resolve, reject) => {
      pending = {
        resolve, reject, started: false, accepted: false, idle: false,
        cancelled: false, cancelling: false,
        streamedMessages: new Set(), completeMessages: new Set(),
      };
    });
    const operation = pending;
    operation.completion = completion;
    sources.add(source);
    sending = false;
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
    if (pending || sending || interrupting) throw new AcpError(-32001, "A native prompt is already in flight");
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
    if (validateSendContext) {
      const input = await requireSelectedContext();
      recheckSelectedContext(false, input);
    }
    selectedInput = null;
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

  async function interrupt() {
    requireControl();
    requireStartup();
    if (sending || interrupting || pending?.cancelling) {
      throw new AcpError(-32001, "A native input operation is already in flight");
    }
    interrupting = true;
    report();
    let abortStarted = false;
    try {
      if (validateSendContext) await validateSendContext();
      recheckSelectedContext();
      const operation = pending;
      if (!operation && !nativeActive && sources.size === 0) return;
      if (operation) {
        operation.cancelled = true;
        operation.cancelling = true;
      }
      abortStarted = true;
      await session.abort();
      recheckSelectedContext();
      if (pending === operation && operation) {
        operation.cancelling = false;
        if (operation.idle && operation.accepted) finish();
      }
      // An ACP turn retains the input slot until both acceptance and idle settle.
      if (operation) await operation.completion;
      recheckSelectedContext();
    } catch {
      controlled = false;
      const error = new AcpError(-32001, abortStarted
        ? "Native cancellation outcome is unknown"
        : "Selected native execution context is unavailable; no interruption was dispatched");
      finish(error);
      throw error;
    } finally {
      interrupting = false;
      report();
    }
  }

  async function dispatch(method, params) {
    if (method === "_copilot-driver/input-status") method = "copilot-driver/input-status";
    if (method === "_copilot-driver/native-send") method = "copilot-driver/native-send";
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
      if (selectedInput) {
        selectedInput.cancelled = true;
        return {};
      }
      if (interrupting) return {};
      if (pending?.cancelling) return {};
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
      } else if (nativeActive || sources.size !== 0) await interrupt();
      return {};
    }
    throw new AcpError(-32601, "Operation is unsupported by this bound headed session");
  }

  return {
    sessionId,
    interrupt,
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
