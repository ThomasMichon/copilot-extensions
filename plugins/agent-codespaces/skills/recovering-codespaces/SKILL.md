---
name: recovering-codespaces
description: >-
  Diagnose a disconnected versus corrupted GitHub CodeSpace, preserve and audit
  recoverable work, and rebuild only after explicit confirmation. Use when
  asked to "recover a codespace", "reconnect a codespace session", "rescue a
  broken codespace", "rebuild a corrupted codespace", or "fix recovery mode".
---

# Recovering CodeSpaces

First distinguish a transport disconnect from resource corruption. A dropped
bridge connection usually leaves the remote agent and its work alive; corruption
is the exceptional case that may require replacement.

`codespaces-lifecycle` owns CodeSpace status, connectivity, create/delete,
bootstrap hooks, session recovery, logger storage, and command failure
semantics. `agent-bridge` owns session liveness and reattachment.
`borrowing-codespaces` owns claims and lease semantics. This skill supplies only
the destructive-recovery safety gates and ordered orchestration.

Use the exact `argv` from the agent-codespaces session command catalog for
CodeSpace operations. Append the arguments shown below; never substitute a same-named command
found through `PATH`.

## Diagnose: disconnect or corruption

Use lifecycle and bridge status before touching the resource:

| Evidence | Classification | Action |
|---|---|---|
| Bridge disconnected, remote resource healthy, agent/session alive | Disconnect | Reattach and continue the existing session |
| Resource stopped but healthy | Disconnect/idle | Reconnect through lifecycle; do not recreate |
| Agent exited, resource and checkout healthy | Session failure | Audit logs and work, then resume or redispatch as bridge policy allows |
| Provisioning, filesystem, or repository state is genuinely unusable | Corruption | Enter the gated rebuild phases |

## Never-destroy-live-session gate

Before any destructive action:

1. Check the bridge session and lifecycle state.
2. If a session is active, stalled, or merely disconnected with evidence that
   the remote agent is alive, do not delete, clear, or redispatch it.
3. Reattach using `agent-bridge` / `codespaces-lifecycle` and read current
   progress.
4. Escalate only when evidence shows the resource itself is unusable.

Do not run diagnostic SSH against an active dispatch when lifecycle guidance
says it could disrupt the session.

## Gated rebuild

Parameterize the procedure with `<name>`, `<owner/repo>`, `<branch>`,
`<workspace-path>`, and the repository's configured source-control provider.

### Phase 1: Preserve

- Attempt the lifecycle's normal session and logger recovery.
- Preserve dirty files and repository-specific exported state when reachable.
- Store preserved work only in a user-approved durable location.
- Record anything that could not be recovered.

### Phase 2: Audit

- Enumerate local branches and dirty work in `<workspace-path>`.
- Verify each required commit or branch against the configured provider's
  durable remote.
- Query pull-request state through that provider.
- Confirm any borrowed-resource ownership through `borrowing-codespaces`.

Produce a manifest of preserved, remotely verified, and unrecoverable items.

### Phase 3: Confirm destructive recovery

Show the audit manifest and request explicit confirmation to destroy exactly
`<name>`. Confirmation must acknowledge every unrecoverable item. A transient
connectivity, startup, or recovery error is not sufficient justification.

### Phase 4: Force-delete only when justified

When normal finalization cannot operate on a proven-corrupt resource and the
audit gates passed:

```bash
<agent-codespaces catalog argv[0]> delete <name> --force
```

Use the lifecycle's normal finalize/delete path whenever it remains available.

### Phase 5: Recreate

Create the replacement for `<owner/repo>` through `codespaces-lifecycle`, using
the repository/provider configuration for branch, machine, region,
devcontainer, and naming. Do not embed organization-specific defaults.

### Phase 6: Bootstrap

Run only bootstrap or provisioning hooks declared by the repository/provider
configuration. Let lifecycle readiness and failure semantics decide whether the
replacement is usable; do not substitute private setup commands.

### Phase 7: Restore and verify

- Restore preserved work into `<workspace-path>`.
- Fetch and check out each required `<branch>` from its durable remote.
- Verify clean/expected git state and provider visibility.
- Re-establish the effort claim through `borrowing-codespaces`.
- Dispatch or reattach through `agent-bridge` only after lifecycle readiness
  succeeds.

Report the old and replacement resource identities, restored branches, session
recovery result, and any remaining manual action.
