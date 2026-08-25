# GitHub account conduct

The active `gh` account is global, shared, and racy. For ordinary calls, never
run `gh auth switch`; the documented auth-repair flow is the exception and must
restore the prior account. Use
`agent-worktrees repos gh <owner/repo> -- <gh args>`; it injects the resolved
account per process when a token is available. If it reports ambient-auth
fallback, verify identity before mutation. Agent-worktrees commands already
handle account resolution.

For details, load `agent-worktrees:agent-worktrees-repos` or run
`agent-worktrees repos gh --help` / `agent-worktrees accounts --help`.
