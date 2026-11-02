import test from "node:test";
import assert from "node:assert/strict";
import { inspectSelectionHost, canonicalRequestEvidence } from "../scripts/skill-selection-native.mjs";
import { spikeInputs, spikeStatus, syntheticHandler } from "../scripts/skill-selection-spike.mjs";
import { resolve } from "node:path";
import { selectionAgent } from "../scripts/skill-selection-contract.mjs";

const profile = { agentName: "skill-selection-specialist", model: "candidate",
    reasoningEffort: "medium", contextTier: "long_context" };
const foreground = { modelId: "coordinator", reasoningEffort: "medium", contextTier: "long_context" };

test("canonical owned witness needs an explicit complete profile and no alternate discovery mode", () => {
    const paths = [resolve("sdk-fixture"), resolve("fresh-fixture")];
    const options = ["--canonical-agent-only", "--model", "candidate", "--reasoning-effort", "medium",
        "--context-tier", "long_context"];
    assert.equal(spikeInputs([...paths, ...options]).canonicalAgentOnly, true);
    assert.throws(() => spikeInputs([...paths, "--canonical-agent-only"]), /complete explicit profile/);
    assert.throws(() => spikeInputs([...paths, ...options, "--list-models"]), /excludes other modes/);
    assert.throws(() => spikeInputs([...paths, "--allow-model-metadata"]), /requires canonical-agent-only/);
    assert.equal(spikeInputs([...paths, ...options, "--allow-model-metadata"]).allowModelMetadata, true);
});

test("metadata opt-in forwards only native-supplied model-list GETs, never unowned inference", async () => {
    const calls = [], events = [];
    class Metadata {
        async sendRequest(request) {
            calls.push([request.method, new URL(request.url).pathname]);
            return new Response("{}", { status: 200 });
        }
    }
    const context = { signal: new AbortController().signal };
    const handler = syntheticHandler(Metadata, new Set(), [], undefined, undefined, undefined, {
        allowModelMetadata: true, onModelMetadata: kind => events.push(kind),
    });
    assert.equal((await handler.sendRequest(new Request("https://example.invalid/models"), context)).status, 200);
    assert.deepEqual(calls, [["GET", "/models"]]);
    assert.deepEqual(events, ["attempts", "forwardAttempts", "succeeded"]);
    for (const request of [new Request("https://example.invalid/responses", { method: "POST", body: "{}" }),
        new Request("https://example.invalid/models", { method: "POST", body: "{}" }),
        new Request("https://example.invalid/other")]) {
        await assert.rejects(handler.sendRequest(request, context), /unowned model request/);
    }
    assert.equal(calls.length, 1);
    const denied = syntheticHandler(Metadata, new Set(), []);
    await assert.rejects(denied.sendRequest(new Request("https://example.invalid/models"), context), /not admitted/);
    let refused = 0;
    const httpOnly = syntheticHandler(Metadata, new Set(), [], undefined, undefined, undefined, {
        onWebSocketRefused: () => { refused++; },
    });
    await assert.rejects(httpOnly.openWebSocket(context), /WebSocket/);
    assert.equal(refused, 1);
    assert.equal(calls.length, 1);
});

test("canonical role evidence comes from system input, not a quoted user prompt", () => {
    const prompt = selectionAgent(profile).prompt;
    const base = { model: profile.model, tools: [{ type: "function", name: "skill_selection_read" }] };
    const response = canonicalRequestEvidence({ ...base, instructions: prompt, input: [] }, profile);
    assert.deepEqual(response, { canonicalSystemPromptPresent: true, modelMatches: true, readOnlyToolsetMatches: true });
    const chat = { ...base, messages: [{ role: "developer", content: [{ type: "text", text: prompt }] }] };
    assert.equal(canonicalRequestEvidence(chat, profile).canonicalSystemPromptPresent, true);
    assert.equal(canonicalRequestEvidence({ ...base, messages: [{ role: "user", content: prompt }] }, profile)
        .canonicalSystemPromptPresent, false);
    assert.equal(canonicalRequestEvidence({ ...base, model: "different", instructions: prompt }, profile).modelMatches, false);
    assert.equal(canonicalRequestEvidence({ ...base, tools: [...base.tools, ...base.tools] }, profile).readOnlyToolsetMatches, false);
    assert(!JSON.stringify(response).includes(JSON.stringify(prompt)));
});

