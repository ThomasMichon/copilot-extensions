---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","customizationKind":"instructions","destination":".github/instructions/copilot-extensions-harness/cross-repo-debug-tracking.instructions.md","plugin":"copilot-extensions-harness@copilot-extensions","pluginVersion":"0.1.0-dev45","renderedBytes":2076,"schema":"copilot-extensions.instruction-projection","sourceId":"cross-repo-debug-tracking","template":"instructions/cross-repo-debug-tracking.instructions.md","templateBytes":1483,"templateSha256":"948fcb1fa052c62d0cc0c4f00a9e281b9822655773d9ca45dc191e4dbdbec1b3","version":1} -->

# Cross-repo debug tracking fallback

**Fallback policy `[owner: copilot-extensions-harness@0.1.0-dev44]`:** Before
concluding an `agent-*`, `context-handoff`, or other `copilot-extensions`
plugin's source is undocumented or filing an upstream bug against it, resolve
its actual checked-out location first (for example
`<agent-worktrees catalog argv[0]> related resolve <repo>` -- use the exact
`argv[0]` from the session command catalog, never a bare `agent-worktrees`
PATH lookup) -- an installed runtime under
`~/.agent-*` is never the only copy, and assuming otherwise produces a
false documentation-gap report. When a local symptom traces to an upstream
`ThomasMichon/copilot-extensions` issue or PR (or the reverse), cross-link
both directions: cite the upstream number in the local tracking issue and the
local tracking issue in the upstream one, so the trail between the observed
symptom and its root cause survives across sessions. Invoke the
`agent-worktrees:working-cross-repo` skill for the complete resolution and
cross-linking flow. After merging a PR to `ThomasMichon/copilot-extensions`,
if you are also operating in a harness/consumer repo this session,
immediately force-update installed plugins and re-run the projection sync
(`<agent-worktrees catalog argv[0]> update --force`, then the
`customizing-copilot:reviewing-customizations` projection sync) in that repo
before ending your turn -- do not wait for a later drift audit to catch it.
