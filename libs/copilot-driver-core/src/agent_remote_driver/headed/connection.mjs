import { createConnection } from "node:net";
import { setTimeout as delay } from "node:timers/promises";
import { createHeadedAdapter } from "./acp-adapter.mjs";

export function headedBinding(raw) {
  const binding = JSON.parse(raw || "null");
  if (!binding || binding.version !== 1 || !Number.isInteger(binding.port) ||
      binding.port < 1 || binding.port > 65535 || typeof binding.nonce !== "string" ||
      binding.nonce.length < 32 || typeof binding.sessionId !== "string" ||
      (binding.startupPrompt !== undefined && typeof binding.startupPrompt !== "string")) {
    throw new Error("Missing launch-bound Driver endpoint");
  }
  return binding;
}

export async function connectHeadedSession(session, binding, { validateSendContext = null } = {}) {
  if (session.sessionId !== binding.sessionId) throw new Error("Native session binding changed");
  const socket = createConnection({ host: "127.0.0.1", port: binding.port });
  await new Promise((resolve, reject) => {
    socket.once("connect", resolve);
    socket.once("error", reject);
  });
  const send = (message) => {
    if (socket.destroyed) return;
    if (socket.writableLength > 2_000_000) {
      socket.destroy();
      return;
    }
    socket.write(JSON.stringify(message) + "\n");
  };
  socket.write(JSON.stringify({
    contract: "copilot-driver.headed-sdk", version: 1,
    sessionId: session.sessionId, nonce: binding.nonce, extensionPid: process.pid,
    nativePid: process.ppid,
  }) + "\n");
  const adapter = createHeadedAdapter(session, {
    emit: send, cwd: process.cwd(), startupPrompt: binding.startupPrompt, validateSendContext,
    observeState: binding.authWait ? (state) => send({
      contract: "copilot-driver.headed-auth-state", version: 1,
      incarnation: binding.authWait.incarnation, state,
    }) : null,
  });
  let buffer = Buffer.alloc(0);
  let inFlight = 0;
  socket.on("data", (chunk) => {
    buffer = Buffer.concat([buffer, chunk]);
    while (!socket.destroyed) {
      const end = buffer.indexOf(10);
      if (end > 1_000_000 || (end < 0 && buffer.length > 1_000_000)) {
        socket.destroy();
        return;
      }
      if (end < 0) return;
      const line = buffer.subarray(0, end);
      buffer = buffer.subarray(end + 1);
      let request;
      try {
        request = JSON.parse(line.toString("utf8"));
      } catch {
        socket.destroy();
        return;
      }
      if (binding.authWait && request?.contract === "copilot-driver.headed-auth-freeze" &&
          request.version === 1 && request.incarnation === binding.authWait.incarnation) {
        adapter.authWait();
        continue;
      }
      if (++inFlight > 32) {
        socket.destroy();
        return;
      }
      adapter.handle(request).then((response) => {
        if (response) send(response);
      }).catch(() => socket.destroy()).finally(() => { inFlight--; });
    }
  });
  socket.on("error", () => {});
  socket.once("close", () => {
    adapter.close();
  });
  return { adapter, close: () => { socket.destroy(); adapter.close(); } };
}

export function headedDeliverySession(native, adapter) {
  const sessionId = native.sessionId;
  const unavailable = () => new Error("Headed delivery is unavailable; no replay or native fallback");
  async function readyForInput() {
    const deadline = Date.now() + 5_000;
    while (Date.now() < deadline) {
      if (native.sessionId !== sessionId) throw unavailable();
      const response = await adapter.handle({
        jsonrpc: "2.0", id: 1, method: "copilot-driver/input-status", params: { sessionId },
      });
      if (response?.result?.sessionId !== sessionId) throw unavailable();
      if (response.result.nativeSend === "ready") return;
      if (response.result.nativeSend !== "pending") throw unavailable();
      await delay(Math.min(20, Math.max(0, deadline - Date.now())));
    }
    throw new Error("Native startup input is not ready; no prompt was dispatched");
  }
  return {
    get sessionId() { return native.sessionId; },
    rpc: native.rpc,
    getEvents: (...args) => native.getEvents(...args),
    async send(options) {
      if (!options || typeof options.prompt !== "string" || !options.prompt ||
          ![undefined, "enqueue", "immediate"].includes(options.mode) ||
          Object.keys(options).some((key) => !["prompt", "mode", "source", "displayPrompt"].includes(key))) {
        throw new Error("Unsupported headed delivery options");
      }
      await readyForInput();
      if (native.sessionId !== sessionId) throw unavailable();
      const response = await adapter.handle({
        jsonrpc: "2.0", id: 1, method: "copilot-driver/native-send",
        params: {
          sessionId, prompt: options.prompt, mode: options.mode ?? "enqueue",
          ...(options.displayPrompt === undefined ? {} : { displayPrompt: options.displayPrompt }),
        },
      });
      if (native.sessionId !== sessionId || response?.error ||
          typeof response?.result?.messageId !== "string" || !response.result.messageId) throw unavailable();
      return response.result.messageId;
    },
    async abort() { throw new Error("Retained headed delivery does not authorize abort"); },
  };
}
