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
session.rpc.tools.getCurrentMetadata = async () => { throw new Error("synthetic"); };
await emit("session.tools_updated", { model: "synthetic" });
await emit("session.usage_info", { ...pressure, currentTokens: 710 });
const warningCount = () => session.logs.filter((s) => /observation failed/.test(s)).length;
const firstWarnings = warningCount();
await emit("session.usage_info", { ...pressure, currentTokens: 710 });
assert.equal(warningCount(), firstWarnings);
await emit("session.context_cleared", { messagesCleared: 8 });
await emit("session.usage_info", { ...pressure, currentTokens: 710 });
assert.equal(warningCount(), firstWarnings + 1);
const generate = session.config.tools.find((tool) => tool.name === "generate_handoff_prompt");
await generate.handler({}, {});
await emit("session.context_cleared", { messagesCleared: 8 });
const beforeManualReset = session.prompts.length;
await emit("session.usage_info", { ...pressure, currentTokens: 710 });
await emit("session.idle");
assert.equal(session.prompts.length, beforeManualReset + 1);
await emit("session.context_cleared", { messagesCleared: 8 });
await writeFile(join(process.env.COPILOT_HOME, "settings.json"),
  '{"contextManagementTools":true}');
let entered;
let release;
const started = new Promise((resolve) => { entered = resolve; });
const held = new Promise((resolve) => { release = resolve; });
session.rpc.tools.getCurrentMetadata = async () => { entered(); await held; return { tools: null }; };
const interruptedUsage = emit("session.usage_info", pressure);
await started;
await emit("session.tools_updated", { model: "synthetic" });
release();
await interruptedUsage;
await new Promise((resolve) => setTimeout(resolve, 150));
const beforeRetry = session.prompts.length;
await emit("session.idle");
assert.equal(session.prompts.length, beforeRetry + 1);
assert.match(session.prompts.at(-1), /Context Handoff -- native/);
await emit("session.compaction_complete", { success: true, postCompactionTokens: 100 });
await emit("session.tools_updated", { model: "synthetic" });
await new Promise((resolve) => setTimeout(resolve, 150));
const afterCompaction = session.prompts.length;
await emit("session.idle");
assert.equal(session.prompts.length, afterCompaction);
let generationEntered;
let generationRelease;
const generationStarted = new Promise((resolve) => { generationEntered = resolve; });
const generationHeld = new Promise((resolve) => { generationRelease = resolve; });
session.rpc.tools.getCurrentMetadata = async () => {
  generationEntered();
  await generationHeld;
  return { tools: null };
};
await emit("session.tools_updated", { model: "synthetic" });
const generation = generate.handler({}, {});
await generationStarted;
await emit("session.context_cleared", { messagesCleared: 8 });
generationRelease();
await generation;
await emit("session.usage_info", pressure);
const afterGeneration = session.prompts.length;
await emit("session.idle");
assert.equal(session.prompts.length, afterGeneration + 1);
session.rpc.tools.getCurrentMetadata = async () => ({
  tools: ["get_context_remaining", "session_artifacts", "session_history", "new_context"]
    .map((name) => ({ name })),
});
const evidence = "Original source: canonical Scope Approvals; excludes personal content; current hold applies";
assert.ok(generate.parameters.properties.authorization_evidence);
const captured = await generate.handler({ authorization_evidence: evidence }, {});
assert.ok(captured.textResultForLlm.includes(evidence));
await emit("session.context_cleared", { messagesCleared: 8 });
assert.ok((await generate.handler({}, {})).textResultForLlm.includes(evidence));
await assert.rejects(generate.handler({ authorization_evidence: false }, {}), /must be a string/);
assert.ok((await generate.handler({}, {})).textResultForLlm.includes(evidence));
const cleared = await generate.handler({ authorization_evidence: "" }, {});
assert.match(cleared.textResultForLlm, /Not captured -- recover the original/);
await generate.handler({ authorization_evidence: evidence }, {});
await import(`${pathToFileURL(process.argv[2]).href}?authorization-reimport`);
const reimported = globalThis.nativeTestSession.config.tools.find(
  (tool) => tool.name === "generate_handoff_prompt",
);
const afterReimport = await reimported.handler({}, {});
assert.ok(!afterReimport.textResultForLlm.includes(evidence));
assert.match(afterReimport.textResultForLlm, /Not captured -- recover the original/);
assert.match(afterReimport.textResultForLlm, /Do not infer permission or ask for blanket reapproval/);
