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
   own machine-local config declaring a preferred scratch root, the same
   way it might declare a preferred source-checkout root) -- prefer a
   mechanism already in scope over inventing a new one. This is how an
   operator configures a durable *default* scratch root, instead of relying
   on an ad hoc per-invocation environment variable every time.
3. **The operating system's preferred/standard temporary-folder system**
   (what `$env:TEMP` resolves to on Windows, `${TMPDIR:-/tmp}` on POSIX) --
   the default when neither of the above is configured. Use a private,
   owner-only `agent-scratch` subfolder of that location (never loose at
   the temp root either, for the same reason as above): `$env:TEMP\agent-scratch`
   on Windows is already per-user and needs no further hardening. On
   **POSIX**, `/tmp` (and any `$TMPDIR` pointing at a shared,
   world-writable location) is a multi-user directory -- a fixed,
   predictable name like `/tmp/agent-scratch` there can be pre-created by
   another local user or replaced with a symlink to redirect writes, and
   files created under a permissive umask can be readable by other local
   users. On POSIX, use a **private per-user** path instead of a bare
   shared name -- e.g. `${TMPDIR:-/tmp}/agent-scratch-$(id -u)` -- and
   before writing into it: create it with owner-only permissions (`mkdir -m
   700`, or `chmod 700` immediately after creation) if it doesn't already
   exist, and if it does already exist, verify it is a real directory (not
   a symlink) owned by the current user with mode `700` before trusting
   it; refuse to use it and fall back to a fresh, uniquely-named directory
   (e.g. via `mktemp -d`) if that check fails.

Fall through silently -- don't ask the operator to configure something just
to write one scratch file; only escalate if even the operating system's
temporary-folder system isn't writable.

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

Scratch output is the agent's own responsibility to clear, not something a
later sweep is expected to discover and judge. At the end of a session (or
once a task's scratch output has been consumed -- posted, committed
elsewhere, or otherwise no longer needed), remove that task's own subfolder
rather than leaving it indefinitely. If the content might still be useful
pending follow-up (e.g. draft PR comments that may need another round),
leave it and say so rather than silently deleting or silently leaving it
with no note; the timestamp in the folder name is exactly what lets a later
cleanup pass judge it safe to remove even without that note. Scratch space
is never the right home for anything meant to last: a durable artifact --
a plan, a decision record, a finding worth keeping -- gets filed through
whatever mechanism the operator's own environment already defines for that
(an effort, a tracked issue, a committed doc), or by asking the operator
on demand when no such mechanism is established; it does not get left
behind in a scratch subfolder because the session ended.

## Never stage checkouts, builds, or sensitive data in session state

A session-state folder (wherever the active session/extension framework
keeps its own managed state) is not a general-purpose scratch root. Do not
clone or check out a repository into it, run a build inside it, or extract
PII, secrets, or other sensitive data into it -- those belong in an
approved scratch location (resolved above) instead. If a task genuinely
needs an ongoing, reusable checkout of another repository, that is a sign
it should become a tracked, registered repository rather than a disposable
scratch artifact: ask the operator to register it as a related repository
checked out under their normal source-checkout root, where it can later be
promoted to a full worktree project if the work continues -- rather than
quietly growing a throwaway checkout inside session state.

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
