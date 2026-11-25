import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Worker } from "node:worker_threads";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { IntentCorpus, classifyIntent, intentScope } from "../extensions/context-handoff/intent-corpus.mjs";
import { registerIntentObservation } from "../extensions/context-handoff/intent-observation.mjs";
import { readCanonicalGoal, resolveIntentCwd } from "../extensions/context-handoff/intent-goal.mjs";
import { formatHandoffMarkdown } from "../extensions/context-handoff/handoff-markdown.mjs";
import { formatIntentOutline, MAX_INTENT_OUTLINE_BYTES } from "../extensions/context-handoff/intent-outline.mjs";
import { sanitizedGitEnv } from "../extensions/context-handoff/handoff-core.mjs";

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "intent-continuity-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const storageRoot = join(root, "store");
  const a = join(root, "a");
  const b = join(root, "b");
  mkdirSync(a); mkdirSync(b);
  return { root, a, b, options: { storageRoot } };
}
function client(cwd) {
  const handlers = new Map();
  const state = { cwd, sessionId: "root-session" };
  const sent = [], warnings = [];
  const session = {
    sessionId: state.sessionId,
    on(name, callback) {
      const callbacks = handlers.get(name) || new Set();
      callbacks.add(callback); handlers.set(name, callbacks);
      return () => callbacks.delete(callback);
    },
    emit(name, event = {}) { for (const callback of handlers.get(name) || []) callback(event); },
    log(message) { warnings.push(message); },
    async send(options) {
      sent.push(options);
      session.emit("user.message", { id: `replay-${sent.length}`, data: {
        content: options.prompt, source: options.source,
      } });
    },
  };
  return { session, state, sent, warnings };
}
const message = (id, text, source = "user") => ({ id, timestamp: "2026-01-01T00:00:00.000Z",
  data: { content: text, source } });

test("registered adapter persists before reset, pages exact text, and does not capture its own replay", async (t) => {
  const f = fixture(t);
  const c = client(f.a);
  let goal = "Deliver the whole objective, not only the latest phase.";
  const observation = registerIntentObservation(c.session, c.state, {
    createCorpus: (cwd) => new IntentCorpus(cwd, f.options),
    readGoal: () => ({ status: "goal", path: "effort.md", goal }),
  });
  t.after(() => observation.dispose());
  await observation.settle();
  c.sent.length = 0;
  const long = "α😀\r\nhold; do not restart.\n".repeat(1500);
  c.session.emit("user.message", message("one", long));
  c.session.emit("user.message", message("two", "Later amendment."));
  await observation.settle();
  c.sent.length = 0;
  c.session.emit("session.context_cleared", {});
  await observation.settle();
  assert.equal(c.sent.length, 1);
  assert.equal(c.sent[0].source, "system");
  assert.equal(c.sent[0].mode, "immediate");
  assert.match(c.sent[0].prompt, /Read only the sources the current next step needs/u);
  assert.ok(Buffer.byteLength(c.sent[0].prompt, "utf8") < MAX_INTENT_OUTLINE_BYTES + 100);
  assert.equal(c.sent[0].prompt.includes(long), false);
  const facts = await observation.facts();
  assert.equal(facts.corpus.count, 2);
  const fragments = [];
  let cursor = {};
  do {
    const page = observation.page(facts.corpus.id, cursor);
    fragments.push(...page.records);
    cursor = page.next;
  } while (cursor);
  assert.equal(fragments.filter((v) => v.eventId === "one").map((v) => v.text).join(""), long);
  assert.deepEqual([...new Set(fragments.map((v) => v.eventId))], ["one", "two"]);
  goal = "Amended objective; same original authority limits.";
  c.session.emit("session.compaction_complete", { data: { success: false } });
  await observation.settle();
  assert.equal(c.sent.length, 1);
  c.session.emit("session.compaction_complete", { data: { success: true } });
  await observation.settle();
  assert.match(c.sent[1].prompt, /Amended objective/u);
  c.session.emit("session.context_cleared", { agentId: "child" });
  await observation.settle();
  assert.equal(c.sent.length, 2);
});

test("operator inputs and peer steers stay distinct; noise and child instructions are excluded", () => {
  const genuine = classifyIntent(message("one", "My objective is complete delivery."), "root");
  assert.equal(genuine.origin, "operator");
  const peer = classifyIntent(message("two",
    '<agent-message from="peer" msg-id="2">\nHold deployment pending verification.\n</agent-message>',
    "agent-bridge"), "root");
  assert.equal(peer.origin, "peer-steer");
  assert.equal(peer.source, "agent-bridge");
  const transformed = classifyIntent({ ...message("three", "Message from peer", "agent-bridge"),
    data: { source: "agent-bridge", content: "Message from peer", transformedContent: peer.text } }, "root");
  assert.equal(transformed.text, peer.text);
  assert.equal(transformed.origin, "peer-steer");
  for (const event of [
    { ...message("x", "child instructions"), agentId: "child" },
    message("x", '<skill-context name="example">loaded skill</skill-context>'),
    message("x", "<system_notification>task result</system_notification>"),
    message("x", "<system_reminder>[agent-worktrees] generated status reminder</system_reminder>"),
    { ...message("x", "Status checkpoint"), data: { content: "Status checkpoint",
      transformedContent: "<system_reminder>[agent-worktrees] generated status reminder</system_reminder>" } },
    message("x", "generated context", "system"),
    message("x", "unqualified bridge delivery", "agent-bridge"),
    message("x", '<agent-message kind="notify">result</agent-message>', "agent-bridge"),
    message("x", '<agent-message kind="status-check">status?</agent-message>', "agent-bridge"),
  ]) assert.equal(classifyIntent(event, "root"), null);
});

