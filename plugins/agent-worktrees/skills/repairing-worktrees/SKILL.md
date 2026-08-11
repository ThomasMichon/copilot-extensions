---
name: repairing-worktrees
description: >
  Diagnose and repair worktree/session health for a project via the
  agent-worktrees `doctor` command — corrupt tracking records, empty session
  registries, stale status, orphaned empty session shells, and cwd/path
  misalignment — and safely clean up / reap worktrees without reaping a live
  session. Use when asked to:
  - 'repair worktrees'
  - 'repair sessions'
  - 'fix corrupt tracking records'
  - 'worktree doctor'
  - 'session doctor'
  - 'clean up empty sessions'
  - 'clean up worktrees'
  - 'organize / backfill worktrees'
  - 'backfill worktree status'
  - 'backfill sessions'
  - 'worktree health check'
  - 'why is a worktree showing 0 turns'
  - 'picker shows wrong session'
  - 'worktree shows active but only offers Resume'
  - 'reclaim an orphaned Copilot'
  - "can't resume — active process lock"
  For pruning finished worktree *directories* use `gc`/`cleanup`; for orphaned
  mux tabs use `reap-sessions`. This skill covers record + session-state health,
  and the liveness rules that keep cleanup from reaping a live session.
---

# Repairing worktrees & sessions

`agent-worktrees doctor` is the single repeatable primitive for worktree/session
health. It is **per-project** (run it through each project's binstub, e.g.
`dotfiles doctor`, `aperture-labs doctor`) and **read-only by default**.

## What it checks/repairs

1. **Tracking-record integrity** — records that fail to parse (e.g. an
   unquoted `title:` with a `:` from before the serializer quoted titles) are
   silently skipped by the picker; `--fix` re-quotes them so they load again.
2. **Registry + title backfill** — empty `sessions:` registries and missing
   titles are filled from cwd-matched session-state (wraps `backfill-sessions`).
3. **Stale status** — `status: active` with a `completed_at` set → `complete`.
4. **Empty session-state GC** — 0-user-message session shells (aborted starts /
   pre-fix cross-cwd resumes) are removed with their orphaned `session-store.db`
   rows. **Destructive**, so gated behind `--gc-sessions` and guarded by
   age / lock / current-session / registered-session.
5. **Alignment audit** (report-only) — session-less worktrees whose
   `parent_session` cwd differs from their own path.

## Procedure

1. **Report first** (safe, no writes), for each project you manage:
   ```
   <project> doctor            # e.g. dotfiles doctor
   <project> doctor --json     # machine-readable
   ```
2. **Apply non-destructive repairs** (integrity, backfill, stale status):
   ```
   <project> doctor --fix
   ```
3. **Also GC empty session shells** (destructive; only after reviewing the
   report count):
   ```
   <project> doctor --fix --gc-sessions
   ```

Run it per project (`dotfiles`, `aperture-labs`, …) — the command scopes to the
current project's tracking store; the Copilot session-state/store it cleans is
shared across projects, so the guards (current + registered session ids) protect
live work regardless of which project you invoke it from.

## Liveness trumps git state — never reap a live worktree

A worktree's **liveness** — a live `wt-<id>` mux session **or** a live Copilot
session lock (`inuse.<pid>.lock`) — is authoritative and **trumps git/record
state, always.** A worktree that is `finalized` / merged / clean is **still
active** if it is sitting in a live mux or holds a live lock (e.g. resumed by a
psmux startup-restore). *"Active" means "intentionally in a mux."* Never reap,
clean, or GC such a worktree out from under its live session — a merged/clean git
state is **not** license to reap.

- `gc` / `cleanup` already consult liveness: their `active` prune-bucket spares a
  worktree whose path is in the live-session set (`_build_active_paths` = the live
  mux batch + the cached `mux_live` / `bound_live` hints + the registered-session
  lock scan). Prefer these commands to hand-reaping.
- **But the cached signal can lag.** Historically `mux_live` was only stamped at
  launch / Stop / teardown and **never refreshed**, so a mux created *after* that
  stamp (a startup-restore landing minutes after resume) persisted a stale
  `false`, and the worktree could be misclassified `completed` and lose its
  protection. Fixed in **agent-worktrees ≥ 1.5.3-dev476** — the off-hot-path
  populate/doctor sweep now reconciles `mux_live` alongside `bound_live`. On older
  builds, **verify liveness against the live scan before reaping.**
- **Before any bulk `gc --clean`**, confirm no target is in a live mux
  (`psmux ls` / `tmux ls`) or holds a live lock. When in doubt, prefer **per-item
  `<project> cleanup --worktree-id <id> --clean`** (add `--include-unused` for a
  no-commit/no-turn shell) — it re-checks prune-safety and refuses an active
  session, so you can target exactly the intended (e.g. unused / titleless)
  worktrees without risking a live-mux sibling.

## Orphaned bound Copilot with no mux — Reclaim, not Resume

A worktree can show **Active** but offer only **Resume** when a bound Copilot (a
live `inuse.<pid>.lock`) owns it while its `wt-<id>` mux is gone — a bare /
orphaned resume (e.g. a startup-restore whose launcher shell died). Resuming
forks a second Copilot that collides with the live lock (*"active process
lock"*). The picker is meant to offer **Reclaim / Stop** for this state. To
resolve by hand:

- **Reclaim the orphan, then resume fresh:**
  ```
  <project> reclaim --worktree-id <id> --bare-only        # dry run — lists targets
  <project> reclaim --worktree-id <id> --bare-only --yes  # reap bare orphans
  ```
  A Copilot that is **mux-homed but its mux has died** is *not* `--bare-only` (its
  cwd is the worktree, homing `mux`); target it with
  `<project> reclaim --worktree-id <id> --yes` (no `--bare-only`).
- **Windows has no `remux`** (ConPTY cannot adopt a running process) — the
  `reclaim` → resume-fresh path above is the only route; do not expect to reattach
  a bare Copilot in place.
- **Clean residual stale locks:** after the process is gone, a dead-PID
  `inuse.<pid>.lock` can linger and keep the session looking busy. Remove **only**
  locks whose PID is confirmed dead (validate the PID first), then resume.

## Notes

- Idempotent: a second `--fix` run finds nothing new.
- The misalignment audit is informational — the resume path no longer honors a
  foreign `parent_session` for cwd, so opening a worktree always runs in its own
  directory.
