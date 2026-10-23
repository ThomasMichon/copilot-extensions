# agent-pull-requests

Cross-repository pull-request commands for Copilot CLI sessions that need to
act on `owner/repo` targets **without** requiring a local checkout of that repo.

This plugin is the extraction from `agent-worktrees`' worktree-bound PR
commands into a separate, API-first surface:

- `agent-worktrees` remains unchanged and fully supported.
- `status`, `create`, `merge`, and `wait` are all implemented.

## Installation

Marketplace install deploys a real runtime:

```bash
copilot plugin install agent-pull-requests@copilot-extensions
agent-pull-requests status --repo <owner/repo> --number <n>
```

The installer builds an immutable versioned venv under
`~/.agent-pull-requests/versions/<version>/`, deploys a real
`~/.local/bin/agent-pull-requests` binstub, and self-reconciles that runtime on
session start the same way the other Python runtime plugins do.

Both native installers also accept an explicitly
[prepared source payload](../../tools/prepare_runtime_source.md). Its verified
receipt selects a source-qualified immutable generation; the deploy manifest
retains the package version in `source.version` and records the independent
`runtime_version` and `runtime_source`. Stamping preserves the complete hashed
assembly rather than rematerializing its dependencies from a checkout.
An existing source-qualified snapshot is reused only after its complete receipt
matches the selected source; it is never replaced in place.

The installed legacy first-use wrapper uses the shared
[publication admission](../../libs/installation-context/README.md) before cold
trace or provisioning-lock writes. Managed deployments retain healthy runtime
dispatch; cold refusal and missing/older helper readiness do not provision.
Retained snapshots preserve canonical source identity after publisher removal.

The installer's `uninstall` action holds the same transaction lock as
install/update. It removes runtime/config contents but retains the lock files
and `.install-stage` coordination, so the install directory may remain.
Normal self-staging reap removes dead-owner stages on later invocations; purge
never replaces a held lock.

The installation-cell adopter remains under validation below. Its generated
payload command and `scripts/init.ps1` / `scripts/init.sh` resolve native
governance before touching a runtime. Missing/false policy retains legacy
bootstrap, including first use before Python is installed. Namespaced requests
never fall back to that legacy root.
The native `bootstrap-check.admission` session-start wrapper owns exactly one
empty JSON response through the suite-standard exit/finally contract. It keeps
admission and legacy-bootstrap failures visible on stderr with their original
exit status; only the delegated bootstrap's stdout is suppressed to prevent a
second JSON response.
For an unattributed legacy payload, a valid disabled policy may omit the
`marketplaces` map or leave it empty. Invalid member types still refuse; a
nonempty scoped map requires resolvable source identity.
Legacy self-staging revalidates the original installed marketplace against the
current policy. The child accepts an origin hint only inside its own canonical
staging layout, with an existing installed payload and identical, link-free
payload contents; staging flags alone never bypass admission. A replaced or
unavailable origin fails closed rather than guessing its marketplace.

## Verbs

```text
agent-pull-requests status --repo <owner/repo> --number <n> [--json]
agent-pull-requests create --repo <owner/repo> --head <branch> [--base <branch>] --title <t> [--body <b>] [--draft] [--json]
agent-pull-requests merge  --repo <owner/repo> --number <n> [--squash|--merge|--rebase] [--auto] [--delete-branch] [--json]
agent-pull-requests wait   --repo <owner/repo> --number <n> [--interval <s>] [--timeout <s>] [--json]
```

By default these verbs retain their GitHub interface. `create` and `merge`
shell out to `gh pr create`/`gh pr merge` with an explicit
`--repo`/`--head` pair so no local checkout or branch context is required;
`wait` polls `status` until the pull request reaches `MERGED` or `CLOSED`, or
the timeout elapses (exit code `3`).

### Explicit native provider commands

Add `--worktrees-command <absolute-command>` to any of these four verbs to
reuse the configured Worktrees PR command family, including Azure DevOps.
Select the exact command from the session command catalog, not an ambient
same-named executable. In an installation cell, the validated same-cell peer
is authoritative instead of the legacy command hint. The selected runtime
must support `pr-create --from-branch`, `pr-merge --now` and the versioned
`pr-watch snapshot` contract; a missing command, invalid response or target
configuration error refuses the operation without falling back to GitHub.

```text
agent-pull-requests status --repo <project/repository> --number <n> --worktrees-command <absolute-command> --json
agent-pull-requests create --repo <project/repository> --head <pushed-branch> --title <t> --draft --worktrees-command <absolute-command> --json
agent-pull-requests merge --repo <project/repository> --number <n> --worktrees-command <absolute-command> --json
agent-pull-requests wait --repo <project/repository> --number <n> --timeout 600 --worktrees-command <absolute-command> --json
```

Native output preserves the central command's JSON shape, not the legacy
GitHub status shape. Status and wait retain the complete validated native
snapshot, including separate persistent votes, approval events, threads,
policies and human gates. Wait adds `timedOut` and retains the existing exit
codes: `0` merged, `1` closed without merging/error, `3` timed out.

