# Reversible action controls

`agent-dispatch actions` inhibits selected actions without stopping the services
that can observe a later clear. It is local to the selected installation:
`AGENT_DISPATCH_INSTALL_DIR` or the default `~/.agent-dispatch`.

Consumers cover **automatic coordinator replacement**, **automatic supervisor
self-update**, **new-worker admission**, **automatic runtime reconciliation**,
**native runtime activation** and **native supervisor reconciliation**. They
guard the supported Dispatch callers described below, not arbitrary calls into
the policy-neutral shared layout library or every external worker launcher.
Release and actual consumer qualification remain separate in the
[implementation effort](../../../efforts/active/dispatch-reversible-action-controls/README.md).

## Commands and support

```bash
agent-dispatch actions contract --json
agent-dispatch actions status --json
agent-dispatch actions inhibit coordinator-replacement --reason "temporary maintenance"
agent-dispatch actions inhibit supervisor-self-update --reason "temporary maintenance"
agent-dispatch actions inhibit worker-admission --reason "temporary maintenance"
agent-dispatch actions inhibit automatic-runtime-reconcile --reason "temporary maintenance"
agent-dispatch actions inhibit runtime-activation --reason "temporary maintenance"
agent-dispatch actions inhibit supervisor-reconcile --reason "temporary maintenance"
agent-dispatch actions clear coordinator-replacement
agent-dispatch actions clear supervisor-self-update
agent-dispatch actions clear worker-admission
agent-dispatch actions clear automatic-runtime-reconcile
agent-dispatch actions clear runtime-activation
agent-dispatch actions clear supervisor-reconcile
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
`automatic_attempts` field retains only automatic actions, including runtime
reconciliation; explicit maintenance and worker admission are excluded.

## Native maintenance

The POSIX and PowerShell installers check the required effects before source
initialization, instance reconciliation or active-state publication.
`install`, `update` and `provision` require `runtime-activation`,
`coordinator-replacement` and `supervisor-reconcile`; `stamp` requires
`runtime-activation`. Restoration checks the full maintenance set before
delegating to the retained runtime's installer. A selected older retained
consumer still needs separate capability qualification.

Fresh checks also precede publication and the actual native coordinator and
supervisor reconciliation calls. `deploy` and `_cutover` check
`coordinator-replacement` for both conditional and legacy cutover paths; legacy
cutover rechecks after waiting for its routing lock. An action denial exits
nonzero, and a denied cutover never falls back to a normal restart. `--force`
does not bypass policy. `--no-supervisor`/`-NoSupervisor` still removes native
definitions; it is not a preservation option and remains a reconciliation
effect.

Legacy cutover captures its selected installation before reading `service.env`.
That snapshot may supply ordinary host, port and credential pins, but cannot
redirect the installation root or captured routing directory, or change
`COPILOT_EXTENSIONS_CONTEXT`. A refused
overlay leaves the caller's environment unchanged; select the intended
installation explicitly before retrying. Root comparison preserves lexical
parent traversal rather than normalizing away intermediate links.
On Windows, the shared overlay canonicalizes environment keys before validation
and publication, including mixed-case root/context entries; later pins retain
native case-insensitive overwrite semantics. POSIX keys remain case-sensitive.
An explicitly selected custom routing directory remains supported; an overlay
cannot replace or clear it to retarget an already admitted attempt. Lock,
recovery, routing publication and replacement environment use the same captured
target rather than resolving ambient routing again mid-cutover.
Variable references in durable pins resolve against that overlay snapshot,
including earlier pins in the file, not the triggering process's unrelated
environment. POSIX dollar references and Windows dollar/percent references keep
their native quoting, escape and unknown-variable behavior without temporarily
rewriting process state.
Internal conditional callers must supply the target's captured `install_root`
alongside its captured `routing_dir` and conditional request. A missing policy
root is an explicit refusal, not permission to use the caller's installation.
Conditional deployment still skips caller configuration, `service.env` and
legacy lock/recovery preludes; its native snapshot and ownership fences remain
authoritative, including deliberately customized target routing.

An inhibition of a required effect is not a successful full update. Early
denial leaves active publication untouched. A later denial after an earlier
effect committed remains a partial operation, not an atomic rollback guarantee.
Policy refusal retains exit 3 through installer callers, owned publication
cleanup and bootstrap workers, even if policy clears before a fallback check.
Callable PowerShell guards still throw; the real installer maps their tagged
refusals to that distinct process result after cleanup. Unrelated errors keep
their normal classification. Neither a generic failure status nor a successful
cleanup may erase the refusal and admit another installer in the same attempt.
Native filesystem/publication helpers explicitly return required write, mode,
directory and removal failures through their nested callers. They do not rely
on Bash `errexit`, which is suppressed when a caller checks a helper's status.
Those failures stop later service effects, preserve their status and release
the update's cutover lock. Existing audited best-effort native-manager and
diagnostic behavior is unchanged.
Existing activation locks, source receipts, conditional publication recovery,
ownership fences and drain semantics remain authoritative.

The native reader can be run directly from the selected payload without
starting a coordinator or constructing a runtime:

```bash
python -I -B scripts/action_controls.py --root "<install-root>" \
  --action runtime-activation --action coordinator-replacement \
  --action supervisor-reconcile
