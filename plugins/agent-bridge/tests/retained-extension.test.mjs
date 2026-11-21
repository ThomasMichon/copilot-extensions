import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import * as path from "node:path";
import * as delivery from "../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/delivery.mjs";
import * as retained from "../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/retained-delivery.mjs";
import * as questions from "../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/questions.mjs";
import * as deliveryState from "../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/delivery-state.mjs";
import { isRemoteDriverSource, selectedLoadout } from "../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/remote-driver.mjs";

const settle = () => new Promise((resolve) => setImmediate(resolve));
const source = readFileSync(new URL("../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/extension.mjs", import.meta.url), "utf8");

async function load(selected, version = 50, {
  exact = false, nativeId = "native-a", driver = false, headed = false, headedRefused = false,
  snapshot, send, muted = false, eventFailure, compatible = true,
  historyReplay = false,
  historyResponse = { replay_accepted: true, ingested: 1 },
  autoPoll = true, initialSessionId = "native-a", inboxMessages,
  tokenInitiallyMissing = false,
} = {}) {
  const calls = [], sends = [], logs = [], timers = [], handlers = [], driverSends = [], headedSends = [];
  const deferred = [];
  const shutdowns = new Map();
  const controlStarts = new Set();
  const legacyAcked = new Set();
  const registeredSessions = new Set();
  let closed = 0;
  let consumer;
  let joined;
  let eventPosts = 0;
  let bridgeReady = !tokenInitiallyMissing;
  const endpointState = {
    base: "http://127.0.0.1:9280", token: bridgeReady ? "synthetic-secret" : null,
  };
  const session = {
    sessionId: "native-a", on: (handler) => handlers.push(handler),
    log: async (message) => logs.push(message),
    send: async (options) => { sends.push(options); return send ? send(options) : "sdk-message-a"; },
    getEvents: async () => [],
    close() { assert.fail("Closing the Bridge client must not close native"); },
    rpc: { metadata: { snapshot: snapshot || (async () => ({
      sessionId: nativeId, workingDirectory: process.cwd(),
    })) } },
  };
  const binding = { installation: "synthetic-installation", epoch: 1 };
  const call = async (method, route, body) => {
    calls.push({ method, route, body });
    if (method === "POST" && route === "/api/v1/live-sessions") {
      registeredSessions.add(body.session_id);
    }
    if (route.endsWith("/events") && ++eventPosts === 1 && eventFailure) {
      if (eventFailure === "throw") throw new Error("synthetic event POST reply lost");
      if (typeof eventFailure === "function") return eventFailure();
      return { ok: false, status: 503, json: async () => ({}) };
    }
    let result = {};
    if (route === "/health") result = {
      protocol_version: version, capabilities: compatible ? { compatible_native_inbox: 1 } : {},
    };
    else if (route.endsWith("/events?replay=true")) result = { session_id: nativeId, ...historyResponse };
    else if (route.includes("/recovery?")) result = { messages: [], next_after: null };
    else if (route.endsWith("/negotiate")) result = {
      contract: "retained-inbox-v1", binding, expected_incarnation: null,
      ...(historyReplay ? { message_base: 1 } : {}),
    };
    else if (route.endsWith("/enroll")) {
      consumer = body.consumer;
      result = { contract: "retained-inbox-v1", binding, expected_incarnation: null };
    } else if (route.endsWith("/disposition")) {
      const begin = body.state === "started" && !controlStarts.has(body.message_id);
      if (begin) controlStarts.add(body.message_id);
      result = { ...body, begin };
    } else if (route.endsWith("/poll")) result = {
      contract: "retained-inbox-v1", binding, consumer, messages: [{
        id: 1, body: "synthetic message", sender: "peer", kind: "prompt",
        delivery: "steer", handoff_id: "handoff-a", handoff_stage: "offered", consumer,
        ...(historyReplay ? {
          body: "", kind: "control:replay-history", control: true,
          message_contract: {
            base_version: 1, type: "control:replay-history", type_version: 1, requires: [],
            message_id: 1, session_id: nativeId, receiver_incarnation: consumer.incarnation, sender: "peer",
          },
        } : {}),
      }],
    };
    else if (route.endsWith("/begin")) result = { begin: true };
    else if (route.endsWith("/outcome")) result = { stage: body.outcome };
    else if (route.includes("/messages?")) result = {
      messages: (inboxMessages ?? [{
        id: 1, body: "legacy body", sender: "peer", kind: "prompt", delivery: "steer",
        ...(exact ? { exact_session: true, original_session_id: "native-a" } : {}),
      }]).filter((message) => !legacyAcked.has(message.id)),
    };
    else if (route.endsWith("/messages/ack")) {
      for (const id of body.ids) legacyAcked.add(id);
      if (inboxMessages) {
        for (let index = inboxMessages.length - 1; index >= 0; index--) {
          if (body.ids.includes(inboxMessages[index].id)) inboxMessages.splice(index, 1);
        }
      }
    }
    else if (route.endsWith("/controls")) result = { messages: [] };
    return { ok: true, status: 200, json: async () => result };
  };
  const context = vm.createContext({
    process: {
      env: { SESSION_ID: initialSessionId, AGENT_BRIDGE_RETAINED_INBOX: selected,
        ...(muted ? { AGENT_BRIDGE_DELIVERY: "0" } : {}),
        ...(headed ? { COPILOT_DRIVER_HEADED_CLIENT: "synthetic-selected-core" } : {}),
        ...(driver ? {
          AGENT_BRIDGE_REMOTE_DRIVER_ROOT: process.cwd(), AGENT_BRIDGE_NATIVE_SESSION_ID: "native-a",
          AGENT_BRIDGE_DRIVER_OWNER: "synthetic-launch",
        } : {}) },
      cwd: () => process.cwd(),
      ppid: 101,
      stderr: { write: (line) => logs.push(line) }, once: (name, callback) => shutdowns.set(name, callback),
    },
    fetch: () => { throw new Error("real network forbidden"); },
    setInterval: (fn, ms) => { timers.push({ fn, ms }); return { unref() {} }; },
    clearInterval: () => {},
    setTimeout: (fn, ms) => {
      if (ms !== 0) return setTimeout(fn, ms);
      const task = { fn, cancelled: false, unref() {} };
      deferred.push(task);
      return task;
    },
    clearTimeout: (task) => {
      if (deferred.includes(task)) task.cancelled = true;
      else clearTimeout(task);
    },
    Buffer,
  });
  const dependencies = {
    "node:fs": {
      existsSync: (file) => file.endsWith("auth.yaml"),
      readFileSync: () => "token: synthetic-secret",
    },
    "node:fs/promises": { open: async () => {
      if (!historyReplay) throw new Error("real transcript reads forbidden");
      const transcript = JSON.stringify({
        id: "history-one", type: "assistant.message", data: { content: "represented history" },
      }) + "\n";
      return {
        stat: async () => ({ size: Buffer.byteLength(transcript) }),
        read: async (buffer) => { buffer.write(transcript); },
        close: async () => {},
      };
    } },
    "node:path": path,
    "node:os": { homedir: () => "/synthetic-home" },
    "@microsoft/copilot-sdk": { approveAll: () => {} },
    "@microsoft/copilot-sdk/extension": { joinSession: async (config) => { joined = config; return session; } },
    "./delivery.mjs": delivery,
    "./retained-delivery.mjs": retained,
    "./questions.mjs": questions,
    "./delivery-state.mjs": deliveryState,
    "./remote-driver.mjs": {
      isRemoteDriverSource,
      selectedLoadout,
      createHeadedDriverSession: async (native, options) => {
        assert.equal(headed, true);
        assert.equal(options.declaration, "synthetic-selected-core");
        assert.equal(options.nativePid, 101);
        assert.equal(options.cwd, process.cwd());
        if (driver) {
          assert.equal(options.pluginRoot, process.cwd());
          assert.equal(options.expectedSessionId, "native-a");
        }
        if (headedRefused) throw new Error("selected headed client unavailable");
        return {
          ...native, send: async (value) => { headedSends.push(value); return "headed-message"; },
          close: () => { closed++; },
        };
      },
      createRemoteDriverSession: (native, options) => {
        assert.equal(driver, true);
        assert.equal(options.expectedSessionId, "native-a");
        assert.equal(options.ownerId, "synthetic-launch");
        return { ...native, send: async (value) => { driverSends.push(value); return "driver-message"; } };
      },
    },
    "./announce.mjs": { firstLoadThisSession: () => false },
    "./bridge-endpoint.mjs": { makeBridgeEndpoint: () => ({
      ep: endpointState, call,
      refresh: () => { endpointState.token = bridgeReady ? "synthetic-secret" : null; },
    }) },
    "./metadata.mjs": {
      processIdentity: () => ({ pid: 102, process_started_at: 1 }),
      resolveMachineAsync: async () => "synthetic",
      resolveMetadataAsync: async () => ({ machine: "synthetic", cwd: process.cwd() }),
      metadataRefresher: () => async () => false,
      refreshRegistrationMetadata: async (state) => state.meta,
    },
    "./machine-context.mjs": { machineLookup: () => async () => "synthetic" },
  };
  context.__dependencies = dependencies;
  // Stable VM scripts avoid the Node 22 experimental-module SIGSEGV. Only
  // the extension's explicit named imports are linked; its body is unchanged.
  const linked = source.replace("export async function activateBridgeExtension", "async function activateBridgeExtension").replace(
    /^import\s+\{([^}]+)\}\s+from\s+["']([^"']+)["'];/gm,
    (_statement, names, name) => {
      const exports = dependencies[name];
      assert.ok(exports, `unexpected dependency ${name}`);
      for (const binding of names.split(",").map((part) => part.trim()).filter(Boolean)) {
        assert.match(binding, /^[A-Za-z_$][\w$]*$/);
        assert.ok(Object.hasOwn(exports, binding), `missing dependency export ${name}:${binding}`);
      }
      return `const {${names}} = __dependencies[${JSON.stringify(name)}];`;
    },
  );
  assert.doesNotMatch(linked, /^import\s/m, "unsupported extension import syntax");
  await new vm.Script(`(async () => {\n${linked}\nawait activateBridgeExtension({
    joinSession: __dependencies["@microsoft/copilot-sdk/extension"].joinSession,
    pluginRoot: process.cwd(),
  });\n})()`).runInContext(context);
  await settle();
  const poll = timers.find((timer) => timer.ms === 2_000);
  assert.ok(poll);
  if (autoPoll) {
    poll.fn();
    await settle();
  }
  const runDeferred = async () => {
    for (let count = 0; deferred.length; count++) {
      assert.ok(count < 10, "deferred inbox work must remain bounded");
      const task = deferred.shift();
      if (!task.cancelled) task.fn();
      await settle();
    }
  };
  return {
    calls, sends, logs, timers, handlers, poll, driverSends, headedSends, shutdowns, joined,
    closed: () => closed, runDeferred, registeredSessions,
    publishBridge: () => { bridgeReady = true; },
    rename: (sid) => { session.sessionId = sid; nativeId = sid; },
  };
}

