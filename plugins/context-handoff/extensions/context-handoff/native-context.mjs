import { open } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { findRepositoryRootAsync } from "./config.mjs";

export const NATIVE_CONTEXT_TOOLS = Object.freeze([
  "get_context_remaining", "session_artifacts", "session_history", "new_context",
]);
const MAX_SETTINGS_BYTES = 1024 * 1024;

async function readBoundedSettings(path) {
  const handle = await open(path, "r");
  try {
    const buffer = Buffer.alloc(MAX_SETTINGS_BYTES + 1);
    let offset = 0;
    while (offset < buffer.length) {
      const { bytesRead } = await handle.read(buffer, offset, buffer.length - offset, null);
      if (!bytesRead) break;
      offset += bytesRead;
    }
    if (offset > MAX_SETTINGS_BYTES) throw new Error("settings exceed size limit");
    return buffer.subarray(0, offset);
  } finally { await handle.close(); }
}

async function bounded(operation, timeoutMs) {
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(operation),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error("observation-timeout")), timeoutMs);
      }),
    ]);
  } finally { clearTimeout(timer); }
}

// Preserve strings and line boundaries while removing JSONC comments/commas.
export function parseSettings(text) {
  let output = "";
  let quoted = false;
  let escaped = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (quoted) {
      output += char;
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') quoted = false;
    } else if (char === '"') {
      quoted = true;
      output += char;
    } else if (char === "/" && text[i + 1] === "/") {
      while (i < text.length && text[i] !== "\n") i++;
      output += "\n";
    } else if (char === "/" && text[i + 1] === "*") {
      i += 2;
      while (i < text.length && !(text[i] === "*" && text[i + 1] === "/")) {
        output += text[i] === "\n" ? "\n" : " ";
        i++;
      }
      if (i >= text.length) throw new Error("unterminated JSONC comment");
      i++;
      output += " ";
    } else {
      output += char;
    }
  }
  let normalized = "";
  quoted = false;
  escaped = false;
  for (let i = 0; i < output.length; i++) {
    const char = output[i];
    if (!quoted && char === ",") {
      let next = i + 1;
      while (/\s/.test(output[next] ?? "") && next < output.length) next++;
      const previous = normalized.trimEnd().at(-1);
      if ((output[next] === "}" || output[next] === "]") &&
          previous && !["[", "{", ","].includes(previous)) continue;
    }
    normalized += char;
    if (quoted && escaped) escaped = false;
    else if (quoted && char === "\\") escaped = true;
    else if (char === '"') quoted = !quoted;
  }
  const value = JSON.parse(normalized.replace(/^\uFEFF/, ""));
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("settings must be an object");
  }
  return value;
}

export async function readNativeSetting(cwd, {
  configHome = process.env.COPILOT_HOME || join(homedir(), ".copilot"),
  read = readBoundedSettings,
} = {}) {
  const root = await findRepositoryRootAsync(cwd);
  const paths = [
    join(configHome, "settings.json"),
    // Installed hosts still accepting legacy user keys layer them last.
    join(configHome, "config.json"),
    ...(root ? [
      join(root, ".github", "copilot", "settings.json"),
      join(root, ".github", "copilot", "settings.local.json"),
    ] : []),
  ];
  let enabled = null;
  let source = null;
  const warnings = [];
  for (const path of paths) {
    try {
      const raw = await read(path);
      if (Buffer.byteLength(raw) > MAX_SETTINGS_BYTES) {
        throw new Error("settings exceed size limit");
      }
      const settings = parseSettings(raw.toString("utf8"));
      if (!Object.hasOwn(settings, "contextManagementTools")) continue;
      if (typeof settings.contextManagementTools !== "boolean") {
        throw new Error("contextManagementTools must be boolean");
      }
      enabled = settings.contextManagementTools;
      source = path;
    } catch (error) {
      if (error?.code === "ENOENT") continue;
      // Never return settings contents (which can contain credentials).
      warnings.push(`Cannot read native-context setting in ${path}: ` +
        (error?.code || "invalid-settings"));
      enabled = null;
      source = path;
    }
  }
  return { enabled, source, warnings };
}

