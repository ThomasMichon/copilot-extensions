import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { assertNativeProfile, bindNativeSuccessor } from "../extensions/context-handoff/native-runtime.mjs";

test("mux admission requires token binding and verified head, not a candidate", () => {
  const record = {
    worktree: "owned", worktreeDir: "/owned", handoffId: "token",
    nativeGoal: { launchTransport: "mux" },
  };
  const calls = [];
  bindNativeSuccessor(record, "target", (command, args) => {
    calls.push([command, args]);
    return JSON.stringify({ bound: true, head_session: "target", handoff_token: "token" });
  });
  assert.deepEqual(calls, [["agent-worktrees", [
    "bind-session", "--session-id", "target", "--worktree-id", "owned", "--handoff-token", "token",
  ]]]);
  assert.throws(() => bindNativeSuccessor(record, "target", () =>
    JSON.stringify({ bound: true, head_session: "source", handoff_token: "token" })),
  /acknowledged worktree head/);
  bindNativeSuccessor({ ...record, nativeGoal: { launchTransport: "herdr" } }, "target", () =>
    assert.fail("Herdr must not create a parallel ownership registry"));
});

test("profile assertion checks model, agent and permissions without changing them", async () => {
  const model = { modelId: "gpt-5.4-mini", reasoningEffort: "low", contextTier: "default" };
  const goal = { profile: { model, agentId: null }, permissionMode: "manual" };
  let permissionMode = "manual";
  const session = { rpc: {
    model: { getCurrent: async () => model },
    agent: { getCurrent: async () => ({ agent: null }) },
    permissions: { getMode: async () => ({ mode: permissionMode }) },
  } };
  await assertNativeProfile(session, goal);
  permissionMode = "allow-all";
  await assert.rejects(assertNativeProfile(session, goal), /permissions differ/);
});

test("queued send waits for its native user event before admission and never resends", async () => {
  let record = {
    kind: "context-handoff-session-request", consumed: true,
    nativeGoal: {
      successorSessionId: "target", hydratedBySession: "target",
      consumedBySession: "target", intent: "running",
      continuationRequested: true, continuationMessageId: "queued-id",
      continuationPrompt: "Handoff continuation token: owned brief",
    },
  };
  const source = readFileSync(new URL(
    "../extensions/context-handoff/native-runtime.mjs", import.meta.url,
  ), "utf8").replace(/^import[\s\S]*?from "[^"]+";\n/gm, "").replaceAll("export ", "");
  const context = vm.createContext({
    readFileSync: () => JSON.stringify(record),
    writeJsonAtomic: (_path, value) => { record = structuredClone(value); },
    runCli: () => assert.fail("No host retirement before native submission"),
    readNativeGoal: () => assert.fail("Do not reactivate an accepted send"),
  });

  vm.runInContext(`${source}\nglobalThis.continue = continueNativeAfterAdmission;`, context);
  const events = [];
  const session = {
    sessionId: "target",
    getEvents: async () => events,
    send: async () => assert.fail("Never send again"),
  };
  await context.continue(session, "owned-checkpoint");
  assert.equal(record.nativeGoal.admissionComplete, undefined);
  assert.equal(record.consumed, true);
  events.push({ id: "native-event", type: "user.message", data: {
    messageId: "queued-id", content: record.nativeGoal.continuationPrompt,
  } });
  await context.continue(session, "owned-checkpoint");
  assert.equal(record.nativeGoal.admissionComplete, true);
  assert.equal(record.consumed, true);
  await context.continue(session, "owned-checkpoint");
});

test("receiver does not write preparation while the source is publishing launch", async () => {
  let record = {
    kind: "context-handoff-session-request",
    nativeGoal: {
      successorSessionId: "target", phase: "frozen",
      launchTransport: "herdr", launchRequested: true,
    },
  };
  let changed;
  let prepared = false;
  const source = readFileSync(new URL(
    "../extensions/context-handoff/native-runtime.mjs", import.meta.url,
  ), "utf8").replace(/^import[\s\S]*?from "[^"]+";\n/gm, "").replaceAll("export ", "");
  const context = vm.createContext({
    process: { env: {} },
    readFileSync: () => JSON.stringify(record),
    dirname: () => "owned-session",
    watch: (_directory, listener) => {
      changed = listener;
      return { close() {}, on() {} };
    },
    prepareNativeGoal: async () => { prepared = true; },
  });
  vm.runInContext(`${source}\nglobalThis.bootstrap = bootstrapNativeHandoff;`, context);
  const pending = context.bootstrap({ sessionId: "target" }, {
    CONTEXT_HANDOFF_NATIVE_CHECKPOINT: "owned-checkpoint",
    CONTEXT_HANDOFF_NATIVE_PHASE: "prepare",
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(prepared, false);
  record.nativeGoal.launch = { ok: true, new_session: "target" };
  changed();
  await pending;
  assert.equal(prepared, true);
});

test("lost send acknowledgement reconciles one exact event or stops without replay", async () => {
  let record = {
    kind: "context-handoff-session-request", consumed: false,
    nativeGoal: {
      successorSessionId: "target", hydratedBySession: "target",
      consumedBySession: "target", intent: "running",
      continuationRequested: true, continuationPrompt: "Owned unique continuation",
    },
  };
  const source = readFileSync(new URL(
    "../extensions/context-handoff/native-runtime.mjs", import.meta.url,
  ), "utf8").replace(/^import[\s\S]*?from "[^"]+";\n/gm, "").replaceAll("export ", "");
  const context = vm.createContext({
    readFileSync: () => JSON.stringify(record),
    writeJsonAtomic: (_path, value) => { record = structuredClone(value); },
    runCli: () => assert.fail("No host action"),
  });
  vm.runInContext(`${source}\nglobalThis.continue = continueNativeAfterAdmission;`, context);
  const events = [{ id: "unrelated", type: "user.message", data: { content: "other" } }];
  const session = {
    sessionId: "target", getEvents: async () => events,
    send: async () => assert.fail("Unknown send must never be replayed"),
  };
  await assert.rejects(context.continue(session, "checkpoint"), /unresolved/);
  assert.equal(record.consumed, false);
  events.push({ id: "observed", type: "user.message", data: {
    content: record.nativeGoal.continuationPrompt, messageId: "accepted",
  } });
  await context.continue(session, "checkpoint");
  assert.equal(record.nativeGoal.continuationObserved, "observed");
  assert.equal(record.nativeGoal.admissionComplete, true);
  await context.continue(session, "checkpoint");
  assert.equal(events.length, 2);
});
