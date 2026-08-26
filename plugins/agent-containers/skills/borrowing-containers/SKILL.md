---
name: borrowing-containers
description: >-
  Bind a borrowed local dev container to an effort, dispatch work to it, report
  the effort-to-container mapping, and release it during effort wrap-up. Use
  when asked to "borrow a container for an effort", "run this effort in a
  container", "release the effort's container", or "show effort container
  leases".
---

# Borrowing Containers

This skill owns only the control-plane binding between an effort and a
container. The `containers-fleet` skill owns fleet provisioning, readiness,
configuration, lease storage and conflict rules, and the underlying dispatch
integration. Read and follow that skill rather than reproducing those mechanics
here.

Use the exact `argv` from the agent-containers session command catalog for
container operations. For dispatch, follow the `agent-bridge` skill; its
current management command remains an explicit compatibility boundary. Append
the arguments shown below; never substitute a same-named agent-containers
command found through `PATH`.

## State contract

Efforts live in the **user's state repo**, which may differ from the launch or
product repo. Resolve that state repo using the active control plane, then find
the active effort by repository and effort slug. Never create or update effort
state in the plugin or product checkout.

The effort slug is the lease holder. Borrow with:

```bash
<agent-containers catalog argv[0]> borrow <effort-slug>
```

Record the printed container name under the effort's existing metadata in its
`README.md`:

```markdown
**Container:** <name>
```

For an effort with per-branch records, put the binding in the matching branch
record instead. Persist the state-repo change according to that repo's normal
change policy.

## Dispatch

Dispatch through the bridge's container resolver, not by entering the container
directly:

```bash
agent-bridge send container:<name> "<task>" # marketplace-isolation: allow agent-bridge-management
```

Follow the `agent-bridge` skill for session and follow-up mechanics. Follow
`containers-fleet` for all container transport and lifecycle details.

## Release and archive

During effort completion or archival:

1. Release by effort slug: `<agent-containers catalog argv[0]> release <effort-slug>`.
2. Remove the active `**Container:**` binding or annotate it as released in the
   archived effort.
3. Archive the effort in the user's state repo using that repo's effort
   conventions.

Do not treat archival as proof that release succeeded. Surface release failures
so a stale lease can be reconciled through `containers-fleet`.

## Status mapping

Use `<agent-containers catalog argv[0]> leases` and map each row's effort holder back to the active
effort slug in the user's state repo. Report:

- effort slug → container name for active mappings;
- leases with no matching active effort as release candidates; and
- effort files whose `**Container:**` value has no matching lease as stale
  state.

The fleet skill remains authoritative when lease state and an effort file
disagree.
