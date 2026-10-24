import { test } from "node:test";
import assert from "node:assert/strict";
import { parse } from "node:path";
import {
  createNativeReceiptPendingSlot, RECEIPT_METADATA_PREFIX,
} from "../extensions/context-handoff/prompt-receipt-state.mjs";
import { nativeClientMetadata, pendingIdentity, context, until } from "./fixtures/native-client-metadata.mjs";

async function capture(slot, locator = "task:one") {
  return slot.capture({ sessionId: "subject", locator }, context());
}

test("a checked first prompt without recovery is terminal without a qualified record", async () => {
  const runtime = nativeClientMetadata();
  const slot = createNativeReceiptPendingSlot(runtime.client());
  const checked = await slot.capture({ sessionId: "subject", locator: null }, context());
  assert.equal(checked.status, "checked");
  assert.equal((await createNativeReceiptPendingSlot(runtime.client()).restore(context())).status, "checked");
  await assert.rejects(slot.qualify(pendingIdentity(), context()), /conflict/);
  assert.equal(Object.keys(runtime.bag).length, 1);
});

test("native adapter uses three deterministic records and preserves foreign metadata", async () => {
  const runtime = nativeClientMetadata({ initial: { "other/client": "private foreign value" } });
  const slot = createNativeReceiptPendingSlot(runtime.client());
  assert.equal((await slot.restore(context())).status, "empty");
  await capture(slot);
  const pending = pendingIdentity();
  assert.equal((await slot.qualify(pending, context())).status, "uncertain");
  assert.equal((await slot.confirm(pending, context())).status, "confirmed");
  const before = { ...runtime.bag };
  assert.equal((await slot.qualify(pending, context())).status, "confirmed");
  assert.equal((await slot.confirm(pending, context())).status, "confirmed");
  assert.deepEqual(runtime.bag, before);
  assert.equal(runtime.bag["other/client"], "private foreign value");
  assert.equal(Object.keys(runtime.bag).filter((key) => key.startsWith(RECEIPT_METADATA_PREFIX)).length, 3);
  for (const call of runtime.calls.filter((entry) => entry.method === "update")) {
    assert.deepEqual(Object.keys(call.patch), ["set"]);
    for (const [key, value] of Object.entries(call.patch.set)) {
      assert.ok(key.startsWith(RECEIPT_METADATA_PREFIX) && Buffer.byteLength(key) <= 256);
      assert.ok(Buffer.byteLength(value) <= 8192);
    }
  }
  const fresh = createNativeReceiptPendingSlot(runtime.client());
  assert.equal((await fresh.restore(context())).status, "confirmed");
});

test("parallel native clients merge identical immutable writes without CAS or lost keys", async () => {
  const runtime = nativeClientMetadata({ initial: { foreign: "untouched" } });
  const a = createNativeReceiptPendingSlot(runtime.client());
  const b = createNativeReceiptPendingSlot(runtime.client());
  await Promise.all([capture(a), capture(b)]);
  await Promise.all([a.qualify(pendingIdentity(), context()), b.qualify(pendingIdentity(), context())]);
  await Promise.all([a.confirm(pendingIdentity(), context()), b.confirm(pendingIdentity(), context())]);
  assert.equal((await a.restore(context())).status, "confirmed");
  assert.equal(runtime.bag.foreign, "untouched");
  assert.equal(Object.keys(runtime.bag).length, 4);
});

test("confirmed marker wins a late same-identity possibly-sent write", async () => {
  const runtime = nativeClientMetadata();
  const a = createNativeReceiptPendingSlot(runtime.client());
  const b = createNativeReceiptPendingSlot(runtime.client());
  await capture(a);
  let release;
  let blocked = false;
  runtime.beforeUpdate = async (patch) => {
    if (!blocked && Object.keys(patch.set)[0].includes("/qualified/")) {
      blocked = true;
      await new Promise((resolve) => { release = resolve; });
    }
  };
  const late = a.qualify(pendingIdentity(), context());
  await until(() => release);
  await b.qualify(pendingIdentity(), context());
  await b.confirm(pendingIdentity(), context());
  release();
  assert.equal((await late).status, "confirmed");
  assert.equal((await createNativeReceiptPendingSlot(runtime.client()).restore(context())).status, "confirmed");
});

