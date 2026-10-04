# Archival Content Policy — what belongs in a session's `files/` folder

`files/` exists for **persistent storage of curated session artifacts** —
small reference documents, architecture notes, task breakdowns — that
shouldn't land in a repo. It is **not** a general-purpose scratch directory,
a package cache, or a place to stage a cloned repository for inspection.

`agent_logger.sync` already refuses to copy installed plugins, credentials,
settings, or other `~/.copilot` state at the sync boundary (see
[`architecture.md`](architecture.md) § *Session sync*). This doc covers the
one boundary that engine can't police on its own: the **contents a consumer
writes into `files/` during a session**, which sync then faithfully (and
indiscriminately) carries forward. A consumer that doesn't clean up after
itself — a virtualenv it created to test something, a repo it cloned to
inspect, a browser profile from a login flow — leaks that scratch into
every archived copy of the session from then on.

## Why this matters

An operator audit of one long-running deployment found a session corpus
that had grown far beyond its transcript content: a large share of total
archived bytes turned out to be stray virtualenvs, full repository
checkouts, browser profiles, and ad-hoc test fixtures that had been written
into `files/` and never cleaned up, then faithfully synced and retained
indefinitely. None of it was session content in any meaningful sense — it
was scratch that outlived the task that created it.

## Denylist — never acceptable in `files/`

Any of the following found anywhere in a `files/` subtree — not just at the
top level; a wrapper folder around one of these still counts — marks that
whole top-level entry as non-archival scratch:

| Pattern | What it is |
|---|---|
| `pyvenv.cfg`, `site-packages/`, a `Scripts/activate` / `bin/activate` script, a name containing `venv` | A Python (or similar) virtual environment |
| A `.git/` directory anywhere in the subtree | A cloned repository checkout |
| A name containing `pytest-` (or a bare `tmp_path`/`tmpdir`-shaped directory) | A test framework's temp-directory fixture |
| Browser profile markers (`Cookies`, `Local State`, a `places.sqlite`, a `.mozilla`/`.config/google-chrome`-shaped tree) | A captured browser profile |
| `__pycache__/`, `node_modules/` | A language runtime's compiled-artifact or dependency cache |
| A name containing `blob-` paired with many same-sized files | Synthetic test fixture data |

This list is **evidence-based, not exhaustive** — build it from what you
actually find leaking through, not from enumerating every conceivable
pattern up front. Expect to add entries.

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

## If you're reconciling two copies of the same corpus

Comparing an old sync destination against a current one (e.g. while
migrating targets, or auditing retention) has three sharp edges worth
knowing about up front:

1. **Don't compare non-session bookkeeping directories as if they were
   sessions.** Only treat entries that are actually session-identifier
   shaped (e.g. UUIDs) as sessions; a lock-file directory or a
   provider-rescue side-channel sitting at the same tree depth is not one.
2. **A file present only in the newer copy is not a loss.** It usually
   means the schema gained a field after the older copy was taken. Only
   the reverse direction — present in the old copy, missing from the new
   one — is worth investigating.
3. **Junk classification must operate at the same granularity the
   original prune used.** If a whole top-level `files/<name>` folder was
   removed because *something inside it* matched a denylist pattern, a
   later byte-level diff will see every individual surviving-elsewhere
   file as "unexpectedly missing" unless it re-applies that same
   whole-folder reasoning. Conversely, never assume everything in such a
   gap *is* junk, either — verify it, since a partially-synced legitimate
   reference fixture looks identical to a partially-pruned junk folder
   until you actually look.

## See Also

- [`architecture.md`](architecture.md) § *Session sync* — the sync-boundary
  scoping this policy complements
