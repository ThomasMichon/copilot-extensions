import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, existsSync, readFileSync, readdirSync, renameSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import {
  isProcessAlive,
  listDescriptorFiles,
  isStale,
  sweepStale,
  reapIfStillStale,
  sweepOrphanedSidecars,
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

test("listDescriptorFiles propagates a non-ENOENT readdirSync failure instead of reporting an empty fleet", () => {
  // Pointing at a regular FILE (not a directory) makes readdirSync fail
  // with ENOTDIR, not ENOENT -- simulating a real "the registry exists but
  // is not actually readable as a directory" failure (EACCES/EIO are the
  // realistic causes in production) without needing platform-specific
  // permission APIs. Silently returning [] here would make a sweep falsely
  // "succeed" and make bin/list-sessions.mjs falsely report zero sessions
  // when the registry is actually inaccessible.
  const dir = tmpDir();
  const notADir = join(dir, "not-a-directory");
  writeFileSync(notADir, "");
  assert.throws(() => listDescriptorFiles(notADir), /ENOTDIR|ENOENT/);
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

test("isStale is true when updatedAt is present but unparsable (real corruption signal)", () => {
  assert.equal(isStale({ pid: process.pid, updatedAt: "not-a-date" }), true);
});

test("isStale is FALSE for a live pid with no updatedAt key at all -- legacy/pre-heartbeat compatibility", () => {
  // A descriptor entirely missing `updatedAt` (as opposed to one present but
  // garbage) is treated as pid-liveness-only -- a future rolling update
  // where an already-running older session never learned to write this
  // field must not have its still-alive descriptor reaped on heartbeat
  // grounds it was never taught to satisfy.
  assert.equal(isStale({ pid: process.pid }), false);
  assert.equal(isStale({ pid: process.pid, startedAt: new Date(Date.now() - 1_000_000).toISOString() }), false);
});

test("isStale is still true for a DEAD pid with no updatedAt key -- liveness alone still governs", () => {
  assert.equal(isStale({ pid: deadPid() }), true);
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

// --- TOCTOU regression coverage ---
//
// A sweep's staleness snapshot can go stale itself: the owning session can
// refresh its own heartbeat between the snapshot read and the delete. These
// tests simulate that race directly by mutating the file AFTER taking a
// stale snapshot, then calling reapIfStillStale with that stale snapshot --
// exactly the shape sweepStale's own loop produces.

test("reapIfStillStale does NOT delete a file that was refreshed after the snapshot was taken", () => {
  const dir = tmpDir();
  const deadSnapshot = { pid: deadPid(), updatedAt: new Date().toISOString() };
  const path = writeDescriptor(dir, "racing-session", { pid: deadPid() });
  // Simulate the race: the real owner (a different, live pid) refreshes the
  // descriptor on disk AFTER the stale snapshot above was captured, but
  // BEFORE the sweep acts on it.
  writeDescriptor(dir, "racing-session", { pid: process.pid });

  const result = reapIfStillStale(path, deadSnapshot);

  assert.equal(result.removed, false);
  assert.equal(existsSync(path), true);
  assert.equal(result.descriptor.pid, process.pid);
  // The atomic-claim mechanism renames the file away and back -- confirm it
  // actually landed back at the ORIGINAL path (not stuck under a
  // `.reap-claim.` name) and left no claim artifact behind.
  assert.deepEqual(readdirSync(dir), ["racing-session.json"]);
  assert.deepEqual(JSON.parse(readFileSync(path, "utf-8")).pid, process.pid);
});

test("reapIfStillStale DOES delete a file that is still stale on revalidation", () => {
  const dir = tmpDir();
  const pid = deadPid();
  const deadSnapshot = { pid, updatedAt: new Date().toISOString() };
  const path = writeDescriptor(dir, "truly-dead-session", { pid });

  const result = reapIfStillStale(path, deadSnapshot);

  assert.equal(result.removed, true);
  assert.equal(existsSync(path), false);
});

test("reapIfStillStale never revalidates (or touches disk) for an already-live snapshot", () => {
  const dir = tmpDir();
  const liveSnapshot = { pid: process.pid, updatedAt: new Date().toISOString() };
  const path = writeDescriptor(dir, "already-live", { pid: process.pid });

  const result = reapIfStillStale(path, liveSnapshot);

  assert.equal(result.removed, false);
  assert.equal(existsSync(path), true);
  assert.equal(result.descriptor, liveSnapshot); // returned the snapshot as-is, no re-read needed
});

test("reapIfStillStale NEVER exposes a known-stale snapshot as live when the claim itself fails", () => {
  // A permission/other claim failure (anything but ENOENT) must not fall
  // back to trusting the already-stale snapshot -- that would let a failed
  // reap attempt expose a dead descriptor to listLive()/bin/list-sessions.mjs
  // as if it were a genuinely live session.
  const dir = tmpDir();
  const deadSnapshot = { pid: deadPid(), updatedAt: new Date().toISOString() };
  const path = writeDescriptor(dir, "unreapable-session", { pid: deadSnapshot.pid });
  const alwaysFailRename = () => {
    const e = new Error("permission denied");
    e.code = "EACCES";
    throw e;
  };

  const result = reapIfStillStale(path, deadSnapshot, {}, alwaysFailRename);

  assert.equal(result.removed, false);
  assert.equal(result.descriptor, null); // NOT the stale snapshot -- never reported as live
  // The file itself is untouched (the fake rename never actually ran), but
  // that's incidental to this test -- what matters is the RETURN value a
  // caller like sweepStale/listLive sees.
  assert.equal(existsSync(path), true);
});

test("reapIfStillStale NEVER exposes a known-stale descriptor as live when the FINAL delete (not just the claim) fails", () => {
  // The claim-rename can succeed (we genuinely own the entry now) while the
  // subsequent delete still fails for a real reason (permission/I/O). The
  // already-confirmed-stale claimed descriptor must not be reported as live
  // just because the actual unlink didn't work -- same bug class as the
  // claim-failure case above, one step later in the same function.
  const dir = tmpDir();
  const deadSnapshot = { pid: deadPid(), updatedAt: new Date().toISOString() };
  const path = writeDescriptor(dir, "undeletable-session", { pid: deadSnapshot.pid });
  const alwaysFailUnlink = () => {
    const e = new Error("permission denied");
    e.code = "EACCES";
    throw e;
  };

  const result = reapIfStillStale(path, deadSnapshot, {}, renameSync, alwaysFailUnlink);

  assert.equal(result.removed, false);
  assert.equal(result.descriptor, null); // NOT the stale claimed descriptor -- never reported as live
});

test("sweepStale never adds a failed-claim entry to `kept` (would otherwise report it as live)", () => {
  const dir = tmpDir();
  const deadSnapshot = { pid: deadPid(), updatedAt: new Date().toISOString() };
  writeDescriptor(dir, "unreapable-session", { pid: deadSnapshot.pid });
  const alwaysFailRename = () => {
    const e = new Error("permission denied");
    e.code = "EACCES";
    throw e;
  };

  // Mirror sweepStale's own loop with the injected failing rename, since
  // sweepStale itself doesn't take a renameFn parameter (production code
  // always uses the real one) -- this proves reapIfStillStale's contract is
  // actually what sweepStale relies on, without needing to thread a test
  // seam through every layer.
  const entries = listDescriptorFiles(dir);
  const kept = [];
  const removed = [];
  for (const { path, descriptor } of entries) {
    const result = reapIfStillStale(path, descriptor, {}, alwaysFailRename);
    if (result.removed) removed.push(path);
    else if (result.descriptor) kept.push(result.descriptor);
  }

  assert.deepEqual(removed, []);
  assert.deepEqual(kept, []); // NOT [{ pid: deadSnapshot.pid, ... }] -- never silently reported as live
});

test("sweepStale as a whole never deletes a descriptor refreshed mid-sweep", () => {
  const dir = tmpDir();
  // A plain, unambiguously-stale entry alongside the racing one, to confirm
  // the fix doesn't just make sweepStale over-conservative in general.
  const stalePath = writeDescriptor(dir, "plain-dead", { pid: deadPid() });
  const racingPath = writeDescriptor(dir, "racing", { pid: deadPid() });

  // listDescriptorFiles (sweepStale's own first step) takes its snapshot now.
  const snapshot = listDescriptorFiles(dir);
  // The owner of "racing" refreshes between the snapshot and any action on it.
  writeDescriptor(dir, "racing", { pid: process.pid });

  const removed = [];
  const kept = [];
  for (const { path, descriptor } of snapshot) {
    const result = reapIfStillStale(path, descriptor);
    if (result.removed) removed.push(path);
    else kept.push(path);
  }

  assert.deepEqual(removed, [stalePath]);
  assert.deepEqual(kept, [racingPath]);
  assert.equal(existsSync(racingPath), true);
});

// --- sweepOrphanedSidecars (orphaned .tmp / .reap-claim.* sidecar cleanup) ---
//
// Neither sidecar shape is a bare `.json` file, so listDescriptorFiles/
// sweepStale's main loop never sees them on its own -- a crash exactly
// mid-writeDescriptorAtomic (between creating the .tmp and renaming it) or
// mid-reapIfStillStale (between claiming and deleting/restoring) would
// otherwise leave a credential-bearing orphan on disk forever.

function writeSidecar(dir, name, descriptor) {
  const path = join(dir, name);
  writeFileSync(path, JSON.stringify(descriptor));
  return path;
}

test("sweepOrphanedSidecars removes a .tmp sidecar whose owning pid (from the filename) is dead", () => {
  const dir = tmpDir();
  const pid = deadPid();
  const path = writeSidecar(dir, `s1.json.${pid}.1700000000000.tmp`, { pid, sessionId: "s1" });
  const removed = sweepOrphanedSidecars(dir);
  assert.deepEqual(removed, [path]);
  assert.equal(existsSync(path), false);
});

test("sweepOrphanedSidecars removes a .reap-claim.* sidecar whose CLAIMANT pid (from the filename) is dead", () => {
  const dir = tmpDir();
  const claimantPid = deadPid();
  // Content carries the ORIGINAL (already-stale) target session's pid --
  // deliberately a DIFFERENT dead pid than the claimant's, to confirm
  // liveness is decided by the filename, not the content.
  const path = writeSidecar(dir, `s1.json.reap-claim.${claimantPid}.1700000000000`, { pid: deadPid(), sessionId: "s1" });
  const removed = sweepOrphanedSidecars(dir);
  assert.deepEqual(removed, [path]);
  assert.equal(existsSync(path), false);
});

test("sweepOrphanedSidecars leaves a sidecar alone while its OWNER pid (filename) is alive -- may be mid-operation", () => {
  const dir = tmpDir();
  const path = writeSidecar(dir, `s1.json.${process.pid}.1700000000000.tmp`, { pid: process.pid, sessionId: "s1" });
  const removed = sweepOrphanedSidecars(dir);
  assert.deepEqual(removed, []);
  assert.equal(existsSync(path), true);
});

test("sweepOrphanedSidecars keeps a LIVE claimant's .reap-claim.* file even though its CONTENT pid is a dead (already-reaped) target", () => {
  // This is the exact bug this fix closes: a .reap-claim.<claimantPid>.<ts>
  // file's JSON body is the CLAIMED (already stale) target session's own
  // descriptor -- its pid is expected to be dead. Checking that content
  // pid for liveness would make every legitimate, still-in-flight claim
  // look "orphaned" and get deleted out from under a live, currently-
  // reaping claimant.
  const dir = tmpDir();
  const path = writeSidecar(dir, `s1.json.reap-claim.${process.pid}.1700000000000`, {
    pid: deadPid(), // the claimed target's own pid -- expected to be dead
    sessionId: "s1",
  });
  const removed = sweepOrphanedSidecars(dir);
  assert.deepEqual(removed, []);
  assert.equal(existsSync(path), true);
});

test("sweepOrphanedSidecars keeps a mid-write .tmp file (unparseable content) as long as its FILENAME pid is alive", () => {
  const dir = tmpDir();
  const path = join(dir, `s1.json.${process.pid}.1700000000000.tmp`);
  writeFileSync(path, '{"incomplete truncated conte'); // not valid JSON -- genuinely mid-write
  const removed = sweepOrphanedSidecars(dir);
  assert.deepEqual(removed, []);
  assert.equal(existsSync(path), true);
});

test("sweepOrphanedSidecars never touches an ordinary .json descriptor", () => {
  const dir = tmpDir();
  const path = writeDescriptor(dir, "ordinary-session", { pid: deadPid() });
  const removed = sweepOrphanedSidecars(dir);
  assert.deepEqual(removed, []);
  assert.equal(existsSync(path), true);
});

test("sweepStale also reaps orphaned sidecars alongside ordinary stale descriptors", () => {
  const dir = tmpDir();
  const sidecarPath = writeSidecar(dir, `orphan.json.${deadPid()}.1700000000000.tmp`, { pid: deadPid(), sessionId: "orphan" });
  const stalePath = writeDescriptor(dir, "plain-dead", { pid: deadPid() });
  const livePath = writeDescriptor(dir, "still-alive", { pid: process.pid });

  const { removed } = sweepStale(dir);

  assert.ok(removed.includes(sidecarPath));
  assert.ok(removed.includes(stalePath));
  assert.equal(existsSync(sidecarPath), false);
  assert.equal(existsSync(stalePath), false);
  assert.equal(existsSync(livePath), true);
});

