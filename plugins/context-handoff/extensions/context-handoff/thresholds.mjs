// Percentage-based context handoff thresholds. Repositories may override these
// defaults through .context-handoff/config.yaml.
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

export function validateThresholds(thresholds, source = "thresholds") {
  const { softPercent, hardPercent, forcePercent } = thresholds;
  for (const [name, value] of Object.entries({ softPercent, hardPercent, forcePercent })) {
    if (!Number.isInteger(value) || value < 1 || value > 79) {
      throw new Error(`${source}: ${name} must be an integer from 1 through 79`);
    }
  }
  if (softPercent >= hardPercent) {
    throw new Error(`${source}: softPercent must be less than hardPercent`);
  }
  if (hardPercent >= forcePercent) {
    throw new Error(`${source}: hardPercent must be less than forcePercent`);
  }
  return Object.freeze({ softPercent, hardPercent, forcePercent });
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
  const forceThreshold = validLimit
    ? Math.ceil(tokenLimit * thresholds.forcePercent / 100)
    : null;
  return {
    softPercent: thresholds.softPercent,
    hardPercent: thresholds.hardPercent,
    forcePercent: thresholds.forcePercent,
    softThreshold,
    hardThreshold,
    forceThreshold,
    soft: validLimit && currentTokens >= softThreshold,
    hard: validLimit && currentTokens >= hardThreshold,
    force: validLimit && currentTokens >= forceThreshold,
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
