import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";

process.on("uncaughtExceptionMonitor", error => process.stderr.write(`${error.stack || error}\n`));

await import(pathToFileURL(process.argv[2]).href);
const session = globalThis.nativeTestSession;
const tools = session.config.tools.definitions ?? session.config.tools;
const commands = session.config.interaction?.commands ?? session.config.commands;
const emit = async (type, data = {}, extra = {}) =>
  session.handlers.get(type)?.({ type, data, ...extra });
const waitFor = async (predicate) => {
  const end = Date.now() + 3000;
  while (!predicate()) {
    assert.ok(Date.now() < end, "bound extension did not settle");
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
};
await emit("user.message", { content: "Keep the bound mission.", source: "user" }, { id: "one" });
if (process.env.FIXTURE_DELAYED_ID) {
  assert.equal(session.sends.length, 0);
  assert.equal(globalThis.intentGoalReads, undefined);
  let text;
  if (process.env.FIXTURE_CONSUME) {
    const consume = commands.find((command) => command.name === "consume-handoff");
    await consume.handler({ sessionId: "fixture-bound-session" });
    assert.equal(session.sends.length, 1);
    text = session.sends[0].prompt;
    assert.ok(globalThis.consumeCwds.length);
    assert.ok(globalThis.consumeCwds.every((cwd) => cwd === process.env.FIXTURE_BOUND_CWD));
    session.sends.length = 0;
  } else {
    const generate = tools.find((tool) => tool.name === "generate_handoff_prompt");
    text = (await generate.handler({}, { sessionId: "fixture-bound-session" })).textResultForLlm;
  }
  assert.match(text, /Bound Goal/u);
  assert.match(text, /Pinned snapshot/u);
  assert.ok(text.includes(process.env.FIXTURE_BOUND_CWD));
}
await emit("session.context_cleared");
// Bound mode off must win over launch mode auto, including the initial-load race.
const status = tools.find((tool) => tool.name === "handoff_pressure");
const initial = await status.handler({ action: "status" }, {});
assert.match(initial, /disabled by config \(mode: off\)/u);
assert.equal(session.sends.length, 0);
const config = join(process.env.FIXTURE_BOUND_CWD, ".context-handoff", "config.yaml");
await writeFile(config, "mode: manual-only\n");
await emit("tool.execution_start", { toolCallId: "enable", toolName: "view" });
await waitFor(() => session.sends.some((item) => item.prompt?.includes("<intent-continuity")));
assert.match(session.sends.at(-1).prompt, /Bound Goal/u);
await writeFile(config, "mode: auto\n");
await writeFile(join(process.env.COPILOT_HOME, "settings.json"), '{"contextManagementTools":false}');
const beforeReset = session.sends.length;
await emit("session.context_cleared");
await waitFor(() => session.sends.slice(beforeReset).some((item) =>
  item.prompt?.includes('<intent-continuity reason="context-cleared">')));
// Explicit status settles the live config refresh before emergency pressure.
await status.handler({ action: "status" }, {});
const reads = globalThis.intentGoalReads;
globalThis.emergencyStarted = true;
await emit("session.usage_info", {
  currentTokens: 950, tokenLimit: 1000, conversationTokens: 900, messagesLength: 8,
});
await waitFor(() => globalThis.emergencyStored);
assert.equal(globalThis.intentGoalReads, reads);
assert.equal(globalThis.emergencyStored.cwd, process.env.FIXTURE_BOUND_CWD);
assert.match(globalThis.emergencyStored.promptText, /cached-unqualified/u);
assert.match(globalThis.emergencyStored.promptText, /current goal unqualified/u);
await emit("session.shutdown");
