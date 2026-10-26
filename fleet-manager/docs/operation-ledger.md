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

## Transactional allocation claims

`admit_allocation(intent, resource=...)` admits a `workspace.allocate` intent
and holds one identified resource in the same transaction as its outbox and
audit. Resource IDs use the existing bounded identifier shape (1-128 ASCII
letters/digits/dot/underscore/hyphen, starting with a letter or digit).
The caller must resolve a canonical resource identity and authorize the
intent/resource binding before calling. An arbitrary caller-supplied alias
cannot establish exclusivity over the underlying workspace.

A resource is exclusive across all operations, principals, accounts, targets
and generations in this authority/installation's ledger, not scoped by target.
An operation can claim only one resource. Repeating identical intent and resource
returns the original operation without new audit; changing either fails.
Resource contention fails without admitting an orphan intent/outbox/audit.
Claims never expire, and accepted, UNKNOWN, failed or succeeded receipts never
release them. There is no TTL-based release.

`release_allocation(intent, proof, resource=..., installation=...)` consumes
immutable `AllocationRelease` evidence from the cooperative node effect owner.
The caller must first authenticate and authorize the exact retiring
authority/target/node installation and intent/resource binding. Parsing/digest
correlation is not authentication. The proof's scope, generation, command,
content digest, resource and node installation must match; exact admitted intent
and currently held claim are required. Release evidence, held-claim removal and
an `allocation-released` audit event commit together, independently of receipt
status. An audit/storage failure retains the claim and leaves no tombstone.

`allocation_release(intent)` reads the accepted permanent proof or null;
the operation must be admitted. Exact release replay is idempotent even after
a successor claims the resource, and cannot release that successor. A conflicting
proof fails. Original allocation-admission repeats return the original operation
without recreating its claim. A new allocation must carry a generation strictly
higher than all recorded releases for that resource, regardless of target,
principal or account. This is an allocation generation, not a connector epoch.
Release rows store the generation separately with a verified
resource/generation-descending index; admission reads one indexed maximum,
not historical proof bytes. Missing or incompatible index definitions fail
on construction and inside operation transactions. A 5,000-release regression
checks the covering-index query plan and a fixed SQLite instruction budget.
No tombstone cleanup, automatic retirement, external/provider release or
distributed resource fence is supplied. A correctly implemented trusted
SQLite-only node retirement adapter is the sole producer this contract covers;
external resources require their actual effect owner to fence/retire them.

`inspect_allocation(resource=...)` reads local held-claim evidence (resource,
operation row ID and immutable intent), or null. Neither presence nor absence
is a signed grant, authentication, remote resource fence or permission to execute.
The existing `admit` API remains a generic journal primitive and does **not**
claim resources, even for a legacy allocation intent. Such an already-admitted
unclaimed operation cannot acquire a claim retroactively through
`admit_allocation`: it might already have dispatched. Safe allocation adapters
must use the combined admission API, never generic admission followed by a claim.
Migrating old unclaimed admissions requires separate explicit reconciliation;
schema upgrade invents no ownership from historical intent.

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
through 3 to schema 4, adding result cursors/claims where missing and an empty
release history; intent, outbox, receipts, audit, existing cursors and held
claims stay unchanged. Historical outcomes never invent releases.
Foreign table/object inventories (including a node journal) cannot be
adopted as an earlier schema. A live old-schema reader/writer refuses the upgraded version
rather than continuing as a second compatible writer. There is no downgrade,
backup restore, authority relocation or distributed writer migration.
Only explicit construction may create a database; subsequent verified
transactions open the resolved path in existing-file mode and fail without
recreating storage if it disappears.
SQLite/storage exceptions propagate explicitly. Paths and underlying files
must remain under a trusted deployment owner's control on supported local
storage; this is not hostile-client or network-filesystem isolation.

The opt-in [delegated enrollment producer](continuity-bootstrap.md#same-owner-enrollment-producer)
adds an explicit schema-five migration, `machine.enroll` claims and durable
accepted-policy floors to this same journal. Default construction and unrelated
schema-four operations retain the behavior above; generic admission cannot
substitute for that producer's exclusive transaction.

The separate explicit [node journal](node-journal.md) now owns local acceptance,
cooperative effect receipts and a result spool; it does not share this database.

**Not yet delivered:** target authentication,
external effect-owner fences, bounded queue/audit/release retention,
cross-host authoritative generation transfer, subscriptions, authenticated Gateway/node protocol,
installer readiness or high availability. The current journal is a local
building block in one product, not a second deployed queue beside V1.
The node journal additionally enforces held ownership for its cooperative
SQLite-only allocation effects; this does not fence an external workspace
or authorize Gateway-side release.

Tests prove real separate-process committed admission, abrupt process exit with
an uncommitted transaction, concurrent duplicate clients, admission/receipt audit
failure rollback, replay/order refusal and identity/schema rejection. Receiver
process tests cover lost replies after commit and abrupt exit before cursor
commit, plus atomic page rollback, two independent node spools and exact
scope/generation/digest denial. The installed two-node recovery test holds one
real node process at UNKNOWN while another commits its cooperative SQLite effect.
A separate receiver exits during or after the fast node's page transaction;
replacement receivers resume each stream's own durable cursor without duplicate
audit or effects. Explicit test evidence then resolves the original UNKNOWN
operation; elapsed time and receipt replay never authorize its retry. The fixture
uses local journals and a test-only resolution signal, not resident services,
network transport or authenticated target evidence. These tests do not
prove power-loss durability, arbitrary Git/SSH/prompt exactly-once, multi-machine
partitions, credential security or live service cutover.
Allocation tests additionally cover separate-process competing and duplicate
admissions, abrupt exit before claim commit, lost replies after commit, rollback
of either audit failure, known-schema upgrades and retained claims after every
receipt status. These are installed-package, local SQLite proofs, not distributed
resource exclusion or provider fencing. Release regressions add proof correlation,
atomic retirement/audit rollback, higher local generation transfer and installed
process crash/lost-reply/contention with no duplicate retirement or stale release.
