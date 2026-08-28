// agent-worktrees may need to initialize its runtime and scan session bindings
// before answering a query, especially during successor startup on Windows.
export const AGENT_WORKTREES_QUERY_TIMEOUT_MS = 15_000;
