import { lstatSync, readFileSync } from "node:fs";
import { createConnection } from "node:net";
import { isAbsolute, join, resolve } from "node:path";

function document(path, limit = 16384) {
  const info = lstatSync(path);
  if (!info.isFile() || info.isSymbolicLink() || info.size > limit) return null;
  const bytes = readFileSync(path);
  return bytes.length <= limit ? bytes.toString("utf8") : null;
}

function endpoint(sessionDirectory) {
  const text = document(join(sessionDirectory, "instructions", "agent-worktrees",
    "session-guidance.instructions.md"));
  const section = text?.split("## agent-worktrees session command catalog")[1];
  const value = JSON.parse(section?.match(/```json\s*\n([\s\S]*?)\n```/)?.[1] || "null");
  if (value?.schema !== "copilot-extensions.session-command-catalog" ||
      value.version !== 1 || value.plugin !== "agent-worktrees" ||
      value.binding?.status !== "selected") return null;
  const control = value.residentControl;
  const installation = value.binding.installation;
  if (control?.version !== 1 || control.pluginId !== "agent-worktrees" ||
      !isAbsolute(control.runtimeRoot || "") ||
      resolve(control.runtimeRoot) !== resolve(installation?.runtimeRoot || "") ||
      !["installationId", "sourceFingerprint", "pluginId"]
        .every(key => typeof control[key] === "string" && control[key] &&
          control[key] === installation?.[key]) ||
      typeof control.marketplaceId !== "string" || !control.marketplaceId ||
      control.sourceFingerprint !== value.binding.source?.fingerprint ||
      control.marketplaceId !== value.binding.source?.marketplaceId ||
      control.pluginId !== value.binding.source?.pluginId) return null;
  const peer = JSON.parse(document(join(control.runtimeRoot, "status-monitor.lock")) || "null");
  if (peer?.hook_transport !== "tcp" ||
      !Array.isArray(peer.hook_capabilities) ||
      !peer.hook_capabilities.includes("worktree-change-v1") ||
      typeof peer.hook_generation !== "string" || !peer.hook_generation ||
      typeof peer.hook_token !== "string" || !peer.hook_token) return null;
  const address = peer.hook_endpoint?.match(/^127\.0\.0\.1:([0-9]+)$/);
  const port = Number(address?.[1]);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return null;
  return { peer, port };
}

export async function notifyWorktreeChanged({ sessionDirectory, cwd }) {
  // Only the admitted session's selected installation; never boot or rediscover a service.
  try {
    if (!isAbsolute(sessionDirectory) || !isAbsolute(cwd) ||
        cwd.includes("\0") || Buffer.byteLength(cwd) > 4096) return false;
    const selected = endpoint(sessionDirectory);
    if (!selected) return false;
    const { peer, port } = selected;
    const frame = JSON.stringify({
      version: 1, token: peer.hook_token, kind: "worktreeChanged",
      hook_generation: peer.hook_generation, deadline: Date.now() / 1000 + 1,
      payload: { cwd },
    }) + "\n";
    if (Buffer.byteLength(frame) > 65536) return false;
    return await new Promise(resolveResult => {
      const socket = createConnection({ host: "127.0.0.1", port });
      let settled = false;
      const finish = result => {
        if (settled) return;
        settled = true;
        socket.destroy();
        resolveResult(result);
      };
      socket.setTimeout(100, () => finish(false));
      socket.on("error", () => finish(false));
      socket.on("close", () => finish(false));
      socket.on("connect", () => socket.end(frame, () => finish(true)));
    });
  } catch {
    return false;
  }
}
