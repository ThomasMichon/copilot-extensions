/** Opt-in owned-runtime selector. Importing never joins, launches or dispatches. */
import { mkdir } from "node:fs/promises";
import { isAbsolute, join } from "node:path";
import { selectionAgent, selectionPrompt, selectionReader, selectionReadTool,
    selectionReadParameters } from "./skill-selection-contract.mjs";
import { canonicalRequestEvidence } from "./skill-selection-native.mjs";
import { bytes, collectHistory, deadline, runtimeEnvironment, safeError, summarizeRequest,
    syntheticHandler, SENTINELS } from "./skill-selection-spike.mjs";

const matches = (actual, profile) => actual?.modelId === profile.model
    && actual.reasoningEffort === profile.reasoningEffort && actual.contextTier === profile.contextTier;
const finite = value => typeof value === "number" && Number.isFinite(value) && value >= 0;

/** A successful filesystem read is not proof the native tool payload reached the model. */
export function readContentDelivered(body, content) {
    function contains(value, depth = 0) {
        if (depth > 8 || value === null || value === undefined) return false;
        if (typeof value === "string") {
            if (value.includes(content) || value.includes(JSON.stringify(content).slice(1, -1))) return true;
            let parsed;
            try { parsed = JSON.parse(value); }
            catch (error) { if (error instanceof SyntaxError) return false; throw error; }
            return contains(parsed, depth + 1);
        }
        if (Array.isArray(value)) return value.some(item => contains(item, depth + 1));
        if (typeof value === "object") return Object.values(value).some(item => contains(item, depth + 1));
        return false;
    }
    return (body.messages ?? body.input ?? []).some(message =>
        (message.type === "function_call_output" || ["tool", "function"].includes(message.role))
        && contains(message.output ?? message.content));
}

export function recordReadDelivery(body, outputs, delivered) {
    const pending = [...outputs].filter(([digest]) => !delivered.has(digest));
    if (pending.some(([, content]) => !readContentDelivered(body, content))) {
        throw new Error("acquired source content was not delivered inline to the selector");
    }
    for (const [digest] of pending) delivered.add(digest);
}

/** Replayed accounting identities count once; contradictory duplicates are errors. */
export function selectionUsage() {
    const calls = new Map();
    return {
        add(event) {
            if (event.type !== "assistant.usage") return;
            const data = event.data;
            const identity = data.accounting?.usageId
                ? JSON.stringify([data.accounting.sourceSessionId, data.accounting.usageId])
                : data.apiCallId ? JSON.stringify(["api", data.apiCallId]) : event.id;
            if (!identity) throw new Error("usage identity unavailable");
            const call = {
                model: data.model, reasoningEffort: data.reasoningEffort ?? null,
                inputTokens: data.inputTokens ?? null, outputTokens: data.outputTokens ?? null,
                cacheReadTokens: data.cacheReadTokens ?? null, cacheWriteTokens: data.cacheWriteTokens ?? null,
                nanoAiu: data.copilotUsage?.totalNanoAiu ?? null,
                maxPromptTokens: data.maxPromptTokens ?? null,
            };
            const previous = calls.get(identity);
            if (previous && JSON.stringify(previous) !== JSON.stringify(call)) {
                throw new Error("contradictory duplicate usage");
            }
            calls.set(identity, call);
        },
        summary() {
            const values = [...calls.values()];
            return { calls: values, uniqueCalls: values.length,
                cost: values.length && values.every(call => finite(call.nanoAiu))
                    ? { unit: "nano-AIU", value: values.reduce((sum, call) => sum + call.nanoAiu, 0) }
                    : { unit: "nano-AIU", value: null, reason: "authoritative per-call cost unavailable" } };
        },
    };
}