test("reforked clients read the same corpus; other worktrees and cwd switches stay isolated", async (t) => {
  const f = fixture(t);
  const old = client(f.a);
  const options = { createCorpus: (cwd) => new IntentCorpus(cwd, f.options) };
  const first = registerIntentObservation(old.session, old.state, options);
  old.session.emit("user.message", message("one", "Original mission."));
  await first.settle();
  await first.dispose();
  const fresh = client(f.a);
  const resumed = registerIntentObservation(fresh.session, fresh.state, options);
  t.after(() => resumed.dispose());
  assert.equal(fresh.sent.length, 0);
  fresh.session.emit("tool.execution_start", {});
  await resumed.settle();
  assert.match(fresh.sent[0].prompt, /Original mission/u);
  assert.equal((await resumed.facts()).corpus.count, 1);
  fresh.state.cwd = f.b;
  assert.equal((await resumed.facts()).corpus, null);
  fresh.session.emit("user.message", message("two", "Other worktree."));
  assert.equal((await resumed.facts()).corpus.count, 1);
  fresh.state.cwd = f.a;
  assert.equal((await resumed.facts()).corpus.count, 1);
});

test("failed persistence is visible and exact pending input survives a lifecycle retry", async (t) => {
  const f = fixture(t), c = client(f.a);
  const store = new IntentCorpus(f.a, f.options);
  const capture = store.capture.bind(store);
  let unavailable = true;
  store.capture = (input) => {
    if (unavailable) throw new Error("PRIVATE storage detail");
    return capture(input);
  };
  const observation = registerIntentObservation(c.session, c.state, { createCorpus: () => store });
  t.after(() => observation.dispose());
  c.session.emit("user.message", message("one", "Exact held instruction."));
  assert.equal(observation.pendingInputs, 1);
  assert.ok(c.warnings.length);
  assert.equal(c.warnings.join("").includes("PRIVATE"), false);
  unavailable = false;
  c.session.emit("session.idle");
  assert.equal(observation.pendingInputs, 0);
  c.session.emit("user.message", message("one", "Exact held instruction."));
  const data = await observation.facts();
  assert.equal(data.corpus.count, 1);
  assert.equal(observation.page(data.corpus.id).records[0].text, "Exact held instruction.");
});

test("replay discards a scope changed during configuration or Goal reads and rearms", async (t) => {
  for (const boundary of ["configuration", "goal"]) {
    const f = fixture(t), c = client(f.a);
    let release, entered;
    const waiting = new Promise((resolve) => { entered = resolve; });
    const held = new Promise((resolve) => { release = resolve; });
    let pause = true;
    const wait = async () => { if (pause) { entered(); await held; } };
    const observation = registerIntentObservation(c.session, c.state, {
      createCorpus: (cwd) => new IntentCorpus(cwd, f.options),
      allowAutomaticReplay: async () => {
        if (boundary === "configuration") await wait();
        return true;
      },
      readGoal: async (cwd) => {
        if (boundary === "goal") await wait();
        return { status: "goal", goal: cwd === f.a ? "Old Goal" : "Current Goal" };
      },
    });
    t.after(() => observation.dispose());
    c.session.emit("user.message", message("old", "Old scope input"));
    await waiting;
    c.state.cwd = f.b;
    pause = false;
    release();
    await observation.settle();
    assert.equal(c.sent.length, 0, boundary);
    c.session.emit("user.message", message("current", "Current scope input"));
    await observation.settle();
    assert.equal(c.sent.length, 1, boundary);
    assert.match(c.sent[0].prompt, /Current Goal/u);
    assert.equal(c.sent[0].prompt.includes("Old scope input"), false);
    const facts = observation.latest;
    assert.equal(observation.page(facts.corpus.id).records[0].text, "Current scope input");
  }
});

test("a rejected replay send retries on later root activity without recapturing history", async (t) => {
  const f = fixture(t), c = client(f.a);
  const send = c.session.send;
  let reject = true;
  c.session.send = async (options) => {
    if (reject) throw new Error("disconnected");
    await send(options);
  };
  const observation = registerIntentObservation(c.session, c.state, {
    createCorpus: (cwd) => new IntentCorpus(cwd, f.options),
  });
  t.after(() => observation.dispose());
  c.session.emit("user.message", message("one", "Retain this mission."));
  await observation.settle();
  assert.equal(c.sent.length, 0);
  assert.equal(observation.latest, null);
  assert.ok(c.warnings.length);
  const snapshot = (await observation.facts()).corpus.id;
  reject = false;
  c.session.emit("tool.execution_start", { agentId: "child" });
  await observation.settle();
  assert.equal(c.sent.length, 0);
  c.session.emit("tool.execution_start", {});
  await observation.settle();
  assert.equal(c.sent.length, 1);
  assert.equal(observation.latest.corpus.id, snapshot);
  assert.equal((await observation.facts()).corpus.count, 1);
});

test("emergency pointers avoid Goal discovery and explicitly disqualify cached freshness", async (t) => {
  const f = fixture(t), c = client(f.a);
  let reads = 0;
  const observation = registerIntentObservation(c.session, c.state, {
    createCorpus: (cwd) => new IntentCorpus(cwd, f.options),
    allowAutomaticReplay: () => false,
    readGoal: () => { reads++; return { status: "goal", path: "effort.md", goal: "Old Goal" }; },
  });
  t.after(() => observation.dispose());
  c.session.emit("user.message", message("one", "Original constraint."));
  await observation.settle();
  const cached = await observation.facts();
  c.session.emit("user.message", message("two", "Later constraint."));
  await observation.settle();
  const emergency = observation.emergencyFacts();
  assert.equal(reads, 1);
  assert.equal(emergency.goal.goal, null);
  assert.equal(emergency.goal.path, "effort.md");
  assert.equal(emergency.corpus.id, cached.corpus.id);
  assert.match(emergency.instruction, /may omit later inputs/u);
  assert.ok(emergency.corpusPath);
  c.state.cwd = f.b;
  const other = observation.emergencyFacts();
  assert.equal(other.corpus, null);
  assert.equal(other.goal.path, null);
  assert.equal(reads, 1);
});

