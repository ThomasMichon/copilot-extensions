// agent-worktrees may need to initialize its runtime and scan session bindings
// before answering a query, especially during successor startup on Windows or
// under real machine load (observed 24-27s completions under load average
// 9-10 on a busy host -- aperture-labs#6724). 45s keeps real margin above
// that while still failing fast on a genuinely hung/missing binary. Override
// with AGENT_WORKTREES_QUERY_TIMEOUT_MS for unusual hosts.
const envOverride = Number(process.env.AGENT_WORKTREES_QUERY_TIMEOUT_MS);
export const AGENT_WORKTREES_QUERY_TIMEOUT_MS =
  Number.isFinite(envOverride) && envOverride > 0 ? envOverride : 45_000;
