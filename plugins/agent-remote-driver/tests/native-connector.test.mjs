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

function observedSelection() {
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
  return { options, plugins, native };
}

function executingSelection() {
  const f = observedSelection();
  f.options.extensionPid = process.pid;
  f.extensions = [{
    id: "plugin:copilot-driver-headed:copilot-driver-headed",
    name: "copilot-driver-headed", source: "plugin", status: "running", pid: process.pid,
  }];
  f.native.rpc.extensions = { list: async () => ({ extensions: f.extensions }) };
  f.native.rpc.plugins.list = () => assert.fail("Configuration metadata is not executing-module evidence");
  return f;
}

test("the launch-bound executing connector needs no installer origin or configuration deduplication", async () => {
  const f = executingSelection();
  f.plugins.forEach((plugin) => { delete plugin.installedFrom; });
  f.plugins.push({ name: "agent-remote-driver", enabled: true, source: "live" });
  await selectedLoadout(f.native, f.options);
});

test("executing identity is independent of valid discovery-name spelling", async () => {
  for (const name of ["runtime-discovery-label", "plugin:copilot-driver-headed:copilot-driver-headed"]) {
    const f = executingSelection();
    f.extensions[0].name = name;
    await selectedLoadout(f.native, f.options);
  }
});

test("discovery-name metadata cannot compensate for another identity, source, state or process", async () => {
  for (const [change, reason] of [
    [{ id: "plugin:copilot-driver-headed:another" }, "extension-identity-mismatch"],
    [{ source: "user" }, "extension-identity-mismatch"],
    [{ status: "starting" }, "extension-not-running"],
    [{ pid: process.pid + 1 }, "extension-process-mismatch"],
  ]) {
    const f = executingSelection();
    Object.assign(f.extensions[0], { name: "runtime-discovery-label", ...change });
    await assert.rejects(selectedLoadout(f.native, f.options), (error) =>
      error.selectedContextReason === reason);
  }
});

test("live-owner validation retains the module root check independently of the observed PID", async () => {
  const f = executingSelection();
  f.options.extensionPluginRoot = bridgeRoot;
  await assert.rejects(selectedLoadout(f.native, f.options), (error) =>
    error.selectedContextReason === "selection-root-mismatch");
});

test("identity refusal reports bounded comparisons without dispatch or control reacquisition", async () => {
  const observed = executingSelection(), f = nativeFixture();
  Object.assign(observed.extensions[0], {
    name: observed.extensions[0].id, source: "session", status: "starting", pid: process.pid + 1,
  });
  let checks = 0, aborts = 0;
  f.native.abort = async () => { aborts++; };
  const adapter = createHeadedAdapter(f.native, {
    emit() {}, cwd: root, validateSendContext: async () => {
      checks++;
      await selectedLoadout(observed.native, observed.options);
    },
  });
  try {
    const request = {
      jsonrpc: "2.0", id: 1, method: "session/prompt",
      params: { sessionId, prompt: [{ type: "text", text: "not dispatched" }] },
    };
    const response = await adapter.handle(request);
    assert.equal(response.error.code, -32001);
    assert.equal(response.error.message,
      "Selected native execution context is unavailable; no input was dispatched " +
      "[extension-identity-mismatch:connector] " +
      "[owner-checks id=match name=qualified source=session status=starting pid=other]");
    assert.match((await adapter.handle({ ...request, id: 2 })).error.message, /control is unavailable/);
    await assert.rejects(adapter.interrupt(), /control is unavailable/);
    assert.equal(checks, 1);
    assert.equal(aborts, 0);
    assert.equal(f.sends.length, 0);
  } finally { adapter.close(); }
});

test("identity evidence classifies unavailable and arbitrary registry fields without forwarding them", async () => {
  const f = executingSelection();
  Object.assign(f.extensions[0], {
    id: "plugin:copilot-driver-headed:private-identity",
    name: "private-name", source: "private-source", status: "private-status", pid: "private-pid",
  });
  await assert.rejects(selectedLoadout(f.native, f.options), (error) => {
    assert.equal(error.selectedContextReason, "extension-identity-mismatch");
    assert.deepEqual(error.selectedContextIdentity, {
      id: "other", name: "other", source: "unknown", status: "unknown", pid: "invalid",
    });
    assert.doesNotMatch(JSON.stringify(error.selectedContextIdentity), /private-/);
    return true;
  });
  delete f.extensions[0].name;
  delete f.extensions[0].pid;
  await assert.rejects(selectedLoadout(f.native, f.options), (error) => {
    assert.equal(error.selectedContextIdentity.name, "missing");
    assert.equal(error.selectedContextIdentity.pid, "missing");
    return true;
  });
});

