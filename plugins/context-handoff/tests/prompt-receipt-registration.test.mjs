import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import childProcess from "node:child_process";
import { syncBuiltinESMExports } from "node:module";
import { registerNativePromptReceiptObservation } from "../extensions/context-handoff/prompt-receipt.mjs";
import { createNativeReceiptPendingSlot } from "../extensions/context-handoff/prompt-receipt-state.mjs";
import { residentFixture } from "./fixtures/resident-control.mjs";
import { nativeClientMetadata, pendingIdentity, until } from "./fixtures/native-client-metadata.mjs";

let helpers = 0;
for (const key of ["exec", "execFile", "spawn", "fork", "execSync", "execFileSync", "spawnSync"]) {
  childProcess[key] = () => { helpers++; throw new Error("registration helper forbidden"); };
}
syncBuiltinESMExports();
const state = () => ({ turnCount: 0, sessionId: "subject", cwd: process.cwd() });
const prompt = (session) => session.emit("user.message", {
  data: { content: "Continue | Recovery: context-handoff task:one" },
});
async function settled(observation) {
  await until(() => !["idle", "pending"].includes(observation.status.status));
}

test("a non-recovery first prompt is not forgotten by fresh extension state", async () => {
  const runtime = nativeClientMetadata();
  let calls = 0;
  const observe = async () => { calls++; return { status: "logged" }; };
  const session = runtime.client();
  const first = registerNativePromptReceiptObservation(session, state(), { observe });
  let next;
  try {
    session.emit("user.message", { data: { content: "ordinary-first" } });
    await until(() => first.status.reason === "metadata-first-prompt-checked");
    assert.equal(first.status.status, "idle");
    await first.dispose();
    const resumed = runtime.client();
    next = registerNativePromptReceiptObservation(resumed, state(), { observe });
    prompt(resumed);
    await settled(next);
    assert.equal(next.status.status, "unknown");
    assert.equal(calls, 0);
    assert.equal(Object.keys(runtime.bag).length, 1);
    assert.equal(JSON.stringify(runtime.bag).includes("ordinary-first"), false);
  } finally { await first.dispose(); await next?.dispose(); }
});

test("production registration adapter restores fresh state and queries an exact lost reply", async () => {
  const f = await residentFixture();
  const runtime = nativeClientMetadata({ initial: { "other/client": "PRIVATE" } });
  let first;
  let second;
  let third;
  try {
    f.dropRecordReply = true;
    const session = runtime.client();
    let injectedStoreCalls = 0;
    first = registerNativePromptReceiptObservation(session, state(), {
      sessionDir: f.sessionDir, retryMs: 0,
      pendingSlot: { restore() { injectedStoreCalls++; throw new Error("mock store forbidden"); } },
    });
    assert.equal(runtime.calls.length, 0);
    prompt(session);
    await settled(first);
    assert.equal(first.status.status, "unknown");
    assert.equal(injectedStoreCalls, 0);
    assert.equal(f.effects.size, 1);
    await first.dispose();
    f.dropRecordReply = false;
    second = registerNativePromptReceiptObservation(runtime.client(), state(), {
      sessionDir: f.sessionDir, retryMs: 0,
    });
    await settled(second);
    assert.equal(second.status.status, "logged");
    assert.deepEqual(f.requests.filter((request) => request.action === "handoff-prompt-receipt")
      .map((request) => request.payload.operation), ["record", "query"]);
    assert.equal(f.effects.size, 1);
    await second.dispose();
    const before = f.requests.length;
    const resumed = runtime.client();
    third = registerNativePromptReceiptObservation(resumed, state(), { sessionDir: f.sessionDir });
    prompt(resumed);
    await settled(third);
    assert.equal(third.status.status, "logged");
    assert.equal(f.requests.length, before);
    assert.equal(runtime.bag["other/client"], "PRIVATE");
    assert.equal(helpers, 0);
  } finally {
    await first?.dispose(); await second?.dispose(); await third?.dispose();
    await f.close();
  }
});

for (const changed of ["nonce", "source", "root"]) {
  test(`fresh state refuses ${changed} redirection of persisted uncertainty`, async () => {
    const f = await residentFixture();
    const runtime = nativeClientMetadata();
    let first;
    let next;
    try {
      f.dropRecordReply = true;
      const session = runtime.client();
      first = registerNativePromptReceiptObservation(session, state(), { sessionDir: f.sessionDir });
      prompt(session);
      await settled(first);
      assert.equal(first.status.status, "unknown");
      await first.dispose();
      const before = f.requests.length;
      if (changed === "nonce") { f.subjectBinding.creationNonce = "changed"; f.writeBinding(); }
      if (changed === "source") {
        f.candidate.sourceFingerprint = f.catalog.binding.source.fingerprint =
          f.catalog.binding.installation.sourceFingerprint = "changed";
        f.writeCatalog();
      }
      if (changed === "root") {
        const replacement = f.runtimeRoot + "-different";
        f.candidate.runtimeRoot = f.catalog.binding.installation.runtimeRoot = replacement;
        f.candidate.activeRoutePath = replacement + (process.platform === "win32" ? "\\active.json" : "/active.json");
        f.candidate.tokenPath = replacement + (process.platform === "win32" ? "\\token" : "/token");
        f.writeCatalog();
      }
      next = registerNativePromptReceiptObservation(runtime.client(), state(), { sessionDir: f.sessionDir });
      await settled(next);
      assert.equal(next.status.status, "unknown");
      assert.equal(f.requests.length, before);
      assert.equal(f.effects.size, 1);
    } finally { await first?.dispose(); await next?.dispose(); await f.close(); }
  });
}

