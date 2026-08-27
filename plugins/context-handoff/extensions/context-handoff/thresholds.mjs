// Percentage-based context handoff thresholds. Repositories may override these
// defaults through .context-handoff/config.yaml.
export const SOFT_UTILIZATION_PERCENT = 55;
export const HARD_UTILIZATION_PERCENT = 70;

export const DEFAULT_THRESHOLDS = Object.freeze({
  softPercent: SOFT_UTILIZATION_PERCENT,
  hardPercent: HARD_UTILIZATION_PERCENT,
});

export function validateThresholds(thresholds, source = "thresholds") {
  const { softPercent, hardPercent } = thresholds;
  for (const [name, value] of Object.entries({ softPercent, hardPercent })) {
    if (!Number.isInteger(value) || value < 1 || value > 79) {
      throw new Error(`${source}: ${name} must be an integer from 1 through 79`);
    }
  }
  if (softPercent >= hardPercent) {
    throw new Error(`${source}: softPercent must be less than hardPercent`);
  }
  return Object.freeze({ softPercent, hardPercent });
}

export function contextPressure(
  currentTokens,
  tokenLimit,
  thresholds = DEFAULT_THRESHOLDS,
) {
  const validLimit = Number.isFinite(tokenLimit) && tokenLimit > 0;
  const softThreshold = validLimit
    ? Math.ceil(tokenLimit * thresholds.softPercent / 100)
    : null;
  const hardThreshold = validLimit
    ? Math.ceil(tokenLimit * thresholds.hardPercent / 100)
    : null;
  return {
    softPercent: thresholds.softPercent,
    hardPercent: thresholds.hardPercent,
    softThreshold,
    hardThreshold,
    soft: validLimit && currentTokens >= softThreshold,
    hard: validLimit && currentTokens >= hardThreshold,
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