Create delegates to `pr-create --from-branch`. It requires a tracked caller
worktree and a different registered target, uses the target's configured base,
and retains central publication checks, attribution, idempotent reuse and claim
journaling. No second claim is written here. `--base` is refused before creation:
the central CLI does not expose that override. `claimed: false` and its recovery
warning remain visible if the PR exists but its claim could not be recorded.
An empty or whitespace-only head refuses before invoking the central command,
so it cannot select local squash/push by losing the foreign-branch operand.
Forwarded option values are attached with `=` to preserve titles and bodies
that themselves begin with dashes.

Merge delegates to `pr-merge --now`; the central role/profile gate may refuse
it. Native ADO auto-completion, policy evaluation, latest-push evidence and
authorized bypass configuration remain owned by Worktrees. An accepted,
pending auto-completion is not reported as a completed merge. `--auto`,
`--squash`, `--merge`, `--rebase` and `--delete-branch` are refused before mutation rather
than silently overriding repository policy. Use the central consent workflow
for a target whose profile does not allow `--now`.

Each read has a 20-second deadline; each mutation has a 90-second deadline and
bounded output. A lost mutation response is uncertain: reconcile with native
status before retrying, never retry through the GitHub path. On Windows,
PowerShell command arguments travel as JSON data over stdin. PowerShell 7.3
or newer with standard native argument passing is required through the final
Python invocation, preserving empty arguments, multiline bodies and quotes.
Older PowerShell hosts and batch command shims are refused before the target
command executes; use the catalog's PowerShell command or a validated same-cell
native peer. An empty selector is invalid, never a request for GitHub fallback.

## Acknowledged watch callbacks (opt-in)

The existing shared watch daemon advertises
`"capabilities": ["acknowledged_notifications/v1"]` in `watch health --json`
(`serve status --json` reports the same health). Consumers must negotiate this
capability before sending new fields; missing support permits their own ordinary
process-wait fallback. The subscribe CLI checks it and fails explicitly rather
than silently registering a legacy callback.

```text
agent-pull-requests watch subscribe --repo example/project --number 42 --subscriber-id consumer-1 --until merged --timeout 600 --acknowledged-notifications --notify-timeout 10 --json --notify-argv consumer-callback
agent-pull-requests watch status --json
agent-pull-requests watch unsubscribe --repo example/project --number 42 --subscriber-id consumer-1 --registration-id <registration_id> --json
```

Place `--notify-argv` last; it accepts an argv list, not a shell expression.
For callbacks with option-looking arguments, use the structured registration
wire contract documented in [the CLI reference](docs/cli-reference.md).
Callback argv is durable configuration: use an attributable executable path
and **never put credentials in it**. The owner does not persist callback output,
exception text, or environment variables. The consumer owns authentication,
idempotency and recovery of its own effects; there is no consumer-specific
coordination dependency here.

Before invoking a callback, the owner atomically persists an immutable fired
JSON payload with `notification_protocol`, `event_id` and `registration_id`,
alongside the legacy event fields. **Exit code 0 acknowledges that event**;
nonzero, launch errors and callback timeout retain it. Callback stdout/stderr
are discarded. Callbacks run headlessly with a positive, finite deadline
(default 30 seconds, configurable up to 30 with `--notify-timeout`).
Every acknowledged attempt uses the canonical owned-process runner, independently
of the observation provider. Timeout cleanup owns the entire callback tree,
including Windows wrappers and their descendants, before replay can begin.
The subscription's optional `--timeout` must also be a finite positive number;
expiration fires a durable `timed_out` event, even during polling outages.
Acknowledged subscriptions retain their original absolute deadline across
state writes and reloads; persistence never resamples a wall/monotonic clock
pair to invent a replacement deadline.

Delivery is **at least once, not exactly-once subprocess execution**. Retry
delays are 1, 2, 4, 8, 16, 32, then 60 seconds, capped at 60 indefinitely.
Restart/crash recovery replays the same event identity and payload, including
when the consumer committed its effect but the local ACK was lost. Consumers
must commit idempotently by event/registration identity before exiting 0 and
make that commit concurrency-safe: a callback can outlive a crashed owner
while a successor replays the same event.
Independent callback workers prevent a slow callback from blocking another
subscriber (including one on the same PR), with at most eight callback workers
per owner. Pending retries remain queued, not one sleeping thread per event.
One live owner never overlaps
deliveries for the same registration. Graceful restart drains those callbacks,
but ungraceful owner death does not provide that execution guarantee.
No persistence lock is held while running a callback.

`watch status` exposes pending event/registration identity, failed attempt count,
next retry time (Unix seconds) and sanitized reason codes:
`callback_nonzero`, `callback_timeout`, `callback_error`. State-write outages
are reported separately as `persistence_error: "state_write_failed"` in
status/health, clearing after successful persistence. There is no automatic
retry exhaustion or invisible dead-letter removal. The event remains until ACK
or explicit unsubscribe/replacement. Cancellation cannot undo an already-running
callback; a late ACK cannot delete a re-registered subscriber. Optional
`--registration-id` makes cancellation generation-fenced too.

A failure after state replacement but before its directory-sync acknowledgement
is an ambiguous committed outcome, not a rollback. Memory retains the replaced
state; registration/cancellation reports `ambiguous_registration` or
`ambiguous_cancellation` rather than success. Reconcile that registration before
retry or fallback. Only omission of a cancellation identity selects legacy
unfenced behavior; explicitly empty, null, or malformed identities are rejected.