for (const selected of [undefined, "1"]) {
  test(`${selected ? "retained" : "legacy"} registration drains the actual joined session without a poll tick`, async () => {
    const f = await load(selected, 51, { autoPoll: false, initialSessionId: "stale-placeholder" });
    await f.runDeferred();
    assert.equal(f.sends.length, 1);
    assert.equal(f.sends[0].mode, "enqueue");
    const registrations = f.calls.filter((call) => call.route === "/api/v1/live-sessions");
    assert.deepEqual(registrations.map((call) => call.body.session_id), ["native-a"]);
  });
}

for (const resumed of [false, true]) {
  test(`idle drains pending input ${resumed ? "after resume/re-registration" : "on the current registration"} without an unrelated turn`, async () => {
    const messages = [];
    let active = true;
    const f = await load(undefined, 51, {
      autoPoll: false, inboxMessages: messages,
      send: () => {
        if (active) throw new Error("SDK unavailable during the active turn");
        return "accepted-idle-input";
      },
    });
    await f.runDeferred();
    f.handlers[0]({ type: "assistant.turn_start", data: {} });
    messages.push({
      id: 17, sender: "peer", body: "pending work", delivery: "steer",
      exact_session: true, original_session_id: "native-a",
    });
    f.poll.fn();
    await settle();
    assert.equal(f.sends[0].mode, "immediate");
    const notice = f.joined.hooks.onPostToolUse(
      { sessionId: "native-a" }, { sessionId: "native-a" },
    );
    assert.match(notice.additionalContext, /pending.*SDK submission/s);
    assert.doesNotMatch(notice.additionalContext, /END YOUR TURN/);
    active = false;
    if (resumed) {
      f.rename("resumed-session");
      messages[0].original_session_id = "resumed-session";
    }
    f.handlers[0]({ type: "session.idle", data: {} });
    await f.runDeferred();
    assert.equal(messages.length, 0);
    assert.equal(f.sends.length, 2);
    assert.equal(f.sends[1].mode, "enqueue");
    assert.match(f.sends[1].prompt, /msg-id="17"/);
    assert.equal(f.calls.some((call) => call.body?.outcome === "consumed"), false);
    if (resumed) assert.ok(f.calls.some((call) =>
      call.route === "/api/v1/live-sessions" && call.body.session_id === "resumed-session"));
  });
}

