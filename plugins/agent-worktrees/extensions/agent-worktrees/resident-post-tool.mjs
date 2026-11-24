import { execFile } from "node:child_process";
import { closeSync, lstatSync, openSync, readSync, statSync } from "node:fs";
import { connect } from "node:net";
import { homedir } from "node:os";
import { isAbsolute, join, win32 } from "node:path";
import { createManagedSessionAdmission, resolveInterpreter } from "./managed-session.mjs";

// Mirrors scripts/post-tool-hook.ps1 (Invoke-ResidentPostTool).
const FORWARDED = ["AGENT_WORKTREES_NUDGE", "AGENT_WORKTREES_AGENDA_NUDGE",
  "AGENT_WORKTREES_NUDGE_CALLS", "AGENT_WORKTREES_NUDGE_MINUTES",
  "AGENT_WORKTREES_STATELESS_GUARD", "CROSS_REPO_GUARD", "CROSS_REPO_GUARD_MODE",
  "ANCHOR_WRITE_GUARD", "ANCHOR_WRITE_GUARD_MODE", "PR_SUPERSEDE_GUARD", "PR_SUPERSEDE_GUARD_MODE"];
const CAPABILITY = "native-tool-policy-v1";
const BUDGET_MS = 500;
const LIMIT = 65536;
const LOCK_LIMIT = 16384;
// The POSIX command entry has a 10 s host budget; keep its client inside it.
const POSIX_TIMEOUT_MS = 9000;
export const UNAVAILABLE = "selected native resident unavailable; advisory/accounting completion is unconfirmed; no local fallback or retry was run";
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);

function linked(path) {
  try { return lstatSync(path).isSymbolicLink(); }
  catch (error) {
    if (error?.code === "ENOENT" || error?.code === "ENOTDIR") return false;
    throw error;
  }
}

// Mirrors Get-PreparedWorktreesPath: absolute drive path with no linked ancestor.
export function preparedPath(path) {
  if (typeof path !== "string" || path.length > 4096 || !/^[A-Za-z]:[\\/]/.test(path) || path.includes("\0")) {
    throw new Error("unsupported-path");
  }
  let full = win32.resolve(path);
  if (full.length > 3) full = full.replace(/\\+$/, "");
  for (let ancestor = full, depth = 0; ; depth++) {
    if (depth >= 128) throw new Error("path-bound");
    if (linked(ancestor)) throw new Error("linked-path");
    const parent = win32.dirname(ancestor);
    if (parent === ancestor) break;
    ancestor = parent;
  }
  return full.toLowerCase();
}

// Mirrors Get-PostToolRuntimeRoot.
export function runtimeRoot(environment) {
  const pointer = environment.COPILOT_EXTENSIONS_CONTEXT;
  if (pointer) {
    preparedPath(pointer);
    if (win32.basename(pointer) !== "install.json") throw new Error("invalid-context-pointer");
    const runtime = win32.dirname(win32.resolve(pointer));
    if (win32.basename(runtime) !== "agent-worktrees" ||
        win32.basename(win32.dirname(runtime)) !== "plugins") throw new Error("invalid-context-layout");
    for (let ancestor = pointer, depth = 0; ; depth++) {
      if (depth >= 128) throw new Error("path-bound");
      if (lstatSync(ancestor).isSymbolicLink()) throw new Error("linked-context-path");
      const parent = win32.dirname(ancestor);
      if (parent === ancestor) break;
      ancestor = parent;
    }
    preparedPath(runtime);
    return runtime;
  }
  const home = environment.AGENT_HOME?.trim() || environment.USERPROFILE;
  const runtime = win32.join(home, ".agent-worktrees");
  preparedPath(runtime);
  return runtime;
}

