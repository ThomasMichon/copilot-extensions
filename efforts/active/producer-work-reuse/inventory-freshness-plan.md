# S3 amendment proposal: durable inventory and push-assisted freshness

[Effort](README.md) · [Previous authority contract](selected-authority-contract.md) · #7949

**Status: proposal; no cache, push producer, transport, credential or runtime
algorithm change is authorized by this document.** Exact grace durations and
action-admission policy require a recorded decision before implementation.
The retained S3 draft remains unqualified.

## Request and boundaries

**Public-safe summary of the new direction:** CodeSpace loadouts change much
less frequently than status consumers read them. Retain the last-known
inventory of **all** CodeSpaces locally, including inactive/stopped entries,
with whole-source timestamps and per-venue observations. Prefer state-change
notifications over an already-owned active SSH connection; reconcile missing,
expired, disconnected and out-of-band changes using an explicit bounded-
staleness policy. A cached active row must not accidentally wake a stopped
venue. This changes the previous no-durable-inventory assumption, not
credential freshness, source ownership or atomic mutation fences.

The representative adopter fixture has two accounts and 0/1/4/8 active venues,
one total Copilot session per venue, **ACP or Mux**, not both. Four-venue shapes
include balanced and single-account placement; eight includes balanced and
mixed-mode placement. These are configurable workload examples, **not GitHub,
product or runtime limits**. Retained inventory cardinality is independent.
The older three-account/four-mux/ten-venue packet remains historical stress
evidence, unchanged, not the representative workload or an acceptance ceiling
for the new design.

Future multiple-session venues use a venue-local Bridge + Worktrees aggregate
queried on demand by the host. That is a separate future profile and ownership
contract, not permission to deploy that stack, add per-session host pollers,
borrow transports or implement S3c/S4 now.

Everything below marked **proposed** is agent-recommended design for review,
not an already-selected implementation or grace duration.

## Source-grounded seams and ownership

| Existing owner/symbol | What exists | Proposed use / admission boundary |
|---|---|---|
| `agent_codespaces.lifecycle.list_codespaces`, `_list_codespaces_under` | Canonical discovery and account-tagged rows; ordinary flattened API | One source-owned reconciliation path; retain account/page/error provenance before flattening. #5761 discovery ownership remains; the narrow CandidateRead grant is not permission to replace this algorithm. |
| Draft `owner_inventory.OwnerInventory`, `AccountListing` in #8143 | Immutable per-wave rows, completeness/error/age checks | Project qualified evidence into a durable status envelope only after the amended contract is reviewed; do not persist its credential digests. |
| `owner_availability.AvailabilityGate.stopped` | In-memory TTL cache; refresh on forward loss, unknown returns no down-set | Reconcile its no-wake admission with the canonical inventory owner. Its current fail-open unknown behavior is not a sufficient cache authorization rule. Do not add another refresh loop. |
| `status.CodespaceStatus`, `_status_lock`, `_write` | Atomic JSON prune/reuse eligibility; absent means active; corrupt reads become empty | Reuse the **storage pattern**, not the file or its meaning. Eligibility is not provider lifecycle inventory; corruption/absence must not imply an empty inventory or an active venue. |
| `connection_owner.ConnectionOwner`, `RelayChannel`, `run_owner` | Existing on-demand singleton, owned forward lifecycle, active process indication | One cache writer/event consumer when already running; no new resident service or activation merely for a cache hit. `RelayChannel` exposes start/stop/is_alive, not a status stream. |
| `session_forwards.SessionForwards` | Mux observation and generation-fenced tenant mutations in the draft | Consume qualified inventory and route-specific observations; preserve cancellation, owner/tenant replacement, confirmed-only release and owed local push debt. |
| `ssh_manager.ConnectionManager.acquire_carrier`, `CarrierSubscription`, `carrier_requests.subscribe` | Owned carrier identity, bounded frames/backpressure, replay position and reconnect primitives | Candidate transport mechanism **only if the existing owner already admits that endpoint**. There is no CodeSpace-status operation here today; acquiring a carrier may open SSH. No speculative connection, new endpoint, unsolicited terminal output or ACP-stdio injection. Separate SSH/Bridge owner agreement is required. |
| `agent_bridge.session_manager.Session.public_state/liveness_state`, `acp_client.AcpClient.is_running/mark_transport_lost` | ACP frame/transport-derived liveness; disconnect is not proof of child death | ACP observation adapter is Bridge-owned. Return this exact dependency rather than model ACP as a tmux target or infer provider lifecycle from a live transport. |
| `agent_bridge.session_host.codespace_transport.codespace_launch_ownership` | Existing remote launch/claim/rollback ownership | Preserve that owner and exclusive launch rules; cache/push design does not reimplement ACP launch or recovery. |
| `resolver.CodespaceResolver`, `picker_pool` | Namespace and UI consumers of canonical discovery/authentication | Later read-only cache projections need exact discovery/UI owner admission; no duplicate candidate selection or silently cached authentication banner. |

