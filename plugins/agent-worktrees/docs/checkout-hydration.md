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
| `hydration --release-recovery-hold <id> [--json]` | Clear a recovery hold after relationships are reconciled. |
| `dehydrate --capabilities` | The `agent-worktrees.checkout-hydration.v1` contract. |

Exit codes: `0` success, `2` a hold that needs inspection, `1` a structured
refusal (`--json` prints `{"complete": false, "code": ..., "error": ...}`).

## Catalog

Each dehydrated worktree has one entry at
`<checkout-parent>/.dehydrated/<worktree-id>.json`, beside the recorded checkout
path (normally the project's resolved worktree root). Lookups use the recorded
checkout path first, so a later change of the configured root does not lose
the entry. When the `.dehydrated` directory is first created, its parent
directory is also synced. The entry is written atomically (temporary
file, `fsync`, replace) **before** removal, and is retired only after a
verified rehydration. Schema `agent-worktrees.checkout-dehydration` version 1:

- **identity**: repo, worktree ID, creation nonce, checkout path, anchor.
- **git**: expected local branch, exact HEAD, and the exact per-worktree
  config. Each key keeps its ordered value list (multi-valued keys such as
  `remote.origin.fetch`), and a valueless key (`[commit] gpgSign`) stays
  distinct from an empty value.
- **agency**: references from the tracking record (status, disposition,
  sessions, pair, claims, PR, worktree-data locators). It holds references
  only, never transcripts or credentials.
- **operation**: operation ID, `stage` (`removing`, `dehydrated`,
  `rehydrating`), prior state and timestamps.

The catalog entry is the only hydration state; the tracking record schema is
unchanged. A record is dehydrated while its catalog entry exists. Every
transition re-reads the entry under the record lock and checks its identity
(worktree ID, repo, branch, path, creation nonce and anchor) against the
tracking record. A mismatch is a hold, and nothing changes.

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
   succeeds and is empty, and no index entry has the assume-unchanged or
   skip-worktree flag (`git ls-files -v`: a lowercase tag or `S`). Both Git
   status and `git worktree remove` trust those flags, so a hidden edit would
   otherwise be lost. This is checked again just before removal.
7. Strict session liveness proves no live registered session, and no process
   uses the checkout. Unknown liveness refuses.

Dehydrate, rehydrate, reconcile and release-recovery-hold apply the
receiving-side `execution_spaces.require_record_mutation` admission under the
shared guards, before they read or change the catalog or checkout (code
`mutation-refused`). Recover applies it to the rebuilt record before it
writes anything.

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
`git worktree add --no-checkout <path> <branch>` (no `-b`, no fetch), restores
the recorded per-worktree config file atomically and verifies its entries, and
only then populates the files with `git reset --hard HEAD` inside the new
checkout. That reset is safe only because the checkout was just created and
holds nothing but its `.git` file and no index. Overrides such as
`core.symlinks` or `core.autocrlf` therefore apply to the files. It then
requires a clean status, verifies branch and HEAD, and deletes the catalog
entry last.

Reconcile never removes a valid checkout:

- Stage `removing` with a clean, valid checkout at the snapshot HEAD rolls
  back to hydrated; a different HEAD or a partly removed (dirty) checkout is a
  hold.
- A valid checkout at any other stage completes the rehydrate. An unpopulated
  checkout (only `.git`, no index) gets its config and then its files; files
  without an index, or a checkout that is not clean afterwards, are a hold.
- A partial or foreign path, or an absent path that Git still registers, is a
  hold.
- An absent, unregistered path becomes `dehydrated`.

Recover rebuilds a missing tracking record only for a `dehydrated` entry whose
identity matches its location and project and whose branch tip still equals
the snapshot. With `--apply` it takes the lifecycle and admission guards and
the record lock, re-reads the entry, and creates the record only if it is
still absent. The rebuilt record is non-active (`complete`), so it never
becomes a live owner. Its catalog entry gains a `recovery_hold`, because only
references, not relationships, are restored: `rehydrate` refuses with code
`recovery-hold`, and the entry is kept, until an operator reconciles the
relationships and runs `hydration --release-recovery-hold <id>`. Anything
contradictory is reported as a hold.

## Consumers

A dehydrated worktree has the `DEHYDRATED` git state instead of `GONE`.
`prune`/`cleanup` give it the non-cleanable `dehydrated` bucket (UNSAFE),
`gc` skips it as `dehydrated` and ignores the `.dehydrated` directory in its
orphan sweep, pair sync is blocked and `remove-system` names it as a blocker.
`finalize` (including `--abandon --handoff-to`) refuses before it rehomes or
publishes any obligation. Claimant resolution treats a dehydrated record, or
a catalog-only recovery candidate, as a held live claimant, so the sweep does
not abandon its inbound or outbound claims. Managed branch deletion and
feature-branch moves (`branch -f`) refuse a branch that a catalog entry
reserves. Reservation discovery reads fresh entries from today's configured
roots and from the catalog beside every recorded checkout, so it survives a
worktree-root change. It fails closed: for an unreadable or corrupt entry the
branch comes from its tracking record; when that is unknown, or the catalog
directory cannot be listed, every branch of that repository is held until the
entry is reconciled.

Observation paths (for example `prune` assessing every inactive record) share
one parsed index per catalog directory. The index is revalidated on each call
by a cheap signature of the entries (name, size, mtime and inode), so any
publication or retirement re-reads it. Transitions and branch mutations always
read entries fresh.
