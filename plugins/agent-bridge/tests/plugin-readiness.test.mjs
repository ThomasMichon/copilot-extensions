import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { observePluginReadiness } from "../extensions/agent-bridge/plugin-readiness.mjs";
import { controlPlan, drainControls, drainInbox, InFlightMessages } from "../extensions/agent-bridge/delivery.mjs";

function fixture(t, rows = [{ name: "hooks-only", marketplace: "alias", enabled: true }]) {
  const cwd = mkdtempSync(join(process.cwd(), ".plugin-readiness-"));
  t.after(() => rmSync(cwd, { recursive: true, force: true }));
  const request = { owner: "bridge", cwd, expected_session_id: "native",
    selection_digest: "selected", timeout_seconds: 1,
    plugins: [{ projected_source: "hooks-only@alias", logical_source: "hooks-only@original" }] };
  const session = { sessionId: "native", rpc: {
    metadata: { snapshot: async () => ({ sessionId: "native", workingDirectory: cwd }) },
    plugins: { list: async () => ({ plugins: rows }) },
  } };
  return { cwd, request, session };
}

test("current golden task is held until its declared native loadout registers", async (t) => {
  const golden = JSON.parse(readFileSync(new URL(
    "../contract/fixtures/http/current/startup-plugin-readiness-generation-44.json", import.meta.url,
  ), "utf8"));
  const f = fixture(t, []);
  const queued = structuredClone(golden.queued_message);
  const expected = queued.plugin_readiness;
  expected.cwd = f.cwd;
  expected.plugins[0].live_marketplace_root = f.cwd;
  f.session.rpc.metadata.snapshot = async () => ({
    sessionId: expected.expected_session_id, workingDirectory: f.cwd,
  });
  const sent = [], acknowledged = [];
  f.session.send = async (options) => sent.push(options);
  const dependencies = {
    getJson: async () => ({ messages: [queued] }),
    post: async (_method, _path, body) => acknowledged.push(body),
    session: f.session, inFlight: new InFlightMessages(), pluginReadiness: true,
  };
  assert.deepEqual(await drainInbox(expected.expected_session_id, dependencies), []);
  assert.deepEqual(sent, []);
  assert.deepEqual(acknowledged, []);
  const [name, marketplace] = expected.plugins[0].projected_source.split("@");
  f.session.rpc.plugins.list = async () => ({ plugins: [{
    name, marketplace, enabled: true, installed: true,
    version: expected.plugins[0].version, installedFrom: f.cwd,
  }] });
  const observed = await observePluginReadiness(f.session, expected);
  const { control_id: _controlId, ...nativeResult } = golden.readiness_response;
  assert.deepEqual(observed, { ...nativeResult, cwd: f.cwd });
  assert.deepEqual(await drainInbox(expected.expected_session_id, dependencies), [queued.id]);
  assert.equal(sent.length, 1);
  assert.ok(sent[0].prompt.includes(golden.request.body));
  assert.deepEqual(acknowledged, [{ ids: [queued.id] }]);
});

for (const change of ["none", "disabled", "cwd"]) {
  test(`owned autopilot rechecks ${change} at application, not only admission`, async (t) => {
    const f = fixture(t);
    const modes = [], acknowledgments = [];
    f.session.rpc.mode = { set: async ({ mode }) => {
      modes.push(mode);
      return { modeApplied: true };
    } };
    if (change === "disabled") {
      f.session.rpc.plugins.list = async () => ({
        plugins: [{ name: "hooks-only", marketplace: "alias", enabled: false }],
      });
    }
    if (change === "cwd") {
      const changed = join(f.cwd, "changed");
      mkdirSync(changed);
      f.session.rpc.metadata.snapshot = async () => ({
        sessionId: "native", workingDirectory: changed,
      });
    }
    await drainControls("native", {
      getJson: async () => ({ messages: [{
        id: 71, kind: "control:set-mode-ready",
        body: JSON.stringify({ mode: "autopilot", plugin_readiness: f.request }),
      }] }),
      post: async (_method, _path, body) => acknowledgments.push(body), session: f.session,
    });
    assert.deepEqual(modes, change === "none" ? ["autopilot"] : []);
    assert.equal(acknowledgments[0].applied, change === "none");
  });
}

