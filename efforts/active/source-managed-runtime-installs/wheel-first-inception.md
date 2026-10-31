# Wheel-first direction — operator capture

Back to the [canonical effort](README.md) and
[transition proposal](wheel-first-transition.md).

## Operator request — verbatim

> Okay, this is the winning strategy then. We now need to rebuild the rest of the release, deployment, install, and start flow around it.
>
> 1. Promotion will produce a install-source snapshot which has proper buildable uv project folders for each app. Each app folder is indend to the straight-copyable into the version-slot folder, and then be either built in-place using uv setup, or from a pre-built wheel. Promotion should produce pre-built wheels from every final app folder. When the next main is ready, the wheels should all be associated artifacts.
> 2. Even though the install footprints will come down in the plugin payload folders installed by Copilot, let's not bother using them. Instead, let's update our install flow to specifically point back to the upstream GitHub repo, to run the `curl | bash` or `irm | iex` command that installs worktree-manager, and have that flow run locally, check out a staging commit from the remote, install worktree-manager only, and then sweep for registered repos, or use a repo supplied as the argument to the installer.
> 3. Based on the registered repos, we'll pull the target repo or all registered repos, sweep their plugin registrations, and determine which services should be activated locally. We'll then run the deployment flow for each service the registered repos declare need to be there. We'll copy to the version slots, check for wheels associated with the local commit, and then run `uv run ___ -- ___ setup` in each version-slot folder (ideally in parallel).
> 4. As each `setup` flow ends, when setup thinks it's ready, it takes responsibility for updating the bin-stub redirect markers pointing to it. Though we might want to split "prep" and "activate" parts of setup, so we can stage a fully-ready version, and do a quick cutover on-demand. As services start up, old versions will self-retire, or be nudged off by the incoming service. The main deployment flow won't wait for full cutover, just validation that each service indicated it had a complete build and was able to start.
>
> Ideally, we only do deployment for an initial setup, on-demand and key moments, or in the background on a long-ish interval (once an hour, maybe?). We can also make it so each launch of worktree manager kicks an update-prep (but not cutover) after load, so that if an update is ready for cutover, the next worktree launch kicks the cutover. In theory, cutovers should function purely in the background, but it's nice to have a new complete set of tools before a fresh session.
>
> What's "missing" from this flow is the notion that plugin versions maintained by copilot self-install: we stop doing that. At most, sessionStart hooks from plugins can warn if their associated back-end appears not to be installed, and we may consider an extension from worktree-manager itself which send a live notification into a session (only once) when a new version of the toolset is available.
>
> We will specifically march towards the copilot-extensions suite being a separately installed system, optimized for its own independent maintenance, using the plugins as connectors to the services and tools, and not as their source.
>
> Identify two things:
> 1. A happy fast-path where we can use our new wheel-build, copy, uv run flow to get perf and relability relief for install now
> 2. The long-term plan where we migrate the suite to plugin-independent install and update, eliminating long venv builds just as users try to launch worktrees.
>
> We will also need to have a reckoning with the "plugin reconciliation" flow, as well as the instruction projection and plugin-hoist flows; these are also taking a while and wll require us to build the flows "from scratch" in the fastest possible manner.

## Proposal instruction — verbatim

> Get the effort written out with vision extension upstream in copilot-extensions before you get started; include reports on the performance gains from the strategy.

## Subsequent authorized scope — summarized, not a verbatim transcript

The latest operator sequence requires the public effort/vision/performance
proposal to be reviewed and merged first, then local end-to-end implementation
and recovery qualification, then one final whole-flow implementation PR. This
replaces the interim combined-draft sequence. The local atomic all-prepared toolset strategy is
permitted with last-good selection, application-owned readiness, native
ownership and rollback; it is not a new source-hash framework.

Whole-system Windows-container proof is required before risking an active
host: local promotion/install-source inputs, real Manager and selected daemons,
supported fixture authentication/roles, actual agent-command round trips,
source changes and canonical version updates, reinstall, native retirement,
state survival and failed-candidate rollback. Compile-only evidence is not
that acceptance gate.

Default source is the trusted live upstream Main, with explicit local
install-source, source-checkout/commit and Main-ancestor last-known-good paths.
Container deployments use local promoted inputs without requiring a prior
GitHub release. Source/dependency admission, existing release trust and
Git/uv validation remain; no manual version or wheel-metadata weakening.

Automatic update inhibition remains intact and blocks only an explicit
`<repo> update --auto`. Manual update without that flag, raw self-install,
setup/install-source, doctor/list and ordinary launch are not blocked by the
switch; a nested automatic update does not break launch. Native exclusivity,
unsettled-effect protection and source/security admission remain separate.
The assigned manual rescue is
bounded to its addressed installation and is not fleet rollout or permission
to clear another owner's state. Already-ready doctor/inventory evidence comes
first; preserve durable configuration/data, active worktrees/forwards, other
agents and unknown ownership. Failed containers remain for inspection and
explicit disposition.

The generic recovery classes are captured separately in
[wheel-first-recovery-cases.md](wheel-first-recovery-cases.md). They are
operator-provided regression inputs, not a direction to reproduce unofficial
workarounds or supersede another contributor's open fix.

Implementation decomposition, named source switches, owner reuse and suggested
test placement in the [proposal](wheel-first-transition.md) are explicitly
agent-recommended choices; the quoted request and the scope summary above are
not silently replaced by those recommendations.
