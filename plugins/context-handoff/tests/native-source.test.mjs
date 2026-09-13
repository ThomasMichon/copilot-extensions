import assert from "node:assert/strict";
import { test } from "node:test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { prepareObservationHandoff } from "../extensions/context-handoff/native-observation.mjs";

// Exercise the actual caller AND core's execFileSync path. Only the executable
// locator is replaced: an owned Node child emits the mux CLI protocol and exits.
const core = readFileSync(new URL(
  "../extensions/context-handoff/handoff-core.mjs", import.meta.url,
), "utf8");
const runCliSource = core.slice(core.indexOf("export function runCli("),
  core.indexOf("// True if an agent-dispatch")).replace("export ", "");
const source = readFileSync(new URL(
  "../extensions/context-handoff/native-source.mjs", import.meta.url,
), "utf8").replace(/^import[\s\S]*?from "[^"]+";\n/gm, "")
  .replaceAll("export ", "").replace("import.meta.url", '"file:///owned/native-source.mjs"');

function fixture({ status = 0, output = '{"ok":true,"new_pane":"%5"}',
  permissionMode = "allow-all", herdr = false, onExit = () => {},
  lifecycle = () => ({ managed: false }), tasks = [] } = {}) {
  let record = {
    sessionId: "source", handoffId: "token", seed: "owned seed",
    nativeGoal: {
      phase: "frozen", sourceObjectiveId: null, successorSessionId: "target",
      cwd: process.cwd(), permissionMode,
    },
  };
  const calls = [];
  const pauses = [];
  const context = vm.createContext({
    prepareObservationHandoff,
    process, JSON, dirname: () => "/owned", join: (...parts) => parts.join("/"),
    fileURLToPath: value => value,
    execFileSync: (bin, args, options) => {
      calls.push({ bin, args });
      try {
        return execFileSync(bin, ["-e",
          `process.stdout.write(${JSON.stringify(output)}); process.exit(${status});`,
        ], options);
      } finally {
        onExit(record);
      }
    },
    resolveSystemCliDescriptor: () => ({ path: process.execPath }),
    readSessionStateHandoff: () => ({ path: "owned-checkpoint", record }),
    writeJsonAtomic: (_path, value) => { record = structuredClone(value); },
    readNativeGoal: async () => ({ state: null }),
    isHerdrPane: () => herdr,
    launchHerdrSuccessor: () => assert.fail("No Herdr launch expected"),
    workerLifecycle: lifecycle,
  });
  vm.runInContext(`${runCliSource}\n${source}
    globalThis.request = requestNativeCutover;
    globalThis.launch = freezeAndLaunchNative;`, context);
  const session = { sessionId: "source", rpc: {
    tasks: { list: async () => ({ tasks }) },
    mode: {
      get: async () => "interactive",
      set: async value => { pauses.push(value); return {}; },
    },
    permissions: { getMode: async () => ({ mode: permissionMode }) },
    model: { getCurrent: async () => ({ modelId: "owned-model" }) },
    agent: { getCurrent: async () => ({ agent: null }) },
  } };
  return {
    record: () => record, calls, pauses,
    request: () => context.request(session, record.seed),
    launch: () => context.launch(session, "owned-checkpoint"),
  };
}

test("managed registry preparation precedes the first receiver launch and preparation failure spawns nothing", async () => {
  const order = [];
  const selectors = { managed: true, owner_id: "stable-owner", mode: "off", depth: 1 };
  const f = fixture({
    lifecycle: (_record, _path, action) => {
      assert.equal(action, "handoff-prepare");
      order.push("registry-prepared");
      return selectors;
    },
    onExit: record => {
      order.push("receiver-created");
      assert.deepEqual(record.nativeGoal.workerLifecycle, selectors);
    },
  });

  await f.launch();
  assert.deepEqual(order, ["registry-prepared", "receiver-created"]);
  const failed = fixture({ lifecycle: () => { throw new Error("registry prepare failed"); } });
  await assert.rejects(failed.launch(), /registry prepare failed/);
  assert.equal(failed.calls.length, 0);
  assert.equal(failed.record().nativeGoal.launchRequested, undefined);
});

