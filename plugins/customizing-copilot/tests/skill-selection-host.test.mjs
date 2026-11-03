import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { selectionAgent, selectionSnapshot, selectionReadParameters } from "../scripts/skill-selection-contract.mjs";
import { runOwnedSelection, selectionRequestHandler, selectionUsage, readContentDelivered,
    recordReadDelivery } from "../scripts/skill-selection-host.mjs";
import { validateEvaluation, runSelectionEvaluation } from "../scripts/skill-selection-evaluation.mjs";

const profile = { model: "fixture-candidate", reasoningEffort: "medium", contextTier: "long_context" };
const snapshot = selectionSnapshot([
    { name: "fixture-skill", commandName: "owner:fixture-skill", pluginName: "owner",
        description: "Fixture guidance.", enabled: true, source: "provider" },
]);
const result = { version: 1, catalogDigest: snapshot.digest, status: "no-match", recommendations: [], uncertainties: [] };
const request = { intent: "Compute seven times six.", purpose: "execution",
    context: { messages: [], truncated: false, omitted: 0 } };
const body = (extra = {}) => ({ model: profile.model, reasoning: { effort: profile.reasoningEffort },
    instructions: selectionAgent(profile).prompt, input: [],
    tools: [{ type: "function", name: "skill_selection_read", parameters: selectionReadParameters(snapshot) }], ...extra });
const event = (extra = {}) => ({ type: "assistant.usage", id: "event-one", data: {
    model: profile.model, reasoningEffort: profile.reasoningEffort, inputTokens: 50, outputTokens: 20,
    accounting: { sourceSessionId: "owned-session", usageId: "usage-one", sequence: 1 }, ...extra,
} });

test("source delivery must be actual tool content, not a read receipt, user quote or spill pointer", () => {
    const content = '# Guide\nA quoted "value" and a backslash: \\.\n'.repeat(800);
    assert.equal(readContentDelivered({ input: [{ type: "function_call_output",
        output: JSON.stringify({ status: "available", content }) }] }, content), true);
    assert.equal(readContentDelivered({ messages: [{ role: "tool", content }] }, content), true);
    assert.equal(readContentDelivered({ input: [{ type: "function_call_output",
        output: `<tool_result>${JSON.stringify({ content })}</tool_result>` }] }, content), true);
    assert.equal(readContentDelivered({ input: [{ role: "user", content }] }, content), false);
    assert.equal(readContentDelivered({ input: [{ type: "function_call_output",
        output: "Output saved to a file; use a filesystem tool to read it." }] }, content), false);
    assert.equal(readContentDelivered({ input: [{ type: "function_call_output",
        output: JSON.stringify({ contentDigest: "receipt-only" }) }] }, content), false);
});
test("rejected partial source delivery cannot accumulate false coverage across retries", () => {
    const outputs = new Map([["a", "Complete source A"], ["b", "Complete source B"]]), delivered = new Set();
    const input = values => ({ input: values.map(content => ({ type: "function_call_output", output: content })) });
    for (const content of outputs.values()) {
        assert.throws(() => recordReadDelivery(input([content]), outputs, delivered), /not delivered/);
        assert.equal(delivered.size, 0);
    }
    recordReadDelivery(input([...outputs.values()]), outputs, delivered);
    assert.deepEqual([...delivered], ["a", "b"]);
});
test("usage deduplicates replay without inventing a price and rejects contradictory accounting", () => {
    const usage = selectionUsage();
    usage.add(event()); usage.add({ ...event(), id: "replayed-event" });
    assert.equal(usage.summary().uniqueCalls, 1);
    assert.equal(usage.summary().cost.value, null);
    assert.throws(() => usage.add(event({ outputTokens: 21 })), /contradictory/);
    const billed = selectionUsage();
    billed.add(event({ copilotUsage: { totalNanoAiu: 100 } }));
    billed.add({ ...event({ copilotUsage: { totalNanoAiu: 200 },
        accounting: { sourceSessionId: "owned-session", usageId: "usage-two", sequence: 2 } }), id: "event-two" });
    assert.deepEqual(billed.summary().cost, { unit: "nano-AIU", value: 300 });
});

