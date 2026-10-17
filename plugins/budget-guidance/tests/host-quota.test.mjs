import assert from "node:assert/strict";
import { cp, copyFile, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createQuotaTool } from "../extensions/budget-guidance/session-quota.mjs";
import { boundedJson } from "../extensions/budget-guidance/quota-projection.mjs";

const invocation = { sessionId: "session-example" };
const secret = "sensitive-sentinel";
const projection = () => ({
    isTbbUser: true, token: secret,
    snapshots: { premium_interactions: {
        isUnlimitedEntitlement: false, entitlementRequests: 1000, usedRequests: 250,
        tokenBasedBilling: true, resetDateEpochMs: 1896134400000,
        hasQuota: true, upgradeUrl: secret,
    } },
});
const identity = () => ({
    isAuthenticated: true, login: "Example_User", host: "https://github.com",
    token: secret, statusMessage: secret, authType: secret, plan: secret,
});
const accountArgs = { expectedLogin: "example_user", expectedHost: "https://GITHUB.com:443/" };
function fixture() {
    const calls = [];
    const forbidden = () => { throw new Error("forbidden lifecycle mutation"); };
    const session = {
        sessionId: invocation.sessionId,
        rpc: {
            quota: {
                async get() { calls.push("quota"); return projection(); },
                refresh: forbidden, takeWarnings: forbidden,
            },
            gitHubAuth: {
                async getStatus() { calls.push("status"); return identity(); },
                setCredentials: forbidden,
            },
        },
        abort: forbidden, destroy: forbidden, disconnect: forbidden,
    };
    Object.defineProperty(session, "connection", { get: forbidden });
    Object.defineProperty(session, "auth", { get: forbidden });
    const tool = createQuotaTool(() => session);
    const read = async (args = {}, context = invocation) => {
        const response = await tool.handler(args, context);
        assert(Buffer.byteLength(response.textResultForLlm, "utf8") <= 65536);
        assert(!response.textResultForLlm.includes(secret));
        const body = JSON.parse(response.textResultForLlm);
        assert.equal(response.resultType, body.status === "captured" ? "success" : "failure");
        return body;
    };
    return { calls, session, tool, read };
}
function withheld(result, diagnostic) {
    assert.equal(result.capture, null);
    assert.notEqual(result.status, "captured");
    if (diagnostic) assert.deepEqual(result.diagnostics, [diagnostic]);
}

test("minimal session capture is allowlisted and offline-only", async () => {
    const f = fixture();
    const result = await f.read();
    assert.equal(result.status, "captured");
    assert.deepEqual(f.calls, ["quota"]);
    assert.equal(result.capture.runtime_version, "unknown");
    assert.equal(result.capture.session_id, invocation.sessionId);
    assert.equal(result.freshness, "unknown");
    assert.equal(result.raw_reset, "unsupported");
    assert.deepEqual(Object.keys(result.capture.projection).sort(), ["isTbbUser", "snapshots"]);
    assert.equal(result.capture.projection.snapshots.premium_interactions.usedRequests, 250);
    assert(!("calculated" in result));
});

test("credential-free identity reads bracket quota, retaining non-atomic scope", async () => {
    const f = fixture();
    const result = await f.read(accountArgs);
    assert.deepEqual(f.calls, ["status", "quota", "status"]);
    assert.deepEqual(result.account_identity, {
        status: "matched", scope: "session-github-auth", login: "example_user",
        host: "https://github.com", quota_binding: "not-atomic",
    });
});