/** This guard belongs only to a newly owned client, never to the requesting runtime. */
export function selectionRequestHandler(Base, { owned, profile, requests, maxRequests = 16, live = true,
    syntheticOnly = () => false, seedRequests = [], inspectReadDelivery = () => {}, readParameters,
    isCancelled = () => false }) {
    let attempts = 0, metadata = 0;
    const seed = syntheticHandler(Base, owned, seedRequests);
    return new class extends Base {
        async sendRequest(request, context) {
            if (context.signal?.aborted || isCancelled()) throw new Error("selector request cancelled");
            if (request.method === "GET" && /\/models\/?$/.test(new URL(request.url).pathname)) {
                if (++metadata > 4) throw new Error("selector model-metadata allowance exhausted");
                return super.sendRequest(request, context);
            }
            if (!owned.has(context.sessionId) || context.signal.aborted || isCancelled()) {
                throw new Error("unowned or cancelled selector request refused");
            }
            if (syntheticOnly()) return seed.sendRequest(request.clone(), context);
            if (++attempts > maxRequests) throw new Error("selector request allowance exhausted");
            const body = await request.clone().json();
            if (context.signal.aborted || isCancelled()) throw new Error("selector cancelled during request inspection");
            const inspection = canonicalRequestEvidence(body, profile);
            if (readParameters) {
                const parameters = (body.tools?.[0]?.function ?? body.tools?.[0])?.parameters;
                inspection.nativeReadNamesMatch = JSON.stringify(parameters?.properties?.skill?.enum)
                    === JSON.stringify(readParameters.properties.skill.enum);
            }
            const effort = body.reasoning_effort ?? body.reasoning?.effort;
            if (Object.values(inspection).some(value => value !== true)
                || effort !== profile.reasoningEffort) {
                throw new Error("effective selector request differs from the canonical profile or tool boundary");
            }
            inspectReadDelivery(body);
            requests.push({ ...summarizeRequest(body, context), inspection, reasoningEffort: effort,
                forwarded: live });
            if (!live) throw new Error("model-free selector request inspected; live dispatch is disabled");
            return super.sendRequest(request, context);
        }
        async openWebSocket() {
            throw new Error("owned selector requires inspectable HTTP inference");
        }
    };
}

/**
 * Host-supplied snapshot/roots/profile are policy, not model arguments. Each decision owns
 * a fresh runtime and state directory; stopping it cannot affect the requesting session.
 */
