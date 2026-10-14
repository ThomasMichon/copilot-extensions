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

## Verbs

```text
agent-pull-requests status --repo <owner/repo> --number <n> [--json]
agent-pull-requests create --repo <owner/repo> --head <branch> [--base <branch>] --title <t> [--body <b>] [--draft] [--json]
agent-pull-requests merge  --repo <owner/repo> --number <n> [--squash|--merge|--rebase] [--auto] [--delete-branch] [--json]
agent-pull-requests wait   --repo <owner/repo> --number <n> [--interval <s>] [--timeout <s>] [--json]
```

`create` and `merge` shell out to `gh pr create`/`gh pr merge` with an explicit
`--repo`/`--head` pair so no local checkout or branch context is required;
`wait` polls `status` until the pull request reaches `MERGED` or `CLOSED`, or
the timeout elapses (exit code `3`).

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
The subscription's optional `--timeout` must also be a finite positive number;
expiration fires a durable `timed_out` event, even during polling outages.

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
for the reviewed governed-cutover design. **No installer or service currently
opts into it:** ordinary `serve`, stop/restart and callback wire contracts keep
their existing behavior. These Python methods are owner-adapter seams, not new
operator commands or evidence that installation-cell adoption is complete.

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
