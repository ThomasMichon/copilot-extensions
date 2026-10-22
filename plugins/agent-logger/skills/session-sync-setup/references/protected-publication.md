# Protected publication

Reference for the [`session-sync-setup`](../SKILL.md) skill: the opt-in
`sync.redact_secrets` and `sync.session_files` settings of the `local` and
`onedrive` targets.

Two opt-in settings change what the `local` and `onedrive` targets publish.
Both default off; with both off, every target publishes exactly as before.

```yaml
sync:
  redact_secrets: true      # default false
  session_files: essential  # all (default) | essential
```

- **`session_files: essential`** publishes only these files from each
  session: `events.jsonl`, `workspace.yaml`, `origin.json`, `context.json`,
  `agent-worktrees.json`, `checkpoints/index.md`, and
  `review-annotations.json`. Everything else stays local, including
  `files/`, `rewind-file-snapshots/`, other checkpoints, `session.db`,
  `instructions/`, provenance sidecars, the session index, and any file kind
  added later. Change detection uses the same list, so editing an unlisted
  file publishes nothing.
- **`redact_secrets: true`** replaces known secret shapes (access tokens,
  keys, connection strings, credential assignments, PEM private keys) with
  `[REDACTED]` in each *pushed* copy. The local source is never modified.
  JSON and JSONL files are redacted inside JSON strings only, so every
  `events.jsonl` line stays valid JSON, with record order and non-string
  values unchanged. Other text files are redacted as a stream. A binary file
  that contains a known token shape, and any database file (`session.db`,
  the session index, SQLite/WAL sidecars), is *withheld*: not copied, and
  listed. JSONL is processed record by record and binaries are scanned in
  bounded chunks, so there is no size cap, truncation, or raw fallback.
  Matching is pattern-based and best-effort. This setting does not change
  the rendered-log redactor or any SSH/ingest target.
- **An unreadable transcript is deferred, never published raw.** Under
  `redact_secrets`, a JSONL file that ends in an incomplete record (for
  example, a write in progress) or contains a malformed record is deferred
  as a *whole file*. No new version is published: the last successfully
  published copy stays in place, or no copy is created if there was none.
  The pass reports `partial` with a sanitized reason (`incomplete-record` or
  `malformed-record`, or `malformed-document` for a `.json` file) and never
  includes file content. Full-sync completion is not advanced, and the next
  ordinary run retries the file; there is no separate retry loop. Once the
  source is complete, the next pass publishes the complete redacted version.
  Locked or changing files keep the usual `skipped N locked file(s), will
  retry` handling.
- **Unchanged inputs are not republished.** With `redact_secrets` on, each
  session records what was derived in
  `<target>/<machine>/.session-sync-projection/<session-id>.json`, so
  unchanged files are not re-derived on every pass. Changing either setting
  (or a new pattern-set version) changes the sync identity and forces one
  full reconcile.
- **Supported scope.** Only `local` and `onedrive` implement these settings.
  `ssh`, `ssh-tunnel`, and `ingest` refuse to run with either one on (a
  config error; `doctor` reports `[FAIL] session protection`). `rescue-push`
  and `compact-hub` are refused, and `sync.compact` is skipped during `run`.
  With `redact_secrets` on, process-log publication and its reclamation are
  also skipped, because process logs are not redacted.

> **Old raw copies are not retroactively protected.** Changing either setting
> forces one full reconcile. For each session still on this machine and
> selected by the repo filter, the files the settings select are published
> again: a destination copy that differs is replaced by the version the new
> settings produce, and an identical one is left alone. The reconcile removes
> no copy, so these stay exactly as previously published, possibly raw: files
> outside `session_files`; the earlier copy of a file that is now withheld;
> the last published copy of a deferred transcript, until its source
> completes; and copies of sessions the reconcile does not revisit (no longer
> on this machine, or excluded by the repo filter). `doctor` warns when it
> finds such copies. If they must not be kept, delete them, or clear this
> machine's destination folder and run `session-sync run --full`.

`run --dry-run --verbose` prints the effective scope without creating any
destination output: the `filter:` line (when a repo filter applies),
`protection: redact_secrets=on, session_files=essential`, and the same
`protection:` suffix on the `would push` line. A real `run` lists what it
held back (this example uses `session_files: all`):

```
session-sync: withheld 1 protected file(s): session-state/<sid>/files/a.bin [binary-secret-shape]
session-sync: deferred 1 protected file(s), will retry: session-state/<sid>/events.jsonl [incomplete-record]
```

`status` shows the configured `protection:` line and the last pass's
`protection_last:` counts (published, unselected, redacted strings, deferred,
withheld), with up to 10 sample paths and reasons.

With `sync.redact_secrets` or `sync.session_files` set, `run --dry-run
--verbose` also prints the effective `protection:` scope, and `doctor` adds a
"session protection" check that warns about destination copies the current
settings did not produce.
