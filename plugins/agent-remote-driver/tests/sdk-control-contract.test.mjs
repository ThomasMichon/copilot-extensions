import { test } from "node:test";
import assert from "node:assert/strict";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";
import { createSessionDriver } from "../extensions/agent-remote-driver/session-driver.mjs";

const sdkRoot = process.env.AGENT_REMOTE_DRIVER_SDK_ROOT;

test("installed SDK request construction preserves the adapter's prompt and ownership source", {
  skip: sdkRoot ? false : "Set AGENT_REMOTE_DRIVER_SDK_ROOT to an inspected installed SDK; no runtime is launched",
}, async () => {
  const { CopilotSession } = await import(pathToFileURL(join(resolve(sdkRoot), "index.js")).href);
  const requests = [];
  const session = new CopilotSession("synthetic-session", {
    async sendRequest(method, params) {
      requests.push({ method, params: JSON.parse(JSON.stringify(params)) });
      return { messageId: "synthetic-message" };
    },
  });
  const adapter = createSessionDriver(session);
  await adapter.send({ content: "synthetic prompt", mode: "immediate", source: "agent-synthetic-source" });
  const request = requests[0];
  assert.equal(request.method, "session.send");
  assert.equal(request.params.prompt, "synthetic prompt");
  assert.equal(request.params.source, "agent-synthetic-source");
  assert.equal(request.params.mode, "immediate");
  assert.equal(Object.hasOwn(request.params, "content"), false);
});
