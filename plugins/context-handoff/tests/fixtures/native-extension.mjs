import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";
await import(pathToFileURL(process.argv[2]).href);
const session = globalThis.nativeTestSession;
const emit = async (type, data = {}, agentId) =>
  session.handlers.get(type)?.({ data, agentId });
const pressure = {
  currentTokens: 820, tokenLimit: 1000, conversationTokens: 600, messagesLength: 8,
};
await emit("session.usage_info", pressure);
await emit("session.idle");
assert.equal(session.prompts.length, 1);
assert.match(session.prompts[0], /Context Handoff -- native/);
assert.match(session.prompts[0], /offered tools unknown/);
assert.doesNotMatch(session.logs.join("\n"), /Auto-generating and triggering/);
assert.equal((await session.config.onPermissionRequest({ kind: "write" })).kind, "approve");
await emit("session.context_cleared", { messagesCleared: 8 }, "child");
await emit("session.usage_info", pressure);
await emit("session.idle");
assert.equal(session.prompts.length, 1);
await emit("session.context_cleared", { messagesCleared: 8 });
await emit("session.usage_info", pressure);
await emit("session.idle");
assert.equal(session.prompts.length, 2);
assert.match(session.prompts[1], /parent objective\/completion gate/);
session.rpc.tools.getCurrentMetadata = async () => ({
  tools: ["get_context_remaining", "session_artifacts", "session_history", "new_context"]
    .map((name) => ({ name })),
});
await writeFile(join(process.env.COPILOT_HOME, "settings.json"), "{}");
await emit("session.tools_updated", { model: "synthetic" });
await emit("session.context_cleared", { messagesCleared: 8 });
await emit("session.usage_info", pressure);
await emit("session.idle");
assert.match(session.prompts[2], /tools-available/);
await writeFile(join(process.env.COPILOT_HOME, "settings.json"),
  '{"contextManagementTools":false}');
await emit("session.context_cleared", { messagesCleared: 8 });
await emit("session.usage_info", { ...pressure, currentTokens: 710 });
await emit("session.idle");
assert.match(session.prompts[3], /trigger_handoff directly/);
assert.doesNotMatch(session.prompts[3], /Context Handoff -- native/);
assert.ok(session.handlers.has("session.tools_updated"));
