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
import {
  encodeHandoffPayload, decodeHandoffPayload, buildSeedForStored,
  safePathSegment, quoteWinArg, HANDOFF_META_PREFIX,
} from "../extensions/context-handoff/handoff-core.mjs";

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
    metadata: { title: "Fix the parser", oldPane: null, worktree: null, sessionId: "sid1" },
  };
  const seed = buildSeedForStored(stored, { retry: true });
  assert.match(seed, /consume_handoff tool/);
  assert.match(seed, /"handoff_id":"handoff-sid1"/);
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
