// handoff-core.test.mjs -- unit tests for the SDK-free handoff store/seed core.
//
// Run: node --test  (from plugins/context-handoff/, or point at this file)
//
// Covers the pure, I/O-free surface of handoff-core.mjs -- the payload
// encode/decode round-trip (the on-disk + task-payload format the extension's
// consume path reads), the seed composition for a stored handoff (delegating to
// the issue-#853 bash-first invariant in cutover-seed.mjs), and the path/arg
// sanitizers. The store/trigger functions that shell out to agent-worktrees /
// agent-dispatch are exercised by the clean-room context-handoff-cutover
// scenario, not here.

import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import {
  encodeHandoffPayload, decodeHandoffPayload, buildSeedForStored,
  safePathSegment, quoteWinArg, agentWorktreesGet, handoffDirFor,
  agentWorktreesGetResult, consumeFileHandoffOnce, writeJsonAtomic,
  currentMuxSession,
  HANDOFF_META_PREFIX,
} from "../extensions/context-handoff/handoff-core.mjs";
import {
  mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync, utimesSync,
} from "node:fs";
import {
  AGENT_WORKTREES_QUERY_TIMEOUT_MS,
} from "../extensions/context-handoff/cli-timeouts.mjs";

test("encode/decode round-trips metadata + text", () => {
  const meta = { kind: "context-handoff", id: "handoff-sid1", title: "Fix X", oldPane: "%3" };
  const body = "## Session Continuation\nline two\nline three";
  const encoded = encodeHandoffPayload(body, meta);
  assert.ok(encoded.startsWith(HANDOFF_META_PREFIX));
  const { metadata, text } = decodeHandoffPayload(encoded);
  assert.deepEqual(metadata, meta);
  assert.equal(text, body);
});

test("decode of a plain (metadata-less) string returns null metadata + full text", () => {
  const raw = "no metadata here\njust text";
  const { metadata, text } = decodeHandoffPayload(raw);
  assert.equal(metadata, null);
  assert.equal(text, raw);
});

test("decode tolerates malformed metadata JSON (falls back to raw)", () => {
  const raw = `${HANDOFF_META_PREFIX} {not valid json} -->\nbody`;
  const { metadata, text } = decodeHandoffPayload(raw);
  assert.equal(metadata, null);
  assert.equal(text, raw);
});

test("buildSeedForStored: task-backed + known pane/worktree/session -> bash-first seed", () => {
  const stored = {
    storage: "agent-dispatch",
    id: "task-42",
    metadata: {
      title: "Ship the thing",
      oldPane: "%7",
      worktree: "wt-abc",
      worktreeDir: "/tmp/src/wt-abc",
      sessionId: "sid-9",
    },
  };
  const seed = buildSeedForStored(stored, { retry: true });
  // Bash-first invariant (issue #853): the successor's first action is a core
  // shell chain, not the consume_handoff tool.
  assert.match(seed, /agent-dispatch consume task-42 --defer-complete/);
  assert.match(seed, /agent-worktrees handoff-cutover --retire-pane %7/);
  assert.match(seed, /^Task: Ship the thing/);
  assert.match(seed, /intended cwd "\/tmp\/src\/wt-abc"/);
  assert.match(seed, /agent-worktrees bind-session --worktree-id wt-abc/);
  assert.doesNotMatch(seed, /consume_handoff tool/);
});

test("buildSeedForStored: file-backed -> tool-based consume_handoff seed", () => {
  const stored = {
    storage: "file",
    id: "handoff-sid1",
    path: "C:\\state\\handoff-sid1.json",
    metadata: { title: "Fix the parser", oldPane: null, worktree: null, sessionId: "sid1" },
  };
  const seed = buildSeedForStored(stored, { retry: true });
  assert.match(seed, /consume_handoff tool/);
  assert.match(seed, /"path":"C:\\\\state\\\\handoff-sid1.json"/);
});

test("buildSeedForStored: paste prompt (retry:false) omits the loading-race retry clause", () => {
  const stored = {
    storage: "file", id: "h1",
    metadata: { title: "x", oldPane: null, worktree: null, sessionId: "s" },
  };
  const paste = buildSeedForStored(stored, { retry: false });
  assert.doesNotMatch(paste, /tool-not-found/);
});

test("safePathSegment sanitizes and caps", () => {
  assert.equal(safePathSegment("a/b c:d"), "a_b_c_d");
  assert.equal(safePathSegment(""), "unknown");
  assert.equal(safePathSegment(null), "unknown");
  assert.equal(safePathSegment("x".repeat(300)).length, 160);
});

test("quoteWinArg quotes only when needed", () => {
  assert.equal(quoteWinArg("plain"), "plain");
  assert.equal(quoteWinArg("has space"), '"has space"');
  assert.equal(quoteWinArg('a"b'), '"a""b"');
});

test("currentMuxSession resolves the predecessor pane identity", () => {
  let invocation = null;
  const result = currentMuxSession("%7", (bin, args, options) => {
    invocation = { bin, args, options };
    return "caller-session\n";
  });
  assert.equal(result, "caller-session");
  assert.deepEqual(invocation.args, [
    "display-message", "-p", "-t", "%7", "#{session_name}",
  ]);
});

