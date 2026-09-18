import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_THRESHOLDS,
  contextPressure,
  formatConfiguredThreshold,
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

test("absolute token thresholds do not need a reported token limit", () => {
  const pressure = contextPressure(
    90_000,
    0,
    { softTokens: 50_000, hardTokens: 75_000, forceTokens: 95_000 },
  );

  assert.equal(pressure.softThreshold, 50_000);
  assert.equal(pressure.hardThreshold, 75_000);
  assert.equal(pressure.forceThreshold, 95_000);
  assert.equal(pressure.soft, true);
  assert.equal(pressure.hard, true);
  assert.equal(pressure.force, false);
});

test("mixed percent and token thresholds resolve against the live token limit", () => {
  const pressure = contextPressure(
    70_000,
    100_000,
    { softTokens: 50_000, hardPercent: 70, forceTokens: 90_000 },
  );

  assert.equal(pressure.softThreshold, 50_000);
  assert.equal(pressure.hardThreshold, 70_000);
  assert.equal(pressure.forceThreshold, 90_000);
  assert.equal(pressure.soft, true);
  assert.equal(pressure.hard, true);
  assert.equal(pressure.force, false);
});

test("mixed thresholds validate ordering once a token limit is known", () => {
  assert.deepEqual(
    validateThresholds(
      { softTokens: 50_000, hardPercent: 70, forceTokens: 90_000 },
      "thresholds",
    ),
    { softTokens: 50_000, hardPercent: 70, forceTokens: 90_000 },
  );
  assert.throws(
    () => contextPressure(
      0,
      60_000,
      { softTokens: 50_000, hardPercent: 70, forcePercent: 79 },
    ),
    /soft threshold .* must be less than hard threshold/,
  );
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

test("configured threshold formatter names the active unit", () => {
  assert.equal(
    formatConfiguredThreshold({ softPercent: 55, hardPercent: 70, forcePercent: 79 }, "hard"),
    "70%",
  );
  assert.equal(
    formatConfiguredThreshold({ softTokens: 50_000, hardTokens: 75_000, forceTokens: 90_000 }, "force"),
    "90,000 tokens",
  );
});
