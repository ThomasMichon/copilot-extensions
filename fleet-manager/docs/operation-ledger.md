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

## Durable node result reconciliation

`result_cursor(target=..., installation=...)` reads the durable receiver
position for one explicit node spool identity, initially zero.
`reconcile_results(target=..., installation=..., after=..., entries=...)`
accepts a bounded page from the [node journal](node-journal.md)'s `results`.
The caller must authenticate the exact authority/target/node installation and
authorize its receipts before calling; this API does not supply a network trust
boundary. A node installation identifier is not proof of connector identity.

The receiver requires the supplied `after` to match its persisted position and
every entry cursor to be contiguous. Each receipt must correlate with an
already-admitted exact intent, including scope, digest and generation.
The complete page's receipts, outbox changes, audit and final cursor commit
together. Any invalid entry or storage/audit failure rolls the whole page back;
no unadmitted operation is created or skipped silently.
Pages contain at most 256 immutable `TargetReceipt` entries; this bounds one
transaction, not total retained history.

On reconnect, read this receiver's durable cursor and request the node's next
page from that position. A crash before commit rolls back the page; a lost reply
after commit leaves the cursor and outcomes together. An old or racing cursor
fails explicitly: reread the persisted position before requesting another page.
Do not blindly replay historical acceptance over an already-recorded outcome.
Receipts directly recorded outside the cursor path do not advance a spool's
position; adapters must reconcile that deliberate mixed-path state explicitly.
No cursor is an objective-settlement, resource-release or destructive-retention
acknowledgment. Different node installations retain separate cursor namespaces;
changing the supplied identity cannot make an old receipt fresh.

Schema and authority/installation identity are checked when opening and inside
every operation transaction. Unsupported versions and unrecognized databases
are refused. Construction transactionally upgrades this journal's known schema 1
to schema 2 by adding result cursors; intent, outbox, receipts and audit stay
unchanged. Foreign table/object inventories (including a node journal) cannot be
adopted as schema 1. A live old-schema reader/writer refuses the upgraded version
rather than continuing as a second compatible writer. There is no downgrade,
backup restore, authority relocation or distributed writer migration.
Only explicit construction may create a database; subsequent verified
transactions open the resolved path in existing-file mode and fail without
recreating storage if it disappears.
SQLite/storage exceptions propagate explicitly. Paths and underlying files
must remain under a trusted deployment owner's control on supported local
storage; this is not hostile-client or network-filesystem isolation.

The separate explicit [node journal](node-journal.md) now owns local acceptance,
cooperative effect receipts and a result spool; it does not share this database.

**Not yet delivered:** target authentication,
allocation claims and their effect-owner fences, bounded queue/audit retention,
authoritative generation transfer, subscriptions, authenticated Gateway/node protocol,
installer readiness or high availability. The current journal is a local
building block in one product, not a second deployed queue beside V1.

Tests prove real separate-process committed admission, abrupt process exit with
an uncommitted transaction, concurrent duplicate clients, admission/receipt audit
failure rollback, replay/order refusal and identity/schema rejection. Receiver
process tests cover lost replies after commit and abrupt exit before cursor
commit, plus atomic page rollback, two independent node spools and exact
scope/generation/digest denial. They do not
prove power-loss durability, arbitrary Git/SSH/prompt exactly-once, multi-machine
partitions, credential security or live service cutover.
