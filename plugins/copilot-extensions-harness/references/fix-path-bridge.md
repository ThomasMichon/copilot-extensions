# The fix-path bridge (a review flagged an external plugin)

When `reviewing-customizations` (the `customizing-copilot` plugin) reviews a
consumer harness with `--from-settings`, a **trigger collision** — or any
finding — that lands on a plugin from **this** suite is *outside that repo's
control*: it can't be fixed in the consumer repo, only here. This is the **fix
path** that review points at (via the `<repo>-harness → contributing-to-<repo>`
bridge). When you arrive here from such a finding:

1. **Confirm it's a copilot-extensions plugin.** The review tags each collision
   owner `skill [marketplace/plugin]`; a `[copilot-extensions/<plugin>]` origin
   (or a `source:` of `github.com/ThomasMichon/copilot-extensions`) is ours.
2. **Reproduce against the repo source, never the installed payload.** Resolve
   the anchor and read the offending skill/agent in `plugins/<plugin>/…` — do
   **not** inspect or edit `~/.copilot/installed-plugins/…` (overwritten on
   update).
3. **Fix it through the normal flow above** — worktree, edit, **bump the
   version**, gates, PR/self-merge, deploy. A trigger collision is usually
   resolved by sharpening or de-duplicating the phrase in the owning skill's
   `description` / `Trigger phrases include:` list.
4. **Can't/shouldn't fix it now?** File a GitHub issue on
   `ThomasMichon/copilot-extensions` describing the collision (both skills, the
   shared phrase) in generic, world-readable terms (see the
   `contributing-to-copilot-extensions` skill's *Coordinating concurrent
   drivers* section) so it's tracked for a maintainer.

The consumer repo's own options (an in-repo authority-override skill that
reclaims the phrase, or disabling the plugin there) live on the *consumer* side
and are documented by `customizing-copilot:reviewing-customizations`; this
bridge covers the upstream half — landing the real fix in the plugin.
