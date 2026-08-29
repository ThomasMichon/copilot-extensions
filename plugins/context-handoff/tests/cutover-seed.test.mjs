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
import {
  CONTINUATION_DIRECTIVE,
  leadFrom,
  buildCutoverSeed,
} from "../extensions/context-handoff/cutover-seed.mjs";

const TASK = "abc123def456";
const WT = "lambda-core-wsl-20260101-000000-0000";
const SID = "11111111-2222-3333-4444-555555555555";
const PANE = "%42";
const WTDIR = "/tmp/src/lambda-core";
const known = {
  oldPane: PANE,
  worktree: WT,
  worktreeDir: WTDIR,
  sessionId: SID,
  muxSession: `wt-${WT}`,
};

test("leadFrom: empty -> generic lead", () => {
  assert.equal(leadFrom(""), "Task: Continue the current work");
  assert.equal(leadFrom(null), "Task: Continue the current work");
  assert.equal(leadFrom(undefined), "Task: Continue the current work");
});

test("leadFrom: leads with the actual task title", () => {
  assert.equal(leadFrom("Fix the widget"), "Task: Fix the widget");
});

test("leadFrom: replaces inherited handoff prefixes", () => {
  assert.equal(leadFrom("Continue: Fix the widget"), "Task: Fix the widget");
  assert.equal(leadFrom("task: fix"), "Task: fix");
});

test("continuation directive makes an active effort the completion gate", () => {
  assert.match(
    CONTINUATION_DIRECTIVE,
    /effort -- not the handoff task, latest phase, or pull request -- as the source of truth and completion gate/,
  );
  assert.match(CONTINUATION_DIRECTIVE, /Focus on driving it to `Done`/);
  assert.match(
    CONTINUATION_DIRECTIVE,
    /select and execute the next authorized Plan or Validation Plan item/,
  );
  assert.match(
    CONTINUATION_DIRECTIVE,
    /do not finalize the worktree while any item remains unresolved/,
  );
  assert.match(
    CONTINUATION_DIRECTIVE,
    /explicitly transferred to a named tracked objective/,
  );
});

test("task + known pane/worktree/session -> BASH-FIRST seed (issue #853)", () => {
  const seed = buildCutoverSeed("task", TASK, leadFrom("Fix the widget"), known);

  // The actionable first step is a shell chain, not the extension tool.
  assert.match(seed, /As your FIRST action, run this single shell command/);
  assert.ok(
    !seed.includes("consume_handoff"),
    "bash-first task seed must NOT reference the consume_handoff extension tool",
  );

  // It consumes, claims the exact numbered handoff while binding, then retires.
  const consumeAt = seed.indexOf(`agent-dispatch consume ${TASK} --defer-complete`);
  const retireAt = seed.indexOf(
    `agent-worktrees handoff-cutover --retire-pane ${PANE} --successor-verified`,
  );
  assert.ok(consumeAt >= 0, "seed must contain the consume verb");
  const bindAt = seed.indexOf(
    `agent-worktrees bind-session --worktree-id ${WT} --handoff-token ${TASK}`,
  );
  assert.ok(bindAt > consumeAt, "successor binding must follow consume");
  assert.ok(retireAt > bindAt, "retire verb must follow successor binding");
  assert.ok(
    !seed.includes("agent-worktrees conclude-session"),
    "exact handoff binding atomically concludes the predecessor",
  );

  assert.match(seed, new RegExp(`worktree ID ${WT}`));
  assert.ok(seed.includes(`intended cwd "${WTDIR}"`));
  assert.ok(seed.includes(`--mux-session wt-${WT}`));

  // Retire verb passes the explicit worktree/session so it resolves from any cwd.
  assert.match(seed, new RegExp(`--worktree-id ${WT} --session-id ${SID}`));

  // Completion is explicit + deferred (autopilot successor).
  assert.match(seed, new RegExp(`agent-dispatch complete ${TASK}`));
  assert.ok(seed.includes(CONTINUATION_DIRECTIVE));
  assert.match(seed, /completion of the predecessor's latest phase is not enough/);
  assert.match(seed, /Focus on driving it to `Done`/);

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
  const seed = buildCutoverSeed("task", TASK, leadFrom("x"), {
    oldPane: PANE,
    worktreeDir: WTDIR,
  });
  assert.match(seed, /consume_handoff tool/);
  assert.ok(!seed.includes("agent-worktrees handoff-cutover --retire-pane"));
});

test("task + missing worktree cwd -> tool-based fallback seed", () => {
  const seed = buildCutoverSeed("task", TASK, leadFrom("x"), {
    oldPane: PANE,
    worktree: WT,
    sessionId: SID,
  });
  assert.match(seed, /consume_handoff tool/);
  assert.ok(!seed.includes("agent-worktrees bind-session"));
});

test("file-backed handoff -> tool-based seed (never bash-first)", () => {
  const seed = buildCutoverSeed("file", "handoff-xyz", leadFrom("x"), known);
  assert.match(seed, /consume_handoff tool/);
  assert.match(seed, /"handoff_id":"handoff-xyz"/);
  assert.ok(!seed.includes("agent-dispatch consume"));
  assert.ok(seed.includes(CONTINUATION_DIRECTIVE));
});

test("retry:false drops the retry-on-not-ready clause (human paste prompt)", () => {
  const seed = buildCutoverSeed("file", "handoff-xyz", leadFrom("x"), {
    ...known,
    retry: false,
  });
  assert.ok(!seed.includes("retry the SAME"));
});
