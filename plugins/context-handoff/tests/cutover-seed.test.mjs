// cutover-seed.test.mjs -- unit tests for the live-cutover seed builders.
//
// Run: node --test  (from plugins/context-handoff/, or point at this file)
//
// These guard the load-bearing invariant behind GitHub issue #853: a
// TASK-backed cutover seed with a known predecessor pane / worktree / session
// must be BASH-FIRST -- the successor's first actionable step is a core `bash`
// command chain, NOT the `consume_handoff` extension tool -- so the successor
// cannot be orphaned by the CLI's startup extension-reload race. The tool-based
// seed is retained only as the fallback (file-backed handoffs, or when the
// pane/worktree/session are unknown).

import { test } from "node:test";
import assert from "node:assert/strict";
import { leadFrom, buildCutoverSeed } from "../extensions/context-handoff/cutover-seed.mjs";

const TASK = "abc123def456";
const WT = "lambda-core-wsl-20260101-000000-0000";
const SID = "11111111-2222-3333-4444-555555555555";
const PANE = "%42";
const known = { oldPane: PANE, worktree: WT, sessionId: SID };

test("leadFrom: empty -> generic lead", () => {
  assert.equal(leadFrom(""), "Continue this session");
  assert.equal(leadFrom(null), "Continue this session");
  assert.equal(leadFrom(undefined), "Continue this session");
});

test("leadFrom: prepends 'Continue:' when absent", () => {
  assert.equal(leadFrom("Fix the widget"), "Continue: Fix the widget");
});

test("leadFrom: does not compound an existing 'Continue:' prefix", () => {
  assert.equal(leadFrom("Continue: Fix the widget"), "Continue: Fix the widget");
  assert.equal(leadFrom("continue: fix"), "continue: fix");
});

test("task + known pane/worktree/session -> BASH-FIRST seed (issue #853)", () => {
  const seed = buildCutoverSeed("task", TASK, leadFrom("Fix the widget"), known);

  // The actionable first step is a shell chain, not the extension tool.
  assert.match(seed, /As your FIRST action, run this single shell command/);
  assert.ok(
    !seed.includes("consume_handoff"),
    "bash-first task seed must NOT reference the consume_handoff extension tool",
  );

  // It carries the exact three verbs consume_handoff shells to, in order.
  const consumeAt = seed.indexOf(`agent-dispatch consume ${TASK} --defer-complete`);
  const concludeAt = seed.indexOf(
    `agent-worktrees conclude-session --worktree ${WT} --session ${SID} --state handed-off`,
  );
  const retireAt = seed.indexOf(
    `agent-worktrees handoff-cutover --retire-pane ${PANE} --successor-verified`,
  );
  assert.ok(consumeAt >= 0, "seed must contain the consume verb");
  assert.ok(concludeAt > consumeAt, "conclude verb must follow consume");
  assert.ok(retireAt > concludeAt, "retire verb must follow conclude");

  // Retire verb passes the explicit worktree/session so it resolves from any cwd.
  assert.match(seed, new RegExp(`--worktree-id ${WT} --session-id ${SID}`));

  // Completion is explicit + deferred (autopilot successor).
  assert.match(seed, new RegExp(`agent-dispatch complete ${TASK}`));

  // Rides `copilot -i`: single line, ASCII only.
  assert.ok(!seed.includes("\n"), "seed must be a single line");
  // eslint-disable-next-line no-control-regex
  assert.ok(!/[^\x00-\x7F]/.test(seed), "seed must be ASCII");
});

test("task + missing pane -> tool-based fallback seed", () => {
  const seed = buildCutoverSeed("task", TASK, leadFrom("x"), {
    worktree: WT,
    sessionId: SID, // no oldPane
  });
  assert.match(seed, /consume_handoff tool/);
  assert.ok(
    !seed.includes("As your FIRST action, run this single shell command"),
    "without a known pane, fall back to the tool-based seed",
  );
  // Fallback still carries the retry-on-not-ready clause by default.
  assert.match(seed, /retry the SAME/);
});

test("task + missing worktree/session -> tool-based fallback seed", () => {
  const seed = buildCutoverSeed("task", TASK, leadFrom("x"), { oldPane: PANE });
  assert.match(seed, /consume_handoff tool/);
  assert.ok(!seed.includes("agent-worktrees handoff-cutover --retire-pane"));
});

test("file-backed handoff -> tool-based seed (never bash-first)", () => {
  const seed = buildCutoverSeed("file", "handoff-xyz", leadFrom("x"), known);
  assert.match(seed, /consume_handoff tool/);
  assert.match(seed, /"handoff_id":"handoff-xyz"/);
  assert.ok(!seed.includes("agent-dispatch consume"));
});

test("retry:false drops the retry-on-not-ready clause (human paste prompt)", () => {
  const seed = buildCutoverSeed("file", "handoff-xyz", leadFrom("x"), {
    ...known,
    retry: false,
  });
  assert.ok(!seed.includes("retry the SAME"));
});
