# Archival Content Policy — what belongs in a session's `files/` folder

`files/` exists for **persistent storage of curated session artifacts** —
small reference documents, architecture notes, task breakdowns — that
shouldn't land in a repo. It is **not** a general-purpose scratch directory,
a package cache, or a place to stage a cloned repository for inspection.

## What's already handled: `sync.detritus`

`agent_logger.sync.detritus` already detects and excludes four recurring
"scope creep" categories, by on-disk signature, at sync time — see
[`deployment-topologies.md`](deployment-topologies.md) for the full
mechanism:

| Category | Detected by |
|---|---|
| Python virtual environment | a `pyvenv.cfg` file at the environment root |
| Git clone or linked worktree | a `.git` entry named exactly `.git` — a directory for a normal clone, or any regular file (the linked-worktree `gitdir:` pointer shape; its content is not verified) |
| `node_modules` tree | the directory name itself |
| Chromium browser profile | a `Local State` file alongside a `Default`/`Profile N` subdirectory containing `Preferences` + a `Network` directory |

Detection walks top-down and excludes the **whole subtree** the moment a
root signature matches — it never descends into an already-excluded root,
and a later byte-level audit should reason the same way (see *Reconciling
two copies*, below). This is **detection, not prevention**: it keeps these
artifacts out of the synced archive and removes any stale copy already on a
filesystem-backed destination, but it does not stop an agent from writing
them into `files/` in the first place, and the latest exclusion footprint is
visible via `session-sync status`.

## What detritus detection does *not* yet cover

The four categories above are the ones proven common enough to warrant a
dedicated signature. They are not exhaustive. Categories observed in the
wild that still land in `files/` unfiltered:

- Test-framework temp-directory fixtures (e.g. a copied `pytest` `tmp_path`/
  `tmpdir`), which carry no root-level signature distinguishing them from
  ordinary curated output.
- Synthetic test fixture data (large sets of same-sized placeholder files).
- A manually-downloaded build/CI tool (e.g. a CI runner package) extracted
  into `files/` for inspection.

If you find a new recurring pattern, the right fix is usually a new
signature in `sync.detritus`, not just a documentation note — file an issue
or open a PR adding the detection, following the existing categories'
shape (a cheap, root-directory-local check, never a full-tree scan).

## Allowlist / acceptable content

- **Curated reference documents** — Markdown, plain text, small images —
  plans, draft commit messages, PR bodies, issue text.
- **Deliberately pinned reference fixtures, *with documented provenance*** —
  vendoring a subset of an upstream project is fine *if and only if* it
  carries its own explanation of why it's there: the pinned source
  repository, tag/commit, and license. A fixture without that
  self-documentation is indistinguishable from scratch and should be
  treated as such until proven otherwise.
- **Small structured artifacts the pipeline itself writes** — digests,
  context summaries, workspace metadata — these are consistently tiny and
  never the source of unbounded growth.

## Known-benign cruft — don't flag these as a problem

- Stale process-lock files (zero-to-a-few bytes) left behind by a
  long-exited process.
- An editor/CLI's own undo-history or snapshot backups — individually
  rotated over time by the tool itself; partial survival across two copies
  of the same session is the *normal* pattern, not evidence of loss.
- Ephemeral database journal/WAL sidecars next to a session's own SQLite
  state.

## Reconciling two copies of the same corpus

Comparing an old sync destination against a current one (e.g. while
migrating targets, or auditing retention) has four sharp edges worth
knowing about up front:

1. **Don't compare non-session bookkeeping directories as if they were
   sessions.** Only treat entries that are actually session-identifier
   shaped (e.g. UUIDs) as sessions; a lock-file directory or a
   provider-rescue side-channel sitting at the same tree depth is not one.
2. **A file present only in the newer copy is not a loss.** It usually
   means the schema gained a field after the older copy was taken. Only
   the reverse direction — present in the old copy, missing from the new
   one — is worth investigating.
3. **Audit at the same granularity detection used.** `sync.detritus`
   excludes whole subtrees from a root signature, not individual files — a
   later byte-level diff will see every individual file *below* an excluded
   root as "unexpectedly missing" unless it re-applies the same
   whole-subtree reasoning. Conversely, never assume everything in such a
   gap *is* excluded detritus, either — a partially-synced legitimate
   reference fixture (e.g. an incomplete transfer, unrelated to detritus
   exclusion) looks identical to correctly-excluded detritus until you
   actually check file counts on both sides.
4. **A whole-subtree-missing heuristic is itself a blind spot.** Applying
   rule 3's own reasoning automatically — "this whole top-level child is
   gone from the new copy, so it must have been excluded detritus" — can
   silently mark a session "clean" even when it is missing tens of
   thousands of genuinely unaccounted files, as long as the gap happens to
   be shaped like a plausible detritus exclusion. A coarse pass's "clean"
   verdict is a hypothesis, not proof: any tool actually acting on the
   reconciliation (a dedup, a migration, a deletion) should surface its own
   "present in old copy, unmatched in new copy, not independently
   classified as detritus/junk" list for direct inspection — grouped by
   session — rather than trusting a prior pass's verdict at face value.

A reconciliation can also turn up a **structurally different legacy
archival format**, not just missing files from the current one — e.g. a
predecessor archival convention that stored a condensed digest rather than
raw session content. That is not a duplicate of anything in the current
corpus, so a dedup tool doesn't apply to it; confirm the content *shape*
matches before assuming any two trees are comparable at all, and prefer
preserving a structurally distinct legacy artifact (as its own clearly-
labeled sibling) over deleting or forcing it through dedup logic it wasn't
designed for.

## See Also

- [`deployment-topologies.md`](deployment-topologies.md) — the detritus
  detection mechanism this policy documents
- [`architecture.md`](architecture.md) § *Session sync* — the broader
  sync-boundary scoping this policy complements
