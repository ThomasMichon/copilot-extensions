import { test } from "node:test";
import assert from "node:assert/strict";
import {
  discoveryDir,
  descriptorPath,
  generateToken,
  buildDescriptor,
  authorizes,
} from "../extensions/agent-remote-driver/discovery.mjs";

test("discoveryDir honors AGENT_REMOTE_DRIVER_CONFIG_DIR override", () => {
  const dir = discoveryDir({ AGENT_REMOTE_DRIVER_CONFIG_DIR: "/tmp/xyz" });
  assert.match(dir, /sessions$/);
  assert.match(dir, /xyz/);
});

test("discoveryDir falls back to homedir()/.copilot/remote-driver/sessions", () => {
  const dir = discoveryDir({});
  assert.match(dir, /\.copilot/);
  assert.match(dir, /remote-driver/);
  assert.match(dir, /sessions$/);
});

test("descriptorPath nests the session id under discoveryDir", () => {
  const p = descriptorPath("abc-123", { AGENT_REMOTE_DRIVER_CONFIG_DIR: "/tmp/xyz" });
  assert.match(p, /abc-123\.json$/);
  assert.match(p, /xyz/);
});

test("generateToken produces distinct, reasonably long tokens", () => {
  const a = generateToken();
  const b = generateToken();
  assert.notEqual(a, b);
  assert.ok(a.length >= 32, `token too short: ${a.length}`);
});

test("buildDescriptor produces the expected shape", () => {
  const d = buildDescriptor({
    sessionId: "s1",
    pid: 1234,
    port: 5555,
    token: "tok",
    cwd: "/workspaces/example",
  });
  assert.equal(d.version, 1);
  assert.equal(d.sessionId, "s1");
  assert.equal(d.pid, 1234);
  assert.equal(d.host, "127.0.0.1");
  assert.equal(d.port, 5555);
  assert.equal(d.token, "tok");
  assert.equal(d.cwd, "/workspaces/example");
  assert.ok(d.startedAt);
  assert.ok(d.updatedAt);
});

test("buildDescriptor defaults updatedAt to 'now' when not given, independent of a startedAt override", () => {
  const before = Date.now();
  const d = buildDescriptor({ sessionId: "s1", pid: 1, port: 1, token: "t", startedAt: "2026-01-01T00:00:00.000Z" });
  assert.equal(d.startedAt, "2026-01-01T00:00:00.000Z");
  assert.ok(Date.parse(d.updatedAt) >= before);
});

test("buildDescriptor lets updatedAt be refreshed independently of startedAt (heartbeat)", () => {
  const d = buildDescriptor({
    sessionId: "s1",
    pid: 1,
    port: 1,
    token: "t",
    startedAt: "2026-01-01T00:00:00.000Z",
    updatedAt: "2026-01-01T00:05:00.000Z",
  });
  assert.equal(d.startedAt, "2026-01-01T00:00:00.000Z");
  assert.equal(d.updatedAt, "2026-01-01T00:05:00.000Z");
});

test("buildDescriptor rejects a missing sessionId/pid/port/token", () => {
  assert.throws(() => buildDescriptor({ pid: 1, port: 1, token: "t" }));
  assert.throws(() => buildDescriptor({ sessionId: "s", port: 1, token: "t" }));
  assert.throws(() => buildDescriptor({ sessionId: "s", pid: 1, token: "t" }));
  assert.throws(() => buildDescriptor({ sessionId: "s", pid: 1, port: 1 }));
});

test("authorizes accepts a matching Bearer token", () => {
  assert.equal(authorizes("Bearer secret-token", "secret-token"), true);
});

test("authorizes rejects a mismatched token", () => {
  assert.equal(authorizes("Bearer wrong", "secret-token"), false);
});

test("authorizes rejects malformed/missing headers without throwing", () => {
  assert.equal(authorizes(undefined, "secret-token"), false);
  assert.equal(authorizes("", "secret-token"), false);
  assert.equal(authorizes("Basic foo", "secret-token"), false);
  assert.equal(authorizes("Bearer ", "secret-token"), false);
});

test("authorizes rejects when no token is configured", () => {
  assert.equal(authorizes("Bearer anything", ""), false);
  assert.equal(authorizes("Bearer anything", undefined), false);
});
