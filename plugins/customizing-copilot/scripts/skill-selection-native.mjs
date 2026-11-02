/** Read-only native host preflight. Importing does not join a session or dispatch inference. */
import { createHash } from "node:crypto";
import { selectionSnapshot, selectionReadTool, selectionAgent } from "./skill-selection-contract.mjs";
import { catalogSummary, collectHistory } from "./skill-selection-spike.mjs";

const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const digest = value => createHash("sha256").update(value).digest("hex");
const parentConfig = model => Object.fromEntries([
    "modelId", "providerId", "planBaseModelId", "planBaseProviderId", "reasoningEffort",
    "contextTier", "autoTier", "pendingAutoTier", "activatingAutoTier",
].map(key => [key, model[key] ?? null]));

/** Inspect owned fixture input, never a foreground request or a saved capture. */
export function canonicalRequestEvidence(body, profile) {
    const expected = selectionAgent(profile);
    const messages = Array.isArray(body?.messages) ? body.messages : Array.isArray(body?.input) ? body.input : [];
    const content = value => typeof value === "string" ? value : Array.isArray(value)
        ? value.map(part => typeof part?.text === "string" ? part.text : "").join("") : "";
    const instructions = [content(body?.instructions), ...messages
        .filter(message => ["system", "developer"].includes(message.role)).map(message => content(message.content))];
    const tools = Array.isArray(body?.tools) ? body.tools : [];
    return {
        canonicalSystemPromptPresent: instructions.some(text => text.includes(expected.prompt)),
        modelMatches: typeof profile.model === "string" && body?.model === profile.model,
        readOnlyToolsetMatches: tools.length === 1 && tools[0].type === "function"
            && (tools[0].function?.name ?? tools[0].name) === selectionReadTool,
    };
}

/** Inspect declarations without treating them as resolved child configuration. */
export async function inspectSelectionHost(session, profile, { readContext = false } = {}) {
    if (!profile || typeof profile.agentName !== "string" || !profile.agentName
        || !["default", "long_context"].includes(profile.contextTier) || typeof readContext !== "boolean") {
        throw new Error("native selector preflight requires an explicit profile");
    }
    if (!profile.model || !profile.reasoningEffort) throw new Error("native selector profile is incomplete");
    const expected = selectionAgent({ model: profile.model, reasoningEffort: profile.reasoningEffort });
    const [before, beforeAgent] = await Promise.all([
        session.rpc.model.getCurrent(), session.rpc.agent.getCurrent(),
    ]);
    if (typeof before.modelId !== "string" || !before.modelId) {
        throw new Error("foreground model configuration unavailable");
    }
    const [nativeSkills, nativeAgents] = await Promise.all([
        session.rpc.skills.list(),
        session.rpc.agent.list({ includeBuiltInAgents: true, includePrompt: true }),
    ]);
    if (!Array.isArray(nativeSkills.skills) || !Array.isArray(nativeAgents.agents)) {
        throw new Error("native selection catalog unavailable");
    }
    const snapshot = selectionSnapshot(nativeSkills.skills);
    const candidates = nativeAgents.agents.filter(agent => agent.name === profile.agentName);
    const agent = candidates.length === 1 ? candidates[0] : null;
    const failures = [];
    if (!agent?.id || nativeAgents.agents.filter(item => item.id === agent.id).length !== 1) {
        failures.push("canonical specialist native ID unavailable or ambiguous");
    }
    if (!agent || !same(agent.tools, [selectionReadTool])) failures.push("specialist read-only tool declaration differs");
    if (agent?.model !== profile.model) failures.push("specialist authored model differs");
    if (agent?.reasoningEffort !== profile.reasoningEffort) failures.push("specialist authored reasoning effort differs");
    const promptAvailable = typeof agent?.prompt === "string";
    if (!promptAvailable) failures.push("canonical specialist prompt unavailable");
    else if (agent.prompt !== expected.prompt) failures.push("canonical specialist prompt differs");
    const context = readContext ? await collectHistory(session.rpc.eventLog) : null;
    const [after, afterAgent] = await Promise.all([
        session.rpc.model.getCurrent(), session.rpc.agent.getCurrent(),
    ]);
    if (!same(parentConfig(before), parentConfig(after))
        || (beforeAgent.agent?.id ?? null) !== (afterAgent.agent?.id ?? null)) {
        throw new Error("foreground model or selected agent changed during native selection preflight");
    }
    return {
        schema: "copilot-extensions.skill-selection-native-preflight", version: 1,
        status: failures.length ? "unavailable" : "declared",
        catalog: catalogSummary(snapshot.catalog),
        nativeAgentCount: nativeAgents.agents.length,
        nativeAgentScope: "observed-joined-rpc-view",
        agent: agent ? { id: agent.id, name: agent.name, model: agent.model ?? null,
            reasoningEffort: agent.reasoningEffort ?? null, tools: agent.tools ?? null,
            source: agent.source ?? null, pathAvailable: typeof agent.path === "string" && !!agent.path,
            declaredPrompt: {
                available: promptAvailable,
                bytes: promptAvailable ? Buffer.byteLength(agent.prompt) : null,
                digest: promptAvailable ? digest(agent.prompt) : null,
                expectedBytes: Buffer.byteLength(expected.prompt), expectedDigest: digest(expected.prompt),
                matchesCanonical: promptAvailable ? agent.prompt === expected.prompt : null,
            } } : null,
        parent: { ...parentConfig(after), selectedAgentId: afterAgent.agent?.id ?? null,
            modelAndAgentUnchangedDuringPreflight: true },
        requestedChild: { model: profile.model, reasoningEffort: profile.reasoningEffort,
            contextTier: profile.contextTier },
        child: { resolvedModel: null, reasoningEffort: null, contextTier: null,
            effectiveTools: null, prospectiveContextFit: null },
        context: context ? { pages: context.pages, omitted: context.omitted,
            truncated: context.truncated, messages: context.messages.length,
            inputBytes: context.inputBytes, boundary: "bounded-sample-not-frozen",
            contentsReturned: false } : null,
        failures,
        limitations: [
            "Authored model and tools are declarations, not effective child evidence.",
            "The task and custom-agent contracts do not accept a child context tier.",
            "Resolved child configuration requires attributed native task events.",
            "The extension has no public server client for native history forking.",
            "Bounded supplied context does not bound the assembled inherited child context.",
            "The joined agent listing does not establish preservation or removal of unrelated agents.",
        ],
        inferenceDispatched: false, qualityEvidence: false, comparable: false,
    };
}
