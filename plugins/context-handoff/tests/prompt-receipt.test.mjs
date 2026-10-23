import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, readFileSync, existsSync, copyFileSync } from "node:fs";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { registerPromptReceiptObservation } from "../extensions/context-handoff/prompt-receipt.mjs";
import { observeHandoffPromptReceived, runReceiptChild } from "../extensions/context-handoff/handoff-core.mjs";

const prompt = "Continue work | Recovery: context-handoff task:receipt-1";
function harness(options = {}) {
  const handlers = new Map();
  const warnings = [];
  const session = {
    on(name, callback) {
      const callbacks = handlers.get(name) || new Set();
      callbacks.add(callback);
      handlers.set(name, callbacks);
      return () => callbacks.delete(callback);
    },
    log: (message) => warnings.push(message),
  };
  const state = { turnCount: 0, cwd: process.cwd(), sessionId: "successor" };
  const observation = registerPromptReceiptObservation(session, state, { env: {}, ...options });
  return {
    state, session, observation, warnings,
    emit: (name, event = {}) => {
      for (const callback of handlers.get(name) || []) callback(event);
    },
  };
}
async function settle(h) {
  for (let i = 0; i < 400; i++) {
    if (h.observation.status.status !== "pending") return;
    await delay(10);
  }
  assert.fail("observation did not settle");
}
function payload() {
  const root = mkdtempSync(join(process.cwd(), ".receipt-test-"));
  mkdirSync(join(root, "scripts"));
  writeFileSync(join(root, "plugin.json"), JSON.stringify({
    name: "agent-worktrees", repository: "https://github.com/ThomasMichon/copilot-extensions",
  }));
  return root;
}
function assertStopped(pid) {
  if (process.platform === "linux") {
    try {
      // A group-killed descendant can briefly await its new parent's reap.
      if (/^\s*Z\s/.test(readFileSync(join("/proc", String(pid), "stat"), "utf-8").split(")").at(-1))) return;
    } catch { /* a reaped process has no proc entry */ }
  }
  assert.throws(() => process.kill(pid, 0), { code: "ESRCH" });
}
function fixtureExecutor(calls, { replay = false, unavailable = false } = {}) {
  return async (bin, args, opts) => {
    calls.push({ bin, args, opts });
    if (calls.length === 1) return { stdout: unavailable ? "" : process.execPath };
    if (args.includes("get")) return { stdout: join(process.cwd(), "owned") };
    if (args.includes("activity")) return {
      stdout: replay ? JSON.stringify({ locator: "task:receipt-1", session_id: "successor" }) : "",
    };
    return { stdout: "" };
  };
}

test("registered first-prompt callback returns before observation; duplicates coalesce", async () => {
  let calls = 0;
  let release;
  const h = harness({ observe: () => { calls++; return new Promise((r) => { release = r; }); } });
  try {
    const start = performance.now();
    h.emit("user.message", { data: { content: prompt } });
    assert.ok(performance.now() - start < 100);
    assert.equal(calls, 0);
    assert.equal(registerPromptReceiptObservation(h.session, h.state), h.observation);
    for (let i = 0; i < 100; i++) {
      h.emit("user.message", { data: { content: prompt } });
      h.emit("session.idle");
    }
    await delay(10);
    assert.equal(calls, 1);
    release({ status: "logged" });
    await settle(h);
    h.emit("user.message", { data: { content: prompt } });
    await delay(10);
    assert.equal(calls, 1);
  } finally { await h.observation.dispose(); }
});

test("unavailable observation retains first locator and retries only at a later boundary", async () => {
  let clock = 0;
  const calls = [];
  const h = harness({
    now: () => clock, retryMs: 100,
    observe: async (input) => {
      calls.push(input);
      return { status: calls.length === 1 ? "unavailable" : "logged" };
    },
  });
  try {
    h.emit("user.message", { data: { content: prompt } });
    await settle(h);
    h.emit("session.idle");
    await delay(10);
    assert.equal(calls.length, 1);
    assert.equal(h.warnings.length, 1);
    clock = 101;
    h.emit("user.message", { data: { content: "Recovery: context-handoff task:other" } });
    await settle(h);
    assert.equal(calls.length, 2);
    assert.equal(calls[1].locator, "task:receipt-1");
  } finally { await h.observation.dispose(); }
});