test("actual extension entry joins once with no startup read or extra authority", async (t) => {
    const root = await mkdtemp(join(tmpdir(), "budget-host-quota-"));
    t.after(() => rm(root, { recursive: true }));
    await cp(fileURLToPath(new URL("../extensions/budget-guidance/", import.meta.url)),
        join(root, "extensions"), { recursive: true });
    const sdkRoot = join(root, "node_modules", "@github", "copilot-sdk");
    await mkdir(sdkRoot, { recursive: true });
    await writeFile(join(sdkRoot, "package.json"), JSON.stringify({
        type: "module", exports: { "./extension": "./extension.mjs" },
    }));
    await copyFile(fileURLToPath(new URL("./fixtures/host-sdk.mjs", import.meta.url)),
        join(sdkRoot, "extension.mjs"));
    const sdk = await import(pathToFileURL(join(sdkRoot, "extension.mjs")).href);
    await import(pathToFileURL(join(root, "extensions", "extension.mjs")).href);
    assert.equal(sdk.state.joins, 1);
    assert.deepEqual(Object.keys(sdk.state.options), ["tools"]);
    assert.deepEqual(sdk.state.calls, []);
    const tool = sdk.state.options.tools[0];
    assert.equal(tool.name, "budget_guidance_session_quota");
    const response = await tool.handler(accountArgs, invocation);
    const result = JSON.parse(response.textResultForLlm);
    assert.equal(result.status, "captured");
    assert.deepEqual(sdk.state.calls, ["status", "quota", "status"]);
    assert(!response.textResultForLlm.includes(secret));
    // The outer Python test feeds this actual entry-point capture to the CLI.
    console.log("ENTRY_CAPTURE=" + JSON.stringify(result.capture));
    sdk.state.stringQuantity = true;
    const rejected = JSON.parse((await tool.handler({}, invocation)).textResultForLlm);
    withheld(rejected, "quota-projection-invalid");
    console.log("ENTRY_STRING_RESULT=" + JSON.stringify(rejected));
});

test("old SDK shapes and missing invocation identity never read quota", async () => {
    for (const shape of [undefined, {}, { quota: {} }, { quota: { get: true } }]) {
        const f = fixture();
        f.session.rpc = shape;
        withheld(await f.read(), "session-quota-rpc-unsupported");
        assert.deepEqual(f.calls, []);
    }
    for (const context of [undefined, {}, { sessionId: "" }, { sessionId: "other" }]) {
        const f = fixture();
        withheld(JSON.parse((await f.tool.handler({}, context)).textResultForLlm));
        assert.deepEqual(f.calls, []);
    }
    const f = fixture();
    delete f.session.rpc.gitHubAuth;
    withheld(await f.read(accountArgs), "session-github-auth-status-unsupported");
    assert.deepEqual(f.calls, []);
});

test("invalid arguments fail before any read, including paired identity and upper deadline", async () => {
    for (const args of [
        null, [], { timeoutSeconds: true }, { timeoutSeconds: 0 }, { timeoutSeconds: 31 },
        { timeoutSeconds: null }, { category: null },
        { timeoutSeconds: NaN }, { timeoutSeconds: Infinity }, { category: "" },
        { quotaId: "unpaired" }, { expectedLogin: "example-user" },
        { expectedHost: "https://github.com" }, { expectedLogin: "bad/login", expectedHost: "x" },
        { ...accountArgs, expectedHost: "http://github.com" },
        { ...accountArgs, expectedHost: "https://user:pass@github.com" },
        { ...accountArgs, expectedHost: "https://github.com/path" },
        { ...accountArgs, expectedHost: "https://github.com?secret" },
        { ...accountArgs, expectedHost: "https://github.com#secret" },
        { ...accountArgs, expectedHost: "https://github.com:444" },
        { ...accountArgs, expectedHost: "https://github.com/private/.." },
        { ...accountArgs, expectedHost: "https://github.com?" },
        { ...accountArgs, expectedHost: "https://github.com#" },
        { ...accountArgs, expectedHost: "https://99999999999" },
        { ...accountArgs, expectedHost: "https://git\nhub.com" },
        { session_owned: true }, { providerId: "x".repeat(257) },
    ]) {
        const f = fixture();
        withheld(await f.read(args), "quota-arguments-invalid");
        assert.deepEqual(f.calls, []);
    }
});

test("unexpected argument processing errors have a distinct sanitized diagnostic", async () => {
    const f = fixture();
    const input = new Proxy({}, { ownKeys() { throw new Error(secret); } });
    withheld(await f.read(input), "quota-arguments-error");
    assert.deepEqual(f.calls, []);
});

test("quantity types match the existing number-only capture reader", async () => {
    for (const [value, valid] of [
        [1000, true], [1000000000000000000, true], [0.25, true],
        [1000000000000000100, false],
        ["1000", false], ["1000000000000000000", false], ["1000000000000000000.000", false],
        ["999999999999999999.999999999999999999", false],
        ["-1000000000000000000", false], ["0000000000000000001", false],
        ["1000000000000000001", false], ["1000000000000000000.000000000000000001", false],
        ["-1000000000000000001", false], ["-1000000000000000000.1", false],
    ]) {
        const f = fixture();
        f.session.rpc.quota.get = async () => {
            const raw = projection();
            raw.snapshots.premium_interactions.entitlementRequests = value;
            return raw;
        };
        const result = await f.read();
        if (valid) {
            assert.equal(result.status, "captured", value);
            assert.equal(result.capture.projection.snapshots.premium_interactions.entitlementRequests, value);
        } else {
            withheld(result, "quota-projection-invalid");
        }
    }
});

