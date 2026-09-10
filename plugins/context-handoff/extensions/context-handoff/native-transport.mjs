import { fork } from "node:child_process";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";

export function encodeRpcFrame(message) {
  const body = JSON.stringify(message);
  return Buffer.from(`Content-Length: ${Buffer.byteLength(body)}\r\n\r\n${body}`);
}

export function rpcFrameReader(onMessage) {
  let buffer = Buffer.alloc(0);
  return chunk => {
    buffer = Buffer.concat([buffer, chunk]);
    while (true) {
      const boundary = buffer.indexOf("\r\n\r\n");
      if (boundary < 0) return;
      const header = buffer.subarray(0, boundary).toString("ascii");
      const match = /^Content-Length:\s*(\d+)$/im.exec(header);
      if (!match) throw new Error("Invalid extension JSON-RPC frame.");
      const end = boundary + 4 + Number(match[1]);
      if (buffer.length < end) return;
      const message = JSON.parse(buffer.subarray(boundary + 4, end).toString("utf8"));
      buffer = buffer.subarray(end);
      onMessage(message);
    }
  };
}

// The published runtime schema has getState but this SDK has no typed wrapper.
// One transport owner combines complete frames from the SDK and the additional
// schema request, so headers/bodies cannot interleave under stream backpressure.
export function runNativeBridge(entryUrl) {
  const child = fork(fileURLToPath(entryUrl), [], {
    execPath: "node",
    execArgv: [],
    stdio: ["pipe", "pipe", "inherit", "ipc"],
    env: { ...process.env, CONTEXT_HANDOFF_NATIVE_WORKER: "1" },
  });
  const nativeRequests = new Set();
  const readHost = rpcFrameReader(message => {
    if (nativeRequests.delete(message.id)) {
      child.send({ kind: "native-goal-result", ...message });
    } else {
      child.stdin.write(encodeRpcFrame(message));
    }
  });
  const readSdk = rpcFrameReader(message => process.stdout.write(encodeRpcFrame(message)));
  process.stdin.on("data", readHost);
  child.stdout.on("data", readSdk);
  child.on("message", message => {
    if (message.kind !== "native-goal-read") return;
    nativeRequests.add(message.id);
    process.stdout.write(encodeRpcFrame({
      jsonrpc: "2.0", id: message.id,
      method: "session.autopilotObjective.getState",
      params: { sessionId: message.sessionId },
    }));
  });
  const terminate = () => child.kill("SIGTERM");
  process.once("exit", terminate);
  process.on("SIGTERM", terminate);
  process.on("SIGINT", terminate);
  return new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      process.stdin.off("data", readHost);
      process.off("SIGTERM", terminate);
      process.off("SIGINT", terminate);
      process.off("exit", terminate);
      resolve(code ?? (signal ? 1 : 0));
    });
  });
}

export function readNativeGoal(sessionId) {
  if (!process.send) {
    return Promise.reject(new Error("Native goal transport is unavailable; handoff must preserve its source."));
  }
  const id = `context-handoff-native:${randomUUID()}`;
  return new Promise((resolve, reject) => {
    const finish = () => {
      clearTimeout(timer);
      process.off("message", receive);
    };
    const receive = message => {
      if (message.kind !== "native-goal-result" || message.id !== id) return;
      finish();
      if (message.error) {
        reject(new Error(`Native objective API unavailable (${message.error.code}): ${message.error.message}`));
      } else {
        resolve(message.result);
      }
    };
    const timer = setTimeout(() => {
      finish();
      reject(new Error("Native objective API timed out; source is preserved."));
    }, 15000);
    process.on("message", receive);
    process.send({ kind: "native-goal-read", id, sessionId }, error => {
      if (error) {
        finish();
        reject(error);
      }
    });
  });
}
