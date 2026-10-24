# S3 R1a: concrete admission choices

[Reviewed plan](inventory-freshness-plan.md) · [Episode](s3ab.md) · #7949

**R1a pure-storage/model admission recorded:** the dedicated JSON prototype and
exact two-file source/unit slice below are admitted, with direct docs/changefile
updates. No production storage activation, policy/default, consumer, cache/push
effect, transport or new shared helper is admitted. “R1a” means this CodeSpaces
storage/model phase, not another campaign's similarly named slice.

## Implemented private API refinements

`inventory_store.py` remains unwired. `Limits` and `Policy` have required
arguments and no defaults; the 1 MiB/1,024-row/4 KiB fixture is calibration, not
a production quota. Frozen models accept only immutable tuples; bounded JSON
decoding copies nested documents into those models.

The implementation makes the proposed CAS token explicit:

```text
VersionToken(source: SourceKey, epoch: str, revision: int)
Snapshot(schema_version, source, epoch, revision, accounts)
read_inventory(path, *, expected_source, limits) -> ReadResult
commit_inventory(path, candidate, *, expected_version, limits) -> CommitResult
apply_account_observation(snapshot, observation, *, limits) -> Snapshot
classify_status(observation, name, *, now_epoch, live_transport_epoch, policy)
    -> StatusFreshness
```

`epoch` is the cache creation epoch, not a credential revision or proof of a
live producer. An initial caller supplies a non-reused epoch with revision 1;
`expected_version=None` works only for a truly absent file. An update compares
source + epoch + revision under custody and requires candidate revision exactly
one greater with the same source/epoch. It does not increment again. Epoch
replacement is not an ordinary update. A post-write unknown result never rolls
back or retries; retrying the old token cannot advance the revision twice.

The pure classifier is per account-qualified venue, so different venues need
not share a transport epoch. The row's optional `disconnected_at` separates
known-disconnect display grace from last-seen time. No disk field restores live
transport authority. The result has only label, age, reasons and reconcile.

The reducer never deletes unseen/inactive rows. It merges positive rows from
partial/error observations; retained unseen or unusable rows make the merged
view incomplete, even if an incoming report was complete. Thus an empty or
uncertain report cannot turn retained evidence into a false empty inventory.
Source-qualified absence/control publication remains outside this model.

Paths are supplied explicitly; the sibling lock is `<data-filename>.lock`.
Absolute existing plain parents and plain targets are checked before custody,
under custody and before the existing atomic helper. Post-write custody,
parent/file identity and bounded decoded contents are checked; uncertainty is
typed unknown, without rollback. These checks do not claim protection from
every hostile filesystem race or add directory-fsync/power-loss guarantees.

The following recommendations remain **unselected production policy**.

## 1. Admitted minimal R1a scope

**Only two new plugin-private files:**

- `plugins/agent-codespaces/src/agent_codespaces/inventory_store.py`:
  immutable schema/types, explicit-field codec, bounded read, revision-CAS
  atomic commit, account-observation reducer and pure status-freshness classifier.
  One bounded module under the unchanged source-size guard; no generic
  provider/backend/cache framework.
- `plugins/agent-codespaces/tests/test_inventory_store.py`: pure/isolated unit
  contracts, with the existing OS-lock/atomic-write boundaries replaced in
  unit tests. Real multi-process/crash/Windows-file-sharing qualification is a
  later explicitly scheduled integration lane, not a claimed unit result.

The implemented private signatures above supersede the original integer-only
`expected_revision` pseudocode. `Limits` and `Policy` remain explicit; these are
not shared APIs or current runtime caller contracts.

All are synchronous/pure except explicit `read_inventory`/`commit_inventory`
I/O on the **caller-supplied path**. No import-time filesystem access, automatic
`RUNTIME_DIR` resolution, CLI/hook wiring, config/provider/token read, subprocess,
timer, transport, runtime activation or production fallback.

`ReadResult` distinguishes absent, valid-empty, corrupt, unsupported schema,
wrong source, oversized and I/O failure. `CommitResult` distinguishes committed,
stale revision, busy and refused/unknown. Neither failure returns authoritative
empty or success-shaped fresh data. `StatusFreshness` exposes only labelled
status/age/reconcile reasons—**no connect/renew/release permission**.

An account report preserves all known inactive/stopped rows. Partial/error/
limit observations merge positive rows without deleting unseen rows or granting
negative evidence. Confirmed absence stays source-qualified, not a global
cross-account verdict. Duplicate identities under different accounts remain
distinct. No age-based eviction of inventory or automatic history pruning.

