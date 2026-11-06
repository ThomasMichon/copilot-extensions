// Live control of automatic context-pressure handling within one session:
//   * a config reloader that re-reads `.context-handoff/config.yaml` layers
//     only when their stat fingerprint changes, keeping the last good config
//     when an edit is invalid; and
//   * a session-scoped override (pause, or replacement thresholds) that is
//     never written back to any config file.
// Pure helpers live here so they are unit-testable without the SDK session.

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { lstat } from "node:fs/promises";
import { join } from "node:path";

import { mergeThresholdConfigs } from "./config.mjs";
import { formatConfiguredThreshold, validateThresholds } from "./thresholds.mjs";

export const OVERRIDE_FILE_NAME = "context-handoff-override.json";
export const OVERRIDE_ACTIONS = Object.freeze(["status", "pause", "resume", "set", "clear"]);
export const ACTOR_OPERATOR = "operator";
export const ACTOR_AGENT = "agent";
// The agent may only make automatic pressure handling less frequent.
const AGENT_ACTIONS = Object.freeze(["status", "pause", "set"]);
const TIERS = Object.freeze(["soft", "hard", "force"]);
const THRESHOLD_KEYS = Object.freeze(
  TIERS.flatMap((tier) => [`${tier}Percent`, `${tier}Tokens`]),
);

// --- Config reloader ---

export function createConfigReloader({ paths, load, statFn = lstat }) {
  let fingerprint = null;

  async function fingerprintOf() {
    const parts = await Promise.all(paths.map(async (path) => {
      try {
        const stat = await statFn(path);
        return `${path}:${stat.mtimeMs}:${stat.size}`;
      } catch {
        return `${path}:absent`;
      }
    }));
    return parts.join("|");
  }

  return {
    async prime() {
      fingerprint = await fingerprintOf();
    },
    // { changed: true, config } when a valid edit landed; { changed: false,
    // warning } when an edit is invalid (the caller keeps its current config);
    // { changed: false } when nothing moved. An invalid edit is reported once:
    // the fingerprint advances, so only the next edit is re-evaluated.
    async refresh() {
      const next = await fingerprintOf();
      if (next === fingerprint) return { changed: false };
      fingerprint = next;
      const config = await load();
      if (config.warning) return { changed: false, warning: config.warning };
      return { changed: true, config };
    },
  };
}

// --- Session override ---

export function emptyOverride() {
  return { paused: false, thresholds: null, updatedBy: null, updatedAt: null };
}

function normalizeOverride(value) {
  if (!value || typeof value !== "object") return emptyOverride();
  let thresholds = null;
  if (value.thresholds && typeof value.thresholds === "object") {
    thresholds = {};
    for (const key of THRESHOLD_KEYS) {
      const entry = value.thresholds[key];
      if (Number.isInteger(entry) && entry > 0) thresholds[key] = entry;
    }
    if (Object.keys(thresholds).length === 0) thresholds = null;
  }
  return {
    paused: value.paused === true,
    thresholds,
    updatedBy: [ACTOR_OPERATOR, ACTOR_AGENT].includes(value.updatedBy) ? value.updatedBy : null,
    updatedAt: typeof value.updatedAt === "string" ? value.updatedAt : null,
  };
}

export function overrideActive(override) {
  return Boolean(override?.paused || override?.thresholds);
}

export function readOverride(path) {
  try {
    if (!path || !existsSync(path)) return emptyOverride();
    return normalizeOverride(JSON.parse(readFileSync(path, "utf-8")));
  } catch {
    return emptyOverride();
  }
}

export function writeOverride(path, override) {
  if (!path) return false;
  try {
    writeFileSync(path, JSON.stringify(normalizeOverride(override)), "utf-8");
    return true;
  } catch {
    return false;
  }
}

export function sessionOverridePath(sessionStateRoot, sessionId) {
  if (!sessionStateRoot || !sessionId || sessionId === "unknown") return null;
  const dir = join(sessionStateRoot, sessionId);
  // An active session already owns its directory; never create it here.
  return existsSync(dir) ? join(dir, OVERRIDE_FILE_NAME) : null;
}

// Config thresholds with any session override layered on top. An override
// that no longer fits a reloaded config (or the live token limit, for mixed
// percent/token tiers) falls back to the config thresholds.
export function effectiveThresholds(configThresholds, override, tokenLimit = null) {
  if (!override?.thresholds) return { thresholds: configThresholds, warning: null };
  try {
    return {
      thresholds: validateThresholds(
        mergeThresholdConfigs(configThresholds, override.thresholds),
        "session override",
        tokenLimit,
      ),
      warning: null,
    };
  } catch (error) {
    return {
      thresholds: configThresholds,
      warning: `Session threshold override ignored: ${error.message}`,
    };
  }
}

// "60", "60%" -> percent; "150000t", "150000 tokens", "150k" -> tokens.
export function parseThresholdValue(tier, raw) {
  if (!TIERS.includes(tier)) throw new Error(`unknown threshold tier '${tier}'`);
  const text = String(raw).trim().toLowerCase();
  const match = text.match(/^(\d+)\s*(%|k|t|tokens?)?$/);
  if (!match) {
    throw new Error(
      `${tier}: expected a percent (e.g. 60 or 60%) or tokens (e.g. 150000t or 150k)`,
    );
  }
  const amount = Number(match[1]);
  const unit = match[2] ?? "%";
  if (unit === "%") return { [`${tier}Percent`]: amount };
  return { [`${tier}Tokens`]: unit === "k" ? amount * 1000 : amount };
}

