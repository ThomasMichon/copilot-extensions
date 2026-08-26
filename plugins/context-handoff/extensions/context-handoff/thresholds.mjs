// Cost-aware context handoff thresholds.
//
// Large context windows make percentage-only thresholds wait too long: a
// linearly growing prefix incurs roughly quadratic cumulative cache reads. The
// absolute caps trigger earlier there, while the percentage limits preserve the
// established pre-compaction safety behavior for smaller windows.

export const SOFT_TOKEN_CAP = 150_000;
export const HARD_TOKEN_CAP = 250_000;
export const SOFT_UTILIZATION_THRESHOLD = 0.55;
export const HARD_UTILIZATION_THRESHOLD = 0.70;

export function contextPressure(currentTokens, tokenLimit) {
  const validLimit = Number.isFinite(tokenLimit) && tokenLimit > 0;
  const softThreshold = validLimit
    ? Math.min(
      SOFT_TOKEN_CAP,
      Math.round(tokenLimit * SOFT_UTILIZATION_THRESHOLD),
    )
    : SOFT_TOKEN_CAP;
  const hardThreshold = validLimit
    ? Math.min(
      HARD_TOKEN_CAP,
      Math.round(tokenLimit * HARD_UTILIZATION_THRESHOLD),
    )
    : HARD_TOKEN_CAP;
  return {
    softThreshold,
    hardThreshold,
    soft: currentTokens >= softThreshold,
    hard: currentTokens >= hardThreshold,
  };
}
