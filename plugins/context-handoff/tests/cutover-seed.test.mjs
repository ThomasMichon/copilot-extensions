import { test } from "node:test";
import assert from "node:assert/strict";
import {
  MAX_CUTOVER_SEED_LENGTH,
  buildCutoverSeed,
  extractRecoveryLocatorFromPrompt,
  leadFrom,
  parseRecoveryLocator,
  recoveryLocatorFor,
} from "../extensions/context-handoff/cutover-seed.mjs";

test("leadFrom preserves a stable task-first title lead", () => {
  assert.equal(leadFrom("Fix the widget"), "Task: Fix the widget");
  assert.equal(leadFrom("Continue: Fix it"), "Task: Fix it");
  assert.equal(leadFrom(""), "Task: Continue the current work");
  assert.equal(
    leadFrom('Continue: Fix "quoted" | locator'),
    'Task: Fix "quoted" locator',
  );
});

test("task seed is a bounded ASCII three-part locator", () => {
  const seed = buildCutoverSeed(
    "task", "task-42", leadFrom("Fix the widget"),
  );
  assert.equal(
    seed,
    "Task: Fix the widget | Resume: /consume-handoff to take over | " +
      "Recovery: context-handoff task:task-42",
  );
  assert.equal(seed.split(" | ").length, 3);
  assert.ok(seed.length <= MAX_CUTOVER_SEED_LENGTH);
  assert.ok(!seed.includes("\n"));
  assert.ok(!/[^\x00-\x7F]/.test(seed));
  assert.doesNotMatch(seed, /node -e|handoff-cli/);

  const directLead = buildCutoverSeed(
    "task", "task-42", "Task: Fix parser | preserve contract",
  );
  assert.equal(directLead.split(" | ").length, 3);
  assert.match(directLead, /^Task: Fix parser preserve contract \| Resume:/);
});

test("file seed carries one short opaque recovery locator", () => {
  const locator = recoveryLocatorFor("file", "handoff-1");
  const seed = buildCutoverSeed("file", "handoff-1", leadFrom("Continue"));
  assert.equal(locator, "file:handoff-1");
  assert.ok(seed.endsWith(`Recovery: context-handoff ${locator}`));
  assert.ok(!seed.includes("## Session Continuation"));
  assert.ok(seed.length <= MAX_CUTOVER_SEED_LENGTH);
});

test("recovery locators round-trip without paths or shell syntax", () => {
  assert.deepEqual(parseRecoveryLocator("task:abc_123-xyz"), {
    kind: "task",
    id: "abc_123-xyz",
  });
  assert.deepEqual(parseRecoveryLocator("file:handoff-session.1"), {
    kind: "file",
    id: "handoff-session.1",
  });
});

test("recovery locators reject unsafe or ambiguous identifiers", () => {
  assert.throws(() => recoveryLocatorFor("other", "id"), /unsupported/);
  assert.throws(() => recoveryLocatorFor("task", "two words"), /unsafe/);
  assert.throws(() => recoveryLocatorFor("file", "caf\u00e9"), /ASCII/);
  assert.throws(() => parseRecoveryLocator("task:"), /non-empty|unsafe/);
});

test("long titles are compacted without changing the recovery locator", () => {
  const locator = "task:task-99";
  const seed = buildCutoverSeed(
    "task",
    "task-99",
    leadFrom("x".repeat(3000)),
  );
  assert.ok(seed.length <= MAX_CUTOVER_SEED_LENGTH);
  assert.ok(seed.endsWith(`Recovery: context-handoff ${locator}`));
});

test("an impossible recovery locator fails instead of truncating it", () => {
  assert.throws(
    () => buildCutoverSeed(
      "task", "x".repeat(MAX_CUTOVER_SEED_LENGTH), leadFrom("x"),
    ),
    /exceeds 200/,
  );
});

test("recovery locators reject non-ASCII instead of corrupting it", () => {
  assert.throws(
    () => buildCutoverSeed(
      "file", "caf\u00e9", leadFrom("x"),
    ),
    /single-line ASCII/,
  );
});

// -- Phase 3 item 3: the deterministic "did my launch actually land" signal --

test("extractRecoveryLocatorFromPrompt finds the locator in a real seed", () => {
  const seed = buildCutoverSeed("task", "abc123", leadFrom("Fix the XSS bug"));
  assert.deepEqual(extractRecoveryLocatorFromPrompt(seed), { kind: "task", id: "abc123" });
});

test("extractRecoveryLocatorFromPrompt finds the locator embedded in a longer message", () => {
  const seed = buildCutoverSeed("file", "handoff-1", leadFrom("Continue"));
  const message = `Some preamble text.\n\n${seed}\n\nSome trailing text.`;
  assert.deepEqual(
    extractRecoveryLocatorFromPrompt(message),
    { kind: "file", id: "handoff-1" },
  );
});

test("extractRecoveryLocatorFromPrompt returns null for ordinary conversation", () => {
  assert.equal(extractRecoveryLocatorFromPrompt("please fix the login bug"), null);
  assert.equal(extractRecoveryLocatorFromPrompt(""), null);
  assert.equal(extractRecoveryLocatorFromPrompt(null), null);
  assert.equal(extractRecoveryLocatorFromPrompt(undefined), null);
});

test("extractRecoveryLocatorFromPrompt rejects a malformed locator rather than throwing", () => {
  assert.equal(
    extractRecoveryLocatorFromPrompt("Recovery: context-handoff not-a-real-kind:abc"),
    null,
  );
});

test("extractRecoveryLocatorFromPrompt stops at the first whitespace (never captures trailing text)", () => {
  assert.deepEqual(
    extractRecoveryLocatorFromPrompt("Recovery: context-handoff task:abc trailing words"),
    { kind: "task", id: "abc" },
  );
});

test("extractRecoveryLocatorFromPrompt round-trips recoveryLocatorFor", () => {
  const locator = recoveryLocatorFor("task", "8b270b8e-c165-494a");
  const message = `Recovery: context-handoff ${locator}`;
  assert.deepEqual(extractRecoveryLocatorFromPrompt(message), {
    kind: "task", id: "8b270b8e-c165-494a",
  });
});