test("independent evaluation must cover each retained report once with bounded explicit grades", () => {
    const row = { id: "case-arm", requiredRecall: 1, precision: 1, abstentionCorrect: true,
        guideProvenanceCorrect: true, sourceCoverage: "complete", forbiddenFailures: [], notes: "Grounded." };
    const value = { summary: "Small sample; total price remains unavailable.", cases: [row] };
    assert.deepEqual(validateEvaluation(value, ["case-arm"]), value);
    for (const invalid of [{ ...value, cases: [] }, { ...value, cases: [row, row] },
        { ...value, cases: [{ ...row, id: "invented" }] },
        { ...value, cases: [{ ...row, requiredRecall: 2 }] },
        { ...value, cases: [{ ...row, forbiddenFailures: [""] }] }]) {
        assert.throws(() => validateEvaluation(invalid, ["case-arm"]), /invalid/);
    }
});

test("owned request guard checks effective profile and tools without consuming the forwarded body", async () => {
    const forwarded = [], requests = [];
    class Base {
        async sendRequest(value) { forwarded.push(await value.json()); return Response.json({}); }
    }
    const context = { sessionId: "owned-session", signal: new AbortController().signal };
    const handler = selectionRequestHandler(Base, {
        owned: new Set(["owned-session"]), profile, requests, maxRequests: 8,
        readParameters: selectionReadParameters(snapshot),
    });
    const make = value => new Request("https://example.invalid/responses", { method: "POST", body: JSON.stringify(value) });
    await handler.sendRequest(make(body()), context);
    assert.deepEqual(forwarded, [body()]);
    assert.equal(requests[0].forwarded, true);
    assert.equal(requests[0].assembledBytes > 0, true);
    for (const invalid of [{ model: "wrong" }, { reasoning: { effort: "high" } },
        { tools: [{ type: "function", name: "shell" }] }, { instructions: "not canonical" },
        { tools: [{ type: "function", name: "skill_selection_read", parameters: {
            ...selectionReadParameters(snapshot), properties: { skill: { type: "string", enum: ["owner:fixture-skill"] } },
        } }] }]) {
        await assert.rejects(handler.sendRequest(make(body(invalid)), context), /differs/);
    }
    await assert.rejects(handler.sendRequest(make(body()), { ...context, sessionId: "foreign" }), /unowned/);
    await assert.rejects(handler.openWebSocket(context), /HTTP/);
    assert.equal(forwarded.length, 1);
    const denied = selectionRequestHandler(Base, { owned: new Set(["owned-session"]), profile, requests: [], live: false });
    await assert.rejects(denied.sendRequest(make(body()), context), /live dispatch is disabled/);
    assert.equal(forwarded.length, 1);
});

