import { open } from "node:fs/promises";
import { realpathSync } from "node:fs";
import { isAbsolute, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

export const isRemoteDriverSource = (source) =>
  typeof source === "string" && /^agent-remote-driver-[0-9a-f-]{36}$/.test(source);

export async function selectedLoadout(native, {
  pluginRoot, expectedSessionId, cwd, bridgePluginRoot, extensionPluginRoot,
}) {
  if (bridgePluginRoot === undefined) return;
  const canonical = (path) => realpathSync(resolve(path));
  if (typeof bridgePluginRoot !== "string" || !isAbsolute(bridgePluginRoot)
      || typeof extensionPluginRoot !== "string" || !isAbsolute(extensionPluginRoot)
      || canonical(bridgePluginRoot) !== canonical(extensionPluginRoot)
      || typeof native.rpc?.plugins?.list !== "function") {
    throw new Error("Portable receiving requires native observation of its selected extension roots");
  }
  const before = await native.rpc.metadata.snapshot();
  const loadout = await native.rpc.plugins.list();
  const after = await native.rpc.metadata.snapshot();
  for (const snapshot of [before, after]) {
    if (snapshot?.sessionId !== expectedSessionId
        || typeof snapshot.workingDirectory !== "string"
        || canonical(snapshot.workingDirectory) !== canonical(cwd)) {
      throw new Error("Portable receiving native execution context changed");
    }
  }
  if (!Array.isArray(loadout?.plugins)) throw new Error("Portable receiving plugin observation is unavailable");
  for (const [name, root] of [["agent-bridge", bridgePluginRoot], ["agent-remote-driver", pluginRoot]]) {
    const active = loadout.plugins.filter((plugin) => plugin?.name === name && plugin.enabled === true);
    if (active.length !== 1 || active[0].installed === false
        || typeof active[0].installedFrom !== "string"
        || !isAbsolute(active[0].installedFrom)
        || canonical(active[0].installedFrom) !== canonical(root)) {
      throw new Error("Portable receiving extensions are missing, duplicated or from another root");
    }
  }
}

export async function createHeadedDriverSession(native, {
  declaration, cwd, nativePid, pluginRoot, bridgePluginRoot, expectedSessionId, extensionPluginRoot,
}) {
  let binding;
  try { binding = JSON.parse(declaration); }
  catch { throw new Error("Invalid headed launch declaration; native SDK fallback is forbidden"); }
  if (binding?.contract !== "copilot-driver.headed-client" || binding.version !== 1 ||
      typeof binding.client !== "string" || !isAbsolute(binding.client)) {
    throw new Error("Missing retained headed client; native SDK fallback is forbidden");
  }
  if (pluginRoot !== undefined && (
    typeof pluginRoot !== "string" || !isAbsolute(pluginRoot) || expectedSessionId !== binding.sessionId
  )) throw new Error("Retained receiving selection differs from the headed native launch");
  const module = await import(pathToFileURL(binding.client).href);
  const client = module.createHeadedSession(native, { binding, cwd, nativePid });
  let held = null;
  return {
    ...client,
    get sessionId() { return native.sessionId; },
    async send(options) {
      if (held) throw held;
      try {
        await selectedLoadout(native, {
          pluginRoot, bridgePluginRoot, expectedSessionId: binding.sessionId, cwd, extensionPluginRoot,
        });
      } catch (error) {
        held = error;
        client.close();
        throw error;
      }
      return client.send(options);
    },
  };
}

export function createRemoteDriverSession(native, {
  pluginRoot, descriptorDirectory, expectedSessionId, cwd, ownerId, bridgePluginRoot, extensionPluginRoot,
}) {
  if (![pluginRoot, descriptorDirectory, cwd].every((value) => typeof value === "string" && isAbsolute(value))
      || typeof expectedSessionId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$/.test(expectedSessionId)
      || typeof ownerId !== "string" || !ownerId || ownerId.length > 128) {
    throw new Error("Remote-driver delivery requires its explicit launch identity and absolute support paths");
  }
  let startup = null;
  let held = null;

  async function connect() {
    if (native.sessionId !== expectedSessionId) throw new Error("Remote-driver native session changed");
    const handle = await open(join(descriptorDirectory, `${expectedSessionId}.json`), "r");
    let descriptor;
    try {
      const { size } = await handle.stat();
      if (size > 64 * 1024) throw new Error("Remote-driver descriptor exceeds the launch bound");
      descriptor = JSON.parse(await handle.readFile("utf8"));
    } finally {
      await handle.close();
    }
    if (descriptor.sessionId !== expectedSessionId || descriptor.cwd !== cwd) {
      throw new Error("Remote-driver descriptor differs from the selected launch");
    }
    const module = await import(pathToFileURL(join(resolve(pluginRoot),
      "extensions", "agent-remote-driver", "startup-client.mjs")).href);
    return module.createStartupSession(descriptor, { ownerId });
  }

  async function controlled() {
    if (native.sessionId !== expectedSessionId) throw new Error("Remote-driver native session changed");
    if (held) throw held;
    try {
      await selectedLoadout(native, {
        pluginRoot, expectedSessionId, cwd, bridgePluginRoot, extensionPluginRoot,
      });
    } catch (error) {
      held = error;
      throw error;
    }
    // Keep a rejected/unknown initial grant too. A polling tick or reconnect
    // must not reacquire ownership after input, takeover or a lost response.
    startup ??= connect();
    return startup;
  }

  return {
    get sessionId() { return native.sessionId; },
    rpc: {
      metadata: { snapshot: (...args) => native.rpc.metadata.snapshot(...args) },
      plugins: { list: (...args) => native.rpc.plugins.list(...args) },
    },
    getEvents: (...args) => native.getEvents(...args),
    send: async (options) => (await controlled()).send(options),
    abort: async () => (await controlled()).abort(),
  };
}
