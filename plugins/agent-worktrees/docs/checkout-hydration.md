# Reversible checkout dehydration

> **Status.** Manual commands, hydrate-before-entry and opt-in automatic
> post-exit dehydration are implemented. Automatic dehydration is **off** unless
> the machine-local project config enables it (see
> [Automatic dehydration and entry hydration](#automatic-dehydration-and-entry-hydration)).
> Manager UI, `(D)` suffixes and a Manager auto/manual control (Phase 5) are
> not implemented yet. `agent-worktrees dehydrate --capabilities` reports
> `"trigger": ["manual", "automatic-post-exit"]`, `"automatic": true`,
> `"automatic_policy"` (key and default) and `"entry_hydration": true`.

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
| `hydration --run-pending [--json]` | Run one bounded automatic pass (reconcile, then queued candidates). The resident status monitor runs this. |
| `dehydrate --capabilities` | The `agent-worktrees.checkout-hydration.v1` contract. |

Exit codes: `0` success, `2` a hold that needs inspection, `1` a structured
refusal (`--json` prints `{"complete": false, "code": ..., "error": ...}`).

## Catalog

Each dehydrated worktree has one entry at
`<checkout-parent>/.dehydrated/<worktree-id>.json`, beside the recorded checkout
path (normally the project's resolved worktree root). Lookups use the recorded
checkout path first, so a later change of the configured root does not lose
the entry. When the `.dehydrated` directory is first created, its parent
directory is also synced. Each root that holds a catalog is also recorded in
`dehydration-roots.json` in the project's state directory (written
atomically before the entry). Recovery, branch reservations and claimant
lookup use this registry, so they find an entry after a root change even when
the tracking record is lost. An unreadable registry holds every branch of the
repository. The entry is written atomically (temporary
file, `fsync`, replace) **before** removal, and is retired only after a
verified rehydration. On POSIX the entry and the registry are created
owner-only (`0o600`), and a new `.dehydrated` directory is `0o700`. Schema `agent-worktrees.checkout-dehydration` version 1:

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
tracking record. A mismatch is a hold, and nothing changes. Before any
checkout change, the whole snapshot is validated (absolute checkout path,
hexadecimal HEAD, a safe branch name, the per-worktree config structure and
its base64 payload, and other field types). A malformed entry is a hold, not
an error trace.

## Dehydrate gates

Dehydrate refuses, without writing a catalog entry, unless all hold:

1. The worktree is managed (not the anchor, a reference or an unmanaged
   checkout) and is not finalizing.
2. HEAD is the recorded branch: detached, unborn or another branch refuses.
3. No merge, rebase, cherry-pick, revert, bisect or multi-commit sequencer
   operation is in progress.
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
8. The per-worktree config has no credential-bearing key (code
   `credential-config`). Parsed key names are matched case-insensitively:
   `http.extraheader` and `http.<url>.extraheader`, every `credential.*` and
   `credential.<url>.*` key, `http.cookiefile`, `http.sslkey`, `http.sslcert`,
   `http.sslcertpasswordprotected`, and any key whose last component ends in
   `password`, `token` or `secret`. The refusal names the keys, never their
   values. This is checked again just before removal.

Dehydrate, rehydrate, reconcile and release-recovery-hold apply the
receiving-side `execution_spaces.require_record_mutation` admission under the
shared guards, before they read or change the catalog or checkout (code
`mutation-refused`). Recover applies it to the rebuilt record before it
writes anything.

A removed checkout cannot supply Git worktree proof. When the recorded path is
absent and outside today's worktree root, namespace admission
(`record_namespace_matches`) accepts the catalog entry beside that path as
placement evidence. The entry must match the record and its on-disk receiving
ledger in worktree ID, repository, branch, checkout path and creation nonce,
and must name the configured anchor. Missing, unreadable or mismatched
evidence still refuses. Ownership and platform checks are unchanged.

The operation holds the finalize lock, then the lifecycle and launch-admission
guards. It then runs `git worktree remove <path>` from the anchor **without
`--force`**. With Git 2.56, plain removal deletes ignored files and refuses
modified or untracked files, so the clean gate is enough and force is never
needed. Removal is verified (path absent and not in `git worktree list`)
before the stage becomes `dehydrated`. The catalog publication and the
removal run under a per-repository reservation lock in the Git common
directory. Under that lock, after the final clean probe and immediately
before removal, the retained-data hold is checked again, so an ignored
`.effort-agenda` created while the lock was awaited refuses instead of being
deleted. If removal fails, the entry is kept and reconcile decides: an
intact clean checkout rolls back, a partly removed one is a hold.

## Rehydrate, reconcile and recover

Rehydrate requires that the branch exists and its tip equals the cataloged
HEAD; a moved tip is a `conflict` refusal (never a reset or a recreated
branch). The destination must be absent or empty. It runs
`git worktree add --no-checkout <path> <branch>` (no `-b`, no fetch), restores
the recorded per-worktree config file atomically and verifies its entries, and
only then populates the files with `git reset --hard HEAD` inside the new
checkout. The entry records `config_restored: true` before the files are
populated; a retry after that marker never rewrites the per-worktree config,
so newer settings survive an interrupted catalog retirement. The marker is
cleared whenever the entry returns to `dehydrated` and before any replacement
checkout is added, so a new checkout always gets the snapshot config. That reset is safe only because the checkout was just created and
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
  The snapshot config is restored only at stage `rehydrating` without the
  `config_restored` marker; a populated checkout whose per-worktree config
  differs from the snapshot is a `worktree-config-conflict` hold.
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

## Automatic dehydration and entry hydration

`checkout_hydration_auto` adds two behaviors on top of the same owner and
gates. It never duplicates a gate: automatic removal calls `dehydrate()`, and
entry calls `rehydrate()`.

### Entry hydration (always on)

Managed entry restores a dehydrated checkout before any path-dependent policy,
trust, projection or process-CWD use: mux resume
(`resolve <id>`, before `agent_policy.require`), Bridge resume
(`resolve <id> --json`, before launch preflight), Picker resume (before
preflight), `embody --worktree-id` and `embody --codename` (before the embody
operation, so before its agent-policy check; a codename is resolved on this
machine only), and remote seed admission (`launch_request` resume, before its
agent-policy check). Remote seed admission hydrates only after the request
command's own side-effect-free invocation validation passes; a rejected
envelope (for example `--dry-run` or a native-seed flag that disagrees with the
envelope version) changes no checkout, catalog entry or candidate. `embody`
likewise hydrates only after its own flag validation: a rejected combination
(for example `--deliver-pending-seed` with `--fresh` or `--seed`) changes
nothing. `--new` creates a new checkout and the anchor is never
dehydratable, so neither hydrates. A harness record paired with a knowledge **worktree**
also hydrates
that sibling; pair state, claims, efforts, PRs and session succession are not
changed. A declared pair whose sibling record is missing, unreadable, not a
reciprocal local pair, or named by an unqualified reference refuses entry with
`sibling-unavailable` when hydration is involved: this entry restored the
primary checkout, or the sibling project's catalog (its configured or
registered roots, by worktree id) holds an entry for the sibling or cannot be
read. Then it is never treated as unpaired. Otherwise entry proceeds exactly as
it would without dehydration, and the unresolved pair stays the pair owner's
concern. When entry restored a
paired harness checkout (paired with a knowledge worktree or with the knowledge
anchor; only a worktree sibling is hydrated), it then runs the existing pair-preparation owner
(`knowledge_plugins.compose_from_pair`), which restores the ignored pair
settings and generated companion instructions that dehydration removed. A
composition opt-out (`disabled`) is preserved; a pair error or failed
preparation refuses entry with `pair-preparation-failed`. Before any restore in
a paired entry (of the primary checkout or of its sibling alone), entry writes a `pair-readiness-pending.json` marker in the
hydration state directory and removes it only after the sibling resolves and
pair preparation succeeds. A retry after a refusal or a crash therefore runs
sibling validation and pair preparation again, even though the checkout is
already present. Every restore of a paired harness checkout writes the same
marker before it retires the catalog entry, including a manual `rehydrate` and
reconciliation that completes an interrupted rehydrate, so the next entry
prepares the pair. If the marker cannot be written, the entry is kept. Previews (`--dry-run`), `--plan`, status, list and roster reads never
hydrate. A refusal (for example a `hold` for an unreadable or newer catalog
schema, or an occupied destination) stops the launch with exit code `3`, and
nothing starts. JSON surfaces report it as
`{"ok": false, "reason": "checkout-dehydrated", "worktree_id": ..., "code": ...,
"remedy": "agent-worktrees rehydrate <id>", "error": ...}`. Manual policy does not disable entry hydration, so rolling
back to manual leaves every dehydrated entry held, inspectable and enterable.

Entry first writes an **entry lease** (`hydration-entry.json` in the
worktree's external state directory) and then reads the catalog. Dehydrate
checks the lease before inspection and again after it publishes its
`removing` entry; while a lease is held, it deletes its own entry and refuses
with the transient code `entry-pending`. Entry withdraws any queued candidate
before it reads the catalog, and automatic dehydration re-checks its candidate
after it publishes, so an entry that finished meanwhile supersedes it
(`superseded`). Rehydration itself runs under the owner's finalize lock and
lifecycle and launch-admission guards; entry retries a busy guard for up to 30
seconds and then refuses with `busy`.

Entry releases its lease as soon as hydration finishes or is refused, so a
manual `dehydrate` right after an entry does not wait. The lease records the
owner's pid and process start time: a dead or replaced owner releases it, and
the 120-second expiry only bounds a lease left by a crash whose owner cannot be
disproved. After entry, the launched process and its registered session are
held by the live-process and live-session gates. A manual `dehydrate` that runs
in the short gap between a finished entry and the launch's first process can
still remove the checkout; the launch then fails on the missing path and the
next entry restores it. Automatic dehydration is not exposed to that gap,
because entry withdraws its candidate.

### Automatic post-exit dehydration (opt-in)

Enable it in the project's machine-local `config.yaml` (the file
`config.default_config_path()` resolves for that project):

```yaml
checkout_dehydration:
  mode: automatic   # anything else, or unreadable config, means manual
```

The `post-exit` hook only queues a candidate (`dehydration-candidate.json`)
for a managed session worktree and wakes the resident status monitor (a cheap
no-op when it is already running); it never runs a pass or dehydrates inline.
An end of turn or a Stop callback is not process retirement. Existing resident
maintenance then processes at most three candidates per pass, from the
resident status monitor's periodic loop. The monitor's tick never blocks:
at most once a minute, and at once on monitor startup (restart reconciliation),
a background thread runs `hydration --run-pending` as a windowless child
process with a 5-minute timeout, only for registered projects whose policy is
automatic and which have a queued candidate or an interrupted catalog stage.
So a sole worktree is dehydrated after its process retires even when no other
exit follows. A project with retained work keeps the monitor's demand open; the
attempt limit bounds it. Each one must show, again, strict session liveness with
no active or mux-live session, no pending seed or staged launch, no entry
lease, and then every `dehydrate()` gate, including no process with its CWD in
the checkout. Transient refusals (`live-session`, `live-process`,
`entry-pending`, `busy`, `pending-launch` and unavailable probes) keep the
candidate for a later pass, up to 24 attempts. Permanent refusals (for
example `dirty-checkout` or `retained-data`) drop it and keep the checkout.
Switching back to manual drops queued candidates on the next pass.

Every queued exit carries a random `generation`. Candidate writes, entry
withdrawal and retry updates take one cross-process candidate lock, and a retry
only updates the generation it read, so a withdrawn or replaced candidate is
never recreated. `dehydrate()` accepts a final pre-removal check that runs
inside its finalize lock, lifecycle and launch-admission guards; the automatic
caller uses it to reload the record and re-confirm the generation, retirement
and pending-launch or seed state. Seed staging takes the same admission guard,
so a seed staged after the first check refuses removal (`pending-launch`).

The monitor's maintenance starts closed and opens when the monitor activates
its request surfaces, together with the other request surfaces; a drain closes
it and a later activation reopens it. A pass that is still running after a
drain keeps the monitor busy (and its ownership held) until its child ends.
Every monitor exit, including a timed shutdown that bypasses busy reasons and
a loop error, closes maintenance and then waits, outside the state lock, for a
running pass for up to the pass timeout. If the pass is still running, the
monitor terminates its child and waits briefly again before it releases route
ownership. The child runs windowless in its own process group, and a timeout or
shutdown stops its whole process tree, including its Git children. The root is also killed by its handle. If
the tree stop cannot be confirmed, or the root is still running, maintenance stays busy: it keeps route ownership
for a bounded wait (30 seconds after the root exits), logs the condition and starts no new pass. The next pass
reconciles an interrupted operation.

Each pass first settles up to three interrupted `removing` or `rehydrating`
catalog entries with `reconcile` (restart reconciliation). It never removes a
valid checkout, and leaves unreadable entries as holds for inspection. Entries
are attempted least recently attempted first, with attempt times kept in the
project's `.hydration-reconcile.json`, so persistent holds cannot starve later
entries. A hold whose evidence (catalog entry, checkout path and the anchor's
worktree metadata) is unchanged is backed off for an hour and does not count as
monitor demand. A pass
never raises into the post-exit hook.

## Consumers

A dehydrated worktree has the `DEHYDRATED` git state instead of `GONE`.
`prune`/`cleanup` give it the non-cleanable `dehydrated` bucket (UNSAFE),
`gc` skips it as `dehydrated` and ignores the `.dehydrated` directory in its
orphan sweep, pair sync is blocked and `remove-system` names it as a blocker.
`finalize` (including `--abandon --handoff-to`) refuses before it rehomes or
publishes any obligation. Claimant resolution treats a dehydrated record, or
a catalog-only recovery candidate, as a held live claimant, so the sweep does
not abandon its inbound or outbound claims. The lookup uses the target
project's own configuration and the root registry (which alone suffices when
that configuration cannot be loaded); when the registry or a catalog
directory cannot be read, the claimant is unknown, not dead. Managed branch deletion and
feature-branch moves (`branch -f`) refuse a branch that a catalog entry
reserves. Every managed `branch -f` write uses `git_ops.force_branch`: the
feature-branch sync and merge paths, and the PR-head snapshot writes in
`create-pr`, `finalize` and the PR rewrite. `create-pr` prepares its
snapshot ref before it persists publishing intent, so a reservation refusal
leaves the attempt retryable. Each write checks reservations
again, under the same reservation lock as dehydration, so a dehydration that
starts during a fetch cannot be overwritten. Branches named by a cataloged PR
reference are reserved too. When a readable entry names a different branch
than its tracking record, both branches are held. When an entry's
recorded anchor names another repository, the reservation also stays with the
repository whose catalog placement holds it. The Git common-directory probe
ignores inherited `GIT_DIR`-style selectors, and the guarded branch write runs
in the same isolated scope, so the check and the write target one repository. Reservation discovery reads fresh entries from today's configured
roots and from the catalog beside every recorded checkout, so it survives a
worktree-root change. It fails closed: when configuration cannot be loaded the mutation refuses; for an unreadable or corrupt entry the
branch comes from its tracking record; when that is unknown, or the catalog
directory cannot be listed, every branch of that repository is held until the
entry is reconciled.

Observation paths (for example `prune` assessing every inactive record) share
one parsed index per catalog directory. The index is revalidated on each call
by a cheap signature of the entries (name, size, mtime and inode), so any
publication or retirement re-reads it. Transitions and branch mutations always
read entries fresh.