test("broken storage cannot prevent facts or manufacture a complete replay", async (t) => {
  const f = fixture(t), c = client(f.a);
  const observation = registerIntentObservation(c.session, c.state, {
    createCorpus: () => { throw new Error("PRIVATE corrupt record path"); },
    readGoal: () => ({ status: "goal", path: "effort.md", goal: "Keep the whole objective." }),
  });
  t.after(() => observation.dispose());
  const data = await observation.facts();
  assert.equal(data.status, "unavailable");
  assert.equal(data.corpus, null);
  assert.equal(data.goal.goal, "Keep the whole objective.");
  assert.ok(c.warnings.length);
  assert.equal(c.warnings.join("").includes("PRIVATE"), false);
  c.session.emit("session.context_cleared", {});
  await observation.settle();
  assert.match(c.sent[0].prompt, /unavailable/u);
});

test("mode off retains explicit reads without automatically starting turns", async (t) => {
  const f = fixture(t), c = client(f.a);
  let allowed = false;
  const observation = registerIntentObservation(c.session, c.state, {
    createCorpus: (cwd) => new IntentCorpus(cwd, f.options),
    allowAutomaticReplay: () => allowed,
  });
  t.after(() => observation.dispose());
  c.session.emit("user.message", message("one", "Preserve the mission."));
  c.session.emit("session.context_cleared", {});
  await observation.settle();
  assert.equal(c.sent.length, 0);
  assert.equal((await observation.facts()).corpus.count, 1);
  allowed = true;
  c.session.emit("tool.execution_start", {});
  await observation.settle();
  assert.equal(c.sent.length, 1);
});

test("immutable records reject conflicting retries and pinned pages detect changed bytes", (t) => {
  const f = fixture(t), store = new IntentCorpus(f.a, f.options);
  const input = classifyIntent(message("one", "Keep this exact constraint."), "root");
  store.capture(input);
  const snapshot = store.snapshot();
  store.capture(input);
  assert.equal(store.snapshot().id, snapshot.id);
  assert.throws(() => store.capture({ ...input, text: "Conflicting replacement." }), /identity-conflict/u);
  assert.equal(store.page(snapshot.id).records[0].text, input.text);
  assert.throws(() => store.page("../escape"), /cursor-invalid/u);
  const path = join(store.home, "records", readdirSync(join(store.home, "records"))[0]);
  writeFileSync(path, readFileSync(path, "utf8") + " ");
  assert.throws(() => store.page(snapshot.id), /record-changed/u);
});

test("independent concurrent writers retain all distinct events and deduplicate the same event", async (t) => {
  const f = fixture(t);
  const moduleUrl = new URL("../extensions/context-handoff/intent-corpus.mjs", import.meta.url).href;
  const workers = Array.from({ length: 4 }, (_, n) => new Promise((resolve, reject) => {
    const worker = new Worker(`
      const { parentPort, workerData } = require("node:worker_threads");
      import(workerData.url).then(({IntentCorpus}) => {
        const store = new IntentCorpus(workerData.cwd, workerData.options);
        for (const eventId of ["shared", "writer-" + workerData.n]) {
          store.capture({sessionId:"root",eventId,source:"user",origin:"operator",
            timestamp:"2026-01-01T00:00:00Z",text:eventId});
        }
        parentPort.postMessage("done");
      }).catch(e => { throw e; });
    `, { eval: true, workerData: { url: moduleUrl, cwd: f.a, options: f.options, n } });
    worker.on("error", reject);
    worker.on("exit", (code) => code === 0 ? resolve() : reject(new Error(`worker exit ${code}`)));
  }));
  await Promise.all(workers);
  const store = new IntentCorpus(f.a, f.options);
  assert.equal(store.snapshot().count, 5);
  assert.equal(readdirSync(join(store.home, "records")).filter((v) => v.endsWith(".tmp")).length, 0);
});

test("Goal lookup uses the bound effort home and refreshes source text, with explicit legacy fallback", (t) => {
  const f = fixture(t), effort = join(f.b, "effort.md");
  writeFileSync(effort, "# Work\n\n## Goal\n\nFull mission.\n\n## Plan\n\nNot the mission.");
  const execute = (_bin, args, options) => {
    assert.equal(options.allowProvision, false);
    return args[0] === "state-root"
      ? JSON.stringify({ state_root: f.b, requires_external: false, bound: true }) : JSON.stringify({
      active_effort: { active: true, path: "effort.md", participant: "driver", slice: "phase" },
    });

  };
  assert.equal(readCanonicalGoal(f.a, execute).goal, "Full mission.");
  writeFileSync(effort, "# Work\n\n## Goal\n\nChanged objective.\n\n" +
    "## Scope Approvals\n\nDo not deploy.\n\n" +
    "## Outstanding Background Flows & External State\n\nWatch the open PR.\n\n" +
    "## Next Action\n\nRun the focused tests.\n");
  const updated = readCanonicalGoal(f.a, execute);
  assert.equal(updated.goal, "Changed objective.");
  assert.deepEqual(updated.recovery, {
    limits: "Do not deploy.", obligations: "Watch the open PR.", nextAction: "Run the focused tests.",
  });
  writeFileSync(effort, "# Work\n\n## Guiding Intent\n\nLegacy mission.\n");
  assert.equal(readCanonicalGoal(f.a, execute).status, "legacy-guiding-intent");
  assert.equal(readCanonicalGoal(f.a, execute).goal, "Legacy mission.");
  assert.equal(readCanonicalGoal(f.a, () => { throw new Error("missing peer"); }).status, "unavailable");
});

