# Reversible checkout dehydration (manual)

> **Status: manual only.** These operations run only when an operator or
> caller invokes them explicitly. Automatic stop/resume triggers (Phase 4) and
> Manager UI, `(D)` suffixes and auto/manual configuration (Phase 5) are not
> implemented yet. `agent-worktrees dehydrate --capabilities` reports
> `"trigger": "manual", "automatic": false`.

Dehydration removes the files of a **clean** managed linked checkout to save
disk, and keeps everything else: the worktree ID, the local branch and its
exact HEAD, the tracking record, session and pair references, claims, PR
references and worktree-data locators. Rehydration recreates the checkout at
the same path from the existing branch. Dehydration is not finalization: it
never deletes the branch, unpairs, releases claims or changes lifecycle status.

## Commands

| Command | Effect |
|---------|--------|
| `dehydrate <id> [--plan] [--json]` | Check every gate; `--plan` reports admissibility without changes. |
| `rehydrate <id> [--json]` | Recreate the checkout from the cataloged branch and HEAD. |
| `hydration --reconcile <id> [--json]` | Settle an interrupted dehydrate or rehydrate from fresh evidence. |
| `hydration --recover [--apply] [--json]` | Report (and with `--apply`, rebuild) tracking from catalog entries. |
| `dehydrate --capabilities` | The `agent-worktrees.checkout-hydration.v1` contract. |

Exit codes: `0` success, `2` a hold that needs inspection, `1` a structured
refusal (`--json` prints `{"complete": false, "code": ..., "error": ...}`).

## Catalog

Each dehydrated worktree has one entry at
`<worktree-root>/.dehydrated/<worktree-id>.json`, where `<worktree-root>` is the
project's resolved worktree root. The entry is written atomically (temporary
file, `fsync`, replace) **before** removal, and is retired only after a
verified rehydration. Schema `agent-worktrees.checkout-dehydration` version 1:

- **identity**: repo, worktree ID, creation nonce, checkout path, anchor.
- **git**: expected local branch, exact HEAD, recorded per-worktree config.
- **agency**: references from the tracking record (status, disposition,
  sessions, pair, claims, PR, worktree-data locators). It holds references
  only, never transcripts or credentials.
- **operation**: operation ID, `stage` (`removing`, `dehydrated`,
  `rehydrating`), prior state and timestamps.

The tracking record carries an additive `hydration` field
(`state`, `catalog`, `operation_id`, `head`, `revision`, `at`). A record is
treated as dehydrated when that state is a catalog stage **or** a catalog
entry exists, so a lost or stale field never turns a stored checkout into
garbage. Older readers ignore the field.

## Dehydrate gates

Dehydrate refuses, without writing a catalog entry, unless all hold:

1. The worktree is managed (not the anchor, a reference or an unmanaged
   checkout) and is not finalizing.
2. HEAD is the recorded branch: detached, unborn or another branch refuses.
3. No merge, rebase, cherry-pick, revert or bisect is in progress.
4. No submodules and no sparse checkout (refused explicitly for now).
5. No checkout-local retained data, using the same
   `checkout_data.preservation_hint` hold as finalize (for example a
   checkout-local `.effort-agenda`). The broader `preservation_reason` is not
   used, because it also flags ordinary ignored build output, which
   dehydration is meant to remove.
6. `git status --porcelain=v1 -z --untracked-files=all --ignore-submodules=none`
   succeeds and is empty, checked again just before removal.
7. Strict session liveness proves no live registered session, and no process
   uses the checkout. Unknown liveness refuses.

The operation holds the finalize lock, then the lifecycle and launch-admission
guards. It then runs `git worktree remove <path>` from the anchor **without
`--force`**. With Git 2.56, plain removal deletes ignored files and refuses
modified or untracked files, so the clean gate is enough and force is never
needed. Removal is verified (path absent and not in `git worktree list`)
before the stage becomes `dehydrated`.

## Rehydrate, reconcile and recover

Rehydrate requires that the branch exists and its tip equals the cataloged
HEAD; a moved tip is a `conflict` refusal (never a reset or a recreated
branch). The destination must be absent or empty. It runs
`git worktree add <path> <branch>` (no `-b`, no fetch), restores recorded
per-worktree config, verifies branch and HEAD, marks the record `hydrated`,
and deletes the catalog entry last.

Reconcile never removes a valid checkout:

- Stage `removing` with a valid checkout at the snapshot HEAD rolls back to
  hydrated; at a different HEAD it is a hold.
- A valid checkout at any other stage completes the rehydrate.
- A partial or foreign path, or an absent path that Git still registers, is a
  hold.
- An absent, unregistered path becomes `dehydrated`.

Recover rebuilds a missing tracking record only for a `dehydrated` entry whose
identity matches its location and project and whose branch tip still equals
the snapshot. The rebuilt record is non-active (`complete`) and dehydrated, so
it never becomes a live owner. Anything contradictory is reported as a hold.

## Consumers

A dehydrated worktree has the `DEHYDRATED` git state instead of `GONE`.
`prune`/`cleanup` give it the non-cleanable `dehydrated` bucket (UNSAFE),
`gc` skips it as `dehydrated` and ignores the `.dehydrated` directory in its
orphan sweep, the stale-record health check skips it, pair sync is blocked,
`remove-system` names it as a blocker and `finalize` refuses until it is
rehydrated. No path deletes its branch.
