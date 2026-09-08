# Session-Scoped Dynamic Guidance

**Serves:** Vision `harness-guidance` (feature `concise-context-kernel`;
behaviors `resilient-safety-boundary`, `ambient-delivery-fails-open`).

## Problem

`sessionStart` `additionalContext` aggregation across multiple hooks is
**empirically unreliable** in current Copilot CLI releases. This is a distinct,
harder failure than the
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
- The observed `sessionStart` hook input included at least
  `{"sessionId", "timestamp", "cwd", "source", "initialPrompt"}` in this test;
  treat this as a lower bound, not an exhaustive or closed schema -- future or
  other launch paths may add fields.
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
ordinary static fail-safe projection through the existing declarative
projection mechanism (`instruction-projections.json` +
`customizing-copilot:reviewing-customizations` sync/scan). The projected
file's entire body is a minimal, literal,
non-interpolated pointer:

```markdown
---
applyTo: "**"
---

At the start of this session, resolve your current Copilot session-state folder
using the session identifier exposed as `COPILOT_AGENT_SESSION_ID`, or any
equivalent mechanism available to you, and read the file at
`instructions/<plugin>/<topic>.instructions.md` inside that folder, if it
exists. Treat its contents as authoritative for this session. If the file does
not exist, proceed without it -- do not treat its absence as an error.
```

This file never embeds a session ID, host path, or other live value -- it
instructs the *agent* to resolve one at read time, which is not interpolation
and satisfies the existing static-instruction constraints. It fails open by
design: a missing dynamic file is explicitly a no-op, never a blocker.

### 2. Dynamic half: a session-folder file, written as a side effect

The plugin's `sessionStart` hook computes the dynamic content and writes it as
a **pure side effect** -- never through `additionalContext` -- to:

```text
~/.copilot/session-state/<sessionId>/instructions/<plugin>/<topic>.instructions.md
```

using the `sessionId` supplied in the hook's own stdin payload. The write:

- is atomic (write to a temp file in the same directory, then rename);
- validates `sessionId` against a general safe-identifier pattern (non-empty,
  restricted to `[A-Za-z0-9._-]`, bounded length -- not UUID-only) and rejects
  a missing or malformed value rather than guessing;
- is contained beneath the exact session's `session-state` root -- no
  symlink/reparse escape, no path outside `instructions/<plugin>/`;
- uses the plugin's own topic-scoped subpath so two plugins never collide;
- overwrites deterministically on every `sessionStart` invocation (fresh or
  resume), so the content reflects the current live state rather than a stale
  snapshot from an earlier launch;
- stays within a bounded size (recommend the same 4 KiB per-file guidance as
  the static projection budget) -- this is a targeted per-session fact sheet,
  not a spill dump.

The hook emits exactly `{}`. The static pointer plus the agent's own file read
is the delivery path. Direct plugin-owned `additionalContext` is activated only
after the supported Copilot CLI version floor proves native composition across
fresh, resume, non-interactive, and ACP paths. That future activation replaces
the compatibility path deliberately; it is not a redundant second mechanism
enabled in advance.

### 2a. Only the computed part belongs in the session-folder file

A plugin's per-session content is rarely *all* dynamic. Splitting it wrong --
writing explainer prose, field-meaning documentation, or "run this command for
more" boilerplate into the session-folder file alongside the genuinely
computed values -- silently reintroduces the cost this pattern exists to
avoid: identical static text gets regenerated and rewritten on every single
session start, burns budget that should go to live facts, and (worse) can't
be reviewed or diffed as checked-in guidance.

The rule: the session-folder file carries **only** the values that had to be
computed this session -- resolved paths, current bindings, a live command's
`argv`, a config-derived summary. Everything else -- what a field means, why
it's bounded/curated rather than exhaustive, and which live commands to run
for the complete picture -- is a second, ordinary static projection (its own
`instructions/<topic>.instructions.md` template plus an
`instruction-projections.json` entry), checked in and reviewed like any other
static fail-safe. Both files load independently and automatically; there is no
ordering dependency between them, and either can be absent without breaking
the other.

