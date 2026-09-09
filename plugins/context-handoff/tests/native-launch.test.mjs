import assert from "node:assert/strict";
import { test } from "node:test";
import { nativeLaunchArguments } from "../extensions/context-handoff/native-launch.mjs";

const record = {
  handoffId: "handoff-source",
  seed: "Consume the existing baton",
  nativeGoal: { successorSessionId: "target" },
};

test("preparation starts a named empty successor without an initial prompt", () => {
  const args = nativeLaunchArguments(record, "prepare", ["--session-id", "target", "--allow-all"]);
  assert.equal(args.filter(value => value === "--session-id").length, 1);
  assert.ok(args.includes("--name"));
  assert.ok(args.includes("--allow-all"));
  assert.ok(!args.includes("-i"));
  assert.ok(!args.includes("--resume"));
});

test("cold resume leaves admission to the runtime without starting a model turn", () => {
  const args = nativeLaunchArguments(record, "resume", ["--session-id", "target"]);
  assert.ok(!args.includes("--session-id"));
  assert.deepEqual(args.slice(0, 2), ["--resume", "target"]);
  assert.ok(!args.includes("-i"));
  assert.ok(!args.includes(record.seed));
});

test("interrupted empty receiver preparation resumes the same saved CLI identity", () => {
  const args = nativeLaunchArguments(record, "prepare", [], { receiverExists: true });
  assert.deepEqual(args, ["--resume", "target", "--mode", "interactive"]);
});

test("launcher refuses a different successor identity rather than generating another", () => {
  assert.throws(() => nativeLaunchArguments(record, "prepare", ["--session-id", "other"]), /identity disagrees/);
});

test("source model and agent replace launcher defaults, without widening permissions", () => {
  const withProfile = {
    ...record,
    nativeGoal: {
      ...record.nativeGoal,
      profile: {
        model: { modelId: "gpt-5.4-mini", reasoningEffort: "low", contextTier: "default" },
        agentId: null,
      },
    },
  };
  const args = nativeLaunchArguments(withProfile, "prepare", [
    "--model=other", "--effort=xhigh", "--context", "long_context",
    "--agent=coordinator", "--plugin-dir", "/owned/path with spaces",
  ]);
  assert.ok(!args.includes("--allow-all"));
  assert.ok(!args.includes("--agent=coordinator"));
  assert.ok(!args.includes("long_context"));
  assert.ok(args.includes("/owned/path with spaces"));
  assert.deepEqual(args.slice(2, 8), [
    "--model", "gpt-5.4-mini", "--effort", "low", "--context", "default",
  ]);
});
