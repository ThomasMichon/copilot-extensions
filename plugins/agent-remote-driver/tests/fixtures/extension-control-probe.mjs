import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createControlClient } from "../../extensions/agent-remote-driver/control-client.mjs";
import { createDriverServer } from "../../extensions/agent-remote-driver/driver-server.mjs";

const directory = mkdtempSync(join(tmpdir(), "driver-extension-probe-"));
process.env.AGENT_REMOTE_DRIVER_CONFIG_DIR = directory;
process.env.SESSION_ID = "synthetic-extension-session";
let observe;
const sends = [];
const intervals = [];
let target;
globalThis.probeCreateServer = (options) => {
  target = createDriverServer(options);
  return target;
};
const realInterval = globalThis.setInterval;
globalThis.setInterval = (...args) => {
  const timer = realInterval(...args);
  intervals.push(timer);
  return timer;
};
globalThis.probeSession = {
  on(callback) {
    if (process.env.PROBE_OBSERVER_UNAVAILABLE === "1") throw new Error("synthetic observer unavailable");
    observe = callback;
  },
  async send(options) {
    assert.equal(typeof observe, "function");
    assert.equal(typeof options.prompt, "string");
    assert.match(options.source, /^agent-remote-driver-/);
    sends.push(options);
    observe({ id: `native-${sends.length}`, type: "user.message", data: { source: options.source } });
    return { messageId: `message-${sends.length}` };
  },
  async abort() { return {}; },
};
try {
  await import("../../extensions/agent-remote-driver/extension.mjs");
  const descriptors = readdirSync(join(directory, "sessions")).filter((name) => name.endsWith(".json"));
  assert.equal(descriptors.length, 1);
  const descriptor = JSON.parse(readFileSync(join(directory, "sessions", descriptors[0]), "utf8"));
  assert.equal(descriptor.controlProtocol, 2);
  assert.notEqual(descriptor.token, descriptor.controlToken);
  const client = createControlClient(descriptor);
  const state = await client.health();
  if (process.env.PROBE_OBSERVER_UNAVAILABLE === "1") {
    assert.equal(state.ready, false);
    await assert.rejects(client.grant("probe-driver", { rehook: true }),
      (error) => error.code === "observation_unavailable");
    assert.equal(sends.length, 0);
    console.log("PASS: unavailable production observer disables mutation but retains observation");
  } else {
    assert.equal(typeof observe, "function");
    assert.equal(state.ready, true);
    assert.equal(state.mode, "human");
    const lease = await client.grant("probe-driver", { rehook: true });
    await client.invoke("send", lease, 1, "synthetic prompt");
    assert.equal((await client.health()).mode, "remote");
    observe({ id: "human", type: "user.message", data: { source: "user" } });
    assert.equal((await client.health()).mode, "human");
    await assert.rejects(client.invoke("send", lease, 2, "stale"), (error) => error.code === "generation_stale");
    const fresh = await client.grant("probe-driver", { rehook: true });
    await client.invoke("steer", fresh, 1, "fresh");
    assert.equal(sends.length, 2);
    console.log("PASS: production extension wiring, native-fake provenance, takeover and fresh re-hook");
  }
} finally {
  for (const timer of intervals) clearInterval(timer);
  globalThis.setInterval = realInterval;
  if (target) {
    target.server.closeAllConnections();
    await target.close();
  }
  rmSync(directory, { recursive: true });
}
