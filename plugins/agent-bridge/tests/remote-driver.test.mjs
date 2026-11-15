import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createDriverServer } from "../../agent-remote-driver/extensions/agent-remote-driver/driver-server.mjs";
import { createRemoteDriverSession } from "../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/remote-driver.mjs";

async function fixture(run) {
  const directory = await mkdtemp(join(tmpdir(), "bridge-driver-"));
  const calls = [];
  let nativeId = "selected-native-session";
  const server = createDriverServer({
    token: "synthetic-observation", controlToken: "synthetic-administration",
    getSessionId: () => nativeId, getPid: () => process.pid,
    send: async (options) => {
      calls.push(options);
      server.control.observe({
        type: "user.message", id: `native-${calls.length}`, data: { source: options.source },
      });
      return { messageId: `native-${calls.length}` };
    },
    abort: async () => { calls.push("abort"); },
    subscribe: () => () => {},
  });
  server.control.markReady();
  const port = await server.listen();
  const descriptor = {
    sessionId: nativeId, instanceId: server.control.snapshot().instanceId,
    host: "127.0.0.1", port, token: "synthetic-observation",
    controlProtocol: 2, controlToken: "synthetic-administration", cwd: directory,
  };
  const path = join(directory, `${nativeId}.json`);
  const write = () => writeFile(path, JSON.stringify(descriptor), { mode: 0o600 });
  await write();
  const native = {
    get sessionId() { return nativeId; },
    getEvents: async () => ["readonly-history"],
    rpc: { metadata: { snapshot: async () => ({ sessionId: nativeId, workingDirectory: directory }) } },
    send: () => { throw new Error("raw native send must not be used"); },
    abort: () => { throw new Error("raw native abort must not be used"); },
  };
  const options = {
    extensionPluginRoot: fileURLToPath(new URL("../", import.meta.url)),
    pluginRoot: fileURLToPath(new URL("../../agent-remote-driver/", import.meta.url)),
    descriptorDirectory: directory, expectedSessionId: nativeId, cwd: directory, ownerId: "bridge-launch",
  };
  try {
    await run({ create: () => createRemoteDriverSession(native, options), server, calls, descriptor, write, native, options,
      rename: () => { nativeId = "another-native-session"; } });
  } finally {
    server.server.closeAllConnections();
    await server.close();
    await rm(directory, { recursive: true });
  }
}

test("Bridge's selected adapter sends and aborts only through the standalone driver", async () => {
  await fixture(async ({ create, calls, server }) => {
    const adapter = create();
    assert.deepEqual(await adapter.getEvents(), ["readonly-history"]);
    assert.equal((await adapter.rpc.metadata.snapshot()).sessionId, adapter.sessionId);
    assert.deepEqual(await adapter.send({ prompt: "one", mode: "immediate" }), { messageId: "native-1" });
    await adapter.abort();
    assert.equal(calls.length, 2);
    assert.match(calls[0].source, /^agent-remote-driver-/);
    server.control.observe({ type: "user.message", id: "human", data: { source: "user" } });
    await assert.rejects(adapter.send({ prompt: "stale" }), (error) => error.code === "generation_stale");
    await assert.rejects(adapter.send({ prompt: "no re-hook" }), (error) => error.code === "startup_control_held");
    assert.equal(calls.length, 2);
  });
});

test("a failed descriptor selection is retained and cannot acquire a later replacement", async () => {
  await fixture(async ({ create, calls, descriptor, write }) => {
    descriptor.cwd += "-other";
    await write();
    const adapter = create();
    await assert.rejects(adapter.send({ prompt: "wrong-checkout" }), /differs from the selected launch/);
    descriptor.cwd = descriptor.cwd.slice(0, -6);
    await write();
    await assert.rejects(adapter.send({ prompt: "never retry" }), /differs from the selected launch/);
    assert.equal(calls.length, 0);
  });
});

test("native identity changes do not silently select a second descriptor or conversation", async () => {
  await fixture(async ({ create, calls, rename }) => {
    const adapter = create();
    await adapter.send({ prompt: "one" });
    rename();
    await assert.rejects(adapter.abort(), /native session changed/);
    assert.equal(calls.length, 1);
  });
});

test("portable receiving observes unique selected native roots before granting control", async () => {
    await fixture(async ({ create, calls, native, options }) => {
      options.bridgePluginRoot = fileURLToPath(new URL("../", import.meta.url));
      const plugins = [
        { name: "agent-bridge", enabled: true, installedFrom: options.bridgePluginRoot },
        { name: "agent-remote-driver", enabled: true, installedFrom: options.pluginRoot },
      ];
      native.rpc.plugins = { list: async () => ({ plugins }) };
      const adapter = create();
      await adapter.send({ prompt: "one" });
      plugins.push({ ...plugins[1] });
      await assert.rejects(adapter.send({ prompt: "duplicate" }), /duplicated/);
      plugins.pop();
      await assert.rejects(adapter.send({ prompt: "no reacquisition" }), /duplicated/);
      assert.equal(calls.length, 1);
    });
  });

test("missing native loadout observation never falls back to direct SDK control", async () => {
    await fixture(async ({ create, calls, native, options, server }) => {
      options.bridgePluginRoot = fileURLToPath(new URL("../", import.meta.url));
      const before = server.control.snapshot();
      const adapter = create();
      await assert.rejects(adapter.send({ prompt: "unsupported" }), /native observation/);
      native.rpc.plugins = { list: async () => ({ plugins: [] }) };
      await assert.rejects(adapter.send({ prompt: "still held" }), /native observation/);
      assert.equal(calls.length, 0);
      assert.deepEqual(server.control.snapshot(), before);
  });
});
