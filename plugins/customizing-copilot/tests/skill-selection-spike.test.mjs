import test from "node:test";
import assert from "node:assert/strict";
import { freezeCatalog, collectHistory, bytes, spikeInputs, summarizeRequest, SENTINELS,
    modelSummary, observeOwnTask, deadline, syntheticHandler } from "../scripts/skill-selection-spike.mjs";

const event = (id, type, content, extra = {}) => ({ id, type, data: { content }, ...extra });
test("runtime selection is explicit, absolute and never a silent bundled fallback", () => {
    const sdk = process.cwd();
    const root = process.cwd();
    const runtime = process.execPath;
    assert.deepEqual(spikeInputs([sdk, root]), { sdkDirectory: sdk, root, runtimePath: undefined, modelsOnly: false, profile: {} });
    assert.deepEqual(spikeInputs([sdk, root, runtime]), { sdkDirectory: sdk, root, runtimePath: runtime, modelsOnly: false, profile: {} });
    assert.deepEqual(spikeInputs([sdk, root, runtime, "--list-models"]),
        { sdkDirectory: sdk, root, runtimePath: runtime, modelsOnly: true, profile: {} });
    assert.throws(() => spikeInputs([sdk, root, "relative-runtime"]), /absolute-runtime-executable/);
    assert.throws(() => spikeInputs([sdk, root, runtime, "extra"]), /Usage/);
    assert.throws(() => spikeInputs([sdk, root, "--other-mode"]), /unsupported/);
    assert.deepEqual(spikeInputs([sdk, root, "--model", "synthetic-model", "--reasoning-effort", "medium",
        "--context-tier", "long_context"]).profile,
    { model: "synthetic-model", reasoningEffort: "medium", contextTier: "long_context" });
    assert.throws(() => spikeInputs([sdk, root, "--model"]), /missing/);
    assert.throws(() => spikeInputs([sdk, root, "--model", "synthetic", "--context-tier", "invented"]), /unsupported/);
    assert.throws(() => spikeInputs([sdk, root, "--reasoning-effort", "medium"]), /explicit model/);
    assert.throws(() => spikeInputs([sdk, root, "--list-models", "--list-models"]), /duplicate/);
});
test("durable tail pages keep chronology/exclusive boundary and exclude tools/reasoning/children", async () => {
    const calls = [];
    const log = { read: async params => {
        calls.push(params);
        return calls.length === 1
            ? { events: [event("3", "user.message", SENTINELS.boundary), event("4", "assistant.message", "later")],
                cursor: "older", hasMore: true, cursorStatus: "ok" }
            : { events: [event("0", "user.message", SENTINELS.old),
                event("bad", "tool.execution_complete", "RAW_TOOL"), event("r", "assistant.reasoning", "REASONING"),
                event("child", "assistant.message", "CHILD", { agentId: "unrelated" }),
                event("1", "assistant.message", "answer"), event("2", "user.message", SENTINELS.recent)],
                cursor: "end", hasMore: false, cursorStatus: "ok" };
    } };
    const result = await collectHistory(log, { boundary: "3" });
    assert.deepEqual(result.messages.map(item => item.content), [SENTINELS.old, "answer", SENTINELS.recent]);
    assert.equal(calls[1].cursor, "older");
    assert(calls.every(call => call.direction === "backward" && call.agentScope === "primary"
        && !call.includeEphemeral && call.types.join() === "user.message,assistant.message"));
});
test("bounded input uses UTF-8 serialized bytes and newest whole messages", async () => {
    const log = { read: async () => ({ events: [event("1", "user.message", "old".repeat(100)),
        event("2", "assistant.message", "Ω".repeat(10))], cursor: "end", cursorStatus: "ok", hasMore: true }) };
    const result = await collectHistory(log, { maxBytes: 90, maxPages: 1 });
    assert.equal(result.messages.length, 1);
    assert.equal(result.inputBytes, bytes(result.messages));
    assert(result.inputBytes <= 90 && result.truncated && result.omitted === 1);
});
test("expired or unobserved frozen boundary is unavailable, not a brief fallback", async () => {
    await assert.rejects(collectHistory({ read: async () => ({ cursorStatus: "expired" }) }), /expired/);
    await assert.rejects(collectHistory({ read: async () => ({
        events: [], cursor: "end", cursorStatus: "ok", hasMore: false,
    }) }, { boundary: "missing" }), /boundary unavailable/);
});
test("catalog keeps alias, disabled and pathless descriptors without user permission inference", () => {
    const catalog = freezeCatalog([{ name: "actual-id", commandName: "alias", description: "full".repeat(6000),
        enabled: false, source: "provider", userInvocable: true },
    { name: "pathless", description: "advisory", enabled: true, source: "provider", userInvocable: false }]);
    assert.equal(catalog[0].description.length, 24000);
    assert.equal(catalog[0].commandName, "alias");
    assert(!catalog[0].enabled && catalog[1].enabled && catalog[1].path === undefined);
    assert(Object.isFrozen(catalog[0]));
    assert.throws(() => freezeCatalog([{ name: "invalid" }]), /invalid native/);
});
test("assembled request includes initialization/tool overhead separate from supplied input", () => {
    const summary = summarizeRequest({ model: "synthetic-model", messages: [
        { role: "system", content: "init".repeat(300) },
        { role: "user", content: SENTINELS.old }, { role: "assistant", content: SENTINELS.recent },
        { role: "user", content: SENTINELS.brief }],
    tools: [{ type: "function", function: { name: "read_only", parameters: { type: "object" } } }] },
    { sessionId: "owned", agentId: "child" });
    assert(summary.assembledBytes > bytes(SENTINELS.brief));
    assert(summary.toolBytes > 2 && summary.oldBeforeRecent);
    assert.deepEqual(summary.tools, ["read_only"]);
    assert(!JSON.stringify(summary).includes(SENTINELS.old));
    assert.throws(() => summarizeRequest({ input: "unknown" }), /unsupported/);
});
test("JsonValue model metadata cannot manufacture supported limits or efforts", () => {
    const summary = modelSummary({ id: "synthetic-model", capabilities: {
        supports: { reasoningEffort: true }, limits: { max_context_window_tokens: -1, max_prompt_tokens: 400 } },
    supportedReasoningEfforts: ["medium"], supportedContextTiers: ["default", "long_context"] });
    assert.equal(summary.contextWindow, null);
    assert.equal(summary.promptLimit, 400);
    assert.deepEqual(summary.efforts, ["medium"]);
    assert.equal(modelSummary(null).id, null);
    assert.equal(modelSummary({ supportedReasoningEfforts: [2] }).efforts, null);
    assert.equal(modelSummary({ billing: { tokenPrices: { longContext: {} } } }).contextTiers, null);
});
test("subscribe before dispatch; ACK/foreign/late output cannot substitute own settlement", async () => {
    let listener, unsubscribed = false;
    const observation = observeOwnTask({ on: callback => { listener = callback; return () => { unsubscribed = true; }; } });
    listener({ type: "subagent.completed", agentId: "foreign", data: { model: "wrong" } });
    assert.equal((await observation.settled("own", 10)).settled, false);
    listener({ type: "subagent.configured", agentId: "own", data: { model: "synthetic", reasoningEffort: "medium" } });
    listener({ type: "subagent.completed", agentId: "own", data: { cancelled: true, model: "synthetic" } });
    const result = await observation.settled("own");
    assert(result.settled && result.cancelled && !result.resultObserved);
    assert.equal(result.actualModel, "synthetic");
    // Result is a detached snapshot: a later event cannot turn cancellation into successful output.
    listener({ type: "assistant.message", agentId: "own", data: { content: "late" } });
    assert.equal(result.resultObserved, false);
    const again = await observation.settled("own");
    assert(!again.resultObserved && again.lateOutputIgnored);
    observation.unsubscribe();
    assert(unsubscribed);
});
test("timeout is not operation abort or settlement", async () => {
    let completed = false;
    const operation = new Promise(resolve => setTimeout(() => { completed = true; resolve(); }, 20));
    await assert.rejects(deadline(operation, 1), /deadline/);
    assert(!completed);
    await operation;
    assert(completed);
});
class NeverForward {
    sendRequest() { throw new Error("unexpected forwarding"); }
    openWebSocket() { throw new Error("unexpected forwarding"); }
}
const request = (content, stream = false) => new Request("https://synthetic.invalid/model", {
    method: "POST", body: JSON.stringify({ model: "synthetic", stream,
        messages: [{ role: "user", content }] }),
});
test("interceptor completes buffered/SSE locally and never inherits forwarding", async () => {
    const captures = [];
    const handler = syntheticHandler(NeverForward, new Set(["own"]), captures);
    const context = { sessionId: "own", signal: new AbortController().signal };
    const buffered = await handler.sendRequest(request(SENTINELS.brief), context);
    assert.equal((await buffered.json()).choices[0].message.content, "SYNTHETIC_TRANSPORT_ONLY");
    const stream = await handler.sendRequest(request(SENTINELS.recent, true), context);
    assert((await stream.text()).endsWith("data: [DONE]\n\n"));
    assert.equal(captures.length, 2);
    assert(!JSON.stringify(captures).includes(SENTINELS.brief));
});
test("Responses input and buffered/SSE output stay synthetic without a chat fallback", async () => {
    const captures = [];
    const handler = syntheticHandler(NeverForward, new Set(["own"]), captures);
    const context = { sessionId: "own", signal: new AbortController().signal };
    const input = stream => new Request("https://synthetic.invalid/responses", {
        method: "POST", body: JSON.stringify({ model: "synthetic", stream,
            input: [{ role: "user", content: [{ type: "input_text", text: SENTINELS.brief }] }] }),
    });
    const buffered = await handler.sendRequest(input(false), context);
    const result = await buffered.json();
    assert.equal(result.object, "response");
    assert.equal(result.output[0].content[0].text, "SYNTHETIC_TRANSPORT_ONLY");
    const stream = await handler.sendRequest(input(true), context);
    const text = await stream.text();
    assert(text.includes("event: response.output_text.delta\n") && text.includes("event: response.completed\n"));
    assert(text.indexOf("response.created") < text.indexOf("response.completed"));
    assert.equal(captures[0].summary.requestFormat, "responses");
    assert(captures[0].summary.sentinels.brief);
    assert(!JSON.stringify(captures).includes(SENTINELS.brief));
});
test("unowned/unsupported/budget-exhausted requests cannot forward or capture", async () => {
    const captures = [];
    const handler = syntheticHandler(NeverForward, new Set(["own"]), captures);
    await assert.rejects(handler.sendRequest(request("foreign"), { sessionId: "foreign" }), /unowned/);
    await assert.rejects(handler.openWebSocket(), /does not forward/);
    captures.length = 16;
    await assert.rejects(handler.sendRequest(request("own"), { sessionId: "own" }), /allowance/);
    assert.equal(captures.length, 16);
});
test("held synthetic request observes abort separately from dispatch acknowledgement", async () => {
    const abort = new AbortController();
    let held;
    const ready = new Promise(resolve => { held = resolve; });
    const handler = syntheticHandler(NeverForward, new Set(["own"]), [], held);
    let completed = false;
    const response = handler.sendRequest(request(SENTINELS.cancel), { sessionId: "own", signal: abort.signal })
        .then(value => { completed = true; return value; });
    await ready;
    assert(!completed);
    abort.abort();
    assert.equal((await response).status, 200);
    assert(completed);
});