test("emergency handoff preserves the original prompt beyond 500 characters and current intent pointers", () => {
  const original = "constraint ".repeat(100);
  const text = formatHandoffMarkdown({ firstUserPrompt: original,
    intentContinuity: { goal: { path: "effort.md", goal: "Complete mission." },
      corpus: { id: "snapshot", count: 4 } } });
  assert.ok(text.includes(original));
  assert.match(text, /Complete mission/u);
  assert.match(text, /snapshot/u);
  assert.doesNotMatch(text, /all corpus pages/u);
});

test("recovery outline bounds worst-case UTF-8 fields without injecting source bodies", () => {
  const huge = "😀\\\r\n".repeat(20000);
  const outline = formatIntentOutline({
    status: "cached-unqualified", pendingInputs: 4, sessionId: huge, checkpoint: huge,
    references: Array.from({ length: 8 }, () => ({ sessionId: huge })),
    goal: { goal: huge, path: huge, status: huge,
      recovery: { limits: huge, obligations: huge, nextAction: huge } },
    corpus: { id: huge, home: huge, count: 10000 },
    page: { records: [{ origin: "operator", text: "PRIVATE SOURCE BODY" }] },
  });
  assert.ok(Buffer.byteLength(outline, "utf8") <= MAX_INTENT_OUTLINE_BYTES);
  assert.ok(outline.startsWith("Goal (source excerpt): "));
  assert.equal(outline.split("\n\n")[0].includes("\n"), false);
  for (const label of ["Limits/holds:", "Live obligations:", "Next action:",
    "- Effort:", "- Checkpoint:", "- Session:", "- Intent history:", "- Pinned snapshot:"]) {
    assert.ok(outline.includes(label), label);
  }
  assert.ok(outline.includes("Capture: cached-unqualified"));
  assert.ok(outline.includes("use explicit facts for the exact locator"));
  assert.equal(outline.includes("PRIVATE SOURCE BODY"), false);
  assert.doesNotMatch(outline, /read every|all corpus pages|before selecting substantive/iu);
});

test("recovery uses canonical goal and safety lines, leaving exact corpus history on demand", async (t) => {
  const f = fixture(t), c = client(f.a);
  const observation = registerIntentObservation(c.session, c.state, {
    createCorpus: (cwd) => new IntentCorpus(cwd, f.options),
    readGoal: () => ({ status: "goal", path: "effort.md", goal: "Complete the full delivery.",
      recovery: { limits: "Hold deployment.", obligations: "Watch PR 42.", nextAction: "Run tests." } }),
  });
  t.after(() => observation.dispose());
  const original = "OLD REQUEST BODY ".repeat(1000);
  c.session.emit("user.message", message("one", original));
  await observation.settle();
  const prompt = c.sent.at(-1).prompt;
  for (const text of ["Complete the full delivery.", "Hold deployment.", "Watch PR 42.", "Run tests.",
    "effort.md", "Pinned snapshot", "Read only the sources"]) assert.ok(prompt.includes(text), text);
  assert.equal(prompt.includes("OLD REQUEST BODY"), false);
  const facts = await observation.facts();
  const fragments = [];
  let cursor = {};
  do {
    const page = observation.page(facts.corpus.id, cursor);
    fragments.push(...page.records.map((record) => record.text));
    cursor = page.next;
  } while (cursor);
  assert.equal(fragments.join(""), original);
});

test("source locators retain exact whitespace and multiline locators use explicit facts", () => {
  const effort = " effort  notes.md ";
  const history = "history\u00a0  directory";
  const outline = formatIntentOutline({
    goal: { path: effort },
    checkpoint: "checkpoint\nnot-a-second-pointer",
    corpus: { home: history, id: "snapshot", count: 1 },
  });
  assert.ok(outline.includes(`- Effort: ${effort} -- `));
  assert.ok(outline.includes(`- Intent history: ${history} -- `));
  assert.ok(outline.includes("- Checkpoint: (reference exceeds one-line budget; use explicit facts"));
  assert.equal(outline.includes("not-a-second-pointer"), false);
  assert.ok(Buffer.byteLength(outline, "utf8") <= MAX_INTENT_OUTLINE_BYTES);
});

test("the extension-free payload reader returns the exact pinned continuation page", (t) => {
  const f = fixture(t);
  const store = new IntentCorpus(f.a, {
    storageRoot: join(f.root, ".copilot", "context-handoff", "intent"),
  });
  const text = "full constraint\n".repeat(1500);
  store.capture(classifyIntent(message("one", text), "root"));
  const snapshot = store.snapshot();
  const first = store.page(snapshot.id);
  const cli = fileURLToPath(new URL("../extensions/context-handoff/handoff-cli.mjs", import.meta.url));
  const parts = [...first.records];
  let cursor = first.next;
  while (cursor) {
    const result = spawnSync(process.execPath, [cli, "intent-page", "--snapshot", snapshot.id,
      "--record", String(cursor.record), "--offset", String(cursor.offset), "--json"], {
      cwd: f.a, env: { ...process.env, HOME: f.root, USERPROFILE: f.root },
      encoding: "utf8", windowsHide: true, timeout: 10000,
    });
    assert.equal(result.status, 0, result.stderr);
    assert.ok(Buffer.byteLength(result.stdout.trim(), "utf8") <= 12000);
    const page = JSON.parse(result.stdout);
    parts.push(...page.records);
    cursor = page.next;
  }
  assert.equal(parts.map((v) => v.text).join(""), text);
});

test("bare resume selects the continuing worktree once without provisioning optional tooling", (t) => {
  const f = fixture(t);
  const execute = (bin, args, options) => {
    assert.equal(bin, "agent-worktrees");
    assert.deepEqual(args, ["get", "worktree-dir", "--session-id", "resumed-session"]);
    assert.equal(options.allowProvision, false);
    return f.b;
  };
  assert.equal(resolveIntentCwd(f.a, "resumed-session", execute), f.b);
  assert.equal(resolveIntentCwd(f.a, "resumed-session", () => { throw new Error("unavailable"); }), f.a);
  assert.equal(resolveIntentCwd(f.a, null, () => { throw new Error("must not call"); }), f.a);
});

