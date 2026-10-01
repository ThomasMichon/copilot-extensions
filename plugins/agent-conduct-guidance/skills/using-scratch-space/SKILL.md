---
name: using-scratch-space
description: >
  Resolves a portable scratch root and a per-task, timestamped subfolder
  convention before an agent writes an ad hoc working file outside a
  repository checkout -- a downloaded artifact, an intermediate log, a draft
  PR/issue body, a one-off JSON/text dump, captured command output. Use
  whenever such a file is about to be written and no repository or session
  working directory already covers it. Not for files that belong inside a
  repository (those get a normal repo-relative path, no scratch root
  involved), and not a replacement for this session's own session-state
  folder when that is the more natural home (e.g. a handoff brief, a
  long-running task's own artifacts the session already manages).
---

# Using Scratch Space

A file written directly at a filesystem/drive root (`C:\`, `D:\`, `/`, `~`)
with no enclosing structure gives nobody -- a human glancing at the root, or a
later agent session -- any way to tell whether it is still needed or has long
since expired. Left unchecked this accumulates indefinitely: loose log files,
draft PR bodies, temp JSON dumps, and package-manager cache trees pile up
with no record of which task produced them or when they stopped mattering.

## Resolve the scratch root -- never hardcode one

Different machines and operators have different drive layouts; this skill's
own guidance (and any checked-in instruction that references it) must never
bake in a specific path. Resolve in this order, first match wins:

1. **`AGENT_SCRATCH_ROOT` environment variable**, if set -- an operator's or
   session's explicit choice. Honor it as-is (create it if it doesn't exist
   yet; don't second-guess its location).
2. **A machine-local convention the current context already resolves**, if
   one is available and documented for this environment (e.g. a harness's
   own machine-local config, a dotfiles-declared preference) -- prefer a
   mechanism already in scope over inventing a new one.
3. **The OS temp directory**, under an `agent-scratch` subfolder of its own
   (never loose at the temp root either, for the same reason as above):
   `$env:TEMP\agent-scratch` on Windows, `${TMPDIR:-/tmp}/agent-scratch` on
   POSIX.

Fall through silently -- don't ask the operator to configure something just
to write one scratch file; only escalate if even the OS temp directory isn't
writable.

## One timestamped subfolder per task

Within the resolved root, create a single subfolder per distinct task before
writing anything into it:

```text
<root>/<task-slug>-<YYYYMMDD-HHMMSS>/
```

- `<task-slug>` -- short, human-recognizable (`pr-review`, `cargo-debug`,
  `issue-21042`), not a generic name like `tmp` or `out` that collides across
  tasks.
- `<YYYYMMDD-HHMMSS>` -- the subfolder's own creation time, local or UTC
  (either is fine; be consistent within one environment). This is what makes
  staleness legible at a glance -- a folder from weeks ago is obviously safe
  to sweep, one from the current session obviously isn't.

Write every file for that task inside its own subfolder -- never back out to
the shared scratch root for individual files, which just recreates the exact
problem (loose, unstructured, undated content) one level down.

## Reuse within one task, not across tasks

A single task may write several files into its own subfolder across
multiple turns -- reuse the one subfolder already created for it rather than
making a new one per file or per turn. A genuinely new, unrelated task
(including a later session resuming different work) gets its own fresh
timestamped subfolder, even under the same root.

## Cleanup

Once a task's scratch output has been consumed -- posted, committed
elsewhere, or otherwise no longer needed -- remove that task's own subfolder
rather than leaving it indefinitely. If the content might still be useful
pending follow-up (e.g. draft PR comments that may need another round),
leave it and say so rather than silently deleting or silently leaving it
with no note; the timestamp in the folder name is exactly what lets a later
cleanup pass judge it safe to remove even without that note.

## Boundaries

- Applies to ad hoc files an agent creates **outside** a repository
  checkout. A file that belongs inside a repo (generated output committed to
  the repo, a test fixture, a build artifact the repo's own tooling expects
  at a repo-relative path) gets that normal path instead -- this skill is not
  about repo-internal file placement.
- Not a replacement for this session's own session-state folder when that is
  the more natural home for an artifact the session framework already
  manages (e.g. a context-handoff brief) -- use that mechanism's own
  conventions first when one already applies.
- Does not cover a package manager's or tool's own cache/data directories
  (those have their own configuration surface, e.g. `UV_CACHE_DIR`,
  `npm config get cache`) -- this skill is about an agent's own ad hoc
  working files, not a dependency's managed cache.
