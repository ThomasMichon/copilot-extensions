import { test } from "node:test";
import assert from "node:assert/strict";
import { boundedRetry, installSignalCleanup, createIntervalTimer } from "../extensions/agent-remote-driver/lifecycle.mjs";

// --- boundedRetry ---

test("boundedRetry returns the first successful attempt's result immediately", async () => {
  let calls = 0;
  const result = await boundedRetry(async () => {
    calls += 1;
    return "ok";
  }, 3);
  assert.equal(result, "ok");
  assert.equal(calls, 1);
});

test("boundedRetry retries on failure and succeeds within the bound", async () => {
  let calls = 0;
  const result = await boundedRetry(async () => {
    calls += 1;
    if (calls < 3) throw new Error(`fail ${calls}`);
    return "ok";
  }, 5);
  assert.equal(result, "ok");
  assert.equal(calls, 3);
});

test("boundedRetry never exceeds maxAttempts -- the anti-process-bombing bound", async () => {
  let calls = 0;
  await assert.rejects(
    boundedRetry(async () => {
      calls += 1;
      throw new Error(`fail ${calls}`);
    }, 3),
    /fail 3/,
  );
  assert.equal(calls, 3); // exactly the bound, never more
});

test("boundedRetry calls onAttemptFailed for each failed attempt with (attempt, error)", async () => {
  const seen = [];
  await assert.rejects(
    boundedRetry(
      async (attempt) => {
        throw new Error(`fail-${attempt}`);
      },
      2,
      (attempt, e) => seen.push([attempt, e.message]),
    ),
  );
  assert.deepEqual(seen, [
    [1, "fail-1"],
    [2, "fail-2"],
  ]);
});

test("boundedRetry survives a throwing onAttemptFailed -- logging must never mask the real failure", async () => {
  await assert.rejects(
    boundedRetry(
      async () => {
        throw new Error("real failure");
      },
      1,
      () => {
        throw new Error("logging blew up");
      },
    ),
    /real failure/,
  );
});

// --- installSignalCleanup ---

function fakeProcess(pid = 4242) {
  const listeners = new Map();
  const killed = [];
  return {
    pid,
    on(signal, handler) {
      listeners.set(signal, handler);
    },
    off(signal, handler) {
      if (listeners.get(signal) === handler) listeners.delete(signal);
    },
    kill(targetPid, signal) {
      killed.push([targetPid, signal]);
    },
    // test helpers, not part of the real process API
    _listeners: listeners,
    _killed: killed,
    _trigger(signal) {
      const handler = listeners.get(signal);
      assert.ok(handler, `no handler registered for ${signal}`);
      handler();
    },
  };
}

test("installSignalCleanup registers a handler for every named signal", () => {
  const proc = fakeProcess();
  installSignalCleanup(["SIGINT", "SIGTERM"], () => {}, proc);
  assert.ok(proc._listeners.has("SIGINT"));
  assert.ok(proc._listeners.has("SIGTERM"));
});

test("installSignalCleanup's handler runs cleanup, then removes itself, then re-raises the signal", () => {
  const proc = fakeProcess(9999);
  const order = [];
  const cleanup = () => order.push("cleanup");
  installSignalCleanup(["SIGTERM"], cleanup, proc);

  proc._trigger("SIGTERM");

  assert.deepEqual(order, ["cleanup"]);
  // The listener removed itself -- re-triggering now must not find a handler.
  assert.equal(proc._listeners.has("SIGTERM"), false);
  // And the signal was re-sent to this exact pid, exactly once.
  assert.deepEqual(proc._killed, [[9999, "SIGTERM"]]);
});

test("installSignalCleanup handles each signal independently -- triggering one never fires the other", () => {
  const proc = fakeProcess();
  const fired = [];
  installSignalCleanup(["SIGINT", "SIGTERM"], () => fired.push("cleanup"), proc);

  proc._trigger("SIGINT");

  assert.deepEqual(fired, ["cleanup"]);
  assert.equal(proc._listeners.has("SIGINT"), false);
  assert.ok(proc._listeners.has("SIGTERM")); // untouched
  assert.deepEqual(proc._killed, [[proc.pid, "SIGINT"]]);
});

test("installSignalCleanup's uninstall() removes every handler without re-raising anything", () => {
  const proc = fakeProcess();
  const handle = installSignalCleanup(["SIGINT", "SIGTERM"], () => {}, proc);
  handle.uninstall();
  assert.equal(proc._listeners.size, 0);
  assert.deepEqual(proc._killed, []);
});

// --- createIntervalTimer ---

function fakeScheduler() {
  const calls = { set: [], clear: [] };
  let nextHandle = 1;
  return {
    setIntervalFn: (fn, ms) => {
      const handle = { id: nextHandle++, fn, ms };
      calls.set.push(handle);
      return handle;
    },
    clearIntervalFn: (handle) => {
      calls.clear.push(handle);
    },
    calls,
  };
}

test("createIntervalTimer does not start until start() is called", () => {
  const { setIntervalFn, clearIntervalFn, calls } = fakeScheduler();
  const timer = createIntervalTimer(() => {}, 1000, { setIntervalFn, clearIntervalFn });
  assert.equal(timer.running, false);
  assert.equal(calls.set.length, 0);
});

test("createIntervalTimer.start() schedules tick at the given interval exactly once", () => {
  const { setIntervalFn, clearIntervalFn, calls } = fakeScheduler();
  let ticks = 0;
  const timer = createIntervalTimer(() => (ticks += 1), 500, { setIntervalFn, clearIntervalFn });

  timer.start();
  assert.equal(timer.running, true);
  assert.equal(calls.set.length, 1);
  assert.equal(calls.set[0].ms, 500);

  // Starting again while already running must not schedule a second timer.
  timer.start();
  assert.equal(calls.set.length, 1);

  // Drive the scheduled callback manually (deterministic, no real waiting).
  calls.set[0].fn();
  calls.set[0].fn();
  assert.equal(ticks, 2);
});

test("createIntervalTimer.stop() clears the underlying timer and is idempotent", () => {
  const { setIntervalFn, clearIntervalFn, calls } = fakeScheduler();
  const timer = createIntervalTimer(() => {}, 500, { setIntervalFn, clearIntervalFn });
  timer.start();

  timer.stop();
  assert.equal(timer.running, false);
  assert.equal(calls.clear.length, 1);

  timer.stop(); // idempotent -- no second clear call
  assert.equal(calls.clear.length, 1);
});

test("createIntervalTimer can be restarted after being stopped", () => {
  const { setIntervalFn, clearIntervalFn, calls } = fakeScheduler();
  const timer = createIntervalTimer(() => {}, 500, { setIntervalFn, clearIntervalFn });
  timer.start();
  timer.stop();
  timer.start();
  assert.equal(timer.running, true);
  assert.equal(calls.set.length, 2);
});
