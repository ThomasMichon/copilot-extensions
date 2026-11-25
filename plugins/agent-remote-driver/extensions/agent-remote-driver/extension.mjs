import { existsSync } from "node:fs";
import { activateDriverExtension } from "./driver-extension.mjs";

const { loadExtensionSdk } = await import(
  existsSync(new URL("./sdk-compat.mjs", import.meta.url))
    ? new URL("./sdk-compat.mjs", import.meta.url).href
    : new URL("../../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/sdk-compat.mjs", import.meta.url).href
);
const { joinSession } = await loadExtensionSdk();

// No tools or permission handler: ordinary native authorization retains its owner.
await activateDriverExtension({ joinSession });
