import { existsSync } from "node:fs";
import { createQuotaTool } from "./session-quota.mjs";

const { loadExtensionSdk } = await import(
  existsSync(new URL("./sdk-compat.mjs", import.meta.url))
    ? new URL("./sdk-compat.mjs", import.meta.url).href
    : new URL("../../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/sdk-compat.mjs", import.meta.url).href
);
const { joinSession } = await loadExtensionSdk();

let session;
const tool = createQuotaTool(() => session);
session = await joinSession({ tools: [tool] });