No other owner was contacted. Read-only current open-PR file checks found
#8143 on the wave files; #5761's retained reservation still applies. Absence of
another PR is not an owner release.

## Proposed storage decision: one source-owned JSON envelope

**Recommendation for review: a dedicated versioned inventory JSON file under
the existing CodeSpaces runtime/state namespace**, not `codespace-status.json`,
Bridge's database, or a new global database/service.

The inspected local registry/eligibility stores already use cross-process
exclusive locks plus atomic replacement. The proposed access pattern is
whole-account reconciliation, frequent read snapshots, and infrequent
state-change deltas—not a high-rate event journal or relational query engine.
One existing owner serializes deltas, action receipts and scan commits;
ordinary local CLI writers use the same lock/revision comparison. Atomically
replace the **entire envelope**, including source generations and push cursor,
so rows and replay position cannot commit separately.

This is a material **recommendation, not a final selection**. Before accepting
JSON, validate stopped-inventory cardinality/serialized bytes, reader cost,
lock contention, crash recovery and change-burst write amplification. If
those require independent transactions/range queries or multiple sustained
writers, return a source-owned SQLite proposal with measured reasons; do not
silently add a database or piggyback on Bridge session storage.

Required envelope information:

- Schema version, writer/cache epoch and local snapshot revision.
- Per-account/source/installation/namespace identity and source observation
  generation; per-account scan time, completeness, page/limit/error provenance.
- All known venue identities, source account and available binding/provider
  **non-secret** provenance, provider lifecycle state, local reachability/session
  observation, their evidence source, `observed_at` and `last_seen`.
- Source/owner/venue generation and accepted push sequence/cursor where actually
  supplied. A local revision is **not** proof that an external account/config
  or credential stayed current. Missing source generations remain unknown.
- Explicit unknown, dirty/pending-action and tombstone evidence, with the
  authoritative source/generation of any confirmed removal.

No credentials, tokens, credential hashes, authentication material or full
transcripts enter this cache. Cache state and selected authentication are
different authority domains.

Persist wall-clock timestamps for restart diagnostics; never reuse persisted
monotonic values as deadlines. In-process admission uses a fresh monotonic
deadline. Restart, future/backward wall-clock anomalies, uncertain boot/owner
epoch or invalid schema remove live-transport/action authority until the
required reconciliation; disk corruption preserves diagnostic evidence and
returns unknown rather than authoritative emptiness.

## Proposed freshness and transition contract

Policy parameters are symbolic: `inventory_fresh_window`,
`inactive_reconcile_window`, `connected_observation_grace`,
`disconnected_recheck_window`, and a hard per-effect proof age. **No numeric
duration or authorization is selected here.** Zero-work reads must not start
an owner daemon merely to refresh a timestamp.

| Input / boundary | Required proposed behavior |
|---|---|
| Fresh local cache read, no due action | Return source-qualified rows with age/completeness labels; zero remote inventory requests. Do not run a recursive whole-provider credential loop to read status. A displayed row is not a control verdict or current authentication result. |
| Missing/expired cache, explicit inventory request | Coalesce one canonical source-qualified reconcile. Preserve per-account successes and failures; missing or failed sources remain unknown, not empty. A no-op owner poll with no due observations still performs zero remote work. |
| Inactive/disconnected out-of-band change | Bound its observation delay by the reviewed inactive/disconnected policy. On the next due/explicit reconcile detect external start/stop/create/delete/account change without requiring an SSH wake. Retain last-known rows while uncertain, visibly aged; do not erase them. |
| Successful create/start/stop/delete or rebind | Invalidate the affected account/venue generation and commit the operation's qualified receipt/pending state through the same writer. Reconcile visibility as required; a request/accepted operation is not proof of final provider state. |
| Failed/timed-out/uncertain action | Mark the affected scope dirty/unknown and require reconciliation before another dependent control decision. Never make a failed stop into Available, failed delete into absence, or silently roll back to a stale positive row. |
| Partial/page-limit/error scan | Retain positive observations with provenance, but do not infer absence or exclude duplicate ownership. The existing 50-row response limit is not completeness. Count additional pages/retries separately; no hidden extra discovery to make a count fixture pass. |
| Stale known-active row or transport loss | Do not open/rebuild SSH based on that row. Clear connected-observation authority and require the chosen provider recheck/no-reconnect contract before an observer effect. Unknown is not a false negative or permission to wake. |
| Fresh stopped/retained row | It remains in inventory; ordinary observation never connects to it. Explicit user start is a distinct existing lifecycle action, not implicit cache refresh. |
| Source/config/account/owner generation change | Invalidate affected proof and pending event epoch; no cross-account aliasing, replacement renewal or silent fallback. Source-owned fresh readers remain authoritative. |

