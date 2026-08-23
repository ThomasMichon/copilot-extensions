// handoff-supersede.test.mjs -- unit tests for supersededHandoffIds.
//
// Run: node --test  (from plugins/context-handoff/, or point at this file)
//
// Guards the context-handoff leak fix (dotfiles #1743): a newer handoff for a
// worktree must supersede older pending handoffs for that same worktree, so a
// re-handoffed worktree doesn't pile up one stale task per session.

import { test } from "node:test";
import assert from "node:assert/strict";

import { supersededHandoffIds } from "../extensions/context-handoff/handoff-tasks.mjs";

const T = (id, worktree, source = "context-handoff") => ({
  id,
  target_worktree: worktree,
  source,
});

test("supersedes older handoffs for the same worktree, keeping the new one", () => {
  const tasks = [T("old1", "wt-a"), T("new", "wt-a"), T("other", "wt-b")];
  assert.deepEqual(supersededHandoffIds(tasks, "wt-a", "new"), ["old1"]);
});

test("never supersedes the just-stored handoff (keepId) itself", () => {
  assert.deepEqual(supersededHandoffIds([T("new", "wt-a")], "wt-a", "new"), []);
});

test("ignores tasks pinned to a different worktree", () => {
  const tasks = [T("b1", "wt-b"), T("b2", "wt-b")];
  assert.deepEqual(supersededHandoffIds(tasks, "wt-a", "new"), []);
});

test("leaves tasks with a different explicit source alone", () => {
  const tasks = [T("mine", "wt-a"), T("theirs", "wt-a", "some-other-source")];
  assert.deepEqual(supersededHandoffIds(tasks, "wt-a", "new"), ["mine"]);
});

test("treats a source-less task as ours (predates the source stamp)", () => {
  const tasks = [{ id: "legacy", target_worktree: "wt-a" }];
  assert.deepEqual(supersededHandoffIds(tasks, "wt-a", "new"), ["legacy"]);
});

test("supersedes every older session's handoff on one worktree", () => {
  const tasks = [
    T("s1", "wt-a"), T("s2", "wt-a"), T("s3", "wt-a"), T("new", "wt-a"),
  ];
  assert.deepEqual(
    supersededHandoffIds(tasks, "wt-a", "new").sort(),
    ["s1", "s2", "s3"],
  );
});

test("degrades safe on unusable input", () => {
  assert.deepEqual(supersededHandoffIds(null, "wt-a", "new"), []);
  assert.deepEqual(supersededHandoffIds([], "wt-a", "new"), []);
  assert.deepEqual(supersededHandoffIds([T("x", "wt-a")], "", "new"), []);
});

test("skips malformed task entries", () => {
  const tasks = [null, { target_worktree: "wt-a" }, T("ok", "wt-a"), 42];
  assert.deepEqual(supersededHandoffIds(tasks, "wt-a", "new"), ["ok"]);
});
