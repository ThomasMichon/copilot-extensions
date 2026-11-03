import test from "node:test";
import assert from "node:assert/strict";
import { join, resolve } from "node:path";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { createSelectionTool } from "../scripts/skill-selection-tool.mjs";
import { selectionSourcePolicy } from "../scripts/skill-selection-native.mjs";

function fixture() {
    const state = { skills: [{ name: "fixture", description: "A registered skill", enabled: true, source: "provider" }],
        model: { modelId: "parent", reasoningEffort: "high", contextTier: "long_context" }, runs: 0 };
    const session = { sessionId: "foreground", rpc: {
        model: { getCurrent: async () => structuredClone(state.model) },
        agent: { getCurrent: async () => ({ agent: null }) },
        skills: { list: async () => ({ skills: structuredClone(state.skills) }) },
        eventLog: { read: async () => ({ cursor: "tail", cursorStatus: "ok", hasMore: false,
            events: [{ id: "latest", type: "user.message", data: { content: "Current constraints" } }] }) },
    } };
    const profile = { model: "host-bound-candidate", reasoningEffort: "medium", contextTier: "long_context" };
    const actual = { modelId: profile.model, reasoningEffort: profile.reasoningEffort, contextTier: profile.contextTier };
    const report = input => ({
        status: "completed", catalogDigest: input.snapshot.digest, requestedProfile: profile,
        resolvedBefore: actual, resolvedAfter: actual, cleanup: { runtimeStopped: true, errors: [] },
        requests: [{ forwarded: true, inspection: { canonicalSystemPromptPresent: true, modelMatches: true,
            readOnlyToolsetMatches: true, nativeReadNamesMatch: true } }],
        result: { version: 1, catalogDigest: input.snapshot.digest, status: "selected",
            recommendations: [{ skill: "fixture", reason: "Owns current intent", confidence: "high", guides: [] }],
            uncertainties: [] },
        receipts: [], sourceFailures: [], usage: { calls: [], cost: { unit: "nano-AIU", value: null } }, elapsedMs: 1,
    });
    const policy = { trustedRoots: [], authorizeSource: async () => true };
    const options = { getSession: () => session, acquireSources: async () => policy,
        run: async input => { state.runs++; state.input = input; return report(input); } };
    const invocation = { sessionId: session.sessionId, signal: new AbortController().signal };
    const args = { intent: "Select guidance for current intent", purpose: "planning" };
    return { state, session, options, report, invocation, args, policy };
}
const resultOf = value => JSON.parse(value.textResultForLlm);

test("callable selector acquires native context and catalog without model, roots or strategy arguments", async () => {
    const f = fixture(), tool = createSelectionTool(f.options);
    const output = await tool.handler(f.args, f.invocation), result = resultOf(output);
    assert.equal(output.resultType, "success");
    assert.equal(result.result.recommendations[0].skill, "fixture");
    assert.equal(result.context.source, "bounded-primary-text-sample");
    assert.equal(f.state.input.request.context.messages[0].content, "Current constraints");
    assert.equal(f.state.input.snapshot.catalog[0].name, "fixture");
    assert.equal(f.state.input.authorizeSource, f.policy.authorizeSource);
    assert(!Object.hasOwn(f.state.input, "profile"));
    assert.deepEqual(Object.keys(tool.parameters.properties), ["intent", "purpose"]);
    assert(!Object.hasOwn(tool, "skipPermission"));
    assert.throws(() => createSelectionTool({ getSession: () => f.session }), /explicitly bound/);
    for (const [args, invocation] of [
        [{ ...f.args, model: "not-host-policy" }, f.invocation],
        [f.args, { ...f.invocation, agentId: "child" }],
        [f.args, { ...f.invocation, sessionId: "another" }],
    ]) {
        assert.equal((await tool.handler(args, invocation)).resultType, "failure");
    }
    assert.equal(f.state.runs, 1);
});

test("failed, stale or incomplete evidence never becomes a successful no-match decision", async () => {
    for (const variant of ["model", "tools", "catalog", "parent", "acquisition", "body", "unavailable"]) {
        const f = fixture();
        if (variant === "body") f.state.skills[0].path = resolve("SKILL.md");
        f.options.run = async input => {
            const report = f.report(input);
            if (variant === "model") report.resolvedAfter = { ...report.resolvedAfter, modelId: "fallback" };
            if (variant === "tools") report.requests[0].inspection.nativeReadNamesMatch = false;
            if (variant === "catalog") f.state.skills[0].enabled = false;
            if (variant === "parent") f.state.model.contextTier = "default";
            if (variant === "acquisition") {
                report.result.status = "no-match";
                report.result.recommendations = [];
                report.sourceFailures.push({ skill: "fixture", reason: "policy unavailable" });
            }
            if (variant === "unavailable") report.status = "unavailable";
            return report;
        };
        const result = resultOf(await createSelectionTool(f.options).handler(f.args, f.invocation));
        assert.equal(result.status, "unavailable", variant);
        assert(!Object.hasOwn(result, "result"), variant);
    }
    const f = fixture();
    f.options.run = async input => {
        const report = f.report(input);
        report.result.status = "no-match";
        report.result.recommendations = [];
        return report;
    };
    assert.equal(resultOf(await createSelectionTool(f.options).handler(f.args, f.invocation)).result.status, "no-match");
});