### Push over an already-owned connection

**Proposed:** an authenticated, bounded, source/venue/generation-bound status
delta feeds the existing owner, not user terminal stdout. One active venue has
one current session in this profile; no host per-session periodic watcher is
added. Guest reachability/process observations must not masquerade as
authoritative GitHub lifecycle state or account membership.

Accept a delta only for the current transport/producer epoch and monotonically
advancing source position. Duplicate/older events are ignored without renewing
proof. A gap, overflow, unknown position, stale epoch, disconnected transport or
new writer generation makes the affected stream uncertain. Reconnect requires
a current source-qualified snapshot or valid replay/resynchronization **before**
granting action authority. A higher sequence from a different epoch is not
freshness. Commit cursor and rows atomically before acknowledging durable
progress; lost ACKs must replay idempotently.

Drain/cancel only owned work on disconnect or shutdown. An event does not
extend a replaced owner/tenant lease, clear missing credentials, or trigger
reconnect. Current SSH carrier primitives do not prove that a status producer
and endpoint already exist: the remote producer/operation, lifecycle owner,
flow control and no-new-wake behavior are explicit implementation prerequisites.
No new remote Bridge/Worktrees stack or service is installed by this plan.

## Reconcile the authority contract explicitly

The previous `selected_then_full_publication` contract remains the existing
implementation requirement until a reviewed successor is explicitly accepted.
This proposal changes **inventory freshness**, not credentials.

**Proposed separation for review:**

1. Local status/cache publication and accepted passive push deltas are
   observational and age-labelled. They do not renew/release tenants, authorize
   a wake, or claim fresh authentication; no full provider-reauth loop runs on
   each idle cache hit or status-only delta.
2. Every true wake-capable action/retry retains a fresh selected source/config/
   account and credential read through the current owning provider, plus owner
   generation/deadline checks. No credential cache or guessed revision is added.
3. Whether a connected source-qualified cached inventory can replace a fresh
   provider lifecycle read for a **non-waking** observation is an explicit
   decision. The current Windows direct-exec route can reconnect; `is_alive`
   alone is not a no-reconnect guarantee. Until a real owned no-reconnect
   contract is admitted, recheck provider state before that effect or defer it.