test("account mismatch, unauthenticated and failed reads withhold all counters", async () => {
    for (const status of [
        {}, null, { ...identity(), isAuthenticated: false },
        { ...identity(), login: "someone-else" }, { ...identity(), host: "https://example.com" },
        { ...identity(), host: "invalid" }, { ...identity(), login: null },
        { ...identity(), opaque: secret.repeat(10000) },
    ]) {
        const f = fixture();
        f.session.rpc.gitHubAuth.getStatus = async () => status;
        withheld(await f.read(accountArgs));
        assert.deepEqual(f.calls, []);
    }
    for (const where of ["quota", "status-before", "status-after"]) {
        const f = fixture();
        let statuses = 0;
        f.session.rpc.gitHubAuth.getStatus = async () => {
            statuses++;
            if ((where === "status-before" && statuses === 1)
                || (where === "status-after" && statuses === 2)) throw new Error(secret);
            return identity();
        };
        if (where === "quota") f.session.rpc.quota.get = async () => { throw new Error(secret); };
        withheld(await f.read(accountArgs), "quota-acquisition-error");
    }
});

test("session/rpc and observed account changes during either read never publish", async () => {
    for (const mutation of [
        (f) => { f.session.sessionId = "changed"; },
        (f) => { f.session.rpc = {}; },
        (f) => { f.session.rpc.quota = {}; },
    ]) {
        for (const stage of ["before", "quota", "after"]) {
            const f = fixture();
            let count = 0;
            f.session.rpc.gitHubAuth.getStatus = async () => {
                count++;
                if ((stage === "before" && count === 1) || (stage === "after" && count === 2)) {
                    mutation(f);
                }
                return identity();
            };
            f.session.rpc.quota.get = async () => {
                if (stage === "quota") mutation(f);
                return projection();
            };
            withheld(await f.read(accountArgs), "requesting-session-changed-during-acquisition");
        }
    }
    const f = fixture();
    let count = 0;
    f.session.rpc.gitHubAuth.getStatus = async () =>
        ({ ...identity(), login: ++count === 1 ? "example_user" : "changed" });
    withheld(await f.read(accountArgs));
});

test("one total deadline holds the slot until uncancellable read settles; no late publication", async () => {
    for (const stage of ["before", "quota", "after"]) {
        const f = fixture();
        let release;
        const blocked = new Promise((resolve) => { release = resolve; });
        let statuses = 0;
        f.session.rpc.gitHubAuth.getStatus = async () => {
            f.calls.push("status");
            statuses++;
            if ((stage === "before" && statuses === 1) || (stage === "after" && statuses === 2)) {
                await blocked;
            }
            return identity();
        };
        f.session.rpc.quota.get = async () => {
            f.calls.push("quota");
            if (stage === "quota") await blocked;
            return projection();
        };
        const result = await f.read({ ...accountArgs, timeoutSeconds: 0.02 });
        withheld(result, "quota-acquisition-timeout");
        const calls = [...f.calls];
        withheld(await f.read(accountArgs), "quota-acquisition-already-pending");
        assert.deepEqual(f.calls, calls);
        release();
        await new Promise((resolve) => setImmediate(resolve));
        assert.deepEqual(f.calls, calls);
        assert.deepEqual(result.diagnostics, ["quota-acquisition-timeout"]);
        assert.equal((await f.read(accountArgs)).status, "captured");
    }
});

test("all RPCs share a deadline, not one allowance per read", async () => {
    const f = fixture();
    f.session.rpc.gitHubAuth.getStatus = async () => {
        await new Promise((resolve) => setTimeout(resolve, 30));
        return identity();
    };
    withheld(await f.read({ ...accountArgs, timeoutSeconds: 0.05 }), "quota-acquisition-timeout");
    await new Promise((resolve) => setTimeout(resolve, 50));
});

