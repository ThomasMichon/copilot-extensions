import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { nativeLaunchArguments } from "../extensions/context-handoff/native-launch.mjs";

const record = {
  handoffId: "handoff-source",
  seed: "Consume the existing baton",
  nativeGoal: { successorSessionId: "target", permissionMode: "allow-all" },
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

test("direct native launcher rejects unsupported modes before spawning a CLI", () => {
  for (const permissionMode of ["manual", "assisted"]) {
    for (const phase of ["frozen", "prepared"]) {
      const checkpoint = JSON.stringify({
        ...record, nativeGoal: { ...record.nativeGoal, permissionMode, phase },
      });
      // The real runner reads the checkpoint through fs; use the same runner
      // source with an in-memory filesystem, never a live session checkpoint.
      const source = readFileSync(new URL(
        "../extensions/context-handoff/native-launch.mjs", import.meta.url,
      ), "utf8").split('if (process.argv[1]')[0]
        .replace(/^import.*;\n/gm, "").replaceAll("export ", "");
      let spawns = 0;
      const context = vm.createContext({
        readFileSync: () => checkpoint,
        existsSync: () => false, process: { env: {} },
        homedir: () => "/owned", join: (...args) => args.join("/"),
      });
      vm.runInContext(`${source}\nglobalThis.run = runNativeSuccessor;`, context);
      assert.throws(() => context.run({
        checkpoint: "owned-checkpoint", cli: "owned-cli", args: ["--allow-all"],
        spawn: () => { spawns++; return { status: 0 }; },
      }), /cannot preserve/);
      assert.equal(spawns, 0);
    }
  }
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