`agent-worktrees` is the reference example: its session-folder file carries
only the current checkout's `Checkout:`/`State:`/`Related:` facts (resolved
this session, by reading config and walking the related-repo registry); the
static, checked-in `worktree-context-guide.instructions.md` explains what
those fields mean, that `Related:` is a deliberately bounded/curated subset,
and which `agent-worktrees` commands to run live for the complete picture.
None of that explainer text is regenerated per session.

> **Known follow-up, not yet applied everywhere:** the session command-catalog
> emitted by every `libs/payload-invocation`-based plugin still bundles a
> static explainer ("Invoke the exact `argv` below...") into the same
> `additionalContext` string as the computed catalog JSON, so that boilerplate
> is currently rewritten into every session-folder file that includes a
> command catalog. Splitting it requires care: at least one consumer
> (`agent-worktrees`' `hook_client.py`) uses the explainer heading as an
> internal de-duplication marker between its registration and catalog
> contexts, so the fix isn't a pure deletion. Tracked for a dedicated pass.

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

### 4. Current and future dynamic convergence

The current reliable delivery path is the checked-in static pointer plus the
exact-session guidance file above. A custom aggregation, rendezvous, cache, or
spill authority is not part of this pattern and must not be recreated beside
the host. Native host-composed `additionalContext` is the only future dynamic
convergence path: it may become preferred after upstream behavior is proven at
the supported version floor, but no current guidance may depend on its arrival.
Until then, every plugin delivering guidance a harness depends on must implement
the static-pointer plus session-folder-file pattern and emit no model context
from its writer hook.

## Rationale

Moving delivery from host-mediated hook-output composition (observed unreliable)
to agent-initiated, statically-instructed file reads (observed reliable)
sidesteps the exact mechanism that is failing, without waiting on an upstream
runtime fix. It also composes cleanly with existing `applyTo`-scoped instruction
file support and the projection sync/scan tooling, so no new distribution or
review mechanism is needed -- only a documented content convention.

## Exemplars

- [`agent-worktrees`](../../plugins/agent-worktrees/) projects
  `instructions/session-guidance.instructions.md` and writes the matching
  session-scoped file from its payload-local `sessionStart` hook client. The
  session-folder file combines the attributable command catalog with the
  current worktree/topology facts (`Checkout:`/`State:`/`Related:`); the
  static, checked-in `instructions/worktree-context-guide.instructions.md`
  (its own separate projection) carries the field-meaning explainer and the
  live commands to run for the complete picture -- see §2a above.
- Runtime command plugins such as
  [`agent-bridge`](../../plugins/agent-bridge/),
  [`agent-codespaces`](../../plugins/agent-codespaces/),
  [`agent-containers`](../../plugins/agent-containers/),
  [`agent-dispatch`](../../plugins/agent-dispatch/),
  [`agent-index`](../../plugins/agent-index/),
  [`agent-logger`](../../plugins/agent-logger/),
  [`agent-machines`](../../plugins/agent-machines/),
  [`agent-mcp`](../../plugins/agent-mcp/),
  [`agent-ssh`](../../plugins/agent-ssh/), and
  [`agent-vault`](../../plugins/agent-vault/) use a side-effect-only writer
  hook backed by a portable `write_session_guidance.py`. Each invokes only its
  own emitters and writes only its own session-scoped file.
- [`ai-attribution`](../../plugins/ai-attribution/) and
  [`context-handoff`](../../plugins/context-handoff/) use the same writer shape
  for repository-aware publication policy and the continuity contract.
- Static-only plugins such as
  [`delegation-guidance`](../../plugins/delegation-guidance/) and
  [`copilot-extensions-harness`](../../plugins/copilot-extensions-harness/)
  project reviewed fallback policy without a dynamic writer.

## See Also

- Vision: `visions/harness-guidance/README.md`
- [github/copilot-cli#3589](https://github.com/github/copilot-cli/issues/3589)
