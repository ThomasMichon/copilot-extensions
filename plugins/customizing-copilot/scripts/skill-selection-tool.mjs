/** Opt-in callable integration. No connection, registration or inference on import. */
import { selectionSnapshot, selectionPrompt, selectionReader } from "./skill-selection-contract.mjs";
import { selectionSourcePolicy } from "./skill-selection-native.mjs";
import { collectHistory, safeError } from "./skill-selection-spike.mjs";

const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const failure = reason => ({ textResultForLlm: JSON.stringify({
    version: 1, status: "unavailable", reason,
}), resultType: "failure" });

function completed(report) {
    const profile = report?.requestedProfile;
    return report?.status === "completed" && report.cleanup?.runtimeStopped === true
        && Array.isArray(report.cleanup.errors) && report.cleanup.errors.length === 0
        && typeof profile?.model === "string" && !!profile.model
        && typeof profile.reasoningEffort === "string" && !!profile.reasoningEffort
        && ["default", "long_context"].includes(profile.contextTier)
        && [report.resolvedBefore, report.resolvedAfter].every(actual => actual?.modelId === profile.model
            && actual.reasoningEffort === profile.reasoningEffort && actual.contextTier === profile.contextTier)
        && Array.isArray(report.requests) && report.requests.length > 0
        && report.requests.every(request => request.forwarded === true
            && request.inspection?.canonicalSystemPromptPresent === true
            && request.inspection?.modelMatches === true
            && request.inspection?.readOnlyToolsetMatches === true
            && request.inspection?.nativeReadNamesMatch === true);
}

/**
 * The host supplies a qualified, owned runner with its explicit profile/lifecycle.
 * Model arguments cannot select a runtime, profile, source root or context strategy.
 */
export function createSelectionTool({ getSession, run, acquireSources = selectionSourcePolicy } = {}) {
    if (typeof getSession !== "function" || typeof run !== "function" || typeof acquireSources !== "function") {
        throw new Error("selection tool requires a requesting session and an explicitly bound owned runner");
    }
    let busy = false, unsettled = false;
    return {
        name: "select_skills",
        description: "Select registered skills and necessary declared guides for execution, planning or review. Advisory only; does not execute recommendations.",
        parameters: {
            type: "object",
            properties: {
                intent: { type: "string", minLength: 1, maxLength: 4000,
                    description: "Current objective and important constraints, not a catalog or hand-written skill shortlist." },
                purpose: { type: "string", enum: ["execution", "planning", "review"] },
            },
            required: ["intent", "purpose"], additionalProperties: false,
        },
        async handler(args, invocation) {
            if (busy || unsettled) return failure(unsettled
                ? "prior owned selector settlement is unconfirmed; host recovery required"
                : "a selection decision is already running");
            busy = true;
            let startedRunner = false, settled = false;
            const started = performance.now();
            const checkCancelled = () => {
                if (invocation?.signal?.aborted) throw new Error("selection decision cancelled");
            };
            try {
                const session = getSession();
                if (!session?.sessionId || invocation?.sessionId !== session.sessionId || invocation.agentId) {
                    throw new Error("selection tool requires its requesting foreground; child callers are not qualified");
                }
                checkCancelled();
                if (!args || Object.keys(args).sort().join() !== "intent,purpose") throw new Error("invalid selection arguments");
                const [beforeModel, beforeAgent, skills] = await Promise.all([
                    session.rpc.model.getCurrent(), session.rpc.agent.getCurrent(), session.rpc.skills.list(),
                ]);
                checkCancelled();
                const snapshot = selectionSnapshot(skills.skills);
                // Validate before reading history or acquiring source authority.
                selectionPrompt(snapshot, { ...args, context: {} });
                const context = await collectHistory(session.rpc.eventLog);
                checkCancelled();
                const sourcePolicy = await acquireSources(session, invocation.signal);
                if (!Array.isArray(sourcePolicy?.trustedRoots) || typeof sourcePolicy.authorizeSource !== "function") {
                    throw new Error("requesting-session source policy is not bound");
                }
                const reader = await selectionReader(snapshot, sourcePolicy.trustedRoots, {
                    maxBytes: 128000, maxReads: 12, authorizeSource: sourcePolicy.authorizeSource,
                });
                checkCancelled();
                const acquisitionMs = Math.round(performance.now() - started);
                const rootFailures = reader.rootFailures();
                const sourceContext = rootFailures.length ? { ...context, unavailableSourceRoots: rootFailures } : context;
                startedRunner = true;
                const report = await run({ snapshot, request: { ...args, context: sourceContext },
                    trustedRoots: sourcePolicy.trustedRoots, authorizeSource: sourcePolicy.authorizeSource,
                    sourceReader: reader,
                    signal: invocation.signal });
                settled = report?.cleanup?.runtimeStopped === true
                    && Array.isArray(report.cleanup.errors) && report.cleanup.errors.length === 0;
                checkCancelled();
                if (!completed(report)) throw new Error(report?.error ?? "owned selector evidence or settlement incomplete");
                const [afterModel, afterAgent, afterSkills] = await Promise.all([
                    session.rpc.model.getCurrent(), session.rpc.agent.getCurrent(), session.rpc.skills.list(),
                ]);
                checkCancelled();
                if (!same(beforeModel, afterModel) || !same(beforeAgent, afterAgent)) {
                    throw new Error("requesting-session model or selected agent changed during selection");
                }
                if (selectionSnapshot(afterSkills.skills).digest !== snapshot.digest
                    || report.catalogDigest !== snapshot.digest) throw new Error("native catalog changed during selection");
                const result = await reader.validate(report.result);
                checkCancelled();
                for (const item of result.recommendations) {
                    if (snapshot.catalog.find(skill => skill.name === item.skill)?.path
                        && !reader.receipts().some(receipt => receipt.skill === item.skill
                            && receipt.reference === undefined && receipt.catalogDigest === snapshot.digest
                            && receipt.bodyDigest && receipt.contentDigest)) {
                        throw new Error("recommended skill body was not read");
                    }
                }
                if (result.status === "no-match" && report.sourceFailures?.length) {
                    throw new Error("source acquisition failure cannot become a successful no-match decision");
                }
                return { textResultForLlm: JSON.stringify({
                    version: 1, status: "completed", result,
                    context: { source: "bounded-primary-text-sample", pages: context.pages,
                        omitted: context.omitted, truncated: context.truncated, inputBytes: context.inputBytes },
                    configuration: { requested: report.requestedProfile, before: report.resolvedBefore, after: report.resolvedAfter },
                    sourceFailures: report.sourceFailures ?? [],
                    unavailableSourceRoots: reader.rootFailures(),
                    usage: report.usage, latency: { acquisitionMs, ownedMs: report.elapsedMs,
                        totalMs: Math.round(performance.now() - started) },
                }), resultType: "success" };
            } catch (error) {
                return failure(safeError(error));
            } finally {
                unsettled ||= startedRunner && !settled;
                busy = false;
            }
        },
    };
}
