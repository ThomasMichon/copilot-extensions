import { test } from "node:test";
import assert from "node:assert/strict";
import { createDriverServer, MAX_SSE_CLIENTS } from "../extensions/agent-remote-driver/driver-server.mjs";
import { createSessionDriver } from "../extensions/agent-remote-driver/session-driver.mjs";
import { readFile } from "node:fs/promises";
import { probeSendRejection } from "../../../tools/clean-room/scenarios/agent-remote-driver-solo/fixtures/send-rejection-probe.mjs";

const TOKEN = "test-token-123";

async function withServer(fn, extraOpts = {}) {
  const calls = { send: [], abort: 0, order: [] };
  const listeners = new Set();
  const session = {
    send: async (options) => {
      assert.equal(typeof options.prompt, "string", "SDK send requires prompt");
      assert.equal(Object.hasOwn(options, "content"), false);
      calls.send.push(options);
      calls.order.push("send");
      return { accepted: true };
    },
    abort: async () => {
      calls.abort += 1;
      calls.order.push("abort");
      return { aborted: true };
    },
  };
  const driverServer = createDriverServer({
    getSessionId: () => "session-1",
    getPid: () => 4242,
    token: TOKEN,
    ...createSessionDriver(session),
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    ...extraOpts,
  });
  const port = await driverServer.listen();
  const base = `http://127.0.0.1:${port}`;
  try {
    await fn({ base, calls, server: driverServer.server, emit: (event) => listeners.forEach((l) => l(event)) });
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

test("Tier-P scenario /send probe authenticates and rejects content before any SDK send", async () => {
  await withServer(async ({ base, calls, server }) => {
    const requestDone = new Promise((resolve) => {
      server.once("request", (request) => {
        let body = "";
        request.on("data", (chunk) => { body += chunk; });
        request.on("end", () => resolve({ method: request.method, path: request.url, body }));
      });
    });
    const descriptor = { host: "127.0.0.1", port: Number(new URL(base).port), token: TOKEN };
    const result = await probeSendRejection(descriptor);
    assert.deepEqual(await requestDone, { method: "POST", path: "/send", body: "{}" });
    assert.deepEqual(result, { status: 400, error: "content (string) is required" });
    assert.deepEqual(calls.send, []);
    assert.equal(calls.abort, 0);
    assert.deepEqual(calls.order, []);
  });
});

test("Tier-P scenario /send probe does not mistake authentication failure for content rejection", async () => {
  await withServer(async ({ base, calls }) => {
    const descriptor = { host: "127.0.0.1", port: Number(new URL(base).port), token: "wrong-test-token" };
    await assert.rejects(probeSendRejection(descriptor), /expected HTTP 400 content-validation rejection/);
    assert.deepEqual(calls.send, []);
  });
});

test("Tier-P scenario invokes the shared rejection probe and matches its manifest stage", async () => {
  const root = new URL("../../../tools/clean-room/scenarios/agent-remote-driver-solo/", import.meta.url);
  const scenario = await readFile(new URL("scenario.sh", root), "utf8");
  const manifest = JSON.parse(await readFile(new URL("manifest.json", root), "utf8"));
  assert.ok(scenario.includes('capture send-rejection -- node "$_SELF_DIR/fixtures/send-rejection-probe.mjs" "$DESCRIPTOR"'));
  assert.ok(scenario.includes(`phase 3 "${manifest.stages.find((stage) => stage.n === 3).title}"`));
  assert.equal(scenario.includes("clean-room probe, no reply needed"), false);
});

test("POST /send maps HTTP content/mode to SDK prompt/mode and requires content", async () => {
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
      body: JSON.stringify({ content: "hello", mode: "enqueue" }),
    });
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.ok, true);
    assert.deepEqual(body.result, { accepted: true });
    assert.deepEqual(calls.send, [{ prompt: "hello", mode: "enqueue" }]);
  });
});

test("POST /send preserves the SDK default when HTTP mode is omitted", async () => {
  await withServer(async ({ base, calls }) => {
    const res = await authed(`${base}/send`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content: "use the default mode" }),
    });
    assert.equal(res.status, 200);
    assert.deepEqual(calls.send, [{ prompt: "use the default mode", mode: undefined }]);
  });
});

test("POST /abort calls the driver's abort", async () => {
  await withServer(async ({ base, calls }) => {
    const res = await authed(`${base}/abort`, { method: "POST" });
    assert.equal(res.status, 200);
    assert.equal(calls.abort, 1);
  });
});

test("POST /steer aborts then maps HTTP content to SDK prompt in immediate mode", async () => {
  await withServer(async ({ base, calls }) => {
    const res = await authed(`${base}/steer`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content: "stop and do this instead" }),
    });
    assert.equal(res.status, 200);
    assert.deepEqual((await res.json()).result, { accepted: true });
    assert.equal(calls.abort, 1);
    assert.deepEqual(calls.order, ["abort", "send"]);
    assert.deepEqual(calls.send, [{ prompt: "stop and do this instead", mode: "immediate" }]);
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
      // would be the actual failure this test guards against. Each read is
      // raced against a short deadline so a regression (the server no
      // longer disconnects) fails this test deterministically instead of
      // hanging the whole process.
      const READ_TIMEOUT_MS = 2_000;
      const readOrTimeout = () =>
        Promise.race([
          reader.read(),
          new Promise((_, reject) => setTimeout(() => reject(new Error("read() timed out")), READ_TIMEOUT_MS)),
        ]);

      let disconnected = false;
      try {
        for (let i = 0; i < 20 && !disconnected; i += 1) {
          const result = await readOrTimeout();
          disconnected = result.done;
        }
      } catch (e) {
        assert.doesNotMatch(e.message, /timed out/, "server never disconnected the over-backlogged client");
        disconnected = true; // the abrupt destroy() surfaced as a read error -- also a disconnect
      }
      assert.equal(disconnected, true, "expected the over-backlogged connection to be closed by the server");
    },
    { maxSseBufferedBytes: 1 },
  );
});
