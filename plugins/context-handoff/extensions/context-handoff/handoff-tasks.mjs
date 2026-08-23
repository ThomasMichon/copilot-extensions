// handoff-tasks.mjs -- pure helpers for reasoning about handoff dispatch tasks.
//
// Kept side-effect-free (no I/O, no SDK) so the supersede decision is
// unit-testable in isolation (mirrors cutover-seed.mjs). The extension does the
// list + abandon I/O around these.

/**
 * The ids of the pending handoff tasks that a newly-stored handoff SUPERSEDES.
 *
 * A handoff means "resume THIS worktree's work", so the newest handoff for a
 * worktree makes any older pending handoff for the same worktree moot. Because
 * the store dedups per-session (`handoff-<sid>`), different sessions on one
 * worktree each file their own task; without superseding, a re-handoffed
 * worktree accumulates one stale task per session.
 *
 * Returns the ids to abandon: every task pinned to `worktree` that is NOT the
 * just-stored `keepId`. A task with an explicit non-`context-handoff` source is
 * left alone (we only supersede our own handoffs); a task with no source is
 * treated as ours (older handoffs predate the source stamp).
 *
 * @param {Array<object>|null} tasks - pending handoff tasks (from `list`).
 * @param {string} worktree - the target worktree the new handoff is pinned to.
 * @param {string} keepId - the just-stored handoff task id to preserve.
 * @returns {string[]} task ids to abandon (empty when input is unusable).
 */
export function supersededHandoffIds(tasks, worktree, keepId) {
  if (!Array.isArray(tasks) || !worktree) return [];
  const ids = [];
  for (const t of tasks) {
    if (!t || typeof t.id !== "string") continue;
    if (t.id === keepId) continue;
    if (t.target_worktree !== worktree) continue;
    if (t.source && t.source !== "context-handoff") continue;
    ids.push(t.id);
  }
  return ids;
}
