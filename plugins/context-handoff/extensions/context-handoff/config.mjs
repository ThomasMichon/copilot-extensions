import {
  existsSync,
  lstatSync,
  readFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";

import { DEFAULT_THRESHOLDS, validateThresholds } from "./thresholds.mjs";

export const CONFIG_RELATIVE_PATH = join(".context-handoff", "config.yaml");

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

export function parseThresholdConfig(text, source = CONFIG_RELATIVE_PATH) {
  const configured = {};
  let inThresholds = false;

  for (const [index, rawLine] of text.split(/\r?\n/).entries()) {
    const lineNumber = index + 1;
    if (/^\s*(?:#.*)?$/.test(rawLine)) {
      continue;
    }
    if (/^thresholds:\s*(?:#.*)?$/.test(rawLine)) {
      if (inThresholds) {
        throw new Error(`${source}:${lineNumber}: duplicate thresholds block`);
      }
      inThresholds = true;
      continue;
    }

    const match = rawLine.match(
      /^  +(soft_percent|hard_percent):\s*(\d+(?:\.\d+)?)\s*(?:#.*)?$/,
    );
    if (!inThresholds || !match) {
      throw new Error(
        `${source}:${lineNumber}: expected thresholds.soft_percent or ` +
        "thresholds.hard_percent",
      );
    }

    const key = match[1] === "soft_percent" ? "softPercent" : "hardPercent";
    if (Object.hasOwn(configured, key)) {
      throw new Error(`${source}:${lineNumber}: duplicate ${match[1]}`);
    }
    configured[key] = Number(match[2]);
  }

  if (!inThresholds) {
    throw new Error(`${source}: missing thresholds block`);
  }

  return validateThresholds(
    { ...DEFAULT_THRESHOLDS, ...configured },
    source,
  );
}

export function loadContextHandoffConfig(startDir) {
  const repositoryRoot = findRepositoryRoot(startDir);
  if (!repositoryRoot) {
    return {
      thresholds: DEFAULT_THRESHOLDS,
      configPath: null,
      warning: null,
    };
  }

  const configDir = join(repositoryRoot, ".context-handoff");
  const configPath = join(configDir, "config.yaml");
  if (!existsSync(configPath)) {
    return {
      thresholds: DEFAULT_THRESHOLDS,
      configPath: null,
      warning: null,
    };
  }

  try {
    const dirStat = lstatSync(configDir);
    if (!dirStat.isDirectory() || dirStat.isSymbolicLink()) {
      throw new Error("config directory must be a regular, non-symlink directory");
    }
    const stat = lstatSync(configPath);
    if (!stat.isFile() || stat.isSymbolicLink()) {
      throw new Error("config must be a regular, non-symlink file");
    }
    return {
      thresholds: parseThresholdConfig(readFileSync(configPath, "utf-8"), configPath),
      configPath,
      warning: null,
    };
  } catch (error) {
    return {
      thresholds: DEFAULT_THRESHOLDS,
      configPath,
      warning:
        `Invalid ${CONFIG_RELATIVE_PATH}; using defaults ` +
        `(${DEFAULT_THRESHOLDS.softPercent}%/${DEFAULT_THRESHOLDS.hardPercent}%): ` +
        describeError(error),
    };
  }
}