4. Full candidate/config/**credential** validation before **actionable**
   publication is not silently removed. Decide separately whether the successor
   uses bounded inventory proof plus fresh selected authority and source-owned
   invalidation, or retains that full-authority validation at real control
   publication boundaries. No opaque credential revision is invented. Unknown
   account/config/provider evidence still suppresses control publication.
5. Heartbeat/release continue to compare expected tenant generations at the
   existing owner's atomic mutation boundary; queued mirrors require their own
   fresh admission at actual execution. Cache reads and push acceptance do not
   bypass those fences.

The required decisions are: storage approval; freshness/grace parameters and
which actions they permit; status-only versus control-publication semantics;
no-reconnect/push endpoint ownership; and the full-versus-selected authority
rule for real control publication. Review of this plan alone must not be
presented as selecting missing durations or waiving a source owner's admission.

## Representative measurements and acceptance proposal

[Raw current-consumer packet](evidence/profile-run-01.jsonl) and
[manifest](evidence/representative-profile-manifest.json) characterize the
original consumer and retained draft. They are **not a cache implementation
measurement**, native CPU/latency qualification, or authority-equivalent
performance comparison.

The measured Mux route uses two accounts, one mux/session per active venue,
cold-due/warm-due/unchanged-not-due, CLI/keyring credentials, retained inventory
0/12, balanced and single-account placement, and an independently labelled
configured bridge-forward health consumer. That health probe does **not**
create a second Copilot session. Mixed-fleet frames measure only the Mux slice;
ACP effects and full mixed-fleet cost remain unmeasured, not zero.

| Mux venues, no configured bridge-health consumer | Original CLI cold/warm | Retained draft cold/warm |
|---|---:|---:|
| 0 | 0/0 | 0/0 |
| 1 | 8/6 | 47/47 |
| 4 | 26/24 | 152/152 |
| 8 | 50/48 | 292/292 |

All 288 final characterization frames complete their expected **Mux**
observations. Retained-row counts do not create extra active sessions.
Historical raw evidence is retained unchanged.

For comparison only, under the **previous uncached** selected/full contract
with two accounts and one mux per venue, ideal same-read reuse gives
`6 + 4B + 4C` CLI requests for `C > 0`, where `B` is the number of non-waiting
completed-result publication batches (`ceil(C/4) <= B <= C`). With one
minimum-sized batch this gives 14 for one venue versus its measured original
8/6, and 26 for four versus 26/24; eight venues with two batches gives 46 versus
50/48, before selected metadata, connection and retry overhead. Optional
configured bridge-health effects add `4C` to that optimistic bound.
These are **derived**, not measurements or the acceptance bar for the proposed
cache contract. The older 17-versus-13/10 calculation is not a new-profile fact.

**Proposed acceptance work after decisions and implementation:**

- Actual production-consumer local-cache hit: zero remote inventory work;
  separately account for local I/O, auth work and control actions rather than
  declaring everything free.
- Accepted push delta: zero full relists, counted event/ACK bytes, bounded
  storage writes, sequence/generation correctness and owned cleanup.
- Fresh due action: count the actual route's selected reads, provider calls,
  config/connect/exec/retry effects, and any required publication authority.
- Stale-expiry reconcile and disconnected/inactive out-of-band change: count
  canonical discovery, pages, credentials and retries; prove detection within
  the approved grace without waking stopped venues.
- Separate ACP-only, Mux-only and mixed fleets. Obtain Bridge's actual ACP
  adapter before measuring that route; never use tmux as a mock ACP workload.
- Independently vary stopped/retained cardinality, crossing actual page limits;
  retain duplicate ownership, unknown, malformed, provider-error and expired
  proof controls. Failed/partial/omitted observations cannot qualify savings.
- Match before/after authority and workload explicitly. Keep total-work
  comparison requirements; obtain adjudication of any residual acceptance
  conflict rather than silently substituting a cheaper comparator or old
  numeric ceiling.
- Native CPU/tails/running-state and release/installed qualification remain
  separate. Saturated-host characterization is work-count evidence only.

## Proposed implementation sequence and minimal file scope

1. **Review this plan; record policy and exact seam decisions.** Keep #5761,
   Bridge ACP/session, SSH transport and S4 owners intact. Obtain admission
   before their files/interfaces change.
2. **Storage and pure freshness model only:** proposed
   `agent_codespaces/inventory_store.py`, focused tests and source-owned config
   policy declarations. Strict parser, atomic snapshot/cursor, non-secret
   provenance, restart/clock/partial/error tests; no production default switch.
3. **Canonical reconciliation and read projections:** qualified listing/wave
   adapter, existing `owner_availability.py`, exact admitted lifecycle mutation
   invalidation points and later resolver/picker consumers. One selection and
   refresh owner; no second global registry or config/provider algorithm.
4. **Owned active-transport status delta adapter:** only after the actual
   SSH/Bridge endpoint/producer contract is agreed. No new daemon, session or
   unsolicited output. ACP and Mux remain distinct consumer routes.
5. **Generation-fenced action integration and qualification:** source/owner/
   tenant replacement, selected credential/provider failure, disconnect,
   replay/gap/backpressure, cancellation and no-wake controls; full revised
   representative workload, native lane, normal review/CI, then source merge.
   Release and authorized installed qualification are separate.

## Validation Plan

- [ ] Review the amended inventory/auth/control-publication contract and record
  duration/action-policy decisions; no functional cache/push work before this.
- [ ] Approve the storage choice using actual cardinality/atomicity/concurrency/
  recovery tests, including corruption and crash between data/cursor writes.
- [ ] Obtain exact canonical-discovery, ACP and SSH-event seam admission.
- [x] Characterize the current Mux production callgraph for the revised fixture;
  retain raw frames and original stress evidence without relabelling either.
- [ ] Characterize the actual Bridge-owned ACP route and full mixed fleet;
  current Mux-only slice is not a substitute.
- [ ] Implement and measure cache-hit, push-delta, due-action, stale-expiry and
  out-of-band cases with correctness and resource counters together.
- [ ] Preserve page/duplicate/unknown/no-wake/auth/generation/cancellation and
  local owed-push guarantees; never manufacture savings by omitted work.
- [ ] Complete source review/CI, native qualification, and separate authorized
  release/installed acceptance; no pass is implied by plan publication.