test("oversize nested metadata and invalid quota values never leak even on success path", async () => {
    for (const raw of [
        null, [], { ...projection(), opaque: secret.repeat(10000) },
        { ...projection(), opaque: "💡".repeat(17000) },
        { ...projection(), snapshots: [] },
        { ...projection(), snapshots: { premium_interactions: { usedRequests: secret } } },
        { ...projection(), snapshots: { premium_interactions: { hasQuota: "true" } } },
        { ...projection(), snapshots: { premium_interactions: { usedRequests: Infinity } } },
    ]) {
        const f = fixture();
        f.session.rpc.quota.get = async () => raw;
        withheld(await f.read());
    }
});

test("bounded serialization stops early and counts actual JSON escapes and UTF-8 bytes", async () => {
    for (const value of [
        null, true, 0.25, -0, Infinity, "a\"\\\n\u0001", "💡é界",
        String.fromCharCode(0xd800), String.fromCharCode(0xdc00),
        { a: "text", omitted: undefined, array: [1, undefined, null] },
    ]) {
        assert.equal(boundedJson(value), JSON.stringify(value));
    }
    assert.equal(Buffer.byteLength(boundedJson("x".repeat(65534))), 65536);
    assert.throws(() => boundedJson("x".repeat(65535)), /capture-size-limit/u);
    assert.equal(Buffer.byteLength(boundedJson("💡".repeat(16383) + "xx")), 65536);
    assert.throws(() => boundedJson("💡".repeat(16384)), /capture-size-limit/u);
    assert.throws(() => boundedJson("\u0001".repeat(10923)), /capture-size-limit/u);
    const f = fixture();
    const raw = { ...projection(), huge: "x".repeat(1000000) };
    Object.defineProperty(raw, "later", {
        enumerable: true,
        get() { throw new Error("oversize traversal continued"); },
    });
    f.session.rpc.quota.get = async () => raw;
    withheld(await f.read(), "capture-size-limit");
    const cyclic = {};
    cyclic.self = cyclic;
    f.session.rpc.quota.get = async () => cyclic;
    withheld(await f.read(), "quota-projection-invalid");
    let nested = {};
    for (let index = 0; index < 66; index++) nested = { child: nested };
    f.session.rpc.quota.get = async () => nested;
    withheld(await f.read(), "capture-depth-limit");
});

test("selected provider evidence is filtered, preserving business and reset diagnostics", async () => {
    const f = fixture();
    const state = {
        provider: { id: "provider-example", label: secret }, quotaId: "quota-example",
        compatibilityKey: "premium_interactions", acquisitionStatus: "succeeded",
        observationKind: "account_snapshot", observedAt: "2030-01-10T00:00:00Z",
        accessState: "allowed", capacityState: "available", quantityKind: "authoritative_budget",
        unit: "ai_credits", hasQuota: true, entitledQuantity: 1000, availableQuantity: 750,
        budgetMetadata: { entitlement: 1000, consumed: 250, unlimited: false,
            resetAtEpochMs: 1896134400000, resetEstimated: false, token: secret },
        monthlyUsage: { state: "available", scope: "user", unit: "ai_credits",
            consumedQuantity: 250, cycleStart: "2030-01-01T00:00:00Z",
            queriedAt: "2030-01-10T00:00:00Z", resetOn: "2030-02-01T00:00:00Z",
            opaque: secret },
        source: secret, acquisitionError: secret,
    };
    f.session.rpc.quota.get = async () => ({ ...projection(), providerQuotas: [state] });
    const args = { providerId: "provider-example", quotaId: "quota-example" };
    const result = await f.read(args);
    const selected = result.capture.projection.providerQuotas[0];
    assert.equal(selected.quantityKind, "authoritative_budget");
    assert.deepEqual(selected.provider, { id: "provider-example" });
    assert.equal(selected.budgetMetadata.resetAtEpochMs, 1896134400000);
    assert.equal(selected.monthlyUsage.consumedQuantity, 250);
    console.log("PROVIDER_CAPTURE=" + JSON.stringify(result.capture));
    assert(!("providerQuotas" in (await f.read()).capture.projection));
    state.observationKind = "unknown";
    withheld(await f.read(args), "quota-projection-invalid");
    state.observationKind = "account_snapshot";
    state.quantityKind = secret;
    withheld(await f.read(args), "quota-projection-invalid");
    state.quantityKind = "none";
    state.capacityState = "not_applicable";
    assert.equal((await f.read(args)).status, "captured");
    f.session.rpc.quota.get = async () => ({ providerQuotas: [state, state] });
    withheld(await f.read(args), "provider-quota-ambiguous");
});
