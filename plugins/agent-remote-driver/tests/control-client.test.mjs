import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync, rmSync, existsSync, linkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createDriverServer } from "../extensions/agent-remote-driver/driver-server.mjs";
import { createControlClient } from "../extensions/agent-remote-driver/control-client.mjs";
import { controlSession } from "../bin/control-session.mjs";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

function runHelper(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [
      fileURLToPath(new URL("../bin/control-session.mjs", import.meta.url)), ...args,
    ], { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => child.kill(), 5_000);
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", (error) => { clearTimeout(timer); reject(error); });
    child.on("close", (status) => { clearTimeout(timer); resolve({ status, stdout, stderr }); });
  });
}

async function fixture(fn, native = {}) {
  const calls = [];
  let server;
  server = createDriverServer({
    token: "observer-secret", controlToken: "admin-secret",
    getSessionId: () => "native-session", getPid: () => process.pid,
    async send(options) {
      calls.push(options);
      if (native.send) return native.send(options);
      server.control.observe({ type: "user.message", id: `message-${calls.length}`, data: { source: options.source } });
      return { accepted: true };
    },
    abort: async () => native.abort ? native.abort(server) : { aborted: true },
    subscribe: () => () => {},
  });
  server.control.markReady();
  const port = await server.listen();
  const descriptor = {
    host: "127.0.0.1", port, token: "observer-secret", controlToken: "admin-secret",
    controlProtocol: 2, instanceId: server.control.snapshot().instanceId, sessionId: "native-session",
  };
  const dir = mkdtempSync(join(tmpdir(), "driver-control-client-"));
  const descriptorPath = join(dir, "descriptor.json");
  const statePath = join(dir, "private-claim.json");
  writeFileSync(descriptorPath, JSON.stringify(descriptor), { mode: 0o600 });
  try {
    await fn({ descriptor, descriptorPath, statePath, dir, server, calls });
  } finally {
    server.server.closeAllConnections();
    await server.close();
    rmSync(dir, { recursive: true });
  }
}

test("the real standalone helper re-hooks, drives, observes takeover and re-hooks with a fresh claim", async () => {
  await fixture(async ({ descriptorPath, statePath, server, calls }) => {
    const run = (action, extra = {}) => controlSession({ descriptorPath, statePath, action, ...extra });
    assert.equal((await run("status")).control.mode, "human");
    await run("re-hook", { ownerId: "local-controller" });
    const old = JSON.parse(readFileSync(statePath)).lease;
    assert.equal((await run("send", { content: "first" })).control.lastSequence, 1);
    assert.equal(calls.length, 1);
    server.control.observe({ type: "user.message", id: "real-user-input", data: { source: "user" } });
    await assert.rejects(run("steer", { content: "stale" }), (error) => error.code === "generation_stale");
    assert.equal(calls.length, 1);
    assert.equal(JSON.parse(readFileSync(statePath)).pending, true);
    await assert.rejects(run("send", { content: "retry" }), /No usable claim state/);
    const rehooked = await run("re-hook", { ownerId: "local-controller" });
    assert.equal(JSON.stringify(rehooked).includes(old.token), false);
    const fresh = JSON.parse(readFileSync(statePath)).lease;
    assert.notEqual(fresh.token, old.token);
    assert.notEqual(fresh.generation, old.generation);
    await run("steer", { content: "hooked back up" });
    assert.equal(calls.length, 2);
    assert.equal(calls[1].mode, "immediate");
    await run("release");
    assert.equal(JSON.parse(readFileSync(statePath)).lease, undefined);
    assert.equal((await run("status")).control.mode, "available");
    await run("claim", { ownerId: "next-controller" });
  });
});

test("the private claim-state lock prevents simultaneous helpers from racing the same sequence", async () => {
  let settle;
  let signal;
  const started = new Promise((resolve) => { signal = resolve; });
  const pending = new Promise((resolve) => { settle = resolve; });
  try {
    await fixture(async ({ descriptorPath, statePath, calls }) => {
      const run = (action, extra = {}) => controlSession({ descriptorPath, statePath, action, ...extra });
      await run("re-hook", { ownerId: "controller" });
      const first = run("send", { content: "one" });
      const timeout = setTimeout(signal, 1_000);
      await started;
      clearTimeout(timeout);
      try {
        assert.equal(calls.length, 1);
        await assert.rejects(run("send", { content: "two" }), /Claim state is locked/);
      } finally {
        settle({ accepted: true });
      }
      await first;
      assert.equal(existsSync(`${statePath}.lock`), false);
      assert.equal(calls.length, 1);
    }, { send: async () => { signal(); return pending; } });
  } finally {
    settle({ accepted: true });
  }
});