test("retained resume follows the SDK session getter when idle events omit sessionId", async () => {
  const f = await load("1", 51, { autoPoll: false });
  await f.runDeferred();
  assert.equal(f.sends.length, 1);
  f.rename("resumed-session");
  f.handlers[0]({ type: "assistant.idle", data: {} });
  await f.runDeferred();
  assert.equal(f.sends.length, 2);
  assert.match(f.sends[0].prompt, /retained-session="native-a"/);
  assert.match(f.sends[1].prompt, /retained-session="resumed-session"/);
  assert.ok(f.calls.some((call) =>
    call.route === "/api/v1/live-sessions/resumed-session/retained-inbox/begin"));
  assert.equal(f.calls.some((call) => call.body?.outcome === "consumed"), false);
});

test("heartbeat re-registration rearms idle inbox delivery after the Bridge loses its row", async () => {
  const messages = [];
  const f = await load(undefined, 51, { autoPoll: false, inboxMessages: messages });
  await f.runDeferred();
  assert.equal(f.registeredSessions.has("native-a"), true);
  f.registeredSessions.clear();
  messages.push({ id: 21, sender: "peer", body: "pending after restart", delivery: "steer" });
  f.timers.find((timer) => timer.ms === 30_000).fn();
  await settle();
  assert.equal(f.registeredSessions.has("native-a"), true);
  await f.runDeferred();
  assert.equal(messages.length, 0);
  assert.equal(f.sends.length, 1);
  assert.equal(f.sends[0].mode, "enqueue");
  assert.match(f.sends[0].prompt, /msg-id="21"/);
});