test("cancellation rejects late output, concurrent calls do not dispatch, and unknown settlement stays held", async () => {
    const f = fixture();
    const controller = new AbortController();
    let release, entered;
    const waiting = new Promise(resolve => { entered = resolve; });
    f.options.run = input => new Promise(resolve => {
        release = () => resolve(f.report(input)); entered();
    });
    const tool = createSelectionTool(f.options);
    const running = tool.handler(f.args, { ...f.invocation, signal: controller.signal });
    await waiting;
    assert.match(resultOf(await tool.handler(f.args, f.invocation)).reason, /already running/);
    controller.abort(); release();
    assert.match(resultOf(await running).reason, /cancelled/);
    assert.equal((await tool.handler(f.args, { ...f.invocation, sessionId: "other" })).resultType, "failure");
    for (const mode of ["throws", "unsettled"]) {
        const other = fixture();
        let calls = 0;
        other.options.run = async input => {
            calls++;
            if (mode === "throws") throw new Error("runner lost its terminal report");
            return { ...other.report(input), cleanup: { runtimeStopped: false, errors: ["unknown"] } };
        };
        const held = createSelectionTool(other.options);
        assert.equal((await held.handler(other.args, other.invocation)).resultType, "failure");
        assert.match(resultOf(await held.handler(other.args, other.invocation)).reason, /settlement is unconfirmed/);
        assert.equal(calls, 1);
    }
});

test("source-policy failure before dispatch permits recovery but never invokes an unbound runner", async () => {
    const f = fixture();
    let failed = true;
    f.options.acquireSources = async () => {
        if (failed) throw new Error("source policy unavailable");
        return f.policy;
    };
    const tool = createSelectionTool(f.options);
    assert.equal((await tool.handler(f.args, f.invocation)).resultType, "failure");
    assert.equal(f.state.runs, 0);
    failed = false;
    assert.equal((await tool.handler(f.args, f.invocation)).resultType, "success");
});

test("source access and bytes are revalidated after owned cleanup and final parent checks", async t => {
    const root = await mkdtemp(join(tmpdir(), "selector-return-policy-"));
    t.after(() => rm(root, { recursive: true, force: true }));
    for (const mode of ["revoked-during-cleanup", "changed-during-cleanup", "revoked-at-final-check"]) {
        const f = fixture(), path = join(root, `${mode}.md`);
        await writeFile(path, "Current source");
        f.state.skills[0].path = path;
        let allowed = true, modelReads = 0;
        f.options.acquireSources = async () => ({
            trustedRoots: [root], authorizeSource: async () => allowed,
        });
        if (mode === "revoked-at-final-check") {
            f.session.rpc.model.getCurrent = async () => {
                if (++modelReads > 1) allowed = false;
                return structuredClone(f.state.model);
            };
        }
        f.options.run = async input => {
            await input.sourceReader.read({ skill: "fixture" });
            const report = f.report(input);
            await input.sourceReader.validate(report.result);
            report.receipts = input.sourceReader.receipts();
            if (mode === "revoked-during-cleanup") allowed = false;
            if (mode === "changed-during-cleanup") await writeFile(path, "Changed source");
            return report;
        };
        const output = await createSelectionTool(f.options).handler(f.args, f.invocation);
        assert.equal(output.resultType, "failure", mode);
        assert.match(resultOf(output).reason, /access not admitted|source changed before/, mode);
    }
});

test("native grants and exclusion checks are independent, current and fail closed", async () => {
    const root = resolve("granted"), path = resolve(root, "SKILL.md");
    const state = { allowed: true, approved: [path], available: true, excluded: false, checks: 0 };
    const session = { rpc: {
        permissions: { paths: {
            list: async () => ({ primary: root, directories: [root], sessionApprovedPaths: state.approved }),
            isPathWithinAllowedDirectories: async () => ({ allowed: state.allowed }),
        } },
        contentExclusion: { checkPaths: async ({ paths }) => {
            state.checks++;
            return { available: state.available, checks: state.available
                ? paths.map(path => ({ path, excluded: state.excluded })) : [] };
        } },
    } };
    const controller = new AbortController();
    const policy = await selectionSourcePolicy(session, controller.signal);
    assert.deepEqual(policy.trustedRoots, [root]);
    assert.equal(await policy.authorizeSource(path), true);
    state.allowed = false;
    assert.equal(await policy.authorizeSource(path), true);
    state.approved = [];
    await assert.rejects(policy.authorizeSource(path), /access denied/);
    assert.equal(state.checks, 2);
    state.allowed = true; state.excluded = true;
    await assert.rejects(policy.authorizeSource(path), /source excluded/);
    state.excluded = false; state.available = false;
    await assert.rejects(policy.authorizeSource(path), /policy unavailable/);
    state.available = true;
    session.rpc.contentExclusion.checkPaths = async () => ({ available: true, checks: [] });
    await assert.rejects(policy.authorizeSource(path), /policy unavailable/);
    controller.abort();
    await assert.rejects(policy.authorizeSource(path), /cancelled/);
});
