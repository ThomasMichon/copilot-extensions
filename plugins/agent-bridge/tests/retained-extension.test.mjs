import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import * as path from "node:path";
import * as delivery from "../extensions/agent-bridge/delivery.mjs";
import * as retained from "../extensions/agent-bridge/retained-delivery.mjs";

const settle = () => new Promise((resolve) => setImmediate(resolve));
const source = readFileSync(new URL("../extensions/agent-bridge/extension.mjs", import.meta.url), "utf8");

async function load(selected, version = 50) {
  const calls = [], sends = [], logs = [], timers = [], handlers = [];
  let consumer;
  const session = {
    sessionId: "native-a", on: (handler) => handlers.push(handler),
    log: async (message) => logs.push(message),
    send: async (options) => { sends.push(options); return "sdk-message-a"; },
    getEvents: async () => [],
    rpc: { metadata: { snapshot: async () => ({
      sessionId: "native-a", workingDirectory: process.cwd(),
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
      messages: [{ id: 1, body: "legacy body", sender: "peer", kind: "prompt", delivery: "steer" }],
    };
    else if (route.endsWith("/controls")) result = { messages: [] };
    return { ok: true, status: 200, json: async () => result };
  };
  const context = vm.createContext({
    process: {
      env: { SESSION_ID: "native-a", AGENT_BRIDGE_RETAINED_INBOX: selected },
      stderr: { write: (line) => logs.push(line) }, once: () => {},
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
  const module = new vm.SourceTextModule(source, { context });
  await module.link((name) => {
    const exports = dependencies[name];
    assert.ok(exports, `unexpected dependency ${name}`);
    return new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
    }, { context });
  });
  await module.evaluate();
  await settle();
  const poll = timers.find((timer) => timer.ms === 2_000);
  assert.ok(poll);
  poll.fn();
  await settle();
  return { calls, sends, logs, handlers, poll };
}

test("actual extension default still uses legacy inbox, ACK and controls", {
  skip: !vm.SourceTextModule,
}, async () => {
  const f = await load(undefined);
  assert.equal(f.sends.length, 1);
  assert.ok(f.calls.some((call) => call.route.endsWith("/messages/ack")));
  assert.ok(f.calls.some((call) => call.route.endsWith("/controls")));
  assert.equal(f.calls.some((call) => call.route.includes("/retained-inbox")), false);
});

test("actual selected extension exclusively wires retained delivery off the event handler", {
  skip: !vm.SourceTextModule,
}, async () => {
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

test("actual selected extension holds on old protocol instead of silently downgrading", {
  skip: !vm.SourceTextModule,
}, async () => {
  const f = await load("1", 49);
  assert.equal(f.sends.length, 0);
  assert.equal(f.calls.some((call) => /\/messages|\/controls|\/enroll/.test(call.route)), false);
  assert.ok(f.logs.some((line) => line.includes("protocol 50")));
});