Pending events remain in the owner's existing `watch-subscriptions.json`,
under its installation-boundary state root (`AGENT_PULL_REQUESTS_HOME` when
provided), not a second state authority. Startup resumes pending deliveries
before ordinary polling; it refuses malformed opted-in state with a sanitized
error, leaving the file unchanged for explicit operator recovery. Do not
downgrade to an owner lacking this capability while opted-in records remain:
acknowledge or cancel them first.

Without `--acknowledged-notifications`, registration responses and callback
stdin retain the legacy wire format, callbacks are best-effort, and older
persisted subscriptions still load. Legacy callbacks also use independent
workers so they cannot stall observation for another subscriber; they still
receive only one best-effort attempt. The reliable contract is not silently
enabled for legacy consumers.

## Pull-delivery reconciliation core (internal source library)

[`watch_replay.WatchReplay`](src/agent_pull_requests/watch_replay.py) adds the
PR-owned, transport-neutral registration/replay transaction core described in
[the replay contract](docs/watch-replay.md). It reuses `WatchRegistry` transition,
deadline and stable pending-event behavior, with consumer-scoped reads and exact
registration/event ACKs instead of a callback executable. Native callback
scheduling explicitly excludes these pull records.

This is **not a new CLI capability, serving route or deployed fleet service**.
The embedding adapter still owes authenticated scope, authorized observations,
one active writer, lifecycle custody and atomic persistence of the complete
envelope. No Gateway identity or forge credential authority is implied.

The optional [`watch_gateway.WatchGateway` adapter](docs/watch-gateway.md)
now binds the real Gateway connector's admitted caller to configured
consumer/account/repository scopes and serves bounded versioned replay
operations from a separately provisioned disk store. Its optional `fleet`
extra does not affect ordinary CLI/native installs. This source adapter
starts no daemon or listener; production lifecycle, forge observation,
Dispatch consumption and installed/fleet acceptance remain separate work.

The optional [`watch_service.WatchService` lifecycle](docs/watch-service.md)
now owns that serving adapter, the finite native observer and Fleet connector
under one host-local writer lease. It adopts existing replay state before
starting, reports local readiness and joins actual serving/native/disk work
before releasing custody. Failed drain retains ownership. This is an embedding
API, not the independently installed service/container role or a completed
activation/migration contract.

