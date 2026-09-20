import { test } from "node:test";
import assert from "node:assert/strict";

import {
  automaticHandoffEnabled,
  DEFAULT_HANDOFF_MODE,
  manualHandoffEnabled,
  validateHandoffMode,
} from "../extensions/context-handoff/mode.mjs";

test("manual-only is the default; auto is opt-in and enables every path", () => {
  assert.equal(DEFAULT_HANDOFF_MODE, "manual-only");
  assert.equal(validateHandoffMode("auto"), "auto");
  assert.equal(automaticHandoffEnabled("auto"), true);
  assert.equal(manualHandoffEnabled("auto"), true);
});

test("manual-only suppresses automatic pressure handling but keeps manual tools", () => {
  assert.equal(automaticHandoffEnabled("manual-only"), false);
  assert.equal(manualHandoffEnabled("manual-only"), true);
});

test("calling with no mode argument uses the safe (manual-only) default", () => {
  assert.equal(automaticHandoffEnabled(), false);
  assert.equal(manualHandoffEnabled(), true);
});

test("off disables both automatic and manual handoff entry points", () => {
  assert.equal(automaticHandoffEnabled("off"), false);
  assert.equal(manualHandoffEnabled("off"), false);
});

test("invalid modes are rejected", () => {
  assert.throws(() => validateHandoffMode("sometimes"), /mode must be one of/);
});
