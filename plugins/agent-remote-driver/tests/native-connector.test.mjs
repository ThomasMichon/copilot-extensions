import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import vm from "node:vm";
import test from "node:test";
import { selectedNativeConnector } from "../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/native-connector.mjs";
import { selectedLoadout } from "../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/remote-driver.mjs";
import { resolveMetadataAsync } from "../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/metadata.mjs";
import { createHeadedAdapter } from "../../../libs/copilot-driver-core/src/agent_remote_driver/headed/acp-adapter.mjs";
import { headedBinding, headedDeliverySession } from "../../../libs/copilot-driver-core/src/agent_remote_driver/headed/connection.mjs";

const root = fileURLToPath(new URL("../../..", import.meta.url));
const bridgeRoot = resolve(root, "plugins/agent-bridge");
const driverRoot = resolve(root, "plugins/agent-remote-driver");
const connectorRoot = resolve(root, "libs/copilot-driver-core");
const sessionId = "11111111-1111-4111-8111-111111111111";
const selected = {
  contract: "driver-native-v1", version: 1, sessionId, cwd: root,
  scopeId: "fixture-scope", attempt: "22222222-2222-4222-8222-222222222222",
  bridgePluginRoot: bridgeRoot, driverPluginRoot: driverRoot, connectorPluginRoot: connectorRoot,
};
const selectionEnv = (selection = selected) => ({
  SESSION_ID: sessionId, AGENT_BRIDGE_NATIVE_SESSION_ID: sessionId,
  AGENT_BRIDGE_SCOPE_ID: selected.scopeId, AGENT_BRIDGE_PORTABLE_ATTEMPT: selected.attempt,
  AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT: bridgeRoot, AGENT_BRIDGE_REMOTE_DRIVER_ROOT: driverRoot,
  COPILOT_DRIVER_NATIVE_CONNECTOR: JSON.stringify(selection),
});

test("legacy activation is unchanged without explicit native connector selection", () => {
  assert.equal(selectedNativeConnector({ env: {}, role: "bridge" }), null);
});

for (const [role, pluginRoot] of [
  ["bridge", bridgeRoot], ["driver", driverRoot], ["connector", connectorRoot],
]) {
  test(`only the explicitly selected ${role} root accepts composition`, () => {
    assert.deepEqual(selectedNativeConnector({
      pluginRoot, role, env: selectionEnv(), cwd: root,
    }), selected);
    assert.throws(() => selectedNativeConnector({
      pluginRoot: resolve(root, "plugins"), role, env: selectionEnv(), cwd: root,
    }), /explicit launch selection/);
  });
}

for (const [label, change] of [
  ["unknown contract", { contract: "future" }], ["boolean version", { version: true }],
  ["another session", { sessionId: "other" }], ["another scope", { scopeId: "other" }],
  ["another attempt", { attempt: "other" }], ["another cwd", { cwd: bridgeRoot }],
  ["different Bridge support", { bridgePluginRoot: driverRoot }],
  ["different Driver support", { driverPluginRoot: bridgeRoot }],
  ["unrecognized field", { ignored: true }],
]) {
  test(`${label} refuses before a connector can join`, () => {
    assert.throws(() => selectedNativeConnector({
      pluginRoot: connectorRoot, role: "connector", env: selectionEnv({ ...selected, ...change }), cwd: root,
    }), /explicit launch selection/);
  });
}

test("portable metadata uses the actual executing Driver root, not a Bridge identity", async () => {
  const options = {
    cwd: root, env: selectionEnv(), extensionPluginRoot: connectorRoot, nativeConnector: selected,
  };
  assert.equal((await resolveMetadataAsync(options)).receiving_scope_id, selected.scopeId);
  await assert.rejects(resolveMetadataAsync({ ...options, nativeConnector: undefined }), /selected launch/);
  await assert.rejects(resolveMetadataAsync({
    ...options, extensionPluginRoot: bridgeRoot,
  }), /selected launch/);
});

