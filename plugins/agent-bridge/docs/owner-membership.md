# Passive owner membership

The running Bridge advertises `bridge-owner-membership-v1: 1` only alongside
the owning `observe-owner` parser and authenticated `/api/v1/owner-observations`
GET handler. Existing session observation/history, raw SSE and delivery cursors
retain their contracts.
HTTP generation 52 introduces this endpoint
(`OWNER_MEMBERSHIP_PROTOCOL_VERSION`); the minimum supported HTTP generation
remains 1. Its named capability and request/frame contract remain version 1.
Clients negotiate that exact named capability against the running daemon;
older daemons without it refuse before opening the feed. Previously introduced
session observation/history capabilities retain their independent identities.

```text
agent-bridge observe-owner --owner-ref HOST/PROJECT/WORKTREE \
  --contract-version 1 --json [--follow] [--after POSITION]
```

Invoke the attributed payload command, not a guessed runtime interpreter.
Both payload gates classify this read, including the root parser's project/JSON
abbreviations, before first-use publication/provisioning. The Python handler uses
the existing client with `ensure=False`. An old receiver without the named
capability is unsupported; there is no fallback to raw SSE or a new listener.
Optional root `--project` must match the selected owner's project.

## Authority and privacy

Existing Bridge HTTP authentication grants the established read scope. An owner
reference is a selection, never authentication or an external claim. An unknown
owner with no existing source records is unavailable, not an empty complete set.
Session-qualified owner references use the existing canonical-owner rule.

The projection contains only safe identities, native stored phases and explicit
qualifications:

- Ordinary owned-session caller links are reported, not external custody proof.
- Managed remote ACP relationships retain their stable relationship ID, phase,
  native conversation identity and uncertainty. Only a validated retained
  authority receipt qualifies confirmed relationship retirement.
- Represented supervisor links are reported registration facts. Process
  incarnation fields remain reported; no PID probe or actor connection is made.
- Arbitrary federated handles have no passive adapter in this capability.

No whole target/relationship DTO, protected producer token, encoded claim
reference, account/endpoint configuration or private checkout path is published.
Canonical head, liveness, freshness and current answerability remain unknown.
This capability neither transfers custody nor answers input; pre-compaction
active capacity is not inferred or changed.

## Producer and lifetime boundary

Audited canonical membership producers mark a cheap mutation revision only after
successful commit: session creation/status/target/native-ID/end, managed
relationship phase changes, and represented registration, alias/incarnation,
deregistration, expiry, takeover and purge. Explicit transactions remain
canonical; the connection's stock context-manager/script outcomes and errors are
preserved. Dedicated SQLite tests cover `commit`, C-level context exit,
`executescript`, rollback and scripts that commit before a later error.
Rolled-back changes do not become membership events.
Existing-only writable adoption uses the same connection hook without creating
or migrating a database, starting its writer, changing its lock timeout or
granting serving readiness. Read-only owners remain read-only.

Membership observation is allocated lazily against the existing serving manager
and database. No optional membership query runs in the canonical write critical
path. Commit notification changes only an in-memory revision and schedules at
most one coalesced callback per bounded observer loop. Event-log batches/rebuilds,
cursor, usage, prompt and control-only writes do not dirty membership. Ordinary
internal `execute_write` calls default to no membership notification; producers
changing projected membership fields explicitly opt in.
Registration refreshes compare the metadata already read for canonical
incarnation admission. An unchanged full or ID-only heartbeat updates its lease
without dirtying membership or waking readers; registration, metadata/status
changes, reservation inheritance and predecessor folding still mark dirty.

Initial and dirty records are captured using a separate existing-only
`mode=ro`, `query_only` SQLite connection, never the canonical connection. One
coalesced capture is shared by the observed owners/readers; there is no daemon,
periodic list scan or per-panel poller. Intermediate canonical transitions can
coalesce. Every change remains an observed difference, not a claim that all
canonical transitions were retained.

The private capture bounds physical query execution, not just returned rows:
100,000 SQLite VM steps shared across the capture, a 100-step progress interval
and a 50 ms cooperative elapsed read deadline, including connection, filesystem,
query and materialization work. Private connections have zero SQLite
busy timeout. The deadline uses `time.perf_counter` at creation and every check:
its high-resolution monotonic elapsed clock includes blocking and scheduling,
without `time.monotonic`'s coarse CPython 3.12 Windows tick quantization. This is not
a CPU-time budget and does not exclude time spent waiting. Native
value-length limits apply where supported. Python
materialization checks the same budget between bounded records. No schema/index
preparation occurs, and no canonical progress handler or validator is replaced.
Source file identity, database authority, serving generation and mutation
revision are fenced before materialization; a raced or exhausted capture is an
explicit gap/unavailable read, not an empty complete set.

