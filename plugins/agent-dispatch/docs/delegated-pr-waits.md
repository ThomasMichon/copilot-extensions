# Delegated PR waits

`run` remains an arbitrary-command process waiter by default. With both
`--detach` and `--task`, explicit PR options can replace that process with a
subscription on the optional, already-ready `agent-pull-requests` watch owner:

```sh
agent-dispatch run --detach --task TASK --resume MACHINE/WORKTREE \
  --pr-watch-repo example/project --pr-watch-number 7 \
  --pr-watch-timeout 3600 \
  -- agent-pull-requests wait --repo example/project --number 7 --timeout 3600
```

Keep an **actual blocking command** after `--`, never a placeholder. If
delegation is unavailable, exactly that command runs in the ordinary detached
waiter. Foreground runs and detached runs without a task remain unchanged.
No automatic rewriting of `pr-watch` commands is performed: cursor-based
review-verdict filters are not equivalent to the watch owner's transition
contract.

The default delegated transitions are `merged` and `closed`. Repeat
`--pr-watch-until` to choose from `merged`, `closed`, `review_changed`,
`mergeable_changed`, and `checks_changed`. These are the watch owner's vocabulary,
not `pr-watch wait --since ... --until approved,commented` syntax. The initial
nonterminal snapshot establishes a baseline; changes then fire. Already-terminal
PRs fire without waiting for another snapshot change.

`--pr-watch-timeout` is finite and positive, defaults to three days, and is
bounded at 2147483 seconds. A timeout wakes with an explicit warning to re-check
external state; it is never reported as a successful PR transition. Unlike the
ordinary process waiter's repeated-124 backstop, the explicitly selected
delegated deadline is a single observation deadline.

## Negotiation and credentials

Dispatch negotiates `delegated_run_waiters/v1` from the actual coordinator's
`/health` and `acknowledged_notifications/v1` from the actual PR watch owner.
The optional owner's command resolves through the installation cell/receipt or
canonical runtime slot, **never PATH**. Missing installations and incompatible or
not-ready owners affect only the optimization; no inert manifest dependency is
needed.

| Condition | Decision |
| --- | --- |
| No explicit PR options, foreground run, or no task | Ordinary process behavior (PR options require a detached task). |
| Optional plugin absent, daemon unavailable, or either protocol unavailable | Existing process waiter; legacy prepare schemas receive no new fields. |
| Explicit `--token`, `--control-token`, routing override, `--shared`, `--local`, or task-only context | Existing process waiter; effective CLI credentials remain in its environment, not callback argv. Task-only command restrictions still apply. |
| Explicit task authority (`AGENT_DISPATCH_TASK_AUTHORITY`) or WSL Windows-client routing | Existing process waiter; a resident PR owner cannot attest to the caller's routing environment. |
| Credential environment variables, including command-backed token configuration | Existing process waiter: this owner protocol cannot attest that a resident daemon inherited the caller's environment. Command-backed auth still uses the existing resolver in the process child. |
| Explicit `GH_HOST` or `GH_CONFIG_DIR` | Existing process waiter, before probing: a resident owner may use a different forge or account configuration. |
| Explicit `AGENT_PULL_REQUESTS_HOME` | Existing process waiter, before probing: coordinator recovery cannot reconstruct a caller-only watch-owner home. |
| Installer-owned stable callback entry unavailable | Existing process waiter, before preparing a delegated generation. |
| Custom `--message` or credential-bearing command options | Existing process waiter; do not durably copy custom text or credentials into a subscription. |
| Default local ambient context, or exactly canonical same-cell routing, and both ready protocols | Transactional delegated preparation, acknowledged subscribe, then arm. |
| Uncertain prepare, subscribe, or arm after preparation | Never launch a second owner. Reconcile the exact preparation/registration where provable; otherwise abort that preparing generation with a durable wake, or leave it to visible recovery. |

The callback carries only the task, waiter generation, nonsecret subscription
identity, and coordinator identity. It resolves dispatch through the ordinary
command-backed/ambient CLI authentication mechanism with autostart disabled.
It never serializes credentials or arbitrary environment values. Conservatively selecting
process fallback for a caller's credential overrides is intentional: the
acknowledged-notification capability does not prove credential-context parity.
Delegated queue records intentionally omit the blocking command and use a
fixed PR-target audit note, so arbitrary command arguments cannot become a
durable credential store. The original command remains available in the caller
for pre-prepare process fallback.