function fakeSdk({ configMismatch = false, usageMismatch = false, malformed = false,
    stopError = false, hang = false, signal, toolFailure = false, forkMismatch = false,
    acquisition, stopGate, bodyInspection, bypassHandler = false, requestSignal, lateUsage, waitFailure = false,
    requestProbe } = {}) {
    const observed = { stops: 0, aborts: 0, sends: 0, forwards: 0 };
    const histories = new Map(), subscriptions = new Set();
    let serial = 0;
    class Base { async sendRequest(value) { observed.forwards++; await value.json(); return Response.json({}); } }
    class Client {
        constructor(options) {
            this.options = options;
            this.rpc = { sessions: { fork: async ({ sessionId, toEventId }) => {
                const history = histories.get(sessionId);
                const prefix = history.slice(0, history.findIndex(event => event.id === toEventId));
                const id = `fork-${++serial}`;
                histories.set(id, forkMismatch ? [] : structuredClone(prefix));
                return { sessionId: id };
            } } };
        }
        async start() { await acquisition?.("start"); }
        async resumeSession(id, config) { return this.createSession(config, id); }
        async createSession(config, id = `owned-${++serial}`) {
            await acquisition?.("create");
            observed.config = config;
            const handlers = new Set();
            if (!histories.has(id)) histories.set(id, []);
            const history = histories.get(id);
            return {
                sessionId: id,
                rpc: { model: { getCurrent: async () => ({
                    modelId: configMismatch ? "fallback" : profile.model,
                    reasoningEffort: profile.reasoningEffort, contextTier: profile.contextTier,
                }) }, agent: { getCurrent: async () => ({ agent: { name: config.agent } }) },
                tools: { execute: async ({ arguments: args }) => config.tools[0].handler(args, { sessionId: id }) },
                eventLog: { read: async () => ({ events: history, cursor: "tail", cursorStatus: "ok", hasMore: false }) } },
                on(callback) {
                    handlers.add(callback); subscriptions.add(callback);
                    return () => { handlers.delete(callback); subscriptions.delete(callback); };
                },
                async sendAndWait({ prompt }) {
                    observed.sends++;
                    if (hang) {
                        signal.abort();
                        return new Promise(() => {});
                    }
                    if (!bypassHandler) {
                        const outbound = new Request("https://example.invalid/responses", { method: "POST", body: JSON.stringify(body({
                            instructions: config.customAgents[0].prompt,
                            tools: config.tools.map(tool => ({ type: "function", name: tool.name, parameters: tool.parameters })),
                            input: [{ role: "user", content: prompt }],
                        })) });
                        if (bodyInspection) {
                            const clone = outbound.clone.bind(outbound);
                            outbound.clone = () => ({ json: async () => { await bodyInspection(); return clone().json(); } });
                        }
                        const context = { sessionId: id, signal: requestSignal ?? new AbortController().signal };
                        if (requestProbe) await requestProbe(thisClient.options.requestHandler, outbound, context);
                        else {
                            observed.lastRequest = thisClient.options.requestHandler.sendRequest(outbound, context);
                            if (waitFailure) {
                                observed.lastRequest.then(() => {}, error => { observed.requestFailure = error; });
                                throw new Error("fixture wait failed");
                            }
                            await observed.lastRequest;
                        }
                    }
                    history.push({ id: `user-${++serial}`, type: "user.message", data: { content: prompt } },
                        { id: `assistant-${++serial}`, type: "assistant.message", data: { content: "fixture reply" } });
                    if (toolFailure) await config.tools[0].handler({ skill: "fixture-skill" }, {
                        sessionId: "foreign", agentId: undefined,
                    });
                    const used = event(usageMismatch ? { model: "fallback" } : {});
                    handlers.forEach(callback => { callback(used); callback({ ...used, id: "replay" }); });
                    const value = config.agent === "independent-guidance-evaluator"
                        ? { summary: "Synthetic evaluation.", cases: [{
                            id: "fixture", requiredRecall: 1, precision: 1, abstentionCorrect: true,
                            guideProvenanceCorrect: true, sourceCoverage: "complete", forbiddenFailures: [], notes: "Fixture.",
                        }] } : result;
                    return { data: { content: malformed ? "{}" : JSON.stringify(value) } };
                },
                async abort() { observed.aborts++; },
            };
        }
        async stop() {
            observed.stops++;
            await stopGate?.();
            if (lateUsage) subscriptions.forEach(callback => callback(event({
                accounting: { sourceSessionId: "owned-session", usageId: "late-usage", sequence: 2 }, ...lateUsage,
            })));
            return stopError ? [new Error("owned stop failed")] : [];
        }
    }
    let thisClient;
    return { sdk: { CopilotRequestHandler: Base, RuntimeConnection: { forStdio: value => value },
        CopilotClient: class extends Client { constructor(options) { super(options); thisClient = this; } } }, observed };
}

async function fixture(t, changes = {}) {
    const parent = await mkdtemp(join(tmpdir(), "selector-host-test-"));
    t.after(() => rm(parent, { recursive: true, force: true }));
    const fake = fakeSdk(changes);
    const report = await runOwnedSelection({
        ...fake, root: join(parent, "decision"), runtimePath: resolve("fixture-runtime"),
        profile, snapshot, trustedRoots: [parent], request, signal: changes.signal?.signal, timeoutMs: 2000,
        ...(changes.strategy ? { strategy: changes.strategy, scenarioHistory: [
            "Superseded intent and irrelevant padding. ".repeat(400), "Current intent: compute seven times six.",
        ] } : {}),
    });
    return { report, observed: fake.observed };
}

