import test from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, mkdir, writeFile, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { freezeCatalog, collectHistory, bytes, spikeInputs, summarizeRequest, SENTINELS,
    modelSummary, modelCatalogSummary, runtimeEnvironment, observeOwnTask, deadline,
    syntheticHandler, safeError, spikeStatus, armStatus, catalogMatches,
    historyMatches, catalogSummary } from "../scripts/skill-selection-spike.mjs";

const event = (id, type, content, extra = {}) => ({ id, type, data: { content }, ...extra });
test("runtime selection is explicit, absolute and never a silent bundled fallback", () => {
    const sdk = process.cwd();
    const root = process.cwd();
    const runtime = process.execPath;
    const base = { sdkDirectory: sdk, root, modelsOnly: false, catalogOnly: false, skillDirectories: [], profile: {} };
    assert.deepEqual(spikeInputs([sdk, root]), { ...base, runtimePath: undefined });
    assert.deepEqual(spikeInputs([sdk, root, runtime]), { ...base, runtimePath: runtime });
    assert.deepEqual(spikeInputs([sdk, root, runtime, "--list-models"]),
        { ...base, runtimePath: runtime, modelsOnly: true });
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
test("catalog discovery admits only explicit roots without model inference modes", () => {
    const sdk = process.cwd(), root = process.cwd(), other = join(root, "other");
    const result = spikeInputs([sdk, root, "--catalog-only", "--skill-directory", root, "--skill-directory", other]);
    assert(result.catalogOnly && !result.modelsOnly);
    assert.deepEqual(result.skillDirectories, [root, other]);
    for (const options of [
        ["--catalog-only"], ["--skill-directory", root],
        ["--catalog-only", "--list-models", "--skill-directory", root],
        ["--catalog-only", "--model", "synthetic", "--skill-directory", root],
        ["--catalog-only", "--skill-directory", "relative"],
        ["--catalog-only", "--skill-directory", root, "--skill-directory", root],
    ]) assert.throws(() => spikeInputs([sdk, root, ...options]));
});
test("catalog summaries disclose counts, not private descriptors, and bind the full catalog", () => {
    const catalog = freezeCatalog([{ name: "private-id", description: "private description",
        enabled: true, source: "plugin", pluginName: "private-plugin", path: "private-path" },
    { name: "other-id", description: "other description", enabled: false, source: "provider" }]);
    const summary = catalogSummary(catalog);
    assert.equal(summary.count, 2);
    assert.equal(summary.disabled, 1);
    assert.equal(summary.pathless, 1);
    assert.equal(summary.pluginQualified, 1);
    assert.deepEqual(summary.sources, { plugin: 1, provider: 1 });
    assert(!JSON.stringify(summary).includes("private"));
    assert.notEqual(summary.digest, catalogSummary([{ ...catalog[0], description: "changed" }, catalog[1]]).digest);
    const unknownSource = catalogSummary([{ ...catalog[0], source: "private-source" }]);
    assert.deepEqual(unknownSource.sources, { other: 1 });
    assert(!JSON.stringify(unknownSource).includes("private"));
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
test("bounded history stops at an oversized message rather than resurrecting stale intent", async () => {
    const events = [event("1", "user.message", "old fits"),
        event("2", "user.message", "oversized".repeat(100)), event("3", "assistant.message", "latest fits")];
    const log = { read: async () => ({ events, cursor: "end", cursorStatus: "ok", hasMore: false }) };
    const result = await collectHistory(log, { maxBytes: 90 });
    assert.deepEqual(result.messages.map(message => message.content), ["latest fits"]);
    assert.equal(result.omitted, 2);
    assert(result.truncated);
    events.pop();
    const newestOversized = await collectHistory(log, { maxBytes: 90 });
    assert.deepEqual(newestOversized.messages, []);
    assert.equal(newestOversized.omitted, 2);
    assert(newestOversized.truncated);
});
test("structured errors redact arbitrary absolute paths, mounts, spaces, URIs and accounts", () => {
    for (const path of ["/mnt/team data/session.json", "/opt/private/session.json",
        "Z:\\team data\\session.json", "\\\\server\\share name\\session.json", "file:///Volumes/team data/session.json",
        "/mnt/owner's confidential-fixture", "Z:\\owner's confidential-fixture"]) {
        const result = safeError(new Error(`acquisition failed at "${path}" for synthetic@example.invalid`));
        assert.equal(result, 'acquisition failed at "<local-path>');
    }
});
test("arm completion requires attributed capture, normal result and expected fork history", () => {
    const result = { settled: true, terminal: "subagent.completed", resultObserved: true,
        requests: [{ attributedSession: true, attributedAgent: true }] };
    assert.equal(armStatus("nativeTask", result), "completed");
    assert.equal(armStatus("nativeTask", { ...result, cancelled: true }), "failed");
    assert.equal(armStatus("nativeTask", { ...result, resultObserved: false }), "incomplete");
    assert.equal(armStatus("fullFork", { ...result, requests: [] }), "incomplete");
    assert.equal(armStatus("nativeTask", { ...result, requests: [{ attributedSession: true }] }), "incomplete");
    assert.equal(armStatus("boundaryFork", { ...result, inheritedPrefixIdsMatch: false }), "failed");
    assert.equal(armStatus("cancellation", { ...result, cancelled: true, resultObserved: false }), "completed");
});
test("catalog completeness includes full descriptions and explicit fixture state", () => {
    const expected = [{ name: "review", description: "full review", userInvocable: false },
        { name: "disabled", description: "full disabled" }];
    const catalog = [{ ...expected[0], enabled: true }, { ...expected[1], enabled: false }];
    assert(catalogMatches(catalog, expected, ["disabled"]));
    for (const [index, change] of [[0, { description: "truncated" }],
        [0, { userInvocable: undefined }], [1, { enabled: true }]]) {
        const changed = structuredClone(catalog);
        Object.assign(changed[index], change);
        assert(!catalogMatches(changed, expected, ["disabled"]));
    }
});
test("frozen history checks include boundary events and reject incomplete reads", () => {
    const ids = ["old", "recent", "boundary", "boundary-response"];
    const page = { events: ids.map(id => ({ id })), cursorStatus: "ok", hasMore: false };
    assert(historyMatches(page, ids));
    assert(!historyMatches({ ...page, events: [] }, ids));
    assert(!historyMatches({ ...page, hasMore: true }, ids));
    page.events.at(-1).id = "changed-boundary-response";
    assert(!historyMatches(page, ids, true));
    page.events = [...ids, "appended"].map(id => ({ id }));
    assert(!historyMatches(page, ids));
    assert(historyMatches(page, ids, true));
});
test("aggregate status cannot conceal failed, unfinished or unsettled arms and cleanup", () => {
    const report = { mode: "synthetic-transport", arms: { one: { status: "completed" } },
        native: { catalog: { status: "available" } },
        cleanup: { runtimeStopped: true, taskCancellationErrors: [], abortErrors: [], runtimeStopErrors: [] } };
    assert.equal(spikeStatus(report), "completed-with-limitations");
    report.arms.one = { status: "unavailable" };
    assert.equal(spikeStatus(report), "failed");
    report.arms.one = { status: "completed", settled: false };
    assert.equal(spikeStatus(report), "incomplete");
    report.arms.one = { status: "not-reached" };
    assert.equal(spikeStatus(report), "incomplete");
    report.arms.one = { status: "completed" };
    report.cleanup.abortErrors.push("synthetic abort failure");
    assert.equal(spikeStatus(report), "failed");
    const discovery = { mode: "catalog-discovery", status: "catalog-only",
        native: { interceptedRequests: 0 }, forwardedModelRequests: 0, cleanup: { runtimeStopped: true } };
    assert.equal(spikeStatus(discovery), "catalog-only");
    assert.equal(spikeStatus({ ...discovery, native: { interceptedRequests: 1 } }), "failed");
    assert.equal(spikeStatus({ ...discovery, forwardedModelRequests: undefined }), "failed");
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
test("model preflight preserves non-Luna capability metadata", () => {
    const result = modelCatalogSummary([{ id: "synthetic-other", capabilities: {
        limits: { max_prompt_tokens: 400 }, supports: { reasoningEffort: true } },
    supportedReasoningEfforts: ["medium"] }]);
    assert.equal(result.models[0].id, "synthetic-other");
    assert.equal(result.models[0].promptLimit, 400);
    assert.deepEqual(result.models[0].efforts, ["medium"]);
});
test("runtime environment refuses GitHub credential aliases case-insensitively", () => {
    const aliases = ["GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_TOKEN",
        "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "github_api_token", "COPILOT_GITHUB_TOKEN"];
    const environment = Object.fromEntries(aliases.map(name => [name, "synthetic-value"]));
    assert.deepEqual(runtimeEnvironment({ ...environment, PATH: "synthetic-bin", absent: undefined }),
        { PATH: "synthetic-bin" });
});
test("Responses top-level instructions participate in ordered sentinel evidence", () => {
    const summary = summarizeRequest({ instructions: SENTINELS.old, input: [
        { role: "user", content: [{ type: "input_text", text: SENTINELS.recent }] }] });
    assert(summary.sentinels.old && summary.oldBeforeRecent);
    assert.equal(summary.instructionBytes, bytes(SENTINELS.old));
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
test("first-dispatched model cannot stand in for the completion model", async () => {
    let listener;
    const observation = observeOwnTask({ on: callback => { listener = callback; return () => {}; } });
    listener({ type: "subagent.completed", agentId: "own", data: { firstDispatchedModel: "first" } });
    const result = await observation.settled("own");
    assert.equal(result.actualModel, null);
    assert.equal(result.firstDispatchedModel, "first");
    observation.unsubscribe();
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
test("capture allowance reserves concurrent body reads and releases failed parsing", async () => {
    const captures = Array.from({ length: 15 }, () => ({}));
    const handler = syntheticHandler(NeverForward, new Set(["own"]), captures);
    const context = { sessionId: "own", signal: new AbortController().signal };
    let resolveBody;
    const pending = handler.sendRequest({ json: () => new Promise(resolve => { resolveBody = resolve; }) }, context);
    await assert.rejects(handler.sendRequest(request("over-limit"), context), /allowance/);
    resolveBody({ model: "synthetic", messages: [{ role: "user", content: "owned" }] });
    await pending;
    assert.equal(captures.length, 16);
    captures.pop();
    await assert.rejects(handler.sendRequest({ json: async () => { throw new Error("synthetic parse failure"); } }, context), /parse failure/);
    await handler.sendRequest(request("last-owned"), context);
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
test("expired cancellation hold fails without a normal synthetic completion", async () => {
    const handler = syntheticHandler(NeverForward, new Set(["own"]), [], () => {}, 5);
    await assert.rejects(handler.sendRequest(request(SENTINELS.cancel),
        { sessionId: "own", signal: new AbortController().signal }), /hold expired/);
});

const fakeSdk = `
    import { writeFile } from "node:fs/promises";
    import { basename, dirname, join } from "node:path";
    export const RuntimeConnection = { forStdio: () => ({}) };
    export class CopilotRequestHandler {}
    export class CopilotClient {
        constructor(options) {
            this.options = options;
            this.scenario = basename(dirname(options.baseDirectory));
            this.sessions = new Map(); this.tasks = new Map(); this.sequence = 0;
            this.trace = { created: [], forks: [], resumed: [], dispatched: [], cancelled: [], aborted: [], prompts: [] };
        }
        async start() {}
        rpc = { sessions: { fork: async ({ sessionId, toEventId }) => {
            const parent = this.sessions.get(sessionId);
            const end = toEventId ? parent.events.findIndex(event => event.id === toEventId) : parent.events.length;
            const id = "fork-" + ++this.sequence;
            const events = parent.events.slice(0, end);
            if (this.scenario === "history-corrupt") {
                if (toEventId) events.push(parent.events[end]);
                else events.pop();
            }
            this.sessions.set(id, { events });
            this.trace.forks.push({ id, toEventId: toEventId ?? null });
            return { sessionId: id };
        } } };
        makeSession(id, config, events = []) {
            const listeners = new Set();
            let modelReads = 0;
            const emit = event => { for (const listener of listeners) listener(event); };
            const session = { sessionId: id, capabilities: { tasks: true }, events,
                on: listener => { listeners.add(listener); return () => listeners.delete(listener); },
                abort: async () => { this.trace.aborted.push(id); },
                rpc: {
                    skills: { list: async () => ({ skills: config.skillProvider
                        ? config.skillProvider.listSkills().map(skill =>
                            ({ ...skill, enabled: !config.disabledSkills.includes(skill.name), source: "provider" }))
                        : this.scenario === "catalog-empty" ? [] : [{
                            name: "private-id", description: "private description", enabled: true, source: "project",
                        }] }) },
                    agent: { list: async () => ({ agents: [{ id: "synthetic-selector" }] }) },
                    model: { list: async () => ({ list: [] }), getCurrent: async () => {
                        if (this.scenario === "history-corrupt" && ++modelReads === 5) events.at(-1).id = "changed-boundary-response";
                        return { model: config.model ?? "synthetic" };
                    } },
                    eventLog: { read: async () => ({ events: structuredClone(events),
                        cursor: "end", cursorStatus: "ok", hasMore: false }) },
                    tasks: {
                        startAgent: async ({ prompt }) => {
                            const agentId = "task-" + ++this.sequence, abort = new AbortController();
                            this.tasks.set(agentId, { abort, emit });
                            this.trace.dispatched.push({ id: agentId, parent: id, prompt });
                            if (this.scenario === "unsettled" && !prompt.includes("SPIKE_CANCEL")) {
                                const now = Date.now, timer = globalThis.setTimeout;
                                let offset = 0;
                                Date.now = () => now() + offset;
                                globalThis.setTimeout = (callback, delay, ...args) => timer(() => {
                                    offset += delay === 25 ? 9000 : 0; callback(...args);
                                }, delay);
                            } else {
                                emit({ type: "subagent.configured", agentId, data: { model: config.model ?? "synthetic" } });
                                this.respond(id, prompt, abort.signal, agentId).then(() => {
                                    if (!abort.signal.aborted) {
                                        if (!["task-cancelled", "task-no-result"].includes(this.scenario)) {
                                            emit({ type: "assistant.message", agentId, data: { content: "synthetic result" } });
                                        }
                                        emit({ type: "subagent.completed", agentId, data: {
                                            model: config.model ?? "synthetic", cancelled: this.scenario === "task-cancelled" } });
                                    }
                                });
                            }
                            return { agentId };
                        },
                        cancel: async ({ id: taskId }) => {
                            const task = this.tasks.get(taskId);
                            if (!task) throw new Error("foreign task cancellation");
                            this.trace.cancelled.push(taskId);
                            task.abort.abort();
                            task.emit({ type: "subagent.completed", agentId: taskId,
                                data: { cancelled: true, model: config.model ?? "synthetic" } });
                            return { success: true };
                        },
                    },
                },
                sendAndWait: async ({ prompt }) => {
                    this.trace.prompts.push({ id, prompt });
                    await this.respond(id, prompt, new AbortController().signal);
                    events.push({ id: "event-" + ++this.sequence, type: "user.message", data: { content: prompt } },
                        { id: "event-" + ++this.sequence, type: "assistant.message", data: { content: "synthetic reply" } });
                },
            };
            if (config.skillProvider) config.skillProvider.listSkills();
            else this.trace.catalogConfig = config;
            this.config = config; this.sessions.set(id, session);
            return session;
        }
        async respond(sessionId, prompt, signal, agentId) {
            if (this.scenario === "no-capture" && sessionId.startsWith("fork-")) {
                return { synthetic: true };
            }
            const history = agentId ? [] : (this.sessions.get(sessionId)?.events ?? []).map(event =>
                ({ role: event.type === "user.message" ? "user" : "assistant", content: event.data.content }));
            const response = await this.options.requestHandler.sendRequest(new Request("https://synthetic.invalid/model", {
                method: "POST", body: JSON.stringify({ model: "synthetic",
                    messages: [...history, { role: "user", content: prompt }] }),
            }), { sessionId, agentId, signal });
            return response.json();
        }
        async createSession(config) {
            const id = "session-" + ++this.sequence;
            this.trace.created.push(id);
            return this.makeSession(id, config);
        }
        async resumeSession(id, config) {
            if (this.scenario === "acquisition-failure") throw new Error("synthetic resume acquisition failed");
            this.trace.resumed.push(id);
            return this.makeSession(id, config, this.sessions.get(id).events);
        }
        async stop() {
            this.config?.skillProvider?.listSkills();
            await writeFile(join(this.options.baseDirectory, "fake-witness.json"), JSON.stringify(this.trace));
            return [];
        }
    }
`;
test("model-free SDK orchestration covers all arms, owned cleanup and acquisition failures", async t => {
    const root = await mkdtemp(join(tmpdir(), "skill-spike-orchestration-"));
    t.after(() => rm(root, { recursive: true }));
    const sdk = join(root, "fake-sdk");
    await mkdir(sdk);
    await writeFile(join(sdk, "package.json"), '{"type":"module"}');
    await writeFile(join(sdk, "index.js"), fakeSdk);
    const runner = fileURLToPath(new URL("../scripts/run-skill-selection-spike.mjs", import.meta.url));
    const run = async scenario => {
        const fixture = join(root, scenario);
        const { stdout } = await promisify(execFile)(process.execPath, [runner, sdk, fixture],
            { windowsHide: true, timeout: 3000 });
        return { report: JSON.parse(stdout),
            trace: JSON.parse(await readFile(join(fixture, "owned-home", "fake-witness.json"), "utf8")) };
    };
    const { report, trace } = await run("success");
    assert.equal(report.status, "completed-with-limitations");
    assert(Object.values(report.arms).every(arm => arm.status === "completed"));
    assert.equal(trace.forks.length, 2);
    assert.equal(trace.forks[0].toEventId, null);
    assert(trace.forks[1].toEventId);
    assert(report.arms.boundaryFork.exclusivePrefixIdsMatch);
    assert(report.arms.fullFork.inheritedPrefixIdsMatch);
    assert(report.arms.fullFork.requests[0].sentinels.boundary);
    assert(!report.arms.boundaryFork.requests[0].sentinels.boundary);
    assert.equal(trace.dispatched.length, 2);
    assert.equal(trace.dispatched[1].prompt, SENTINELS.cancel);
    const bounded = trace.prompts.find(item => item.id === trace.created[1]);
    assert(bounded.prompt.includes('"omitted":') && bounded.prompt.includes('"truncated":true'));
    assert(!trace.prompts.find(item => item.id === trace.created[2]).prompt.includes(SENTINELS.old));
    assert(trace.dispatched.every(item => !item.prompt.includes(SENTINELS.old)));
    assert.deepEqual(new Set(trace.aborted), new Set([...trace.created, ...trace.resumed]));
    assert(trace.cancelled.every(id => trace.dispatched.some(task => task.id === id)));
    assert(trace.dispatched.every(task => trace.cancelled.includes(task.id)));
    assert.equal(report.native.catalog.providerCalls.list, trace.created.length + trace.resumed.length + 2);
    assert.equal(report.native.catalog.providerCalls.read, 0);
    assert(report.cleanup.runtimeStopped && report.arms.cancellation.settled && report.arms.cancellation.cancelled);
    const acquisition = await run("acquisition-failure");
    assert.equal(acquisition.report.status, "failed");
    assert.equal(acquisition.report.arms.fullFork.status, "unavailable");
    assert.equal(acquisition.report.arms.boundaryFork.status, "unavailable");
    assert.equal(acquisition.trace.resumed.length, 0);
    assert.deepEqual(new Set(acquisition.trace.aborted), new Set(acquisition.trace.created));
    assert(acquisition.report.cleanup.runtimeStopped);
    const unsettled = await run("unsettled");
    assert.equal(unsettled.report.status, "incomplete");
    assert.equal(unsettled.report.arms.nativeTask.settled, false);
    assert.equal(unsettled.report.arms.cancellation.status, "not-reached");
    assert.equal(unsettled.trace.dispatched.length, 1);
    assert.deepEqual(unsettled.trace.cancelled, [unsettled.trace.dispatched[0].id]);
    assert(unsettled.report.cleanup.runtimeStopped);
    for (const [scenario, status] of [["task-cancelled", "failed"], ["task-no-result", "incomplete"],
        ["no-capture", "incomplete"], ["history-corrupt", "failed"]]) {
        const invalid = await run(scenario);
        assert.equal(invalid.report.status, status);
        assert(invalid.report.cleanup.runtimeStopped);
        if (scenario === "no-capture") assert.equal(invalid.report.arms.fullFork.status, "incomplete");
        if (scenario === "history-corrupt") {
            assert.equal(invalid.report.arms.fullFork.status, "failed");
            assert.equal(invalid.report.arms.boundaryFork.status, "failed");
            assert.equal(invalid.report.native.parentTextPrefixUnchanged, false);
        }
    }
});

test("catalog-only orchestration cannot dispatch, load other discovery or omit owned cleanup", async t => {
    const root = await mkdtemp(join(tmpdir(), "skill-spike-catalog-"));
    t.after(() => rm(root, { recursive: true }));
    const sdk = join(root, "fake-sdk");
    await mkdir(sdk);
    await writeFile(join(sdk, "package.json"), '{"type":"module"}');
    await writeFile(join(sdk, "index.js"), fakeSdk);
    const runner = fileURLToPath(new URL("../scripts/run-skill-selection-spike.mjs", import.meta.url));
    for (const scenario of ["catalog", "catalog-empty"]) {
        const fixture = join(root, scenario);
        const { stdout } = await promisify(execFile)(process.execPath,
            [runner, sdk, fixture, "--catalog-only", "--skill-directory", sdk],
            { windowsHide: true, timeout: 3000 });
        const report = JSON.parse(stdout);
        const trace = JSON.parse(await readFile(join(fixture, "owned-home", "fake-witness.json"), "utf8"));
        assert.equal(report.status, scenario === "catalog" ? "catalog-only" : "incomplete");
        assert.equal(report.native.catalog.foregroundScopeEquivalent, null);
        assert.equal(report.native.interceptedRequests, 0);
        assert.equal(report.forwardedModelRequests, 0);
        assert.deepEqual(report.native.catalog.providerCalls, { list: 0, read: 0 });
        assert(!stdout.includes("private"));
        assert.equal(trace.created.length, 1);
        assert.deepEqual(trace.aborted, trace.created);
        for (const field of ["dispatched", "prompts", "forks", "resumed", "cancelled"]) {
            assert.deepEqual(trace[field], []);
        }
        assert.deepEqual(trace.catalogConfig.skillDirectories, [sdk]);
        assert.equal(trace.catalogConfig.enableConfigDiscovery, false);
        assert.equal(trace.catalogConfig.enableFileHooks, false);
        assert.equal(trace.catalogConfig.requestExtensions, false);
        assert.deepEqual(trace.catalogConfig.mcpServers, {});
        for (const field of ["availableTools", "tools", "customAgents"]) {
            assert.deepEqual(trace.catalogConfig[field], []);
        }
        assert(report.cleanup.runtimeStopped);
    }
    const { stdout } = await promisify(execFile)(process.execPath,
        [runner, sdk, join(root, "missing-root"), "--catalog-only", "--skill-directory", join(root, "absent")],
        { windowsHide: true, timeout: 3000 });
    const invalid = JSON.parse(stdout);
    assert.equal(invalid.status, "failed");
    assert(!invalid.cleanup.clientCreated);
    assert(!stdout.includes(root));
});

test("startup failures stay structured and late owned startup is awaited before cleanup", async t => {
    const root = await mkdtemp(join(tmpdir(), "skill-spike-controls-"));
    t.after(() => rm(root, { recursive: true }));
    const runner = fileURLToPath(new URL("../scripts/run-skill-selection-spike.mjs", import.meta.url));
    const run = async (sdk, fixture, modelsOnly = true) => {
        const { stdout } = await promisify(execFile)(process.execPath,
            [runner, sdk, join(root, fixture), ...(modelsOnly ? ["--list-models"] : [])],
            { windowsHide: true, timeout: 3000 });
        return JSON.parse(stdout);
    };
    const invalid = await promisify(execFile)(process.execPath, [runner, "relative-sdk", "relative-fixture"],
        { windowsHide: true, timeout: 3000 });
    assert.equal(JSON.parse(invalid.stdout).status, "failed");
    assert.equal(invalid.stderr, "");
    assert(!invalid.stdout.includes(runner));
    const missing = await run(join(root, "missing-sdk"), "missing-fixture");
    assert.equal(missing.native.startup.status, "unavailable");
    assert.equal(missing.cleanup.clientCreated, false);
    assert.equal(missing.cleanup.runtimeStopped, null);
    assert(!JSON.stringify(missing).includes(root));
    const sdk = join(root, "fake-sdk");
    await mkdir(sdk);
    await writeFile(join(sdk, "package.json"), '{"type":"module"}');
    await writeFile(join(sdk, "index.js"), `
        export const RuntimeConnection = { forStdio: () => ({}) };
        export class CopilotClient { constructor() { throw new Error("synthetic constructor failure"); } }
    `);
    const construction = await run(sdk, "constructor-fixture");
    assert.equal(construction.native.startup.status, "unavailable");
    assert.equal(construction.error, "synthetic constructor failure");
    assert.equal(construction.cleanup.clientCreated, false);
    await writeFile(join(sdk, "index.js"), `
        const timer = globalThis.setTimeout;
        globalThis.setTimeout = (callback, delay, ...args) => timer(callback, delay >= 15000 ? 5 : delay, ...args);
        export const RuntimeConnection = { forStdio: () => ({}) };
        export class CopilotClient {
            started = false;
            async start() { await new Promise(resolve => timer(resolve, 25)); this.started = true; }
            async listModels() { if (!this.started) throw new Error("startup not settled"); return []; }
            async stop() { return this.started ? [] : [new Error("cleanup preceded late startup")]; }
        }
    `);
    const late = await run(sdk, "late-fixture");
    assert.equal(late.status, "preflight-only");
    assert.equal(late.cleanup.runtimeStopped, true);
    assert.deepEqual(late.cleanup.runtimeStopErrors, []);
    await writeFile(join(sdk, "index.js"), `
        export const RuntimeConnection = { forStdio: () => ({}) };
        export class CopilotRequestHandler {}
        export class CopilotClient {
            async start() {}
            async createSession() {
                return { sessionId: "owned", capabilities: {},
                    rpc: { skills: { list: async () => ({ skills: [
                        { name: "synthetic-review", description: "review", enabled: true, source: "provider" },
                        { name: "synthetic-disabled", description: "disabled", enabled: false, source: "provider" },
                        { name: "synthetic-long", description: "long", enabled: true, source: "provider" }
                    ] }) }, agent: { list: async () => ({ agents: [] }) },
                    model: { list: async () => ({ list: [] }), getCurrent: async () => ({}) } },
                    async sendAndWait() { throw new Error("synthetic seeding stop"); }, async abort() {} };
            }
            async stop() { return []; }
        }
    `);
    const missingPermission = await run(sdk, "permission-fixture", false);
    assert.equal(missingPermission.native.catalog.nonUserInvocableEnabled, false);
});
