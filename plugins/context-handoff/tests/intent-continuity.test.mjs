import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Worker } from "node:worker_threads";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { IntentCorpus, classifyIntent } from "../extensions/context-handoff/intent-corpus.mjs";
import { registerIntentObservation } from "../extensions/context-handoff/intent-observation.mjs";
import { readCanonicalGoal } from "../extensions/context-handoff/intent-goal.mjs";
import { formatHandoffMarkdown } from "../extensions/context-handoff/handoff-markdown.mjs";

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
  assert.match(c.sent[0].prompt, /research each/u);
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
  for (const event of [
    { ...message("x", "child instructions"), agentId: "child" },
    message("x", '<skill-context name="example">loaded skill</skill-context>'),
    message("x", "<system_notification>task result</system_notification>"),
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
  const observation = registerIntentObservation(c.session, c.state, {
    createCorpus: (cwd) => new IntentCorpus(cwd, f.options),
    allowAutomaticReplay: () => false,
  });
  t.after(() => observation.dispose());
  c.session.emit("user.message", message("one", "Preserve the mission."));
  c.session.emit("session.context_cleared", {});
  await observation.settle();
  assert.equal(c.sent.length, 0);
  assert.equal((await observation.facts()).corpus.count, 1);
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
    return args[0] === "state-root" ? f.b : JSON.stringify({
      active_effort: { active: true, path: "effort.md", participant: "driver", slice: "phase" },
    });
  };
  assert.equal(readCanonicalGoal(f.a, execute).goal, "Full mission.");
  writeFileSync(effort, "# Work\n\n## Goal\n\nChanged objective.\n");
  assert.equal(readCanonicalGoal(f.a, execute).goal, "Changed objective.");
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
  const result = spawnSync(process.execPath, [cli, "intent-page", "--snapshot", snapshot.id,
    "--record", String(first.next.record), "--offset", String(first.next.offset), "--json"], {
    cwd: f.a, env: { ...process.env, HOME: f.root, USERPROFILE: f.root },
    encoding: "utf8", windowsHide: true, timeout: 10000,
  });
  assert.equal(result.status, 0, result.stderr);
  const second = JSON.parse(result.stdout);
  assert.equal(second.next, null);
  assert.equal([...first.records, ...second.records].map((v) => v.text).join(""), text);
});
