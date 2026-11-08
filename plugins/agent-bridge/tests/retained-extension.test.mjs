import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import * as path from "node:path";
import * as delivery from "../extensions/agent-bridge/delivery.mjs";
import * as retained from "../extensions/agent-bridge/retained-delivery.mjs";
import * as questions from "../extensions/agent-bridge/questions.mjs";
import * as deliveryState from "../extensions/agent-bridge/delivery-state.mjs";
import { isRemoteDriverSource } from "../extensions/agent-bridge/remote-driver.mjs";

const settle = () => new Promise((resolve) => setImmediate(resolve));
const source = readFileSync(new URL("../extensions/agent-bridge/extension.mjs", import.meta.url), "utf8");

async function load(selected, version = 50, {
  exact = false, nativeId = "native-a", driver = false, headed = false, headedRefused = false,
} = {}) {
  const calls = [], sends = [], logs = [], timers = [], handlers = [], driverSends = [], headedSends = [];
  const shutdowns = new Map();
  let closed = 0;
  let consumer;
  const session = {
    sessionId: "native-a", on: (handler) => handlers.push(handler),
    log: async (message) => logs.push(message),
    send: async (options) => { sends.push(options); return "sdk-message-a"; },
    getEvents: async () => [],
    close() { assert.fail("Closing the Bridge client must not close native"); },
    rpc: { metadata: { snapshot: async () => ({
      sessionId: nativeId, workingDirectory: process.cwd(),
    }) } },
  };
  const binding = { installation: "synthetic-installation", epoch: 1 };
  const call = async (method, route, body) => {
    calls.push({ method, route, body });
    let result = {};
    if (route === "/health") result = { protocol_version: version };
    else if (route.includes("/recovery?")) result = { messages: [], next_after: null };
    else if (route.endsWith("/negotiate")) result = {
      contract: "retained-inbox-v1", binding, expected_incarnation: null,
    };
    else if (route.endsWith("/enroll")) {
      consumer = body.consumer;
      result = { contract: "retained-inbox-v1", binding, expected_incarnation: null };
    } else if (route.endsWith("/poll")) result = {
      contract: "retained-inbox-v1", binding, consumer, messages: [{
        id: 1, body: "synthetic message", sender: "peer", kind: "prompt",
        delivery: "steer", handoff_id: "handoff-a", handoff_stage: "offered",
      }],
    };
    else if (route.endsWith("/begin")) result = { begin: true };
    else if (route.endsWith("/outcome")) result = { stage: body.outcome };
    else if (route.includes("/messages?")) result = {
      messages: [{
        id: 1, body: "legacy body", sender: "peer", kind: "prompt", delivery: "steer",
        ...(exact ? { exact_session: true, original_session_id: "native-a" } : {}),
      }],
    };
    else if (route.endsWith("/controls")) result = { messages: [] };
    return { ok: true, status: 200, json: async () => result };
  };
  const context = vm.createContext({
    process: {
      env: { SESSION_ID: "native-a", AGENT_BRIDGE_RETAINED_INBOX: selected,
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
    clearInterval: () => {}, setTimeout,
  });
  const dependencies = {
    "node:fs": {
      existsSync: (file) => file.endsWith("auth.yaml"),
      readFileSync: () => "token: synthetic-secret",
    },
    "node:fs/promises": { open: () => { throw new Error("real transcript reads forbidden"); } },
    "node:path": path,
    "node:os": { homedir: () => "/synthetic-home" },
    "@github/copilot-sdk": { approveAll: () => {} },
    "@github/copilot-sdk/extension": { joinSession: async () => session },
    "./delivery.mjs": delivery,
    "./retained-delivery.mjs": retained,
    "./questions.mjs": questions,
    "./delivery-state.mjs": deliveryState,
    "./remote-driver.mjs": {
      isRemoteDriverSource,
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
      ep: { base: "http://127.0.0.1:9280", token: "synthetic-secret" }, call,
    }) },
    "./metadata.mjs": {
      processIdentity: () => ({ pid: 102, process_started_at: 1 }),
      resolveMetadataAsync: async () => ({ machine: "synthetic", cwd: process.cwd() }),
      metadataRefresher: () => async () => false,
      registrationMetadata: (meta) => meta,
    },
  };
  context.__dependencies = dependencies;
  // Stable VM scripts avoid the Node 22 experimental-module SIGSEGV. Only
  // the extension's explicit named imports are linked; its body is unchanged.
  const linked = source.replace(
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
  await new vm.Script(`(async () => {\n${linked}\n})()`).runInContext(context);
  await settle();
  const poll = timers.find((timer) => timer.ms === 2_000);
  assert.ok(poll);
  poll.fn();
  await settle();
  return { calls, sends, logs, handlers, poll, driverSends, headedSends, shutdowns, closed: () => closed };
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

test("launch-selected driver cannot fall through to legacy native delivery or controls", async () => {
  const f = await load("0", 50, { driver: true });
  assert.equal(f.sends.length, 0);
  assert.equal(f.driverSends.length, 1);
  assert.equal(f.calls.some((call) => /\/messages|\/controls/.test(call.route)), false);
  const old = await load("0", 49, { driver: true });
  assert.equal(old.sends.length, 0);
  assert.equal(old.driverSends.length, 0);
  assert.equal(old.calls.some((call) => /\/messages|\/controls|\/enroll/.test(call.route)), false);
});

test("the actual extension selects launch-bound common delivery ahead of both legacy paths", async () => {
  for (const driver of [false, true]) {
    const f = await load("0", 50, { headed: true, driver });
    assert.equal(f.sends.length, 0);
    assert.equal(f.driverSends.length, 0);
    assert.equal(f.headedSends.length, 1);
    assert.match(f.headedSends[0].prompt, /retained-handoff="handoff-a"/);
    assert.equal(f.headedSends[0].mode, "immediate");
    assert.equal(f.calls.some((call) => /\/messages|\/controls/.test(call.route)), false);
    f.shutdowns.get("SIGTERM")();
    assert.equal(f.closed(), 1);
  }
});

test("an unavailable selected headed client never falls through to the SDK or old driver", async () => {
  await assert.rejects(load("0", 50, { headed: true, headedRefused: true }), /selected headed client unavailable/);
  const old = await load("0", 49, { headed: true });
  assert.equal(old.sends.length, 0);
  assert.equal(old.driverSends.length, 0);
  assert.equal(old.headedSends.length, 0);
  assert.equal(old.calls.some((call) => /\/messages|\/controls|\/enroll/.test(call.route)), false);
});
