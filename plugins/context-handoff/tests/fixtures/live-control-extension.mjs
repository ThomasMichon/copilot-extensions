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
const configPath = join(process.cwd(), ".context-handoff", "config.yaml");
const writeConfig = (text) => writeFile(configPath, text);
const command = session.config.commands.find((entry) => entry.name === "handoff-pressure");
const status = async () => {
  const before = session.logs.length;
  await command.handler({ args: "status" });
  return session.logs.slice(before).join("\n");
};

await writeFile(join(process.env.COPILOT_HOME, "settings.json"), '{"contextManagementTools":false}');
await emit("session.tools_updated", { model: "synthetic" });

// Default soft tier (55%) fires once at 60%.
await emit("session.usage_info", usage(600));
await emit("session.idle");
assert.equal(session.prompts.length, 1);
assert.match(await status(), /Automatic pressure handling: active\./);

// Raising soft above current usage on reload re-arms the delivered tier, so
// it fires again once usage crosses the new threshold.
await writeConfig("mode: auto\nnative_pressure: nudge\nthresholds:\n  soft_percent: 65\n");
await emit("session.idle");
assert.match(session.logs.join("\n"), /Reloaded config/);
await emit("session.usage_info", usage(660));
await emit("session.idle");
assert.equal(session.prompts.length, 2);

// A mixed-unit edit that misorders against the live window keeps the previous
// config instead of applying it.
await writeConfig("mode: auto\nnative_pressure: nudge\nthresholds:\n  soft_tokens: 900\n  hard_percent: 70\n");
await emit("session.idle");
assert.match(session.logs.join("\n"), /Ignoring the edited context-handoff config/);
assert.match(await status(), /Config thresholds: soft 65%/);

// Status reports mode off as disabled, not active.
await writeConfig("mode: off\n");
await emit("session.idle");
assert.match(await status(), /disabled by config \(mode: off\)/);