test("actual cutover refuses attached work before pause, arming or launch", async () => {
  const f = fixture({ tasks: [
    { type: "shell", attachmentMode: "attached", status: "running", id: "original-work" },
  ] });
  const before = structuredClone(f.record());
  await assert.rejects(f.request(), /Undeclared attached shells: original-work/);
  assert.deepEqual(f.record(), before);
  assert.deepEqual(f.pauses, []);
  assert.deepEqual(f.calls, []);
});

test("rc4 retained receiver publishes the receipt and retry never spawns again", async () => {
  const receipt = {
    ok: false, new_pane: "%5", prompt_received: true,
    candidate_status: "session-association-timeout",
    error: "successor did not create a token-associated Copilot session",
  };
  const f = fixture({
    status: 4, output: JSON.stringify(receipt),
    onExit: record => { record.nativeGoal.receiverObservation = "preserve concurrent write"; },
  });
  assert.deepEqual(await f.launch(), receipt);
  assert.deepEqual(f.record().nativeGoal.launch, receipt);
  assert.equal(f.record().nativeGoal.launchRequested, true);
  assert.equal(f.record().nativeGoal.receiverObservation, "preserve concurrent write");
  assert.deepEqual((await f.request()).launch, receipt);
  assert.deepEqual(await f.launch(), receipt);
  assert.equal(f.calls.length, 1);
});

test("rc3 prelaunch rejection clears the request and permits an explicit retry", async () => {
  const f = fixture({
    status: 3, output: JSON.stringify({ ok: false, error: "no mux session wt-owned; not under mux" }),
  });
  await assert.rejects(f.launch(), /no mux session/);
  assert.equal(f.record().nativeGoal.launchRequested, false);
  assert.equal(f.record().nativeGoal.launch, undefined);
  assert.equal((await f.request()).pending, true);
  await assert.rejects(f.launch(), /no mux session/);
  assert.equal(f.calls.length, 2);
});

test("unknown or malformed child outcomes preserve the request and forbid blind retry", async () => {
  for (const [status, output] of [
    [4, '{"ok":false,"new_pane":null,"error":"mux timeout"}'],
    [3, "not JSON"],
    [4, "{}"],
    [7, '{"ok":false,"error":"unknown transport outcome"}'],
  ]) {
    const f = fixture({ status, output });
    await assert.rejects(f.launch());
    assert.equal(f.record().nativeGoal.launchRequested, true);
    assert.equal(f.record().nativeGoal.launch, undefined);
    await assert.rejects(f.request(), /unresolved/);
    await assert.rejects(f.launch(), /unresolved/);
    assert.equal(f.calls.length, 1);
  }
});

test("native manual/assisted requests on either host reject before pause or arming", async () => {
  for (const herdr of [false, true]) {
    for (const permissionMode of ["manual", "assisted"]) {
      const f = fixture({ permissionMode, herdr });
      const before = structuredClone(f.record());
      await assert.rejects(f.request(), /cannot preserve/);
      assert.deepEqual(f.record(), before);
      assert.equal(f.pauses.length, 0);
      assert.equal(f.calls.length, 0);
      await assert.rejects(f.launch(), /cannot preserve/);
      assert.deepEqual(f.record(), before);
      assert.equal(f.calls.length, 0);
      // A receipt written by an older, permissive source is not acceptance.
      f.record().nativeGoal.launch = { ok: true, new_pane: "%old" };
      await assert.rejects(f.request(), /cannot preserve/);
      await assert.rejects(f.launch(), /cannot preserve/);
      assert.equal(f.pauses.length, 0);
      assert.equal(f.calls.length, 0);
    }
  }
});

test("allow-all native requests still pause, launch once and reuse the receipt", async () => {
  const f = fixture();
  assert.equal((await f.request()).pending, true);
  assert.equal(f.pauses.length, 1);
  assert.equal(f.record().nativeGoal.cutoverRequested, true);
  assert.equal((await f.launch()).new_pane, "%5");
  assert.equal((await f.request()).launch.new_pane, "%5");
  assert.equal(f.calls.length, 1);
});
