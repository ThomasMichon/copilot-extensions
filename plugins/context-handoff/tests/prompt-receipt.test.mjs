import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";
import { registerPromptReceiptObservation } from "../extensions/context-handoff/prompt-receipt.mjs";
import { nativeClientMetadata, pendingIdentity } from "./fixtures/native-client-metadata.mjs";

const prompt = "Continue work | Recovery: context-handoff task:receipt-1";
function harness(options = {}) {
  const handlers = new Map();
  const warnings = [];
  const runtime = nativeClientMetadata();
  const native = runtime.client();
  const session = {
    sessionId: native.sessionId,
    rpc: native.rpc,
    on(name, callback) {
      const callbacks = handlers.get(name) || new Set();
      callbacks.add(callback);
      handlers.set(name, callbacks);
      return () => callbacks.delete(callback);
    },
    log: (message) => warnings.push(message),
  };
  const state = { turnCount: 0, cwd: process.cwd(), sessionId: "subject" };
  const observe = options.observe && (async (input) => {
    const query = input.pending.uncertain === true;
    const result = await options.observe(input);
    if (["logged", "unknown"].includes(result.status)) {
      const identity = pendingIdentity(input.sid, input.locator);
      input.pending.installation ??= identity.installation;
      input.pending.runtimeRoot ??= identity.runtimeRoot;
      input.pending.subject = { ...identity.subject, ...input.pending.subject };
      if (!query) await input.beforeRecord(input.pending);
    }
    return result;
  });
  const observation = registerPromptReceiptObservation(session, state, { ...options, observe });
  return {
    state, session, observation, warnings, runtime,
    emit: (name, event = {}) => {
      for (const callback of handlers.get(name) || []) callback(event);
    },
  };
}
async function settle(observation) {
  for (let i = 0; i < 400; i++) {
    if (observation.status.status !== "pending") return;
    await delay(10);
  }
  assert.fail("observation did not settle");
}

test("registered callback returns before work; concurrent duplicate events coalesce", async () => {
  let calls = 0;
  let release;
  const h = harness({ observe: () => { calls++; return new Promise((r) => { release = r; }); } });
  try {
    const start = performance.now();
    h.emit("user.message", { data: { content: prompt } });
    assert.ok(performance.now() - start < 100);
    assert.equal(calls, 0);
    assert.equal(registerPromptReceiptObservation(h.session, h.state), h.observation);
    for (let i = 0; i < 100; i++) {
      h.emit("user.message", { data: { content: prompt } });
      h.emit("session.idle");
    }
    await delay(10);
    assert.equal(calls, 1);
    release({ status: "logged" });
    await settle(h.observation);
    h.emit("session.idle");
    await delay(10);
    assert.equal(calls, 1);
  } finally { await h.observation.dispose(); }
});

test("unavailable retains the first locator until a later authorized event", async () => {
  let clock = 0;
  const calls = [];
  const h = harness({
    now: () => clock, retryMs: 100,
    observe: async (input) => {
      calls.push(input);
      return { status: calls.length === 1 ? "unavailable" : "logged" };
    },
  });
  try {
    h.emit("user.message", { data: { content: prompt } });
    await settle(h.observation);
    h.emit("session.idle");
    await delay(10);
    assert.equal(calls.length, 1);
    assert.equal(h.warnings.length, 1);
    clock = 101;
    h.emit("user.message", { data: { content: "Recovery: context-handoff task:other" } });
    await settle(h.observation);
    assert.equal(calls.length, 2);
    assert.equal(calls[1].locator, "task:receipt-1");
  } finally { await h.observation.dispose(); }
});

test("later reconciliation fills only a missing captured session ID", async () => {
  let clock = 0;
  const seen = [];
  const h = harness({
    now: () => clock, retryMs: 10,
    observe: async (input) => {
      seen.push({ ...input });
      return { status: input.sid ? "logged" : "unavailable" };
    },
  });
  try {
    h.state.sessionId = null;
    h.session.sessionId = null;
    h.runtime.snapshot.sessionId = "resolved-later";
    h.emit("user.message", { data: { content: prompt } });
    await settle(h.observation);
    assert.equal(seen.length, 0);
    h.state.sessionId = "resolved-later";
    h.session.sessionId = "resolved-later";
    clock = 11;
    h.emit("tool.execution_complete");
    await settle(h.observation);
    assert.equal(seen[0].sid, "resolved-later");
    assert.equal(seen[0].locator, "task:receipt-1");
  } finally { await h.observation.dispose(); }
});

test("dispose preserves uncertain exact identity for a later registration", async () => {
  const h = harness({
    retryMs: 0,
    observe: async ({ pending }) => {
      pending.uncertain = true;
      pending.subject = { creationNonce: "original", sessionId: "subject", locator: "task:receipt-1" };
      return { status: "unknown" };
    },
  });
  h.emit("user.message", { data: { content: prompt } });
  await settle(h.observation);
  await h.observation.dispose();
  assert.equal(h.state.receiptPending.observationState.uncertain, true);
  h.state.sessionId = "replacement";
  const next = registerPromptReceiptObservation(h.session, h.state, {
    observe: async ({ sid, pending }) => {
      assert.equal(sid, "subject");
      assert.equal(pending.subject.creationNonce, "original");
      assert.equal(pending.uncertain, true);
      return { status: "logged" };
    },
  });
  try {
    h.emit("session.idle");
    await settle(next);
    assert.equal(next.status.status, "logged");
    assert.equal(h.state.receiptPending, undefined);
  } finally { await next.dispose(); }
});

test("subagent events and later locators do not fabricate a first-prompt receipt", async () => {
  let calls = 0;
  const h = harness({ observe: async () => { calls++; return { status: "logged" }; } });
  try {
    h.emit("user.message", { agentId: "child", data: { content: prompt } });
    h.emit("user.message", { data: { content: "ordinary first message" } });
    h.emit("user.message", { data: { content: prompt } });
    await delay(10);
    assert.equal(calls, 0);
  } finally { await h.observation.dispose(); }
});

test("extension registers the tested boundary without a synchronous receipt logger", () => {
  const source = readFileSync(new URL("../extensions/context-handoff/extension.mjs", import.meta.url), "utf-8");
  assert.match(source, /registerNativePromptReceiptObservation\(session, state\)/);
  assert.doesNotMatch(source, /logHandoffPromptReceived/);
  assert.doesNotMatch(source, /session\.on\("user\.message"/);
});