```

It emits `ok` and a `decisions` array, returns 0 only when all requested actions
are allowed, and returns 3 for inhibition, including malformed explicit policy.
`--automatic` applies the shared bootstrap veto to supported automatic actions
only. This read-only decision is not a durable grant: callers recheck before
their effect. Explicit native stamping retains its Python-free path only when
ordinary policy is proven absent through unlinked, accessible ancestry. Present
or unprovable state requires the reader; an unavailable interpreter/helper is a
named prerequisite failure, never permission to proceed.
The absence walk preserves lexical link components: trailing separators cannot
hide a linked root, and parent traversal is not normalized away before each
ancestor is checked. Confirmed ordinary roots retain the Python-free path.

The session-start hook checks `automatic-runtime-reconcile` before automatic
stamp or background reconciliation. A denied optional hook action is visibly
skipped; the output-free hook still returns `{}` and a later session start
rechecks. Cold first-use checks before provisioning locks, status writes,
network fetch or installer execution. A denied upstream provision does not
fall back to another installer in the same attempt. An already available,
qualified runtime is executed before these cold checks, so ordinary service
startup does not depend on admitting update work. These are action attempts,
not cancellation of an already admitted background installer.

Cold legacy admission checks both the selected root's original lexical spelling
and governance's canonical effect root. Namespace governance remains authoritative
and does not consult an unselected legacy root. Normalization must not erase a
linked ancestor before policy sees it. Automatic maintenance requires a fully
qualified selected root; relative and Windows drive/root-relative spellings
fail explicitly without changing the warm-runtime startup path.
For a GLOBAL legacy entrypoint, governance's existing canonical-home marker
identifies whether the effect root is the default installation. Only then is
the original platform-home default spelling checked too. Resolving an entrypoint
through a home alias must not evade the shared bootstrap veto. Custom and
namespaced installations do not acquire a veto from an unrelated home/default
root, and GLOBAL entrypoints continue to ignore unrelated ambient install-root
overrides.

Late refusal must not leave a successful or indefinitely provisioning diagnostic.
Both cold frontends settle only their own successfully published provisioning
attempt before releasing its lock. They do not repair historical attempts from
age or promise a finalizer after an uncatchable process kill.
PowerShell background workers attach refusal diagnostics to their existing
attempt-fenced status and log; a superseded worker cannot overwrite replacement
evidence. Diagnostic-write failures are explicit and retain the original refusal
classification rather than admitting fallback or reporting success.

The POSIX session hook anchors a relative `PATH` interpreter to its original
working directory before changing to home. Both its fresh pre-launch admission
check and the worker reuse that same absolute path; the selected interpreter
leaf and virtual-environment context are not replaced by a later PATH lookup.

### Explicit preparation and supervisor preservation

The native installer's `prepare` action (`-Action prepare` on PowerShell) runs
its existing constructor and isolated import-health gate, then completes the
candidate without activating it. It does not publish selectors, launchers,
the deployment manifest or persistent PATH; it does not reconcile instances,
coordinator, supervisors or a Windows launch broker, or collect old runtimes. Activation/replacement
holds therefore do not prevent explicit preparation. Source attribution,
construction admission and dependency policy remain required.

The existing canonical construction helper retains the candidate and prior
selection until the command exits. A completed candidate at rest is not a
durable lease: later adoption reacquires admission/retention and health-checks
again, and may need to reconstruct an unselected candidate already collected.
This is not a new constructor, transactional rollback protocol or migration to
the separately evolving Python-owned construction API.

For an explicitly controlled coordinator-only adoption, native `update
--require-graceful-cutover --preserve-supervisors` (PowerShell: `-Action update
-RequireGracefulCutover -PreserveSupervisors`) skips supervisor reconciliation,
instance reconciliation and runtime collection. It rejects `--no-service`,
`--no-supervisor`, other actions and a legacy `.venv` path. Normal update
behavior is unchanged. Runtime activation and coordinator-replacement policy
still apply; supervisor-reconcile policy is not needed for an omitted effect.
On Windows the existing finite execution driver retains the explicit strict
and preservation options when it re-enters the installer; it is not bypassed.
The option does not inhibit an already-running supervisor's own self-update,
qualify an old consumer, or authorize effects on protected workers. A failed
graceful cutover still reports failure with possible prior selection changes;
there is no restart fallback or automatic rollback.

This is a provider-only expansion of the native installer boundary. Existing
callers keep their ordinary syntax; no automatic caller is migrated to these
modes. An explicit caller must select and verify the actual immutable installer
entrypoint against a reviewed source revision containing this contract before
sending the new syntax. The selected file's source/content identity, not a
newer marketplace payload, package-version equality or a running coordinator's
version, establishes support. There is no new coordinator capability field.
Older installers do not support `prepare`; older PowerShell also rejects the
new preservation parameter. Older Bash may silently ignore an unknown option,
so never use the preservation flag against an unverified older installer or
retry as an ordinary update after incompatibility. Rollback uses the existing
retained-runtime contract, not this new syntax against a historical installer.

The existing **Installation context full** manual workflow offers the
`dispatch-maintenance` portfolio for focused native Windows qualification at its
immutable dispatch SHA. Its three bounded packets cover real policy/overlay
behavior, installer cleanup and retained runtime, and background-worker
lifecycle using disposable roots and owned processes. Inspect actual test
results and skips; PowerShell on WSL does not qualify those Windows boundaries.
This optional portfolio does not add a required-PR gate, reenroll the unavailable
full Windows rotation or authorize a production installation.

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
