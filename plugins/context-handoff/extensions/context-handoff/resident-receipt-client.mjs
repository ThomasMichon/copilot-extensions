import { lstat, open } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { createConnection } from "node:net";
import { PROMPT_RECEIPT_TIMEOUT_MS } from "./cli-timeouts.mjs";

export const OBSERVATION_MS = PROMPT_RECEIPT_TIMEOUT_MS;
export const FRAME_BYTES = 8192;
export const SETTLEMENT_RESERVE_MS = 100;
const GUIDANCE_BYTES = 65536;
const CAPABILITY = "handoff-prompt-receipt-v1";
const IDENTITY = ["installationId", "sourceFingerprint", "marketplaceId", "pluginId",
  "installGeneration", "activationGeneration"];

function refuse(reason) { throw new Error(reason); }
function object(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
function text(value, limit = 4096) {
  return typeof value === "string" && value.length > 0 && value.length <= limit
    && !/[\u0000-\u001f\u007f]/u.test(value);
}
function same(a, b) {
  return object(a) && object(b) && Object.keys(a).length === Object.keys(b).length
    && Object.keys(a).every((key) => a[key] === b[key]);
}

export function parseUniqueJson(raw) {
  const stack = [];
  const tokens = /"(?:\\.|[^"\\])*"|[{}\[\]]/g;
  for (const match of raw.matchAll(tokens)) {
    const token = match[0];
    if (token === "{" || token === "[") {
      if (stack.length >= 32) refuse("json-depth");
      stack.push(token === "{" ? new Set() : null);
    } else if (token === "}" || token === "]") stack.pop();
    else if (/^\s*:/u.test(raw.slice(match.index + token.length))) {
      const keys = stack.at(-1);
      if (keys) {
        const key = JSON.parse(token);
        if (keys.has(key)) refuse("duplicate-field");
        keys.add(key);
      }
    }
  }
  return JSON.parse(raw);
}

async function readBounded(path, limit, check) {
  if (!isAbsolute(path) || resolve(path) !== path) refuse("invalid-artifact-path");
  let cursor = path;
  while (true) {
    check();
    const info = await lstat(cursor);
    check();
    if (info.isSymbolicLink()) refuse("linked-artifact");
    if (cursor === path && (!info.isFile() || info.size > limit || info.nlink !== 1)) refuse("invalid-artifact");
    const parent = dirname(cursor);
    if (parent === cursor) break;
    cursor = parent;
  }
  const before = await lstat(path);
  check();
  const handle = await open(path, "r");
  try {
    const current = await handle.stat();
    check();
    if (!current.isFile() || current.nlink !== 1 || before.dev !== current.dev || before.ino !== current.ino) {
      refuse("artifact-changed");
    }
    const buffer = Buffer.alloc(limit + 1);
    let length = 0;
    while (length <= limit) {
      check();
      const result = await handle.read(buffer, length, buffer.length - length, length);
      check();
      length += result.bytesRead;
      if (!result.bytesRead) break;
    }
    if (length > limit) refuse("artifact-too-large");
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer.subarray(0, length));
  } finally { await handle.close(); }
}

