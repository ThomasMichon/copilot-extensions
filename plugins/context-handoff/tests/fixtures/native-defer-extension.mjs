import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";
await import(pathToFileURL(process.argv[2]).href);
const session = globalThis.nativeTestSession;
const emit = async (type, data = {}, agentId) =>
  session.handlers.get(type)?.({ data, agentId });
const usage = (currentTokens) => ({
  currentTokens, tokenLimit: 1000, conversationTokens: 600, messagesLength: 8,
});
const settings = (enabled) => writeFile(join(process.env.COPILOT_HOME, "settings.json"),
  `{"contextManagementTools":${enabled}}`);
const deferrals = () => session.logs.filter((line) => /deferred to the host/.test(line)).length;
const pressureLogs = () => session.logs.filter((line) =>
  /checkpoint (?:preparation )?boundary reached|Context utilization|Force threshold/.test(line)).length;

// Native selected by default: neither soft/hard nudges nor force fire, even
// when the (possibly inaccurate) usage estimate crosses every threshold.
await emit("session.usage_info", usage(820));
await emit("session.idle");
await emit("session.usage_info", usage(990));
await emit("session.idle");
assert.equal(session.prompts.length, 0);
assert.equal(deferrals(), 1);
assert.equal(pressureLogs(), 0);
assert.doesNotMatch(session.logs.join("\n"), /Auto-generating and triggering/);
assert.equal((await session.config.onPermissionRequest({ kind: "write" })).kind, "approve");

// A nudge queued while custom recovery was selected is dropped at idle once
// native context management becomes selected.
await emit("session.context_cleared", { messagesCleared: 8 });
await settings(false);
await emit("session.tools_updated", { model: "synthetic" });
await emit("session.usage_info", usage(710));
await settings(true);
await emit("session.idle");
assert.equal(session.prompts.length, 0);
assert.equal(deferrals(), 2);

// Custom (non-native) recovery keeps its existing nudges.
await emit("session.context_cleared", { messagesCleared: 8 });
await settings(false);
await emit("session.tools_updated", { model: "synthetic" });
await emit("session.usage_info", usage(710));
await emit("session.idle");
assert.equal(session.prompts.length, 1);
assert.match(session.prompts[0], /trigger_handoff directly/);

// Manual entry points remain available while native pressure is deferred.
await emit("session.context_cleared", { messagesCleared: 8 });
await settings(true);
await emit("session.tools_updated", { model: "synthetic" });
const generate = session.config.tools.find((tool) => tool.name === "generate_handoff_prompt");
const generated = await generate.handler({}, {});
assert.equal(typeof generated.textResultForLlm, "string");
assert.ok(generated.textResultForLlm.length > 0);