export function offeredNativeTools(snapshot) {
  if (!snapshot || snapshot.tools === null) return "unknown";
  if (!Array.isArray(snapshot.tools)) return "unknown";
  const names = new Set(snapshot.tools
    .filter((tool) => tool && !tool.mcpServerName && !tool.mcpToolName)
    .map((tool) => tool.name));
  return NATIVE_CONTEXT_TOOLS.every((name) => names.has(name))
    ? "available" : "unavailable";
}

export function selectNativeContext(setting, availability) {
  if (setting === false) return { native: false, reason: "setting-disabled" };
  if (availability === "unavailable") {
    return { native: false, reason: "tools-unavailable" };
  }
  if (setting === true) return { native: true, reason: "setting-enabled" };
  return { native: availability === "available", reason: `tools-${availability}` };
}

export function createNativeContextObserver({
  readSetting = readNativeSetting,
  getMetadata,
  timeoutMs = 1000,
  cacheMs = 5000,
  now = Date.now,
} = {}) {
  let cached = null;
  let inFlight = null;
  let revision = 0;
  return {
    invalidate() { revision++; cached = null; },
    async observe(cwd, { fresh = false } = {}) {
      if (!fresh && cached?.cwd === cwd && now() - cached.at < cacheMs) {
        return cached.value;
      }
      if (inFlight?.cwd === cwd && inFlight.revision === revision) {
        return inFlight.promise;
      }
      const observedRevision = revision;
      const promise = (async () => {
        let setting;
        try {
          setting = await bounded(() => readSetting(cwd), timeoutMs);
        } catch {
          setting = { enabled: null, warnings: [
            "Native-context settings observation failed or timed out.",
          ] };
        }
        let availability = "unknown";
        const warnings = [...setting.warnings];
        if (getMetadata) {
          try {
            const snapshot = await bounded(getMetadata, timeoutMs);
            if (!snapshot || !Object.hasOwn(snapshot, "tools") ||
                (snapshot.tools !== null && !Array.isArray(snapshot.tools))) {
              throw new Error("invalid-tool-metadata");
            }
            availability = offeredNativeTools(snapshot);
          } catch {
            warnings.push("Native-context offered-tool observation failed or timed out.");
          }
        }
        const value = {
          ...selectNativeContext(setting.enabled, availability),
          setting: setting.enabled, availability, warnings,
        };
        if (revision === observedRevision) cached = { cwd, at: now(), value };
        return value;
      })();
      inFlight = { cwd, revision: observedRevision, promise };
      try { return await promise; }
      finally { if (inFlight?.promise === promise) inFlight = null; }
    },
  };
}

export function nativePressurePrompt(usage, level, observation) {
  return `[Context Handoff -- native] Context utilization is ${usage.utilization} ` +
    `(${usage.tokens}); ${level} checkpoint boundary reached. Native context ` +
    `continuity is selected (${observation.reason}; offered tools ${observation.availability}). ` +
    "Use get_context_remaining and the context-handoff skill's native checkpoint flow. " +
    "Preserve the parent objective/completion gate, current slice, unresolved requests, " +
    "decisions, live background/external obligations, and exactly one next action in " +
    "session_artifacts under the current owner's expected revision. Verify the checkpoint " +
    "and host-required snapshot binding before terminal new_context; do not call a raw clear. " +
    "After rollover read the bound checkpoint and refresh durable guidance. " +
    "If tools are unavailable, diagnose and use explicit mode-governed custom recovery; " +
    "a policy cap or partial persistence error is not permission for another session or " +
    "a blind second rollover. Do not trigger a new-owner handoff merely for token pressure.";
}