test("idle resume re-registers the SDK identity even without any subsequent session event", async () => {
  const messages = [];
  const f = await load(undefined, 51, { autoPoll: false, initialSessionId: null, inboxMessages: messages });
  await f.runDeferred();
  assert.equal(f.registeredSessions.has("native-a"), true);
  f.rename("resumed-without-events");
  messages.push({
    id: 25, sender: "peer", body: "pending resume", delivery: "steer",
    exact_session: true, original_session_id: "resumed-without-events",
  });
  f.timers.find((timer) => timer.ms === 30_000).fn();
  await settle();
  await f.runDeferred();
  assert.equal(f.registeredSessions.has("resumed-without-events"), true);
  assert.equal(messages.length, 0);
  assert.equal(f.sends.length, 1);
  assert.match(f.sends[0].prompt, /msg-id="25"/);
  assert.equal(f.sends[0].mode, "enqueue");
});

for (const selected of [undefined, "1"]) {
  test(`${selected ? "retained" : "legacy"} receiver loaded before Bridge auth recovers without a reload or new turn`, async () => {
    const f = await load(selected, 51, { autoPoll: false, tokenInitiallyMissing: true });
    await f.runDeferred();
    assert.equal(f.registeredSessions.size, 0);
    assert.equal(f.sends.length, 0);
    f.publishBridge();
    f.timers.find((timer) => timer.ms === 30_000).fn();
    await settle();
    await f.runDeferred();
    assert.equal(f.registeredSessions.has("native-a"), true);
    assert.equal(f.sends.length, 1);
    assert.equal(f.sends[0].mode, "enqueue");
  });
}

