---
applyTo: "**"
---

# Agent Worktrees -- ownership-boundary disambiguation

Reflected into the repo so it loads even if `working-cross-repo`'s own skill
is never explicitly invoked.

## Before trusting a local tool/script index, confirm who actually owns it

A repo's own local tool index (e.g. a `docs/tools.md`, a `scripts/`
directory listing) only documents **that repo's own** scripts. A launch
script, binstub, or command that is actually shipped by an installed
**plugin** (or a related repo this one only consumes) is invisible to that
local index and easy to mistake for a local one -- confidently pointing at
the wrong owner is worse than not knowing, since it sends the next step to
a dead end or the wrong repo entirely.

Before acting on a path/script as if the current repo owns it:

1. Check whether the path is under a plugin's own payload root (an installed
   plugin's files, or this repo's own `plugins/*/` if you're inside
   copilot-extensions itself) rather than the consuming repo's own tree.
2. If it's plugin-owned, resolve its actual writable source checkout with
   `related resolve <name>` (the `working-cross-repo` skill) rather than
   editing the installed copy or assuming the consuming repo's local tool
   index covers it.
3. Only trust a local tool index for paths that are genuinely part of the
   consuming repo's own committed tree.

This closes the specific false-positive class of confidently attributing a
plugin-owned script to the wrong (local) repo instead of correctly naming
its owning plugin/repo.
