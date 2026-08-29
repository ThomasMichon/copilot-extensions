// seed-probe.mjs -- clean-room probe for the context-handoff cutover seed invariant.
//
// Usage: node seed-probe.mjs <path-to-installed-cutover-seed.mjs>
//
// Imports the ACTUALLY-INSTALLED `cutover-seed.mjs` from the plugin payload on
// the fresh box and asserts the load-bearing bash-first invariant (GitHub issue
// #853). Prints a human-readable report and exits 0 only if every check holds;
// exits non-zero (with FAIL: lines) otherwise, so the scenario can gate on it.

const modPath = process.argv[2];
if (!modPath) {
  console.error("FAIL: no module path given");
  process.exit(2);
}

let mod;
try {
  mod = await import(modPath);
} catch (e) {
  console.error(`FAIL: could not import installed cutover-seed.mjs: ${e?.message || e}`);
  process.exit(2);
}

const { leadFrom, buildCutoverSeed } = mod;
if (typeof leadFrom !== "function" || typeof buildCutoverSeed !== "function") {
  console.error("FAIL: cutover-seed.mjs does not export leadFrom + buildCutoverSeed");
  process.exit(2);
}

const TASK = "T1abc";
const WT = "clean-room-0000";
const SID = "sid-0000-1111";
const PANE = "%7";
const WORKTREE_DIR = "/home/operator/wt-repo.worktrees/clean-room-0000";
const known = {
  oldPane: PANE,
  worktree: WT,
  worktreeDir: WORKTREE_DIR,
  sessionId: SID,
  muxSession: `wt-${WT}`,
};

const taskSeed = buildCutoverSeed("task", TASK, leadFrom("Fix the widget"), known);
const taskNoPane = buildCutoverSeed("task", TASK, leadFrom("x"), { worktree: WT, sessionId: SID });
const fileSeed = buildCutoverSeed("file", "handoff-xyz", leadFrom("x"), known);

let failed = 0;
const check = (ok, label) => {
  console.log(`${ok ? "ok  " : "FAIL"}: ${label}`);
  if (!ok) failed++;
};

// --- The bash-first invariant (the #853 fix) ---
check(
  taskSeed.includes("As your FIRST action, run this single shell command"),
  "task+known: seed is bash-first (first action is a shell command)",
);
check(
  !taskSeed.includes("consume_handoff"),
  "task+known: seed does NOT invoke the consume_handoff extension tool",
);
const cAt = taskSeed.indexOf(`agent-dispatch consume ${TASK} --defer-complete`);
const kAt = taskSeed.indexOf(`agent-worktrees bind-session --worktree-id ${WT}`);
const rAt = taskSeed.indexOf(`agent-worktrees handoff-cutover --retire-pane ${PANE} --successor-verified`);
check(cAt >= 0, "task+known: carries `agent-dispatch consume --defer-complete`");
check(kAt > cAt, "task+known: carries `bind-session` after consume");
check(rAt > kAt, "task+known: carries `handoff-cutover --retire-pane` after bind");
check(
  taskSeed.includes(`--worktree-id ${WT} --session-id ${SID}`),
  "task+known: retire verb passes explicit --worktree-id/--session-id (cwd-independent)",
);
check(
  taskSeed.includes(`--mux-session wt-${WT}`),
  "task+known: retire verb validates the original mux identity",
);
check(!taskSeed.includes("\n"), "task+known: seed is a single line (rides copilot -i)");
// eslint-disable-next-line no-control-regex
check(!/[^\x00-\x7F]/.test(taskSeed), "task+known: seed is ASCII");

// --- The fallbacks stay tool-based (unchanged behavior) ---
check(
  taskNoPane.includes("consume_handoff tool") &&
    !taskNoPane.includes("As your FIRST action, run this single shell command"),
  "task+unknown-pane: falls back to the tool-based seed",
);
check(
  fileSeed.includes("consume_handoff tool") && !fileSeed.includes("agent-dispatch consume"),
  "file-backed: uses the tool-based seed (never bash-first)",
);

console.log("");
console.log("--- task+known seed (the bash-first cutover seed) ---");
console.log(taskSeed);

process.exit(failed === 0 ? 0 : 1);
