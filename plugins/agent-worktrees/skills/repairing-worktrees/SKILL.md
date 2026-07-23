---
name: repairing-worktrees
description: >
  Diagnose and repair worktree/session health for a project via the
  agent-worktrees `doctor` command — corrupt tracking records, empty session
  registries, stale status, orphaned empty session shells, and cwd/path
  misalignment. Use when asked to:
  - 'repair worktrees'
  - 'repair sessions'
  - 'fix corrupt tracking records'
  - 'worktree doctor'
  - 'session doctor'
  - 'clean up empty sessions'
  - 'backfill sessions'
  - 'worktree health check'
  - 'why is a worktree showing 0 turns'
  - 'picker shows wrong session'
  For pruning finished worktree *directories* use `gc`/`cleanup`; for orphaned
  mux tabs use `reap-sessions`. This skill covers record + session-state health.
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

## Notes

- Idempotent: a second `--fix` run finds nothing new.
- The misalignment audit is informational — the resume path no longer honors a
  foreign `parent_session` for cwd, so opening a worktree always runs in its own
  directory.
