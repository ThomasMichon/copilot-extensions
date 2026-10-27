# Selected Live-Host Round Trip

Back to the [canonical effort](README.md).

The operator authorized one selected Windows host for a real install ->
clean middle -> reinstall experiment. This is an installed-acceptance lane,
not a substitute for production implementation or portable clean-room tests.
Host identity, concrete paths and operational records remain private.

## Scope and ordering

The following execution safeguards are **agent-recommended refinements** of
the operator's literal direction in the inception transcript.

1. Inventory installed payloads, runtime slots, command/profile entry points,
   services, scheduled maintenance, active process ancestry and durable state.
   Classify by the actual owner and removal contract, not a directory prefix.
   Preserve unknown content until classified.
2. Identify the full protected SSH-Gateway dependency chain, including
   transport, credential and forwarding dependencies on both sides. Record
   exact mux, wrapper and real Copilot process identities and terminal reach.
   Do not stop an ancestor or Job owner to manufacture a clean middle.
3. Make access-restricted, timestamped backups outside every removal scope.
   Include configuration, databases, credentials, history, claims and pending
   effects. Use consistent database backup or settled-writer procedures,
   including WAL state; verify hashes and isolated restore/read checks.
   Secret-bearing backups never enter Git or public evidence.
4. Reconcile existing maintenance ownership before effects. Use the reviewed
   banner contract and obtain tool-independent standby in the surviving
   session. Retain a recovery route outside the tools being removed.
5. Use attributable supported uninstallers in dependency-safe order. Preserve
   durable state and the protected connectivity chain. An older unowned layout
   needs an owner-supported migration/removal fix, not forced deletion or
   fabricated ownership. Implement/review/release prerequisite fixes as needed.
   Non-elevated SSH does not imply OS administrative privilege.
6. Record the clean middle: every in-scope tool/service/runtime is absent,
   while explicitly named connectivity, OS/shell, Copilot and mux exceptions
   remain. Disclose any retained helper, runtime slot, lock or refused removal;
   partial cleanup is not a passing clean-middle claim.
7. Reinstall through the owning released recovery/install flow, reconnecting
   retained state without overwriting it or replaying unknown mutations.
   Verify the selected release, actual running executable and current commands.
   Clear only this maintenance window under its existing owner's contract.
8. Prove continuation and final lifecycle handling, then record results in the
   canonical effort. Keep working until the complete parent gate is satisfied.

The operator subsequently authorized controlled closure and relaunch of the
selected host's legacy muxed sessions **only after verified backups and fixed
launcher qualification**, preserving resumable session state. Resolve their
current identities and custody before a managed graceful closure; do not kill
old PID numbers, discard pending work or replay unknown mutations.
This migration is not same-process survival evidence. Record the legacy
baseline separately; the qualified replacement launch must retain its own
identities throughout the later actual uninstall/reinstall experiment.

Before that migration, capture the exact selected active worktree set, each
worktree's associated native Copilot session, and its current mux/pane/process
identity in the private operational record. Restore that same set through the
existing detached create-or-resume owner, verifying the resumed session IDs;
do not enumerate every tracked worktree and start them all. A changed head,
unknown pending effect or still-live predecessor needs reconciliation before
replacement, not a fresh conversation or an automatic replay.

The existing command pair is the intended public entry point:

```text
<repo> worktrees copilot --worktree-id <id>
<repo> worktrees embody --worktree-id <id> --json
```

The former ensures/resumes and attaches the caller's terminal. The latter
ensures/resumes without attaching; repeated calls reuse a live mux. Validate
both against the qualified wrapper rather than adding a second launch path.
Detached creation is not itself proof that the native conversation resumed.

## Acceptance

- Backups are verified restorable before the first removal. Durable identities,
  configuration, queues and unresolved effects survive; credentials stay private.
- Fresh independent SSH connections work before, during and after maintenance;
  the protected chain is not merely assumed from an already-open connection.
- The same real mux, wrapper and Copilot processes remain interactive through
  actual removal and reinstall. Restarting them or using a synthetic child is
  not survival. Old launch construction may expose a baseline failure; stop
  safely and fix the source rather than kill the protected session.
- Clean-middle evidence distinguishes durable data from removable runtime and
  names every permitted exception. No concealed wait-only interpreter, poller
  or old installation supports a passing result.
- Reinstalled services recover their actual retained records through existing
  ownership/reconciliation protocols. Unknown remote effects are not replayed.
- Current start/end dispatch, exact incarnation, quiet absent-handler exit,
  Windows containment and maintenance standby meet the parent validation plan.
- Required review/CI and normal Main promotion precede installed acceptance of
  fixes. Necessary workflow repairs use their own authorized contribution lane;
  a failing unrelated test is not bypassed to accelerate this experiment.

## Initial status

Read-only connection preflight succeeded on the selected Windows host. The
remote shell is not elevated. No backup, service stop, uninstall, install or
live maintenance banner has been performed under this lane yet.
