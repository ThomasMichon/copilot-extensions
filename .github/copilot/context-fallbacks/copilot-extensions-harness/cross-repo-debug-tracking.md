---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","bodySha256":"09811db19a551248d8a4e7838ee9be86b9e792665dc82891f0a1aaa897da1ffb","customizationKind":"instructions","deliveryKind":"body","destination":".github/instructions/copilot-extensions-harness/cross-repo-debug-tracking.instructions.md","plugin":"copilot-extensions-harness@copilot-extensions","pluginVersion":"0.1.14-dev1","renderedBytes":3016,"schema":"copilot-extensions.instruction-projection","sourceId":"cross-repo-debug-tracking","template":"instructions/cross-repo-debug-tracking.instructions.md","templateBytes":2196,"templateSha256":"1e36d1ad73a5d0b2b22d656cb3953518272a4bc97147478bbd8d7545aebc3eee","version":1} -->


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
cross-linking flow. A merge to `dev` does not make the change live: the release
pipeline must first promote it to `main`, then an authorized local
`<repo> update` installs that promoted release. Updating immediately after a
`dev` merge cannot install a change that has not been promoted.
Manual instruction sync is not required after a merge or update.
Keep permissionless local rendering current with the installed
payload and preserve the reviewed offline fallback. Block missing verified
guidance delivery, but keep valid stale guidance advisory; foreign ownership,
unsafe paths and missing/corrupt locked
artifacts remain blocking. Before promotion, report merged/pending release.
After promotion but before local rollout, report released/pending rollout.
Neither state means live or deployed. Payload/runtime updates still require explicit rollout
authorization and the target's required safety/permission gates; do not
create a parallel scheduler or silently claim that a merge updated a running
system.

<!-- copilot-guidance-body-end:v1 {"bindingSha256":"47519217d0dde8aa1441c06f5e61bce9383eb03edf834bf68bbece7b18b6af64"} -->