test("a malformed owned mode cannot become an ungated ordinary mode", () => {
  assert.equal(controlPlan({
    kind: "control:set-mode-ready", body: JSON.stringify({ mode: "autopilot" }),
  }).action, "skip");
  assert.deepEqual(controlPlan({ kind: "control:set-mode", body: "autopilot" }), {
    action: "set-mode", mode: "autopilot",
  });
});

test("registered hooks/MCP-only loadout needs no skills or optional installed/path fields", async (t) => {
  const f = fixture(t);
  f.session.rpc.plugins.install = () => { throw new Error("no installation authorized"); };
  f.session.rpc.plugins.reload = () => { throw new Error("no reload authorized"); };
  const result = await observePluginReadiness(f.session, f.request);
  assert.equal(result.status, "ready");
  assert.equal(result.selection_digest, "selected");
});

test("a desired-not-installed selected record cannot hide behind an older installed row", async (t) => {
  const f = fixture(t, [
    { name: "hooks-only", marketplace: "alias", enabled: true },
    { name: "hooks-only", marketplace: "alias", enabled: true, installed: false, managed: true },
  ]);
  assert.equal((await observePluginReadiness(f.session, f.request, { waitForReady: false })).status, "failed");
});

test("missing, desired-not-installed and disabled states wait for actual enabled registration", async (t) => {
  const f = fixture(t);
  let calls = 0, clock = 0;
  f.session.rpc.plugins.list = async () => ({ plugins: [
    [], [{ name: "hooks-only", marketplace: "alias", enabled: true, installed: false }],
    [{ name: "hooks-only", marketplace: "alias", enabled: false }],
    [{ name: "hooks-only", marketplace: "alias", enabled: true }],
  ][Math.min(calls++, 3)] });
  const result = await observePluginReadiness(f.session, f.request, {
    now: () => clock, pause: async (ms) => { clock += ms; },
  });
  assert.equal(result.status, "ready");
  assert.equal(calls, 4);
});

for (const state of [
  [], [{ name: "hooks-only", marketplace: "alias", enabled: false }],
  [{ name: "hooks-only", marketplace: "alias", enabled: true, installed: false }],
]) {
  test(`unready ${JSON.stringify(state)} never becomes ready from elapsed time`, async (t) => {
    const f = fixture(t, state);
    let clock = 0;
    const result = await observePluginReadiness(f.session, f.request, {
      now: () => clock, pause: async (ms) => { clock += ms; },
    });
    assert.equal(result.status, "failed");
    assert.equal(result.reason, "readiness-timeout");
    assert.deepEqual(result.missing, ["hooks-only@alias"]);
  });
}

test("native operator root and known version are checked when advertised", async (t) => {
  const f = fixture(t);
  const override = join(f.cwd, "operator-root");
  mkdirSync(override);
  f.request.plugins[0].live_marketplace_root = override;
  f.request.plugins[0].version = "2.0.0";
  f.session.rpc.plugins.list = async () => ({ plugins: [
    { name: "hooks-only", marketplace: "alias", enabled: false, version: "1.0.0" },
    { name: "hooks-only", marketplace: "alias", enabled: true, installedFrom: override, version: "2.0.0",
      source: "generic-provenance-not-a-Git-URI" },
  ] });
  assert.equal((await observePluginReadiness(f.session, f.request)).status, "ready");
  f.request.plugins[0].version = "3.0.0";
  let clock = 0;
  const result = await observePluginReadiness(f.session, f.request, {
    now: () => clock, pause: async (ms) => { clock += ms; },
  });
  assert.equal(result.status, "failed");
});

test("versionless Git requirements accept the independently acquired native version", async (t) => {
  const f = fixture(t, [{
    name: "hooks-only", marketplace: "alias", enabled: true, installed: true,
    version: "2.0.0", source: "https://example.invalid/plugins.git",
  }]);
  assert.equal(Object.hasOwn(f.request.plugins[0], "version"), false);
  assert.equal((await observePluginReadiness(f.session, f.request)).status, "ready");
});