for (const confirmed of [false, true]) {
  test(`a different fresh capture cannot replace the ${confirmed ? "confirmed" : "uncertain"} first slot`, async () => {
    const f = await residentFixture();
    const runtime = nativeClientMetadata();
    let first;
    let next;
    try {
      f.dropRecordReply = !confirmed;
      const session = runtime.client();
      first = registerNativePromptReceiptObservation(session, state(), { sessionDir: f.sessionDir });
      prompt(session);
      await settled(first);
      assert.equal(first.status.status, confirmed ? "logged" : "unknown");
      await first.dispose();
      const before = { ...runtime.bag };
      const requests = f.requests.length;
      const fresh = runtime.client();
      next = registerNativePromptReceiptObservation(fresh, state(), { sessionDir: f.sessionDir });
      fresh.emit("user.message", { data: { content: "Recovery: context-handoff task:other" } });
      await settled(next);
      assert.equal(next.status.status, "unknown");
      assert.equal(next.status.reason, "pending-identity-conflict");
      assert.deepEqual(runtime.bag, before);
      assert.equal(f.requests.length, requests);
    } finally { await first?.dispose(); await next?.dispose(); await f.close(); }
  });
}

test("failed pre-send native persistence produces zero record sockets/effects", async () => {
  const f = await residentFixture();
  const runtime = nativeClientMetadata();
  runtime.beforeUpdate = (patch) => {
    if (Object.keys(patch.set)[0].includes("/qualified/")) throw new Error("PRIVATE metadata content");
  };
  const session = runtime.client();
  const observation = registerNativePromptReceiptObservation(session, state(), { sessionDir: f.sessionDir });
  try {
    prompt(session);
    await settled(observation);
    assert.equal(observation.status.status, "unknown");
    assert.equal(f.effects.size, 0);
    assert.equal(f.requests.every((request) => request.action === "health"), true);
    assert.equal(runtime.warnings.some((warning) => warning.includes("PRIVATE")), false);
  } finally { await observation.dispose(); await f.close(); }
});

test("lost native records do not turn known in-memory uncertainty into a new capture", async () => {
  const f = await residentFixture();
  const runtime = nativeClientMetadata();
  const session = runtime.client();
  const shared = state();
  let first;
  let next;
  try {
    f.dropRecordReply = true;
    first = registerNativePromptReceiptObservation(session, shared, { sessionDir: f.sessionDir });
    prompt(session);
    await settled(first);
    assert.equal(first.status.status, "unknown");
    await first.dispose();
    runtime.bag = {};
    const calls = f.requests.length;
    const writes = runtime.calls.filter((entry) => entry.method === "update").length;
    next = registerNativePromptReceiptObservation(session, shared, { sessionDir: f.sessionDir });
    await settled(next);
    assert.equal(next.status.status, "unknown");
    assert.equal(next.status.reason, "pending-record-invalid");
    assert.equal(f.requests.length, calls);
    assert.equal(runtime.calls.filter((entry) => entry.method === "update").length, writes);
  } finally { await first?.dispose(); await next?.dispose(); await f.close(); }
});

test("fresh capture-only state cannot qualify a replacement binding after failed persistence", async () => {
  const f = await residentFixture();
  const runtime = nativeClientMetadata();
  let first;
  let next;
  try {
    runtime.beforeUpdate = (patch) => {
      if (Object.keys(patch.set).some((key) => key.includes("/qualified/"))) throw new Error("storage");
    };
    const session = runtime.client();
    first = registerNativePromptReceiptObservation(session, state(), { sessionDir: f.sessionDir });
    prompt(session);
    await settled(first);
    assert.equal(f.effects.size, 0);
    assert.equal(Object.keys(runtime.bag).length, 1);
    await first.dispose();
    runtime.beforeUpdate = null;
    f.subjectBinding.creationNonce = "replacement";
    f.writeBinding();
    const calls = f.requests.length;
    next = registerNativePromptReceiptObservation(runtime.client(), state(), { sessionDir: f.sessionDir });
    await settled(next);
    assert.equal(next.status.reason, "pending-qualified-identity-unavailable");
    assert.equal(f.requests.length, calls);
    assert.equal(f.effects.size, 0);
    assert.equal(Object.keys(runtime.bag).length, 1);
  } finally { await first?.dispose(); await next?.dispose(); await f.close(); }
});