test("owned selector validates its result and records unique usage through graceful stop", async t => {
    const { report, observed } = await fixture(t);
    assert.equal(report.status, "completed");
    assert.deepEqual(report.result, result);
    assert.equal(report.usage.uniqueCalls, 1);
    assert.equal(report.usage.cost.value, null);
    assert.equal(observed.stops, 1);
    assert.equal(observed.aborts, 0);
    assert.deepEqual(observed.config.availableTools, ["skill_selection_read"]);
    assert.equal(observed.config.enableConfigDiscovery, false);
    assert.deepEqual(observed.config.capi, { enableWebSocketResponses: false });
    assert.equal(observed.config.enableSkills, false);
    assert.equal(observed.config.tools[0].skipPermission, true);
    assert.deepEqual(observed.config.tools[0].parameters.properties.skill.enum, ["fixture-skill"]);
});

test("multiple direct read witnesses never masquerade as conversational tool output", async t => {
    const parent = await mkdtemp(join(tmpdir(), "selector-read-witness-"));
    t.after(() => rm(parent, { recursive: true, force: true }));
    const path = join(parent, "SKILL.md");
    await writeFile(path, "[Guide](guide.md)");
    await writeFile(join(parent, "guide.md"), "Necessary declared guidance.");
    const snapshot = selectionSnapshot([
        { name: "fixture-skill", description: "Fixture guidance.", enabled: true, source: "provider", path },
    ]);
    const fake = fakeSdk();
    const report = await runOwnedSelection({
        ...fake, root: join(parent, "decision"), runtimePath: resolve("fixture-runtime"),
        profile, snapshot, trustedRoots: [parent], request, live: false,
        readWitness: [{ skill: "fixture-skill" }, { skill: "fixture-skill", reference: "guide.md" }],
    });
    assert.equal(report.status, "model-free-witness");
    assert.equal(report.nativeReadWitness.reads, 2);
    assert.equal(report.receipts.length, 2);
    assert.deepEqual(report.sourceDelivery, []);
    assert.equal(fake.observed.forwards, 0);
    assert.equal(report.cleanup.runtimeStopped, true);
});

test("configuration mismatch prevents dispatch; malformed, mismatched and unsettled results never succeed", async t => {
    for (const changes of [{ configMismatch: true }, { usageMismatch: true },
        { malformed: true }, { stopError: true }, { toolFailure: true }]) {
        const { report, observed } = await fixture(t, changes);
        assert.equal(report.status, "unavailable");
        assert.equal(report.result, null);
        assert.equal(observed.stops, 1);
        if (changes.configMismatch) assert.equal(observed.sends, 0);
    }
});

test("cancellation rejects late output, aborts only owned work and still stops its runtime", async t => {
    const signal = new AbortController();
    const { report, observed } = await fixture(t, { hang: true, signal });
    assert.equal(report.status, "unavailable");
    assert.match(report.error, /cancelled/);
    assert.equal(report.result, null);
    assert.equal(observed.aborts, 1);
    assert.equal(observed.stops, 1);
});

test("cancelled acquisitions settle before selector or evaluator cleanup can claim a stopped runtime", async t => {
    for (const host of ["selector", "evaluator"]) for (const phase of ["start", "create"]) {
        const parent = await mkdtemp(join(tmpdir(), "selector-acquisition-test-"));
        t.after(() => rm(parent, { recursive: true, force: true }));
        const signal = new AbortController();
        let release, acquired;
        const gate = new Promise(resolve => { release = resolve; });
        const entered = new Promise(resolve => { acquired = resolve; });
        const fake = fakeSdk({ acquisition: async operation => {
            if (operation === phase) { acquired(); await gate; }
        } });
        const options = { ...fake, root: join(parent, "decision"), runtimePath: resolve("fixture-runtime"),
            profile, snapshot, trustedRoots: [parent], request, signal: signal.signal, timeoutMs: 1,
            evidence: { reports: [{ id: "fixture" }] } };
        let settled = false;
        const running = (host === "selector" ? runOwnedSelection : runSelectionEvaluation)(options)
            .then(report => { settled = true; return report; });
        await entered;
        signal.abort();
        await new Promise(resolve => setTimeout(resolve, 10));
        assert.equal(settled, false);
        assert.equal(fake.observed.stops, 0);
        release();
        const report = await running;
        assert.equal(report.status, "unavailable");
        assert.equal(report.cleanup.runtimeStopped, true);
        assert.equal(fake.observed.stops, 1);
        assert.equal(fake.observed.forwards, 0);
    }
});

