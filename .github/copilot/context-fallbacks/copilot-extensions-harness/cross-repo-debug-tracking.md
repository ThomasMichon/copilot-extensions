---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","bodySha256":"de89e06d89db3bb568c50be4a7aafd56024736c8d8b2be6c0386ca8879b37dda","customizationKind":"instructions","deliveryKind":"body","destination":".github/instructions/copilot-extensions-harness/cross-repo-debug-tracking.instructions.md","plugin":"copilot-extensions-harness@copilot-extensions","pluginVersion":"0.1.13-dev1","renderedBytes":2867,"schema":"copilot-extensions.instruction-projection","sourceId":"cross-repo-debug-tracking","template":"instructions/cross-repo-debug-tracking.instructions.md","templateBytes":2047,"templateSha256":"0f678caacab324ccb58ce1ecfee01109852bcfc73155f7bd3cd186f3f17f4474","version":1} -->


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
`ThomasMichon/copilot-extensions` issue or PR (or the reverse), preserve
public-public traceability with cross-links in both directions only when both
trackers and artifacts are public. A private downstream tracker may link to
public upstream work, but public upstream artifacts must never receive private
links, IDs or context. Keep the private symptom/rationale downstream and make
the public report self-contained. Invoke the
`agent-worktrees:working-cross-repo` skill for the complete resolution and
cross-linking flow. After an upstream merge, let the consuming harness's
adopted, consented maintenance own checked-in projection refresh (normally
once daily), rather than forcing a synchronous resync to finish every coding
session. Keep permissionless local rendering current with the installed
payload and preserve the reviewed offline fallback. Block missing verified
guidance delivery, but keep valid stale guidance advisory; foreign ownership,
unsafe paths and missing/corrupt locked
artifacts remain blocking. If maintenance or rollout has not run, report
primed/pending, not deployed, and retain the named maintenance/deployment
obligation. Payload/runtime updates still require explicit rollout
authorization and the target's required safety/permission gates; do not
create a parallel scheduler or silently claim that a merge updated a running
system.

<!-- copilot-guidance-body-end:v1 {"bindingSha256":"dc74b4e6ed637ef5fe80b9ba31f423352456be275e0edb60f667c8cc32b64cc4"} -->
