// When native context management is selected, the host owns its own
// checkpoint cadence. Its context accounting can differ from the
// session.usage_info estimate this plugin sees (for example when the host
// routes turns across models with different windows), so plugin-originated
// threshold nudges would compete with the host controller. "defer" (default)
// stays silent and lets the host drive; "nudge" restores plugin nudges.
export const NATIVE_PRESSURE_DEFER = "defer";
export const NATIVE_PRESSURE_MODES = Object.freeze([NATIVE_PRESSURE_DEFER, "nudge"]);
export const DEFAULT_NATIVE_PRESSURE = NATIVE_PRESSURE_DEFER;

export function validateNativePressure(value, source = "native_pressure") {
  if (!NATIVE_PRESSURE_MODES.includes(value)) {
    throw new Error(
      `${source}: native_pressure must be one of ` +
      NATIVE_PRESSURE_MODES.map((mode) => `'${mode}'`).join(", "),
    );
  }
  return value;
}

export function nativePressureDeferred(observation, nativePressure = DEFAULT_NATIVE_PRESSURE) {
  return Boolean(observation?.native) &&
    validateNativePressure(nativePressure) === NATIVE_PRESSURE_DEFER;
}
