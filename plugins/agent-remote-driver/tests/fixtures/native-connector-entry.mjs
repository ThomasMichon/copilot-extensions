import assert from "node:assert/strict";
import { createServer } from "node:net";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const [connectorRoot, bridgeRoot, driverRoot, config, mode] = process.argv.slice(2);
const sessionId = "11111111-1111-4111-8111-111111111111";
const nonce = "fixture-".repeat(8), handlers = new Set(), sends = [];
let joins = 0, peer, handshake, reply;
let bound, answered;
const binding = new Promise((resolve) => { bound = resolve; });
const response = new Promise((resolve) => { answered = resolve; });
const server = createServer((socket) => {
  peer = socket;
  let buffer = "";
  socket.on("data", (chunk) => {
    buffer += chunk;
    for (let end; (end = buffer.indexOf("\n")) >= 0;) {
      const message = JSON.parse(buffer.slice(0, end));
      buffer = buffer.slice(end + 1);
      if (message.contract === "copilot-driver.headed-sdk") {
        handshake = message;
        bound();
      } else if (message.id === 1) {
        reply = message;
        answered();
      }
    }
  });
});
const timer = setTimeout(() => { throw new Error("Bounded synthetic connector timed out"); }, 10_000);
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const selected = {
    contract: "driver-native-v1", version: 1, sessionId, cwd: process.cwd(),
    scopeId: "fixture-scope", attempt: "22222222-2222-4222-8222-222222222222",
    bridgePluginRoot: bridgeRoot, driverPluginRoot: driverRoot, connectorPluginRoot: connectorRoot,
  };
  Object.assign(process.env, {
    SESSION_ID: sessionId, AGENT_BRIDGE_NATIVE_SESSION_ID: sessionId,
    AGENT_BRIDGE_SCOPE_ID: selected.scopeId, AGENT_BRIDGE_PORTABLE_ATTEMPT: selected.attempt,
    AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT: bridgeRoot, AGENT_BRIDGE_REMOTE_DRIVER_ROOT: driverRoot,
    AGENT_BRIDGE_CONFIG_DIR: config, COPILOT_HOME: config,
    HOME: config, USERPROFILE: config,
    COPILOT_DRIVER_NATIVE_CONNECTOR: JSON.stringify(selected),
    COPILOT_DRIVER_HEADED_CLIENT: "explicit synthetic retained delivery",
    COPILOT_DRIVER_HEADED_BINDING: JSON.stringify({ version: 1, sessionId, nonce, port: server.address().port }),
  });
  globalThis.fetch = () => assert.fail("No network or provisioning without a Bridge credential");
  globalThis.__joinSession = async (options) => {
    joins++;
    assert.deepEqual(Object.keys(options).sort(), ["hooks", "interaction"]);
    assert.deepEqual(Object.keys(options.hooks.handlers).sort(), ["onPostToolUse", "onPostToolUseFailure"]);
    assert.deepEqual(options.interaction.commands.map((command) => command.name), ["peer"]);
    return {
      sessionId, log: async () => {}, getEvents: async () => [],
      rpc: {
        metadata: { snapshot: async () => ({ sessionId, workingDirectory: process.cwd() }) },
        plugins: { list: () => assert.fail("Installer records cannot establish executing origins") },
        extensions: mode === "missing-observer" ? {} : { list: async () => ({ extensions: [
          { id: "plugin:copilot-driver-headed:copilot-driver-headed", name: "copilot-driver-headed",
            source: "plugin", status: "running",
            pid: mode === "missing-origin" ? undefined : mode === "wrong-loadout" ? process.pid + 1 : process.pid },
          ...(mode === "competing-driver" ? [{
            id: "plugin:agent-remote-driver:agent-remote-driver", name: "agent-remote-driver",
            source: "plugin", status: "running", pid: process.pid + 2,
          }] : []),
        ] }) },
      },
      on(handler) { handlers.add(handler); return () => handlers.delete(handler); },
      async send(options) {
        sends.push(options);
        assert.match(options.source, /^agent-remote-driver-/);
        for (const [index, [type, data]] of [
          ["user.message", { source: options.source, content: options.prompt }],
          ["session.idle", {}],
        ].entries()) {
          for (const handler of handlers) handler({ id: String(index), type, data });
        }
        return "native-message";
      },
    };
  };
  for (const [root, name] of [[bridgeRoot, "agent-bridge"], [driverRoot, "agent-remote-driver"]]) {
    await import(pathToFileURL(resolve(root, "extensions", name, "extension.mjs")).href);
  }
  assert.equal(joins, 0, "selected support facades must not join");
  await import(pathToFileURL(resolve(connectorRoot, "extensions/copilot-driver-headed/extension.mjs")).href);
  await binding;
  assert.equal(joins, 1);
  assert.equal(handlers.size, 2, "one headed observer and one coordination observer");
  assert.equal(handshake.sessionId, sessionId);
  assert.equal(handshake.nonce, nonce);
  peer.write(JSON.stringify({
    jsonrpc: "2.0", id: 1, method: "copilot-driver/native-send",
    params: { sessionId, prompt: 'literal\n"quote" & $value', mode: "immediate" },
  }) + "\n");
  await response;
  const refusal = {
    "wrong-loadout": "extension-process-mismatch:connector",
    "missing-origin": "extension-process-mismatch:connector",
    "missing-observer": "extensions-list-unsupported",
    "competing-driver": "extension-competing:driver",
  }[mode];
  if (refusal) {
    assert.equal(reply.error.code, -32001);
    assert.equal(reply.error.message,
      `Selected native execution context is unavailable; no input was dispatched [${refusal}]`);
    assert.equal(sends.length, 0);
    assert.equal(joins, 1);
    console.log(`observed ${mode.replaceAll("-", " ")} refused before native input; bounded reason; no fallback join`);
  } else {
    assert.equal(reply.result.messageId, "native-message");
    assert.equal(sends.length, 1);
    assert.equal(sends[0].prompt, 'literal\n"quote" & $value');
    console.log("one staged Driver join; passive support facades; original native adapter; no suite service");
  }
} finally {
  clearTimeout(timer);
  peer?.destroy();
  await new Promise((resolve) => server.close(resolve));
}