The same optional extra exposes the
[`watch_replay_client.ReplayClient` API](docs/watch-gateway.md#finite-typed-replay-client)
over Fleet's finite authenticated caller. It validates typed receipts and bounded
native event batches, retains original request identities and makes no automatic
retry, poller or process fallback. Revocation, cancellation and lost mutation
responses remain uncertain; even HTTP 403 cannot prove an in-flight commit
did not happen. The embedding consumer still owns intent persistence,
effect idempotency, exact ACK and lifecycle.

## Native configured observation (source capability)

The existing watch owner also accepts an explicitly attributed Worktrees
command for native Azure DevOps observation. It executes one bounded
`pr-watch snapshot <repo> <number> --json` read per polling cycle. Worktrees
owns target configuration, credentials, native provider reads and gate policy;
this plugin neither imports its Python package nor implements another REST
adapter. A successful registration requires a version-1
`agent-worktrees.pr-snapshot` envelope with an ADO observation, or a native
terminal state when the retained head and active observation are unavailable. A missing or
incompatible capability fails registration, never starts a second watcher.
In an installation cell, the physically attested owner uses its validated
immutable same-cell Worktrees peer prefix; the recorded command hint is not
executed as an alternative. Native observation is passed through the existing
passive owner and writer adapter, so it shares their state root, writer lease,
quiescence and activation boundaries. Legacy commands retain their explicit
attributed executable path.

Native snapshot children and their callbacks retain owned containment through
completion, timeout and cancellation. Windows uses a strict kill-on-close Job;
POSIX keeps the original child unreaped until its process group is retired,
then reaps it. Unsupported or lost child custody fails visibly rather than
signalling a potentially reused process-group identity.
Windows callback input has independent absolute-deadline supervision, so a
non-reading child cannot block cleanup during a synchronous pipe write.
Supervision and pipe-reader threads are joined before returning.
Execution uses the canonical `agent_procutil.owned_process` helper. Generated
delivery callbacks select an explicitly contained worker, whose Bridge peers
inherit that verified outer scope instead of opening separate POSIX sessions.
The public standalone delivery entry supervises the same worker, preserving
cleanup without relying on an ambient containment flag.
Pre-flag source-preview callback configurations are not rewritten automatically;
reconcile their exact registrations before treating them as contained workers.

```text
agent-pull-requests watch subscribe --repo example/project --number 42 --subscriber-id pr-watch:worker --worktrees-command <absolute-worktrees-command> --continuous --acknowledged-notifications --until merged --until closed --until head_changed --until votes_changed --until approvals_changed --until threads_changed --until policies_changed --until gates_changed --json --notify-argv <callback>
```

Health advertises `native_snapshots/v1` and `continuous_notifications/v1`
when the native adapter is available. These describe protocol support, not
permission to assume any target's ADO configuration is usable: registration
preflights that target. The native executable is part of the watch key, so
different attributed installations cannot accidentally share a target's poller.
Its exact path must also accompany cancellation.
Registrations also bind the normalized provider endpoint/repository/PR identity;
configuration changes cannot silently retarget an existing subscription.

For callbacks containing options such as `python -m`, use `--notify-stdin`
instead of `--notify-argv`: stdin contains a bounded JSON object such as
`{"argv":["<python>","-m","consumer"]}`. The same notify validation and
acknowledgement rules apply; this is an input adapter, not another wire protocol.

Native events additionally include `snapshot_protocol` and the immutable
`snapshot` envelope. Vote, approval-event, discussion, policy, head and gate
changes are observable even when aggregate CI/review states do not move.
Completeness, error and guidance changes also wake gate subscribers.
Vote/approval evidence also maps to `review_changed`; readiness becoming
human-actionable maps to `ready_for_human`. Both are observations, not actions.
This owner does not reinterpret approval freshness, reviewer membership,
comment resolution or human-owned gates.

Continuous subscriptions are explicit and require acknowledged notifications
plus both terminal transitions. Legacy subscriptions remain one-shot.
The owner keeps polling while a continuous callback awaits ACK. One immutable
pending event and at most one coalesced successor are persisted in the same
subscription file; the successor carries the latest snapshot and the union
of intervening transition names, not an exhaustive backend event history.
ACK promotes that successor, or re-arms the retained registration. Terminal
observation (or an explicit subscription timeout) retires the registration
only after its final event is acknowledged. Repeating an identical continuous
registration preserves its generation and pending delivery; changing it
requires explicit cancellation.
An open native registration persists its validated admission snapshot as the
baseline before reporting success. Polls started before that registration
cannot roll its baseline back. Terminal admission keeps the immediate
terminal-event behavior. Delivery scheduling captures retry metadata under
the state transaction and rechecks event identity before launching a worker;
an ACK may clear or replace a continuous pending event without invalidating
the shared scheduler.

Status adds registration identities, in-flight callback identities and
sanitized native observation errors. Cancellation cannot recall an already
running callback. A facade must refuse to claim a worktree is stopped while
one remains in flight or its status is unknown.

This documents source behavior, not installation, rollout or a new service
lifecycle. Existing stop/restart and governed-state boundaries are unchanged.

The Worktrees facade integration hook handles `pr-watch subscribe <repo> <pr>
--notify <worktree-handle> --bridge <absolute-command>`, `pr-watch stop --notify
<worktree-handle>`, and `pr-watch ownerstatus` (each supports `--json`).
Legacy peer discovery uses only the selected Worktrees payload's sibling,
validates both plugin manifests and canonical platform bin paths, and never
searches PATH. `--pull-requests` can only select that same verified owner.
With explicit installation context, the facade instead validates the existing
cell and uses canonical peer launch for the PR owner, the Worktrees callback,
and Bridge delivery. Raw owner-command overrides are refused in that mode;
missing peers or invalid receipts never select another marketplace or legacy
installation. The callback rebinds to Worktrees before invoking its private
delivery handler, then resolves Bridge in that same cell.

The callback resolves only an existing live bridge handle, sends with an exact
session fence, protocol floor 39 and its stable event ID as the idempotency key,
and queues the prompt without waiting for agent work to finish. Exit success
alone is not acknowledgement: the callback requires a current matching durable
inbox admission receipt. This proves admission, not consumption or completed
work. Missing live owners or incompatible receipts leave the event pending;
the callback never creates/resumes an agent or restarts the bridge.
Native callback processes and descendants remain inside the existing callback
deadline and strict caller-owned process boundary, including peer-bootstrap
layers. Quiescence still waits for callback completion and durable ACK writes
before state-writer transfer; ACK remains inbox admission, not consumption.

## Watch daemon shutdown

The on-demand PR-watch daemon (`serve`) persists pending subscriptions so
`serve restart` can restore them. Shutdown closes request admission and the
listener, drains already-accepted handlers, then wakes and joins polling
threads and callback workers before releasing the single-instance lease. Handler
draining has a five-second deadline; worker joining allows 35 seconds so a
30-second callback can finish before successor replay. Callback workers are joined
even when handler draining fails. Exceeding either deadline raises an explicit
error rather than reporting a completed graceful shutdown. Pending subscriptions
are retained, not cleared by shutdown.

Already-accepted legacy callbacks queued beyond the eight-worker limit are
drained in bounded batches before shutdown finishes, with 35 seconds allowed
per newly started batch. A large legacy backlog can exceed restart's 45-second
wait: restart then refuses to start a successor while the owner still drains.

`serve restart` probes lease ownership, not leftover rendezvous metadata or an
ambiguous failed health RPC, to decide whether a predecessor is running.
An unreachable lease holder must not be bypassed. Restart probes actual lease
availability for up to twenty seconds
before starting its successor. If the predecessor still owns the lease, restart
fails without spawning another daemon. After starting, restart requires a live
health response within ten seconds; a stale rendezvous file is not readiness.

This is the drain boundary for the existing on-demand stop/restart path, not a
zero-downtime active/passive rollout. See the shared
[graceful cutover pattern](../../docs/patterns/graceful-daemon-cutover.md)
for resident-daemon rollout requirements.

## Governed watch-state groundwork (internal)

The watch-state core supplies an inactive construction and quiescence boundary
for the reviewed governed-cutover design. This adoption is not yet released or
advertised in the peer roster. Policy-default legacy `serve`, stop/restart and callback
wire contracts keep their existing behavior. The internal explicit-context entry
now composes the core with the shared orchestrator, but is not evidence that
installation-cell adoption or deployment is complete.

`WatchDaemon(active=False)` does not read subscriptions, persist state, start
polling or callbacks, or accept registration/cancellation mutations.
After acquiring the installation's state-writer authority, the adapter can call
`reload_state()` to read the final persisted snapshot without starting effects.
Strict adoption refuses malformed legacy or acknowledged entries instead of
dropping them. A failed or late read cannot activate an earlier cached registry.
`resume_effects()` is a separate operation authorized by the owner's durable
transaction commit; it does not itself acquire or validate leases, installation
receipts, governance or transaction authorization.

`quiesce(timeout=...)` closes only application mutation admission while retaining
the authenticated listener and foreground lifecycle. New registration or
cancellation requests receive an explicit retryable error. Already-admitted
handlers, pollers, callbacks and ACK writes finish before a final persistence
acknowledgement permits a quiescent result. Unlike legacy stop's per-batch
allowance, quiescence never extends its total budget to launch another accepted
legacy batch. An incomplete drain keeps the registry, queued batches and live
worker references: it does not authorize releasing the writer lease or loading
a replacement snapshot. A same-owner abort can resume those retained objects;
after any writer-authority transfer, the adapter must reload the final state
before authorizing effects.

Lifecycle operations use serialized admission and a finite positive completion
budget. Late
I/O or worker startup is not reported as successful completion or reopened
mutation admission. Partially started workers remain attributable to the owner
for cleanup/retry. Activation's terminal-state recheck and worker-stop clearing
are atomic with shutdown and close, so a terminal acknowledgement cannot be
followed by activation erasing the stop signal. Invalid close budgets are
rejected before changing owner state. The installation transaction,
exact-instance control and retirement, writer-lease handoff, crash journal and governed installer activation
remain the adopter's separate obligations.

The internal `WatchWriter` adapter binds an inactive core and the real
`SingleInstance` writer lease to an explicit absolute state root. It requires
a caller-supplied authority validator, with no permissive default, before
adoption, activation, predecessor drain, post-drain release and resumption.
An incomplete validator result refuses the transition. The validator still
owes current installation receipts, governance, exact instance and transaction
authorization; constructing this adapter is not that evidence.

`adopt()` acquires the native lease before final reload and leaves effects
disabled. `activate()` authorizes effects separately. `drain_and_release()`
retains the lease on incomplete drain or post-drain authority refusal; a
release response alone is not proof that the successor acquired it.
An incomplete-drain `resume()` retains the original registry and accepted work.
After writer transfer, resumption requires caller-proven exact successor exit,
then actual native reacquisition and final reload before effects. Failed reads
and incomplete close keep native ownership accountable for recovery. Inherited
processes refuse before touching parent-owned state or locks.

Explicitly bound roots retain their own subscription state even if an ambient
legacy override changes; the adapter does not rewrite that override.
Ordinary no-context daemon construction and service commands are unchanged.
The internal explicit-context entry connects this adapter to authenticated
lifecycle control and completed-target installer activation through the
production installation-cell launcher.

`WatchInstallation` is the internal receipt/governance boundary. Its script
bootstrap and wheel carry generated, byte-identical canonical
installation-context primitives. Capturing a binding pins the exact plugin,
cell, payload, receipt generations and roots without provisioning watch state
or publishing discovery. Each mutation boundary revalidates that evidence
and the authoritative default-off installation-mode policy; disabled policy,
maintenance, missing activation, foreign environment, or changed receipts
refuse rather than select legacy state.

Its `transaction()` holds the canonical installation-local `CutoverLock`
across the caller's entire operation, including rollback or commit-forward,
and rechecks governance after acquisition. The shared orchestrator must borrow
that exact guard; startup and mutating reconciliation must use the same run
root. This binding alone supplies neither completed-runtime/process evidence
nor transaction authorization.

`WatchRuntime` composes that current binding with canonical immutable snapshot,
slot ownership and build completion evidence. A freshly governed action can
attest an older owned incumbent without treating the new payload as its loaded
runtime. Rewritten completion evidence refuses. `WatchInstance` pins runtime,
PID/native birth token and a random instance ID. Native exit-reference capture
opens a non-signaling exact-process reference, then brackets one actual
authenticated health exchange with fresh runtime and native-birth checks,
closing the reference on any refusal or observed exit. These are
evidence boundaries, not permission to promote or retire an owner.

The internal `WatchJournal` records random transaction authorization, exact
instances, prior marker/manifest/route bytes and the completed target separately
from ZDD's breadcrumb. Journal writes require custody of the installation's
canonical guard, including inside borrowed orchestrator callbacks. Recorded
drain/release/read-ready phases authorize bounded control intent; they are not
proof that control or native writer transfer succeeded. Commit requires coherent
target selection and an exact effect-free read-ready writer. Its durable commit
precedes effect authorization and changes recovery to forward-only, even if the
write acknowledgement or shared breadcrumb is lost. Rollback resumption requires
captured native successor exit and coherent prior metadata; terminal completion
also requires the appropriate active-writer and retirement evidence.
The journal brackets the actual health RPC itself before checking readiness;
its transport-only client callback grants no installation or process authority.
Ordinary client health retains its own complete validation bracket. Neither
path caches authority across an RPC or extends the transaction deadline.

Rollback route restoration uses the public native routing CAS, not a raw
snapshot overwrite. The journal records the exact prepublication route under
that CAS before restoring the predecessor with a monotonically newer generation.
The shared sticky publication history remains intact, including a successor
that briefly became active. Recovery accepts only the attributable restored
route and unchanged prior markers/manifest; a matching PID or instance alone
cannot authorize resumption. Interrupted conservative-history publication can
be retried from that exact durable intent. Neither lost acknowledgement nor a
later same-instance reannouncement can erase or replace the recorded custody.

Receiver-side control validation requires the exact current process, runtime,
instance, transaction capability, permitted phase, current governance and the
native transaction guard held by the live recorded controller. Local callbacks
validate the actual held guard; remote receivers must observe native contention,
not merely a stale PID file. Recovery rotates the capability under that guard
before accepting more control.

Explicit-context stop uses separate private withdrawal custody referencing the
exact archived terminal owner; it is not a synthetic upgrade or PID-only route
cleanup. The caller holds the same whole-transaction guard, captures native
exit before authenticated shutdown, and proves both original-process exit and
actual writer availability before withdrawing the native route by exact CAS.
The owner revalidates stop authority after quiescing accepted work and before
releasing the lease. Pending subscriptions and deliveries remain durable.
Incomplete or ambiguous shutdown stays pending for reconciliation without
spawning another owner. A completed withdrawal retains publication history
and the stopped endpoint's generation witness; later cold activation or
on-demand start accepts that route only with archived exact withdrawal proof
and advances the generation. A reachable PID, stale rendezvous file or free
writer lease alone cannot provide that proof.

Disabled policy, stale receipts and route drift block ordinary control or
recovery rather than bypass them. Read-only canonical installation/build
identity is separate from permission to mutate. Restricted cleanup uses the
same native transaction guard and exact capability, accepts only unchanged
precommit selection, and checks that the actual child has no writer,
read-readiness or effects. Missing/default-off policy may permit this cleanup;
malformed policy, maintenance, missing activation or changed receipts do not.

The journal reserves a random successor identity before attributable spawning,
so a crash between spawn and ordinary binding still has exact cleanup custody.
Authenticated passive health can bind only that pre-reserved child to recovery,
never an arbitrary discovered process.
Recovery also reclaims the exact reserved launch endpoint when a controller dies
after binding that successor but before persisting its endpoint. It validates the
private launch receipt and fresh authenticated instance health before recording
endpoint custody; missing or conflicting evidence leaves recovery pending.
`cleanup-stop` is separately authorized
before and after passive close, and exit recording requires a non-signaling
native reference captured before control. After controller loss, strict shared
read-only original-exit observation can record the exact already-gone child,
including a private reserved launch that never reached ordinary binding.
Denied, unknown or unsupported native evidence never means exit, and policy-off
observation still cannot resume a writer or complete the transaction.
Native governed-owner identity and exit references currently support Windows
and Linux. On macOS, unavailable native birth evidence refuses governed startup,
and unsupported exit evidence refuses cleanup/recovery rather than guessing from
a PID or switching to legacy state. An absent-owner stop remains bounded;
ordinary no-context legacy commands retain their existing behavior.
Governed `restart --json` retains the legacy response shape:
`{"restarted": true, "was_running": <boolean>}`. The incumbent flag comes from
discovery under the same installation transaction guard; a failed or refused
restart emits no success response.
Cleanup preserves the pending phase
and prior selection; it does not claim rollback completion, resume a
predecessor, acquire a writer or enable effects. An adopted writer, published
selection or durable commit requires explicit governed reconciliation instead.

`WatchInvocation` additionally checks the actual interpreter's venv and
installation-local completed slot, the loaded package version, and all loaded
owner/shared-runtime modules' placement beneath that slot's installed library
roots. Receipt discovery or a rebound home cannot supply those proofs.
Current action bindings refresh management receipts in the already-known cell
while retaining the incumbent's immutable historical runtime identity and
original state/run roots; a fresh receipt cannot relocate an already-running
owner's writer or controller custody. The PR owner is registered as both a
same-cell caller and peer. Its authentication prefix loads the canonical
boundary and packaged validator from that runtime's validated immutable
snapshot, never a replaceable management payload or machine-global fallback.
Neither the in-process loader nor its bytecode-disabled bootstrap writes cache
files into the snapshot. Peer-launched PR commands receive the exact receipt's
state root rather than an inherited legacy override.

The internal `WatchControlOwner`/`WatchControlClient` adapter uses the existing
authenticated loopback TCP protocol, not a new HTTP service. Authority-bearing
requests derive their coalescing identity from their entire payload rather than
the caller's key. Different transaction, operation, capability, nonce or instance
values cannot join an already-authorized execution. Control refusals produce an
explicit sanitized response. Adoption acquires/reloads without effects;
ZDD's post-commit adoption hook separately activates effects. Only an authorized
terminal control closes the owner loop; writer quiescence leaves the listener
alive. Retirement acknowledgement is not native exit confirmation.

Private launch custody now records only the transaction's pre-reserved actual
child and authenticated loopback endpoint before controller binding, independently
of active discovery or the spawning controller's stdout. Readers validate the
known cell, immutable completed runtime, native instance and reservation; an
authenticated live/passive health check is still required before binding or
cleanup. Controller recovery rotates its capability without changing the launch
receipt. A reserved but unbound child blocks predecessor resumption and rollback
completion rather than being mistaken for an absent child.

Journal, launch and history capabilities are private to the OS account: mode
`0600` and current UID on POSIX, a protected owner/SYSTEM-only DACL and current
token owner on Windows. New Windows capability files explicitly assign that
token user before secret publication rather than inheriting an elevated
process's default group owner. Permission or link failures refuse rather than repair
foreign custody. Secret-bearing temporary files are protected before writing;
publication is exclusive for launch/history, atomic for journal replacement and
crash-durable. Endpoint tokens never appear in public route or health evidence.
Lifecycle preflight, capability lookup, control and confirmation share the
caller's remaining budget, including a monotonic whole-transport deadline.

Terminal transactions require explicit durable archival before a subsequent
transaction can replace the live journal. Pending, malformed or unarchived
custody is never overwritten. A new transaction retains the exact surviving
owner and endpoint; immutable history tolerates only recovery's controller,
capability and revision rotation. After a canonical receipt update, only
already-archived terminal history in the same known cell can seed the next
transaction. Current governance and immutable historical runtime proofs are
rechecked; ordinary recovery and all pending controls still require their exact
pinned receipt generations.

Stopping a restored predecessor derives its native route from the archived
rollback intent, including the restoration's incremented generation and
preserved publication history, rather than treating the pre-restoration route
as the restored state.

The explicit-context CLI now captures its physical completed runtime before
discovery. It uses private journal-bound endpoints, never the legacy rendezvous
or PATH peer authentication. With validated explicit context, `serve` ensures a detached
owner and returns; the reserved passive child retains the foreground listener.
No-context `serve` retains its foreground behavior. Governed `stop` uses the
exact withdrawal and native-exit contract above, never legacy shutdown.

`WatchDriver` delegates lifecycle sequencing to the public
`CutoverOrchestrator(drain_before_publish=True)` under the already-held owner
guard. Its adapters journal commit before effects or predecessor retirement,
confirm native exit before returning the shared retirement acknowledgement,
and verify the actual surviving writer before completing and archiving.
Native publication intent retains exact generation and known promotion history.
For the first native publication, the adapter captures the routing-owned epoch
under guarded exact-endpoint validation rather than inventing an epoch or
rewriting native history. Availability-driven reassertion can advance only the
same effect-free, read-ready successor before owner commit.

Pending reconciliation never spawns a replacement. Committed recovery activates
only the known read-ready target and retires only the exact recorded obsolete
instance, or uses strict read-only original-exit observation after it has gone.
An interrupted first native write can finish the exact reserved publication
from its conservative promotion history, without spawning, discarding history
or inventing a new generation. Foreign or missing history refuses that retry.
Precommit cleanup stops and proves the known successor's exit before restoring
selection and resuming a predecessor. An unresolved reserved launch, uncertain
native identity, foreign metadata or changed governance stays pending.

`WatchSelection` composes a held owner transaction with the canonical completed
runtime-slot CAS and nested genesis/install metadata locks. It writes the
journal's exact nonsecret manifest bytes separately from native service
routing. Interrupted current/last-known-good/manifest publication may retry
only captured prior or target metadata. Precommit rollback restores the exact
prior bytes; a committed cold selection repairs only its known target forward.
Unknown metadata, governance changes or lost guard custody refuse. This
metadata helper does not discover or prove incumbent absence: the installer
must establish that under the whole transaction guard before choosing the cold
path. Its cold tests start no process, publish no route and create no watch
state. On-demand startup uses the same guard, reserves its child before spawning,
and commits only after writer acquisition and native publication.

The explicit installer adapter dispatches before legacy staging, locks or
writes. Cell operations require `-Context` / `--context` and
`-ExpectedMarketplaceId` / `--expected-marketplace-id`; an ambient context
variable alone is not write authority. `-InstallDir` / `--install-dir` and
force-rebuild options cannot be combined with a cell request.
`snapshot-stamp`, `snapshot-validate`, `slot-provision`, `slot-validate`,
`slot-complete` and `slot-completion-validate` delegate only their named metadata
operation to the canonical installation-context API. Snapshot stamping also
requires both expected receipt generations; slot operations require explicit
snapshot and runtime identities.

`snapshot-prepare` constructs immutable input without starting a runtime or
owner. A development checkout uses that checkout's canonical release
materializer to expand local libraries, installer-engine references and
packaged launcher assets; a published standalone payload must already contain
them. Receipt generations and source content are rechecked before publication.
The snapshot name includes its content digest. Atomic directory creation
claims production, final bytes are checked, and canonical provenance publishes
completion last. Existing incomplete, changed or unattributed snapshots refuse
without overwrite; only the invocation's unexposed staging directory is
discarded on failure.

Cell `provision` constructs the owned version slot using the shared installer
engine's venv and package-install helpers. It never selects the runtime or
starts an owner. An explicit prepared snapshot remains supported; otherwise
`provision`, `install` and `update` prepare their own snapshot and derive the
runtime version from the owning payload. The snapshot must include local shared
libraries and the installer engine; an explicitly supplied raw development-tree
snapshot is refused. Incomplete, nonempty builds require explicit repair, not
in-place overwrite. A completed slot is validated and reused.

Package construction sources the shared engine from its disposable payload
copy and uses that copy as its working directory, keeping dependency-policy
source attribution aligned with the packages being built. The policy owns
its private retained inputs under the inherited temporary root; nesting those
inputs inside an already deep version-slot path can exceed native Windows
subprocess path limits.

Cell `install` and `update` invoke `agent_pull_requests.watch_install` in that
actual completed target and await its bounded, journaled lifecycle result.
Bounded construction helpers retain their kill-on-close jobs; activation does
not add another construction job around the durable owner. The caller's outer
containment and the canonical daemon primitive's contained-test suppression
remain intact. Native target failure and malformed completion output refuse
activation rather than reporting success. Its physical runtime, explicit
snapshot/version, current receipt and management payload must agree. Activation acquires the
whole native owner guard before metadata locks. With verified writer absence,
it selects only target markers and a matching manifest and archives the cold
transaction; it publishes no route and creates no watch state. A known live
incumbent uses the same governed cutover driver as on-demand startup.
The installer captures one 300-second activation envelope before binding its
physical target. The initial owner cutover retains its 120-second limit. Only
a timeout from that attempt's own committed or complete transaction with an
exact recorded successor permits one reconciliation attempt. The original
timeout remains visible on stderr. Under a fresh native transaction guard, the
installer checks the unchanged transaction, installation, target, selection
and authenticated live writer before rotating controller custody through the
normal journal recovery path. Reconciliation retains its own at-most-120-second
limit and consumes the original envelope; it never begins a transaction,
spawns a daemon or invents a generation. Ready requires terminal archive,
native predecessor exit and final exact-owner health before that original
deadline. Changed identity, unconfirmed health, failed recovery and all other
activation errors still fail. Successful recovery adds `reconciled: true` and
`initialOutcome: "activation-timeout"` to the existing ready result.

Normal payload bootstrap uses native installation status before selecting
Python. With enabled governance and genuinely absent legacy state, canonical
receipt stamping and generation-pinned activation establish the fresh cell.
Existing unactivated receipts, legacy state, migration requirements, invalid
policy or missing provenance require an explicit decision rather than implicit
repair. An active cell builds through its own installer; cold bootstrap starts
no owner. The session-start admission wrapper leaves the shared legacy
reconciler unchanged and never runs it for a governed cell. Cell construction
and activation remain explicit/on-demand, not a new background reconciler.

Commands require coherent current/last-known-good markers and a matching
schema-3 manifest, canonical slot ownership, immutable snapshot provenance and
build completion. Payload bootstrap may provision an entirely absent selection;
partial/corrupt selection refuses without scanning for a different slot.
Direct completed-runtime peer entry uses the same selection admission before
ordinary command handlers and never provisions or accepts an LKG-only fallback.
Reserved passive startup instead requires its existing journal authorization
before publication; it is not an ordinary-command bypass.
An older owned, completed selected runtime remains usable under current
management receipts until an installer-driven update selects its replacement.
The follow-on command preserves the caller's outside-payload working directory,
argument vector, standard streams and native exit status. Context-bearing
invocation requires an existing Python 3.10+ interpreter; this prerequisite does
not affect native legacy first-touch installation.

A live lease-only holder without
verifiable same-cell ownership blocks activation, even when its legacy endpoint
is reachable. PID text and an endpoint token do not license installer shutdown
or fabrication of a governed instance. There is no automatic lease-only
migration or new one-time authorization surface.

## Current constraint

The current GitHub implementation still shells out through:

`agent-worktrees repos gh <owner/repo> -- gh ...`

That means this plugin currently depends on an available `agent-worktrees`
runtime plus its GitHub account-resolution logic. An explicit `owner/repo`
target does **not** require a local checkout, but unregistered repos still
rely on `agent-worktrees`' owner-to-login mapping or owner fallback, so an
org-owned repo whose GitHub login differs from the owner may still fall back
to ambient `gh` authentication until a dedicated account-resolution layer
lands here.

See [docs/cli-reference.md](docs/cli-reference.md) for full verb documentation.

## Native validation bounds

The source-qualified installed-closure fixture preserves separate native
stamping, fresh construction and reuse transactions plus the final import
after removing the source payload. Its Windows PowerShell parameter has a
45-second item bound: an unchanged-bound reproduction timed out at 30 seconds,
while stage-observed execution completed in 26.92 seconds and was still doing
native source verification near the former cap. Only that native parameter
gets the additional margin; the other parameters, subprocess deadlines,
group/plugin budgets, production code and all assertions remain unchanged.
