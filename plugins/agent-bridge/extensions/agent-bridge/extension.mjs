import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

async function importExtensionSdk(subpath = "") {
  try {
    return await import(`@microsoft/copilot-sdk${subpath}`);
  } catch (error) {
    if (error?.code !== "ERR_MODULE_NOT_FOUND" ||
        !error.message?.startsWith("Cannot find package '@microsoft/copilot-sdk' imported from ")) {
      throw error;
    }
    return import(`@github/copilot-sdk${subpath}`);
  }
}

const { joinSession } = await importExtensionSdk("/extension");

const packaged = new URL("./core/extension.mjs", import.meta.url);
// Promotion projects the canonical Core assets into this plugin. A source
// checkout uses that same owner directly, without installing a runtime.
const implementation = existsSync(new URL("./core/", import.meta.url)) ? packaged : new URL(
  "../../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/extension.mjs",
  import.meta.url,
);
const { activateBridgeExtension } = await import(implementation.href);
await activateBridgeExtension({
  joinSession, pluginRoot: fileURLToPath(new URL("../..", import.meta.url)),
});