test("registration binds capture to the actual SDK session, not a proposal state SID", async () => {
  const f = await residentFixture();
  const runtime = nativeClientMetadata();
  const session = runtime.client();
  const proposal = { ...state(), sessionId: "proposal-foreign" };
  const observation = registerNativePromptReceiptObservation(session, proposal, { sessionDir: f.sessionDir });
  try {
    prompt(session);
    await settled(observation);
    assert.equal(observation.status.status, "logged");
    const request = f.requests.find((entry) => entry.action === "handoff-prompt-receipt");
    assert.equal(request.payload.subject.sessionId, "subject");
    assert.equal(JSON.stringify(runtime.bag).includes("proposal-foreign"), false);
  } finally { await observation.dispose(); await f.close(); }
});

for (const changed of ["source", "nonce"]) {
  test(`${changed} change during pending commit blocks record socket admission`, async () => {
    const f = await residentFixture();
    const runtime = nativeClientMetadata();
    runtime.afterUpdate = (patch) => {
      if (!Object.keys(patch.set)[0].includes("/qualified/")) return;
      if (changed === "source") {
        f.candidate.sourceFingerprint = f.catalog.binding.source.fingerprint =
          f.catalog.binding.installation.sourceFingerprint = "changed";
        f.writeCatalog();
      } else {
        f.subjectBinding.creationNonce = "changed";
        f.writeBinding();
      }
    };
    const session = runtime.client();
    const observation = registerNativePromptReceiptObservation(session, state(), { sessionDir: f.sessionDir });
    try {
      prompt(session);
      await settled(observation);
      assert.equal(observation.status.status, "unknown");
      assert.equal(f.effects.size, 0);
      assert.equal(f.requests.every((request) => request.action === "health"), true);
    } finally { await observation.dispose(); await f.close(); }
  });
}

for (const fault of ["unsupported", "unsupported-rpc", "remote", "corrupt", "capacity"]) {
  test(`actual registration ${fault} is unavailable, never in-memory persisted`, async () => {
    const runtime = nativeClientMetadata();
    const session = runtime.client();
    let calls = 0;
    if (fault === "unsupported") delete session.rpc.metadata.getClientMetadata;
    if (fault === "unsupported-rpc") session.rpc.metadata.getClientMetadata = async () => {
      throw Object.assign(new Error("PRIVATE"), { code: -32601 });
    };
    if (fault === "remote") runtime.snapshot.isRemote = true;
    if (fault === "corrupt") runtime.bag["context-handoff/prompt-receipt/v1/bad"] = "PRIVATE";
    if (fault === "capacity") for (let i = 0; i < 128; i++) runtime.bag[`foreign/${i}`] = "PRIVATE";
    const observation = registerNativePromptReceiptObservation(session, state(), {
      observe: async () => { calls++; return { status: "logged" }; },
    });
    try {
      prompt(session);
      await settled(observation);
      assert.equal(observation.status.status, "unavailable");
      assert.equal(calls, 0);
      assert.equal(runtime.calls.filter((entry) => entry.method === "update").length, 0);
      if (fault === "remote") assert.equal(runtime.calls.filter((entry) => entry.method === "get").length, 0);
      assert.equal(runtime.warnings.some((warning) => warning.includes("PRIVATE") || warning.includes("foreign/")), false);
    } finally { await observation.dispose(); }
  });
}

test("disposed qualification RPC settles before re-registration; restored slot queries first", async () => {
  const runtime = nativeClientMetadata();
  const session = runtime.client();
  const slot = createNativeReceiptPendingSlot(session);
  let release;
  runtime.beforeUpdate = (patch) => {
    if (Object.keys(patch.set)[0].includes("/qualified/") && !release) {
      return new Promise((resolve) => { release = resolve; });
    }
  };
  let records = 0;
  let queries = 0;
  const observe = async ({ sid, locator, pending, beforeRecord }) => {
    if (pending.uncertain) {
      queries++;
      pending.uncertain = false;
      return { status: "absent" };
    }
    Object.assign(pending, pendingIdentity(sid, locator));
    await beforeRecord(pending);
    records++;
    pending.uncertain = false;
    return { status: "logged" };
  };
  const first = registerNativePromptReceiptObservation(session, state(), { observe, retryMs: 0 });
  prompt(session);
  await until(() => release);
  await first.dispose();
  assert.equal(records, 0);
  assert.equal(slot.busy, true);
  const next = registerNativePromptReceiptObservation(session, state(), { observe, retryMs: 0 });
  try {
    await settled(next);
    assert.equal(next.status.status, "unavailable");
    release();
    await until(() => !slot.busy);
    session.emit("session.idle");
    await until(() => next.status.status === "absent");
    assert.equal(queries, 1);
    assert.equal(records, 0);
    session.emit("tool.execution_complete");
    await until(() => next.status.status === "logged");
    assert.equal(records, 1);
  } finally { release(); await next.dispose(); }
});

test("extension invokes the actual native adapter registration, not a mock store", () => {
  const extension = readFileSync(new URL("../extensions/context-handoff/extension.mjs", import.meta.url), "utf8");
  assert.match(extension, /registerNativePromptReceiptObservation\(session, state\)/u);
  assert.equal(helpers, 0);
});
