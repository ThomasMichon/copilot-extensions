# GitHub account conduct

Before code or issue/PR/release/settings mutations, identify target
`owner/repo` (`owner` for org calls); never infer its account from cwd or active
`gh`. Resolve via
`agent-worktrees repos account-for <owner|owner/repo>` or
`agent-worktrees:agent-worktrees-repos`.

Run ordinary `gh` through
`agent-worktrees repos gh <owner/repo> -- <gh args>`; it scopes identity per
process. Verify any ambient fallback.

Active account is global/shared/racy: never switch ordinarily. Account-scoped
transports (e.g. CodeSpaces) and auth repair are exceptions; follow their skill,
then restore the prior account.