test("disposal deadline retains uncancellable RPC ownership until actual settlement", async () => {
  const runtime = nativeClientMetadata();
  const session = runtime.client();
  const slot = createNativeReceiptPendingSlot(session);
  await capture(slot);
  let release;
  runtime.beforeUpdate = () => new Promise((resolve) => { release = resolve; });
  const attempt = context();
  const operation = slot.qualify(pendingIdentity(), attempt);
  await until(() => release);
  attempt.controller.abort();
  await assert.rejects(operation, /cancelled/);
  assert.equal(slot.busy, true);
  assert.equal(createNativeReceiptPendingSlot(session), slot);
  const callCount = runtime.calls.length;
  await assert.rejects(slot.restore(context()), /inflight/);
  assert.equal(runtime.calls.length, callCount);
  release();
  await until(() => !slot.busy);
  assert.equal((await slot.restore(context())).status, "uncertain");
});

test("conflicting parallel captures remain preserved and forbid new qualification", async () => {
  const runtime = nativeClientMetadata();
  const a = createNativeReceiptPendingSlot(runtime.client());
  const b = createNativeReceiptPendingSlot(runtime.client());
  await Promise.allSettled([capture(a, "task:one"), capture(b, "task:two")]);
  await assert.rejects(a.restore(context()), /conflict/);
  const before = { ...runtime.bag };
  await assert.rejects(a.qualify(pendingIdentity(), context()), /conflict/);
  assert.deepEqual(runtime.bag, before);
});

test("different qualified identities are not overwritten or chosen from current binding", async () => {
  const runtime = nativeClientMetadata();
  const slot = createNativeReceiptPendingSlot(runtime.client());
  await capture(slot);
  await slot.qualify(pendingIdentity(), context());
  const changed = pendingIdentity();
  changed.subject.creationNonce = "different";
  const before = { ...runtime.bag };
  await assert.rejects(slot.qualify(changed, context()), /conflict/);
  assert.deepEqual(runtime.bag, before);
});

test("conflicting qualified clients cannot both admit mutation without CAS", async () => {
  const runtime = nativeClientMetadata();
  const a = createNativeReceiptPendingSlot(runtime.client());
  const b = createNativeReceiptPendingSlot(runtime.client());
  await capture(a);
  const other = pendingIdentity();
  other.subject.creationNonce = "other-incarnation";
  const results = await Promise.allSettled([
    a.qualify(pendingIdentity(), context()), b.qualify(other, context()),
  ]);
  assert.ok(results.filter((result) => result.status === "fulfilled").length <= 1);
  await assert.rejects(a.restore(context()), /conflict/);
  assert.equal(Object.keys(runtime.bag).length, 3);
});

test("record admission verifies an atomic committed bag rather than a stale read", async () => {
  const runtime = nativeClientMetadata();
  const session = runtime.client();
  const slot = createNativeReceiptPendingSlot(session);
  await capture(slot);
  await slot.qualify(pendingIdentity(), context());
  const stale = { ...runtime.bag };
  const other = nativeClientMetadata();
  const otherSlot = createNativeReceiptPendingSlot(other.client());
  await capture(otherSlot);
  const changed = pendingIdentity();
  changed.subject.creationNonce = "other";
  await otherSlot.qualify(changed, context());
  runtime.bag = { ...runtime.bag, ...other.bag };
  session.rpc.metadata.getClientMetadata = async () => ({ ...stale });
  const writes = runtime.calls.filter((call) => call.method === "update").length;
  await assert.rejects(slot.qualify(pendingIdentity(), context()), /conflict/);
  assert.equal(runtime.calls.filter((call) => call.method === "update").length, writes + 1);
  assert.equal(Object.keys(runtime.bag).length, 3);
});

