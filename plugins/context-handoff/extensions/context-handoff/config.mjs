import {
  existsSync,
  lstatSync,
  readFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";

import { DEFAULT_THRESHOLDS, validateThresholds } from "./thresholds.mjs";
import { DEFAULT_HANDOFF_MODE, validateHandoffMode } from "./mode.mjs";

export const CONFIG_RELATIVE_PATH = join(".context-handoff", "config.yaml");
// Payload-only plugins in this repo commonly keep user-global configuration in
// a plugin-named hidden home directory. Mirroring the repo-local
// `.context-handoff/config.yaml` shape in `~/.context-handoff/config.yaml`
// keeps the two layers obvious and portable without inventing an install root.
export const USER_CONFIG_RELATIVE_PATH = join(".context-handoff", "config.yaml");

export function describeError(error) {
  return error instanceof Error ? error.message : String(error);
}

export function findRepositoryRoot(startDir) {
  let current = resolve(startDir);
  while (true) {
    if (existsSync(join(current, ".git"))) {
      return current;
    }
    const parent = dirname(current);
    if (parent === current) {
      return null;
    }
    current = parent;
  }
}

const THRESHOLD_KEY_MAP = Object.freeze({
  soft_percent: "softPercent",
  hard_percent: "hardPercent",
  force_percent: "forcePercent",
  soft_tokens: "softTokens",
  hard_tokens: "hardTokens",
  force_tokens: "forceTokens",
});
const THRESHOLD_TIERS = Object.freeze(["soft", "hard", "force"]);

export function parseContextHandoffConfig(text, source = CONFIG_RELATIVE_PATH) {
  const configuredThresholds = {};
  let configuredMode;
  let inThresholds = false;
  let sawThresholds = false;
  let sawSetting = false;

  for (const [index, rawLine] of text.split(/\r?\n/).entries()) {
    const lineNumber = index + 1;
    if (/^\s*(?:#.*)?$/.test(rawLine)) {
      continue;
    }
    if (!rawLine.startsWith(" ")) {
      inThresholds = false;
    }
    const modeMatch = rawLine.match(/^mode:\s*(\S+)\s*(?:#.*)?$/);
    if (modeMatch) {
      if (configuredMode != null) {
        throw new Error(`${source}:${lineNumber}: duplicate mode`);
      }
      configuredMode = validateHandoffMode(modeMatch[1], `${source}:${lineNumber}`);
      sawSetting = true;
      continue;
    }
    if (/^thresholds:\s*(?:#.*)?$/.test(rawLine)) {
      if (sawThresholds) {
        throw new Error(`${source}:${lineNumber}: duplicate thresholds block`);
      }
      sawThresholds = true;
      inThresholds = true;
      sawSetting = true;
      continue;
    }

    const match = rawLine.match(
      /^  +(soft_percent|hard_percent|force_percent|soft_tokens|hard_tokens|force_tokens):\s*(\d+(?:\.\d+)?)\s*(?:#.*)?$/,
    );
    if (!inThresholds || !match) {
      throw new Error(
        `${source}:${lineNumber}: expected top-level mode or thresholds block entries ` +
        "(soft_percent|hard_percent|force_percent|soft_tokens|hard_tokens|force_tokens)",
      );
    }

    const key = THRESHOLD_KEY_MAP[match[1]];
    if (Object.hasOwn(configuredThresholds, key)) {
      throw new Error(`${source}:${lineNumber}: duplicate ${match[1]}`);
    }
    configuredThresholds[key] = Number(match[2]);
  }

  if (!sawSetting) {
    throw new Error(`${source}: missing mode or thresholds block`);
  }

  const result = {};
  if (configuredMode != null) {
    result.mode = configuredMode;
  }
  if (Object.keys(configuredThresholds).length) {
    result.thresholds = configuredThresholds;
  }
  return Object.freeze(result);
}

export function parseThresholdConfig(text, source = CONFIG_RELATIVE_PATH) {
  const parsed = parseContextHandoffConfig(text, source);
  if (!parsed.thresholds) {
    throw new Error(`${source}: missing thresholds block`);
  }
  return validateThresholds(
    mergeThresholdConfigs(parsed.thresholds),
    source,
  );
}

function mergeThresholdConfigs(...layers) {
  const merged = { ...DEFAULT_THRESHOLDS };
  for (const layer of layers) {
    if (!layer) continue;
    for (const tier of THRESHOLD_TIERS) {
      const percentKey = `${tier}Percent`;
      const tokensKey = `${tier}Tokens`;
      if (Object.hasOwn(layer, percentKey)) {
        delete merged[tokensKey];
        merged[percentKey] = layer[percentKey];
      }
      if (Object.hasOwn(layer, tokensKey)) {
        delete merged[percentKey];
        merged[tokensKey] = layer[tokensKey];
      }
    }
  }
  return merged;
}

function validateConfigPath(configDir, configPath) {
  const dirStat = lstatSync(configDir);
  if (!dirStat.isDirectory() || dirStat.isSymbolicLink()) {
    throw new Error("config directory must be a regular, non-symlink directory");
  }
  const stat = lstatSync(configPath);
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw new Error("config must be a regular, non-symlink file");
  }
}

function loadConfigLayer(configPath) {
  if (!existsSync(configPath)) {
    return null;
  }
  validateConfigPath(dirname(configPath), configPath);
  return {
    path: configPath,
    ...parseContextHandoffConfig(readFileSync(configPath, "utf-8"), configPath),
  };
}

function defaultConfig(configPath = null) {
  return {
    mode: DEFAULT_HANDOFF_MODE,
    thresholds: DEFAULT_THRESHOLDS,
    configPath,
    warning: null,
  };
}

export function loadContextHandoffConfig(startDir, options = {}) {
  const repositoryRoot = findRepositoryRoot(startDir);
  const home = options.homeDir ?? homedir();
  const userConfigPath = home ? join(home, USER_CONFIG_RELATIVE_PATH) : null;
  const repoConfigPath = repositoryRoot ? join(repositoryRoot, CONFIG_RELATIVE_PATH) : null;
  const configPath = repoConfigPath && existsSync(repoConfigPath)
    ? repoConfigPath
    : userConfigPath && existsSync(userConfigPath)
      ? userConfigPath
      : null;
  if (!repoConfigPath && !userConfigPath) {
    return defaultConfig();
  }
  try {
    const userConfig = userConfigPath ? loadConfigLayer(userConfigPath) : null;
    const repoConfig = repoConfigPath ? loadConfigLayer(repoConfigPath) : null;
    const thresholds = validateThresholds(
      mergeThresholdConfigs(userConfig?.thresholds, repoConfig?.thresholds),
      repoConfig?.path || userConfig?.path || CONFIG_RELATIVE_PATH,
    );
    const mode = validateHandoffMode(
      repoConfig?.mode ?? userConfig?.mode ?? DEFAULT_HANDOFF_MODE,
      repoConfig?.path || userConfig?.path || CONFIG_RELATIVE_PATH,
    );
    return {
      mode,
      thresholds,
      configPath: repoConfig?.path || userConfig?.path || null,
      warning: null,
    };
  } catch (error) {
    return {
      mode: DEFAULT_HANDOFF_MODE,
      thresholds: DEFAULT_THRESHOLDS,
      configPath,
      warning:
        "Invalid context-handoff config; using defaults " +
        `(${DEFAULT_HANDOFF_MODE}; ${DEFAULT_THRESHOLDS.softPercent}%/` +
        `${DEFAULT_THRESHOLDS.hardPercent}%/${DEFAULT_THRESHOLDS.forcePercent}%): ` +
        describeError(error),
    };
  }
}
