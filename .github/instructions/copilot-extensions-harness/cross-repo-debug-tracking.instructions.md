---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","customizationKind":"instructions","destination":".github/instructions/copilot-extensions-harness/cross-repo-debug-tracking.instructions.md","plugin":"copilot-extensions-harness@copilot-extensions","pluginVersion":"0.1.0-dev44","renderedBytes":1663,"schema":"copilot-extensions.instruction-projection","sourceId":"cross-repo-debug-tracking","template":"instructions/cross-repo-debug-tracking.instructions.md","templateBytes":1070,"templateSha256":"3f4fbf2a11725bbeab6b5ad1c37e843fc7c3d92b6a9fd40e4260acab2c0a7292","version":1} -->

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
cross-linking flow.
