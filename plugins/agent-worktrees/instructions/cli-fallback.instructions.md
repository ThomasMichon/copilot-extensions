---
applyTo: "**"
---

# Agent Worktrees -- CLI/GraphQL-call fallback guidance

This file is reflected directly into the consuming repo's own source tree, so
it loads on every session regardless of whether `agent-worktrees` registered
correctly this session.

## If a `gh`-family command (or `repos gh`, `create-pr`, `pr-merge`, label
## operations) fails with a GraphQL error

Read the literal error text before retrying -- these are distinct failure
classes, not one generic "GraphQL broke":

- **Wrong account/scope** -- the resolved identity for this repo lacks the
  needed permission. Confirm which account was actually used:
  `agent-worktrees repos account-for <owner/repo>`, then check that
  account's own auth: `gh auth status --hostname <host>`. Never switch the
  global active `gh` account to work around this -- the per-repo account
  resolution exists precisely so you don't have to.
- **Rate limit / secondary rate limit** (message mentions "rate limit" or
  "abuse detection") -- back off; a rapid retry loop makes it worse, not
  better. Wait and retry once, not repeatedly.
- **Permission/scope error** ("Resource not accessible", 403/404 on a node
  the account should see) -- the resolved account is missing a collaborator
  grant on that repo; this is a repo-access problem, not a token problem.
- **Malformed query / unexpected schema shape** -- if the error names a field
  or type this tool's own GraphQL query doesn't expect, that is a bug in
  `agent-worktrees` itself (a GitHub schema change), not a local
  misconfiguration -- file an issue rather than working around it locally.

## If a plugin's own CLI command is not found ("command not found" / "not
## recognized as an internal or external command")

This means the binstub is not (yet, or no longer) on `PATH` for **this**
process -- it does not mean the plugin failed to install. Before assuming a
broken install:

- Confirm you're not in a spawned/nested process that didn't inherit the
  parent session's `PATH` (a background/detached shell, a sub-agent, or a
  container) -- re-resolve or re-source the environment there rather than
  reinstalling.
- If this is genuinely the first time in this session/shell, the plugin's own
  `sessionStart` hook stamps the binstub onto `PATH` on first run; ask for
  "set up agent-worktrees" (or the relevant plugin) to bootstrap it rather
  than guessing at a manual PATH edit.
- If the source checkout (not just the runtime) is what you actually need,
  that is a different problem -- resolve it via `related resolve <name>` per
  the `working-cross-repo` skill, not this PATH fallback.