test("cancellation during successful shutdown invalidates selector output and independent grades", async t => {
    for (const host of ["selector", "evaluator"]) for (const cancel of [false, true]) {
        const parent = await mkdtemp(join(tmpdir(), "selector-settlement-test-"));
        t.after(() => rm(parent, { recursive: true, force: true }));
        const signal = new AbortController();
        let release, reachedStop;
        const gate = new Promise(resolve => { release = resolve; });
        const stopped = new Promise(resolve => { reachedStop = resolve; });
        const fake = fakeSdk({ stopGate: async () => { reachedStop(); await gate; } });
        const options = { ...fake, root: join(parent, "decision"), runtimePath: resolve("fixture-runtime"),
            profile, snapshot, trustedRoots: [parent], request, signal: signal.signal,
            evidence: { reports: [{ id: "fixture" }] } };
        const running = (host === "selector" ? runOwnedSelection : runSelectionEvaluation)(options);
        await stopped;
        if (cancel) signal.abort();
        release();
        const report = await running;
        assert.equal(fake.observed.forwards, 1);
        assert.equal(report.cleanup.runtimeStopped, true);
        assert.equal(report.status, cancel ? "unavailable" : "completed");
        if (cancel) {
            assert.equal(report.result, null);
            assert.match(report.error, /cancelled during settlement/);
        } else assert(report.result);
    }
});

test("both hosts refuse forwarding after caller or SDK cancellation during body inspection", async t => {
    for (const host of ["selector", "evaluator"]) for (const source of ["caller", "sdk"]) {
        const parent = await mkdtemp(join(tmpdir(), "selector-inspection-test-"));
        t.after(() => rm(parent, { recursive: true, force: true }));
        const signal = new AbortController();
        const sdkSignal = new AbortController();
        let release, inspecting;
        const gate = new Promise(resolve => { release = resolve; });
        const entered = new Promise(resolve => { inspecting = resolve; });
        const fake = fakeSdk({ requestSignal: sdkSignal.signal,
            bodyInspection: async () => { inspecting(); await gate; } });
        const running = (host === "selector" ? runOwnedSelection : runSelectionEvaluation)({
            ...fake, root: join(parent, "decision"), runtimePath: resolve("fixture-runtime"),
            profile, snapshot, trustedRoots: [parent], request, signal: signal.signal,
            evidence: { reports: [{ id: "fixture" }] },
        });
        await entered;
        (source === "caller" ? signal : sdkSignal).abort();
        release();
        const report = await running;
        await assert.rejects(fake.observed.lastRequest, /cancelled/);
        assert.equal(fake.observed.forwards, 0);
        assert.deepEqual(report.requests, []);
        assert.equal(report.status, "unavailable");
        assert.equal(report.result, null);
        assert.equal(report.cleanup.runtimeStopped, true);
    }
});

test("valid-looking replies and usage without an inspected request cannot pass either host", async t => {
    for (const host of ["selector", "evaluator"]) {
        const parent = await mkdtemp(join(tmpdir(), "selector-missing-request-"));
        t.after(() => rm(parent, { recursive: true, force: true }));
        const fake = fakeSdk({ bypassHandler: true });
        const report = await (host === "selector" ? runOwnedSelection : runSelectionEvaluation)({
            ...fake, root: join(parent, "decision"), runtimePath: resolve("fixture-runtime"),
            profile, snapshot, trustedRoots: [parent], request, evidence: { reports: [{ id: "fixture" }] },
        });
        assert.equal(report.usage.uniqueCalls, 1);
        assert.deepEqual(report.requests, []);
        assert.equal(report.status, "unavailable");
        assert.match(report.error, /request evidence missing/);
        assert.equal(report.result, null);
    }
});

