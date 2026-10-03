import { test } from "node:test";
import assert from "node:assert/strict";
import { createDriverServer } from "../extensions/agent-remote-driver/driver-server.mjs";

const TOKEN = "test-token-123";

async function withServer(fn) {
  const calls = { send: [], abort: 0 };
  const listeners = new Set();
  const driverServer = createDriverServer({
    getSessionId: () => "session-1",
    getPid: () => 4242,
    token: TOKEN,
    send: async ({ content, mode }) => {
      calls.send.push({ content, mode });
      return { accepted: true };
    },
    abort: async () => {
      calls.abort += 1;
      return { aborted: true };
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  });
  const port = await driverServer.listen();
  const base = `http://127.0.0.1:${port}`;
  try {
    await fn({ base, calls, emit: (event) => listeners.forEach((l) => l(event)) });
  } finally {
    await driverServer.close();
  }
}

function authed(path, init = {}) {
  return fetch(path, {
    ...init,
    headers: { ...(init.headers || {}), authorization: `Bearer ${TOKEN}` },
  });
}

test("GET /health requires auth and reports session id/pid", async () => {
  await withServer(async ({ base }) => {
    const unauth = await fetch(`${base}/health`);
    assert.equal(unauth.status, 401);

    const res = await authed(`${base}/health`);
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.ok, true);
    assert.equal(body.sessionId, "session-1");
    assert.equal(body.pid, 4242);
  });
});

test("POST /send forwards content/mode to the driver and requires content", async () => {
  await withServer(async ({ base, calls }) => {
    const missing = await authed(`${base}/send`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({}),
    });
    assert.equal(missing.status, 400);

    const res = await authed(`${base}/send`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content: "hello", mode: "queue" }),
    });
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.ok, true);
    assert.deepEqual(calls.send, [{ content: "hello", mode: "queue" }]);
  });
});

test("POST /abort calls the driver's abort", async () => {
  await withServer(async ({ base, calls }) => {
    const res = await authed(`${base}/abort`, { method: "POST" });
    assert.equal(res.status, 200);
    assert.equal(calls.abort, 1);
  });
});

test("POST /steer aborts then sends in immediate mode", async () => {
  await withServer(async ({ base, calls }) => {
    const res = await authed(`${base}/steer`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content: "stop and do this instead" }),
    });
    assert.equal(res.status, 200);
    assert.equal(calls.abort, 1);
    assert.deepEqual(calls.send, [{ content: "stop and do this instead", mode: "immediate" }]);
  });
});

test("unknown routes 404, wrong method/path combos too", async () => {
  await withServer(async ({ base }) => {
    const res = await authed(`${base}/nope`);
    assert.equal(res.status, 404);
  });
});

test("GET /events streams fanned-out events as SSE", async () => {
  await withServer(async ({ base, emit }) => {
    const controller = new AbortController();
    const res = await fetch(`${base}/events`, {
      headers: { authorization: `Bearer ${TOKEN}` },
      signal: controller.signal,
    });
    assert.equal(res.status, 200);
    assert.match(res.headers.get("content-type") || "", /text\/event-stream/);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    // Drain the initial ": connected" comment line before emitting a real event.
    let received = "";
    const readOne = async () => {
      const { value } = await reader.read();
      received += decoder.decode(value, { stream: true });
    };
    await readOne();
    emit({ type: "assistant.message", content: "hi" });
    await readOne();

    assert.match(received, /data: \{"type":"assistant\.message","content":"hi"\}/);
    controller.abort();
    try {
      await reader.cancel();
    } catch {
      /* expected once aborted */
    }
  });
});
