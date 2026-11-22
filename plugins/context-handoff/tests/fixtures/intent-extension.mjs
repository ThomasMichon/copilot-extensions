import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { IntentCorpus, intentScope } from "../../extensions/context-handoff/intent-corpus.mjs";

const legacy = new IntentCorpus(process.cwd(), { scope: intentScope(process.cwd()).identity });
legacy.capture({ sessionId: "old-anchor", eventId: "old-anchor-event", source: "user",
  origin: "operator", text: "UNRELATED OLD ANCHOR GOAL" });
const other = new IntentCorpus(process.cwd(), { sessionId: "other-anchor" });
other.capture({ sessionId: "other-anchor", eventId: "other-event", source: "user",
  origin: "operator", text: "UNRELATED OTHER SESSION GOAL" });
await import(pathToFileURL(process.argv[2]).href);
const session = globalThis.nativeTestSession;
const tools = session.config.tools.definitions ?? session.config.tools;
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
assert.ok(Buffer.byteLength(replay.prompt, "utf8") < 4200);
assert.ok(!replay.prompt.includes(text));
assert.match(replay.prompt, /Read only the sources/u);
assert.doesNotMatch(replay.prompt, /UNRELATED/u);
const snapshot = /- Pinned snapshot: ([a-f0-9]{64}) -- 2 records/u.exec(replay.prompt)?.[1];
assert.ok(snapshot);
const reader = tools.find((tool) => tool.name === "read_intent_corpus");
assert.ok(reader);
assert.match(reader.description, /follow next only when the current step needs more source text/u);
const segments = [];
let cursor = {};
do {
  const result = await reader.handler({ snapshot, ...cursor });
  assert.equal(result.resultType, "success");
  const page = JSON.parse(result.textResultForLlm);
  segments.push(...page.records.filter((record) => record.eventId === "operator-one").map((record) => record.text));
  cursor = page.next;
} while (cursor);
assert.equal(segments.join(""), text);
const generate = tools.find((tool) => tool.name === "generate_handoff_prompt");
const generated = await generate.handler({}, {});
assert.ok(generated.textResultForLlm.includes(snapshot));
assert.match(generated.textResultForLlm, /Canonical Goal and Intent Corpus/u);
assert.doesNotMatch(generated.textResultForLlm, /UNRELATED/u);
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