// Mirrors Test-PostToolDiscoveryContext.
export function discoveryMatches(discovery, runtime, environment) {
  if (!object(discovery)) return false;
  const pointer = environment.COPILOT_EXTENSIONS_CONTEXT;
  if (!pointer) return !(discovery.installReceipt || discovery.marketplaceId || discovery.pluginRoot);
  try {
    const cell = win32.dirname(win32.dirname(runtime));
    return typeof discovery.marketplaceId === "string" &&
      discovery.marketplaceId === win32.basename(cell) &&
      preparedPath(discovery.installReceipt) === preparedPath(pointer) &&
      preparedPath(discovery.pluginRoot) === preparedPath(runtime);
  } catch { return false; }
}

function readBoundedJson(path, limit) {
  const descriptor = openSync(path, "r");
  try {
    const buffer = Buffer.alloc(limit + 1);
    let count = 0;
    while (count < buffer.length) {
      const read = readSync(descriptor, buffer, count, buffer.length - count, null);
      if (read === 0) break;
      count += read;
    }
    if (count > limit) return null;
    const value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(buffer.subarray(0, count)));
    return object(value) ? value : null;
  } finally { closeSync(descriptor); }
}

export function readSelectedEndpoint(environment) {
  const runtime = runtimeRoot(environment);
  const endpoint = readBoundedJson(win32.join(runtime, "status-monitor.lock"), LOCK_LIMIT);
  if (endpoint === null) throw new Error("rendezvous-unavailable");
  if (!discoveryMatches(endpoint, runtime, environment)) throw new Error("installation-mismatch");
  return endpoint;
}

export function residentPayload(input, environment) {
  const payload = { ...input };
  if (!("sessionId" in payload)) payload.sessionId = environment.COPILOT_AGENT_SESSION_ID ?? null;
  if (!("cwd" in payload)) {
    payload.cwd = "workingDirectory" in payload ? payload.workingDirectory : environment.COPILOT_PROJECT_DIR ?? null;
  }
  // Accounting and target invalidation consume the invocation, never its output.
  delete payload.toolResult;
  delete payload.tool_result;
  payload._agentWorktrees = {
    nativePolicy: 1,
    environment: Object.fromEntries(FORWARDED.map(key => [key, environment[key] ?? null])),
  };
  return payload;
}

export function requestResident({ endpoint, payload, budgetMs = BUDGET_MS, now = () => performance.now() }) {
  const started = now();
  const match = /^127\.0\.0\.1:([0-9]+)$/.exec(typeof endpoint.hook_endpoint === "string" ? endpoint.hook_endpoint : "");
  const port = match ? Number(match[1]) : 0;
  if (endpoint.hook_transport !== "tcp" || !match || !Number.isSafeInteger(port) || port < 1 || port > 65535 ||
      typeof endpoint.hook_token !== "string" || !endpoint.hook_token ||
      typeof endpoint.hook_generation !== "string" || !endpoint.hook_generation ||
      !Array.isArray(endpoint.hook_capabilities) || !endpoint.hook_capabilities.includes(CAPABILITY)) {
    return Promise.resolve(null);
  }
  const frame = Buffer.from(JSON.stringify({
    version: 1, token: endpoint.hook_token, kind: "postToolUse",
    hook_generation: endpoint.hook_generation, payload,
    deadline: Date.now() / 1000 + Math.max(0, budgetMs - (now() - started)) / 1000,
  }) + "\n", "utf8");
  if (frame.length > LIMIT) return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    let settled = false;
    const socket = connect({ host: "127.0.0.1", port });
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket.destroy();
      if (error) reject(error); else resolve(value);
    };
    const timer = setTimeout(() => finish(null, null), Math.max(0, budgetMs - (now() - started)));
    timer.unref?.();
    socket.on("connect", () => socket.write(frame));
    socket.on("error", error => finish(error));
    socket.on("end", () => finish(null, null));
    socket.on("data", chunk => {
      const newline = chunk.indexOf(10);
      const part = chunk.subarray(0, Math.min(newline < 0 ? chunk.length : newline, LIMIT - size));
      chunks.push(part);
      size += part.length;
      if (newline < 0) {
        if (size >= LIMIT) finish(null, null);
        return;
      }
      let reply;
      try { reply = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks))); }
      catch (error) { return finish(error); }
      const valid = object(reply) && reply.version === 1 && reply.fallback !== true &&
        Array.isArray(reply.capabilities) && reply.capabilities.includes(CAPABILITY) &&
        object(reply.result) &&
        (!("additionalContext" in reply.result) || typeof reply.result.additionalContext === "string");
      finish(null, valid && now() - started <= budgetMs ? reply.result : null);
    });
  });
}

