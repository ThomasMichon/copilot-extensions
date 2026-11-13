import { createConnection } from "node:net";
import { joinSession } from "@github/copilot-sdk/extension";
import { createHeadedAdapter } from "./acp-adapter.mjs";

const binding = JSON.parse(process.env.COPILOT_DRIVER_HEADED_BINDING || "null");
delete process.env.COPILOT_DRIVER_HEADED_BINDING;
if (!binding || binding.version !== 1 || !Number.isInteger(binding.port) ||
    binding.port < 1 || binding.port > 65535 || typeof binding.nonce !== "string" ||
    binding.nonce.length < 32 || typeof binding.sessionId !== "string" ||
    (binding.startupPrompt !== undefined && typeof binding.startupPrompt !== "string")) {
  throw new Error("Missing launch-bound Driver endpoint");
}

// No tools, permission callback, hooks or environment-access declarations.
const session = await joinSession();
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
  emit: send, cwd: process.cwd(), startupPrompt: binding.startupPrompt,
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
