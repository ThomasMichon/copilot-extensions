import { test } from "node:test";
import assert from "node:assert/strict";
import { createDriverServer, MAX_SSE_CLIENTS } from "../extensions/agent-remote-driver/driver-server.mjs";

const TOKEN = "test-token-123";

async function withServer(fn, extraOpts = {}) {
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
    ...extraOpts,
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

test("GET /events rejects a connection past MAX_SSE_CLIENTS with 503", async () => {
  await withServer(async ({ base }) => {
    const controllers = [];
    const opens = [];
    try {
      // Open exactly MAX_SSE_CLIENTS connections -- all should succeed.
      for (let i = 0; i < MAX_SSE_CLIENTS; i += 1) {
        const controller = new AbortController();
        controllers.push(controller);
        const res = await authed(`${base}/events`, { signal: controller.signal });
        opens.push(res);
        assert.equal(res.status, 200, `connection ${i} should be accepted`);
      }
      // One more must be refused, not silently accepted or hung.
      const over = await authed(`${base}/events`);
      assert.equal(over.status, 503);
      const body = await over.json();
      assert.equal(body.ok, false);
      assert.match(body.error, /too many concurrent/);
    } finally {
      for (const c of controllers) c.abort();
    }
  });
});

test("GET /events disconnects a client whose buffered backlog exceeds maxSseBufferedBytes", async () => {
  // A tiny cap (independent of any real network backpressure) exercises the
  // mechanism itself: res.writableLength is nonzero immediately after any
  // write, so a 1-byte cap deterministically trips the check without
  // needing a genuinely slow/non-reading consumer.
  await withServer(
    async ({ base, emit }) => {
      const res = await authed(`${base}/events`);
      assert.equal(res.status, 200);
      const reader = res.body.getReader();
      // Drain the initial ": connected" comment so the stream is established.
      await reader.read();

      emit({ type: "assistant.message", content: "this write alone exceeds the 1-byte cap" });

      // The server should destroy this connection -- either the stream ends
      // cleanly (done: true) or the abrupt server-side destroy surfaces as a
      // socket-level read error on the client, depending on timing. Either
      // outcome proves the connection did not survive to keep buffering
      // further events indefinitely; a read that keeps succeeding forever
      // would be the actual failure this test guards against.
      let disconnected = false;
      try {
        for (let i = 0; i < 20 && !disconnected; i += 1) {
          const result = await reader.read();
          disconnected = result.done;
        }
      } catch {
        disconnected = true; // the abrupt destroy() surfaced as a read error -- also a disconnect
      }
      assert.equal(disconnected, true, "expected the over-backlogged connection to be closed by the server");
    },
    { maxSseBufferedBytes: 1 },
  );
});