test("qualified launch identity avoids identity query; legacy ambient identity is ignored", async () => {
  const calls = [];
  const root = payload();
  const h = harness({
    env: { AGENT_WORKTREES_OWNER_REF: "host/project/owned#successor", WORKTREE_ID: "wrong" },
    observe: (input) => observeHandoffPromptReceived(input, {
      payloadRoot: root, execute: fixtureExecutor(calls),
    }),
  });
  try {
    h.emit("user.message", { data: { content: prompt } });
    await settle(h);
    assert.equal(h.observation.status.status, "logged");
    assert.equal(calls.some(({ args }) => args.includes("get")), false);
    assert.ok(calls.at(-1).args.includes("owned"));
    assert.ok(calls.at(-1).args.includes("project"));
    assert.equal(calls.at(-1).opts.windowsHide, true);
  } finally {
    await h.observation.dispose();
    rmSync(root, { recursive: true, force: true });
  }
});

test("receipt replay reconciles without a second append", async () => {
  const root = payload();
  const calls = [];
  try {
    const result = await observeHandoffPromptReceived({
      cwd: process.cwd(), sid: "successor", locator: "task:receipt-1",
    }, { payloadRoot: root, execute: fixtureExecutor(calls, { replay: true }) });
    assert.deepEqual(result, { status: "logged", replayed: true });
    assert.equal(calls.some(({ args }) => args.includes("activity-log")), false);
    assert.ok(calls.find(({ args }) => args.includes("get")).args.includes("--session-id"));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("absent service fails visibly and missing session never starts a child", async () => {
  const root = payload();
  const calls = [];
  try {
    const execute = async (bin, args, options) => {
      calls.push(args);
      if (calls.length === 1) return { stdout: process.execPath };
      throw new Error("service unavailable");
    };
    assert.equal((await observeHandoffPromptReceived({
      cwd: process.cwd(), sid: null, locator: "task:receipt-1",
    }, { payloadRoot: root, execute })).status, "unavailable");
    assert.equal(calls.length, 0);
    assert.equal((await observeHandoffPromptReceived({
      cwd: process.cwd(), sid: "successor", locator: "task:receipt-1",
      launchIdentity: { project: "project", worktreeId: "owned" },
    }, { payloadRoot: root, execute })).status, "unavailable");
    assert.equal(calls.length, 2);
    assert.equal(calls.some((args) => args.includes("activity-log")), false);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("native consumer succeeds after deferred runtime availability", async (t) => {
  const root = payload();
  let available = false;
  let clock = 0;
  const commands = [];
  const h = harness({
    now: () => clock, retryMs: 10,
    env: { AGENT_WORKTREES_OWNER_REF: "host/project/owned#successor" },
    observe: (input) => observeHandoffPromptReceived(input, {
      payloadRoot: root,
      execute: (bin, args, options) => {
        commands.push(args);
        if (!args.includes("-m")) return Promise.resolve({ stdout: available ? process.execPath : "" });
        return runReceiptChild(process.execPath, ["-e", "process.stdout.write('')"], options);
      },
    }),
  });
  try {
    h.emit("user.message", { data: { content: prompt } });
    await settle(h);
    assert.equal(h.observation.status.status, "unavailable");
    available = true;
    clock = 11;
    const start = performance.now();
    h.emit("session.idle");
    await settle(h);
    assert.equal(h.observation.status.status, "logged");
    assert.equal(commands.filter((args) => args.includes("activity-log")).length, 1);
    t.diagnostic(`deferred native receipt lifecycle=${(performance.now() - start).toFixed(2)}ms`);
  } finally {
    await h.observation.dispose();
    rmSync(root, { recursive: true, force: true });
  }
});

test("extension uses the tested registration boundary rather than the synchronous logger", () => {
  const source = readFileSync(new URL("../extensions/context-handoff/extension.mjs", import.meta.url), "utf-8");
  assert.match(source, /registerPromptReceiptObservation\(session, state\)/);
  assert.doesNotMatch(source, /logHandoffPromptReceived/);
  assert.doesNotMatch(source, /session\.on\("user\.message"/);
});

test("isolated first touch runs the native resolver without invoking a provisioner", async (t) => {
  const root = payload();
  for (const name of ["resolve-runtime.ps1", "resolve-runtime.sh"]) {
    copyFileSync(new URL(`../../agent-worktrees/scripts/${name}`, import.meta.url), join(root, "scripts", name));
  }
  const calls = [];
  const h = harness({
    env: { ...process.env, HOME: root, USERPROFILE: root },
    observe: (input) => observeHandoffPromptReceived(input, {
      payloadRoot: root,
      execute: (bin, args, options) => {
        calls.push(args);
        return runReceiptChild(bin, args, options);
      },
    }),
  });
  try {
    const start = performance.now();
    h.emit("user.message", { data: { content: prompt } });
    const callbackMs = performance.now() - start;
    await settle(h);
    const lifecycleMs = performance.now() - start;
    assert.ok(callbackMs < 100);
    assert.ok(lifecycleMs < 2000);
    assert.equal(h.observation.status.status, "unavailable");
    assert.equal(calls.length, 1);
    assert.equal(calls[0].includes("--help"), false);
    assert.equal(existsSync(join(root, ".agent-worktrees")), false);
    t.diagnostic(`missing runtime callback=${callbackMs.toFixed(2)}ms lifecycle=${lifecycleMs.toFixed(2)}ms`);
  } finally {
    await h.observation.dispose();
    rmSync(root, { recursive: true, force: true });
  }
});

for (const cancel of [false, true]) {
  test(`native blocked identity child is joined after ${cancel ? "dispose" : "deadline"}`, async (t) => {
    const root = payload();
    const pidFile = join(root, "child.pid");
    const descendantFile = join(root, "descendant.pid");
    let calls = 0;
    const h = harness({
      observe: (input) => observeHandoffPromptReceived(input, {
        payloadRoot: root,
        execute: (bin, args, options) => {
          calls++;
          if (calls === 1) return Promise.resolve({ stdout: process.execPath });
          return runReceiptChild(process.execPath, [
            "-e", [
              "const fs=require('node:fs');",
              "const c=require('node:child_process').spawn(process.execPath,",
              "['-e','setInterval(()=>{},1000)'],{windowsHide:true});",
              "fs.writeFileSync(process.argv[2],String(c.pid));",
              "fs.writeFileSync(process.argv[1],String(process.pid));",
              "setInterval(()=>{},1000);",
            ].join(""),
            pidFile, descendantFile,
          ], options);
        },
      }),
    });
    try {
      let heartbeats = 0;
      const timer = setInterval(() => { heartbeats++; }, 10);
      const start = performance.now();
      h.emit("user.message", { data: { content: prompt } });
      const callbackMs = performance.now() - start;
      if (cancel) {
        for (let i = 0; i < 100 && !existsSync(pidFile); i++) await delay(10);
        assert.ok(existsSync(pidFile), "blocked child reached startup before cancellation");
        await h.observation.dispose();
      }
      else await settle(h);
      clearInterval(timer);
      const lifecycleMs = performance.now() - start;
      assert.ok(callbackMs < 100);
      assert.ok(lifecycleMs < 2000);
      assert.ok(heartbeats > 3);
      assert.equal(calls, 2);
      const pid = Number(readFileSync(pidFile, "utf-8"));
      assertStopped(pid);
      const descendant = Number(readFileSync(descendantFile, "utf-8"));
      assertStopped(descendant);
      t.diagnostic(`blocked identity callback=${callbackMs.toFixed(2)}ms lifecycle=${lifecycleMs.toFixed(2)}ms; child tree gone`);
    } finally {
      await h.observation.dispose();
      rmSync(root, { recursive: true, force: true });
    }
  });
}