test("a competing live sender leaves prompt and cancellation held without replay", async () => {
  const f = nativeFixture(), observed = executingSelection();
  observed.extensions.push({
    id: "plugin:agent-remote-driver:agent-remote-driver", name: "agent-remote-driver",
    source: "plugin", status: "running", pid: process.pid + 1,
  });
  let checks = 0, aborts = 0;
  f.native.abort = async () => { aborts++; };
  const adapter = createHeadedAdapter(f.native, {
    emit() {}, cwd: root, validateSendContext: async () => {
      checks++;
      await selectedLoadout(observed.native, observed.options);
    },
  });
  try {
    const response = await adapter.handle({
      jsonrpc: "2.0", id: 1, method: "session/prompt",
      params: { sessionId, prompt: [{ type: "text", text: "not dispatched" }] },
    });
    assert.equal(response.error.code, -32001);
    assert.match(response.error.message, /\[extension-competing:driver\]/);
    await assert.rejects(adapter.interrupt(), /control is unavailable/);
    assert.equal(checks, 1);
    assert.equal(aborts, 0);
    assert.equal(f.sends.length, 0);
  } finally { adapter.close(); }
});

for (const [label, change, reason, plugin] of [
  ["missing live observer", (f) => { delete f.native.rpc.extensions; }, "extensions-list-unsupported"],
  ["failed live observer", (f) => {
    f.native.rpc.extensions.list = async () => { throw new Error("private observer details"); };
  }, "extensions-list-failed"],
  ["invalid live observation", (f) => { f.native.rpc.extensions.list = async () => []; },
    "extension-observation-invalid"],
  ["missing executing connector", (f) => { f.extensions.length = 0; }, "plugin-missing", "connector"],
  ["duplicate executing connector", (f) => { f.extensions.push({ ...f.extensions[0] }); },
    "plugin-duplicated", "connector"],
  ["another connector process", (f) => { f.extensions[0].pid++; }, "extension-process-mismatch", "connector"],
  ["a non-plugin connector", (f) => { f.extensions[0].source = "user"; },
    "extension-identity-mismatch", "connector"],
  ["missing discovery-name metadata", (f) => { delete f.extensions[0].name; },
    "extension-identity-mismatch", "connector"],
  ["empty discovery-name metadata", (f) => { f.extensions[0].name = ""; },
    "extension-identity-mismatch", "connector"],
  ["invalid discovery-name metadata", (f) => { f.extensions[0].name = 1; },
    "extension-identity-mismatch", "connector"],
  ["another connector identity", (f) => { f.extensions[0].id = "plugin:another:copilot-driver-headed"; },
    "extension-identity-mismatch", "connector"],
  ["a disabled connector", (f) => { f.extensions[0].status = "disabled"; },
    "extension-not-running", "connector"],
  ["a competing global Driver", (f) => { f.extensions.push({
    id: "plugin:agent-remote-driver:agent-remote-driver", name: "agent-remote-driver",
    source: "plugin", status: "running", pid: process.pid + 1,
  }); }, "extension-competing", "driver"],
  ["a starting Bridge sender", (f) => { f.extensions.push({
    id: "plugin:agent-bridge:agent-bridge", name: "agent-bridge", source: "plugin", status: "starting",
  }); }, "extension-competing", "bridge"],
]) {
  test(`executing context refuses ${label} before using a configured origin`, async () => {
    const f = executingSelection();
    change(f);
    await assert.rejects(selectedLoadout(f.native, f.options), (error) => {
      assert.equal(error.selectedContextReason, reason);
      assert.equal(error.selectedContextPlugin, plugin);
      assert.doesNotMatch(error.message, /private observer details/);
      return true;
    });
  });
}

