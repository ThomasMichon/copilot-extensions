import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_THRESHOLDS,
  contextPressure,
  formatContextUsage,
  validateThresholds,
} from "../extensions/context-handoff/thresholds.mjs";

test("large windows use percentage-based thresholds", () => {
  const below = contextPressure(549_999, 1_000_000);
  const soft = contextPressure(550_000, 1_000_000);
  const hard = contextPressure(700_000, 1_000_000);
  const force = contextPressure(790_000, 1_000_000);

  assert.equal(below.soft, false);
  assert.equal(soft.soft, true);
  assert.equal(soft.hard, false);
  assert.equal(hard.hard, true);
  assert.equal(hard.force, false);
  assert.equal(force.force, true);
  assert.equal(soft.softThreshold, 550_000);
  assert.equal(hard.hardThreshold, 700_000);
  assert.equal(force.forceThreshold, 790_000);
});

test("configured percentages override defaults", () => {
  const pressure = contextPressure(
    130_000,
    200_000,
    { softPercent: 65, hardPercent: 75, forcePercent: 78 },
  );

  assert.equal(pressure.softThreshold, 130_000);
  assert.equal(pressure.hardThreshold, 150_000);
  assert.equal(pressure.forceThreshold, 156_000);
  assert.equal(pressure.soft, true);
  assert.equal(pressure.hard, false);
  assert.equal(pressure.force, false);
});

test("percentage fallback never fires below its exact boundary", () => {
  const below = contextPressure(110, 201);
  const at = contextPressure(111, 201);

  assert.equal(below.softThreshold, 111);
  assert.equal(below.soft, false);
  assert.equal(at.soft, true);
});

test("unknown window size does not invent an absolute threshold", () => {
  const pressure = contextPressure(1_000_000, 0);

  assert.equal(pressure.softThreshold, null);
  assert.equal(pressure.hardThreshold, null);
  assert.equal(pressure.forceThreshold, null);
  assert.equal(pressure.soft, false);
  assert.equal(pressure.hard, false);
  assert.equal(pressure.force, false);
});

test("unknown window size is rendered without a misleading zero limit", () => {
  assert.deepEqual(formatContextUsage(150_000, 0), {
    utilization: "unknown",
    tokens: "150,000 tokens; limit unknown",
  });
});

test("threshold validation preserves the pre-compaction margin", () => {
  assert.deepEqual(validateThresholds(DEFAULT_THRESHOLDS), DEFAULT_THRESHOLDS);
  assert.throws(
    () => validateThresholds({ softPercent: 70, hardPercent: 70, forcePercent: 79 }),
    /softPercent must be less than hardPercent/,
  );
  assert.throws(
    () => validateThresholds({ softPercent: 55, hardPercent: 80, forcePercent: 85 }),
    /hardPercent must be an integer from 1 through 79/,
  );
  assert.throws(
    () => validateThresholds({ softPercent: 55, hardPercent: 70, forcePercent: 70 }),
    /hardPercent must be less than forcePercent/,
  );
});