Ordinary writable database bootstrap prepares three expression indexes matching
the reader's exact canonical-owner predicate across owned sessions, relationships
and represented registrations. JSON-backed indexes exclude malformed JSON;
session-qualified references retain the same owner normalization. Selected reads
need not scan unrelated owners' retained rows. Read-only opening, existing-only
writable adoption and passive capture never prepare these indexes. Older or
unprepared stores retain bounded scans and explicit budget failures; no new
schema version, capability, prerequisite command or reader retry is required.

Changes are qualified `observed-change`, not reconstructed native claim events.
The projection is auxiliary, in-memory and serving-generation scoped. It is not
a persistent relationship/claim ledger, a durable lifetime archive, or a claim
that independent legacy writers were captured.

An optional publication failure cannot roll back or fail the completed canonical
commit. It is surfaced without source contents and invalidates the affected
observation scope (or all membership scopes for a general capture failure), and
produces an explicit gap. Failed coverage is not silently repaired by a duplicate
acknowledgement; this generation stays unavailable. A later serving generation
can bootstrap current observations without claiming the missing history.
Budget/race failures can be retried on later read demand without replaying work;
the retained coverage-gap qualification is not erased by a successful current
capture.

Discovery does not increment session subscriber counts or postpone idle reaping.
Canceling one reader removes only its membership waiter. Other membership and
session observers, actors, claims and hosting transports remain owned by their
existing custodians. Closing/replacing the serving generation ends its feed.
Each stream also holds one scope reader reference through completion/cancellation.
At the 16-owner bound, admission reclaims only an inactive cached scope; an owner
with any active reader cannot be reclaimed. Re-admission establishes a new
retained-floor barrier, so a cursor from its reclaimed scope requires a fresh
partial snapshot, even if no canonical records changed while it was absent.
That barrier advances only the projection position: it does not mint a membership
change or imply any canonical milestone. Other admitted scopes keep their replay
coverage until actual ring eviction.

## Frames and bounds

The strict query requires `owner_ref` and `contract_version=1`; only `follow`
and an opaque `after` position are optional. Duplicate/unknown fields and
malformed versions are rejected.

- `snapshot`: current safe members, replay barrier and partial bootstrap.
- `delta`: required base position plus ordered upserts/tombstones.
- `gap`: source replacement, retained-floor violation, overflow or publication
  failure; never an empty/complete success.
- `heartbeat`: connection evidence only.
- `going-away`: hosting lifecycle, not resource retirement.
- `error`: unavailable/unadmitted selection or malformed source.

Positions include exact owner, the existing database relationship-authority ID
and actual hosting generation/journal incarnation.
The generated membership sequence is a projection position, not a session event
ID or canonical claim admission number. Restart requires reconciliation.
Publication timestamps are local observation labels, not cross-host causal order.
Unrelated-owner changes can advance the private scan cursor without emitting a
delta. The delivered cursor advances only with an emitted frame; subsequent
deltas splice from that exact delivered position, including after a heartbeat.

The source retains at most 16 observed owners, 64 canonical records per owner,
256 change entries and 1 MiB of ring bytes. Sequence exhaustion at the signed
64-bit limit explicitly withholds the projection without failing canonical work.
Snapshot overflow is explicit; records
are not silently clipped into a complete-looking set. Ring eviction advances the
retained floor; an old position receives a gap while a fresh snapshot remains
partial. Bodies/frames retain the existing 32/35 KiB bounds, 64-change delivery
limit and 15-second I/O silence budget. Unexpected EOF is unavailable.
Dirty-notification fan-out is bounded to four observer loops and 64 waiters;
fan-out executes on those existing loops, not in canonical writers.

Registration disappearance, registration expiry/takeover, local session
retirement, local relationship admission retirement, unreserved closure and
confirmed relationship retirement remain distinct. A tombstone never grants
claim release. Closed/unadmitted historical requests do not become current
pending input.

## Source validation

`tests/test_owner_membership.py` exercises actual canonical SQLite producers,
bounded journal/wake behavior, authenticated HTTP and the owning root CLI,
transaction rejection/rollback, immutable capture, retirement distinctions,
token exclusion, source/owner/retention fences and independent cancellation.
The attributed payload tests exercise the new verb through both shell gates
with inert missing-runtime/publication/installer spies.

`tests/test_owner_membership_budget.py` adds structural writer-path assertions
and real SQLite VM-work controls for large unrelated history and unknown owners,
unrelated EventLog commits, private progress-handler isolation, coalescing and
actual explicit transaction paths. These controls do not substitute a row-count
limit or a wall-clock benchmark for physical query budgeting.
An indexed control includes 10,000 unrelated rows in each registry, selected
session-qualified owners, malformed JSON and an absent owner under one VM budget.
An old-store control retains bounded failure without read-side preparation and
verifies ordinary writable reopening preserves records while preparing lookups.

The current HTTP-52 capture portfolio retains its existing owner, family and
generation; historical HTTP-51 captures are not rewritten. Source tests and prepared
declarations are not source-bound published evidence, installed capability or
native-platform qualification.