test("composition observes all three actual roots and refuses missing or duplicate connector owners", async () => {
  const options = {
    cwd: root, expectedSessionId: sessionId, pluginRoot: driverRoot,
    bridgePluginRoot: bridgeRoot, extensionPluginRoot: connectorRoot, nativeConnector: selected,
  };
  const plugins = [
    { name: "agent-bridge", enabled: true, installedFrom: bridgeRoot },
    { name: "agent-remote-driver", enabled: true, installedFrom: driverRoot },
    { name: "copilot-driver-headed", enabled: true, installedFrom: connectorRoot },
  ];
  const native = { rpc: {
    metadata: { snapshot: async () => ({ sessionId, workingDirectory: root }) },
    plugins: { list: async () => ({ plugins }) },
  } };
  await selectedLoadout(native, options);
  const connector = plugins.pop();
  await assert.rejects(selectedLoadout(native, options), /missing, duplicated/);
  plugins.push(connector, connector);
  await assert.rejects(selectedLoadout(native, options), /missing, duplicated/);
});

function nativeFixture(send) {
  const handlers = new Set(), sends = [];
  const native = {
    sessionId, rpc: {}, getEvents: async () => [],
    on(handler) { handlers.add(handler); return () => handlers.delete(handler); },
    async send(options) {
      sends.push(options);
      if (send) return send(options);
      event("user.message", { source: options.source });
      event("session.idle");
      return "native-message";
    },
  };
  let eventId = 0;
  const event = (type, data = {}) => {
    for (const handler of handlers) handler({ id: String(++eventId), type, data });
  };
  return { native, event, sends };
}

test("retained delivery shares headed input ownership and preserves native takeover", async () => {
  const f = nativeFixture();
  const adapter = createHeadedAdapter(f.native, { emit() {}, cwd: root });
  const delivery = headedDeliverySession(f.native, adapter);
  try {
    assert.equal(await delivery.send({
      prompt: 'literal\n"quote" & $value', mode: "immediate", source: "agent-bridge",
    }), "native-message");
    assert.equal(f.sends.length, 1);
    assert.match(f.sends[0].source, /^agent-remote-driver-/);
    assert.equal(f.sends[0].prompt, 'literal\n"quote" & $value');
    assert.equal((await adapter.handle({
      jsonrpc: "2.0", id: 2, method: "copilot-driver/input-status", params: { sessionId },
    })).result.nativeSend, "ready");
    f.event("user.message", { source: "unowned-keyboard" });
    await assert.rejects(delivery.send({ prompt: "must not replay" }), /no replay/);
    assert.equal(f.sends.length, 1);
  } finally { adapter.close(); }
});

test("unknown composed send is held without another SDK send", async () => {
  const f = nativeFixture(() => { throw new Error("reply lost"); });
  const adapter = createHeadedAdapter(f.native, { emit() {}, cwd: root });
  try {
    const delivery = headedDeliverySession(f.native, adapter);
    await assert.rejects(delivery.send({ prompt: "one original input" }), /no replay/);
    await assert.rejects(delivery.send({ prompt: "one original input" }), /no replay/);
    assert.equal(f.sends.length, 1);
  } finally { adapter.close(); }
});

test("composed abort reaches an accepted native send only through its bound adapter", async () => {
  const f = nativeFixture((options) => {
    f.event("user.message", { source: options.source });
    return "active-native-message";
  });
  let aborts = 0;
  f.native.abort = async () => { aborts++; f.event("session.idle"); };
  const adapter = createHeadedAdapter(f.native, { emit() {}, cwd: root });
  const delivery = headedDeliverySession(f.native, adapter);
  try {
    assert.equal(await delivery.send({ prompt: "active work" }), "active-native-message");
    await delivery.abort();
    assert.equal(aborts, 1, "an accepted native-send has no ACP pending prompt but remains interruptible");
    f.native.sessionId = "another-session";
    await assert.rejects(delivery.abort(), /no replay or native fallback/);
    assert.equal(aborts, 1);
    assert.equal(f.sends.length, 1);
  } finally { adapter.close(); }
});

test("composed delivery waits for the one launch seed and shares auth-wait refusal", async () => {
  const f = nativeFixture();
  const adapter = createHeadedAdapter(f.native, { emit() {}, cwd: root, startupPrompt: "original seed" });
  try {
    const delivery = headedDeliverySession(f.native, adapter);
    const operation = delivery.send({ prompt: "one later input" });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(f.sends.length, 0);
    f.event("user.message", { content: "original seed" });
    f.event("session.idle");
    assert.equal(await operation, "native-message");
    adapter.authWait();
    await assert.rejects(delivery.send({ prompt: "not authorized after expiry" }), /no replay/);
    assert.equal(f.sends.length, 1);
  } finally { adapter.close(); }
});