export function createPosixPostTool({ scriptsRoot, environment = process.env }) {
  return payload => new Promise((resolve, reject) => {
    const python = resolveInterpreter(undefined, environment);
    const script = join(scriptsRoot, "hook_client.py");
    try { if (!python || !statSync(script).isFile()) throw new Error(); }
    catch { return reject(new Error("hook-client-unavailable")); }
    const child = execFile(python, [script, "postToolUse"], {
      windowsHide: true, cwd: homedir(), env: { ...environment, PYTHONDONTWRITEBYTECODE: "1" },
      encoding: "utf8", timeout: POSIX_TIMEOUT_MS, maxBuffer: LIMIT,
    }, (error, stdout, stderr) => {
      if (stderr) process.stderr.write(stderr);
      if (error) return reject(new Error("hook-client-failed"));
      try {
        const result = stdout.trim() ? JSON.parse(stdout) : {};
        resolve(object(result) ? result : {});
      } catch { reject(new Error("hook-client-failed")); }
    });
    child.stdin?.on("error", () => {});
    child.stdin?.end(JSON.stringify(payload), "utf8");
  });
}

export function createResidentPostToolHook({
  scriptsRoot, environment = process.env, platform = process.platform,
  admit = createManagedSessionAdmission({ scriptsRoot, environment }),
  readEndpoint = () => readSelectedEndpoint(environment),
  request = requestResident,
  posix = createPosixPostTool({ scriptsRoot, environment }),
  report = reason => console.error(`agent-worktrees postToolUse: ${reason}`),
}) {
  const reported = new Set();
  const once = reason => {
    if (reported.has(reason)) return;
    reported.add(reason);
    try { report(reason); } catch { /* Diagnostics never fail the hook. */ }
  };
  return async input => {
    if (!object(input) || typeof input.workingDirectory !== "string" ||
        !isAbsolute(input.workingDirectory)) return {};
    try {
      if (!await admit(input)) return {};
    } catch { return {}; }
    if (platform !== "win32") {
      try {
        // SDK uses workingDirectory; the command payload contract uses cwd.
        return await posix({ ...input, cwd: input.workingDirectory });
      } catch (error) {
        once(error?.message === "hook-client-unavailable" ? "hook client unavailable" : "hook client failed");
        return {};
      }
    }
    try {
      const result = await request({ endpoint: readEndpoint(), payload: residentPayload(input, environment) });
      if (result) return result;
    } catch {
      // A timed-out request may have been delivered; never replay it.
    }
    once(UNAVAILABLE);
    return {};
  };
}

export function selectResidentPostToolHooks({
  environment = process.env, createHook,
  report = reason => console.error(`agent-worktrees post-tool selection: ${reason}`),
}) {
  const mode = environment.AGENT_WORKTREES_POST_TOOL_IMPLEMENTATION || "command";
  if (mode === "command") return undefined;
  if (mode !== "resident") {
    try { report("invalid-implementation; command hook retained"); } catch { /* best effort */ }
    return undefined;
  }
  try {
    const hook = createHook();
    if (typeof hook !== "function") throw new Error("resident-hook-unavailable");
    return { onPostToolUse: hook };
  } catch {
    try { report("resident-hook-unavailable; select command mode"); } catch { /* best effort */ }
    return { onPostToolUse: async () => ({}) };
  }
}
