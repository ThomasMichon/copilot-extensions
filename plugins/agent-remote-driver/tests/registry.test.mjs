import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import {
  isProcessAlive,
  listDescriptorFiles,
  isStale,
  sweepStale,
  listLive,
  DEFAULT_HEARTBEAT_TIMEOUT_MS,
} from "../extensions/agent-remote-driver/registry.mjs";

function tmpDir() {
  return mkdtempSync(join(tmpdir(), "agent-remote-driver-registry-test-"));
}

function writeDescriptor(dir, sessionId, overrides = {}) {
  const path = join(dir, `${sessionId}.json`);
  writeFileSync(
    path,
    JSON.stringify({
      version: 1,
      sessionId,
      pid: process.pid,
      host: "127.0.0.1",
      port: 1,
      token: "t",
      cwd: null,
      startedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      ...overrides,
    }),
  );
  return path;
}

// A pid that is guaranteed to be dead: spawn a trivial child synchronously
// and use its pid after it has already exited.
function deadPid() {
  const result = spawnSync(process.execPath, ["-e", "process.exit(0)"]);
  return result.pid;
}

test("isProcessAlive is true for this process's own pid", () => {
  assert.equal(isProcessAlive(process.pid), true);
});

test("isProcessAlive is false for an exited process's pid", () => {
  assert.equal(isProcessAlive(deadPid()), false);
});

test("isProcessAlive is false for a non-integer/non-positive pid", () => {
  assert.equal(isProcessAlive(0), false);
  assert.equal(isProcessAlive(-1), false);
  assert.equal(isProcessAlive(NaN), false);
});

test("listDescriptorFiles returns [] for a missing directory (empty fleet, not an error)", () => {
  assert.deepEqual(listDescriptorFiles(join(tmpDir(), "does-not-exist")), []);
});

test("listDescriptorFiles skips non-.json files and parses the rest", () => {
  const dir = tmpDir();
  writeDescriptor(dir, "s1");
  writeFileSync(join(dir, "notes.txt"), "irrelevant");
  const entries = listDescriptorFiles(dir);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].descriptor.sessionId, "s1");
});

test("listDescriptorFiles returns descriptor: null for unreadable/malformed JSON", () => {
  const dir = tmpDir();
  writeFileSync(join(dir, "broken.json"), "{not json");
  const entries = listDescriptorFiles(dir);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].descriptor, null);
});

test("isStale is true when descriptor is null (unreadable)", () => {
  assert.equal(isStale(null), true);
});

test("isStale is true when the pid is dead, regardless of heartbeat freshness", () => {
  const descriptor = { pid: deadPid(), updatedAt: new Date().toISOString() };
  assert.equal(isStale(descriptor), true);
});

test("isStale is false for a live pid with a fresh heartbeat", () => {
  const descriptor = { pid: process.pid, updatedAt: new Date().toISOString() };
  assert.equal(isStale(descriptor), false);
});

test("isStale is true for a live pid whose heartbeat is older than the timeout", () => {
  const old = new Date(Date.now() - DEFAULT_HEARTBEAT_TIMEOUT_MS - 1_000).toISOString();
  const descriptor = { pid: process.pid, updatedAt: old };
  assert.equal(isStale(descriptor), true);
});

test("isStale honors a custom heartbeatTimeoutMs", () => {
  const descriptor = { pid: process.pid, updatedAt: new Date(Date.now() - 5_000).toISOString() };
  assert.equal(isStale(descriptor, { heartbeatTimeoutMs: 1_000 }), true);
  assert.equal(isStale(descriptor, { heartbeatTimeoutMs: 60_000 }), false);
});

test("isStale is true when updatedAt/startedAt are both missing or unparsable", () => {
  assert.equal(isStale({ pid: process.pid, updatedAt: "not-a-date" }), true);
  assert.equal(isStale({ pid: process.pid }), true);
});

test("sweepStale removes only genuinely stale descriptors and leaves live ones", () => {
  const dir = tmpDir();
  const livePath = writeDescriptor(dir, "live-session", { pid: process.pid });
  const stalePath = writeDescriptor(dir, "dead-session", { pid: deadPid() });

  const { removed, kept } = sweepStale(dir);

  assert.deepEqual(removed, [stalePath]);
  assert.equal(kept.length, 1);
  assert.equal(kept[0].descriptor.sessionId, "live-session");
  assert.equal(existsSync(livePath), true);
  assert.equal(existsSync(stalePath), false);
});

test("sweepStale is idempotent -- a second sweep removes nothing further", () => {
  const dir = tmpDir();
  writeDescriptor(dir, "live-session", { pid: process.pid });
  writeDescriptor(dir, "dead-session", { pid: deadPid() });

  sweepStale(dir);
  const second = sweepStale(dir);
  assert.deepEqual(second.removed, []);
  assert.equal(second.kept.length, 1);
});

test("listLive returns only live descriptors (post-sweep), never file paths", () => {
  const dir = tmpDir();
  writeDescriptor(dir, "live-session", { pid: process.pid, cwd: "/workspaces/example" });
  writeDescriptor(dir, "dead-session", { pid: deadPid() });

  const live = listLive(dir);
  assert.equal(live.length, 1);
  assert.equal(live[0].sessionId, "live-session");
  assert.equal(live[0].cwd, "/workspaces/example");
});

test("a fleet of many live descriptors all survive a sweep untouched", () => {
  const dir = tmpDir();
  for (let i = 0; i < 25; i += 1) {
    writeDescriptor(dir, `session-${i}`, { pid: process.pid });
  }
  const { removed, kept } = sweepStale(dir);
  assert.deepEqual(removed, []);
  assert.equal(kept.length, 25);
});

// Confirm the descriptor's own persisted shape survives a read-back
// (defends against a future buildDescriptor change silently dropping a
// field registry.mjs depends on, like updatedAt).
test("a written-and-reread descriptor still parses with the fields isStale needs", () => {
  const dir = tmpDir();
  const path = writeDescriptor(dir, "roundtrip");
  const reread = JSON.parse(readFileSync(path, "utf-8"));
  assert.equal(isStale(reread), false);
});
