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

// Normalize a handoff title into the seed's leading topic clause. Copilot's
// inferred session title is biased toward the beginning of the first prompt, so
// lead with the actual task title rather than generic handoff/resume wording.
// Strip prefixes inherited from older handoffs before adding the stable marker.
export function leadFrom(title) {
  const t = (title || "").toString().trim()
    .replace(/^(?:continue|task)\s*:\s*/i, "");
  return `Task: ${t || "Continue the current work"}`;
}

export const CONTINUATION_DIRECTIVE =
  "Treat the handoff as active responsibility within the authority it assigns, " +
  "not as proof that the predecessor's latest phase finished the work. Objective " +
  "owners continue the original objective; bounded delegates continue only their " +
  "inherited scope and return or re-handoff at that boundary. After " +
  "loading the brief, keep driving every actionable next phase the original " +
  "request permits, as far as your context and available work allow, without " +
  "waiting for another user nudge. Consuming the handoff is setup, not " +
  "completion: begin substantive work immediately after pickup. If the " +
  "inherited plan is incomplete, finish the planning needed to act and then " +
  "execute it, subject to any required safety, review, approval, or " +
  "confirmation gate; do not stop at a plan unless the user explicitly " +
  "requested planning only. Stop only when the parent objective's " +
  "completion gate is met, an explicit user scope boundary or required " +
  "confirmation stops you, or a real blocker needs input; if context pressure " +
  "returns first, hand off again with the same parent objective and remaining " +
  "roster. When the handoff cites an active effort, load that effort before " +
  "reconstructing intent from session history. Treat the effort -- not the " +
  "handoff task, latest phase, or pull request -- as the source of truth and " +
  "completion gate. Objective owners focus on driving it to `Done`: select and execute the next " +
  "authorized Plan or Validation Plan item, and do not finalize the worktree " +
  "while any item remains unresolved unless responsibility is explicitly " +
  "transferred to a named tracked objective.";

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
// pane / worktree / cwd / session id are known (`oldPane`, `worktree`,
// `worktreeDir`, `sessionId`); otherwise, and for file-backed handoffs, we fall
// back to the tool-based seed + retry clause.
//
// `retry` (default true) appends the retry-on-not-ready clause described above.
// Pass `retry: false` for the human-facing paste prompt (resumed in an
// already-loaded session, where there is no such race and brevity matters).
export function buildCutoverSeed(
  kind, id, lead,
  {
    retry = true,
    oldPane = null,
    worktree = null,
    worktreeDir = null,
    sessionId = null,
    path = null,
    muxSession = null,
  } = {},
) {
  const retryClause = retry
    ? " If that call fails because the tool is not yet available (e.g. a 400 / " +
      "tool-not-found on this freshly launched session while the context-handoff " +
      "extension is still loading), wait a couple of seconds and retry the SAME " +
      "call, up to 5 attempts, before doing anything else."
    : "";
  const continuationClause =
    "The successful consume result supplies the continuation directive. If " +
    "consumption ultimately fails, report that blocker; a missing brief is not " +
    "completion and is not permission to reconstruct the objective from session history.";
  if (kind === "task") {
    // Bash-first path: load the brief + take ownership, bind the new session to
    // the exact numbered handoff, then retire + reap its pane. Reproducing that as
    // one shell chain keeps extension tools out of the reload-window critical
    // path and makes the successor's intended worktree explicit.
    if (oldPane && worktree && worktreeDir && sessionId) {
      const cwd = `"${String(worktreeDir)
        .replace(/[\r\n]+/g, " ")
        .replace(/"/g, '\\"')}"`;
      return (
        `${lead}. Handoff target: agent-dispatch task ${id}; worktree ID ` +
        `${worktree}; intended cwd ${cwd}. Continue IN PLACE: do not restart, ` +
        `create another worktree, or work from a different cwd. As your FIRST ` +
        `action, run this single shell command from that intended cwd -- it loads ` +
        `your full brief, durably binds this new session to the worktree, then ` +
        `retires the predecessor now that you are alive: agent-dispatch consume ` +
        `${id} --defer-complete && agent-worktrees bind-session --worktree-id ` +
        `${worktree} --handoff-token ${id} && agent-worktrees ` +
        `handoff-cutover --retire-pane ${oldPane} --successor-verified ` +
        `--retire-reason handoff-consume --require-mux-identity ` +
        `--worktree-id ${worktree} --session-id ` +
        `${sessionId}` +
        (muxSession ? ` --mux-session ${muxSession}` : "") +
        ` . The first command prints your full brief; the trailing ` +
        `JSON lines are bookkeeping. ${CONTINUATION_DIRECTIVE} For this deferred ` +
        `handoff task, completion of the predecessor's latest phase is not enough; ` +
        `ONLY when you reach the handoff's completion gate run: agent-dispatch ` +
        `complete ${id} .`
      );
    }
    return (
      `${lead}. You are taking over a handoff (agent-dispatch task ${id}) ` +
      `IN PLACE -- do not restart or create a new worktree. ` +
      `Call the context-handoff consume_handoff tool with arguments ` +
      `{"task_id":"${id}","defer_complete":true}.${retryClause} That consumes ` +
      `the handoff, loads your full brief, and retires the predecessor pane only ` +
      `after you are alive. ${continuationClause} For this deferred handoff ` +
      `task, completion of the predecessor's latest phase is not enough; ONLY ` +
      `when you reach the handoff's completion gate run: agent-dispatch complete ` +
      `${id} .`
    );
  }
  const consumeArgs = path
    ? JSON.stringify({ path })
    : JSON.stringify({ handoff_id: id });
  return (
    `${lead}. Call the context-handoff consume_handoff tool with ` +
    `arguments ${consumeArgs} to load this one-time file-backed ` +
    `handoff and continue in place.${retryClause} ${continuationClause}`
  );
}
