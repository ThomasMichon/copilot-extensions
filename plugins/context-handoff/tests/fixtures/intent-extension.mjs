import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";

await import(pathToFileURL(process.argv[2]).href);
const session = globalThis.nativeTestSession;
session.sessionId = "fixture-intent-session";
const emit = async (type, data = {}, extra = {}) =>
  session.handlers.get(type)?.({ type, data, ...extra });
const waitFor = async (predicate) => {
  const end = Date.now() + 3000;
  while (!predicate()) {
    assert.ok(Date.now() < end, "extension continuity fixture did not settle");
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
};
const text = "Preserve the complete objective and current holds.\r\n".repeat(700);
await emit("user.message", { content: text, source: "user" }, { id: "operator-one" });
await emit("user.message", { content: "<skill-context>noise</skill-context>" }, { id: "skill" });
await emit("user.message", { content: "child instruction" }, { id: "child", agentId: "child" });
const peer = '<agent-message from="peer" msg-id="2">Research this steer before action.</agent-message>';
await emit("user.message", { content: "Message from peer", transformedContent: peer,
  source: "agent-bridge" }, { id: "peer-one" });
await emit("session.context_cleared");
await waitFor(() => session.sends.some((message) => message.prompt?.includes("<intent-continuity")));
const replay = session.sends.find((message) => message.prompt?.includes("<intent-continuity"));
assert.equal(replay.source, "system");
const facts = JSON.parse(replay.prompt.split("\n\n")[2]);
assert.equal(facts.corpus.count, 2);
const reader = session.config.tools.find((tool) => tool.name === "read_intent_corpus");
assert.ok(reader);
const segments = [];
let cursor = {};
do {
  const result = await reader.handler({ snapshot: facts.corpus.id, ...cursor });
  assert.equal(result.resultType, "success");
  const page = JSON.parse(result.textResultForLlm);
  segments.push(...page.records.filter((record) => record.eventId === "operator-one").map((record) => record.text));
  cursor = page.next;
} while (cursor);
assert.equal(segments.join(""), text);
const generate = session.config.tools.find((tool) => tool.name === "generate_handoff_prompt");
const generated = await generate.handler({}, {});
assert.ok(generated.textResultForLlm.includes(facts.corpus.id));
assert.match(generated.textResultForLlm, /Canonical Goal and Intent Corpus/u);
const before = session.sends.length;
await emit("session.compaction_complete", { success: false });
await new Promise((resolve) => setImmediate(resolve));
assert.equal(session.sends.length, before);
await emit("session.compaction_complete", { success: true });
await waitFor(() => session.sends.length > before);
assert.match(session.sends.at(-1).prompt, /compaction-complete/u);
const config = join(process.cwd(), ".context-handoff", "config.yaml");
await writeFile(config, "mode: off\n");
const beforeOff = session.sends.length;
await emit("session.context_cleared");
await waitFor(() => session.logs.some((line) => line.includes("Reloaded config") && line.includes("mode off")));
await new Promise((resolve) => setImmediate(resolve));
assert.equal(session.sends.length, beforeOff);
await writeFile(config, "mode: manual-only\n");
await emit("tool.execution_start", { toolCallId: "mode-enabled", toolName: "view" });
await waitFor(() => session.sends.length > beforeOff);
assert.match(session.sends.at(-1).prompt, /intent-continuity/u);
await emit("session.shutdown");
