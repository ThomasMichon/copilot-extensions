# Session-Scoped Dynamic Guidance

**Serves:** Vision `harness-guidance` (features `concise-context-kernel`,
`resilient-safety-boundary`; behavior `ambient-delivery-fails-open`).

## Problem

`sessionStart` `additionalContext` aggregation across multiple hooks -- whether
raw competing hooks or the coordinated `context-injection` authority pattern in
[`context-injection.md`](context-injection.md) -- is **empirically unreliable**
in current Copilot CLI releases. This is a distinct, harder failure than the
previously tracked
[github/copilot-cli#3589](https://github.com/github/copilot-cli/issues/3589)
("only the last hook's `additionalContext` survives"): repeated real testing
shows sessions where **none** of several concurrently registered
`additionalContext` contributors reach the model, even though the hooks
themselves demonstrably executed. A harness cannot correctly depend on
`additionalContext` delivery today, and waiting for an upstream fix is not
acceptable for guidance that must reach the agent now.

### Observed evidence

A clean-room test (disposable container, real Copilot CLI session, blind
hidden-token methodology) registered six `sessionStart` hooks emitting distinct
`additionalContext` tokens (some deliberately slow) alongside one hook that
performed only a file-write side effect. Across repeated runs:

- **Zero** of the six `additionalContext` tokens ever reached the model.
- The file-write side effect **always** completed successfully once the
  repository was trusted, proven by inspecting the hook's own stdin payload
  and the file it produced.
- The confirmed `sessionStart` hook input shape is
  `{"sessionId", "timestamp", "cwd", "source", "initialPrompt"}`.
- **Repository-level (non-plugin) `.github/hooks/*.json` hooks silently do not
  run at all** in a non-interactive launch (`-p`) against an untrusted folder --
  no error, no diagnostic, just complete absence. This is a sharper trust gate
  than previously documented and applies independently of the additionalContext
  finding above.
- A checked-in `.github/instructions/**/*.instructions.md` file (frontmatter
  `applyTo`) reached the model reliably in every run, static or not.
- A static instruction directing the agent to resolve its own session folder
  (via the `COPILOT_AGENT_SESSION_ID` environment variable and the documented
  `~/.copilot/session-state/<sessionId>/` convention) and read a named file
  inside it was **followed correctly** by the agent, which located and reported
  the dynamically written content.

## Standard approach

Prefer **file-based delivery the agent is instructed to read** over
**hook-emitted `additionalContext` the host must aggregate**. This inverts
which side does the unreliable work: composition across independent hooks is
where delivery is observed to fail; a single agent-initiated file read,
directed by an always-loaded static instruction, is observed to succeed.

### 1. Static half: a checked-in pointer projection

Every plugin that needs to deliver per-session dynamic guidance ships an
ordinary static fail-safe projection through the existing
[declarative projection mechanism](context-injection.md#project-plugin-owned-static-fail-safes-declaratively)
(`instruction-projections.json` + `customizing-copilot:reviewing-customizations`
sync/scan). The projected file's entire body is a minimal, literal,
non-interpolated pointer:

```markdown
---
applyTo: "**"
---

At the start of this session, resolve your current session-state folder (for
example via the `COPILOT_AGENT_SESSION_ID` environment variable combined with
the `~/.copilot/session-state/<id>/` convention, or any equivalent mechanism
available to you) and read the file at
`instructions/<plugin>/<topic>.instructions.md` inside that folder, if it
exists. Treat its contents as authoritative for this session. If the file does
not exist, proceed without it -- do not treat its absence as an error.
```

This file never embeds a session ID, host path, or other live value -- it
instructs the *agent* to resolve one at read time, which is not interpolation
and satisfies the existing static-instruction constraints in
[`context-injection.md`](context-injection.md). It fails open by design: a
missing dynamic file is explicitly a no-op, never a blocker.

### 2. Dynamic half: a session-folder file, written as a side effect

The plugin's `sessionStart` hook computes the dynamic content and writes it as
a **pure side effect** -- never through `additionalContext` -- to:

```text
~/.copilot/session-state/<sessionId>/instructions/<plugin>/<topic>.instructions.md
```

using the `sessionId` supplied in the hook's own stdin payload. The write:

- is atomic (write to a temp file in the same directory, then rename);
- validates `sessionId` against the documented UUID shape and rejects a
  missing or malformed value rather than guessing;
- is contained beneath the exact session's `session-state` root -- no
  symlink/reparse escape, no path outside `instructions/<plugin>/`;
- uses the plugin's own topic-scoped subpath so two plugins never collide;
- overwrites deterministically on every `sessionStart` invocation (fresh or
  resume), so the content reflects the current live state rather than a stale
  snapshot from an earlier launch;
- stays within a bounded size (recommend the same 4 KiB per-file guidance as
  the static projection budget) -- this is a targeted per-session fact sheet,
  not a spill dump.

The hook's own `additionalContext` output, if any, is **not required and not
relied upon** for this content to reach the model -- the static pointer plus
the agent's own file read is the delivery path. A plugin **may** still also
emit an `additionalContext` best-effort value as a redundant supplementary
attempt (in case a future Copilot CLI release fixes composition), but must
never treat its arrival as guaranteed, and every path this pattern protects
must work correctly with `additionalContext` completely absent.

### 3. Folder trust is a hard prerequisite for repository-level hooks

A repository-level `.github/hooks/*.json` hook (as opposed to an installed
plugin's hooks, which are trusted at install/enable time) requires the working
directory to already be in the host's persisted `trustedFolders` set. Outside
an interactive trust prompt -- notably `-p`/autopilot launches -- an untrusted
folder's local hooks are **silently skipped**, with no diagnostic. A harness
that provisions worktrees or launch directories must ensure the folder is
trusted (e.g. at worktree-creation time) before depending on this pattern, and
should treat "the dynamic file never appears" as a trust-gap symptom to check
first, not a delivery-mechanism failure.

### 4. Status of `additionalContext` aggregation

The `context-injection` authority in [`context-injection.md`](context-injection.md)
remains a documented, tested engine and is not being removed -- some launch
paths and some low-stakes advisory content may still benefit from a best-effort
`additionalContext` attempt. But it must not be treated as the **primary or
sole** delivery channel for guidance a harness actually depends on. Until
upstream Copilot CLI lands a fix for `sessionStart` `additionalContext`
composition (`#3589` and the complete-loss mode this document adds evidence
for) and that fix reaches the supported version floor, **every plugin
delivering guidance a harness depends on must implement the static-pointer +
session-folder-file pattern above**, independent of whatever `additionalContext`
contribution it also attempts.

## Rationale

Moving the "many independent contributors, one result" problem from
host-mediated hook-output composition (observed unreliable) to agent-initiated,
statically-instructed file reads (observed reliable) sidesteps the exact
mechanism that is failing, without waiting on an upstream runtime fix. It also
composes cleanly with existing `applyTo`-scoped instruction file support and
the already-shipped projection sync/scan tooling, so no new distribution or
review mechanism is needed -- only a documented content convention.

## Exemplars

None yet -- this pattern is newly proven and not yet adopted by a shipped
plugin. The first migrating plugin becomes the reference exemplar; update this
section when one lands.

## See Also

- [`context-injection.md`](context-injection.md) -- the aggregation authority
  this pattern now supersedes as the primary delivery path, and the static
  fail-safe projection mechanism this pattern reuses for its static half.
- Vision: `visions/harness-guidance/README.md`
- [github/copilot-cli#3589](https://github.com/github/copilot-cli/issues/3589)
