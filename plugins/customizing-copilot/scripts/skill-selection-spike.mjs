/** Model-free context helpers. No SDK import, runtime connection, or inference on import. */
import { isAbsolute } from "node:path";
import { createHash } from "node:crypto";

export const SENTINELS = Object.freeze({
    old: "SPIKE_OLD_8d256a",
    recent: "SPIKE_RECENT_b739ce",
    boundary: "SPIKE_EXCLUDED_245cf1",
    brief: "SPIKE_BRIEF_e8652b",
    cancel: "SPIKE_CANCEL_190aae",
});
export const bytes = value => Buffer.byteLength(typeof value === "string" ? value : JSON.stringify(value));
const textTypes = ["user.message", "assistant.message"];

export const safeError = error => String(error?.message ?? error)
    .replace(/(?:[a-z][a-z0-9+.-]*:\/\/|[A-Za-z]:[\\/]|\\\\|\/)[^\r\n]+/gi, "<local-path>")
    .replace(/\b[\w.+-]+@[\w.-]+\.\w+\b/g, "<account>").slice(0, 500);

export function armStatus(name, result) {
    if (result.settled === false) return "incomplete";
    if (result.terminal === "subagent.failed" || result.inheritedPrefixIdsMatch === false
        || (name === "nativeTask" && result.cancelled === true)
        || (name === "cancellation" && result.cancelled !== true)) return "failed";
    if (name === "nativeTask" && result.resultObserved !== true) return "incomplete";
    if (!result.requests?.length || result.requests.some(request =>
        !request.attributedSession || (["nativeTask", "cancellation"].includes(name) && !request.attributedAgent))) {
        return "incomplete";
    }
    return "completed";
}

export function catalogMatches(catalog, expected, disabled) {
    return expected.every(fixture => catalog.some(skill => skill.name === fixture.name
        && skill.description === fixture.description && skill.enabled === !disabled.includes(fixture.name)
        && (fixture.userInvocable === undefined || skill.userInvocable === fixture.userInvocable)));
}

export function historyMatches(page, expectedIds, allowAppended = false) {
    return page.cursorStatus === "ok" && !page.hasMore
        && (allowAppended || page.events.length === expectedIds.length)
        && JSON.stringify(page.events.slice(0, expectedIds.length).map(event => event.id)) === JSON.stringify(expectedIds);
}

export function spikeStatus(report) {
    const cleanup = report.cleanup;
    if (report.error || ["taskCancellationErrors", "abortErrors", "runtimeStopErrors"]
        .some(key => cleanup?.[key]?.length)) return "failed";
    if (cleanup?.runtimeStopped === false) return "incomplete";
    if (report.mode === "catalog-discovery") {
        return report.native.interceptedRequests === 0 && report.forwardedModelRequests === 0
            && report.native.requestAttempts?.http === 0 && report.native.requestAttempts?.webSocket === 0
            ? report.status : "failed";
    }
    if (report.mode === "model-enumeration") return report.status;
    const arms = Object.values(report.arms);
    if (arms.some(arm => ["failed", "unavailable"].includes(arm.status))
        || report.native.parentConfigurationUnchanged === false
        || report.native.parentTextPrefixUnchanged === false) return "failed";
    if (arms.some(arm => arm.status !== "completed" || arm.settled === false)
        || report.native.catalog.status !== "available") return "incomplete";
    return "completed-with-limitations";
}

export function runtimeEnvironment(environment) {
    const excluded = /^(COPILOT_|AGENT_|OTEL_|OPENAI_|ANTHROPIC_|CLAUDE_|GH_TOKEN$|GH_ENTERPRISE_TOKEN$|GITHUB_TOKEN$|GITHUB_API_TOKEN$|GITHUB_ENTERPRISE_TOKEN$)/i;
    return Object.fromEntries(Object.entries(environment).filter(([key, value]) =>
        value !== undefined && !excluded.test(key)));
}

