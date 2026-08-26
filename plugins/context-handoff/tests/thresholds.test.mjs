import test from "node:test";
import assert from "node:assert/strict";

import {
  HARD_TOKEN_CAP,
  SOFT_TOKEN_CAP,
  contextPressure,
} from "../extensions/context-handoff/thresholds.mjs";

test("large windows use cost-aware absolute token caps", () => {
  const below = contextPressure(SOFT_TOKEN_CAP - 1, 1_000_000);
  const soft = contextPressure(SOFT_TOKEN_CAP, 1_000_000);
  const hard = contextPressure(HARD_TOKEN_CAP, 1_000_000);

  assert.equal(below.soft, false);
  assert.equal(soft.soft, true);
  assert.equal(soft.hard, false);
  assert.equal(hard.hard, true);
  assert.equal(soft.softThreshold, 150_000);
  assert.equal(hard.hardThreshold, 250_000);
});

test("small windows retain percentage-based compaction safety", () => {
  const pressure = contextPressure(110_000, 200_000);

  assert.equal(pressure.softThreshold, 110_000);
  assert.equal(pressure.hardThreshold, 140_000);
  assert.equal(pressure.soft, true);
  assert.equal(pressure.hard, false);
});

test("unknown window size falls back to absolute caps", () => {
  const pressure = contextPressure(150_000, 0);

  assert.equal(pressure.softThreshold, 150_000);
  assert.equal(pressure.hardThreshold, 250_000);
  assert.equal(pressure.soft, true);
  assert.equal(pressure.hard, false);
});