for (const change of ["sid", "cwd", "claim", "rpc"]) {
  test(`stale ${change} observation cannot authorize delivery`, async (t) => {
    const f = fixture(t);
    const other = join(f.cwd, "other");
    mkdirSync(other);
    let claimed = true;
    f.session.rpc.plugins.list = async () => {
      if (change === "sid") f.session.rpc.metadata.snapshot = async () => ({ sessionId: "replaced", workingDirectory: f.cwd });
      if (change === "cwd") f.session.rpc.metadata.snapshot = async () => ({ sessionId: "native", workingDirectory: other });
      if (change === "claim") claimed = false;
      if (change === "rpc") f.session.rpc = {};
      return { plugins: [{ name: "hooks-only", marketplace: "alias", enabled: true }] };
    };
    assert.equal((await observePluginReadiness(f.session, f.request, { isCurrent: () => claimed })).status, "failed");
  });
}

test("unsupported, malformed and rejected public calls fail explicitly", async (t) => {
  const f = fixture(t);
  const list = f.session.rpc.plugins.list;
  f.session.rpc.plugins.list = undefined;
  assert.equal((await observePluginReadiness(f.session, f.request)).status, "unsupported");
  f.session.rpc.plugins.list = async () => ({ plugins: [{ name: "missing-fields" }] });
  assert.equal((await observePluginReadiness(f.session, f.request)).reason, "malformed-plugin-row");
  f.session.rpc.plugins.list = async () => { throw new Error("native RPC rejected"); };
  assert.equal((await observePluginReadiness(f.session, f.request)).reason, "native-observation-error");
  f.session.rpc.plugins.list = list;
  assert.equal((await observePluginReadiness(f.session, f.request)).status, "ready");
});

test("cancellation ends a hanging readonly query without installation or task sends", async (t) => {
  const f = fixture(t);
  const controller = new AbortController();
  f.session.rpc.plugins.list = () => new Promise(() => {});
  const pending = observePluginReadiness(f.session, f.request, { signal: controller.signal });
  setImmediate(() => controller.abort());
  assert.equal((await pending).reason, "cancelled");
});

test("ordinary operator inbox traffic is not held by a pending owned observation", async (t) => {
  const f = fixture(t);
  let release;
  f.session.rpc.plugins.list = () => new Promise((resolve) => { release = resolve; });
  const pending = observePluginReadiness(f.session, f.request);
  while (!release) await new Promise((resolve) => setImmediate(resolve));
  const sent = [];
  f.session.send = async (options) => sent.push(options);
  await drainInbox("native", {
    getJson: async () => ({ messages: [{ id: 1, body: "operator input", sender: "operator" }] }),
    post: async () => ({ ok: true }), session: f.session, inFlight: new InFlightMessages(),
  });
  assert.equal(sent.length, 1);
  release({ plugins: [{ name: "hooks-only", marketplace: "alias", enabled: true }] });
  assert.equal((await pending).status, "ready");
});

test("existing control transport carries a typed result without invoking send/mode", async (t) => {
  const f = fixture(t);
  const posts = [];
  f.session.send = () => { throw new Error("observation is not a prompt"); };
  await drainControls("native", {
    getJson: async () => ({ messages: [{ id: 5, kind: "control:plugin-readiness", body: JSON.stringify(f.request) }] }),
    post: async (...args) => { posts.push(args); return { ok: true }; }, session: f.session,
  });
  assert.equal(posts.length, 1);
  assert.equal(posts[0][2].plugin_readiness.status, "ready");
});

test("optional metadata remains optional even with a known expected local root", async (t) => {
  const f = fixture(t);
  f.request.plugins[0].live_marketplace_root = f.cwd;
  f.request.plugins[0].version = "2.0.0";
  assert.equal((await observePluginReadiness(f.session, f.request)).status, "ready");
});