export function spikeInputs(args) {
    const firstOption = args.findIndex(value => typeof value === "string" && value.startsWith("--"));
    const paths = firstOption < 0 ? args : args.slice(0, firstOption);
    if (![2, 3].includes(paths.length) || !paths.every(value => typeof value === "string" && isAbsolute(value))) {
        throw new Error("Usage: node run-skill-selection-spike.mjs <absolute-sdk-directory> <fresh-absolute-fixture-root> [absolute-runtime-executable] [--list-models | --catalog-only --skill-directory absolute-root] [--model id] [--reasoning-effort level] [--context-tier tier]");
    }
    const profile = {}, seen = new Set();
    const skillDirectories = [];
    let modelsOnly = false, catalogOnly = false;
    for (let index = paths.length; index < args.length; index++) {
        const option = args[index];
        if (seen.has(option) && option !== "--skill-directory") throw new Error("duplicate spike option");
        seen.add(option);
        if (option === "--list-models") { modelsOnly = true; continue; }
        if (option === "--catalog-only") { catalogOnly = true; continue; }
        if (!["--model", "--reasoning-effort", "--context-tier", "--skill-directory"].includes(option)) throw new Error("unsupported spike option");
        const value = args[++index];
        if (typeof value !== "string" || !value.trim() || value.startsWith("--")) throw new Error("missing spike option value");
        if (option === "--skill-directory") {
            if (!isAbsolute(value) || skillDirectories.includes(value)) throw new Error("skill roots must be unique absolute paths");
            skillDirectories.push(value);
        }
        else if (option === "--model") profile.model = value;
        else if (option === "--reasoning-effort" && ["none", "low", "medium", "high", "xhigh", "max"].includes(value)) profile.reasoningEffort = value;
        else if (option === "--context-tier" && ["default", "long_context"].includes(value)) profile.contextTier = value;
        else throw new Error("unsupported spike option or value");
    }
    if (!profile.model && Object.keys(profile).length) throw new Error("effort and context tier require an explicit model");
    if (catalogOnly && (modelsOnly || !skillDirectories.length || Object.keys(profile).length)
        || skillDirectories.length && !catalogOnly) throw new Error("catalog-only requires skill roots and excludes other modes and model profiles");
    const [sdkDirectory, root, runtimePath] = paths;
    return { sdkDirectory, root, runtimePath, modelsOnly, catalogOnly, skillDirectories, profile };
}

/** Counts and a frozen-descriptor digest only; never print adopter catalog text or paths. */
export function catalogSummary(catalog) {
    const categories = ["project", "inherited", "personal-copilot", "personal-agents",
        "plugin", "custom", "builtin", "sdk", "provider"];
    const sources = {};
    for (const skill of catalog) {
        const category = categories.includes(skill.source) ? skill.source : "other";
        sources[category] = (sources[category] ?? 0) + 1;
    }
    return {
        count: catalog.length, bytes: bytes(catalog),
        disabled: catalog.filter(skill => !skill.enabled).length,
        pathless: catalog.filter(skill => !skill.path).length,
        pluginQualified: catalog.filter(skill => typeof skill.pluginName === "string").length,
        sources,
        digest: createHash("sha256").update(JSON.stringify(catalog)).digest("hex"),
    };
}

/** Freeze the native identities and descriptors, not a reconstructed eligible-skill list. */
export function freezeCatalog(skills) {
    return Object.freeze(skills.map(skill => {
        if (typeof skill.name !== "string" || typeof skill.description !== "string"
            || typeof skill.enabled !== "boolean" || typeof skill.source !== "string") {
            throw new Error("invalid native skill descriptor");
        }
        return Object.freeze(Object.fromEntries(
            ["name", "description", "enabled", "source", "pluginName", "path", "commandName", "userInvocable", "argumentHint"]
                .filter(key => skill[key] !== undefined).map(key => [key, skill[key]]),
        ));
    }));
}

/** Tail-first, durable, primary text only; never substitute reasoning or raw tool output. */
export async function collectHistory(eventLog, { boundary, maxBytes = 6000, maxPages = 3, pageSize = 16 } = {}) {
    if (![maxBytes, maxPages, pageSize].every(n => Number.isInteger(n) && n > 0)
        || pageSize > 1000) throw new Error("invalid history bounds");
    let cursor, more = true, pages = 0, found = !boundary;
    const collected = [], seen = new Set();
    while (more && pages < maxPages) {
        const page = await eventLog.read({
            ...(cursor ? { cursor } : {}), direction: "backward", max: pageSize,
            types: textTypes, agentScope: "primary", includeEphemeral: false,
        });
        if (page.cursorStatus !== "ok") throw new Error("history cursor expired");
        if (typeof page.cursor !== "string") throw new Error("missing history cursor");
        const fresh = page.events.filter(event => !seen.has(event.id));
        fresh.forEach(event => seen.add(event.id));
        collected.unshift(...fresh);
        cursor = page.cursor;
        more = page.hasMore;
        pages++;
        if (boundary && collected.some(event => event.id === boundary)) found = true;
    }
    if (!found) throw new Error("frozen boundary unavailable within history bounds");
    const prefix = boundary ? collected.slice(0, collected.findIndex(event => event.id === boundary)) : collected;
    const eligible = prefix.filter(event => textTypes.includes(event.type) && !event.agentId
        && !event.data?.agentId && !event.data?.parentToolCallId && typeof event.data?.content === "string");
    const messages = [];
    let used = 2, omitted = 0;
    for (const [index, event] of eligible.toReversed().entries()) {
        const message = { role: event.type === "user.message" ? "user" : "assistant", content: event.data.content };
        const size = bytes(message) + (messages.length ? 1 : 0);
        if (used + size > maxBytes) { omitted = eligible.length - index; break; }
        messages.unshift(message);
        used += size;
    }
    return { messages, pages, omitted, truncated: more || omitted > 0, inputBytes: bytes(messages) };
}