test("idle wakes coalesce, ignore sub-agents and preserve inbox order across idle arrivals", async () => {
  const messages = [];
  const f = await load(undefined, 51, { autoPoll: false, inboxMessages: messages });
  await f.runDeferred();
  f.handlers[0]({ type: "assistant.turn_start", data: {} });
  messages.push(...[31, 32].map((id) => ({
    id, sender: "peer", body: `pending ${id}`, delivery: "steer",
  })));
  const before = f.calls.length;
  f.handlers[0]({ type: "session.idle", agentId: "child", data: {} });
  await f.runDeferred();
  assert.equal(f.calls.length, before);
  f.handlers[0]({ type: "assistant.idle", data: {} });
  f.handlers[0]({ type: "session.idle", data: {} });
  await f.runDeferred();
  assert.equal(f.calls.slice(before).filter((call) => call.route.includes("/messages?")).length, 1);
  assert.deepEqual(f.sends.map((options) => options.prompt.match(/msg-id="(\d+)"/)[1]), ["31", "32"]);
  assert.ok(f.sends.every((options) => options.mode === "enqueue"));
  messages.push({ id: 33, sender: "peer", body: "arrived while idle", delivery: "queue" });
  f.poll.fn();
  await settle();
  assert.equal(f.sends.length, 3);
  assert.match(f.sends[2].prompt, /msg-id="33"/);
  f.poll.fn();
  await settle();
  assert.equal(f.sends.length, 3);
});

test("shutdown cancels an admitted deferred inbox wake without replacing the session", async () => {
  const f = await load(undefined, 51, { autoPoll: false });
  f.shutdowns.get("SIGTERM")();
  await f.runDeferred();
  assert.equal(f.sends.length, 0);
  assert.equal(f.closed(), 0);
});

test("actual retained activation reconstructs represented transcript without sending a prompt", async () => {
  const f = await load("1", 50, { historyReplay: true });
  assert.equal(f.sends.length, 0);
  const replay = f.calls.find((call) => call.route.endsWith("/events?replay=true"));
  assert.ok(replay, f.logs.join("\n"));
  assert.equal(replay.body.events.length, 1);
  assert.equal(replay.body.events[0].id, "history-one");
  assert.ok(f.calls.some((call) => call.route.endsWith("/disposition") && call.body.state === "completed"));
  assert.equal(f.calls.some((call) => call.route.endsWith("/begin")), false);
});

test("accepted empty or deduplicated replay still completes with zero ingested events", async () => {
  const f = await load("1", 50, {
    historyReplay: true, historyResponse: { replay_accepted: true, ingested: 0 },
  });
  assert.equal(f.sends.length, 0);
  assert.ok(f.calls.some((call) => call.route.endsWith("/disposition") && call.body.state === "completed"));
});

for (const [label, historyResponse] of [
  ["expired", { replay_accepted: false, ingested: 0 }],
  ["missing acceptance", { ingested: 0 }],
  ["malformed acceptance", { replay_accepted: "true", ingested: 1 }],
  ["different context", { session_id: "another-session", replay_accepted: true, ingested: 1 }],
]) {
  test(`HTTP success for ${label} history never falsely completes the retained control`, async () => {
    const f = await load("1", 50, { historyReplay: true, historyResponse });
    assert.ok(f.calls.some((call) => call.route.endsWith("/events?replay=true")));
    assert.equal(f.sends.length, 0);
    assert.equal(f.calls.some((call) => call.route.endsWith("/disposition") && call.body.state === "completed"), false);
    assert.ok(f.calls.some((call) => call.route.endsWith("/disposition") && call.body.state === "unknown"));
    f.poll.fn();
    await settle();
    assert.equal(f.calls.filter((call) => call.route.endsWith("/events?replay=true")).length, 1);
  });
}

test("actual extension default still uses legacy inbox, ACK and controls", async () => {
  const f = await load(undefined);
  assert.equal(f.sends.length, 1);
  assert.ok(f.calls.some((call) => call.route.endsWith("/messages/ack")));
  assert.ok(f.calls.some((call) => call.route.endsWith("/controls")));
  assert.equal(f.calls.some((call) => call.route.includes("/retained-inbox")), false);
});

test("actual extension observes exact native metadata before SDK send", async () => {
  const current = await load(undefined, 51, { exact: true });
  assert.equal(current.sends.length, 1);
  assert.ok(current.calls.some((call) => call.route.endsWith("/messages/ack")));
  const changed = await load(undefined, 51, { exact: true, nativeId: "successor" });
  assert.equal(changed.sends.length, 0);
  assert.equal(changed.calls.some((call) => call.route.endsWith("/messages/ack")), false);
  assert.ok(changed.logs.some((line) => line.includes("native context changed")));
});