test("paired Goal reads use the owning knowledge worktree, not its registered checkout", (t) => {
  const f = fixture(t), registered = join(f.root, "registered");
  mkdirSync(registered);
  writeFileSync(join(f.b, "effort.md"), "# Work\n\n## Goal\n\nPaired worktree mission.\n");
  writeFileSync(join(registered, "effort.md"), "# Work\n\n## Goal\n\nOther checkout mission.\n");
  const execute = (_bin, args) => {
    if (args[0] === "effort-focus") return JSON.stringify({
      active_effort: { active: true, state: "open", path: "effort.md" },
    });
    if (args.includes("--pair")) return JSON.stringify({
      paired: true, sibling: { path: f.b, role: "knowledge" },
    });
    return JSON.stringify({ state_root: registered, requires_external: true, bound: true });
  };
  assert.equal(readCanonicalGoal(f.a, execute).goal, "Paired worktree mission.");
  assert.equal(readCanonicalGoal(f.a, execute).path, join(f.b, "effort.md"));
});

test("stale bindings retain the original pointer and oversized legacy intent is not injected", (t) => {
  const f = fixture(t);
  const execute = (_bin, args) => args[0] === "effort-focus" ? JSON.stringify({
    active_effort: { active: false, state: "stale", path: "missing.md", reason: "effort is missing" },
  }) : JSON.stringify({ state_root: f.b, requires_external: false, bound: true });
  const stale = readCanonicalGoal(f.a, execute);
  assert.equal(stale.status, "unavailable");
  assert.equal(stale.path, join(f.b, "missing.md"));
  assert.equal(stale.bindingReason, "effort is missing");
  const path = join(f.b, "legacy.md");
  writeFileSync(path, "# Work\n\n## Guiding Intent\n\n" + "\\\r\n😀".repeat(4000) +
    "\n\n## Scope Approvals\n\nHold deployment.\n");
  const active = (_bin, args) => args[0] === "effort-focus" ? JSON.stringify({
    active_effort: { active: true, state: "open", path: "legacy.md" },
  }) : JSON.stringify({ state_root: f.b, requires_external: false, bound: true });
  const legacy = readCanonicalGoal(f.a, active);
  assert.equal(legacy.status, "legacy-guiding-intent-reference");
  assert.equal(legacy.goal, null);
  assert.equal(legacy.path, path);
  assert.equal(legacy.recovery.limits, "Hold deployment.");
  const outline = formatIntentOutline({ goal: legacy });
  assert.ok(outline.startsWith("Goal (source excerpt):"));
  assert.match(outline, /Hold deployment/u);
  assert.ok(Buffer.byteLength(outline, "utf8") <= MAX_INTENT_OUTLINE_BYTES);
});

test("many short escaped steers respect the complete serialized page budget", (t) => {
  const f = fixture(t), store = new IntentCorpus(f.a, f.options);
  const text = "\\\r\n😀";
  for (let i = 0; i < 128; i++) store.capture(classifyIntent(message(String(i), text), "root"));
  const snapshot = store.snapshot();
  let cursor = {}, pages = 0, count = 0;
  do {
    const page = store.page(snapshot.id, { ...cursor, maxBytes: 512 });
    assert.ok(Buffer.byteLength(JSON.stringify(page), "utf8") <= 512);
    assert.ok(page.records.every((record) => record.text === text));
    pages++;
    count += page.records.length;
    cursor = page.next;
  } while (cursor);
  assert.ok(pages > 1);
  assert.equal(count, 128);
});

test("a known unavailable binding is replayed even when its home cannot be resolved", async (t) => {
  const f = fixture(t), c = client(f.a);
  const observation = registerIntentObservation(c.session, c.state, {
    createCorpus: (cwd) => new IntentCorpus(cwd, f.options),
    readGoal: () => ({ status: "unavailable", ref: "efforts/active/known/README.md", path: null, goal: null }),
  });
  t.after(() => observation.dispose());
  c.session.emit("session.context_cleared", {});
  await observation.settle();
  assert.equal(c.sent.length, 1);
  assert.match(c.sent[0].prompt, /efforts\/active\/known/u);
  assert.match(c.sent[0].prompt, /unavailable/u);
});

test("pending input acquires only its missing session identity at a later lifecycle boundary", async (t) => {
  const f = fixture(t), c = client(f.a);
  c.session.sessionId = c.state.sessionId = null;
  const observation = registerIntentObservation(c.session, c.state, {
    createCorpus: (cwd) => new IntentCorpus(cwd, f.options),
  });

  t.after(() => observation.dispose());
  c.session.emit("user.message", message("early", "Keep this early instruction exactly."));
  await observation.settle();
  assert.equal(observation.pendingInputs, 1);
  c.state.sessionId = "late-identity";
  c.session.emit("tool.execution_complete", {});
  assert.equal(observation.pendingInputs, 0);
  const facts = await observation.facts();
  const saved = observation.page(facts.corpus.id).records[0];
  assert.equal(saved.sessionId, "late-identity");
  assert.equal(saved.text, "Keep this early instruction exactly.");
  assert.equal(saved.eventId, "early");
});

