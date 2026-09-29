import { test } from "node:test";
import assert from "node:assert/strict";

import {
  automaticHandoffEnabled,
  automaticPressureHandlingEnabled,
  DEFAULT_HANDOFF_MODE,
  manualHandoffEnabled,
  validateHandoffMode,
} from "../extensions/context-handoff/mode.mjs";

test("manual-only is the default; auto is opt-in and enables every path", () => {
  assert.equal(DEFAULT_HANDOFF_MODE, "manual-only");
  assert.equal(validateHandoffMode("auto"), "auto");
  assert.equal(automaticHandoffEnabled("auto"), true);
  assert.equal(manualHandoffEnabled("auto"), true);
  assert.equal(automaticPressureHandlingEnabled("auto"), true);
});

test("manual-only suppresses automatic pressure handling but keeps manual tools", () => {
  assert.equal(automaticHandoffEnabled("manual-only"), false);
  assert.equal(manualHandoffEnabled("manual-only"), true);
  assert.equal(automaticPressureHandlingEnabled("manual-only"), true);
});

test("calling with no mode argument uses the safe (manual-only) default", () => {
  assert.equal(automaticHandoffEnabled(), false);
  assert.equal(manualHandoffEnabled(), true);
  assert.equal(automaticPressureHandlingEnabled(), true);
});

test("off disables only automatic/unprompted behavior -- manual entry points always work", () => {
  // Revised design decision: `off` used to refuse every extension-provided
  // manual entry point too (a "Phase 4 judgment call"). That conflated two
  // different things -- automatic/unprompted behavior (pressure nudges, the
  // force tier) vs. a session/operator explicitly reaching for the
  // mechanism -- under one flag. `off` now disables only the former.
  assert.equal(automaticHandoffEnabled("off"), false);
  assert.equal(automaticPressureHandlingEnabled("off"), false);
  assert.equal(manualHandoffEnabled("off"), true);
});

test("invalid modes are rejected", () => {
  assert.throws(() => validateHandoffMode("sometimes"), /mode must be one of/);
});