test("a pending exact-native lookup stalls inbox progress while liveness stays fresh", async () => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const f = await load(undefined, 51, { exact: true, snapshot: () => pending });
  const inboxCalls = () => f.calls.filter((call) => call.route.includes("/messages?")).length;
  const registrations = () => f.calls.filter((call) => call.route === "/api/v1/live-sessions").length;
  assert.equal(inboxCalls(), 1);
  assert.equal(f.sends.length, 0);
  const before = registrations();
  for (let tick = 0; tick < 3; tick++) {
    f.poll.fn();
    f.timers.find((timer) => timer.ms === 30_000).fn();
    await settle();
  }
  assert.ok(registrations() > before);
  assert.equal(inboxCalls(), 1);
  assert.equal(f.sends.length, 0);
  assert.equal(f.calls.some((call) => call.route.endsWith("/messages/ack")), false);
  release({ sessionId: "native-a", workingDirectory: process.cwd() });
  await settle();
  assert.equal(f.sends.length, 1);
  assert.ok(f.calls.some((call) => call.route.endsWith("/messages/ack")));
});

test("a pending SDK send holds the drain and ACK without blocking heartbeat", async () => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const f = await load(undefined, 51, { send: () => pending });
  assert.equal(f.sends.length, 1);
  const before = f.calls.length;
  f.poll.fn();
  f.timers.find((timer) => timer.ms === 30_000).fn();
  await settle();
  assert.equal(f.sends.length, 1);
  assert.equal(f.calls.filter((call) => call.route.includes("/messages?")).length, 1);
  assert.equal(f.calls.some((call) => call.route.endsWith("/messages/ack")), false);
  assert.ok(f.calls.slice(before).some((call) => call.route === "/api/v1/live-sessions"));
  release("sdk-message-a");
  await settle();
  assert.ok(f.calls.some((call) => call.route.endsWith("/messages/ack")));
});

test("SDK acceptance alone does not produce a represented model-input event", async () => {
  const f = await load(undefined);
  assert.equal(f.sends.length, 1);
  assert.ok(f.calls.some((call) => call.route.endsWith("/messages/ack")));
  const flush = f.timers.find((timer) => timer.ms === 1_000);
  flush.fn();
  await settle();
  assert.equal(f.calls.some((call) => call.route.endsWith("/events")), false);
  assert.equal(f.handlers[0]({
    id: "input-a", type: "user.message", sessionId: "native-a",
    data: { source: "agent-bridge", content: f.sends[0].prompt },
  }), undefined);
  flush.fn();
  await settle();
  const events = f.calls.filter((call) => call.route.endsWith("/events"));
  assert.equal(events.length, 1);
  assert.equal(events[0].body.events[0].id, "input-a");
});

test("post-tool notification sees a stalled drain without network or false consumption", async () => {
  const f = await load(undefined, 51, { exact: true, snapshot: () => new Promise(() => {}) });
  const before = f.calls.length;
  const hook = f.joined.hooks.onPostToolUse;
  const input = { sessionId: "native-a", toolCallId: "tool-a" };
  const invocation = { sessionId: "native-a" };
  const notice = hook(input, invocation);
  assert.match(notice.additionalContext, /1 pending.*SDK submission/s);
  assert.doesNotMatch(notice.additionalContext, /END YOUR TURN/);
  assert.equal(hook(input, invocation), undefined);
  assert.equal(f.joined.hooks.onPostToolUseFailure(input, invocation), undefined);
  assert.equal(f.calls.length, before);
  assert.equal(f.sends.length, 0);
  assert.equal(f.calls.some((call) => call.route.endsWith("/messages/ack")), false);
  f.timers.find((timer) => timer.ms === 1_000).fn();
  await settle();
  const observation = f.calls.find((call) => call.route.endsWith("/events")).body.events[0];
  assert.equal(observation.type, "bridge.delivery_observation");
  assert.deepEqual(JSON.parse(JSON.stringify(observation.data)), {
    stage: "hook_notice", source: "pending-input-set-v1", session_id: "native-a",
    pending_count: 1, incomplete: false, delivery_path: "legacy",
  });
});