Same-cell subscriptions also retain the Dispatch installation's nonsecret root
and canonical receipt identity. The callback revalidates that receipt and current
governance through the canonical PR-owner-to-Dispatch peer boundary, then
reconstructs Dispatch routing before resolving its client; it
does not inherit the PR daemon's installation or client overrides. Only intrinsic
routing values that exactly match the canonical Dispatch receipt remain eligible.
Foreign, invalid or deactivated installation receipts refuse completion without
acknowledging the delivery or selecting a legacy coordinator.

Legacy callbacks also retain the subscribing Dispatch installation root, discard
the resident PR owner's Dispatch environment overrides, and reconstruct routing
from that root's ordinary configuration and rendezvous. Legacy owner commands
run the selected installed interpreter with `-I`; caller working directories
and `PYTHONPATH` cannot substitute another owner package.

## Durability, replay and recovery

Preparation suspends the task and records `kind=delegated` in the existing
waiter transaction. A unique preparation identity is bound to the task and
durable coordinator identity; the watch owner's registration receipt is bound
when that **same waiter generation** arms. A delegated waiter has no process
identity: it does not use a sentinel host or PID for liveness. The stored host
is the real observation host, so wake/claim cleanup retains its existing routing.

Subscribe requests `--acknowledged-notifications --notify-timeout 30`. The
callback reads and validates the owner's versioned event on stdin, including
event/registration identity, PR target, transitions and the all-or-none snapshot.
Completion uses the existing generation-fenced finish transaction and durable
wake outbox; no second task lifecycle or resume mechanism exists.

Exit zero acknowledges only an accepted completion or a provably settled
matching generation. A delivery before arm, coordinator outage, incompatible
coordinator, or identity mismatch returns nonzero. The watch owner retains the
event durably and retries with backoff. A remote commit followed by a lost ACK
is safe to replay; concurrent duplicates enqueue one wake. A late callback for
a superseded/cancelled matching generation can ACK without waking a newer one.
The coordinator identity persists across restore and normal rolling cutover.

The installer publishes a stable callback entry beside the installation's
canonical runtime resolver. Subscriptions store that entry and a hex-encoded
nonsecret envelope, not a Python executable or callback file inside a version
slot. Each delivery resolves the currently installed runtime without provisioning
or PATH lookup, so ordinary upgrade/garbage collection cannot strand a dormant
subscription on a deleted slot. The Windows entry uses the system PowerShell
host's positional-safe slash options; the event still travels unchanged on stdin.
Same-cell deliveries retain the receipt/governance checks described above.
Acknowledged delivery uses the PR owner's canonical owned-process runner on both
platforms, even with the ordinary forge provider. A timeout retires the complete
callback tree before the attempt returns and becomes eligible for replay; on
Windows this includes the PowerShell wrapper, Python and peer descendants.

The existing recovery sweep handles unarmed preparations after its arm grace.
After its two startup passes, that same coordinator-owned recovery task checks
active delegated waits every minute; owner loss after startup does not require
a coordinator restart to become visible. Process-waiter startup and prepare
recovery remain unchanged. Task and bulk waiter summaries expose delegated
kind, generation, PR target and observation deadline without callback credentials.
For active delegated waits, it checks the local owner's supported health/status
surface and reports missing subscriptions or unavailable/incompatible owners
after a two-minute setup grace. On another host, it cannot claim to have observed
local owner liveness; the finite observation deadline plus two-minute delivery
grace remains the backstop. Recovery retires the exact generation and enqueues a
warning wake through the same outbox, including claim cleanup. It does not
silently leave a permanently unreapable waiter.

An uncertain registration may linger at the PR owner until a transition or its
finite timeout delivers a callback. Once dispatch has settled that generation,
the callback safely acknowledges it. No unconfirmed unregister or second
registration is issued, and legacy owners are never allowed to downgrade pending
acknowledged records.

## Cross-platform source acceptance

The existing manual installation-context workflow has a bounded
`dispatch_delegation` selector for these contracts:

```sh
gh workflow run installation-context-acceptance.yml --ref <branch> \
  -f dispatch_delegation=true
```

It runs only the four delegation modules on Linux and Windows, without adding
them to required unit CI or starting unrelated native-owner scenarios. The cases
exercise real isolated coordinator listeners, owner subscription CLI and callback
processes, same-cell peer resolution, restart, outage, timeout and replay.
This is source integration evidence, not an installed native-account or fleet
deployment claim; those remain separate acceptance obligations.
