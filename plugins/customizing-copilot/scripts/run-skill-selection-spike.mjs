/** Opt-in owned SDK witness. Never load this file as a foreground extension. */
import { mkdir, stat } from "node:fs/promises";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";
import { SENTINELS, bytes, spikeInputs, freezeCatalog, collectHistory, syntheticHandler,
    modelCatalogSummary, runtimeEnvironment, observeOwnTask, deadline, safeError,
    spikeStatus, armStatus, catalogMatches, historyMatches, catalogSummary } from "./skill-selection-spike.mjs";

let sdkDirectory, root, runtimePath, modelsOnly = false, catalogOnly = false;
let skillDirectories = [], profile = {}, home, workspace;
let sdk, client, config;
const requests = [], sessions = [], taskOwners = [], owned = new Set();
let heldRequestResolve;
const heldRequest = new Promise(resolve => { heldRequestResolve = resolve; });
const report = {
    schema: "copilot-extensions.skill-selection-spike", version: 1,
    status: "unavailable",
    mode: "unavailable",
    requestedProfile: profile,
    liveInferenceDispatched: false, qualityEvidence: false,
    native: {
        startup: { status: "not-reached" },
        catalog: { status: "not-reached" }, models: { status: "not-reached" },
        agentIds: null, capabilities: null,
    },
    arms: Object.fromEntries(["fullFork", "boundaryFork", "boundedHistory", "explicitBrief",
        "nativeTask", "cancellation"].map(name => [name, { status: "not-reached" }])),
    limitations: [
        "Synthetic replies/usage do not qualify model quality, price, context capacity or savings.",
        "Joined extensions expose no public server client for forks; this witness owns a separate runtime.",
        "Tasks have no documented isolation, context-tier, effort or schema parameters.",
        "No production selector, main preference, live inference or model qualification is installed.",
    ],
};
const fixtureSkills = [
    { name: "synthetic-review", description: "Review the synthetic fixture without changing it.", userInvocable: false },
    { name: "synthetic-disabled", description: "A disabled negative-control descriptor." },
    { name: "synthetic-long", description: "Full native descriptor. ".repeat(32) },
];
const providerCalls = { list: 0, read: 0 };
const provider = {
    listSkills: () => { providerCalls.list++; return fixtureSkills; },
    readSkill: () => { providerCalls.read++; throw new Error("spike must not fetch skill bodies"); },
};
function sessionConfig() { return {
    ...profile, ...(profile.model ? { allowedModels: [profile.model] } : {}),
    workingDirectory: workspace, configDirectory: home, enableConfigDiscovery: false,
    availableTools: catalogOnly ? [] : ["synthetic_catalog_read"], enableSkills: true,
    ...(catalogOnly ? { skillDirectories, enableFileHooks: false, mcpServers: {}, tools: [], customAgents: [] }
        : { skillProvider: provider, disabledSkills: ["synthetic-disabled"] }),
    remoteSession: "off",
    infiniteSessions: { enabled: false }, requestExtensions: false,
    ...(!catalogOnly ? { tools: [{
        name: "synthetic_catalog_read", description: "Read-only fixture catalog. No execution or delegation.",
        parameters: { type: "object", properties: {} },
        handler: () => "synthetic read-only fixture",
    }],
    customAgents: [{
        name: "synthetic-selector", description: "Synthetic transport witness only.",
        prompt: "Use only the synthetic catalog read tool. Do not execute, delegate or read skill bodies.",
        tools: ["synthetic_catalog_read"], infer: false,
        ...(profile.model ? { model: profile.model } : {}),
        ...(profile.reasoningEffort ? { reasoningEffort: profile.reasoningEffort } : {}),
    }] } : {}),
}; }
async function create() {
    const session = await client.createSession(config);
    sessions.push(session); owned.add(session.sessionId);
    return session;
}
async function turn(session, prompt) {
    const start = requests.length;
    const started = performance.now();
    await session.sendAndWait({ prompt }, 12000);
    return { suppliedBytes: bytes(prompt), elapsedMs: Math.round(performance.now() - started),
        model: await deadline(session.rpc.model.getCurrent()),
        requests: requests.slice(start).filter(item => item.sessionId === session.sessionId).map(item => item.summary) };
}
async function attempt(name, action) {
    try {
        const result = await action();
        report.arms[name] = { ...result, status: armStatus(name, result) };
    }
    catch (error) { report.arms[name] = { status: "unavailable", error: safeError(error) }; }
}
try {
    ({ sdkDirectory, root, runtimePath, modelsOnly, catalogOnly, skillDirectories, profile } = spikeInputs(process.argv.slice(2)));
    for (const directory of skillDirectories) {
        if (!(await stat(directory)).isDirectory()) throw new Error("explicit skill root is not a directory");
    }
    home = join(root, "owned-home"); workspace = join(root, "synthetic-workspace");
    config = sessionConfig();
    report.mode = modelsOnly ? "model-enumeration" : catalogOnly ? "catalog-discovery" : "synthetic-transport";
    report.requestedProfile = profile;
    Object.assign(report, modelsOnly ? { vendorModelEnumeration: true } : { modelFree: true, forwardedModelRequests: 0 });
    await mkdir(root); // Refuse reuse of existing session state.
    await mkdir(home);
    await mkdir(workspace);
    sdk = await import(pathToFileURL(join(resolve(sdkDirectory), "index.js")).href);
    client = new sdk.CopilotClient({
        mode: "empty", baseDirectory: home, workingDirectory: workspace,
        env: runtimeEnvironment(process.env),
        connection: sdk.RuntimeConnection.forStdio({
            ...(runtimePath ? { path: runtimePath } : {}),
            args: ["--no-auto-update", "--no-remote"],
        }),
        ...(!modelsOnly ? {
            requestHandler: syntheticHandler(sdk.CopilotRequestHandler, owned, requests, heldRequestResolve),
        } : {}),
        useLoggedInUser: true, logLevel: "none",
    });
    const startup = performance.now();
    await client.start();
    report.native.startup = { status: "started" };
    report.native.startupMs = Math.round(performance.now() - startup);
    if (modelsOnly) {
        const models = await deadline(client.listModels());
        report.native.models = {
            status: "available", ...modelCatalogSummary(models),
        };
        report.status = "preflight-only";
    } else if (catalogOnly) {
        const parent = await create();
        const catalog = freezeCatalog((await deadline(parent.rpc.skills.list())).skills);
        report.native.catalog = {
            status: catalog.length ? "observed" : "empty", ...catalogSummary(catalog),
            explicitRootCount: skillDirectories.length,
            foregroundScopeEquivalent: null,
        };
        report.native.agentIds = (await deadline(parent.rpc.agent.list())).agents.map(agent => agent.id);
        report.native.capabilities = Object.fromEntries(Object.entries(parent.capabilities)
            .filter(([, value]) => typeof value === "boolean"));
        report.status = catalog.length ? "catalog-only" : "incomplete";
    } else {
        const parent = await create();
        report.native.capabilities = Object.fromEntries(Object.entries(parent.capabilities)
            .filter(([, value]) => typeof value === "boolean"));
        const catalog = freezeCatalog((await deadline(parent.rpc.skills.list())).skills);
        report.native.catalog = {
            status: catalogMatches(catalog, fixtureSkills, config.disabledSkills) ? "available" : "incomplete",
            count: catalog.length, bytes: bytes(catalog),
            disabled: catalog.filter(skill => !skill.enabled).length,
            pathless: catalog.filter(skill => !skill.path).length,
            fixtureIds: catalog.filter(skill => skill.name.startsWith("synthetic-")).map(skill => skill.name),
            longDescriptorComplete: catalog.some(skill => skill.name === "synthetic-long"
                && skill.description === fixtureSkills[2].description),
            nonUserInvocableEnabled: catalog.some(skill => skill.name === "synthetic-review"
                && skill.enabled && skill.userInvocable === false),
        };
        report.native.agentIds = (await deadline(parent.rpc.agent.list())).agents.map(agent => agent.id);
        try {
            const models = await deadline(parent.rpc.model.list());
            report.native.models = modelCatalogSummary(models.list);
        } catch (error) { report.native.models = { status: "unavailable", error: safeError(error) }; }
        const before = await deadline(parent.rpc.model.getCurrent());
        report.native.sourceModel = before;
        try {
            await turn(parent, SENTINELS.old + " " + "Synthetic historical padding. ".repeat(400));
            await turn(parent, SENTINELS.recent + " The requested action is read-only review.");
            await turn(parent, SENTINELS.boundary + " This later message is outside the exclusive prefix.");
        } catch (error) {
            report.native.seed = { status: "unavailable", error: safeError(error) };
            throw new Error("synthetic source seeding unavailable; no alternate live inference");
        }
        const page = await deadline(parent.rpc.eventLog.read({
            direction: "backward", max: 32, types: ["user.message", "assistant.message"],
            agentScope: "primary", includeEphemeral: false,
        }));
        if (page.cursorStatus !== "ok" || page.hasMore) throw new Error("synthetic source freeze incomplete");
        const boundary = page.events.find(event => event.type === "user.message" && event.data.content.includes(SENTINELS.boundary))?.id;
        if (!boundary) throw new Error("synthetic exclusive boundary missing");
        const prefix = page.events.slice(0, page.events.findIndex(event => event.id === boundary));
        const prefixIds = prefix.map(event => event.id);
        const frozenIds = page.events.map(event => event.id);
        report.native.frozenTextEvents = prefixIds.length;
        const prompt = SENTINELS.brief + "\nReturn synthetic guidance only.\nFrozen native catalog:\n" + JSON.stringify(catalog);
        for (const full of [true, false]) await attempt(full ? "fullFork" : "boundaryFork", async () => {
            const fork = await client.rpc.sessions.fork({
                sessionId: parent.sessionId, ...(full ? {} : { toEventId: boundary }), name: "synthetic-context-fork",
            });
            owned.add(fork.sessionId);
            const child = await client.resumeSession(fork.sessionId, config);
            sessions.push(child);
            const inherited = await deadline(child.rpc.eventLog.read({
                direction: "backward", max: 32, types: ["user.message", "assistant.message"],
                agentScope: "primary", includeEphemeral: false,
            }));
            return { ...await turn(child, prompt), inheritedTextEvents: inherited.events.length,
                inheritedPrefixIdsMatch: historyMatches(inherited, full ? frozenIds : prefixIds),
                exclusivePrefixIdsMatch: !full ? historyMatches(inherited, prefixIds) : null };
        });
        await attempt("boundedHistory", async () => {
            const history = await deadline(collectHistory(parent.rpc.eventLog, { boundary }));
            const child = await create();
            return { ...await turn(child, JSON.stringify(history) + "\n" + prompt),
                acquisition: { ...history, messages: undefined } };
        });
        await attempt("explicitBrief", async () => turn(await create(), prompt));
        await attempt("nativeTask", async () => {
            const observation = observeOwnTask(parent);
            let id;
            try {
                const start = requests.length;
                ({ agentId: id } = await parent.rpc.tasks.startAgent({
                    agentType: "synthetic-selector", prompt, name: "synthetic-context-task",
                    ...(profile.model ? { model: profile.model } : {}),
                }));
                taskOwners.push({ parent, id });
                return { suppliedBytes: bytes(prompt), ...await observation.settled(id),
                    requests: requests.slice(start).filter(item => item.agentId === id).map(item => item.summary),
                    taskInfo: "No public per-ID TaskAgentInfo getter; lifecycle/result events used." };
            } finally { observation.unsubscribe(); }
        });
        if (report.arms.nativeTask.settled !== true) {
            report.arms.cancellation = { status: "not-reached", error: "prior owned task did not settle; parent arms stopped" };
        } else await attempt("cancellation", async () => {
            const observation = observeOwnTask(parent);
            try {
                const start = requests.length;
                const { agentId: id } = await parent.rpc.tasks.startAgent({
                    agentType: "synthetic-selector", prompt: SENTINELS.cancel, name: "synthetic-cancel-task",
                    ...(profile.model ? { model: profile.model } : {}),
                });
                taskOwners.push({ parent, id });
                await deadline(heldRequest, 8000);
                const ack = await parent.rpc.tasks.cancel({ id });
                const settlement = await observation.settled(id);
                return { acknowledgement: ack, ...settlement,
                    requests: requests.slice(start).filter(item => item.agentId === id).map(item => item.summary) };
            } finally { observation.unsubscribe(); }
        });
        report.native.parentConfigurationUnchanged = JSON.stringify(before)
            === JSON.stringify(await deadline(parent.rpc.model.getCurrent()));
        report.native.parentTextPrefixUnchanged = historyMatches(
            await deadline(parent.rpc.eventLog.read({ direction: "backward", max: 32,
                types: ["user.message", "assistant.message"], agentScope: "primary", includeEphemeral: false })),
            frozenIds, true);
    }
} catch (error) {
    report.error = safeError(error);
    if (report.native.startup.status === "not-reached") {
        report.native.startup = { status: "unavailable", error: report.error };
        for (const field of ["catalog", "models"]) {
            report.native[field] = { status: "unavailable", error: "owned runtime startup failed" };
        }
        report.arms = Object.fromEntries(Object.keys(report.arms).map(name =>
            [name, { status: "unavailable", error: "owned runtime startup failed" }]));
    }
}
finally {
    // Only fixture-owned IDs/sessions. No global task drain or shared-process termination.
    report.cleanup = { taskCancellationErrors: [], abortErrors: [], runtimeStopErrors: [],
        clientCreated: !!client, runtimeStopped: client ? false : null };
    for (const { parent, id } of taskOwners) {
        try { await parent.rpc.tasks.cancel({ id }); }
        catch (error) { report.cleanup.taskCancellationErrors.push(safeError(error)); }
    }
    for (const session of sessions) {
        try { await session.abort(); }
        catch (error) { report.cleanup.abortErrors.push(safeError(error)); }
    }
    if (client) try {
        report.cleanup.runtimeStopErrors = (await client.stop()).map(safeError);
        report.cleanup.runtimeStopped = report.cleanup.runtimeStopErrors.length === 0;
    }
    catch (error) { report.cleanup.runtimeStopErrors.push(safeError(error)); }
    report.native.interceptedRequests = requests.length;
    if (!modelsOnly) report.native.catalog.providerCalls = { ...providerCalls };
    report.status = spikeStatus(report);
    console.log(JSON.stringify(report, null, 2));
}
