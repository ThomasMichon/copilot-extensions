// Cost-aware context handoff thresholds.
//
// Large context windows make percentage-only thresholds wait too long: a
// linearly growing prefix incurs roughly quadratic cumulative cache reads. The
// absolute caps trigger earlier there, while the percentage limits preserve the
// established pre-compaction safety behavior for smaller windows.

export const SOFT_TOKEN_CAP = 150_000;
export const HARD_TOKEN_CAP = 250_000;
export const SOFT_UTILIZATION_PERCENT = 55;
export const HARD_UTILIZATION_PERCENT = 70;

export function contextPressure(currentTokens, tokenLimit) {
  const validLimit = Number.isFinite(tokenLimit) && tokenLimit > 0;
  const softThreshold = validLimit
    ? Math.min(
      SOFT_TOKEN_CAP,
      Math.ceil(tokenLimit * SOFT_UTILIZATION_PERCENT / 100),
    )
    : SOFT_TOKEN_CAP;
  const hardThreshold = validLimit
    ? Math.min(
      HARD_TOKEN_CAP,
      Math.ceil(tokenLimit * HARD_UTILIZATION_PERCENT / 100),
    )
    : HARD_TOKEN_CAP;
  return {
    softThreshold,
    hardThreshold,
    soft: currentTokens >= softThreshold,
    hard: currentTokens >= hardThreshold,
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
