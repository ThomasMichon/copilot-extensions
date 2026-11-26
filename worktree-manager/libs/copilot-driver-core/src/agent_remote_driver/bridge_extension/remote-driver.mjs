import { open } from "node:fs/promises";
import { realpathSync } from "node:fs";
import { isAbsolute, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

export const isRemoteDriverSource = (source) =>
  typeof source === "string" && /^agent-remote-driver-[0-9a-f-]{36}$/.test(source);

function selectedContextError(reason, message, plugin) {
  return Object.assign(new Error(message), {
    selectedContextReason: reason,
    ...(plugin === undefined ? {} : { selectedContextPlugin: plugin }),
  });
}

function selectedIdentityObservation(owner, expectedPid) {
  return {
    id: owner.id === "plugin:copilot-driver-headed:copilot-driver-headed" ? "match"
      : typeof owner.id === "string" && owner.id ? "other" : "missing",
    name: owner.name === "copilot-driver-headed" ? "directory"
      : typeof owner.name !== "string" || !owner.name ? "missing"
      : owner.name === owner.id ? "qualified" : "other",
    source: ["project", "user", "plugin", "session"].includes(owner.source) ? owner.source : "unknown",
    status: ["running", "disabled", "failed", "starting"].includes(owner.status) ? owner.status : "unknown",
    pid: Number.isSafeInteger(owner.pid) && owner.pid > 0
      ? owner.pid === expectedPid ? "match" : "other"
      : owner.pid == null ? "missing" : "invalid",
  };
}

export async function selectedLoadout(native, {
  pluginRoot, expectedSessionId, cwd, bridgePluginRoot, extensionPluginRoot, nativeConnector, extensionPid,
}) {
  if (bridgePluginRoot === undefined) return;
  const canonical = (path) => realpathSync(resolve(path));
  if (typeof bridgePluginRoot !== "string" || !isAbsolute(bridgePluginRoot)
      || typeof extensionPluginRoot !== "string" || !isAbsolute(extensionPluginRoot)
      || extensionPid !== undefined && (
        nativeConnector === undefined || !Number.isSafeInteger(extensionPid) || extensionPid <= 0
      )
      || nativeConnector !== undefined && (
        nativeConnector.sessionId !== expectedSessionId || nativeConnector.cwd !== cwd
        || nativeConnector.bridgePluginRoot !== bridgePluginRoot
        || nativeConnector.driverPluginRoot !== pluginRoot
      )) {
    throw selectedContextError("selection-invalid",
      "Portable receiving requires native observation of its selected extension roots");
  }
  let connectorRoot, extensionRoot;
  try {
    connectorRoot = canonical(nativeConnector?.connectorPluginRoot ?? bridgePluginRoot);
    extensionRoot = canonical(extensionPluginRoot);
  } catch {
    throw selectedContextError("selection-root-unavailable", "Selected extension roots are unavailable");
  }
  if (connectorRoot !== extensionRoot) {
    throw selectedContextError("selection-root-mismatch",
      "Portable receiving requires native observation of its selected extension roots");
  }
  const executing = extensionPid !== undefined;
  const observer = executing ? native.rpc?.extensions : native.rpc?.plugins;
  if (typeof observer?.list !== "function") {
    throw selectedContextError(executing ? "extensions-list-unsupported" : "plugins-list-unsupported",
      "Portable receiving requires native observation of its selected extension roots");
  }
  if (typeof native.rpc?.metadata?.snapshot !== "function") {
    throw selectedContextError("metadata-snapshot-unsupported", "Native metadata observation is unsupported");
  }
  const observe = async (operation, reason) => {
    try { return await operation(); }
    catch { throw selectedContextError(reason, "Native execution context observation failed"); }
  };
  const before = await observe(() => native.rpc.metadata.snapshot(), "metadata-before-failed");
  const loadout = await observe(() => observer.list(),
    executing ? "extensions-list-failed" : "plugins-list-failed");
  const after = await observe(() => native.rpc.metadata.snapshot(), "metadata-after-failed");
  for (const snapshot of [before, after]) {
    if (snapshot?.sessionId !== expectedSessionId) {
      throw selectedContextError("native-session-mismatch", "Portable receiving native execution context changed");
    }
    let observedCwd, selectedCwd;
    try {
      if (typeof snapshot.workingDirectory !== "string") throw new Error();
      observedCwd = canonical(snapshot.workingDirectory);
      selectedCwd = canonical(cwd);
    } catch {
      throw selectedContextError("native-cwd-unavailable", "Native working directory observation is unavailable");
    }
    if (observedCwd !== selectedCwd) {
      throw selectedContextError("native-cwd-mismatch", "Portable receiving native execution context changed");
    }
  }
  if (executing) {
    if (!Array.isArray(loadout?.extensions)) {
      throw selectedContextError("extension-observation-invalid", "Native extension observation is unavailable");
    }
    const invalid = (reason, role) => selectedContextError(reason,
      "The launch-bound native connector has another or unavailable executing owner", role);
    const owners = loadout.extensions.filter((extension) =>
      extension?.name === "copilot-driver-headed"
      || typeof extension?.id === "string" && extension.id.startsWith("plugin:copilot-driver-headed:"));
    if (owners.length !== 1) {
      throw invalid(owners.length === 0 ? "plugin-missing" : "plugin-duplicated", "connector");
    }
    const owner = owners[0];
    if (owner.id !== "plugin:copilot-driver-headed:copilot-driver-headed"
        || owner.name !== "copilot-driver-headed" || owner.source !== "plugin") {
      throw Object.assign(invalid("extension-identity-mismatch", "connector"), {
        selectedContextIdentity: selectedIdentityObservation(owner, extensionPid),
      });
    }
    if (owner.status !== "running") throw invalid("extension-not-running", "connector");
    if (owner.pid !== extensionPid) throw invalid("extension-process-mismatch", "connector");
    for (const [name, role] of [["agent-bridge", "bridge"], ["agent-remote-driver", "driver"]]) {
      const peers = loadout.extensions.filter((extension) =>
        extension?.name === name || typeof extension?.id === "string" && extension.id.startsWith(`plugin:${name}:`));
      if (peers.some((extension) => !["disabled", "failed"].includes(extension.status))) {
        throw invalid("extension-competing", role);
      }
    }
    return;
  }
  if (!Array.isArray(loadout?.plugins)) {
    throw selectedContextError("plugin-observation-invalid", "Portable receiving plugin observation is unavailable");
  }
  const required = [["agent-bridge", bridgePluginRoot, "bridge"], ["agent-remote-driver", pluginRoot, "driver"]];
  if (nativeConnector !== undefined) required.push(["copilot-driver-headed", extensionPluginRoot, "connector"]);
  for (const [name, root, role] of required) {
    const active = loadout.plugins.filter((plugin) => plugin?.name === name && plugin.enabled === true);
    const invalid = (reason) => selectedContextError(reason,
      "Portable receiving extensions are missing, duplicated or from another root", role);
    if (active.length !== 1) throw invalid(active.length === 0 ? "plugin-missing" : "plugin-duplicated");
    if (active[0].installed === false) throw invalid("plugin-not-installed");
    if (typeof active[0].installedFrom !== "string" || !active[0].installedFrom) {
      throw invalid("plugin-origin-missing");
    }
    if (!isAbsolute(active[0].installedFrom)) throw invalid("plugin-origin-relative");
    let observedRoot, selectedRoot;
    try {
      observedRoot = canonical(active[0].installedFrom);
      selectedRoot = canonical(root);
    } catch {
      throw invalid("plugin-origin-unavailable");
    }
    if (observedRoot !== selectedRoot) throw invalid("plugin-origin-mismatch");
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
