# Inception request record

This is the operator's request record for
[Source-Managed Runtime Installs](README.md). Quoted operator text is retained
verbatim except for the explicitly marked private-host redactions.
The implementation plan is a separate interpretation, not part of these quotes.
Assistant design responses are summarized separately below; they are not
additional operator instructions.

## Disposable services and YAML maintenance banner

Operator direction, 2026-10-25:

> Okay, focus on getting the upstream vision alignment to support the notion of all copilot-extensions services being properly "disposable", with support for a yaml "maintenance banner" file that simultaneously acts as a killswitch against auto-update, and a notification to all agents about what is going on. We'll need to pair that with an instructions file in agent-conduct-guidance which informs agents that if the tools they need to invoke from the agent-* catalogs appear to be missing, they need to look at the provided yaml file for instructions, polling every 30 minutes via scheduled prompt until the blockage clears. Agents are not to attempt their own self-repair if the maintenance banner is up and has an explanation for the outage.

The [alignment contract](maintenance-banner.md) distinguishes this requested
shared YAML control from the earlier JSON-only source companion. Its proposed
filename, compatibility ordering and parser/admission safeguards are
agent-recommended implementation detail. No activation or host teardown is
authorized by this request capture.

## Critical connectivity exemption

Operator direction, 2026-10-25:

> One thing we should ensure: machine-to-machine connectivity services must be spared from any uninstall/reinstall snenanigans and must be treated like "critical" session-state holders, similar to Copilot mux or session-host wrappers. We should audit whether each form of SSH host/client/gateway mechanics would be impacted if we attempted a "complete uninstall" of all other copilot-extensions services on a machine. For example, agent-ssh *positions* keys and performs OpenSSH setup, but in theory it isn't itself the service which manages SSH requests. We should draw clear boundaries there. We don't want a force uninstall over SSH to suddenly kill SSH.

Follow-up scope clarification:

> credential relays are fair game for uninstall. credential relays aren't needed for direct machine management, so it's okay if an uninstall takes those out.

This qualifies the earlier all-supporting-services disposability statement.
Protect actual access-bearing connectivity, not every component that provisions
it or brokers agent credentials. Source audit and reviewed intent correction
are tracked in #9048; neither statement authorizes a live uninstall or transport
change.

## Initial request

> We have a general problem that when we try to stabilize copilot-extensions in main, we have no good way of uniformly validating the suite against live usage. Presently, our installation package is coupled to `copilot plugin update`, and the sessionStart hooks in the various plugins bootstrap the version-slot install folders for the payload.
>
> I want to consider the following:
> 1. Allow a local config switch to decouple the service installs from the Copilot sessionStart hooks, so that only Worktree Manager takes responsibility for service updates.
> 2. Add a configuration switch, and susequent support, for Worktree Manager to maintain an independent install-source checkout of copilot-extensions, which can be kept in sync with main, pinned to older, marked-stable main commits, or instead pointed at dev for direct install from dev.
> 3. Get dev so that our service suite installers can be sourced directly from dev, instead of the plugin payloads or the vendor-merged main checkouts. This would entail ensuring the copy-to-version-slot flow properly live-vendors necessary dependencies, and that the binstub redirects support hash-based dev slots. We'll want to switch from a singular, mutable "dev" slot to immutable, hash-marked dev slots. We won't source from worktrees or "dirty" dev checkouts, only clean, merged versions, but sourcing directly from dev on opted-in machines will allow for faster turnaround time when validating fixes.

## Plugin delivery and narrow hook boundary

> I expect our plugins to still have the Copilot-side of things still handled by Copilot's own update system. All the skills, instructions, etc. will still come through the plugin marketplace. If we wanted to support overrides for that, too, it gets more complicated, because we'd need to redirect the marketplace definition to a local folder. We want all behaviors of the plugin hooks to run except the part where it copies out the service payloads into the version-slot folder and updates the binstub. That's the part the local config needs to control.

## Thin adapters and future plugin sourcing

> This does, of course, mean that hooks and extensions might not be version-locked to the backing service, and so will need to be as "thin" as possible, either quickly redirecting to durable installed binstubs (preferred), doing  everyting in-place, or having drift-compatible contracts with the host services.
>
> Allowing plugin-sourcing from the local checkout probably *is* a long-term goal, too, but we need to be careful not to cause "shadowing" where Copilot sources both the marketplace version and the local-checkout version in parallel and causes pickup to stall.