for (const selected of [undefined, "1"]) {
  test(`${selected ? "retained" : "legacy"} submission clears the yield notice without claiming consumption`, async () => {
    let release;
    const pending = new Promise((resolve) => { release = resolve; });
    const f = await load(selected, 51, { send: () => pending });
    const hook = f.joined.hooks.onPostToolUse;
    const input = { sessionId: "native-a" };
    const invocation = { sessionId: "native-a" };
    assert.equal(f.sends.length, 1);
    assert.equal(hook({ ...input, agentId: "child" }, invocation), undefined);
    assert.equal(hook({ ...input, parentToolCallId: "parent" }, invocation), undefined);
    assert.equal(hook({ ...input, sessionId: "child" }, invocation), undefined);
    assert.equal(hook(input, { sessionId: "foreign" }), undefined);
    if (selected) {
      assert.match(hook(input, invocation).additionalContext, /1 pending/);
      f.timers.find((timer) => timer.ms === 1_000).fn();
      await settle();
      const diagnostic = f.calls.find((call) => call.route.endsWith("/events")).body.events[0].data;
      const enrolled = f.calls.find((call) => call.route.endsWith("/enroll")).body.consumer;
      assert.equal(diagnostic.consumer_incarnation, enrolled.incarnation);
      assert.equal(diagnostic.delivery_path, "retained");
    }
    release("sdk-message-a");
    await settle();
    assert.equal(hook(input, invocation), undefined);
    f.timers.find((timer) => timer.ms === 1_000).fn();
    await settle();
    assert.equal(f.calls.some((call) => call.route.endsWith("/events")), Boolean(selected));
    assert.equal(f.calls.some((call) => call.body?.outcome === "consumed"), false);
    assert.equal(f.sends.length, 1);
  });
}

test("intentional mute suppresses pending-input hooks", async () => {
  const f = await load(undefined, 51, { muted: true });
  assert.equal(f.sends.length, 0);
  assert.equal(f.joined.hooks.onPostToolUse(
    { sessionId: "native-a" }, { sessionId: "native-a" },
  ), undefined);
});

for (const eventFailure of ["http", "throw"]) {
  test(`root-input receipt survives a failed event POST (${eventFailure}) without another send`, async () => {
    const f = await load(undefined, 51, { eventFailure });
    f.handlers[0]({
      id: "input-a", type: "user.message", sessionId: "native-a",
      data: { source: "agent-bridge", content: f.sends[0].prompt },
    });
    f.handlers[0]({
      id: "output-a", type: "assistant.message", sessionId: "native-a", data: { content: "output" },
    });
    const flush = f.timers.find((timer) => timer.ms === 1_000);
    flush.fn();
    await settle();
    flush.fn();
    await settle();
    const posts = f.calls.filter((call) => call.route.endsWith("/events"));
    assert.equal(posts.length, 2);
    assert.deepEqual(Array.from(posts[0].body.events, (event) => event.id), ["input-a", "output-a"]);
    assert.deepEqual(Array.from(posts[1].body.events, (event) => event.id), ["input-a"]);
    assert.equal(f.sends.length, 1);
    flush.fn();
    await settle();
    assert.equal(f.calls.filter((call) => call.route.endsWith("/events")).length, 2);
  });
}

test("ordinary event bursts cannot evict buffered root-input evidence", async () => {
  const f = await load(undefined);
  f.handlers[0]({
    id: "input-a", type: "user.message", sessionId: "native-a",
    data: { source: "agent-bridge", content: f.sends[0].prompt },
  });
  for (let id = 0; id < 1_010; id++) {
    f.handlers[0]({
      id: `output-${id}`, type: "assistant.message", sessionId: "native-a", data: {},
    });
  }
  f.timers.find((timer) => timer.ms === 1_000).fn();
  await settle();
  const post = f.calls.find((call) => call.route.endsWith("/events"));
  assert.equal(post.body.events.length, 250);
  assert.equal(post.body.events[0].id, "input-a");
});