function beneath(root, path) {
  if (!text(root) || !text(path) || !isAbsolute(root) || !isAbsolute(path)) return false;
  const rel = relative(root, path);
  return rel !== "" && !isAbsolute(rel) && rel !== ".." && !rel.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`);
}

function candidateFromGuidance(raw) {
  const catalogs = [];
  for (const match of raw.matchAll(/```json\s*\r?\n([\s\S]*?)\r?\n```/g)) {
    const value = parseUniqueJson(match[1]);
    if (value?.schema === "copilot-extensions.session-command-catalog"
        && value.plugin === "agent-worktrees") catalogs.push(value);
  }
  if (catalogs.length !== 1) refuse("catalog-unavailable");
  const catalog = catalogs[0];
  const binding = catalog.binding;
  const candidate = catalog.residentControl;
  if (catalog.version !== 1 || !object(binding) || binding.status !== "selected"
      || binding.schema !== "copilot-extensions.command-installation-binding"
      || binding.version !== 1 || !object(binding.source) || !object(binding.installation)
      || !object(candidate) || candidate.version !== 1 || candidate.state !== "candidate"
      || candidate.pluginId !== "agent-worktrees"
      || binding.source.pluginId !== "agent-worktrees"
      || candidate.sourceFingerprint !== binding.source.fingerprint
      || candidate.marketplaceId !== binding.source.marketplaceId
      || candidate.runtimeRoot !== binding.installation.runtimeRoot) refuse("binding-unavailable");
  const fields = new Set(["version", "state", ...IDENTITY, "runtimeRoot", "activeRoutePath", "tokenPath"]);
  if (Object.keys(candidate).some((key) => !fields.has(key))) refuse("invalid-candidate");
  for (const key of IDENTITY) {
    const value = candidate[key];
    if (key === "marketplaceId") {
      if (!text(value, 256)) refuse("binding-unavailable");
    } else if (value !== binding.installation[key]) refuse("binding-mismatch");
    if (key.endsWith("Generation")) {
      if (value !== undefined && (!Number.isSafeInteger(value) || value < 1)) refuse("invalid-generation");
    } else if (!text(value)) refuse("binding-unavailable");
  }
  if (!beneath(candidate.runtimeRoot, candidate.activeRoutePath)
      || !beneath(candidate.runtimeRoot, candidate.tokenPath)) refuse("foreign-artifact-path");
  return candidate;
}

function installation(candidate) {
  return Object.fromEntries(IDENTITY.filter((key) => candidate[key] !== undefined)
    .map((key) => [key, candidate[key]]));
}

function stableInstallation(value) {
  if (!object(value)) return null;
  return Object.fromEntries(Object.entries(value)
    .filter(([key]) => key !== "installGeneration" && key !== "activationGeneration"));
}

function activeRoute(raw) {
  const route = parseUniqueJson(raw)?.active;
  if (!object(route) || route.bind !== "127.0.0.1"
      || !Number.isSafeInteger(route.port) || route.port < 1 || route.port > 65535
      || !Number.isSafeInteger(route.pid) || route.pid < 1
      || !Number.isSafeInteger(route.generation) || route.generation < 1
      || !text(route.process_start_time, 256)) refuse("active-route-unavailable");
  return {
    port: route.port, pid: route.pid, routeGeneration: route.generation,
    processStartTime: route.process_start_time,
  };
}

function request(route, token, action, payload, signal, check, sent) {
  check();
  const bytes = Buffer.from(JSON.stringify({ version: 1, token, action, payload }) + "\n");
  if (bytes.length > FRAME_BYTES) refuse("request-too-large");
  return new Promise((resolveResult, reject) => {
    let length = 0;
    let chunks = [];
    let finished = false;
    const socket = createConnection({ host: "127.0.0.1", port: route.port });
    const finish = (error, value) => {
      if (finished) return;
      finished = true;
      signal.removeEventListener("abort", abort);
      socket.destroy();
      chunks = [];
      if (error) reject(error); else resolveResult(value);
    };
    const abort = () => finish(new Error("observation-cancelled"));
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
    socket.once("connect", () => {
      try { check(); sent?.(); socket.write(bytes); }
      catch { finish(new Error("observation-cancelled")); }
    });
    socket.on("data", (chunk) => {
      length += chunk.length;
      if (length > FRAME_BYTES) return finish(new Error("response-too-large"));
      chunks.push(chunk);
      if (!chunk.includes(10)) return;
      try {
        check();
        const raw = new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks));
        if (!raw.endsWith("\n") || raw.indexOf("\n") !== raw.length - 1) refuse("invalid-frame");
        const response = parseUniqueJson(raw);
        if (response?.version !== 1 || !object(response.result)) refuse("invalid-response");
        finish(null, response.result);
      } catch { finish(new Error("invalid-response")); }
    });
    socket.once("error", () => finish(new Error("transport-unavailable")));
    socket.once("end", () => finish(new Error("transport-ended")));
  });
}

function subjectFromBinding(binding, sid, locator) {
  const subject = {
    project: binding.project, worktreeId: binding.worktreeId,
    creationNonce: binding.creationNonce, sessionId: binding.sessionId, locator,
  };
  if (subject.sessionId !== sid || Object.values(subject).some((value) => !text(value, 512))
      || !text(subject.creationNonce, 256)
      || !/^[A-Za-z0-9_@][A-Za-z0-9_.@-]{0,255}$/u.test(subject.project)
      || !/^[A-Za-z0-9_@][A-Za-z0-9_.@-]{0,255}$/u.test(subject.worktreeId)
      || !/^(task|file):[!-~]+$/u.test(locator)) refuse("subject-unavailable");
  return subject;
}

export async function observeResidentReceipt({
  sessionDir, sid, locator, signal, pending = {}, deadline: suppliedDeadline, beforeRecord,
}) {
  if (pending.inflight) return { status: "unavailable", reason: "local-observation-in-flight", pending };
  if (!text(sessionDir) || !text(sid, 256) || !text(locator, 512)) {
    return { status: "unavailable", reason: "session-unavailable", pending };
  }
  if (typeof beforeRecord !== "function") {
    return { status: "unavailable", reason: "native-metadata-required", pending };
  }
  const deadline = Math.min(
    performance.now() + OBSERVATION_MS - SETTLEMENT_RESERVE_MS,
    Number.isFinite(suppliedDeadline) ? suppliedDeadline : Infinity,
  );
  const aborter = new AbortController();
  const cancel = () => aborter.abort();
  signal?.addEventListener("abort", cancel, { once: true });
  if (signal?.aborted) cancel();
  const check = () => {
    if (aborter.signal.aborted || performance.now() >= deadline) refuse("observation-expired");
  };
  const unavailable = () => ({
    status: pending.uncertain ? "unknown" : "unavailable",
    reason: pending.uncertain ? "exact-reconciliation-required" : "resident-unavailable", pending,
  });
  let timer;
  const expired = new Promise((resolveResult) => {
    const settle = () => resolveResult(unavailable());
    aborter.signal.addEventListener("abort", settle, { once: true });
    timer = setTimeout(cancel, Math.max(0, deadline - performance.now()));
    if (aborter.signal.aborted) settle();
  });
  const work = (async () => {
    check();
    const guidance = join(sessionDir, "instructions", "agent-worktrees", "session-guidance.instructions.md");
    const candidate = candidateFromGuidance(await readBounded(guidance, GUIDANCE_BYTES, check));
    const selected = installation(candidate);
    if (pending.installation
        && !same(stableInstallation(pending.installation), stableInstallation(selected))) {
      refuse("installation-changed");
    }
    if (pending.runtimeRoot && pending.runtimeRoot !== candidate.runtimeRoot) refuse("installation-changed");
    const binding = parseUniqueJson(await readBounded(join(sessionDir, "worktree-binding.json"), FRAME_BYTES, check));
    const subject = subjectFromBinding(binding, sid, locator);
    if (pending.subject && !same(pending.subject, subject)) refuse("subject-changed");
    pending.subject ??= subject;
    pending.installation ??= selected;
    pending.runtimeRoot ??= candidate.runtimeRoot;
    const routeText = await readBounded(candidate.activeRoutePath, FRAME_BYTES, check);
    const route = activeRoute(routeText);
    const token = (await readBounded(candidate.tokenPath, 4096, check)).trim();
    if (!text(token, 4096)) refuse("token-unavailable");
    const health = await request(route, token, "health", {}, aborter.signal, check);
    const receipt = health.promptReceipt;
    if (!Array.isArray(health.capabilities) || !health.capabilities.includes(CAPABILITY)
        || receipt?.contractVersion !== 1 || receipt.status !== "ready" || !object(receipt.owner)) {
      refuse("capability-unavailable");
    }
    const owner = receipt.owner;
    const expected = { ...selected, routeGeneration: route.routeGeneration, pid: route.pid,
      processStartTime: route.processStartTime, runtimeGeneration: owner.runtimeGeneration };
    if (!text(owner.runtimeGeneration, 256) || !same(owner, expected)) refuse("owner-mismatch");
    const query = pending.uncertain === true;
    if (!query) {
      const admission = await beforeRecord(pending);
      check();
      if (admission?.confirmed === true) {
        pending.uncertain = false;
        return { status: "logged", reason: "metadata-confirmed", replayed: true, pending };
      }
      if (admission?.verified !== true) refuse("native-metadata-unverified");
    }
    // Persistence can wait; do not admit a socket effect using a superseded projection.
    const again = candidateFromGuidance(await readBounded(guidance, GUIDANCE_BYTES, check));
    const currentBinding = parseUniqueJson(await readBounded(
      join(sessionDir, "worktree-binding.json"), FRAME_BYTES, check,
    ));
    if (!same(again, candidate)
        || await readBounded(candidate.activeRoutePath, FRAME_BYTES, check) !== routeText
        || !same(subjectFromBinding(currentBinding, sid, locator), subject)) {
      refuse("owner-changed");
    }
    const result = await request(route, token, "handoff-prompt-receipt", {
      contractVersion: 1, operation: query ? "query" : "record",
      subject: pending.subject, expectedOwner: owner,
      remainingMs: Math.max(1, Math.floor(deadline - performance.now())),
    }, aborter.signal, check, () => { if (!query) pending.uncertain = true; });
    if (result.contractVersion !== 1 || !same(result.subject, pending.subject)
        || !same(result.owner, owner) || typeof result.replayed !== "boolean"
        || !["received", "absent", "unavailable", "unknown"].includes(result.status)
        || !/^[a-z][a-z0-9-]{0,79}$/u.test(result.reason || "")
        || (!query && result.status === "absent")) refuse("invalid-receipt-response");
    if (result.status === "received") {
      pending.uncertain = false;
      return { status: "logged", reason: result.reason, replayed: result.replayed, pending };
    }
    if (query && result.status === "absent") pending.uncertain = false;
    // An unavailable post-send response is not assumed to prove no admission.
    return { status: pending.uncertain ? "unknown" : result.status, reason: result.reason, pending };
  })().catch(unavailable);
  Object.defineProperty(pending, "inflight", {
    value: work, writable: true, configurable: true, enumerable: false,
  });
  work.finally(() => { if (pending.inflight === work) delete pending.inflight; });
  try { return await Promise.race([work, expired]); }
  finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
    aborter.abort();
  }
}
