// Real extracted extensions with only the SDK and Bridge carrier substituted.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { createConnection } from "node:net";
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";
import vm from "node:vm";

const config = JSON.parse(process.argv[2]);
const transport = createConnection({ host: "127.0.0.1", port: config.carrierPort });
await new Promise((resolve, reject) => {
  transport.once("connect", resolve);
  transport.once("error", reject);
});
const sid = process.env.AGENT_BRIDGE_NATIVE_SESSION_ID;
const driverRoot = process.env.AGENT_BRIDGE_REMOTE_DRIVER_ROOT;
const bridgeRoot = process.env.AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT;
const lines = createInterface({ input: transport });
const replies = new Map(), handlers = [], events = [], timers = [];
const emit = (value) => transport.write(JSON.stringify(value) + "\n");
let sequence = 0, driver, closing = false;
lines.on("line", async (line) => {
  const value = JSON.parse(line);
  if (value.stop) {
    closing = true;
    for (const timer of timers) clearInterval(timer);
    const deadline = Date.now() + 3000;
    while (replies.size && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    assert.equal(replies.size, 0, "carrier work did not settle");
    driver.cleanupDescriptor();
    driver.driverServer.server.closeAllConnections();
    await driver.driverServer.close();
    const closed = new Promise((resolve) => transport.once("close", resolve));
    transport.end(JSON.stringify({ stopped: true }) + "\n");
    await closed;
    process.exit(0);
  }
  const resolve = replies.get(value.id);
  assert.ok(resolve, "unexpected carrier reply");
  replies.delete(value.id);
  resolve(value);
});
const carrier = async (url, options = {}) => {
  if (closing) throw new Error("fixture consumer is closing");
  const id = ++sequence;
  const reply = new Promise((resolve) => replies.set(id, resolve));
  const parsed = new URL(url);
  emit({
    id, method: options.method || "GET", path: parsed.pathname + parsed.search,
    body: options.body ? JSON.parse(options.body) : undefined,
    headers: options.headers,
  });
  const response = await reply;
  return {
    ok: response.status >= 200 && response.status < 300,
    status: response.status, json: async () => response.body,
  };
};
const plugins = [
  { name: "agent-bridge", marketplace: "fixture", enabled: true, installedFrom: bridgeRoot },
  { name: "agent-remote-driver", marketplace: "fixture", enabled: true, installedFrom: driverRoot },
];
if (config.duplicateDriver) plugins.push({ ...plugins[1] });
const session = {
  sessionId: sid,
  on: (handler) => {
    handlers.push(handler);
    handler({ type: "session.start", sessionId: sid, id: "startup", data: {} });
  },
  log: async () => {},
  getEvents: async () => events,
  rpc: {
    metadata: { snapshot: async () => ({ sessionId: sid, workingDirectory: process.cwd() }) },
    plugins: { list: async () => ({ plugins }) },
  },
  send: async (options) => {
    assert.equal(options.source, "agent-bridge");
    emit({ effect: { sessionId: sid, options } });
    const event = {
      type: "user.message", sessionId: sid, id: `native-${events.length + 1}`,
      data: { source: options.source, content: options.prompt },
    };
    events.push(event);
    for (const handler of handlers) handler(event);
    return event.id;
  },
  abort: async () => assert.fail("unexpected native abort"),
};
async function load(path, tail = "") {
  const source = readFileSync(path, "utf8");
  const dependencies = {};
  const imports = [...source.matchAll(/^import\s+\{([^}]+)\}\s+from\s+["']([^"']+)["'];/gm)];
  for (const [, , name] of imports) {
    dependencies[name] = name === "@github/copilot-sdk" ? { approveAll: () => {} }
      : name === "@github/copilot-sdk/extension" ? { joinSession: async () => session }
        : await import(name.startsWith(".")
          ? pathToFileURL(join(dirname(path), name)).href : name);
  }
  const linked = source.replace("export async function activateBridgeExtension", "async function activateBridgeExtension").replace(
    /^import\s+\{([^}]+)\}\s+from\s+["']([^"']+)["'];/gm,
    (_statement, names, name) => {
      for (const binding of names.split(",").map((part) => part.trim()).filter(Boolean)) {
        assert.match(binding, /^[A-Za-z_$][\w$]*$/);
        assert.ok(Object.hasOwn(dependencies[name], binding));
      }
      return `const {${names}} = __dependencies[${JSON.stringify(name)}];`;
    },
  );
  assert.doesNotMatch(linked, /^import\s/m);
  const context = vm.createContext({
    __dependencies: dependencies, __session: session, __bridgeRoot: bridgeRoot,
    process, Buffer, URL, AbortController,
    fetch: carrier, setTimeout, clearTimeout, clearInterval,
    setInterval: (fn, ms) => {
      const timer = setInterval(fn, ms);
      timers.push(timer);
      return timer;
    },
  });
  return new vm.Script(`(async () => {\n${linked}\n${tail}\n})()`, { filename: path })
    .runInContext(context);
}
const headed = JSON.parse(process.env.COPILOT_DRIVER_HEADED_CLIENT);
assert.equal(headed.sessionId, sid);
emit({ stage: "loading common headed extension" });
await load(join(dirname(headed.client), "extension.mjs"));
emit({ stage: "loading driver extension" });
driver = await load(
  join(driverRoot, "extensions", "agent-remote-driver", "extension.mjs"),
  "return { driverServer, cleanupDescriptor };",
);
emit({ stage: "loading bridge extension" });
await load(
  join(bridgeRoot, "extensions", "agent-bridge", "core", "extension.mjs"),
  "await activateBridgeExtension({ joinSession: async () => __session, pluginRoot: __bridgeRoot });",
);
emit({ ready: true, sessionId: sid, pid: process.pid });
