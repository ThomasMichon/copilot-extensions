import { joinSession } from "@microsoft/copilot-sdk/extension";
import { createQuotaTool } from "./session-quota.mjs";

let session;
const tool = createQuotaTool(() => session);
session = await joinSession({ tools: [tool] });