test("a stale read-back cannot downgrade a committed confirmed marker", async () => {
  const runtime = nativeClientMetadata();
  const first = createNativeReceiptPendingSlot(runtime.client());
  await capture(first);
  await first.qualify(pendingIdentity(), context());
  const stale = { ...runtime.bag };
  await first.confirm(pendingIdentity(), context());
  const session = runtime.client();
  session.rpc.metadata.getClientMetadata = async () => ({ ...stale });
  const next = createNativeReceiptPendingSlot(session);
  await assert.rejects(next.qualify(pendingIdentity(), context()), /unverified/);
  assert.equal(Object.keys(runtime.bag).length, 3);
});

test("a foreign concurrent patch is not lost by an owned immutable set", async () => {
  const runtime = nativeClientMetadata();
  const slot = createNativeReceiptPendingSlot(runtime.client());
  let release;
  runtime.beforeUpdate = (patch) => {
    if (Object.keys(patch.set)[0].startsWith(RECEIPT_METADATA_PREFIX)) {
      return new Promise((resolve) => { release = resolve; });
    }
  };
  const operation = capture(slot);
  await until(() => release);
  await runtime.client().rpc.metadata.updateClientMetadata({ set: { "other/new": "PRIVATE" } });
  release();
  await operation;
  assert.equal(runtime.bag["other/new"], "PRIVATE");
});

test("qualified owned values over8KiB fail without another write", async () => {
  const runtime = nativeClientMetadata();
  const slot = createNativeReceiptPendingSlot(runtime.client());
  await capture(slot);
  const pending = pendingIdentity();
  pending.installation.sourceFingerprint = "x".repeat(4096);
  const root = parse(process.cwd()).root;
  pending.runtimeRoot = root + "r".repeat(4096 - Buffer.byteLength(root));
  const before = { ...runtime.bag };
  await assert.rejects(slot.qualify(pending, context()), /capacity/);
  assert.deepEqual(runtime.bag, before);
  assert.equal(runtime.calls.filter((call) => call.method === "update").length, 1);
});

for (const fault of ["unsupported", "remote", "wrong-session", "bad-bag", "bad-owned", "capacity",
  "oversize-value", "oversize-key", "serialized-capacity", "namespace-count"]) {
  test(`${fault} fails closed without metadata mutation or foreign eviction`, async () => {
    const runtime = nativeClientMetadata();
    const session = runtime.client();
    if (fault === "unsupported") delete session.rpc.metadata.updateClientMetadata;
    if (fault === "remote") runtime.snapshot.isRemote = true;
    if (fault === "wrong-session") runtime.snapshot.sessionId = "foreign";
    if (fault === "bad-bag") session.rpc.metadata.getClientMetadata = async () => null;
    if (fault === "bad-owned") runtime.bag[`${RECEIPT_METADATA_PREFIX}capture/bad`] = "{}";
    if (fault === "capacity") {
      for (let i = 0; i < 128; i++) runtime.bag[`foreign/${i}`] = "private";
    }
    if (fault === "oversize-value") runtime.bag.foreign = "x".repeat(16385);
    if (fault === "oversize-key") runtime.bag["x".repeat(257)] = "PRIVATE";
    if (fault === "serialized-capacity") {
      for (let i = 0; i < 3; i++) runtime.bag[`foreign/${i}`] = '"'.repeat(12000);
    }
    if (fault === "namespace-count") {
      for (let i = 0; i < 4; i++) runtime.bag[`${RECEIPT_METADATA_PREFIX}bad/${i}`] = "{}";
    }
    const before = { ...runtime.bag };
    await assert.rejects(capture(createNativeReceiptPendingSlot(session)));
    assert.equal(runtime.calls.filter((call) => call.method === "update").length, 0);
    assert.deepEqual(runtime.bag, before);
  });
}

test("unsupported RPC errors and failed/false committed replies are not persistence", async () => {
  for (const fault of ["method", "failure", "false-commit"]) {
    const runtime = nativeClientMetadata();
    const session = runtime.client();
    session.rpc.metadata.updateClientMetadata = async () => {
      if (fault === "method") throw Object.assign(new Error("private"), { code: -32601 });
      if (fault === "failure") throw new Error("private metadata error");
      return {};
    };
    await assert.rejects(capture(createNativeReceiptPendingSlot(session)),
      fault === "method" ? /unsupported/ : fault === "failure" ? /unavailable/ : /unverified/);
  }
});