test("delayed identity binds provisional inputs and Goal reads once to the continuing worktree", async (t) => {
  const f = fixture(t), c = client(f.a);
  c.session.sessionId = c.state.sessionId = null;
  let resolutions = 0;
  const observation = registerIntentObservation(c.session, c.state, {
    createCorpus: (cwd) => new IntentCorpus(cwd, f.options),
    allowAutomaticReplay: () => false,
    resolveCwd: (cwd, sid) => {
      resolutions++;
      assert.equal(cwd, f.a);
      assert.equal(sid, "continuing-session");
      return f.b;
    },
    readGoal: (cwd) => ({ status: "goal", goal: cwd === f.b ? "Bound Goal" : "Launch Goal" }),
  });
  t.after(() => observation.dispose());
  c.session.emit("user.message", message("early", "Keep the early instruction."));
  await observation.settle();
  assert.equal(observation.pendingInputs, 1);
  c.state.sessionId = "continuing-session";
  c.session.emit("tool.execution_complete", {});
  const facts = await observation.facts();
  assert.equal(c.state.cwd, f.b);
  assert.equal(facts.goal.goal, "Bound Goal");
  assert.equal(observation.pendingInputs, 0);
  const saved = observation.page(facts.corpus.id).records[0];
  assert.equal(saved.text, "Keep the early instruction.");
  assert.equal(saved.sessionId, "continuing-session");
  assert.equal(new IntentCorpus(f.a, f.options).snapshot(), null);
  assert.equal(resolutions, 1);
});

test("CLI paging and consume facts retain the session-bound scope from an unrelated shell directory", (t) => {
  const f = fixture(t);
  writeFileSync(join(f.a, "effort.md"), "# Work\n\n## Goal\n\nThe bound mission.\n");
  writeFileSync(join(f.b, "effort.md"), "# Work\n\n## Goal\n\nAn unrelated mission.\n");
  const store = new IntentCorpus(f.a, {
    storageRoot: join(f.root, ".copilot", "context-handoff", "intent"),
  });
  store.capture(classifyIntent(message("one", "Preserve the bound instruction."), "root"));
  const snapshot = store.snapshot();
  const loader = new URL("./fixtures/intent-cli-loader.mjs", import.meta.url).href;
  const cli = fileURLToPath(new URL("../extensions/context-handoff/handoff-cli.mjs", import.meta.url));
  const invoke = (args) => {
    const result = spawnSync(process.execPath, ["--experimental-loader", loader, cli, ...args,
      "--cwd", f.b, "--session-id", "fixture-bound-session", "--json"], {
      cwd: f.b, env: { ...process.env, HOME: f.root, USERPROFILE: f.root, FIXTURE_BOUND_CWD: f.a },
      encoding: "utf8", windowsHide: true, timeout: 10000,
    });
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout);
  };
  const page = invoke(["intent-page", "--snapshot", snapshot.id]);
  assert.equal(page.records[0].text, "Preserve the bound instruction.");
  const consumed = invoke(["consume", "--handoff-id", "fixture-handoff"]);
  assert.equal(consumed.intentContinuity.goal.goal, "The bound mission.");
  assert.equal(consumed.intentContinuity.corpus.id, snapshot.id);
});

function gitFixture(cwd, args, env = process.env) {
  const result = spawnSync("git", [
    "-c", `core.hooksPath=${join(cwd, ".fixture-hooks-disabled")}`,
    "-c", "commit.gpgsign=false", ...args,
  ], { cwd, env: sanitizedGitEnv(env), encoding: "utf8", windowsHide: true, timeout: 10000 });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
}

test("mutating Git fixtures ignore inherited selectors for another disposable repository", (t) => {
  const f = fixture(t);
  const commit = ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
    "commit", "--allow-empty", "-qm", "fixture"];
  gitFixture(f.a, ["init", "-q"]);
  gitFixture(f.b, ["init", "-q"]);
  gitFixture(f.b, commit);
  const before = gitFixture(f.b, ["rev-parse", "HEAD"]);
  const worktrees = gitFixture(f.b, ["worktree", "list", "--porcelain"]);
  const poisoned = { ...process.env, GIT_DIR: join(f.b, ".git"), GIT_WORK_TREE: f.b,
    GIT_COMMON_DIR: join(f.b, ".git"), GIT_INDEX_FILE: join(f.b, ".git", "index") };
  gitFixture(f.a, commit, poisoned);
  gitFixture(f.a, ["worktree", "add", "--detach", join(f.root, "confined-linked")], poisoned);
  assert.equal(gitFixture(f.b, ["rev-parse", "HEAD"]), before);
  assert.equal(gitFixture(f.b, ["worktree", "list", "--porcelain"]), worktrees);
  assert.ok(gitFixture(f.a, ["worktree", "list", "--porcelain"]).includes("confined-linked"));
});

test("real Git anchors isolate sessions without importing legacy shared history", (t) => {
  const f = fixture(t);
  gitFixture(f.a, ["init", "-q"]);
  const legacy = new IntentCorpus(f.a, { ...f.options, scope: intentScope(f.a).identity });
  legacy.capture(classifyIntent(message("legacy", "Unrelated historical goal."), "legacy-session"));
  const legacySnapshot = legacy.snapshot();
  assert.throws(() => new IntentCorpus(f.a, f.options), /anchor-session-required/u);
  const first = new IntentCorpus(f.a, { ...f.options, sessionId: "first-session" });
  const second = new IntentCorpus(f.a, { ...f.options, sessionId: "second-session" });
  first.capture(classifyIntent(message("first", "First session objective."), "first-session"));
  assert.equal(second.snapshot(), null);
  assert.throws(() => second.capture(classifyIntent(message("foreign", "Wrong session."), "first-session")),
    /anchor-session-mismatch/u);
  second.capture(classifyIntent(message("second", "Second session objective."), "second-session"));
  const resumed = new IntentCorpus(f.a, { ...f.options, sessionId: "first-session" });
  assert.equal(resumed.home, first.home);
  assert.equal(resumed.snapshot().count, 1);
  assert.equal(resumed.page(resumed.snapshot().id).records[0].text, "First session objective.");
  assert.equal(second.page(second.snapshot().id).records[0].text, "Second session objective.");
  assert.equal(second.page(legacySnapshot.id).records[0].text, "Unrelated historical goal.");
  assert.equal(second.snapshot().count, 1);
  assert.equal(legacy.snapshot().id, legacySnapshot.id);
});