test("unknown native outcomes remain pending on disk and are never automatically retried", async () => {
  await fixture(async ({ descriptorPath, statePath, calls }) => {
    const run = (action, extra = {}) => controlSession({ descriptorPath, statePath, action, ...extra });
    await run("re-hook", { ownerId: "controller" });
    await assert.rejects(run("send", { content: "uncertain" }), (error) => error.code === "native_control_failed");
    const saved = JSON.parse(readFileSync(statePath));
    assert.equal(saved.pending, true);
    assert.equal(saved.sequence, 1);
    await assert.rejects(run("send", { content: "never replay" }), /No usable claim state/);
    assert.equal(calls.length, 1);
  }, { send: async () => { throw new Error("connection lost"); } });
});

test("clients refuse redirecting, old-protocol and wrong-instance discovery", async () => {
  for (const descriptor of [
    { host: "example.com", port: 1234, token: "secret", controlProtocol: 2, instanceId: "i", sessionId: "s" },
    { host: "127.0.0.1", port: 1234, token: "secret", controlProtocol: 1, instanceId: "i", sessionId: "s" },
  ]) assert.throws(() => createControlClient(descriptor), (error) => error.code === "descriptor_invalid");
  await fixture(async ({ descriptor }) => {
    await assert.rejects(createControlClient({ ...descriptor, instanceId: "stale" }).health(),
      (error) => error.code === "target_changed");
    const client = createControlClient(descriptor, { fetchImpl: async (_, init) => {
      assert.equal(init.redirect, "error");
      throw new Error("raw confidential response");
    } });
    await assert.rejects(client.invoke("send", {
      instanceId: descriptor.instanceId, sessionId: descriptor.sessionId,
    }, 1, "text"), (error) => {
      assert.equal(error.code, "transport_outcome_unknown");
      assert.equal(error.message.includes("confidential"), false);
      return true;
    });
  });
});

test("state paths cannot overwrite discovery and malformed state is not silently reset", async () => {
  await fixture(async ({ descriptorPath, statePath }) => {
    await assert.rejects(controlSession({
      descriptorPath, statePath: descriptorPath, action: "re-hook", ownerId: "driver",
    }), /must not overwrite/);
    writeFileSync(statePath, "{invalid");
    await assert.rejects(controlSession({ descriptorPath, statePath, action: "re-hook", ownerId: "driver" }), SyntaxError);
    assert.equal(readFileSync(statePath, "utf8"), "{invalid");
    assert.equal(existsSync(`${statePath}.lock`), false);
  });
});

test("hard-link and Windows case aliases cannot overwrite the discovery descriptor", async () => {
  await fixture(async ({ descriptorPath, statePath }) => {
    const before = readFileSync(descriptorPath, "utf8");
    linkSync(descriptorPath, statePath);
    await assert.rejects(controlSession({
      descriptorPath, statePath, action: "re-hook", ownerId: "driver",
    }), /must not overwrite/);
    assert.equal(readFileSync(descriptorPath, "utf8"), before);
    if (process.platform === "win32") {
      await assert.rejects(controlSession({
        descriptorPath, statePath: descriptorPath.toUpperCase(), action: "re-hook", ownerId: "driver",
      }), /must not overwrite/);
      assert.equal(readFileSync(descriptorPath, "utf8"), before);
    }
  });
});

test("the real CLI retains redacted partial-effect and recovery diagnostics", async () => {
  await fixture(async ({ descriptorPath, statePath }) => {
    await controlSession({ descriptorPath, statePath, action: "re-hook", ownerId: "driver" });
    const result = await runHelper(["abort", "--descriptor", descriptorPath, "--state", statePath]);
    assert.equal(result.status, 1);
    assert.equal(result.stdout, "");
    const diagnostic = JSON.parse(result.stderr);
    assert.equal(diagnostic.code, "driver_revoked");
    assert.equal(diagnostic.status, 409);
    assert.equal(diagnostic.effects.abort, "completed");
    assert.equal(diagnostic.effects.send, "not_started");
    assert.equal(diagnostic.control.mode, "human");
    assert.equal(result.stderr.includes("secret"), false);
  }, { abort: async (server) => {
    server.control.observe({ type: "user.message", id: "during-abort", data: { source: "user" } });
    return { aborted: true };
  } });
});