test("final profile checks include distinct accounting events received during shutdown", async t => {
    for (const host of ["selector", "evaluator"]) for (const lateUsage of [{}, { model: "fallback" }, { reasoningEffort: "high" }]) {
        const parent = await mkdtemp(join(tmpdir(), "selector-late-accounting-"));
        t.after(() => rm(parent, { recursive: true, force: true }));
        const fake = fakeSdk({ lateUsage });
        const report = await (host === "selector" ? runOwnedSelection : runSelectionEvaluation)({
            ...fake, root: join(parent, "decision"), runtimePath: resolve("fixture-runtime"),
            profile, snapshot, trustedRoots: [parent], request, evidence: { reports: [{ id: "fixture" }] },
        });
        const mismatched = Object.keys(lateUsage).length > 0;
        assert.equal(report.usage.uniqueCalls, 2);
        assert.equal(report.cleanup.runtimeStopped, true);
        assert.equal(report.status, mismatched ? "unavailable" : "completed");
        if (mismatched) {
            assert.equal(report.result, null);
            assert.match(report.error, /usage .*mismatched/);
        } else assert(report.result);
    }
});

test("a failed wait closes pending requests before owned cleanup finishes", async t => {
    for (const host of ["selector", "evaluator"]) {
        const parent = await mkdtemp(join(tmpdir(), "selector-failed-wait-"));
        t.after(() => rm(parent, { recursive: true, force: true }));
        let releaseBody, releaseStop, reachedStop;
        const bodyGate = new Promise(resolve => { releaseBody = resolve; });
        const stopGate = new Promise(resolve => { releaseStop = resolve; });
        const stopping = new Promise(resolve => { reachedStop = resolve; });
        const fake = fakeSdk({ waitFailure: true, bodyInspection: () => bodyGate,
            stopGate: async () => { reachedStop(); await stopGate; } });
        const running = (host === "selector" ? runOwnedSelection : runSelectionEvaluation)({
            ...fake, root: join(parent, "decision"), runtimePath: resolve("fixture-runtime"),
            profile, snapshot, trustedRoots: [parent], request, evidence: { reports: [{ id: "fixture" }] },
        });
        await stopping;
        releaseBody();
        await assert.rejects(fake.observed.lastRequest, /cancelled/);
        assert.equal(fake.observed.forwards, 0);
        releaseStop();
        const report = await running;
        assert.equal(report.status, "unavailable");
        assert.match(report.error, /wait failed/);
        assert.equal(report.result, null);
        assert.equal(report.cleanup.runtimeStopped, true);
    }
});

test("the evaluator reserves its allowance before concurrent request inspection", async t => {
    const parent = await mkdtemp(join(tmpdir(), "evaluation-request-limit-"));
    t.after(() => rm(parent, { recursive: true, force: true }));
    let outcomes;
    const fake = fakeSdk({ requestProbe: async (handler, outbound, context) => {
        outcomes = await Promise.allSettled(Array.from({ length: 9 }, () =>
            handler.sendRequest(outbound.clone(), context)));
    } });
    const report = await runSelectionEvaluation({
        ...fake, root: join(parent, "decision"), runtimePath: resolve("fixture-runtime"),
        profile, evidence: { reports: [{ id: "fixture" }] },
    });
    assert.equal(outcomes.filter(item => item.status === "fulfilled").length, 8);
    const rejected = outcomes.filter(item => item.status === "rejected");
    assert.equal(rejected.length, 1);
    assert.match(rejected[0].reason.message, /excess/);
    assert.equal(fake.observed.forwards, 8);
    assert.equal(report.requests.length, 8);
    assert.equal(report.cleanup.runtimeStopped, true);
});

test("matched strategies seed only synthetic traffic and preserve fork/omission evidence", async t => {
    for (const strategy of ["boundaryFork", "boundedHistory", "explicitBrief"]) {
        const { report, observed } = await fixture(t, { strategy });
        assert.equal(report.status, "completed");
        assert.equal(report.source.modelRequestsForwarded, 0);
        assert.equal(report.source.seedRequests, 3);
        assert.equal(observed.forwards, 1);
        assert.equal(report.usage.uniqueCalls, 1);
        if (strategy === "boundaryFork") assert.equal(report.source.inheritedPrefixMatched, true);
        if (strategy === "boundedHistory") {
            assert.equal(report.source.acquisition.truncated, true);
            assert(report.source.acquisition.omitted > 0);
        }
    }
    const { report, observed } = await fixture(t, { strategy: "boundaryFork", forkMismatch: true });
    assert.equal(report.status, "unavailable");
    assert.match(report.error, /frozen scenario prefix/);
    assert.equal(observed.forwards, 0);
});
