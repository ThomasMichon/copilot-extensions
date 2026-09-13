import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import vm from "node:vm";
import { observationBrief } from "../extensions/context-handoff/native-observation.mjs";
import { runNativeSuccessor } from "../extensions/context-handoff/native-launch.mjs";
import { retireHerdrPredecessor, advertiseWorkerLifecycle } from "../extensions/context-handoff/herdr.mjs";

test("paired reload refreshes an already managed frontend registration", t => {
  const root = mkdtempSync(join(tmpdir(), "native-registration-"));
  const keys = ["COPILOT_HOME", "HERDR_ENV", "HERDR_PANE_ID"];
  const before = Object.fromEntries(keys.map(key => [key, process.env[key]]));
  t.after(() => {
    for (const key of keys) {
      if (before[key] === undefined) delete process.env[key];
      else process.env[key] = before[key];
    }
    rmSync(root, { recursive: true, force: true });
  });
  Object.assign(process.env, { COPILOT_HOME: root, HERDR_ENV: "1", HERDR_PANE_ID: "owned-pane" });
  mkdirSync(join(root, "worker-lifecycle"));
  writeFileSync(join(root, "worker-lifecycle", "installation.json"), "{}");
  const files = join(root, "session-state", "owned-session", "files");
  mkdirSync(files, { recursive: true });
  writeFileSync(join(files, "worker-lifecycle.json"), "{}");
  let calls = 0;
  advertiseWorkerLifecycle("owned-session", (_command, args) => {
    calls++;
    assert.ok(args.includes("native-ready"));
    assert.equal(args[args.indexOf("--session") + 1], "owned-session");
  });
  assert.equal(calls, 1);
});

const runtime = readFileSync(new URL(
  "../extensions/context-handoff/native-runtime.mjs", import.meta.url,
), "utf8").replace(/^import[\s\S]*?from "[^"]+";\n/gm, "").replaceAll("export ", "");

function fixture({ failure = null, running = false } = {}) {
  let record = {
    kind: "context-handoff-session-request", handoffId: "fixed-handoff",
    storage: "file", handoffPath: "owned-baton", promptText: "same remaining objective",
    predecessor: { transport: "herdr" },
    nativeGoal: {
      sourceSessionId: "source", successorSessionId: "receiver", phase: "hydrated",
      hydratedBySession: "receiver", intent: running ? "running" : "stopped",
      launchTransport: "herdr", launchRequested: true, launch: { new_pane: "owned-receiver" },
      workerLifecycle: { managed: true, owner_id: "stable-worker", generation: 0, mode: "off", depth: 1 },
    },
  };
  const trace = [];
  let consumeCount = 0;
  let closeCount = 0;
  let failureRemaining = failure;
  const fail = stage => {
    if (failureRemaining === stage) {
      failureRemaining = null;
      throw new Error(`injected ${stage} failure`);
    }
  };
  const session = {
    sessionId: "receiver",
    rpc: { workspaces: { createFile: async () => { trace.push("brief"); fail("admission"); } } },
    getEvents: async () => [],
    send: async () => assert.fail("These stopped/recovery fixtures must not create a model continuation"),
  };
  const context = vm.createContext({
    observationBrief,
    process: { env: {} },
    advertiseWorkerLifecycle: () => {},
    readFileSync: () => JSON.stringify(record),
    writeJsonAtomic: (_path, value) => { record = structuredClone(value); },
    runCli: () => assert.fail("Only the assigned retirement adapter may close a source"),
    readNativeGoal: () => assert.fail("Recovery must not reactivate or replace the objective"),
    consumeFileHandoff: () => {
      consumeCount++;
      record.consumed = true;
      record.nativeGoal.consumedBySession = "receiver";
      trace.push("consume");
      return { ok: true };
    },
    workerLifecycle: (_record, _path, action, sid) => {
      assert.equal(sid, "receiver");
      trace.push(action);
      fail(action);
      return { managed: true };
    },
    retireHerdrPredecessor: () => {
      trace.push("retire");
      fail("retire");
      closeCount++;
      return { retired: true };
    },
  });
  vm.runInContext(`${runtime}
    globalThis.bootstrap = bootstrapNativeHandoff;
    globalThis.continueNative = continueNativeAfterAdmission;
    globalThis.retire = retireNativePredecessor;
    globalThis.noteFailure = recordNativeReceiverFailure;`, context);
  const env = {
    CONTEXT_HANDOFF_NATIVE_CHECKPOINT: "fixed-checkpoint",
    CONTEXT_HANDOFF_NATIVE_PHASE: "resume",
  };
  return {
    context, session, env, trace, record: () => record,
    counts: () => ({ consumeCount, closeCount }),
    bootstrap: () => context.bootstrap(session, env),
  };
}