/** Report only counts, synthetic marker membership/order, and model/tool identities. */
export function summarizeRequest(body, context = {}) {
    const messages = Array.isArray(body?.messages) ? body.messages
        : Array.isArray(body?.input) ? body.input : null;
    if (!messages) {
        throw new Error(`unsupported model request shape (messagesArray=${Array.isArray(body?.messages)}, inputArray=${Array.isArray(body?.input)}, inputString=${typeof body?.input === "string"})`);
    }
    const instructions = typeof body.instructions === "string" ? body.instructions : "";
    const text = instructions + JSON.stringify(messages);
    const positions = Object.fromEntries(Object.entries(SENTINELS).map(([key, marker]) => [key, text.indexOf(marker)]));
    return {
        requestFormat: Array.isArray(body.messages) ? "chat-completions" : "responses",
        instructionBytes: bytes(instructions),
        assembledBytes: bytes(body), messageBytes: bytes(messages),
        messageCount: messages.length, toolBytes: bytes(body.tools ?? []),
        tools: (body.tools ?? []).map(tool => tool.function?.name ?? tool.name ?? "unknown"),
        model: typeof body.model === "string" ? body.model : null,
        attributedSession: !!context.sessionId, attributedAgent: !!context.agentId,
        sentinels: Object.fromEntries(Object.entries(positions).map(([key, pos]) => [key, pos >= 0])),
        oldBeforeRecent: positions.old >= 0 && positions.recent > positions.old,
    };
}

/** Unknown JsonValue fields stay unavailable; no inferred limits, rates, or qualification. */
export function modelSummary(model) {
    const caps = model?.capabilities, limits = caps?.limits, supports = caps?.supports;
    const positive = value => Number.isFinite(value) && value > 0 ? value : null;
    return {
        id: typeof model?.id === "string" ? model.id : null,
        reasoningEffortSupported: typeof supports?.reasoningEffort === "boolean" ? supports.reasoningEffort : null,
        contextWindow: positive(limits?.max_context_window_tokens),
        promptLimit: positive(limits?.max_prompt_tokens), outputLimit: positive(limits?.max_output_tokens),
        efforts: Array.isArray(model?.supportedReasoningEfforts)
            && model.supportedReasoningEfforts.every(value => typeof value === "string")
            ? model.supportedReasoningEfforts : null,
        defaultEffort: typeof model?.defaultReasoningEffort === "string" ? model.defaultReasoningEffort : null,
        contextTiers: Array.isArray(model?.supportedContextTiers)
            && model.supportedContextTiers.every(value => typeof value === "string")
            ? model.supportedContextTiers : null,
        policy: typeof model?.policy?.state === "string" ? model.policy.state : null,
    };
}

export function modelCatalogSummary(models) {
    if (!Array.isArray(models)) throw new Error("invalid native model catalog");
    return { count: models.length, models: models.map(modelSummary) };
}

/** Observe only this dispatch's own events, including events arriving before startAgent resolves. */
export function observeOwnTask(session) {
    const events = [];
    const unsubscribe = session.on(event => {
        if (event.agentId && ["subagent.configured", "subagent.completed", "subagent.failed",
            "assistant.usage", "assistant.message"].includes(event.type)) events.push(event);
    });
    return {
        unsubscribe,
        async settled(id, timeoutMs = 8000) {
            const end = Date.now() + timeoutMs;
            while (Date.now() < end) {
                const own = events.filter(event => event.agentId === id);
                const terminal = own.find(event => ["subagent.completed", "subagent.failed"].includes(event.type));
                const throughTerminal = terminal ? own.slice(0, own.indexOf(terminal) + 1) : [];
                if (terminal) return {
                    settled: true, terminal: terminal.type,
                    cancelled: terminal.data.cancelled ?? false,
                    configured: throughTerminal.find(event => event.type === "subagent.configured")?.data ?? null,
                    actualModel: terminal.data.model ?? null,
                    firstDispatchedModel: terminal.data.firstDispatchedModel ?? null,
                    resultObserved: terminal.type === "subagent.completed" && !terminal.data.cancelled
                        && throughTerminal.some(event => event.type === "assistant.message"),
                    lateOutputIgnored: own.slice(throughTerminal.length).some(event => event.type === "assistant.message"),
                    usage: throughTerminal.filter(event => event.type === "assistant.usage").map(event => ({
                        model: event.data.model, inputTokens: event.data.inputTokens ?? null,
                        outputTokens: event.data.outputTokens ?? null,
                        reasoningEffort: event.data.reasoningEffort ?? null,
                        maxPromptTokens: event.data.maxPromptTokens ?? null,
                        maxOutputTokens: event.data.maxOutputTokens ?? null, synthetic: true,
                    })),
                };
                await new Promise(resolve => setTimeout(resolve, 25));
            }
            return { settled: false, terminal: null, error: "own task settlement deadline" };
        },
    };
}

