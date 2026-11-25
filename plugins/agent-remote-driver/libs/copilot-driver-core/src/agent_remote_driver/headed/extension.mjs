import { existsSync } from "node:fs";
import { headedBinding, connectHeadedSession } from "./connection.mjs";

const binding = headedBinding(process.env.COPILOT_DRIVER_HEADED_BINDING);
delete process.env.COPILOT_DRIVER_HEADED_BINDING;
const { loadExtensionSdk } = await import(
  existsSync(new URL("./sdk-compat.mjs", import.meta.url))
    ? new URL("./sdk-compat.mjs", import.meta.url).href
    : new URL("../bridge_extension/sdk-compat.mjs", import.meta.url).href
);
const { joinSession } = await loadExtensionSdk();
// Ordinary native authorization retains its owner.
await connectHeadedSession(await joinSession(), binding);