test("legacy pinned anchor pages stay explicitly readable without a current session identity", (t) => {
  const f = fixture(t);
  gitFixture(f.a, ["init", "-q"]);
  const legacy = new IntentCorpus(f.a, {
    storageRoot: join(f.root, ".copilot", "context-handoff", "intent"), scope: intentScope(f.a).identity,
  });
  legacy.capture(classifyIntent(message("legacy", "Historical reference only."), "legacy-session"));
  const cli = fileURLToPath(new URL("../extensions/context-handoff/handoff-cli.mjs", import.meta.url));
  const result = spawnSync(process.execPath, [cli, "intent-page", "--snapshot", legacy.snapshot().id, "--json"], {
    cwd: f.a, env: { ...process.env, HOME: f.root, USERPROFILE: f.root, COPILOT_AGENT_SESSION_ID: "" },
    encoding: "utf8", windowsHide: true, timeout: 10000,
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).records[0].text, "Historical reference only.");
});

test("real linked worktrees preserve accumulated history across different sessions", (t) => {
  const f = fixture(t), linked = join(f.root, "linked");
  gitFixture(f.a, ["init", "-q"]);
  gitFixture(f.a, ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
    "commit", "--allow-empty", "-qm", "fixture"]);
  gitFixture(f.a, ["worktree", "add", "--detach", linked]);
  const first = new IntentCorpus(linked, { ...f.options, sessionId: "first-session" });
  first.capture(classifyIntent(message("first", "Complete the worktree objective."), "first-session"));
  const second = new IntentCorpus(linked, { ...f.options, sessionId: "successor-session" });
  second.capture(classifyIntent(message("next", "Finish the remaining gate."), "successor-session"));
  assert.equal(first.home, second.home);
  assert.equal(second.snapshot().count, 2);
  assert.deepEqual(second.page(second.snapshot().id).records.map((record) => record.sessionId),
    ["first-session", "successor-session"]);
});

test("a separate Git directory is still an anchor and a queued old-session replay is discarded", async (t) => {
  const f = fixture(t);
  gitFixture(f.a, ["init", "-q", "--separate-git-dir", join(f.root, "metadata")]);
  assert.equal(intentScope(f.a).anchor, true);
  const c = client(f.a);
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const observation = registerIntentObservation(c.session, c.state, {
    createCorpus: (cwd, sessionId) => new IntentCorpus(cwd, { ...f.options, sessionId }),
    allowAutomaticReplay: () => gate,
  });
  t.after(() => observation.dispose());
  c.session.emit("user.message", message("old", "Old session goal."));
  await new Promise((resolve) => setImmediate(resolve));
  c.session.sessionId = "new-session";
  release(true);
  await observation.settle();
  assert.equal(c.sent.length, 0);
  assert.equal((await observation.facts()).corpus, null);
  assert.equal(observation.emergencyFacts().corpus, null);
  c.session.emit("user.message", message("new", "New session goal."));
  await observation.settle();
  assert.match(c.sent.at(-1).prompt, /New session goal/u);
  assert.doesNotMatch(c.sent.at(-1).prompt, /Old session goal/u);
});

