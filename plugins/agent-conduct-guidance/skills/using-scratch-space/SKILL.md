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
bake in a specific path. Resolve **the scratch root** -- the single directory
per-task subfolders are created directly under (see the next section) -- in
this order, first match wins. **Whichever step resolves it, verify its
privacy before trusting it with anything sensitive** (a draft containing
PII, secrets, or other sensitive output): an explicit override or a
machine-local default is just as capable of pointing at a shared,
symlinked, or overly permissive directory as the OS-temp fallback is.

1. **`AGENT_SCRATCH_ROOT` environment variable**, if set -- an operator's or
   session's explicit choice. **The value itself is the scratch root** --
   create it as-is if it doesn't exist yet (don't second-guess its
   location, and don't add any extra subfolder beneath it), but still
   verify its ownership and effective permissions before writing anything
   sensitive into it, using the same checks described under step 3; an
   operator-set variable is not automatically private.
2. **A machine-local convention the current context already resolves**, if
   one is available and documented for this environment (e.g. a harness's
   own machine-local config declaring a preferred scratch root, the same
   way it might declare a preferred source-checkout root) -- prefer a
   mechanism already in scope over inventing a new one. **The configured
   path itself is the scratch root** here too, the same as step 1; this is
   how an operator configures a durable *default*, instead of relying on an
   ad hoc per-invocation environment variable every time. Apply the same
   privacy verification before trusting it with sensitive content.
3. **The operating system's preferred/standard temporary-folder system**
   (what `$env:TEMP` resolves to on Windows, `${TMPDIR:-/tmp}` on POSIX) --
   the default when neither of the above is configured. Unlike steps 1-2,
   this location is shared with every other process on the machine, so
   **the scratch root here is a dedicated private subfolder of it**
   (`agent-scratch`-style, detailed below), never the bare temp path
   itself and never loose at the temp root.

## Harden the scratch root once; per-task subfolders inherit it

Apply the privacy hardening below to **the scratch root itself** -- the
exact directory resolved above (the `AGENT_SCRATCH_ROOT` value, the
machine-configured path, or the dedicated private subfolder under the OS
temp directory) -- not to every per-task subfolder created under it later.
A correctly-hardened root already keeps every subfolder beneath it equally
private, and repeating per-subfolder ACL/mode changes after creation is
exactly the non-atomic, racy pattern this section avoids.

- **POSIX**: create the root with `os.mkdir(path, 0o700)` (or the shell
  equivalent, `mkdir -m 700`) directly -- **do not** touch the process
  umask to do this. Clearing or changing `umask` is process-wide, mutable
  state; doing it even briefly races every other thread or
  concurrently-running code in the same process that creates files during
  that window, trading one race for a worse one. It is also unnecessary:
  umask can only ever *remove* permission bits from the requested mode, it
  can never add bits beyond what was requested, so asking for `0o700`
  directly already caps the result at `0o700` or tighter regardless of the
  ambient umask. For the OS-temp fallback specifically, use a private,
  per-user name -- e.g. `${TMPDIR:-/tmp}/agent-scratch-$(id -u)` (not a
  bare shared `/tmp/agent-scratch`). If the path already exists, verify
  before trusting it: a real directory (not a symlink), owned by the
  current user, with mode exactly `0700`. Treat a failed check, or an
  `EEXIST` creation race against another process, as "not trustworthy" and
  fall back to a fresh unpredictable directory via `mkdtemp` instead of
  reusing it. Because the root is mode `0700`, only its owner can even
  traverse into it -- every subfolder and file created under it is
  contained by that single check, with nothing further required per task.
