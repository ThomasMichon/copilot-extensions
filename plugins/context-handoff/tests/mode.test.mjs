import { test } from "node:test";
import assert from "node:assert/strict";

import {
  automaticHandoffEnabled,
  DEFAULT_HANDOFF_MODE,
  manualHandoffEnabled,
  validateHandoffMode,
} from "../extensions/context-handoff/mode.mjs";

test("auto remains the default and enables every path", () => {
  assert.equal(DEFAULT_HANDOFF_MODE, "auto");
  assert.equal(validateHandoffMode("auto"), "auto");
  assert.equal(automaticHandoffEnabled("auto"), true);
  assert.equal(manualHandoffEnabled("auto"), true);
});

test("manual-only suppresses automatic pressure handling but keeps manual tools", () => {
  assert.equal(automaticHandoffEnabled("manual-only"), false);
  assert.equal(manualHandoffEnabled("manual-only"), true);
});

test("off disables both automatic and manual handoff entry points", () => {
  assert.equal(automaticHandoffEnabled("off"), false);
  assert.equal(manualHandoffEnabled("off"), false);
});

test("invalid modes are rejected", () => {
  assert.throws(() => validateHandoffMode("sometimes"), /mode must be one of/);
});
