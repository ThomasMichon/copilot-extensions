# GitHub account conduct (multi-account gh)

Multiple `gh` accounts may be logged in on this machine -- e.g. an
enterprise-managed (EMU) account and a personal account that own different
GitHub orgs/repos. Ad-hoc `gh` calls (`gh issue`, `gh label`, `gh api`,
GraphQL) use whichever account is **active** in the keyring; if the active
account can't access the target repo the call fails with a confusing
`GraphQL: Could not resolve to a Repository` -- while `gh pr` REST ops often
still work, so the symptom misleads.

`gh`'s **active account is global per-machine** (`hosts.yml`), shared across
every session -- so `gh auth switch` is **racy** on a shared box: a concurrent
switch by another session flips it under you. **Don't depend on / mutate the
active account for ad-hoc `gh`; inject the resolved account's token instead**
(side-effect-free, race-safe):

- **Preferred** -- let agent-worktrees resolve + inject for you:
  `agent-worktrees repos gh <owner/repo> -- <gh args>`
  (e.g. `agent-worktrees repos gh <owner/repo> -- issue create ...`).
- By hand (equivalent):
  `GH_TOKEN=$(gh auth token --user $(agent-worktrees repos account-for <owner/repo>)) gh <args>`
- `agent-worktrees` commands (`create-pr`, `pr-merge`, ...) already inject
  per-account tokens; `agent-worktrees accounts list` shows the catalog.

`gh auth switch --user <login>` is a **last-resort** fallback (interactive
human), not the mechanism agents should use -- it mutates shared global state.
