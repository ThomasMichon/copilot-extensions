import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  discoveryDir,
  descriptorPath,
  generateToken,
  buildDescriptor,
  authorizes,
  writeDescriptorAtomic,
} from "../extensions/agent-remote-driver/discovery.mjs";

function tmpDir() {
  return mkdtempSync(join(tmpdir(), "agent-remote-driver-discovery-test-"));
}

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

// --- writeDescriptorAtomic (PR #5036 review: atomicity regression) ---

test("writeDescriptorAtomic writes the full, valid JSON content to the target path", () => {
  const dir = tmpDir();
  const path = join(dir, "s1.json");
  const descriptor = buildDescriptor({ sessionId: "s1", pid: process.pid, port: 1234, token: "tok" });

  writeDescriptorAtomic(path, descriptor);

  const reread = JSON.parse(readFileSync(path, "utf-8"));
  assert.deepEqual(reread, descriptor);
});

test("writeDescriptorAtomic never leaves a .tmp file behind on success", () => {
  const dir = tmpDir();
  const path = join(dir, "s1.json");
  writeDescriptorAtomic(path, buildDescriptor({ sessionId: "s1", pid: process.pid, port: 1, token: "t" }));

  const names = readdirSync(dir);
  assert.deepEqual(names, ["s1.json"]);
});

test("writeDescriptorAtomic replaces an existing descriptor's full content (the heartbeat-rewrite case)", () => {
  const dir = tmpDir();
  const path = join(dir, "s1.json");
  const first = buildDescriptor({ sessionId: "s1", pid: process.pid, port: 1, token: "t", updatedAt: "2026-01-01T00:00:00.000Z" });
  writeDescriptorAtomic(path, first);

  const second = buildDescriptor({ sessionId: "s1", pid: process.pid, port: 1, token: "t", updatedAt: "2026-01-01T00:01:00.000Z" });
  writeDescriptorAtomic(path, second);

  const reread = JSON.parse(readFileSync(path, "utf-8"));
  assert.equal(reread.updatedAt, "2026-01-01T00:01:00.000Z");
  // Exactly one file -- the rename replaced the old content, it didn't append
  // a second copy or leave the previous version alongside it.
  assert.deepEqual(readdirSync(dir), ["s1.json"]);
});

test("writeDescriptorAtomic never produces a truncated/partial read: a reader only ever sees complete JSON", () => {
  // Can't literally interleave two OS threads from a single-threaded Node
  // test, but we can confirm the mechanism itself: content only ever lands
  // at the final path via rename (not via in-place truncate+write), so a
  // reader racing this call either sees the complete prior file or the
  // complete new one -- never a half-written one. Write once, then
  // immediately re-read and fully parse without error, many times in a row,
  // as a sanity check that the written bytes are always a complete,
  // parseable document.
  const dir = tmpDir();
  const path = join(dir, "s1.json");
  for (let i = 0; i < 20; i += 1) {
    const descriptor = buildDescriptor({ sessionId: "s1", pid: process.pid, port: i + 1, token: "t" });
    writeDescriptorAtomic(path, descriptor);
    const reread = JSON.parse(readFileSync(path, "utf-8")); // throws on any partial/invalid content
    assert.equal(reread.port, i + 1);
  }
});

test("writeDescriptorAtomic's temp filename is unique per pid+timestamp (no cross-write collision)", () => {
  const dir = tmpDir();
  const path = join(dir, "s1.json");
  // Pre-seed an unrelated stray .tmp file to confirm writeDescriptorAtomic
  // doesn't accidentally pick it up or collide with it.
  writeFileSync(join(dir, `s1.json.${process.pid}.stale.tmp`), "stale");
  writeDescriptorAtomic(path, buildDescriptor({ sessionId: "s1", pid: process.pid, port: 1, token: "t" }));

  const names = readdirSync(dir).sort();
  assert.deepEqual(names, [`s1.json.${process.pid}.stale.tmp`, "s1.json"].sort());
  assert.equal(JSON.parse(readFileSync(path, "utf-8")).sessionId, "s1");
});

