import { test } from "node:test";
import assert from "node:assert/strict";
import { createDriverServer } from "../extensions/agent-remote-driver/driver-server.mjs";
import { createSessionDriver } from "../extensions/agent-remote-driver/session-driver.mjs";

const ACCESS = "observation-key";
const ADMIN = "administrative-key";
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};
async function within(promise) {
  let timeout;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error("native-test boundary timed out")), 2_000); }),
    ]);
  } finally {
    clearTimeout(timeout);
  }
}

async function withTarget(fn, native = {}) {
  const calls = { send: [], abort: 0 };
  const listeners = new Set();
  let target;
  const session = {
    async send(options) {
      assert.equal(typeof options.prompt, "string");
      assert.match(options.source, /^agent-remote-driver-/);
      calls.send.push(options);
      if (native.send) return native.send(options, target);
      target.control.observe({
        type: "user.message", id: `remote-${calls.send.length}`,
        data: { source: options.source, content: options.prompt },
      });
      return { messageId: `message-${calls.send.length}` };
    },
    async abort() {
      calls.abort++;
      if (native.abort) return native.abort(target);
      return { aborted: true };
    },
  };
  target = createDriverServer({
    getSessionId: () => "session-1",
    getPid: () => 4242,
    token: ACCESS,
    controlToken: ADMIN,
    ...createSessionDriver(session),
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  });
  target.control.markReady();
  const port = await target.listen();
  const base = `http://127.0.0.1:${port}`;
  const scheme = "Bear" + "er ";
  const authorization = scheme + ACCESS;
  async function post(path, body) {
    const response = await fetch(`${base}${path}`, {
      method: "POST",
      headers: { authorization, "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5_000),
    });
    return { status: response.status, body: await response.json() };
  }
  async function health() {
    const response = await fetch(`${base}/health`, { headers: { authorization }, signal: AbortSignal.timeout(5_000) });
    assert.equal(response.status, 200);
    return response.json();
  }
  async function rehook(ownerId = "driver-a") {
    const response = await post("/control/re-hook", {
      target: (await health()).control, ownerId, controlToken: ADMIN,
    });
    assert.equal(response.status, 200, JSON.stringify(response.body));
    return response.body.lease;
  }
  try {
    await fn({ target, base, authorization, calls, post, health, rehook, emit: (event) => listeners.forEach((listener) => listener(event)) });
  } finally {
    native.cleanup?.();
    target.server.closeAllConnections();
    await target.close();
  }
}

test("observation access does not authorize grants or token-only mutation", async () => {
  await withTarget(async ({ post, health, calls }) => {
    const state = (await health()).control;
    assert.equal(state.mode, "human");
    const grant = await post("/control/re-hook", { target: state, ownerId: "driver-a", controlToken: ACCESS });
    assert.equal(grant.status, 403);
    const legacy = await post("/send", { content: "must not run" });
    assert.equal(legacy.status, 409);
    assert.equal(legacy.body.code, "driver_claim_required");
    assert.equal(calls.send.length, 0);
    assert.equal(calls.abort, 0);
    assert.equal(JSON.stringify(await health()).includes(ADMIN), false);
  });
});

test("current owners send with native provenance and cannot replay or skip sequences", async () => {
  await withTarget(async ({ rehook, post, health, calls }) => {
    const lease = await rehook();
    const sent = await post("/send", { lease, sequence: 1, content: "owner message", mode: "enqueue" });
    assert.equal(sent.status, 200);
    assert.deepEqual(sent.body.result, { messageId: "message-1" });
    assert.equal(sent.body.control.inFlight, null);
    assert.equal(sent.body.control.mode, "remote");
    assert.equal(calls.send[0].prompt, "owner message");
    assert.equal(calls.send[0].mode, "enqueue");
    assert.equal((await health()).control.pendingSources, 0);
    assert.equal((await post("/send", { lease, sequence: 1, content: "replay" })).body.code, "operation_replayed");
    assert.equal((await post("/send", { lease, sequence: 3, content: "gap" })).body.code, "operation_out_of_order");
    assert.equal(calls.send.length, 1);
    const aborted = await post("/abort", { lease, sequence: 2 });
    assert.equal(aborted.status, 200);
    assert.equal(calls.abort, 1);
    assert.equal(JSON.stringify(await health()).includes(lease.token), false);
  });
});

test("simultaneous contenders cannot both claim the released target", async () => {
  await withTarget(async ({ rehook, post, health, calls }) => {
    const old = await rehook();
    assert.equal((await post("/control/release", { lease: old, sequence: 1 })).status, 200);
    const target = (await health()).control;
    const contenders = await Promise.all(["driver-a", "driver-b"].map((ownerId) => post("/control/claim", {
      target, ownerId, controlToken: ADMIN,
    })));
    assert.deepEqual(contenders.map((entry) => entry.status).sort(), [200, 409]);
    const lease = contenders.find((entry) => entry.status === 200).body.lease;
    assert.equal((await post("/control/release", { lease: old, sequence: 2 })).status, 409);
    assert.equal((await post("/send", { lease, sequence: 1, content: "winner" })).status, 200);
    assert.equal(calls.send.length, 1);
  });
});

test("human takeover preserves observers and only an administrative fresh re-hook restores control", async () => {
  await withTarget(async ({ rehook, post, health, calls, target, base, authorization, emit }) => {
    const lease = await rehook();
    const observer = new AbortController();
    const response = await fetch(`${base}/events`, {
      headers: { authorization }, signal: AbortSignal.any([observer.signal, AbortSignal.timeout(5_000)]),
    });
    const reader = response.body.getReader();
    await within(reader.read());
    try {
      target.control.observe({ type: "user.message", id: "human-1", data: { source: "user", content: "taking over" } });
      assert.equal((await health()).control.mode, "human");
      assert.equal((await post("/send", { lease, sequence: 1, content: "stale" })).status, 409);
      assert.equal((await post("/control/claim", {
        target: (await health()).control, ownerId: "driver-a", controlToken: ADMIN,
      })).body.code, "human_controlled");
      const fresh = await rehook();
      assert.notEqual(fresh.token, lease.token);
      assert.notEqual(fresh.generation, lease.generation);
      assert.equal((await post("/send", { lease: fresh, sequence: 1, content: "hooked back up" })).status, 200);
      assert.equal(calls.send.length, 1);
      emit({ type: "assistant.message", data: { content: "observer stayed attached" } });
      const data = await within(reader.read());
      assert.match(new TextDecoder().decode(data.value), /observer stayed attached/);
    } finally {
      observer.abort();
      try {
        await reader.cancel();
      } catch (error) {
        if (error.name !== "AbortError") throw error;
      }
    }
  });
});

test("a takeover while steer awaits abort prevents the old driver's follow-up send", async () => {
  const started = deferred();
  const settle = deferred();
  await withTarget(async ({ rehook, post, health, target, calls }) => {
    const lease = await rehook();
    const pending = post("/steer", { lease, sequence: 1, content: "must not be sent" });
    await within(started.promise);
    target.control.observe({ type: "user.message", id: "human-race", data: { source: "user" } });
    assert.equal((await post("/control/re-hook", {
      target: (await health()).control, ownerId: "driver-b", controlToken: ADMIN,
    })).body.code, "control_busy");
    settle.resolve({ aborted: true });
    const response = await pending;
    assert.equal(response.status, 409);
    assert.equal(response.body.code, "driver_revoked");
    assert.deepEqual(response.body.effects, { abort: "completed", send: "not_started" });
    assert.equal(calls.abort, 1);
    assert.equal(calls.send.length, 0);
    const fresh = await rehook("driver-b");
    assert.equal((await post("/send", { lease: fresh, sequence: 1, content: "new owner" })).status, 200);
  }, {
    abort: async () => { started.resolve(); return settle.promise; },
    cleanup: () => settle.resolve({ aborted: true }),
  });
});

test("revocation after native send admission reports the accepted effect instead of false success", async () => {
  const started = deferred();
  const settle = deferred();
  await withTarget(async ({ rehook, post, target, calls }) => {
    const lease = await rehook();
    const pending = post("/send", { lease, sequence: 1, content: "already admitted" });
    await within(started.promise);
    target.control.observe({ type: "user.message", id: "human-after-dispatch", data: {} });
    settle.resolve({ messageId: "already-accepted" });
    const response = await pending;
    assert.equal(response.status, 409);
    assert.equal(response.body.effects.send, "completed");
    assert.equal(calls.send.length, 1);
    assert.equal((await post("/send", { lease, sequence: 2, content: "must not run" })).status, 409);
    assert.equal(calls.send.length, 1);
  }, {
    send: async () => { started.resolve(); return settle.promise; },
    cleanup: () => settle.resolve({ messageId: "already-accepted" }),
  });
});

test("a failed native request disables admission until native observation is re-established", async () => {
  await withTarget(async ({ rehook, post, health, target }) => {
    const lease = await rehook();
    const failed = await post("/send", { lease, sequence: 1, content: "uncertain outcome" });
    assert.equal(failed.status, 502);
    assert.equal(failed.body.code, "native_control_failed");
    assert.equal(failed.body.effects.send, "dispatched");
    assert.equal((await health()).control.ready, false);
    assert.equal((await post("/control/re-hook", {
      target: (await health()).control, ownerId: "driver-a", controlToken: ADMIN,
    })).status, 503);
    target.control.markReady();
    const fresh = await rehook();
    assert.notEqual(fresh.generation, lease.generation);
  }, { send: async () => { throw new Error("native connection lost"); } });
});
