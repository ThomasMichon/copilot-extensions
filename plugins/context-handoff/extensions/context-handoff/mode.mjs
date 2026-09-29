export const AUTO_HANDOFF_MODE = "auto";
export const DEFAULT_HANDOFF_MODE = "manual-only";
export const HANDOFF_MODES = Object.freeze([
  AUTO_HANDOFF_MODE,
  DEFAULT_HANDOFF_MODE,
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
  return validateHandoffMode(mode) === AUTO_HANDOFF_MODE;
}

// Manual entry points (generate/save/trigger/consume, and their slash-command
// wrappers) are ALWAYS available, in every mode including `off` -- `off` only
// suppresses the automatic/unprompted behaviors below. Kept as a function
// (rather than inlining `true` at every call site) so callers read as an
// explicit policy decision, still validate the mode value, and have one place
// to change if this policy is ever revisited.
export function manualHandoffEnabled(mode = DEFAULT_HANDOFF_MODE) {
  validateHandoffMode(mode);
  return true;
}

// Automatic/unprompted behaviors -- context-pressure soft/hard nudges, the
// force tier's own auto-draft/store/trigger, and any other background
// behavior a session didn't explicitly ask for. `off` disables exactly this;
// `auto`/`manual-only` both leave it enabled (only `automaticHandoffEnabled`
// further gates live-cutover arming within it).
export function automaticPressureHandlingEnabled(mode = DEFAULT_HANDOFF_MODE) {
  return validateHandoffMode(mode) !== "off";
}
