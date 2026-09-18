// Context handoff thresholds. Repositories may override these defaults through
// .context-handoff/config.yaml or a user-level ~/.context-handoff/config.yaml.
export const SOFT_UTILIZATION_PERCENT = 55;
export const HARD_UTILIZATION_PERCENT = 70;
// The force tier is the last chance to capture a handoff before the native
// runtime's own auto-compaction kicks in (~80%) and destroys the very
// conversation state a handoff needs to describe. 79 preserves the same
// pre-compaction safety margin the old hard-tier ceiling used to guarantee.
export const FORCE_UTILIZATION_PERCENT = 79;

export const DEFAULT_THRESHOLDS = Object.freeze({
  softPercent: SOFT_UTILIZATION_PERCENT,
  hardPercent: HARD_UTILIZATION_PERCENT,
  forcePercent: FORCE_UTILIZATION_PERCENT,
});

const TIERS = Object.freeze(["soft", "hard", "force"]);

function tierConfig(tier, thresholds) {
  const percentKey = `${tier}Percent`;
  const tokensKey = `${tier}Tokens`;
  const percent = thresholds[percentKey];
  const tokens = thresholds[tokensKey];
  const hasPercent = percent != null;
  const hasTokens = tokens != null;
  if (hasPercent && hasTokens) {
    throw new Error(`${percentKey} and ${tokensKey} are mutually exclusive`);
  }
  if (!hasPercent && !hasTokens) {
    throw new Error(`expected exactly one of ${percentKey} or ${tokensKey}`);
  }
  if (hasPercent) {
    return { tier, key: percentKey, kind: "percent", value: percent };
  }
  return { tier, key: tokensKey, kind: "tokens", value: tokens };
}

function resolveThreshold(spec, tokenLimit) {
  if (spec.kind === "tokens") {
    return spec.value;
  }
  if (!Number.isFinite(tokenLimit) || tokenLimit <= 0) {
    return null;
  }
  return Math.ceil(tokenLimit * spec.value / 100);
}

function compareThresholds(lower, upper, source, tokenLimit) {
  const lowerResolved = resolveThreshold(lower, tokenLimit);
  const upperResolved = resolveThreshold(upper, tokenLimit);
  if (lowerResolved != null && upperResolved != null) {
    if (lowerResolved >= upperResolved) {
      const scope = Number.isFinite(tokenLimit) && tokenLimit > 0
        ? ` at token limit ${tokenLimit.toLocaleString()}`
        : "";
      throw new Error(
        `${source}: ${lower.tier} threshold (${lowerResolved.toLocaleString()} tokens)` +
        ` must be less than ${upper.tier} threshold (${upperResolved.toLocaleString()} tokens)` +
        scope,
      );
    }
    return;
  }
  // Mixed token/percent configs are allowed per tier. When the runtime has not
  // reported a token limit yet, a percent tier has no effective token count, so
  // cross-unit ordering cannot be proved here and is deferred until a later
  // contextPressure() call with a real limit.
  if (lower.kind === upper.kind && lower.value >= upper.value) {
    throw new Error(`${source}: ${lower.key} must be less than ${upper.key}`);
  }
}

export function validateThresholds(
  thresholds,
  source = "thresholds",
  tokenLimit = null,
) {
  const normalized = {};
  const configs = TIERS.map((tier) => {
    const config = tierConfig(tier, thresholds);
    if (!Number.isInteger(config.value) || config.value < 1) {
      const range = config.kind === "percent"
        ? "an integer from 1 through 79"
        : "a positive integer";
      throw new Error(`${source}: ${config.key} must be ${range}`);
    }
    if (config.kind === "percent" && config.value > 79) {
      throw new Error(`${source}: ${config.key} must be an integer from 1 through 79`);
    }
    normalized[config.key] = config.value;
    return config;
  });

  compareThresholds(configs[0], configs[1], source, tokenLimit);
  compareThresholds(configs[1], configs[2], source, tokenLimit);
  return Object.freeze(normalized);
}

export function formatConfiguredThreshold(thresholds, tier) {
  const config = tierConfig(tier, thresholds);
  if (config.kind === "tokens") {
    return `${config.value.toLocaleString()} tokens`;
  }
  return `${config.value}%`;
}

export function contextPressure(
  currentTokens,
  tokenLimit,
  thresholds = DEFAULT_THRESHOLDS,
) {
  const normalized = validateThresholds(thresholds, "thresholds", tokenLimit);
  const softThreshold = resolveThreshold(tierConfig("soft", normalized), tokenLimit);
  const hardThreshold = resolveThreshold(tierConfig("hard", normalized), tokenLimit);
  const forceThreshold = resolveThreshold(tierConfig("force", normalized), tokenLimit);
  return {
    ...normalized,
    softThreshold,
    hardThreshold,
    forceThreshold,
    soft: softThreshold != null && currentTokens >= softThreshold,
    hard: hardThreshold != null && currentTokens >= hardThreshold,
    force: forceThreshold != null && currentTokens >= forceThreshold,
  };
}

export function formatContextUsage(currentTokens, tokenLimit) {
  if (Number.isFinite(tokenLimit) && tokenLimit > 0) {
    return {
      utilization: `${Math.round(currentTokens / tokenLimit * 100)}%`,
      tokens:
        `${currentTokens.toLocaleString()} / ${tokenLimit.toLocaleString()} tokens`,
    };
  }
  return {
    utilization: "unknown",
    tokens: `${currentTokens.toLocaleString()} tokens; limit unknown`,
  };
}