test("public rpc getters may return fresh wrappers for the same native session", async (t) => {
  const f = fixture(t);
  const snapshot = f.session.rpc.metadata.snapshot;
  const list = f.session.rpc.plugins.list;
  Object.defineProperty(f.session, "rpc", {
    get: () => ({ metadata: { snapshot }, plugins: { list } }),
  });

  test("authoritative snapshots, not a captured SDK placeholder, bind a resumed session", async (t) => {
    const f = fixture(t);
    f.session.sessionId = "captured-placeholder";
    assert.equal((await observePluginReadiness(f.session, f.request)).status, "ready");
  });
  assert.equal((await observePluginReadiness(f.session, f.request)).status, "ready");
});

test("a pending observation does not serialize later operator mode controls", async (t) => {
  const f = fixture(t);
  let release, changed = 0;
  f.session.rpc.plugins.list = () => new Promise((resolve) => { release = resolve; });
  f.session.rpc.mode = { set: async () => { changed++; return { status: "applied" }; } };
  const readinessTasks = new Map();
  const options = {
    getJson: async () => ({ messages: [
      { id: 1, kind: "control:plugin-readiness", body: JSON.stringify(f.request) },
      { id: 2, kind: "control:set-mode", body: "interactive" },
    ] }),
    post: async () => ({ ok: true }), session: f.session, readinessTasks,
  };
  await drainControls("native", options);
  assert.equal(changed, 1);
  assert.equal(readinessTasks.size, 1);
  options.getJson = async () => ({ messages: [{ id: 3, kind: "control:set-mode", body: "plan" }] });
  await drainControls("native", options);
  assert.equal(changed, 2);
  while (!release) await new Promise((resolve) => setImmediate(resolve));
  release({ plugins: [{ name: "hooks-only", marketplace: "alias", enabled: true }] });
  await Promise.all([...readinessTasks.values()].map((entry) => entry.task));
  assert.equal(readinessTasks.size, 0);
});

test("owned inbox send is held while pending without holding ordinary traffic", async (t) => {
  const f = fixture(t, []);
  const sent = [], posts = [], logs = [];
  const startup = { id: 8, sender: "bridge", body: "owned task", kind: "startup:prompt:5",
    plugin_readiness: f.request };
  let messages = [startup, { id: 9, sender: "operator", body: "ordinary input" }];
  f.session.send = async (options) => { sent.push(options); };
  const options = {
    getJson: async (path) => {
      assert.ok(path.endsWith("?plugin_readiness=true"));
      return { messages };
    },
    post: async (...args) => { posts.push(args); return { ok: true }; },
    session: f.session, inFlight: new InFlightMessages(), pluginReadiness: true,
    log: (message) => logs.push(message),
  };
  await drainInbox("native", options);
  assert.equal(sent.length, 1);
  assert.ok(sent[0].prompt.includes("ordinary input"));
  assert.deepEqual(posts[0][2].ids, [9]);
  assert.ok(logs.some((message) => message.includes("selected-loadout-pending")));
  messages = [startup];
  f.session.rpc.plugins.list = async () => ({ plugins: [{ name: "hooks-only", marketplace: "alias", enabled: true }] });
  await drainInbox("native", options);
  assert.equal(sent.length, 2);
  assert.ok(sent[1].prompt.includes("owned task"));
  assert.ok(!sent[1].prompt.includes('kind="startup:'));
  assert.deepEqual(posts[1][2].ids, [8]);
});

for (const changed of ["withdrawn", "cwd"]) {
  test(`owned send rechecks its retained message and ${changed} context`, async (t) => {
    const f = fixture(t);
    const other = join(f.cwd, "other");
    mkdirSync(other);
    let reads = 0, sends = 0;
    f.session.send = async () => { sends++; };
    const startup = { id: 8, sender: "bridge", body: "owned task", kind: "startup:prompt:5",
      plugin_readiness: f.request };
    await drainInbox("native", {
      getJson: async () => {
        if (++reads > 1) {
          if (changed === "withdrawn") return { messages: [] };
          f.session.rpc.metadata.snapshot = async () => ({ sessionId: "native", workingDirectory: other });
        }
        return { messages: [startup] };
      },
      post: async () => { throw new Error("unsubmitted message must not be acknowledged"); },
      session: f.session, inFlight: new InFlightMessages(), pluginReadiness: true,
    });
    assert.equal(sends, 0);
  });
}
