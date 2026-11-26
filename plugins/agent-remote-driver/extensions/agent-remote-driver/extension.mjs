import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const packaged = existsSync(new URL("./sdk-compat.mjs", import.meta.url));
const resources = packaged
  ? new URL("./", import.meta.url)
  : new URL("../../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/", import.meta.url);
const { activateNativeConnector, GLOBAL_CONNECTOR_CONTRACT, selectedNativeConnector } =
  await import(new URL("native-connector.mjs", resources).href);
const pluginRoot = fileURLToPath(new URL("../..", import.meta.url));
const selected = selectedNativeConnector({ pluginRoot, role: "driver" });
if (selected?.contract === GLOBAL_CONNECTOR_CONTRACT) {
  const projected = existsSync(new URL("./core/extension.mjs", import.meta.url));
  if (packaged && !projected) throw new Error("Selected global Driver connector resources are incomplete");
  await activateNativeConnector({
    selected, pluginRoot,
    coreResources: projected ? new URL("./core/", import.meta.url) : resources,
    headedResources: projected ? new URL("./headed/", import.meta.url)
      : new URL("../headed/", resources),
  });
} else if (!selected) {
  const { activateDriverExtension } = await import("./driver-extension.mjs");
  const { loadExtensionSdk } = await import(new URL("sdk-compat.mjs", resources).href);
  const { joinSession } = await loadExtensionSdk();
  // No tools or permission handler: ordinary native authorization retains its owner.
  await activateDriverExtension({ joinSession });
}