test("managed native admission commits ownership after hydration/consumption and before physical retirement", async () => {
  const f = fixture();
  assert.throws(() => f.context.retire("fixed-checkpoint", "receiver"), /admission is incomplete/);
  assert.deepEqual(f.trace, []);
  await f.bootstrap();
  assert.deepEqual(f.trace, ["brief", "consume", "handoff-commit", "retire", "handoff-retired"]);
  assert.equal(f.record().nativeGoal.admissionComplete, true);
  assert.equal(f.record().nativeGoal.workerLifecycle.owner_id, "stable-worker");
  assert.equal(f.record().nativeGoal.closed, undefined);
  await f.bootstrap();
  assert.deepEqual(f.trace, ["brief", "consume", "handoff-commit", "retire", "handoff-retired"]);
});

test("native Herdr owner checks the lifecycle source identity and recovers a lost close receipt", () => {
  const previous = { HERDR_ENV: process.env.HERDR_ENV, HERDR_PANE_ID: process.env.HERDR_PANE_ID };
  process.env.HERDR_ENV = "1";
  process.env.HERDR_PANE_ID = "receiver-pane";
  try {
    const record = {
      predecessor: { paneId: "source-pane", sessionId: "source", terminalId: "source-terminal" },
      nativeGoal: { sourceSessionId: "source", cwd: process.cwd(), workerLifecycle: { managed: true } },
    };
    const calls = [];
    const recovered = retireHerdrPredecessor(record, "receiver", (_bin, args) => {
      calls.push(args);
      assert.deepEqual(args.slice(0, 2), ["lifecycle", "--config-home"]);
      assert.equal(typeof args[2], "string");
      assert.deepEqual(args.slice(3), [
        "handoff-retire-check", "--checkpoint", "fixed-checkpoint", "--session", "receiver",
      ]);
      return JSON.stringify({ managed: true, source_exited: true });
    }, "fixed-checkpoint");
    assert.equal(recovered.alreadyGone, true);
    assert.equal(calls.length, 1, "no second physical close after exact source exit was proved");
    assert.throws(() => retireHerdrPredecessor(record, "receiver", () => {
      throw new Error("source process family replaced");
    }, "fixed-checkpoint"), /source process family replaced/);
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});

for (const failure of ["admission", "handoff-commit", "retire", "handoff-retired"]) {
  test(`fixed receiver recovers ${failure} failure without consumption or business replay`, async () => {
    const f = fixture({ failure });
    await assert.rejects(f.bootstrap(), new RegExp(`injected ${failure}`));
    f.context.noteFailure("fixed-checkpoint", "receiver", new Error(`injected ${failure}`));
    const before = f.record();
    assert.equal(before.nativeGoal.successorSessionId, "receiver");
    assert.equal(before.nativeGoal.receiverFailure.message, `injected ${failure}`);
    if (failure === "handoff-commit") {
      assert.equal(before.nativeGoal.admissionComplete, undefined);
      assert.equal(f.counts().closeCount, 0);
    }
    if (failure === "retire") {
      assert.equal(before.nativeGoal.admissionComplete, true);
      assert.equal(f.counts().closeCount, 0);
    }
    await f.bootstrap();
    assert.deepEqual(f.counts(), { consumeCount: 1, closeCount: 1 });
    assert.equal(f.record().nativeGoal.retired.retired, true);
    assert.equal(f.record().nativeGoal.successorSessionId, "receiver");
  });
}

test("the actual retry tool routes consumed receiver recovery before source pending-baton lookup", async () => {
  const f = fixture({ failure: "handoff-commit" });
  await assert.rejects(f.bootstrap(), /handoff-commit/);
  Object.assign(f.context, {
    process: { env: f.env }, session: f.session,
    nativeStartup: Promise.resolve(), nativeStartupError: new Error("prior failure"),
    nativeReceiptPath: null,
    recoverPendingHandoff: () => assert.fail("Receiver recovery must not look for a new source baton"),
  });
  const extension = readFileSync(new URL(
    "../extensions/context-handoff/extension.mjs", import.meta.url,
  ), "utf8");
  const start = extension.indexOf("handler: async", extension.indexOf('name: "retry_handoff_cutover"'));
  const end = extension.indexOf("\n      },\n    },", start);
  vm.runInContext(`globalThis.retry = (${extension.slice(start + "handler: ".length, end)}\n});`, f.context);
  assert.match(await f.context.retry(), /Fixed native receiver recovered: receiver/);
  assert.equal(f.context.nativeStartupError, null);
  assert.deepEqual(f.counts(), { consumeCount: 1, closeCount: 1 });
});

test("running ownership cannot commit on a send ACK without its actual continuation event", async () => {
  const f = fixture({ running: true });
  Object.assign(f.record().nativeGoal, {
    consumedBySession: "receiver", continuationRequested: true,
    continuationMessageId: "fixed-message", continuationPrompt: "fixed continuation",
  });
  f.record().consumed = true;
  await f.context.continueNative(f.session, "fixed-checkpoint");
  assert.deepEqual(f.trace, []);
  assert.equal(f.record().nativeGoal.admissionComplete, undefined);
  f.session.getEvents = async () => [{
    id: "observed-event", type: "user.message",
    data: { content: "fixed continuation", messageId: "fixed-message" },
  }];
  await f.context.continueNative(f.session, "fixed-checkpoint");
  assert.deepEqual(f.trace, ["handoff-commit", "retire", "handoff-retired"]);
  assert.equal(f.record().nativeGoal.continuationObserved, "observed-event");
});

test("prepare and cold resume carry frozen off/depth selectors for one fixed receiver", () => {
  const dir = mkdtempSync(join(tmpdir(), "native-worker-selectors-"));
  try {
    const checkpoint = join(dir, "checkpoint.json");
    const record = {
      handoffId: "fixed", seed: "existing baton",
      nativeGoal: {
        sourceSessionId: "source", successorSessionId: "receiver", phase: "frozen",
        permissionMode: "allow-all", cwd: dir,
        workerLifecycle: { managed: true, config_home: dir, xdg_home: join(dir, "xdg"), mode: "off", depth: 1 },
      },
    };
    writeFileSync(checkpoint, JSON.stringify(record));
    const launches = [];
    const checks = [];
    runNativeSuccessor({
      checkpoint, cli: "hosted-cli", args: ["--session-id", "receiver", "--allow-all"],
      lifecycleCheck: (_record, _path, action, sid) => { checks.push([action, sid]); return { managed: true }; },
      spawn: (_cli, args) => {
        launches.push(args);
        record.nativeGoal.phase = "prepared";
        writeFileSync(checkpoint, JSON.stringify(record));
        return { status: 0 };
      },
    });
    assert.equal(launches.length, 2, "two processes are not two logical receivers");
    for (const args of launches) {
      assert.deepEqual(args.slice(0, 5), ["--worker-selectors", dir, join(dir, "xdg"), "off", "1"]);
      assert.ok(!args.includes("-i"));
    }
    assert.ok(launches[0].includes("--session-id"));
    assert.ok(launches[1].includes("--resume"));
    assert.deepEqual(checks, [["handoff-check", "receiver"], ["handoff-check", "receiver"]]);
    record.nativeGoal.admissionComplete = true;
    writeFileSync(checkpoint, JSON.stringify(record));
    assert.throws(() => runNativeSuccessor({
      checkpoint, cli: "hosted-cli",
      lifecycleCheck: () => { throw new Error("closed logical worker"); },
      spawn: () => assert.fail("No cold recovery after business acceptance closed this worker"),
    }), /closed logical worker/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
