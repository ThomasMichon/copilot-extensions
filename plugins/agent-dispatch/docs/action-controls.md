# Reversible action controls

`agent-dispatch actions` inhibits selected actions without stopping the services
that can observe a later clear. It is local to the selected installation:
`AGENT_DISPATCH_INSTALL_DIR` or the default `~/.agent-dispatch`.

Current consumers cover **automatic coordinator replacement**,
**automatic supervisor self-update** and **new-worker admission**. Explicit
`deploy`/installer maintenance and supervisor unit reconciliation remain open in the
[implementation effort](../../../efforts/active/dispatch-reversible-action-controls/README.md).
Do not treat these controls as a global deployment or worker-launch fence.

## Commands and support

```bash
agent-dispatch actions contract --json
agent-dispatch actions status --json
agent-dispatch actions inhibit coordinator-replacement --reason "temporary maintenance"
agent-dispatch actions inhibit supervisor-self-update --reason "temporary maintenance"
agent-dispatch actions inhibit worker-admission --reason "temporary maintenance"
agent-dispatch actions clear coordinator-replacement
agent-dispatch actions clear supervisor-self-update
agent-dispatch actions clear worker-admission
```

These are local commands: they do not start a coordinator, remove a lane,
restart a service or contact a remote installation. Unknown commands, actions
and options are rejected. A task-only credential does not grant this local
operator authority.

The JSON contract is `agent-dispatch.reversible-actions`, version 1. Its
`actions` list describes the **executing runtime's** supported controls, not
the latest package or every running consumer. An old runtime rejects this
command; do not write a policy file and assume that an old resident honors it.
After a reviewed runtime upgrade, independently qualify the installed command
and the actual coordinator/supervisor generation. This command deliberately
reports running consumers as unqualified rather than asserting adoption.

`status` reports each supported action's installation scope, effective
inhibition, reason, state source and any read error. An active switch is a valid
state, not an unsuccessful status query. Invalid state returns exit 2 with
`ok: false`; ordinary status and successful mutation return 0.
`action_attempts` contains all selected actions; the compatibility
`automatic_attempts` field retains only the automatic replacement actions.

## Worker admission

The production coordinator checks `worker-admission` before execution claims
and before minting a new spawn reservation or one-shot handoff-fallback attempt.
An inhibited fallback retains its sole attempt for a later poll.
Atomic create-and-claim also checks before a new task/claim mutation; ordinary
enqueue and exact deduplication no-ops remain available.
REST reports an explicit 409 denial with `detail.code: action_inhibited`,
`detail.action: worker-admission`, `detail.retryable: true` and a message;
the coordinator-hosted MCP tool returns an explicit error. Clearing state lets
the same caller retry against the same coordinator. A denial leaves task
generation, attempts, owner and existing reservations unchanged. An existing
active reservation lookup remains an idempotent no-op, not new launch authority.

Each local supervisor checks its selected installation before new reservation
attempts, restricted startup retries and redriving an unclaimed worker.
A restricted retry checks before preparation. A redrive checks before
marking startup incomplete, so an inhibited attempt cannot strand that pending
identity. Lanes keep polling and settling existing owned work; heartbeats,
start, progress and completion of an already claimed task are not inhibited.
Existing task holds, capacity, exclusivity and lane-stop controls remain
independent. The shared bootstrap switch does not inhibit worker admission.

One successfully reserved launch, or one redrive admitted before its startup
fence, may finish if the switch changes during that attempt. Freezing only that
attempt avoids abandoning a partially created worker or losing its send
identity. Later polls, redrives and execution claims recheck current policy.
An unclaimed retained worker must retry its claim after clear; inhibition does
not authorize a second body, resetting its reservation or blind replay.
The updated `ScriptTaskRuntime` waits and retries that structured denial in the
same worker, without heartbeat/start or a terminal failure while unclaimed.
Canceling that wait does not abandon the task. If the script genuinely exits
before claiming, the supervisor retains its pending reservation during
inhibition; after clear, confirmed absence permits ordinary failed-spawn retry
with the same task and logical creation identity, not terminal abandonment.
Live or unknown bodies remain untouched. Previously claimed scripts retain
their existing explicit-terminal-call requirement.

The coordinator enforces its own installation policy for REST and both MCP
callers, including remote clients. A supervisor additionally enforces its local
installation policy for local launch/redrive attempts. An old remote supervisor
cannot enforce a new local redrive switch until upgraded. Direct `TaskQueue`
embeddings are policy-neutral unless their trusted constructor supplies the
`worker_admission` callback; they do not inherit ambient host files.
Upgrade script runtimes along with their supervising consumers before relying
on the structured-denial retry behavior; an older script helper does not gain
that behavior from a newer coordinator.

## State and precedence

Installation-local state lives in `<install-root>/action-controls.json`:

```json
{
  "supervisor-self-update": {
    "disabled": true,
    "reason": "temporary maintenance",
    "at": 1790000000.0
  }
}
```

The command reuses the existing serialized, atomic override mutation primitive,
but the file is deliberately **separate from `overrides.json`**. Existing lane
and logical-unit overrides keep their stop/wind-down semantics. Clearing an
action never clears a lane override, task hold, safety check or ownership gate.

The explicit policy requires a JSON object of supported action records with
strict boolean `disabled` fields and optional text/null reasons. Missing state
is inactive. Malformed, unreadable, oversized or linked explicit state inhibits
the action and reports the error, while leaving its
consumer available for another check. Mutation refuses invalid existing state
instead of silently discarding it. Restore valid state and retry; no service
restart is required. Reads are bounded to 64 KiB and refuse non-regular files.
The atomic writer publishes UTF-8/LF on every platform, so a valid near-limit
policy cannot become oversized through Windows newline translation.
On POSIX, the reader also refuses files owned by another user; Windows relies
on the installation's existing filesystem access controls for that ownership
boundary.

For **automatic actions in the default installation only**, the existing shared
bootstrap switch is an additional veto. Its state path is
`~/.copilot-extensions/killSwitches/bootstrap-killswitch.json`, falling back to
the older `~/.copilot-extensions/bootstrap-killswitch.json` only when the primary
file is absent. `BOOTSTRAP_KILLSWITCH_STATE_FILE` retains its existing override.
Malformed shared state preserves that guard's legacy fail-open behavior, with a
visible diagnostic; this is not the stricter explicit action-policy contract.
An active explicit policy still wins. Custom install roots and nonempty
`COPILOT_EXTENSIONS_CONTEXT` do not consume the legacy global switch.

## Restoration and lifetime

Each eligible self-update poll reads current state again. The coordinator stays
ready while replacement is inhibited; the supervisor keeps its lease and lanes
alive. Immediately before a supervisor shutdown, policy is checked again after
waiting for existing owned work. No inhibition is cached for the daemon lifetime.

Clearing a switch removes only its veto. Ordinary staleness confirmations,
poll cadence, cooldown, drain, singleton ownership and installation-governance
checks still apply. The next eligible poll may itself replace the process as the
admitted self-update; a manual restart is not the mechanism for observing clear.
An already committed replacement is not canceled or rolled back by setting a
switch.

`AGENT_DISPATCH_SELF_UPDATE` and `AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE` remain
independent startup configuration. A service started with its static update loop
disabled does not acquire a new loop merely because an action switch is cleared.
Use the live action controls for a temporary pause, not startup-only environment
flags masquerading as reversible runtime switches.

## See also

- [Dispatch overview](../README.md)
- [Service lifecycle supervision](../../../docs/patterns/service-lifecycle-supervision.md)
- [Service contract evolution](../../../docs/patterns/service-contract-evolution.md)
