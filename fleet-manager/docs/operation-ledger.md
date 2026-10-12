# Local operation journal - explicit persistence boundary

Back to [operation contract](operation-contract.md) and
[Fleet Control V2](../../efforts/active/fleet-control-v2/README.md).

`fleet_manager.ledger.OperationLedger(path, authority=..., installation=...)`
provides the first explicit local admission/receipt persistence primitive.
The caller supplies a database path; there is no ambient state discovery,
installed CLI mutation, Gateway service, task scheduler, executor or V1 import.
Do not point it at existing production state or adopt it as an authorization
service merely because the class is available.

Admission commits validated immutable intent, one pending outbox row and audit
event together. Identity is principal/account/operation inside the bound
authority/installation. Repeating identical content returns the original row;
conflicting intent fails. Separate SQLite connections serialize concurrent
admission using short `BEGIN IMMEDIATE` transactions, five-second contention
timeout, foreign keys and FULL synchronous commits. No external effect executes
under the transaction.
Inspection uses a deferred query-only transaction rather than reserving a
writer slot, and sees a coherent committed snapshot while another connection
holds a reserved write transaction. It can still be blocked by an exclusive
storage lock; this is not a lock-free availability guarantee.

Receipt recording requires an admitted exact intent and correlated scope/digest.
The receipt, outbox status and audit commit together. Identical sequence/content
is idempotent; lower/conflicting sequence is refused. A terminal succeeded/failed
receipt cannot be replaced. UNKNOWN becomes `uncertain`, not retry-ready or
successful; it cannot regress to acceptance. A later target-authenticated
reconciliation may supply an outcome, but the caller must establish that evidence.
The journal itself verifies shape/correlation, not target attribution.

Outbox states are `pending`, `accepted`, `uncertain`, and `outcome`. Outcome is
not parent-objective completion, claim release or resource settlement. The
primitive has no dispatcher, automatic retry, lease timer or reassignment API.
`inspect(intent)` returns persisted state or null for an unadmitted identity;
it never probes a remote target.

Schema and authority/installation identity are checked when opening and inside
every operation transaction. Unsupported versions and unrecognized databases
are refused; no migration or stale backup restoration is performed.
Only explicit construction may create a database; subsequent verified
transactions open the resolved path in existing-file mode and fail without
recreating storage if it disappears.
SQLite/storage exceptions propagate explicitly. Paths and underlying files
must remain under a trusted deployment owner's control on supported local
storage; this is not hostile-client or network-filesystem isolation.

**Not yet delivered:** durable node-side effect journal, target authentication,
allocation claims and their effect-owner fences, bounded queue/audit retention,
authoritative generation transfer, subscriptions, Gateway/node protocol,
installer readiness or high availability. The current journal is a local
building block in one product, not a second deployed queue beside V1.

Tests prove real separate-process committed admission, abrupt process exit with
an uncommitted transaction, concurrent duplicate clients, admission/receipt audit
failure rollback, replay/order refusal and identity/schema rejection. They do not
prove power-loss durability, arbitrary Git/SSH/prompt exactly-once, multi-machine
partitions, credential security or live service cutover.
