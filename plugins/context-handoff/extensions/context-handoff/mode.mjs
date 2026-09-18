export const DEFAULT_HANDOFF_MODE = "auto";
export const HANDOFF_MODES = Object.freeze([
  DEFAULT_HANDOFF_MODE,
  "manual-only",
  "off",
]);

export function validateHandoffMode(mode, source = "mode") {
  if (!HANDOFF_MODES.includes(mode)) {
    throw new Error(
      `${source}: mode must be one of ${HANDOFF_MODES.map((value) => `'${value}'`).join(", ")}`,
    );
  }
  return mode;
}

export function automaticHandoffEnabled(mode = DEFAULT_HANDOFF_MODE) {
  return validateHandoffMode(mode) === DEFAULT_HANDOFF_MODE;
}

export function manualHandoffEnabled(mode = DEFAULT_HANDOFF_MODE) {
  return validateHandoffMode(mode) !== "off";
}