/** A deadline is not an abort. The caller retains the operation and owns cleanup. */
export async function deadline(promise, ms = 15000) {
    let timer;
    try {
        return await Promise.race([promise, new Promise((_, reject) => {
            timer = setTimeout(() => reject(new Error("owned operation deadline")), ms);
        })]);
    } finally { clearTimeout(timer); }
}

/** Build a synthetic-only public request interceptor; no inherited forwarding path is called. */
export function syntheticHandler(Base, owned, requests, onHeld = () => {}, holdMs = 5000, onAttempt = () => {}) {
    let pendingBodies = 0;
    return new class extends Base {
        async sendRequest(request, context) {
            onAttempt("http");
            if (!owned.has(context.sessionId)) throw new Error("unowned model request refused");
            if (requests.length + pendingBodies >= 16) throw new Error("synthetic request allowance exhausted");
            pendingBodies++;
            let body, summary;
            try {
                body = await request.json();
                summary = summarizeRequest(body, context);
                requests.push({ sessionId: context.sessionId, agentId: context.agentId, summary });
            } finally { pendingBodies--; }
            if (summary.sentinels.cancel) {
                onHeld();
                await new Promise((resolve, reject) => {
                    const abort = () => { clearTimeout(timer); resolve(); };
                    const timer = setTimeout(() => {
                        context.signal.removeEventListener("abort", abort);
                        reject(new Error("synthetic cancellation hold expired"));
                    }, holdMs);
                    if (context.signal.aborted) abort();
                    else context.signal.addEventListener("abort", abort, { once: true });
                });
            }
            if (summary.requestFormat === "responses") {
                const part = { type: "output_text", text: "SYNTHETIC_TRANSPORT_ONLY", annotations: [] };
                const item = { id: "synthetic-message", type: "message", role: "assistant",
                    status: "completed", content: [part] };
                const completion = {
                    id: "synthetic-response", object: "response", created_at: 0,
                    status: "completed", model: body.model, output: [item],
                    error: null, incomplete_details: null,
                    usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2,
                        input_tokens_details: { cached_tokens: 0 },
                        output_tokens_details: { reasoning_tokens: 0 } },
                };
                if (!body.stream) return Response.json(completion);
                const location = { item_id: item.id, output_index: 0, content_index: 0 };
                const events = [
                    { type: "response.created", response: { ...completion, status: "in_progress", output: [], usage: null } },
                    { type: "response.output_item.added", output_index: 0, item: { ...item, status: "in_progress", content: [] } },
                    { type: "response.content_part.added", ...location, part: { ...part, text: "" } },
                    { type: "response.output_text.delta", ...location, delta: part.text },
                    { type: "response.output_text.done", ...location, text: part.text },
                    { type: "response.content_part.done", ...location, part },
                    { type: "response.output_item.done", output_index: 0, item },
                    { type: "response.completed", response: completion },
                ];
                return new Response(events.map((event, sequence_number) =>
                    `event: ${event.type}\ndata: ${JSON.stringify({ ...event, sequence_number })}\n\n`).join(""),
                { headers: { "Content-Type": "text/event-stream" } });
            }
            const completion = {
                id: "synthetic-completion", object: "chat.completion", created: 0, model: body.model,
                choices: [{ index: 0, message: { role: "assistant", content: "SYNTHETIC_TRANSPORT_ONLY" }, finish_reason: "stop" }],
                usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
            };
            if (!body.stream) return Response.json(completion);
            const chunk = { ...completion, object: "chat.completion.chunk",
                choices: [{ index: 0, delta: { role: "assistant", content: "SYNTHETIC_TRANSPORT_ONLY" }, finish_reason: null }] };
            const end = { ...chunk, choices: [{ index: 0, delta: {}, finish_reason: "stop" }] };
            return new Response(`data: ${JSON.stringify(chunk)}\n\ndata: ${JSON.stringify(end)}\n\ndata: [DONE]\n\n`,
                { headers: { "Content-Type": "text/event-stream" } });
        }
        async openWebSocket() {
            onAttempt("webSocket");
            throw new Error("synthetic-only witness does not forward WebSocket requests");
        }
    }();
}
