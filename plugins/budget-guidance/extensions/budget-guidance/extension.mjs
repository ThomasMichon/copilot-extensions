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
import { createQuotaTool } from "./session-quota.mjs";

let session;
const tool = createQuotaTool(() => session);
session = await joinSession({ tools: [tool] });