export async function runOwnedSelection({ sdk, root, runtimePath, profile, snapshot, trustedRoots,
    request, signal, timeoutMs = 180000, maxRequests = 16, live = true,
    strategy = "isolated", scenarioHistory, readWitness = [] }) {
    if (!isAbsolute(root) || !isAbsolute(runtimePath)
        || !profile?.model || !profile.reasoningEffort || !["default", "long_context"].includes(profile.contextTier)
        || !Number.isInteger(timeoutMs) || timeoutMs < 1
        || !Number.isInteger(maxRequests) || maxRequests < 1 || maxRequests > 64
        || !Array.isArray(readWitness) || readWitness.length > 8 || live && readWitness.length
        || !["isolated", "boundaryFork", "boundedHistory", "explicitBrief"].includes(strategy)
        || strategy !== "isolated" && (!Array.isArray(scenarioHistory) || !scenarioHistory.length
            || scenarioHistory.length > 4 || scenarioHistory.some(value => typeof value !== "string" || bytes(value) > 64000))) {
        throw new Error("owned selector requires absolute isolated paths, explicit profile and bounded execution");
    }
    const agent = selectionAgent(profile);
    let prompt = selectionPrompt(snapshot, request);
    const reader = await selectionReader(snapshot, trustedRoots, { maxBytes: 128000, maxReads: 12 });
    const started = performance.now(), owned = new Set(), requests = [], seedRequests = [], usage = selectionUsage();
    const report = { version: 1, status: "unavailable", requestedProfile: { ...profile },
        hostContractVersion: 3, readProfile: { maxBytes: 128000, maxReads: 12, inlineDeliveryRequired: true },
        catalogDigest: snapshot.digest, strategy, suppliedBytes: bytes(prompt), requests,
        liveInferenceDispatched: false, result: null, receipts: [], sourceFailures: [], permissionDenials: [],
        cleanup: { runtimeStopped: null, errors: [] } };
    const readOutputs = new Map(), delivered = new Set();
    let client, session, unsubscribe, usageError, running = false, cancelled = false, seeding = false, witnessing = false;
    const cancel = () => { cancelled = true; };
    signal?.addEventListener("abort", cancel, { once: true });
    const checked = async operation => {
        const value = await deadline(operation, timeoutMs);
        if (cancelled || signal?.aborted) throw new Error("selector decision cancelled");
        return value;
    };
    try {
        if (signal?.aborted) throw new Error("selector decision cancelled before startup");
        await mkdir(root);
        const home = join(root, "owned-home"), workspace = join(root, "workspace");
        await mkdir(home); await mkdir(workspace);
        client = new sdk.CopilotClient({
            mode: "empty", baseDirectory: home, workingDirectory: workspace,
            env: runtimeEnvironment(process.env), useLoggedInUser: true, logLevel: "none",
            connection: sdk.RuntimeConnection.forStdio({ path: runtimePath, args: ["--no-auto-update", "--no-remote"] }),
            requestHandler: selectionRequestHandler(sdk.CopilotRequestHandler,
                { owned, profile, requests, maxRequests, live, syntheticOnly: () => seeding, seedRequests,
                    readParameters: selectionReadParameters(snapshot),
                    isCancelled: () => cancelled || signal?.aborted,
                    inspectReadDelivery: body => recordReadDelivery(body, readOutputs, delivered) }),
        });
        // Acquisitions must settle before cleanup: a deadline race can orphan a late runtime/session.
        await client.start();
        report.startupMs = Math.round(performance.now() - started);
        const config = {
            ...profile, allowedModels: [profile.model], workingDirectory: workspace, configDirectory: home,
            enableConfigDiscovery: false, enableSkills: false, enableFileHooks: false,
            skipCustomInstructions: true, customAgentsLocalOnly: true,
            mcpServers: {}, requestExtensions: false, remoteSession: "off",
            capi: { enableWebSocketResponses: false }, infiniteSessions: { enabled: false },
            largeOutput: { enabled: false },
            availableTools: [selectionReadTool], customAgents: [agent], agent: agent.name,
            onPermissionRequest: permission => {
                report.permissionDenials.push({ kind: permission.kind });
                return { kind: "denied-no-approval-rule-and-could-not-request-from-user" };
            },
            tools: [{
                name: selectionReadTool, description: "Read a necessary skill body or its declared local guide. No execution.",
                skipPermission: true,
                parameters: selectionReadParameters(snapshot),
                async handler(args, invocation) {
                    if (cancelled || signal?.aborted || invocation.sessionId !== session?.sessionId) {
                        throw new Error("source read is outside the active owned selector");
                    }
                    try {
                        const result = await reader.read(args);
                        if (result.status !== "available") report.sourceFailures.push({ skill: args.skill, reason: result.reason });
                        else if (!witnessing) readOutputs.set(result.contentDigest, result.content);
                        return JSON.stringify(result);
                    } catch (error) {
                        report.sourceFailures.push({ skill: args.skill, reason: safeError(error) });
                        throw error;
                    }
                },
            }],
        };
        session = await client.createSession(config);
        owned.add(session.sessionId);
        if (strategy !== "isolated") {
            const source = session, preparation = performance.now();
            seeding = true;
            for (const message of [...scenarioHistory, SENTINELS.boundary]) {
                await checked(source.sendAndWait({ prompt: message }, 15000));
            }
            seeding = false;
            const events = await checked(source.rpc.eventLog.read({
                direction: "backward", max: 32, types: ["user.message", "assistant.message"],
                agentScope: "primary", includeEphemeral: false,
            }));
            const boundary = events.events.find(event => event.type === "user.message"
                && event.data.content === SENTINELS.boundary)?.id;
            if (events.cursorStatus !== "ok" || events.hasMore || !boundary) {
                throw new Error("owned scenario boundary unavailable");
            }
            const prefix = events.events.slice(0, events.events.findIndex(event => event.id === boundary));
            report.source = { synthetic: true, modelRequestsForwarded: 0,
                seedRequests: seedRequests.length, prefixEvents: prefix.length };
            if (strategy === "boundaryFork") {
                const fork = await client.rpc.sessions.fork({ sessionId: source.sessionId,
                    toEventId: boundary, name: "owned-selector-context-comparison" });
                owned.add(fork.sessionId);
                session = await client.resumeSession(fork.sessionId, config);
                const inherited = await checked(session.rpc.eventLog.read({
                    direction: "backward", max: 32, types: ["user.message", "assistant.message"],
                    agentScope: "primary", includeEphemeral: false,
                }));
                if (inherited.cursorStatus !== "ok" || inherited.hasMore
                    || JSON.stringify(inherited.events.map(event => event.id)) !== JSON.stringify(prefix.map(event => event.id))) {
                    throw new Error("owned native fork does not match the frozen scenario prefix");
                }
                report.source.inheritedPrefixMatched = true;
                prompt = selectionPrompt(snapshot, { ...request,
                    context: { source: "owned-native-event-boundary-fork", truncated: false, omitted: 0 } });
            } else {
                if (strategy === "boundedHistory") {
                    const context = await checked(collectHistory(source.rpc.eventLog, { boundary }));
                    report.source.acquisition = { ...context, messages: undefined };
                    prompt = selectionPrompt(snapshot, { ...request, context });
                }
                session = await client.createSession(config);
                owned.add(session.sessionId);
            }
            report.preparationMs = Math.round(performance.now() - preparation);
            report.suppliedBytes = bytes(prompt);
        }
        unsubscribe = session.on(event => {
            try { if (!seeding) usage.add(event); }
            catch (error) { usageError = error; }
        });
        const before = await checked(session.rpc.model.getCurrent());
        const selected = (await checked(session.rpc.agent.getCurrent())).agent;
        if (!matches(before, profile) || selected?.name !== agent.name) {
            throw new Error("resolved selector configuration differs before dispatch");
        }
        report.resolvedBefore = before;
        if (readWitness.length) {
            const initializations = seedRequests.length;
            seeding = true;
            await checked(session.sendAndWait({ prompt: "MODEL_FREE_DECLARED_READ_INITIALIZATION" }, 15000));
            seeding = false;
            witnessing = true;
            for (const arguments_ of readWitness) {
                const count = reader.receipts().length;
                const result = await checked(session.rpc.tools.execute({ name: selectionReadTool, arguments: arguments_ }));
                if (reader.receipts().length <= count) {
                    throw new Error(`native declared-read witness did not acquire its source: ${
                        typeof result === "object" ? result.error ?? result.resultType : "unavailable tool receipt"}`);
                }
            }
            witnessing = false;
            report.nativeReadWitness = { status: "completed", reads: readWitness.length,
                syntheticInitializationRequests: seedRequests.length - initializations };
        }
        running = true;
        let cancelListener;
        const aborted = new Promise((_, reject) => {
            cancelListener = () => { cancelled = true; reject(new Error("selector decision cancelled")); };
            signal?.addEventListener("abort", cancelListener, { once: true });
            if (signal?.aborted) cancelListener();
        });
        let response;
        try {
            response = await checked(Promise.race([session.sendAndWait({ prompt }, timeoutMs), aborted]));
            running = false;
        } finally { signal?.removeEventListener("abort", cancelListener); }
        if (cancelled || signal?.aborted) throw new Error("late selector output rejected");
        const after = await checked(session.rpc.model.getCurrent());
        report.resolvedAfter = after;
        if (!matches(after, profile) || !requests.length) throw new Error("selector configuration or request evidence missing");
        const result = await reader.validate(JSON.parse(response?.data?.content));
        if (result.status === "no-match" && report.sourceFailures.length) {
            throw new Error("source acquisition failure cannot become a successful no-match decision");
        }
        if (usageError) throw usageError;
        report.result = result;
        report.status = "completed";
    } catch (error) {
        if (!live && error?.message === "model-free selector request inspected; live dispatch is disabled"
            && requests.length && requests.every(item => !item.forwarded)) {
            report.status = "model-free-witness";
            report.holdReason = error.message;
        } else report.error = safeError(error);
    } finally {
        cancelled = true;
        signal?.removeEventListener("abort", cancel);
        if (running && session) {
            try { await deadline(session.abort(), 15000); }
            catch (error) { report.cleanup.errors.push(safeError(error)); }
        }
        if (client) {
            try {
                report.cleanup.errors.push(...(await client.stop()).map(safeError));
                report.cleanup.runtimeStopped = report.cleanup.errors.length === 0;
            } catch (error) {
                report.cleanup.runtimeStopped = false;
                report.cleanup.errors.push(safeError(error));
            }
        }
        unsubscribe?.();
        if (usageError) report.error = safeError(usageError);
        report.usage = usage.summary();
        const usageMismatch = report.status === "completed" && (!report.usage.calls.length
            || report.usage.calls.some(call => call.model !== profile.model || call.reasoningEffort !== profile.reasoningEffort));
        if (usageMismatch) report.error = "actual selector usage configuration unavailable or mismatched";
        report.receipts = reader.receipts();
        report.sourceDelivery = [...readOutputs.keys()].map(contentDigest => ({
            contentDigest, deliveredInline: delivered.has(contentDigest),
        }));
        report.liveInferenceDispatched = requests.some(item => item.forwarded);
        report.elapsedMs = Math.round(performance.now() - started);
        if (signal?.aborted) report.error ??= "selector decision cancelled during settlement";
        if (report.cleanup.errors.length || usageError || usageMismatch || signal?.aborted) {
            report.status = "unavailable";
            report.result = null;
        }
    }
    return report;
}