- **Windows**: `%TEMP%` is conventionally per-user but its ACL is not
  guaranteed private by the platform and the standard temp-path APIs
  don't validate it -- don't assume privacy by default. Create the root
  (for the OS-temp fallback, the dedicated `agent-scratch` subfolder) with
  its restrictive ACL set **at creation**, not as a follow-up step, so
  there is no window where it exists with an inherited, broader ACL. An
  explicit allow-rule alone is not enough: a `DirectorySecurity` carrying
  only an added rule can still combine with inherited entries from the
  parent (`%TEMP%`), leaving the folder readable by more than its owner.
  **Protect the DACL so it stops inheriting from the parent** (.NET's
  `DirectorySecurity.SetAccessRuleProtection($true, $false)` -- protect
  the DACL, discard inherited rules), *then* add the explicit rule
  granting full control only to the current user with **both**
  `ContainerInherit` and `ObjectInherit` flags (`ContainerInherit` alone
  only propagates to child directories; without `ObjectInherit` too,
  files created in per-task subfolders would fall back to a default
  token DACL instead of the root's owner-only rule) so every descendant
  file and folder propagates it, and pass that fully-built
  `DirectorySecurity` to the creation call itself (e.g. PowerShell's
  `[System.IO.Directory]::CreateDirectory(path, $directorySecurity)`)
  rather than `New-Item` followed by a separate `icacls` call. If the
  folder already exists, verify its effective ACL grants access only to
  the current user (and Administrators) before trusting it (e.g.
  `icacls <path>` showing no inherited broad grant); if that check fails,
  don't reuse it. Because the root's ACL is both protected (not inherited)
  and set to propagate to children at creation, every per-task subfolder
  created under it inherits the same restriction automatically -- no
  further per-subfolder ACL step is needed.
- **Either platform**: if privacy can't be verified or established for the
  scratch root, don't write sensitive data under it -- fall back to an
  operator-configured default (step 2) or escalate to the operator
  instead.

Fall through silently -- don't ask the operator to configure something just
to write one scratch file; only escalate if even the operating system's
temporary-folder system isn't writable, or (for sensitive content) its
privacy can't be verified or established.

## One timestamped subfolder per task

Within the resolved root, create a single subfolder per distinct task before
writing anything into it:

```text
<root>/<task-slug>-<YYYYMMDD-HHMMSS>/
```

- `<task-slug>` -- short, human-recognizable (`pr-review`, `cargo-debug`,
  `issue-21042`), not a generic name like `tmp` or `out` that collides
  across tasks. Keep it to a **portable, platform-safe grammar**: lowercase
  ASCII letters, digits, and single hyphens only (`[a-z0-9]+(-[a-z0-9]+)*`),
  no path separators, no leading/trailing/doubled hyphens, no bare `.`/`..`
  segments, and not a Windows reserved device name (`CON`, `PRN`, `AUX`,
  `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`, case-insensitive) or a name that
  differs from one only by an extension. Sanitize a natural task
  description into this grammar (lowercase it, replace any disallowed
  character with `-`, collapse repeats, trim leading/trailing hyphens,
  cap the length around 40 characters) rather than using it verbatim; if
  nothing recognizable survives sanitization, fall back to a short generic
  word (e.g. `task`) and rely on the uniqueness suffix below to disambiguate.
- `<YYYYMMDD-HHMMSS>` -- the subfolder's own creation time, local or UTC
  (either is fine; be consistent within one environment). This makes
  staleness legible at a glance -- a folder from weeks ago is an obvious
  *candidate* to review for cleanup -- but age alone never proves a folder
  is safe to delete outright; see Cleanup below.

**Create the subfolder with an exclusive/atomic operation** (one that fails
if the exact name already exists -- e.g. a plain `mkdir` without a
`-p`/`-Force`/"ignore if exists" flag) rather than checking for existence
first and creating afterward, which races against a concurrent task.
Second-resolution timestamps do not guarantee uniqueness by themselves: two
concurrent tasks that happen to share a slug and the same second (e.g. two
`pr-review` tasks started together) would otherwise resolve to, and write
into, the same folder. If the exclusive create fails because the name is
already taken, treat that as a genuine collision -- not the "already
exists and was set up by an earlier turn of this same task" case covered
under Reuse below, which only applies to a folder this task itself
created -- and retry with a short, unpredictable suffix appended (e.g. a
few random hex characters) until an exclusive create succeeds, so unrelated
tasks' artifacts never land in the same folder.

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
with no note. The folder-name timestamp is only a staleness *signal*, not
proof of safety to delete by itself -- a task can legitimately stay active
for weeks, or intentionally leave scratch output pending a later round, as
above. Before anyone (or anything) sweeps a folder based on its age, they
need a positive signal that the task is actually done or abandoned (the
owning task/session confirms completion, or an explicit note says it's safe
to remove) -- age by itself is never sufficient to conclude a folder is
inactive. Scratch space is never the right home for anything meant to
last: a durable artifact -- a plan, a decision record, a finding worth
keeping -- gets filed through whatever mechanism the operator's own
environment already defines for that (an effort, a tracked issue, a
committed doc), or by asking the operator on demand when no such mechanism
is established; it does not get left behind in a scratch subfolder because
the session ended.

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