async function entrypoint(relative, { env = selectionEnv(), failJoin = false, failActivation = false } = {}) {
  const entry = resolve(root, relative), f = nativeFixture();
  const calls = [];
  let adapter;
  const joinSession = async (options) => {
    calls.push(["join", options]);
    if (failJoin) throw new Error("original join failed");
    return f.native;
  };
  const dependencies = {
    "node:fs": { existsSync: () => false },
    "node:url": { fileURLToPath },
    "./core/native-connector.mjs": { selectedNativeConnector: (options) =>
      selectedNativeConnector({ ...options, env, cwd: root }) },
    "./core/remote-driver.mjs": { selectedLoadout },
    "./sdk-compat.mjs": { loadExtensionSdk: async () => { calls.push(["sdk"]); return { joinSession }; } },
    "./connection.mjs": {
      headedBinding, headedDeliverySession,
      connectHeadedSession: async (native) => {
        calls.push(["connect", native]);
        adapter = createHeadedAdapter(native, { emit() {}, cwd: root });
        return { adapter, close: () => { calls.push(["close"]); adapter.close(); } };
      },
    },
    "./core/extension.mjs": { activateBridgeExtension: async (options) => {
      calls.push(["activate", options]);
      const native = await options.joinSession({
        hooks: { onPostToolUse() {}, onPostToolUseFailure() {} }, commands: [{ name: "peer" }],
      });
      assert.equal(native, f.native);
      assert.equal(await options.createDeliverySession(native).send({ prompt: "one input" }), "native-message");
      if (failActivation) throw new Error("activation failed");
    } },
  };
  const context = vm.createContext({
    __dependencies: dependencies, URL, __url: pathToFileURL(
      relative.endsWith("connector-extension.mjs")
        ? resolve(connectorRoot, "extensions/copilot-driver-headed/extension.mjs") : entry,
    ).href,
    process: { env: { ...env, COPILOT_DRIVER_HEADED_BINDING: JSON.stringify({
      version: 1, sessionId, port: 12345, nonce: "fixture-".repeat(8),
    }) } },
    __import: async (specifier) => {
      if (specifier.endsWith("/native-connector.mjs")) return dependencies["./core/native-connector.mjs"];
      throw new Error(`unexpected dynamic import: ${specifier}`);
    },
  });
  const source = readFileSync(entry, "utf8").replace(
    /^import\s+\{([^}]+)\}\s+from\s+["']([^"']+)["'];/gm,
    (_statement, names, name) => `const {${names}} = __dependencies[${JSON.stringify(name)}];`,
  ).replaceAll("import.meta.url", "__url").replaceAll("await import(", "await __import(");
  try {
    await new vm.Script(`(async () => {${source}\n})()`).runInContext(context);
    return { calls, ...f };
  } catch (error) {
    error.calls = calls;
    throw error;
  } finally { adapter?.close(); }
}

for (const relative of [
  "plugins/agent-bridge/extensions/agent-bridge/extension.mjs",
  "plugins/agent-remote-driver/extensions/agent-remote-driver/extension.mjs",
]) {
  test(`${relative} is passive only for its explicit selected launch`, async () => {
    assert.deepEqual((await entrypoint(relative)).calls, []);
    await assert.rejects(entrypoint(relative, {
      env: selectionEnv({ ...selected, sessionId: "another-session" }),
    }), /explicit launch selection/);
  });
}

test("the actual Driver entrypoint owns one join and passes that native object to both consumers", async () => {
  const f = await entrypoint("libs/copilot-driver-core/src/agent_remote_driver/headed/connector-extension.mjs");
  assert.deepEqual(f.calls.map(([name]) => name), ["sdk", "activate", "join", "connect"]);
  const config = f.calls.find(([name]) => name === "join")[1];
  assert.deepEqual(Object.keys(config.hooks).sort(), ["onPostToolUse", "onPostToolUseFailure"]);
  assert.equal(config.commands[0].name, "peer");
  assert.equal(f.calls.find(([name]) => name === "connect")[1], f.native);
  assert.equal(f.sends.length, 1);
});

for (const failure of ["failJoin", "failActivation"]) {
  test(`${failure} never falls back to another join and closes only its adapter`, async () => {
    await assert.rejects(entrypoint(
      "libs/copilot-driver-core/src/agent_remote_driver/headed/connector-extension.mjs", { [failure]: true },
    ), (error) => {
      assert.equal(error.calls.filter(([name]) => name === "join").length, 1);
      assert.equal(error.calls.filter(([name]) => name === "close").length, failure === "failActivation" ? 1 : 0);
      return true;
    });
  });
}
