# Inception request record

This is the operator's request record for
[Source-Managed Runtime Installs](README.md). Quoted operator text is retained
verbatim except for the explicitly marked private-host redactions.
The implementation plan is a separate interpretation, not part of these quotes.
Assistant design responses are summarized separately below; they are not
additional operator instructions.

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