## Execution request

Only the private host name is replaced below; platform, ordering and intent
are unchanged.

> Great. Help build out vision updates, make an effort, and help drive this to reality. We can start by working on making a dev-sourced install path work in a clean-room container on WSL. Then, we can see if we can modify [native Windows host] to detach from the marketplace, and get it working internally using a local branch. This will let us iterate quickly.

## Scope confirmation

The coordinator proposed `source-managed-runtime-installs` as the canonical
effort slug and asked whether the native Windows pilot should detach only
runtime deployment or also change plugin-content sourcing.

The operator selected:

```text
effort_slug=source-managed-runtime-installs
windows_pilot_scope=runtime-only
```

## Full transition and recurring maintenance

Only the private host name is replaced below. The request's complete installed
service/tool scope and regular source-maintenance requirement are retained.

> Your current mission is to get [native Windows host] onto dev-backed copilot-extensions installs for all services and tools, making fixes as needed to ensure everything works end-to-end. Make sure we have a regular flow to keep the source dev checkout healthy and updated, for example using agent-machines maintenance to sync it and re-run the install on an interval. Fix service installers, vendoring, etc, as appropriate to get the faster development and preview loop fully functional

The earlier runtime-only clarification still governs Copilot plugin-content
delivery; "all services and tools" does not authorize enabling unselected roles.

## Execution follow-through

> Sounds like good progress. Keep going!
>
> Be sure to build out the effort as you go and track durable goals as you continue

## Assistant analysis - summary, not operator requirements

The existing source override belongs to the Manager's self-updater. Existing
claim-owned development generations retain editable source, so they are not
the requested clean merged-source deployment. Promotion already supplies
canonical vendor materialization and version-composition machinery to reuse.

Recommended implementation details include exact per-run source resolution,
retaining dependency/profile identities alongside the commit, testing source
removal and failed candidates, and keeping a known-good Manager recovery
selection while canarying services. These are marked agent-recommended in
the effort where they exceed the literal request.

## Manager-owned automatic maintenance

Private consuming-harness names, the private machine name and the private
alternative-controller name are replaced below. The default-off choice,
delegated setup consent, Windows startup preference and end-of-setup outcome
are retained. The operator's reference to Windows Task Manager concerns
scheduled-task registration in the preceding discussion; the implementation
must not assume that every current-user scheduled task requires elevation.

> The problem with Windows Task Manager is it requires a UAC prompt, which works well from some agent-driven situations but not others. It's easier to use Windows Autorun registrations to launch custom daemons than to use Task Scheduler.
>
> copilot-extensions should make auto-update opt-in, but we can allow the consuming harness repo to do that opt-in on behalf of the user or local machine. [A consuming harness] would enable it (though we could also use [an external update controller]). And [another consuming harness, available on its owning machine] would also enable it; at the end of setup of that repo, which installs worktree manager, we'd want auto-update to be enabled.

The coordinator clarified planning-only versus implementation and adoption.
The operator selected:

```text
scope=implement-and-adopt
```

## Explicit repair authorization and legacy pre-launch audit

The coordinator asked whether legacy interruptions could use a separate,
request-bound operator-audited pre-launch recovery path rather than exclusively
machine-verifiable cleanup proof. The operator answered:

> Allow the audited pre-launch recovery, but in the end if the operator says to do a repair, clean install, force reinstall, or whatever, no evidence is needed; just do it

This approves the audited path without making it a prerequisite for explicit
repair. The plan distinguishes operator authorization from native cleanup
proof and retains the original failed outcome. The standing clarification is
not itself a concrete request to run repair on any host.

## Wholesale same-installation cutover

After a progress update emphasized custom-root/installation-cell qualification,
the operator clarified the existing runtime-only target:

> I'm confused: with install-from-dev-branch, we do expect to be installing to the same global space, replacing the main-sourced install with dev install. It's just that the installers themselves source from a dev-hash instead of a main-version.

> Er specifically have the global binstubs pointing at the dev install slot, and have the dev install work with the same runtime data as the main install would have

> Right, we're not doing custom-cell/root for this. This is a wholesale cutover

This is a wholesale replacement of code in the existing installation, not a
second installation or runtime-data migration. Existing global commands and
services must select the immutable Dev generation while using the existing
Main installation's data/configuration. Separate scoped-install capability
does not gate this transition. The clarification does not itself authorize
a concrete host cutover, restart or configuration change.
