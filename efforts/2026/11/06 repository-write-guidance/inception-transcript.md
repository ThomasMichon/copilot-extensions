# Repository Write Guidance - Inception

[Canonical effort](README.md)

The following operator statements are preserved verbatim. No private
identifiers required redaction.

## Guidance-first premise

> Admittedly every form of write is going to be hard to catch. The purpose of the guard is to "nudge" agents to align with the recommended reules of engagement with a given repo: read-only, create a worktree, elevate, or delegate to an agent managing the repo. If the main agent is trying to make a targeted fix, just read some values, assist with hygience and tidiness, etc, it may write the "break-glass" flag which bypasses the hook subsequently for a time bound. The import part is just to inject the guidance so the agent is aware of the rules, not hard-block. We have to "reject" on the first call and provide the guidance and workaround. We could nearly get away with just taking a list of all known registered repos and their modes, and regex-sweeping the payload, if it's fast enough.
> It's too bad we can't use a process-write hook or on-demand sandbox here, which would be more comprehensive.

## Execution request

> Correct. Drive an effort to build this out to merge and resolution

## Session marker suggestion

> Since agent-worktrees creates a worktree marker a session folder upfront at the start of the session, presence of not of that folder in the session folder is probably the fastest check we have for whether the current session is managed by agent-worktes

## Agent interpretation

Use the existing native session association rather than adding hot-path
discovery when it establishes the current workspace's managed binding.
Binding differs from controlling another worktree; a generic session
directory or controller-only sidecar is not itself that association.
Prepared roots and common write-form matching can deliver the nudge cheaply
without promising exhaustive write interception. These are implementation
recommendations, not new execution or deployment authority.