// Parses slash-command arguments: `<action> [soft=60] [hard=150k] [force=78%]`.
export function parseOverrideArgs(text) {
  const tokens = String(text ?? "").trim().split(/\s+/).filter(Boolean);
  const action = (tokens.shift() ?? "status").toLowerCase();
  const thresholds = {};
  for (const token of tokens) {
    const match = token.match(/^(soft|hard|force)=(.+)$/i);
    if (!match) throw new Error(`unrecognized argument '${token}' (expected soft=, hard=, or force=)`);
    Object.assign(thresholds, parseThresholdValue(match[1].toLowerCase(), match[2]));
  }
  return { action, thresholds };
}

// Layers requested tiers onto an existing override without seeding defaults,
// so tiers the override never named keep following the config file.
function layerOverrideThresholds(existing, requested) {
  const layered = { ...(existing ?? {}) };
  for (const tier of TIERS) {
    for (const [key, other] of [
      [`${tier}Percent`, `${tier}Tokens`],
      [`${tier}Tokens`, `${tier}Percent`],
    ]) {
      if (Object.hasOwn(requested, key)) {
        delete layered[other];
        layered[key] = requested[key];
      }
    }
  }
  return layered;
}

function tierSpec(thresholds, tier) {
  const percent = thresholds[`${tier}Percent`];
  return percent != null
    ? { kind: "percent", value: percent }
    : { kind: "tokens", value: thresholds[`${tier}Tokens`] };
}

function resolvedTokens(spec, tokenLimit) {
  if (spec.kind === "tokens") return spec.value;
  return Number.isFinite(tokenLimit) && tokenLimit > 0
    ? Math.ceil(tokenLimit * spec.value / 100)
    : null;
}

// true / false when provable; null when units differ and no token limit is known.
function notLower(before, after, tokenLimit) {
  if (before.kind === after.kind) return after.value >= before.value;
  const b = resolvedTokens(before, tokenLimit);
  const a = resolvedTokens(after, tokenLimit);
  return a == null || b == null ? null : a >= b;
}

// Applies one override request. Returns { ok, override, changed, message }.
// The agent may only pause or raise thresholds (never lower, resume, or clear),
// so it can quiet automatic pressure handling but never make it more frequent.
export function applyOverrideRequest(current, request, {
  actor,
  configThresholds,
  tokenLimit = null,
  now = () => new Date().toISOString(),
} = {}) {
  const base = normalizeOverride(current);
  const action = String(request?.action ?? "status").toLowerCase();
  const reject = (message) => ({ ok: false, override: base, changed: false, message });
  if (!OVERRIDE_ACTIONS.includes(action)) {
    return reject(`Unknown action '${action}'. Expected one of: ${OVERRIDE_ACTIONS.join(", ")}.`);
  }
  if (actor !== ACTOR_OPERATOR && !AGENT_ACTIONS.includes(action)) {
    return reject(
      `The agent may not '${action}' the session override; only the operator can, ` +
      "via the /handoff-pressure command.",
    );
  }
  const stamp = (next) => ({ ...next, updatedBy: actor, updatedAt: now() });

  if (action === "status") return { ok: true, override: base, changed: false, message: null };
  if (action === "pause") {
    return { ok: true, override: stamp({ ...base, paused: true }), changed: !base.paused, message: null };
  }
  if (action === "resume") {
    return { ok: true, override: stamp({ ...base, paused: false }), changed: base.paused, message: null };
  }
  if (action === "clear") {
    return { ok: true, override: emptyOverride(), changed: overrideActive(base), message: null };
  }

  const requested = request?.thresholds ?? {};
  if (Object.keys(requested).length === 0) {
    return reject("'set' needs at least one of soft=, hard=, or force=.");
  }
  const before = effectiveThresholds(configThresholds, base, tokenLimit).thresholds;
  const layered = layerOverrideThresholds(base.thresholds, requested);
  let after;
  try {
    after = validateThresholds(
      mergeThresholdConfigs(configThresholds, layered),
      "session override",
      tokenLimit,
    );
  } catch (error) {
    return reject(error.message);
  }
  if (actor !== ACTOR_OPERATOR) {
    for (const tier of TIERS) {
      const verdict = notLower(tierSpec(before, tier), tierSpec(after, tier), tokenLimit);
      if (verdict === false) {
        return reject(
          `The agent may only raise thresholds; ${tier} would drop from ` +
          `${formatConfiguredThreshold(before, tier)} to ${formatConfiguredThreshold(after, tier)}.`,
        );
      }
      if (verdict === null) {
        return reject(
          `Cannot verify that the new ${tier} threshold is not lower without a known ` +
          "token limit; use the same unit as the current threshold.",
        );
      }
    }
  }
  return {
    ok: true,
    override: stamp({ ...base, thresholds: { ...layered } }),
    changed: true,
    message: null,
  };
}

export function describeThresholds(thresholds) {
  return TIERS.map((tier) => `${tier} ${formatConfiguredThreshold(thresholds, tier)}`).join(", ");
}