for (const [label, change, reason, plugin] of [
  ["missing plugin observer", (f) => { delete f.native.rpc.plugins.list; }, "plugins-list-unsupported"],
  ["missing metadata observer", (f) => { delete f.native.rpc.metadata; }, "metadata-snapshot-unsupported"],
  ["failed metadata read", (f) => {
    f.native.rpc.metadata.snapshot = async () => { throw new Error("private observer details"); };
  }, "metadata-before-failed"],
  ["failed plugin read", (f) => {
    f.native.rpc.plugins.list = async () => { throw new Error("private observer details"); };
  }, "plugins-list-failed"],
  ["failed second metadata read", (f) => {
    let reads = 0;
    f.native.rpc.metadata.snapshot = async () => {
      if (++reads === 2) throw new Error("private observer details");
      return { sessionId, workingDirectory: root };
    };
  }, "metadata-after-failed"],
  ["changed native session", (f) => {
    f.native.rpc.metadata.snapshot = async () => ({ sessionId: "other", workingDirectory: root });
  }, "native-session-mismatch"],
  ["changed native cwd", (f) => {
    f.native.rpc.metadata.snapshot = async () => ({ sessionId, workingDirectory: bridgeRoot });
  }, "native-cwd-mismatch"],
  ["invalid plugin observation", (f) => { f.native.rpc.plugins.list = async () => []; },
    "plugin-observation-invalid"],
  ["missing connector", (f) => { f.plugins.pop(); }, "plugin-missing", "connector"],
  ["duplicate Driver", (f) => { f.plugins.push({ ...f.plugins[1] }); }, "plugin-duplicated", "driver"],
  ["uninstalled Bridge", (f) => { f.plugins[0].installed = false; }, "plugin-not-installed", "bridge"],
  ["missing plugin origin", (f) => { delete f.plugins[2].installedFrom; },
    "plugin-origin-missing", "connector"],
  ["relative plugin origin", (f) => { f.plugins[2].installedFrom = "relative"; },
    "plugin-origin-relative", "connector"],
  ["unreadable plugin origin", (f) => {
    f.plugins[2].installedFrom = resolve(root, "nonexistent-context-diagnostic-fixture");
  }, "plugin-origin-unavailable", "connector"],
  ["changed plugin origin", (f) => { f.plugins[2].installedFrom = bridgeRoot; },
    "plugin-origin-mismatch", "connector"],
]) {
  test(`selected context names ${label} without exposing observer details`, async () => {
    const f = observedSelection();
    change(f);
    await assert.rejects(selectedLoadout(f.native, f.options), (error) => {
      assert.equal(error.selectedContextReason, reason);
      assert.equal(error.selectedContextPlugin, plugin);
      assert.doesNotMatch(error.message, /private observer details/);
      return true;
    });
  });
}

for (const method of ["session/prompt", "copilot-driver/native-send", "interrupt"]) {
  test(`selected context diagnostics survive ${method} without dispatch or replay`, async () => {
    const f = nativeFixture();
    const observed = observedSelection();
    delete observed.plugins[2].installedFrom;
    let checks = 0, aborts = 0;
    f.native.abort = async () => { aborts++; };
    const adapter = createHeadedAdapter(f.native, {
      emit() {}, cwd: root,
      validateSendContext: async () => {
        checks++;
        await selectedLoadout(observed.native, observed.options);
      },
    });
    try {
      if (method === "interrupt") {
        await assert.rejects(adapter.interrupt(), /no interruption was dispatched \[plugin-origin-missing:connector\]/);
      } else {
        const response = await adapter.handle({
          jsonrpc: "2.0", id: 1, method,
          params: {
            sessionId, ...(method === "session/prompt"
              ? { prompt: [{ type: "text", text: "not dispatched" }] }
              : { prompt: "not dispatched", mode: "enqueue" }),
          },
        });
        assert.equal(response.error.code, -32001);
        assert.match(response.error.message, /no input was dispatched \[plugin-origin-missing:connector\]/);
        assert.ok(!response.error.message.includes(root));
      }
      assert.equal(f.sends.length, 0);
      assert.equal(aborts, 0);
      assert.equal(checks, 1);
      await assert.rejects(adapter.interrupt(), /control is unavailable/);
      assert.equal(checks, 1, "an observation failure must keep control held");
    } finally { adapter.close(); }
  });
}

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
