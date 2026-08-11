---
name: repairing-worktrees
description: >
  Diagnose and repair worktree/session health via the agent-worktrees `doctor`
  command (corrupt records, empty registries, stale status, orphaned session
  shells, cwd/path misalignment), and safely clean up / reap / close out
  worktrees without reaping a live session. Use when asked to:
  - 'repair worktrees' / 'repair sessions'
  - 'worktree / session doctor'
  - 'fix corrupt tracking records'
  - 'clean up empty sessions'
  - 'clean up / organize / backfill worktrees'
  - 'backfill worktree status' / 'backfill sessions'
  - 'close out a worktree' / 'release worktree claims'
  - "what did this worktree touch / its footprint"
  - 'why is a worktree showing 0 turns' / 'picker shows wrong session'
  - 'worktree shows active but only offers Resume'
  - 'reclaim an orphaned Copilot' / "can't resume — active process lock"
  For pruning worktree *directories* use `gc`/`cleanup`; for orphaned mux tabs
  use `reap-sessions`. Covers record + session-state health and liveness-safe
  cleanup.
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
  (`psmux list-sessions` / `tmux list-sessions`) or holds a live lock. When in doubt, prefer **per-item
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

## Deep close-out — investigate a worktree's footprint, and let it self-close

Before reaping a worktree, remember it may own **claims and off-machine state**:
it may have dug into a **CodeSpace**, a **cross-repo worktree**, one or more
**cross-repo PRs**, borrowed containers, or other shared spaces. Reaping it
blindly orphans those. Close-out is deeper than the local git/liveness check.

- **Check the claim ledger — but do not trust it alone.**
  ```
  <project> claims <id>                  # outbound resources it owns + inbound tasks
  ```
  `finalize` is claim-aware (its gate blocks on unreleased outbound claims), and
  `claims release <ref>` / `claims settle <ref>` / `claims sweep --apply` retire
  them. **But the ledger is best-effort and relatively new:** many older
  worktrees show `(none)` even though they really did touch a CodeSpace or a
  cross-repo PR — those actions predate consistent claim-journaling. An empty
  ledger is *not* proof of an empty footprint.
- **Deep-investigate what it actually touched.** Corroborate the ledger against
  the worktree's own history and artifacts:
  ```
  <project> recent-messages --worktree <id>      # quick peek at recent work
  <project> head-session --worktree <id>         # resolve its head session id
  <project> session-transcript <session_id>      # full transcript of what it did
  ```
  plus its effort file, and its branch's cross-repo footprint (CodeSpaces it
  connected to, `<other-repo>` worktrees/PRs it opened). **Be willing to dig into
  those dependent spaces** and verify each is closed out (PR merged/closed,
  CodeSpace done/stopped, cross-repo worktree finalized, container lease
  released) *before* reaping the owner.
- **Best: file a durable close-out *task* and let worktrees resolve async.**
  Rather than reconstruct each footprint by hand, queue a close-out task with a
  strong wind-down **goal** and let a worker (often the worktree itself) drive it
  — worktrees then resolve **asynchronously over a duration**, at scale, instead
  of blocking this session. Use the **agent-dispatch** queue:
  ```
  agent-dispatch create "Close out <id>" \
    --target-worktree <id> --dedup-key closeout:<id> \
    --goal "Wind down cleanly — everything filed or built out as efforts" \
    --done-criteria "cross-repo PRs landed/closed; CodeSpaces disconnected; \
      cross-repo worktrees finalized; claims released; worktree finalized" \
    --prompt "Close out worktree <id>. Investigate what it touched (its claims \
      ledger, session transcript, effort files, cross-repo PRs/CodeSpaces). File \
      or build out any unfinished work as efforts, land or close cross-repo PRs, \
      disconnect CodeSpaces, finalize cross-repo worktrees, release/settle your \
      claims, then finalize."
  ```
  **Dedup first** (`agent-dispatch find` / `sweep`) so you file one close-out per
  worktree; workers then `claim` → `start` → `complete` it on their own cadence.
  `--spawn` (with `--spawn-backend bridge` or `embody`) kicks a worker
  immediately; otherwise the task waits in the queue for async pickup.
- **Or drive one now (synchronous).** To close a single worktree immediately,
  embody its session directly — `embody` is **agent-worktrees'** embodiment verb
  (it resumes a detached session and auto-registers with **agent-bridge**), not
  an agent-dispatch verb (agent-dispatch merely *uses* it as a spawn backend):
  ```
  <project> embody --worktree-id <id> --seed "Close yourself out: land or close \
    any cross-repo PRs, disconnect any CodeSpace, finalize any cross-repo \
    worktrees, release your claims, then finalize."
  # or dispatch the same to its owning agent: `<repo> bridge send <machine> "…"`
  ```
  Let the worktree confirm it is fully wrapped up, *then* finalize/reap it. Only
  fall back to manual `claims release`/`sweep` for a worktree that genuinely
  cannot be resumed (its session is gone) or whose obligations are provably
  gone-and-safe.

## Notes

- Idempotent: a second `--fix` run finds nothing new.
- The misalignment audit is informational — the resume path no longer honors a
  foreign `parent_session` for cwd, so opening a worktree always runs in its own
  directory.
