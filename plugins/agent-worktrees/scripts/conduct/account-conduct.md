# GitHub account conduct

The active `gh` account is global, shared, and racy. Never run `gh auth switch`.
For ad-hoc calls, use
`agent-worktrees repos gh <owner/repo> -- <gh args>`; it injects the resolved
account per process. Agent-worktrees commands already do this.

For details, load `agent-worktrees:agent-worktrees-repos` or run
`agent-worktrees repos gh --help` / `agent-worktrees accounts --help`.
