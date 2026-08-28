# GitHub account conduct

Before contributing code or mutating an issue, PR, release, or settings,
identify target `owner/repo`; never infer its account from cwd or active `gh`.
Resolve with
`agent-worktrees repos account-for <owner/repo>` or
`agent-worktrees:agent-worktrees-repos`.

Run ordinary `gh` via
`agent-worktrees repos gh <owner/repo> -- <gh args>`; it resolves per process.
On ambient fallback, verify identity.

The active account is global/shared/racy: never switch ordinarily.
Account-scoped transports (for example CodeSpaces) and auth repair are
exceptions; follow their owning skill and restore the prior account.