The schema whitelists lifecycle/reachability metadata, account/source/producer
epochs, sequence, wall-clock observation/last-seen timestamps, completeness and
bounded error **codes**. It does not serialize the draft `InventoryScope`, whose
credential digests must not enter the new store. No credentials, tokens, hashes,
raw diagnostic payloads or transcripts. Missing external generations remain
unknown; a local revision is not an authentication/currentness revision.

### Existing primitives, not another lock/storage owner

Candidate eventual files are `inventory-v1.json` and `inventory-v1.json.lock` under
the owner's already-resolved runtime namespace, separate from eligibility and
hold stores. R1a itself accepts explicit paths and never resolves/creates that
production namespace.

Use public `single_instance_lease.OperationLock` at a dedicated inventory lock
path: OS-held exclusion plus Windows same-process/thread reservation; kernel
release on death; no stale-age unlink/reclaim. It is already a declared plugin
dependency. Do **not** copy `status._status_lock` or `_binding_lock` age-stealing
loops, take the owner/eligibility lock, or edit those helpers.

Under that lock: bounded strict reread, compare source/revision, validate the
candidate, assert custody, then public `dropin_registry.atomic_write_text`.
That existing declared dependency uses a uniquely named adjacent file,
UTF-8/LF, flush/fsync and `os.replace`, cleaning its own file on failure.
Rows and cursor/epoch commit in one envelope. No lock held across an await;
busy returns a typed result, not another polling loop.

The parent must already exist and be a real, caller-owned directory; reject
unsafe/linked paths and source mismatch. Preserve the old file on refusal.
This promises atomic old-or-new visibility, **not extra power-loss durability
that the helper does not provide**. Missing/corrupt state after restart is
unknown and re-fetchable. Persist wall time only; never resurrect a monotonic
effect deadline or connected-transport proof from disk.

### Bounds to choose, not product limits

Source facts: current listing requests 50 rows/account and treats a 50-row
qualified result as incomplete; there is no existing all-inventory cardinality
cap. The representative packet has retained cardinalities 12 and 102. The SSH
carrier already uses a 1 MiB frame and 4 MiB aggregate-buffer budget. Neither
the active-venue profile nor the listing limit is a storage truncation rule.

**Recommend option A:** 1 MiB serialized envelope, 1,024 venue rows total,
4,096 UTF-8 bytes per schema string, with bounded account/header collections.
**Option B:** 4 MiB / 4,096 rows / the same string bound if larger retained
inventories are expected. These are proposed input/serialization guards, not
vendor quotas, runtime venue limits or eviction permissions.

An in-memory, compact JSON shape with source/account provenance and timestamps
measured 20,196 bytes for 102 rows, 199,986 for 1,024 and 799,026 for 4,096.
This is synthetic schema-size calibration, **not runtime/native performance
or a worst-case string-size guarantee**. Independent byte/row/string checks
apply before commit; overflow returns too-large/unknown and preserves the last
file, never a truncated “complete” snapshot. The real consumer must surface
that failure and suppress dependent control decisions.

## 2. Concrete policy recommendations for review

Existing facts: owner reconcile **15 s**, availability listing TTL **60 s**,
Mux probe cadence **120 s**, idle shutdown **300 s**, draft Available-proof
age **30 s**. SSH's configured keepalive is 30 s with six unanswered probes;
do not claim disconnect notification itself is bounded by 30 s.

| Decision | Recommended option | Bounded alternative / tradeoff |
|---|---|---|
| Status-only fresh badge | 60 s, matching existing availability TTL; no remote work on local hits | 120 s halves the potential display-reconcile frequency but doubles status age |
| Inactive/stopped out-of-band reconcile | 300 s **on demand or while the existing owner is already running** | 120 s for faster external-start visibility at up to 2.5 times this reconcile frequency |
| Known disconnect display grace | 30 s of explicitly disconnected/last-known display; then current status is unknown, retaining the historical row | 0 s for immediate unknown; neither option preserves control/lease authority |
| Lost-channel due action | Invalidate control proof immediately; one source-qualified provider recheck at the next existing 15 s cycle or explicit action, no speculative SSH | Defer until explicit action for least background work, accepting slower recovery |
| Repeated recheck failure | Coalesce by source/account; 60 s cooldown while unknown; explicit refresh may request one coalesced attempt | 120 s reduces failing-provider load, slows recovery |
| Effect-proof age | Retain the existing maximum 30 s; do not raise it | Shorter is possible with greater provider work |

These are **recommendations, not selected defaults**. Timers use the existing
owner cadence only when work is owed. No due observations means zero remote
observation work; cache reads do not start/keep alive a daemon.