test("agentWorktreesGet allows slow startup-time identity queries", () => {
  let invocation = null;
  const value = agentWorktreesGet(
    "worktree-state-dir",
    "/repo",
    "session-1",
    (bin, args, options) => {
      invocation = { bin, args, options };
      return "/state/worktree\n";
    },
  );

  assert.equal(value, "/state/worktree");
  assert.deepEqual(invocation, {
    bin: "agent-worktrees",
    args: ["get", "worktree-state-dir", "--session-id", "session-1"],
    options: {
      cwd: "/repo",
      timeout: AGENT_WORKTREES_QUERY_TIMEOUT_MS,
    },
  });
  assert.equal(AGENT_WORKTREES_QUERY_TIMEOUT_MS, 15_000);
});

test("agentWorktreesGetResult explains an empty resolver result", () => {
  const result = agentWorktreesGetResult(
    "worktree-state-dir",
    "/repo",
    "session-1",
    () => "\n",
  );
  assert.equal(result.value, null);
  assert.match(result.error, /returned an empty result/);
  assert.match(result.error, /session-1/);
});

test("atomic write cleans its temporary file", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-atomic-"));
  try {
    const path = join(dir, "handoff.json");
    writeJsonAtomic(path, { ok: true });
    assert.deepEqual(JSON.parse(readFileSync(path, "utf-8")), { ok: true });
    assert.deepEqual(readdirSync(dir), ["handoff.json"]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("file handoff consumption is exactly once", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-consume-"));
  try {
    const path = join(dir, "handoff.json");
    writeJsonAtomic(path, {
      kind: "context-handoff",
      id: "handoff-once",
      consumed: false,
      consumedAt: null,
      promptText: "continue",
    });
    const first = consumeFileHandoffOnce("/repo", "successor", null, path);
    const retry = consumeFileHandoffOnce("/repo", "successor", null, path);
    const second = consumeFileHandoffOnce("/repo", "other", null, path);
    assert.equal(first.ok, true);
    assert.equal(first.record.consumedBySession, "successor");
    assert.equal(retry.ok, true);
    assert.equal(retry.resumedDelivery, true);
    assert.equal(second.ok, false);
    assert.equal(second.alreadyConsumed, true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("file handoff consumption recovers a dead-owner lock", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-lock-"));
  try {
    const path = join(dir, "handoff.json");
    writeJsonAtomic(path, {
      kind: "context-handoff",
      id: "handoff-lock",
      consumed: false,
      consumedAt: null,
      promptText: "continue",
    });
    writeFileSync(`${path}.consume.lock`, JSON.stringify({ pid: 2147483647 }));
    const consumed = consumeFileHandoffOnce("/repo", "successor", null, path);
    assert.equal(consumed.ok, true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("file handoff consumption does not steal a fresh incomplete lock", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-fresh-lock-"));
  try {
    const path = join(dir, "handoff.json");
    writeJsonAtomic(path, {
      kind: "context-handoff",
      id: "handoff-fresh-lock",
      consumed: false,
      promptText: "continue",
    });
    writeFileSync(`${path}.consume.lock`, "");
    const consumed = consumeFileHandoffOnce("/repo", "successor", null, path);
    assert.equal(consumed.ok, false);
    assert.equal(consumed.busy, true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("file handoff consumption treats EPERM lock owners as alive", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-eperm-lock-"));
  const originalKill = process.kill;
  try {
    const path = join(dir, "handoff.json");
    writeJsonAtomic(path, {
      kind: "context-handoff",
      id: "handoff-eperm-lock",
      consumed: false,
      promptText: "continue",
    });
    writeFileSync(`${path}.consume.lock`, JSON.stringify({ pid: 12345 }));
    process.kill = () => {
      const error = new Error("not permitted");
      error.code = "EPERM";
      throw error;
    };
    const consumed = consumeFileHandoffOnce("/repo", "successor", null, path);
    assert.equal(consumed.ok, false);
    assert.equal(consumed.busy, true);
  } finally {
    process.kill = originalKill;
    rmSync(dir, { recursive: true, force: true });
  }
});

test("file handoff consumption recovers a stale incomplete lock", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-stale-lock-"));
  try {
    const path = join(dir, "handoff.json");
    const lock = `${path}.consume.lock`;
    writeJsonAtomic(path, {
      kind: "context-handoff",
      id: "handoff-stale-lock",
      consumed: false,
      promptText: "continue",
    });
    writeFileSync(lock, "");
    const stale = new Date(Date.now() - 60_000);
    utimesSync(lock, stale, stale);
    const consumed = consumeFileHandoffOnce("/repo", "successor", null, path);
    assert.equal(consumed.ok, true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("file handoff consumption recovers a dead recovery lock", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-recovery-lock-"));
  try {
    const path = join(dir, "handoff.json");
    const lock = `${path}.consume.lock`;
    writeJsonAtomic(path, {
      kind: "context-handoff",
      id: "handoff-recovery-lock",
      consumed: false,
      promptText: "continue",
    });
    writeFileSync(lock, JSON.stringify({ pid: 2147483647 }));
    writeFileSync(`${lock}.recover`, JSON.stringify({ pid: 2147483647 }));
    const consumed = consumeFileHandoffOnce("/repo", "successor", null, path);
    assert.equal(consumed.ok, true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("handoffDirFor resolves only the state directory", () => {
  const keys = [];
  const dir = handoffDirFor("/repo", "session-1", (key, cwd, sid) => {
    keys.push({ key, cwd, sid });
    return "/state/worktree";
  });

  assert.equal(dir, join("/state/worktree", "handoff"));
  assert.deepEqual(keys, [{
    key: "worktree-state-dir",
    cwd: "/repo",
    sid: "session-1",
  }]);
});
