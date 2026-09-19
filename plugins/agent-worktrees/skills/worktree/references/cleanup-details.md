# Worktree — Cleanup Details

Full mechanics behind the entrypoint's cleanup rules: fresh safety
revalidation, and the per-worktree dirty-resolution procedure.

## Fresh safety revalidation before removal

The three **non-forced** cleanup reapers all re-check safety from fresh state
at the action moment, not just at scan time: batch
`cleanup --clean`, single-item `cleanup --worktree-id <id>`, and the automatic
finished-session sweep each reacquire `FinalizeLock`, reload the tracking
record, rebuild worktree-local liveness, re-classify git state, re-derive
conversation turns, and re-run the complete cleanup disposition immediately
before the reap. For a missing worktree directory, the branch-merged proof is
also re-run under that same lock. Non-forced cleanup then keeps the
per-record `_RecordLock(require_sidecar=True)` held through `_reap_worktree`,
so the tool's own record writers (claim/follow-up/session-registration
mutations) cannot slip a conflicting change into the final read→delete window.

This is a **fail-closed, in-tool writer fence**, not a universal filesystem
lock: arbitrary external edits or raw git commands outside agent-worktrees can
still race the local delete. `cleanup --worktree-id <id> --force` remains the
documented, narrower exception: it still refreshes liveness under `FinalizeLock`
and refuses an active/hosted session, but it deliberately bypasses the full
dirty/WIP/claim/follow-up/branch-merge disposition.

**Known limitation:** a record whose tracking `status` is already
`finalized` can still mask genuinely new WIP or conversation-only content added
*after* finalize, because `_apply_tracking_override` currently collapses that
fresh state to `COMPLETED` before `cleanup_disposition` sees it. The tests pin
that behavior so it cannot silently drift, but the limitation itself remains
tracked and unfixed here.

## Dirty worktrees: resolve per-worktree, never blanket `--force`

`cleanup` (and `remove-system`) refuse a worktree with uncommitted changes
**on purpose** — that refusal is the tool protecting real, unlanded work.
`cleanup --worktree-id <id> --force` (and `remove-system ... --force`) exist
for a genuine, individually-verified exception, not as a bulk shortcut for a
backlog of `dirty` worktrees. Treating "dirty" as a synonym for "safe to
force" throws away the one signal that distinguishes real work from noise —
and a mode-only or superseded diff in worktree #1 doesn't guarantee the
same is true of worktree #2 through #23.

For **every** worktree `cleanup` reports as `dirty`, go in individually:

1. **Read the actual diff, including staged and untracked paths.** `DIRTY`
   covers modified, staged, *and* untracked content — `git diff` alone only
   shows unstaged tracked changes. Run `git status --porcelain` first to see
   every path, then `git diff HEAD` for the full tracked diff (staged +
   unstaged), and open each untracked file directly. Distinguish a real
   change (content not already on the default branch, an unlanded fix) from
   noise (a stray mode-only flip, a scratch file, a local edit later
   superseded upstream) — don't rely on `--stat`, which renders a mode-only/
   rename-only change as `0 insertions/deletions`, indistinguishable at a
   glance from real content.
2. **Land real work through the repo's own contribution flow — branch on
   `pr-profile`.** Check `<agent-worktrees catalog argv[0]> get pr-profile`
   first: a `pr-*` profile means commit, open its PR, get it
   reviewed/merged (see § PR Workflow in SKILL.md); a **`direct`** profile has no
   PR flow at all — commit and let `finalize` land the work directly (see
   § Two-Phase Sign-Off in SKILL.md). Sending a direct-profile repo through PR
   steps just produces inapplicable instructions. If the diff looks like it
   duplicates something already on the default branch, confirm with
   `git diff <default-branch> -- <path>` before writing it off as noise —
   don't assume.
3. **Discard confirmed noise explicitly, file by file, matching how it's
   dirty.** `git checkout -- <path>` only restores the working tree from
   the index — a **staged** noise change stays staged and the path stays
   dirty. For a tracked path, discard both index and working tree with
   `git restore --staged --worktree -- <path>` (or `git checkout HEAD --
   <path>`, equivalent for this purpose). For **untracked** noise, remove
   only the reviewed path(s) — `git clean -fd -- <path>` (or plain `rm`) —
   never a bare `git clean -fd`, which deletes every untracked file and
   directory in the worktree, including unrelated unreviewed work.
4. **Re-check state before assuming plain `cleanup --clean` will prune it.**
   A clean tree does not by itself make a worktree `completed` — it
   reclassifies to whatever the underlying content actually is: `wip` if it
   still carries unmerged commits ahead of the default branch (not pruned
   by plain `cleanup --clean`; that content needs to land first, per step
   2); `unused` if it now has no commits *and* the session held no
   conversation turns (needs `--include-unused`); or `conversation-only` if
   it has no commits but the session *did* hold turns (needs the separate
   `--include-conversations` flag — `--include-unused` alone does **not**
   cover this bucket). Confirm with the user per the existing "ask before
   purging unused" rule in either case — clearing noise doesn't
   retroactively make a worktree's conversation/planning history
   disposable. Only a worktree that reclassifies to `completed`/`gone` is
   pruned by plain `cleanup --clean` with no extra flag. If `cleanup` still
   skips a worktree you expected to be clear, that reclassification — not
   `--force` — is the next thing to check.

If a whole batch of worktrees turns out `dirty` for the **same root
cause** (e.g. a shared tool stamping every worktree with an identical
mode-only bit or a stale generated file), that is itself a defect worth
tracking or fixing at the source — reaping the symptom once, by hand, after
verifying it's genuinely inert, is reasonable; silently normalizing
`--force` as the standing remedy for that pattern is not.
