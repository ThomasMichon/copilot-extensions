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
  await assert.rejects(assertNativeProfile(session, goal),
    /permissionMode: expected "manual", observed "allow-all"/);
});

for (const order of ["after-startup", "during-getter", "already-selected", "default-agent",
  "different-agentId", "deselected-agentId", "different-modelId", "different-reasoningEffort",
  "different-contextTier", "different-permissionMode"]) {
  test(`native startup waits for actual agent selection without blocking the host: ${order}`, async () => {
    const model = { modelId: "gpt-6-astra", reasoningEffort: "xhigh", contextTier: "long_context" };
    let agentId = order === "already-selected" ? "coordinator" : null;
    const record = {
      kind: "context-handoff-session-request",
      nativeGoal: {
        successorSessionId: "target", phase: "frozen",
        profile: { model, agentId: order === "default-agent" ? null : "coordinator" },
        permissionMode: "allow-all",
      },
    };
    const listeners = new Set();
    const observedModel = { ...model };
    let permissionMode = "allow-all";
    let prepared = 0;
    let startup;
    const selected = () => {
      agentId = "coordinator";
      const key = order.split("-")[1];
      if (order.startsWith("different-")) {
        if (key === "agentId") agentId = "other-agent";
        else if (key === "permissionMode") permissionMode = "manual";
        else observedModel[key] = "different";
      }
      if (order === "deselected-agentId") agentId = null;
      for (const listener of listeners) listener({
        type: agentId ? "subagent.selected" : "subagent.deselected", data: { agentName: agentId },
      });
    };
    const session = {
      sessionId: "target",
      on: listener => { listeners.add(listener); return () => listeners.delete(listener); },
      rpc: {
        model: { getCurrent: async () => observedModel },
        agent: { getCurrent: async () => {
          const agent = agentId ? { id: agentId } : null;
          if (order === "during-getter" && !agent) selected();
          return { agent };
        } },
        permissions: { getMode: async () => ({ mode: permissionMode }) },
      },
    };
    const source = readFileSync(new URL(
      "../extensions/context-handoff/native-runtime.mjs", import.meta.url,
    ), "utf8").replace(/^import[\s\S]*?from "[^"]+";\n/gm, "").replaceAll("export ", "");
    const context = vm.createContext({
      process: { env: {} },
      readFileSync: () => JSON.stringify(record),
      prepareNativeGoal: async () => { prepared++; },
    });
    vm.runInContext(`${source}\nglobalThis.bootstrap = bootstrapNativeHandoff;`, context);
    // The host awaits extension startup before its UI can select the agent.
    // Match extension.mjs: retain the promise, never await it in this hook.
    await (async function hostStartup() {
      startup = context.bootstrap(session, {
        CONTEXT_HANDOFF_NATIVE_CHECKPOINT: "owned-checkpoint",
        CONTEXT_HANDOFF_NATIVE_PHASE: "prepare",
      });
    })();
    if (order === "after-startup" || order.includes("-agentId") || order.startsWith("different-")) {
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(prepared, 0, "a transient default agent must not reject or prepare");
      selected();
      selected(); // duplicate notifications must not bootstrap twice
    }
    if (order.startsWith("different-") || order === "deselected-agentId") {
      await assert.rejects(startup, new RegExp(`${order.split("-")[1]}: expected .* observed`));
      assert.equal(prepared, 0, "genuine differences must not write, consume, or retire");
    } else {
      await startup;
      assert.equal(prepared, 1);
    }
    assert.equal(listeners.size, 0);
  });
}

test("the actual extension initializer returns before native UI selection", { timeout: 1000 }, async () => {
  const source = readFileSync(new URL(
    "../extensions/context-handoff/extension.mjs", import.meta.url,
  ), "utf8");
  const start = source.indexOf("nativeStartup = bootstrapNativeHandoff(session)");
  const end = source.indexOf("if (handoffConfig.warning)", start);
  assert.ok(start >= 0 && end > start);
  let select;
  const selection = new Promise(resolve => { select = resolve; });
  const context = vm.createContext({
    session: { sessionId: "target" }, state: {},
    bootstrapNativeHandoff: () => selection,
    recoverPendingHandoff: () => null,
  });
  vm.runInContext(`globalThis.initialize = async () => {
    let nativeStartup, nativeReceiptPath, nativeStartupError;
    ${source.slice(start, end)}
    return { completion: nativeStartup, receipt: () => nativeReceiptPath };
  };`, context);
  // Selection is performed by the host only after it awaits initialization.
  const initialized = await context.initialize();
  assert.equal(initialized.receipt(), undefined);
  select({ preparing: false, path: "owned-checkpoint" });
  await initialized.completion;
  assert.equal(initialized.receipt(), "owned-checkpoint");
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