An absence of state-change events is normal and is **not** a disconnected or
dead-session verdict. Do not add a per-session heartbeat poller to make a
30-second push-silence rule true. Only an actual current transport/producer
contract can supply a bounded progress guarantee.

## 3. Status-only versus control publication

**Recommend the simplest first integration:**

- Local cache reads and accepted passive deltas publish only age-labelled
  status. They do not mint credentials, renew/release tenants, wake/reconnect,
  or mark authentication ready. Cursor persistence is not a lease heartbeat.
- Known stopped stays non-waking. Unknown, missing, disconnected or stale-known-
  active data cannot authorize observer reconstruction. A provider check may
  use the existing strict per-name `get_codespace_status[_with_account]` path;
  absence requires its actual source/account/completeness contract, not a
  missing row in a 50-row list.
- Every true effect/retry still reads fresh selected source/config/account/
  credentials and checks current owner/deadline. Control mutations retain
  expected-generation comparison under the existing owner lock.
- **Option F (recommended initial control rule):** retain full live candidate/
  config/credential validation **once per completed control-publication batch**,
  not per status read, push delta or tenant. No unauthoritative old credential
  digest or imaginary cheap revision. This preserves the reviewed contract
  while removing unconditional whole-provider loops from status consumption.
- **Option S (requires an explicit new authority decision):** validate selected
  source/account/credential plus qualified inventory before scoped control
  publication; unrelated account/credential changes no longer invalidate that
  batch unless detected by source reconciliation. This changes the previous
  full-all-account publication rule. It is not implicitly authorized by caching,
  plan merge or selected fresh authentication.

F retains extra work at real renewal/release boundaries; S can reduce it but
accepts a different cross-account revocation/change-detection boundary. Neither
option permits action on partial/ambiguous inventory, unknown selected-provider
authority or stale owner/tenant generations. Re-measure the actual resulting
callgraph; no historical numeric ceiling or comparison basis changes here.

## 4. Exact dependencies outside R1a

- `ConnectionManager.exec_command` starts an SSH child, and retries can heal/
  reconnect; Windows direct mode is not “use only this live channel.”
  `RelayChannel` offers only start/stop/is_alive. Neither supplies no-reconnect
  status observation authority.
- `acquire_carrier`/`subscribe` provide framing, bounded queues and replay but
  can start SSH and have no CodeSpace-status operation. Remote producer,
  endpoint, source/epoch/sequence, disconnect/gap/ACK behavior and ownership
  must be admitted before consuming push. No ACP-stdio injection, terminal
  stdout, new remote stack/service or transport borrowing.
- ACP liveness belongs to Bridge `Session.public_state/liveness_state` and
  `AcpClient.is_running/mark_transport_lost`; its adapter is a separate exact
  owner contract, not a tmux fixture.
- The targeted status helper currently obtains credentials through
  `gh_account.token_for_account`, which can reuse the legacy CLI token cache.
  It cannot simply be called “fresh selected auth.” Same-boundary fresh-token/
  environment plumbing is a later exact source-owner proposal; no shared
  credential helper is changed in R1a.
- All-inventory reconciliation beyond the current 50-row/account response needs
  the canonical listing owner's completeness/pagination contract. The store
  must retain unknown/partial coverage until then; R1a adds no alternative
  account selector or claim that first-page inventory is complete.

No live probe, agent contact or remote operation was used for this packet.

## 5. Pure work that can be admitted now; named next consumer

R1a can test schema round trips; absent versus authoritative empty; wrong
source/schema/clock; bounds; partial/duplicate preservation; CAS/busy/write
failure behavior; no secret serialization; explicit-policy freshness; and
restart not restoring connected authority. Use synthetic clocks/policies and
injected OS-I/O boundaries. No production config/default selection is needed.

**Next real consumer is R1b: `AvailabilityGate.stopped` together with
`SessionForwards.usable`,** replacing the existing listing cache and preserving
refresh-before-rebuild. It must handle unknown separately instead of treating
an empty down-set as permission to rebuild. Canonical `lifecycle` reconciliation
feeds the store; qualified owner waves later reuse that same owner, not a second
selector. Picker/namespace views follow only with their actual auth/display and
#5761 owner admission.

R1a explicitly excludes changes to existing candidate/config/credential,
lifecycle/resolver/picker, owner/probe/forward modules, shared libraries, Bridge,
SSH protocols, hooks/CLI/installation, settings or runtime activation. Admit
those later by exact hunk/contract, after choosing policy and F versus S.
