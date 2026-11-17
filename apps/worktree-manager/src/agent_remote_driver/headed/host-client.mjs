import { randomUUID } from "node:crypto";
import { open } from "node:fs/promises";
import { createConnection } from "node:net";
import { isAbsolute } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const MAX_FRAME = 1_000_128;
const unavailable = () => new Error("Original headed broker is unavailable; no replay or native fallback");

export function createHeadedSession(native, { binding, cwd, nativePid }) {
  if (binding?.contract !== "copilot-driver.headed-client" || binding.version !== 1 ||
      typeof binding.stateFile !== "string" || !isAbsolute(binding.stateFile) ||
      binding.sessionId !== native.sessionId || binding.cwd !== cwd ||
      typeof binding.nonce !== "string" || binding.nonce.length < 32 ||
      !Number.isSafeInteger(binding.hostPid) || binding.hostPid < 1 ||
      !Number.isSafeInteger(nativePid) || nativePid < 1) {
    throw new Error("Headed delivery requires its original launch binding");
  }
  let connection = null;
  let socket = null;
  let held = null;
  let closed = false;
  let buffer = Buffer.alloc(0);
  let helloResolve;
  let helloReject;
  let helloDone = false;
  let inputReadiness = false;
  const pending = new Map();

  function fail() {
    held ??= unavailable();
    helloReject?.(held);
    for (const operation of pending.values()) operation.reject(held);
    pending.clear();
    socket?.destroy();
  }

  function write(type, payload) {
    if (closed || held || !socket || socket.destroyed || socket.writableLength > MAX_FRAME) {
      throw unavailable();
    }
    const body = Buffer.concat([Buffer.from(type), payload]);
    if (body.length > MAX_FRAME) throw new Error("Headed request exceeds its byte bound");
    const header = Buffer.alloc(4);
    header.writeUInt32BE(body.length);
    socket.write(Buffer.concat([header, body]));
  }

  function receive(chunk) {
    buffer = Buffer.concat([buffer, chunk]);
    try {
      while (buffer.length >= 4) {
        const length = buffer.readUInt32BE();
        if (length < 1 || length > MAX_FRAME) throw unavailable();
        if (buffer.length < length + 4) return;
        const frame = buffer.subarray(4, length + 4);
        buffer = buffer.subarray(length + 4);
        const type = frame[0];
        const payload = frame.subarray(1);
        if (!helloDone) {
          // A launch-bound automatic client never inherits a prior controller's
          // buffered RPCs or reclaims its unknown delivery.
          if (type !== 72 || payload.length < 16 || payload.readBigUInt64BE() !== 0n ||
              payload.readBigUInt64BE(8) !== BigInt(nativePid)) throw unavailable();
          helloDone = true;
          helloResolve();
        } else if (type === 70) {
          if (payload.length < 9) throw unavailable();
          const message = JSON.parse(payload.subarray(8).toString("utf8"));
          if (message?.jsonrpc !== "2.0") throw unavailable();
          const operation = pending.get(message.id);
          if (operation) {
            if (!message.error && !Object.hasOwn(message, "result")) throw unavailable();
            pending.delete(message.id);
            if (message.error) operation.reject(unavailable());
            else operation.resolve(message.result);
          }
          write("K", payload.subarray(0, 8));
        } else if (type === 76 && payload[0] !== 1) {
          throw unavailable();
        }
      }
    } catch {
      fail();
    }
  }

  async function request(method, params, timeout = 8_000) {
    if (closed || held || !helloDone || pending.size >= 4) throw unavailable();
    const id = randomUUID();
    let timer;
    const reply = new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      timer = setTimeout(fail, timeout);
      try {
        write("W", Buffer.from(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n"));
      } catch {
        fail();
      }
    });
    try {
      return await reply;
    } finally {
      clearTimeout(timer);
    }
  }

  async function connect() {
    const deadline = Date.now() + 5_000;
    let state;
    while (!closed && Date.now() < deadline) {
      let file;
      try {
        file = await open(binding.stateFile, "r");
        const info = await file.stat();
        if (!info.isFile() || info.size > 64 * 1024) throw unavailable();
        const bytes = Buffer.alloc(64 * 1024 + 1);
        const { bytesRead } = await file.read(bytes, 0, bytes.length, 0);
        if (bytesRead > 64 * 1024) throw unavailable();
        state = JSON.parse(bytes.subarray(0, bytesRead).toString("utf8"));
      } catch (error) {
        if (error?.code !== "ENOENT") throw error;
      } finally {
        await file?.close();
      }
      if (state?.state === "native_starting" || state === undefined) {
        await delay(20);
        continue;
      }
      break;
    }
    if (closed || state?.mode !== "headed" || state.version !== 2 ||
        state.state !== "running" || state.lifetime_policy !== "cli" ||
        state.session_id !== binding.sessionId || state.nonce !== binding.nonce ||
        state.host_pid !== binding.hostPid || state.child_pid !== nativePid ||
        state.cwd !== cwd || state.protocol_version !== 1 ||
        !Number.isInteger(state.port) || state.port < 1 || state.port > 65535 ||
        !Array.isArray(state.headed_client) || !state.headed_client.includes("native-send-v1")) {
      throw unavailable();
    }
    const hello = new Promise((resolve, reject) => { helloResolve = resolve; helloReject = reject; });
    const timer = setTimeout(fail, 5_000);
    socket = createConnection({ host: "127.0.0.1", port: state.port });
    socket.on("data", receive);
    socket.on("error", fail);
    socket.on("close", fail);
    socket.once("connect", () => {
      try { write("A", Buffer.concat([Buffer.alloc(8), Buffer.from(binding.nonce)])); }
      catch { fail(); }
    });
    try { await hello; } finally { clearTimeout(timer); }
    const initialized = await request("initialize", { protocolVersion: 1 });
    const support = initialized?._meta?.nativeSend;
    if (initialized?.protocolVersion !== 1 || initialized?._meta?.nativeSessionId !== native.sessionId ||
        support?.version !== 1 || support.receipt !== "native-message-id" ||
        !Array.isArray(support.modes) ||
        !["enqueue", "immediate"].every((mode) => support.modes.includes(mode))) {
      throw unavailable();
    }
    const readiness = initialized._meta.inputReadiness;
    if (readiness !== undefined) {
      if (readiness?.version !== 1 || readiness.method !== "copilot-driver/input-status") {
        throw unavailable();
      }
      inputReadiness = true;
    }
  }

  function requireBinding() {
    if (closed || held || native.sessionId !== binding.sessionId) {
      fail();
      throw unavailable();
    }
  }

  async function controlled() {
    requireBinding();
    connection ??= connect().catch(() => { fail(); throw unavailable(); });
    try { await connection; } finally { requireBinding(); }
  }

  async function readyForInput() {
    if (!inputReadiness) return;
    const deadline = Date.now() + 5_000;
    while (Date.now() < deadline) {
      const state = await request(
        "copilot-driver/input-status", { sessionId: binding.sessionId }, deadline - Date.now(),
      );
      if (state?.sessionId !== binding.sessionId ||
          !["pending", "observed", "not-requested"].includes(state.startupSeed) ||
          !["pending", "active", "idle-observed", "not-requested"].includes(state.startupTurn) ||
          !["ready", "pending", "busy", "unavailable"].includes(state.prompt) ||
          !["ready", "pending", "busy", "unavailable"].includes(state.nativeSend) ||
          (state.startupSeed === "not-requested") !== (state.startupTurn === "not-requested") ||
          (state.startupSeed === "pending" && state.startupTurn !== "pending") ||
          (state.startupSeed === "observed" && !["active", "idle-observed"].includes(state.startupTurn)) ||
          (state.nativeSend === "ready" && state.startupSeed === "pending") ||
          (state.prompt === "ready" && ["pending", "active"].includes(state.startupTurn))) {
        fail();
        throw unavailable();
      }
      if (state.nativeSend === "ready") return;
      if (state.nativeSend !== "pending") {
        fail();
        throw unavailable();
      }
      await delay(Math.min(20, Math.max(0, deadline - Date.now())));
    }
    throw new Error("Native startup input is not ready; no prompt was dispatched");
  }

  return {
    get sessionId() { return native.sessionId; },
    rpc: {
      metadata: { snapshot: (...args) => native.rpc.metadata.snapshot(...args) },
      plugins: { list: (...args) => native.rpc.plugins.list(...args) },
    },
    getEvents: (...args) => native.getEvents(...args),
    async send(options) {
      if (!options || typeof options.prompt !== "string" || !options.prompt ||
          ![undefined, "enqueue", "immediate"].includes(options.mode) ||
          Object.keys(options).some((key) => !["prompt", "mode", "source", "displayPrompt"].includes(key))) {
        throw new Error("Unsupported headed delivery options");
      }
      await controlled();
      try { await readyForInput(); } finally { requireBinding(); }
      let result;
      try {
        result = await request("copilot-driver/native-send", {
          sessionId: binding.sessionId, prompt: options.prompt, mode: options.mode ?? "enqueue",
          ...(options.displayPrompt === undefined ? {} : { displayPrompt: options.displayPrompt }),
        });
        requireBinding();
      } catch {
        fail();
        throw unavailable();
      }
      if (typeof result?.messageId !== "string" || !result.messageId) {
        fail();
        throw unavailable();
      }
      return result.messageId;
    },
    async abort() { throw new Error("Retained headed delivery does not authorize abort"); },
    close() { closed = true; fail(); },
  };
}
