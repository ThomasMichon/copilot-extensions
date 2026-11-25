import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const resources = existsSync(new URL("./sdk-compat.mjs", import.meta.url))
  ? new URL("./", import.meta.url)
  : new URL("../../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/", import.meta.url);
const { selectedNativeConnector } = await import(new URL("native-connector.mjs", resources).href);
if (!selectedNativeConnector({
  pluginRoot: fileURLToPath(new URL("../..", import.meta.url)), role: "driver",
})) {
  const { activateDriverExtension } = await import("./driver-extension.mjs");
  const { loadExtensionSdk } = await import(new URL("sdk-compat.mjs", resources).href);
  const { joinSession } = await loadExtensionSdk();
  // No tools or permission handler: ordinary native authorization retains its owner.
  await activateDriverExtension({ joinSession });
}