test("anchor observer restores only its own goal across reset, compaction and reconnect", async (t) => {
  const f = fixture(t);
  gitFixture(f.a, ["init", "-q"]);
  let queries = 0;
  const options = {
    createCorpus: (cwd, sessionId) => new IntentCorpus(cwd, { ...f.options, sessionId }),
    readGoal: (cwd, sid) => readCanonicalGoal(cwd, () => {
      queries++;
      throw new Error("anchor-wide effort binding must not be queried");
    }, sid),
  };
  const first = client(f.a);
  const old = registerIntentObservation(first.session, first.state, options);
  first.session.emit("user.message", message("old", "Another session's goal."));
  await old.settle();
  await old.dispose();
  const c = client(f.a);
  c.session.sessionId = c.state.sessionId = "current-anchor";
  const observation = registerIntentObservation(c.session, c.state, options);
  t.after(() => observation.dispose());
  c.session.emit("tool.execution_start");
  await observation.settle();
  assert.equal(c.sent.length, 0);
  c.session.emit("user.message", message("current", "Only this session's goal."));
  await observation.settle();
  for (const [type, event] of [
    ["session.context_cleared", {}],
    ["session.compaction_complete", { data: { success: true } }],
  ]) {
    c.session.emit(type, event);
    await observation.settle();
    assert.match(c.sent.at(-1).prompt, /Only this session's goal/u);
    assert.doesNotMatch(c.sent.at(-1).prompt, /Another session/u);
  }
  await observation.dispose();
  const reconnect = registerIntentObservation(c.session, c.state, options);
  t.after(() => reconnect.dispose());
  c.session.emit("tool.execution_start");
  await reconnect.settle();
  assert.match(c.sent.at(-1).prompt, /Only this session's goal/u);
  c.session.sessionId = "independent-anchor";
  assert.equal(reconnect.emergencyFacts().corpus, null);
  assert.equal((await reconnect.facts()).corpus, null);
  assert.equal(queries, 0);
});

test("anchor explicit startup CLI links remain references, not inherited goals", (t) => {
  const f = fixture(t);
  gitFixture(f.a, ["init", "-q"]);
  const options = { storageRoot: join(f.root, ".copilot", "context-handoff", "intent") };
  const prior = new IntentCorpus(f.a, { ...options, sessionId: "prior-anchor" });
  prior.capture(classifyIntent(message("prior", "Do not inherit this goal."), "prior-anchor"));
  const current = new IntentCorpus(f.a, { ...options, sessionId: "fixture-bound-session" });
  current.capture(classifyIntent(message("start", "Use the prior session only as a reference."),
    "fixture-bound-session"));
  const loader = new URL("./fixtures/intent-cli-loader.mjs", import.meta.url).href;
  const cli = fileURLToPath(new URL("../extensions/context-handoff/handoff-cli.mjs", import.meta.url));
  const invoke = (args) => spawnSync(process.execPath, ["--experimental-loader", loader, cli, ...args,
    "--cwd", f.b, "--session-id", "fixture-bound-session", "--json"], {
    cwd: f.b, env: { ...process.env, HOME: f.root, USERPROFILE: f.root, FIXTURE_BOUND_CWD: f.a },
    encoding: "utf8", windowsHide: true, timeout: 10000,
  });
  assert.notEqual(invoke(["reference-session", "--prior-session", "prior-anchor"]).status, 0);
  const reference = invoke(["reference-session", "--prior-session", "prior-anchor", "--startup-reference"]);
  assert.equal(reference.status, 0, reference.stderr);
  assert.deepEqual(JSON.parse(reference.stdout),
    { version: 1, sessionId: "prior-anchor", purpose: "reference-only" });
  const consumed = invoke(["consume", "--handoff-id", "fixture-handoff"]);
  assert.equal(consumed.status, 0, consumed.stderr);
  const data = JSON.parse(consumed.stdout).intentContinuity;
  assert.equal(data.goal.goal, null);
  assert.equal(data.goal.status, "session-local");
  assert.equal(data.corpus.count, 1);
  assert.equal(data.references[0].sessionId, "prior-anchor");
  assert.doesNotMatch(JSON.stringify(data), /Do not inherit this goal/u);
  assert.match(formatIntentOutline(data), /explicit startup references only/u);
  current.capture(classifyIntent(message("later", "Later unrelated work."), "fixture-bound-session"));
  assert.throws(() => current.referenceSession("another", { startup: true }), /window-closed/u);
  assert.equal(current.referenceSession("prior-anchor", { startup: true }).purpose, "reference-only");
  assert.throws(() => current.referenceSession("fixture-bound-session", { startup: true }), /reference-required/u);
});

test("concurrent anchor reference admission cannot exceed the session budget", async (t) => {
  const f = fixture(t);
  gitFixture(f.a, ["init", "-q"]);
  const options = { ...f.options, sessionId: "current-session" };
  const store = new IntentCorpus(f.a, options);
  store.capture(classifyIntent(message("goal", "Keep current history readable."), "current-session"));
  for (let n = 0; n < 7; n++) store.referenceSession(`prior-${n}`, { startup: true });
  const barrier = new SharedArrayBuffer(8);
  const url = new URL("../extensions/context-handoff/intent-corpus.mjs", import.meta.url).href;
  const outcomes = await Promise.all([0, 1].map((n) => new Promise((resolve, reject) => {
    let outcome;
    const worker = new Worker(`
      const {parentPort,workerData} = require("node:worker_threads");
      import(workerData.url).then(({IntentCorpus}) => {
        const sync = new Int32Array(workerData.barrier);
        const store = new IntentCorpus(workerData.cwd,workerData.options);
        Atomics.add(sync,0,1); Atomics.notify(sync,0);
        while (Atomics.load(sync,0)<2) Atomics.wait(sync,0,1,5000);
        const read = store.references.bind(store);
        store.references = () => {
          const value = read();
          const earlier = Atomics.add(sync,1,1); Atomics.notify(sync,1);
          if (!earlier) Atomics.wait(sync,1,1,1000);
          return value;
        };
        try {
          store.referenceSession("concurrent-"+workerData.n,{startup:true});
          parentPort.postMessage("accepted");
        } catch (error) { parentPort.postMessage(error.message); }
      }).catch(error => { throw error; });
    `, { eval: true, workerData: { url, cwd: f.a, options, barrier, n } });
    worker.on("message", (value) => { outcome = value; });
    worker.on("error", reject);
    worker.on("exit", (code) => code === 0 ? resolve(outcome) : reject(new Error(`worker exit ${code}`)));
  })));
  assert.equal(outcomes.filter((outcome) => outcome === "accepted").length, 1);
  assert.ok(outcomes.every((outcome) => outcome === "accepted"
    || /admission-busy|window-closed/u.test(outcome)));
  assert.equal(store.references().length, 8);
  assert.equal(store.page(store.snapshot().id).records[0].text, "Keep current history readable.");
  assert.equal(readdirSync(store.home).includes("reference-admission.lock"), false);
});

test("optional reference damage or an interrupted admission cannot hide the anchor corpus", async (t) => {
  const f = fixture(t);
  gitFixture(f.a, ["init", "-q"]);
  const c = client(f.a);
  const store = new IntentCorpus(f.a, { ...f.options, sessionId: c.state.sessionId });
  store.capture(classifyIntent(message("goal", "Keep the original objective."), c.state.sessionId));
  store.referenceSession("prior", { startup: true });
  const home = join(store.home, "references");
  writeFileSync(join(home, readdirSync(home)[0]), "{}");
  writeFileSync(join(store.home, "reference-admission.lock"), "interrupted");
  assert.throws(() => store.referenceSession("next", { startup: true }), /admission-busy/u);
  const observation = registerIntentObservation(c.session, c.state, { createCorpus: () => store });
  t.after(() => observation.dispose());
  const facts = await observation.facts();
  assert.equal(facts.status, "available");
  assert.equal(facts.referenceStatus, "unavailable");
  assert.equal(facts.corpus.count, 1);
  c.session.emit("session.context_cleared");
  await observation.settle();
  assert.match(c.sent.at(-1).prompt, /Keep the original objective/u);
  assert.match(c.sent.at(-1).prompt, /Prior-session references: unavailable/u);
});