test("canonical witness cannot pass missing input evidence, mismatch or refused traffic", () => {
    const request = { inspection: {
        canonicalSystemPromptPresent: true, modelMatches: true, readOnlyToolsetMatches: true,
    } };
    const report = { mode: "canonical-agent-witness", forwardedModelRequests: 0,
        cleanup: { runtimeStopped: true, taskCancellationErrors: [], abortErrors: [], runtimeStopErrors: [] },
        native: { catalog: { status: "available" }, interceptedRequests: 1, requestAttempts: { http: 1, webSocket: 0 } },
        arms: { canonicalAgent: { status: "completed", configurationMatched: true, requests: [request] } } };
    assert.equal(spikeStatus(report), "completed-with-limitations");
    for (const change of [{ requests: [] }, { configurationMatched: false }, { requests: [{}] }]) {
        assert.equal(spikeStatus({ ...report, arms: { canonicalAgent: { ...report.arms.canonicalAgent, ...change } } }), "incomplete");
    }
    assert.equal(spikeStatus({ ...report, native: { ...report.native,
        requestAttempts: { http: 2, webSocket: 0 } } }), "failed");
    const metadata = { attempts: 1, forwardAttempts: 1, succeeded: 1, failed: 0, refused: 0 };
    const observed = { ...report, native: { ...report.native, modelMetadata: metadata,
        requestAttempts: { http: 2, webSocket: 0 } } };
    assert.equal(spikeStatus(observed), "completed-with-limitations");
    assert.equal(spikeStatus({ ...observed, native: { ...observed.native,
        modelMetadata: { ...metadata, succeeded: 0, failed: 1 } } }), "failed");
    const httpOnly = { ...observed, native: { ...observed.native, refusedWebSocketRequests: 1,
        requestAttempts: { http: 2, webSocket: 1 } } };
    assert.equal(spikeStatus(httpOnly), "completed-with-limitations");
    assert.equal(spikeStatus({ ...httpOnly, native: { ...httpOnly.native, refusedWebSocketRequests: 0 } }), "failed");
});

function fixture({ agents, models = [foreground], selectedAgents = [null], skills, history } = {}) {
    let modelRead = 0, agentRead = 0;
    const agent = selectionAgent({ model: profile.model, reasoningEffort: profile.reasoningEffort });
    return { sessionId: "owned-parent", rpc: {
        model: { getCurrent: async () => models[Math.min(modelRead++, models.length - 1)] },
        agent: { list: async () => ({ agents: agents ?? [{ ...agent, id: "native:selector" }] }),
            getCurrent: async () => ({ agent: selectedAgents[Math.min(agentRead++, selectedAgents.length - 1)] }) },
        skills: { list: async () => ({ skills: skills ?? [
            { name: "plugin:registered", description: "Registered advisory skill", enabled: true, source: "plugin" },
        ] }) },
        eventLog: { read: async () => history ?? { events: [], cursor: "tail", cursorStatus: "ok", hasMore: false } },
        tasks: { startAgent: () => { throw new Error("preflight must not dispatch"); } },
    } };
}

test("native declarations do not become resolved child, fit or quality evidence", async () => {
    const result = await inspectSelectionHost(fixture(), profile);
    assert.equal(result.status, "declared");
    assert.equal(result.agent.id, "native:selector");
    assert.equal(result.parent.modelId, "coordinator");
    assert.equal(result.parent.modelAndAgentUnchangedDuringPreflight, true);
    assert.equal(result.child.contextTier, null);
    assert.equal(result.child.resolvedModel, null);
    assert.equal(result.child.effectiveTools, null);
    assert.equal(result.comparable, false);
    assert.equal(result.inferenceDispatched, false);
    assert.equal(result.qualityEvidence, false);
});

