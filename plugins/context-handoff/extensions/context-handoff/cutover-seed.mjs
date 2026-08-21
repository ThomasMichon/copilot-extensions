// cutover-seed.mjs -- pure builders for the live-cutover successor's seed prompt.
//
// Extracted from extension.mjs so the seed SHAPE is independently testable
// (unit test + clean-room import) without loading the whole session extension
// (which pulls in the Copilot SDK). Everything here is a pure string function
// with no side effects, no SDK, and no module state -- import it from anywhere.
//
// The load-bearing invariant these functions encode: for a TASK-backed cutover
// with a known predecessor pane / worktree / session, the successor's FIRST
// action is a plain shell-command chain (a CORE `bash` call), NOT the
// `consume_handoff` extension tool. That is what makes the successor immune to
// the CLI's startup extension-reload race (see GitHub issue #853): an
// extension-provided tool call can be orphaned when the extension generation
// servicing it is torn down mid-launch (no completion, no error -> the call
// hangs indefinitely), whereas a core `bash` call cannot.

// Normalize a handoff title into the seed's leading topic clause. A handoff
// title often already begins with "Continue:" (a successor handing off again),
// which would compound into "Continue: Continue: ..." on each hop -- so only
// prepend when absent. Empty title -> a generic lead.
export function leadFrom(title) {
  const t = (title || "").toString().trim();
  if (!t) return "Continue this session";
  return /^\s*continue\s*:/i.test(t) ? t : `Continue: ${t}`;
}

// Build the single-line ASCII CUTOVER seed (the HANDOFF_SEED) for a live
// cutover successor. Kept as the ONE source of this string so
// save_handoff_prompt and retry_handoff_cutover always spawn an identical
// successor. `kind` is "task" (agent-dispatch task-backed) or "file"
// (worktree-state file-backed); `id` is the task id / file handoff id; `lead`
// comes from `leadFrom(title)`.
//
// For a TASK-backed cutover we prefer a BASH-FIRST seed (see GitHub issue #853):
// the successor's first action is a plain shell-command chain, NOT the
// `consume_handoff` extension tool. The CLI's startup extension-reload race can
// route an external-tool request to an extension generation that is torn down
// mid-launch; the request then never returns -- no completion event, no error --
// and the tool call hangs indefinitely (observed: multi-hour stalls). The `bash`
// tool is a CORE tool (not extension-provided), so it cannot be orphaned that
// way. The retry-on-not-ready clause below was an earlier mitigation, but it
// only helps when the bad call fails FAST (a 400 / tool-not-found the model can
// see and retry) -- it does nothing for the silent-hang case, which is why the
// bash-first path exists. The bash-first seed is used only when the predecessor
// pane / worktree / session id are known (`oldPane`, `worktree`, `sessionId`);
// otherwise, and for file-backed handoffs, we fall back to the tool-based seed +
// retry clause.
//
// `retry` (default true) appends the retry-on-not-ready clause described above.
// Pass `retry: false` for the human-facing paste prompt (resumed in an
// already-loaded session, where there is no such race and brevity matters).
export function buildCutoverSeed(
  kind, id, lead,
  { retry = true, oldPane = null, worktree = null, sessionId = null } = {},
) {
  const retryClause = retry
    ? " If that call fails because the tool is not yet available (e.g. a 400 / " +
      "tool-not-found on this freshly launched session while the context-handoff " +
      "extension is still loading), wait a couple of seconds and retry the SAME " +
      "call, up to 5 attempts, before doing anything else."
    : "";
  if (kind === "task") {
    // Bash-first path: the three verbs are exactly what consume_handoff shells
    // to -- load the brief + take ownership (agent-dispatch consume
    // --defer-complete), conclude the predecessor, then retire + reap its pane --
    // reproduced here as a single shell chain so no extension tool sits in the
    // reload-window critical path.
    if (oldPane && worktree && sessionId) {
      return (
        `${lead}. You are taking over a handoff (agent-dispatch task ${id}) IN ` +
        `PLACE -- do not restart or create a new worktree. As your FIRST action, ` +
        `run this single shell command -- it loads your full brief, then retires ` +
        `the predecessor now that you are alive: agent-dispatch consume ${id} ` +
        `--defer-complete && agent-worktrees conclude-session --worktree ` +
        `${worktree} --session ${sessionId} --state handed-off && agent-worktrees ` +
        `handoff-cutover --retire-pane ${oldPane} --successor-verified ` +
        `--retire-reason handoff-consume --worktree-id ${worktree} --session-id ` +
        `${sessionId} . The first command prints your full brief; the trailing ` +
        `JSON lines are bookkeeping. Then continue the prior session's work, and ` +
        `ONLY when you reach the handoff's goal run: agent-dispatch complete ${id} .`
      );
    }
    return (
      `${lead}. You are taking over a handoff (agent-dispatch task ` +
      `${id}) IN PLACE -- do not restart or create a new worktree. ` +
      `Call the context-handoff consume_handoff tool with arguments ` +
      `{"task_id":"${id}","defer_complete":true}.${retryClause} That consumes ` +
      `the handoff, loads your full brief, and retires the predecessor pane only ` +
      `after you are alive. Do the work, and ONLY when you reach the ` +
      `handoff's goal run: agent-dispatch complete ${id} .`
    );
  }
  return (
    `${lead}. Call the context-handoff consume_handoff tool with ` +
    `arguments {"handoff_id":"${id}"} to load this one-time file-backed ` +
    `handoff and continue in place.${retryClause}`
  );
}
