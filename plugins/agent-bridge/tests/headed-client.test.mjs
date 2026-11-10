import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createHeadedDriverSession } from "../extensions/agent-bridge/remote-driver.mjs";
import { createHeadedAdapter } from "../../../libs/copilot-driver-core/src/agent_remote_driver/headed/acp-adapter.mjs";

async function fixture(run, {
  lost = false, previous = false, supported = true, portable = false,
  startupPrompt = null, readiness = "current", onNativeSend = () => {},
} = {}) {
  const directory = await mkdtemp(join(tmpdir(), "headed-bridge-"));
  const sockets = new Set();
  const sends = [];
  const methods = [];
  let observer;
  let connections = 0;
  let sequence = 0n;
  let statusReadResolve;
  const statusRead = new Promise((resolve) => { statusReadResolve = resolve; });
  const native = {
    sessionId: "native-fixture",
    on(callback) { observer = callback; return () => {}; },
    async send(options) {
      sends.push(options);
      observer({ type: "user.message", data: { source: options.source, content: options.prompt } });
      onNativeSend();
      return `actual-native-message-${sends.length}`;
    },
  };
  const adapter = createHeadedAdapter(native, { emit() {}, cwd: directory, startupPrompt });
  const server = createServer((socket) => {
    connections++;
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
    socket.on("error", () => {});
    let buffer = Buffer.alloc(0);
    const frame = (type, payload) => {
      const body = Buffer.concat([Buffer.from(type), payload]);
      const header = Buffer.alloc(4);
      header.writeUInt32BE(body.length);
      socket.write(Buffer.concat([header, body]));
    };
    socket.on("data", (chunk) => {
      buffer = Buffer.concat([buffer, chunk]);
      while (buffer.length >= 4 && buffer.length >= 4 + buffer.readUInt32BE()) {
        const length = buffer.readUInt32BE();
        const type = buffer[4];
        const payload = buffer.subarray(5, 4 + length);
        buffer = buffer.subarray(4 + length);
        if (type === 65) {
          assert.equal(payload.subarray(8).toString(), "fixture-".repeat(8));
          const hello = Buffer.alloc(16);
          hello.writeBigUInt64BE(previous ? 5n : 0n);
          hello.writeBigUInt64BE(BigInt(process.pid), 8);
          frame("H", hello);
        } else if (type === 87) {
          const request = JSON.parse(payload.toString());
          methods.push(request.method);
          adapter.handle(request).then((response) => {
            if (request.method === "copilot-driver/native-send" && lost) {
              socket.destroy();
              return;
            }
            if (!supported && request.method === "initialize") delete response.result._meta.nativeSend;
            if (request.method === "initialize") {
              if (readiness === "legacy") delete response.result._meta.inputReadiness;
              if (readiness === "invalid") response.result._meta.inputReadiness.version = 2;
            }
            if (request.method === "copilot-driver/input-status") {
              if (readiness === "wrong-session") response.result.sessionId = "other-native";
              if (readiness === "inconsistent") {
                response.result.startupSeed = response.result.startupTurn = "pending";
              }
              statusReadResolve();
            }
            const seq = Buffer.alloc(8);
            seq.writeBigUInt64BE(++sequence);
            frame("F", Buffer.concat([seq, Buffer.from(JSON.stringify(response) + "\n")]));
          }).catch(() => socket.destroy());
        }
      }
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const declaration = {
    contract: "copilot-driver.headed-client", version: 1, sessionId: native.sessionId,
    client: fileURLToPath(new URL("../../../libs/copilot-driver-core/src/agent_remote_driver/headed/host-client.mjs", import.meta.url)),
    cwd: directory, stateFile: join(directory, "state.json"),
    nonce: "fixture-".repeat(8), hostPid: process.pid,
  };
  const state = {
    version: 2, mode: "headed", state: "running", lifetime_policy: "cli",
    session_id: native.sessionId, nonce: declaration.nonce,
    host_pid: process.pid, child_pid: process.pid, cwd: directory,
    protocol_version: 1, port: server.address().port, headed_client: ["native-send-v1"],
  };
  await writeFile(declaration.stateFile, JSON.stringify(state));
  const readonly = {
    sessionId: native.sessionId,
    rpc: { metadata: { snapshot: async () => ({ sessionId: native.sessionId, workingDirectory: directory }) } },
    getEvents: async () => ["original native history"],
    send() { assert.fail("Bridge must not call the SDK directly"); },
    abort() { assert.fail("Bridge must not abort the native session"); },
  };
  const pluginRoot = fileURLToPath(new URL("../../agent-remote-driver/", import.meta.url));
  const bridgePluginRoot = fileURLToPath(new URL("../", import.meta.url));
  const plugins = [
    { name: "agent-bridge", enabled: true, installedFrom: bridgePluginRoot },
    { name: "agent-remote-driver", enabled: true, installedFrom: pluginRoot },
  ];
  readonly.rpc.plugins = { list: async () => ({ plugins }) };
  const client = await createHeadedDriverSession(readonly, {
    declaration: JSON.stringify(declaration), cwd: directory, nativePid: process.pid,
    ...(portable ? { pluginRoot, bridgePluginRoot, expectedSessionId: native.sessionId } : {}),
  });
  try {
    await run({ client, native: readonly, sends, methods, state, declaration, plugins, statusRead,
      observer: (...args) => observer(...args),
      connections: () => connections });
  } finally {
    client.close();
    adapter.close();
    for (const socket of sockets) socket.destroy();
    await new Promise((resolve) => server.close(resolve));
    await rm(directory, { recursive: true });
  }
}

test("Bridge uses the retained common broker for queue and steer with actual native receipts", async () => {
  await fixture(async ({ client, sends, methods, connections }) => {
    assert.deepEqual(await client.getEvents(), ["original native history"]);
    for (const mode of [undefined, "immediate"]) {
      const id = await client.send({
        prompt: "literal\npeer message", displayPrompt: "attributed peer", source: "agent-bridge",
        ...(mode ? { mode } : {}),
      });
      assert.equal(id, `actual-native-message-${sends.length}`);
      assert.equal(sends.at(-1).mode, mode ?? "enqueue");
      assert.equal(sends.at(-1).prompt, "literal\npeer message");
      assert.equal(sends.at(-1).displayPrompt, "attributed peer");
    }
    assert.deepEqual(methods, [
      "initialize", "copilot-driver/input-status", "copilot-driver/native-send",
      "copilot-driver/input-status", "copilot-driver/native-send",
    ]);
    assert.equal(connections(), 1);
    await assert.rejects(client.abort(), /does not authorize abort/);
  });
});

test("Bridge waits for the original seed observation without waiting for its turn or sending early", async () => {
  await fixture(async ({ client, sends, statusRead, observer, connections }) => {
    const operation = client.send({ prompt: "controlled input", mode: "enqueue" });
    await statusRead;
    assert.equal(sends.length, 0);
    observer({ type: "session.idle" });
    observer({ type: "user.message", data: { content: "original seed" }, id: "startup" });
    assert.equal(await operation, "actual-native-message-1");
    assert.equal(sends.length, 1);
    assert.equal(connections(), 1);
    observer({ type: "user.message", data: { content: "original seed" }, id: "later-human" });
    await assert.rejects(client.send({ prompt: "revoked" }), /no replay/);
    assert.equal(sends.length, 1);
  }, { startupPrompt: "original seed" });
});

test("native identity changes during readiness permanently hold dispatch to the old conversation", async () => {
  await fixture(async ({ client, native, sends, statusRead, observer, connections }) => {
    const original = native.sessionId;
    const operation = client.send({ prompt: "must not reach the old conversation" });
    const refused = assert.rejects(operation, /no replay/);
    await statusRead;
    native.sessionId = "different-native";
    observer({ type: "user.message", data: { content: "original seed" }, id: "startup" });
    await refused;
    assert.equal(sends.length, 0);
    native.sessionId = original;
    await assert.rejects(client.send({ prompt: "must remain held" }), /no replay/);
    assert.equal(sends.length, 0);
    assert.equal(connections(), 1);
  }, { startupPrompt: "original seed" });
});

test("a startup observation deadline is a pre-dispatch refusal, not a permanent control reset", async (t) => {
  let now = Date.now();
  t.mock.method(Date, "now", () => now);
  await fixture(async ({ client, sends, statusRead, observer, connections }) => {
    const operation = client.send({ prompt: "not yet ready" });
    const refused = assert.rejects(operation, /startup.*not ready.*no prompt was dispatched/i);
    await statusRead;
    now += 5_001;
    await refused;
    assert.equal(sends.length, 0);
    observer({ type: "user.message", data: { content: "original seed" }, id: "startup" });
    assert.equal(await client.send({ prompt: "explicit later request" }), "actual-native-message-1");
    assert.equal(sends.length, 1);
    assert.equal(connections(), 1);
  }, { startupPrompt: "original seed" });
});

test("startup timeout cannot clear an identity change or make the client reusable", async (t) => {
  let now = Date.now();
  t.mock.method(Date, "now", () => now);
  await fixture(async ({ client, native, sends, statusRead, observer }) => {
    const original = native.sessionId;
    const refused = assert.rejects(client.send({ prompt: "must not dispatch" }), /no replay/);
    await statusRead;
    native.sessionId = "different-native";
    now += 5_001;
    await refused;
    native.sessionId = original;
    observer({ type: "user.message", data: { content: "original seed" }, id: "startup" });
    await assert.rejects(client.send({ prompt: "must remain held" }), /no replay/);
    assert.equal(sends.length, 0);
  }, { startupPrompt: "original seed" });
});

test("identity changes before a native receipt hold its unknown outcome without another send", async () => {
  let caller;
  await fixture(async ({ client, native, sends }) => {
    caller = native;
    const original = native.sessionId;
    await assert.rejects(client.send({ prompt: "one original send" }), /no replay/);
    assert.equal(sends.length, 1);
    native.sessionId = original;
    await assert.rejects(client.send({ prompt: "must not replay" }), /no replay/);
    assert.equal(sends.length, 1);
  }, { onNativeSend() { caller.sessionId = "different-native"; } });
});

test("legacy steady delivery is preserved, while unsupported readiness and wrong native identity refuse", async () => {
  await fixture(async ({ client, sends, methods }) => {
    assert.equal(await client.send({ prompt: "legacy steady input" }), "actual-native-message-1");
    assert.equal(sends.length, 1);
    assert.deepEqual(methods, ["initialize", "copilot-driver/native-send"]);
  }, { readiness: "legacy" });
  for (const readiness of ["invalid", "wrong-session", "inconsistent"]) {
    await fixture(async ({ client, sends }) => {
      await assert.rejects(client.send({ prompt: "unqualified" }), /no replay/);
      assert.equal(sends.length, 0);
    }, { readiness });
  }
});

test("lost acceptance remains unknown and never reconnects or calls the SDK again", async () => {
  await fixture(async ({ client, sends, connections }) => {
    await assert.rejects(client.send({ prompt: "one" }), /no replay/);
    await assert.rejects(client.send({ prompt: "never retry" }), /no replay/);
    assert.equal(sends.length, 1);
    assert.equal(connections(), 1);
    assert.deepEqual(await client.getEvents(), ["original native history"]);
  }, { lost: true });
});

test("native user input revokes Bridge delivery without abort, reacquisition or fallback", async () => {
  await fixture(async ({ client, sends, observer, connections }) => {
    await client.send({ prompt: "one" });
    observer({ type: "user.message", data: { source: "user", content: "operator input" } });
    await assert.rejects(client.send({ prompt: "held" }), /no replay/);
    await assert.rejects(client.send({ prompt: "still held" }), /no replay/);
    assert.equal(sends.length, 1);
    assert.equal(connections(), 1);
  });
});

test("old peers and a prior controller's buffered replies refuse before native effects", async () => {
  for (const options of [{ supported: false }, { previous: true }]) {
    await fixture(async ({ client, sends, connections }) => {
      await assert.rejects(client.send({ prompt: "unqualified" }), /no replay/);
      await assert.rejects(client.send({ prompt: "no reacquisition" }), /no replay/);
      assert.equal(sends.length, 0);
      assert.equal(connections(), 1);
    }, options);
  }
});

test("the common headed client preserves portable native loadout checks without using the old driver", async () => {
  await fixture(async ({ client, sends, connections, plugins }) => {
    await client.send({ prompt: "one" });
    plugins.push({ ...plugins[1] });
    await assert.rejects(client.send({ prompt: "duplicate root" }), /duplicated/);
    plugins.pop();
    await assert.rejects(client.send({ prompt: "no reacquisition" }), /duplicated/);
    assert.equal(sends.length, 1);
    assert.equal(connections(), 1);
  }, { portable: true });
});

test("changed launch state remains held even if a later file matches again", async () => {
  await fixture(async ({ client, state, declaration, sends, connections }) => {
    await writeFile(declaration.stateFile, JSON.stringify({ ...state, child_pid: process.pid + 1 }));
    await assert.rejects(client.send({ prompt: "wrong native" }), /no replay/);
    await writeFile(declaration.stateFile, JSON.stringify(state));
    await assert.rejects(client.send({ prompt: "never retry" }), /no replay/);
    assert.equal(sends.length, 0);
    assert.equal(connections(), 0);
  });
});