test("an unconfirmed receipt retains its original native session across a binding change", async () => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const f = await load(undefined, 51, { eventFailure: () => pending });
  f.handlers[0]({
    id: "input-a", type: "user.message", sessionId: "native-a",
    data: { source: "agent-bridge", content: f.sends[0].prompt },
  });
  const flush = f.timers.find((timer) => timer.ms === 1_000);
  flush.fn();
  await settle();
  f.rename("successor");
  f.handlers[0]({
    id: "successor-output", type: "assistant.message", sessionId: "successor", data: {},
  });
  await f.runDeferred();
  release({ ok: false, status: 503 });
  await settle();
  flush.fn();
  await settle();
  const posts = f.calls.filter((call) => call.route.endsWith("/events"));
  assert.equal(posts[0].route, "/api/v1/live-sessions/native-a/events");
  assert.equal(posts[1].route, "/api/v1/live-sessions/successor/events");
  assert.deepEqual(Array.from(posts[1].body.events, (event) => event.id), ["successor-output"]);
  flush.fn();
  await settle();
  const retry = f.calls.filter((call) => call.route.endsWith("/events"))[2];
  assert.equal(retry.route, "/api/v1/live-sessions/native-a/events");
  assert.deepEqual(Array.from(retry.body.events, (event) => event.id), ["input-a"]);
  assert.equal(f.sends.length, 1);
});

test("actual selected extension exclusively wires retained delivery off the event handler", async () => {
  const f = await load("1");
  assert.equal(f.sends.length, 1);
  assert.match(f.sends[0].prompt, /retained-handoff="handoff-a"/);
  assert.ok(f.calls.some((call) => call.route.endsWith("/begin")));
  assert.equal(f.calls.some((call) => /\/messages|\/controls/.test(call.route)), false);
  const count = f.calls.length;
  assert.equal(f.handlers[0]({
    type: "user.message", sessionId: "native-a", data: { source: "agent-bridge" },
  }), undefined);
  assert.equal(f.calls.length, count);
});

test("actual selected extension holds on old protocol instead of silently downgrading", async () => {
  const f = await load("1", 49);
  assert.equal(f.sends.length, 0);
  assert.equal(f.calls.some((call) => /\/messages|\/controls|\/enroll/.test(call.route)), false);
  assert.ok(f.logs.some((line) => line.includes("protocol 50")));
});

test("launch-selected receiving uses the public SDK without invoking private driver authority", async () => {
  const f = await load("0", 50, { driver: true });
  assert.equal(f.sends.length, 1);
  assert.equal(f.driverSends.length, 0);
  assert.equal(f.calls.some((call) => /\/messages|\/controls/.test(call.route)), false);
  const old = await load("0", 49, { driver: true });
  assert.equal(old.sends.length, 0);
  assert.equal(old.driverSends.length, 0);
  assert.equal(old.calls.some((call) => /\/messages|\/controls|\/enroll/.test(call.route)), false);
});

test("headed receiving preserves one-shot receipts using the public native SDK", async () => {
  for (const driver of [false, true]) {
    const f = await load("0", 50, { headed: true, driver });
    assert.equal(f.sends.length, 1);
    assert.equal(f.driverSends.length, 0);
    assert.equal(f.headedSends.length, 0);
    assert.match(f.sends[0].prompt, /retained-handoff="handoff-a"/);
    assert.equal(f.sends[0].mode, "enqueue");
    assert.ok(f.calls.filter((call) => /\/negotiate|\/enroll|\/begin|\/outcome/.test(call.route))
      .every((call) => call.body.receiving_api === "native-sdk-v1"));
    assert.equal(f.calls.some((call) => /\/messages|\/controls/.test(call.route)), false);
    f.shutdowns.get("SIGTERM")();
    assert.equal(f.closed(), 0);
  }
});

test("revoked or missing private headed control cannot gate ordinary native delivery", async () => {
  const f = await load("0", 50, { headed: true, headedRefused: true });
  assert.equal(f.sends.length, 1);
  assert.equal(f.headedSends.length, 0);
  const old = await load("0", 49, { headed: true });
  assert.equal(old.sends.length, 0);
  assert.equal(old.driverSends.length, 0);
  assert.equal(old.headedSends.length, 0);
  assert.equal(old.calls.some((call) => /\/messages|\/controls|\/enroll/.test(call.route)), false);
});

test("new native receiving refuses an old provider before enrollment or begin", async () => {
  const f = await load("1", 52, { compatible: false, headed: true });
  assert.equal(f.sends.length, 0);
  assert.equal(f.calls.some((call) => /\/negotiate|\/enroll|\/begin/.test(call.route)), false);
  assert.ok(f.logs.some((line) => line.includes("compatible native inbox support required")));
});