test("missing or ambiguous native specialist is unavailable, not no-match", async () => {
    const agent = { ...selectionAgent({ model: profile.model, reasoningEffort: profile.reasoningEffort }),
        id: "native:selector" };
    for (const agents of [[], [agent, agent], [agent, { ...agent, name: "different" }]]) {
        const result = await inspectSelectionHost(fixture({ agents }), profile);
        assert.equal(result.status, "unavailable");
        assert(result.failures.includes("canonical specialist native ID unavailable or ambiguous"));
    }
});

test("different authored model, tools or prompt remain explicit failures", async () => {
    const agent = { ...selectionAgent({ model: "different" }), id: "native:selector",
        tools: undefined, prompt: "read-only guidance-selection leaf but not the canonical prompt" };
    const result = await inspectSelectionHost(fixture({ agents: [agent] }), profile);
    assert.equal(result.status, "unavailable");
    assert(result.failures.includes("specialist authored model differs"));
    assert(result.failures.includes("specialist read-only tool declaration differs"));
    assert(result.failures.includes("canonical specialist prompt differs"));
});

test("absent and different prompt evidence stay distinct without returning authored text", async () => {
    const agent = { ...selectionAgent({ model: profile.model, reasoningEffort: profile.reasoningEffort }),
        id: "native:selector" };
    for (const prompt of [undefined, "", "unexpected authored prompt sentinel"]) {
        const result = await inspectSelectionHost(fixture({ agents: [{ ...agent, prompt }] }), profile);
        const evidence = result.agent.declaredPrompt;
        const available = typeof prompt === "string";
        assert.equal(result.status, "unavailable");
        assert.equal(evidence.available, available);
        assert.equal(evidence.bytes, available ? Buffer.byteLength(prompt) : null);
        assert.equal(evidence.matchesCanonical, available ? false : null);
        assert.equal(evidence.digest === null, !available);
        assert(result.failures.includes(available
            ? "canonical specialist prompt differs" : "canonical specialist prompt unavailable"));
        assert(!JSON.stringify(result).includes("unexpected authored prompt sentinel"));
        assert.equal(result.inferenceDispatched, false);
        assert.equal(result.comparable, false);
    }
    const result = await inspectSelectionHost(fixture({ agents: [agent] }), profile);
    assert.equal(result.agent.declaredPrompt.matchesCanonical, true);
    assert.equal(result.agent.declaredPrompt.digest, result.agent.declaredPrompt.expectedDigest);
    assert(!JSON.stringify(result).includes(JSON.stringify(agent.prompt)));
});

test("foreground changes and failed acquisition cannot return success-shaped preflight", async () => {
    for (const changed of [{ ...foreground, modelId: "changed" },
        { ...foreground, providerId: "changed" }, { ...foreground, pendingAutoTier: "changed" }]) {
        await assert.rejects(inspectSelectionHost(fixture({ models: [foreground, changed] }), profile),
            /foreground model or selected agent changed/);
    }
    await assert.rejects(inspectSelectionHost(fixture({ selectedAgents: [null, { id: "changed" }] }), profile),
        /foreground model or selected agent changed/);
    await assert.rejects(inspectSelectionHost(fixture({ models: [{}] }), profile), /configuration unavailable/);
    await assert.rejects(inspectSelectionHost(fixture({ skills: [] }), profile), /nonempty catalog/);
    await assert.rejects(inspectSelectionHost(fixture({ history: { events: [], cursorStatus: "expired" } }),
        profile, { readContext: true }), /history cursor expired/);
});

test("bounded context report does not return source contents or claim a frozen boundary", async () => {
    const result = await inspectSelectionHost(fixture({ history: {
        events: [{ id: "u", type: "user.message", data: { content: "unpublished context sentinel" } }],
        cursor: "tail", cursorStatus: "ok", hasMore: false,
    } }), profile, { readContext: true });
    assert.equal(result.context.messages, 1);
    assert.equal(result.context.contentsReturned, false);
    assert.equal(result.context.boundary, "bounded-sample-not-frozen");
    assert(!JSON.stringify(result).includes("unpublished context sentinel"));
});
