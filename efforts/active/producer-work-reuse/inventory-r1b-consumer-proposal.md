# R1b exact consumer/path/reconcile proposal

[Effort](README.md) · [R1a contract](inventory-freshness-admission.md) · #7949

**Proposal, not implementation admission.** No caller, credential, transport,
push, production path or namespace provisioning is changed. R1a's new captured
A/N1 acceptance is correctness only; N2/performance/full-source acceptance is
separate and does not block this design.

## Selected policy — not another authority choice

The user selected:

| Boundary | Selected rule |
|---|---|
| Fresh status badge | 60 seconds |
| Inactive out-of-band reconciliation | 300 seconds, only on demand or while the existing owner is running |
| Known disconnect | Labelled last-known for 30 seconds, then unknown; retain history |
| Lost-channel due action | Invalidate control proof immediately; one qualified provider recheck at the next existing owner cycle (currently 15 seconds) or explicit action; no speculative SSH |
| Failures | Coalesce by source/account; 60-second cooldown; explicit refresh may request one coalesced attempt |
| Effect proof | Existing maximum 30 seconds, unchanged |
| Control publication | **Option F:** full LIVE candidate/config/credential validation once per completed control-publication batch, not per status read, push delta or tenant |
| Lifetime/silence | No owner start/keepalive merely for cache; silence is not disconnect |

This continues `selected_then_full_publication`. Option S is **not selected**;
no new cross-account revocation boundary is proposed. The 60-second badge is
not a new periodic poller, and a status label is never connection/lease authority.

## First required pure-model refinements

These are proposed hunks, not code changes:

1. `inventory_store.py: Policy/classify_status`: distinguish the universal
   **60-second badge age** from **300-second inactive refresh eligibility**.
   Currently the prototype uses `inactive_age_s` as the stopped-row badge age;
   using `Policy(60, 300, 30)` unchanged would show a 100-second stopped row as
   fresh. Return stale-labelled cached data without forcing an inactive provider
   refresh until the selected due/demand condition.
2. Give the known-disconnect label precedence for its 30-second display period,
   even when the underlying provider fact is old; display the old fact's age
   separately. Current prototype age expiry precedes that label. Control
   invalidation is immediate regardless of this display grace.
3. `apply_account_observation`: retain an enumerated Unknown-state venue's
   identity as uncertain status, without replacing older positive state with a
   fresh success. The prototype's positive-only reducer skips such incoming
   rows; R1b's all-venue status projection must not silently lose that identity.
   This is a model extension, not retrospective whole-cache qualification.

Proposed policy construction stays at the owner boundary: explicitly inject the
selected values into the existing private model/Gate. If a private factory is
needed, it returns only the exact selected policy, without adding a global
default to R1a's `Limits`/`Policy` API. Production limits and activation/path
wiring still require explicit admission; the A guard fixture is not a quota.

## Exact existing-owner hunk map

| File / symbols | Proposed change and boundary |
|---|---|
| `owner_availability.py: AvailabilityGate.__init__, _stale, stopped` | Inject explicit store path, validated `SourceKey`, limits, policy and canonical reconcile callback. Add a private structured observation path; keep `stopped(...) -> set[str]` as the compatibility projection, not the sole owner admission decision. Replace `_states[name]` flattening with account-qualified observations; no first-account wins. |
| `session_forwards.py: SessionForwards.usable` | Compute a **per-venue lost set**, rather than `any(missing(...))` forcing every account/hold to refresh. Read the structured status view; invalidate lost-venue control proof immediately and queue only the allowed coalesced recheck. Preserve hold/tenant intent and expected generations. |
| `connection_owner.py: ConnectionOwner.reconcile, ensure` | Consume an explicit keep/drop/defer-rebuild plan. Keep healthy owned channels on unknown status; do not create/restart deferred ones. Only fresh qualified stopped evidence or removed registry intent closes the appropriate owned channels. Owned cleanup is not withheld because auth became unknown. |
| `session_forwards.py: reconcile, _ensure, _reconcile_extra, _reconcile_local, dynamic reassignment` | Carry the same per-venue rebuild decision **before factories as well as starts/retries**, including dynamic-port replacement. Preserve already-live channels and removal of no-longer-requested intent. These are later control-owner hunks, not hidden inside a status-only cache patch. |
| `owner_inventory.py: qualified collection/reconcile adapter` | One existing source-owned metadata/reconcile producer. Call canonical `read_candidates` only for an admitted reconcile/authority boundary, not a cache read. Project actual reports into account-qualified non-secret store records; never serialize `InventoryScope` credentials/config digests. Join/drain bounded thread work with the existing owned helpers. |
| `owner_cli.py: cmd_owner` construction of `ConnectionOwner/SessionForwards/AvailabilityGate` | Separate, explicit production wiring hunk. Supply the path/source/limits/policy only after existing owner setup, not from the pure store's import or a new daemon. Do not activate merely by constructing/reading a cache. |
| Exact lifecycle provider seam, separately admitted | Qualified page/completeness and fresh selected credential/environment plumbing described below. No shared credential, provider or discovery algorithm is edited by this proposal. |

### Why the two named consumer methods alone cannot safely activate it

Today `usable()` returns a filtered hold dictionary. `ConnectionOwner.reconcile`
drops relays for excluded venues, and `SessionForwards.reconcile` removes their
other forwards. Simply filtering **unknown** venues out would destroy healthy
channels. Leaving them in without a rebuild guard allows speculative factory/
SSH work. Therefore activation needs the explicit existing-owner **keep healthy /
drop proved stopped / defer missing reconstruction** contract above.

`AvailabilityGate.stopped` remains the first status consumer, but its current
empty-set-on-error behavior is not a “may reconnect” verdict. Do not silently
ship the current fail-open interpretation under a new cache.

### Proposed private interfaces for exact admission

```text
StatusBinding(path: Path, source: SourceKey, limits: Limits)
AvailabilityGate.for_inventory(binding, *, policy, reconcile_source,
                               wall_clock, monotonic_clock)
    # required explicit inputs; constructor performs no IO
await gate.observe(codespaces, *, reason, owner_running, lost_venues,
                   explicit_refresh_id) -> AvailabilityView
await gate.stopped(codespaces, *, refresh=False) -> set[str]
    # compatibility projection, never reconnect authority

AvailabilityView(status_rows, unknown, lost_venues, fresh_provider_evidence)
ReconcilePlan(hold_intent, keep_existing, proved_stop_cleanup, defer_rebuild)
    # final effect admission still reads fresh selected authority
```

`reason` distinguishes cached read, explicit refresh, owed maintenance and
lost-channel due action. The compatibility wrapper's `refresh=True` alone is
not an interactive refresh identity or a cooldown bypass. Explicit inventory
mode never falls back to the legacy constructor after a binding/store refusal.
The selected policy is injected by the admitted owner factory; no private
factory/default is implemented by this proposal.

Proposed canonical provider operation, requiring separate ownership admission:

```text
lifecycle.read_account_inventory(account, *, credential_env, deadline)
    -> AccountRead(rows, complete, pages, error)
```

This is a source-qualified observation/evidence result, not a start grant.
`credential_env` is ephemeral and account-pinned from the same fresh owning
provider read; it is never stored. Existing flattened/serial and namespace
concurrent APIs remain unchanged. The exact targeted-status equivalent must
likewise consume that same-boundary auth rather than silently minting again
through a memoized helper.

## Path/source handoff — no provisioning

Candidate filename remains `inventory-v1.json`, with the store-derived adjacent
`inventory-v1.json.lock`, under the already-owned runtime namespace.

The owner must supply an **absolute existing plain parent** and an actual
validated installation/namespace `SourceKey`. The pure model does not resolve
`RUNTIME_DIR`. No creation, migration, corrupt-file reset, alternate-root search
or guessed ambient source is allowed when binding/refusal occurs. Wrong source,
missing parent, busy custody or uncertain data stays explicitly unavailable.

`owner_cli.cmd_owner` currently constructs `AvailabilityGate()` before its
normal daemon loop; `--once` also has a distinct path. Admit the exact setup
handoff before production wiring: do not let the atomic writer's internal
`mkdir` become a hidden setup mechanism. If the owner cannot supply the binding,
leave the cache unavailable rather than inventing an installation identity.

## Local-first flow and reconcile scheduling

1. **Cached status read:** bounded local load/projection only. No
   `capture_scope`, `picker_auth.probe`, credential mint, provider listing,
   process start or owner keepalive. Show source/account/age/completeness.
2. **Demand/due classification:** distinguish cached read, explicit fresh
   request, owed inactive maintenance and lost-channel due action. A stale badge
   alone is not a new active 60-second polling schedule. With no admitted due
   work, issue zero remote observations.
   A never-created/cold missing channel is not a known-disconnect event.
   Do not restart display grace or reconstruct control proof from disk after
   restart; actual current owner/transport generations qualify that input.
3. **One existing-owner reconcile:** coalesce by `(SourceKey, account)` with an
   owned task and monotonic 60-second failure cooldown. Lost-channel refresh
   does not masquerade as an explicit user refresh to bypass cooldown every
   cycle. An explicit refresh request has one coalesced attempt/identity.
   No per-tenant/per-venue poller or separate daemon.
4. **Qualified result:** preserve per-account positive/error/partial/page-limit
   outcomes, immutable projections and all inactive/history rows. Storage CAS
   busy/conflict is not a reason to repeat provider work silently. Surface
   persistence uncertainty; do not claim durable publication from an unknown
   commit. Cleanup drains only owned work.
5. **Control boundary:** cached status never authorizes a new wake-capable
   effect. A real selected-source provider proof has the unchanged monotonic
   30-second cap; persisted wall time cannot reconstruct it after restart.
   Fresh selected auth/config/account checks repeat at every true effect/retry.
6. **Completed control batch:** full LIVE candidate/config/credential validation
   once, at most four venues/sixteen verdicts, without waiting to fill a batch
   or for a slow venue. Retain atomic tenant generation checks; unknown/change
   revokes further wave admission and unpublished results. No credentials or
   control-proof hashes enter JSON.

The old probe currently performs full scope reads at multiple effect/tenant
points. Status consumption must not call that loop. Actual selected-effect/
completed-batch integration is a separate exact `owner_mux_probe.py` /
`SessionForwards._probe_holds` hunk; status-only R1b does not itself fix or close
the earlier runtime performance finding.

## Completeness and fresh-auth dependencies

- Current `_list_codespaces_under` uses `--limit 50`; `list_codespaces` flattens
  accounts/deduplicates names. Neither output alone is an all-account complete
  cache authority. Keep duplicate ownership and account provenance distinct.
- Use the existing source-qualified listing owner. A 50-row/error/malformed/
  partial result stays incomplete; retain unseen rows and unknown membership,
  never infer global absence or drop inactive venues. Counts beyond the active
  representative profile remain a separate inventory cardinality dimension.
- Complete reconciliation beyond that response needs an **additive canonical
  page/completeness operation** with bounded page/retry/deadline accounting.
  Merely raising `--limit` is not completeness proof. Preserve existing serial
  callers and namespace opt-in concurrency/callback/deadline behavior.
- Existing strict per-name `get_codespace_status[_with_account]` provides a
  non-waking control-plane endpoint, but `_get_codespace_status_under` resolves
  through `gh_account.token_for_account`, which can memoize CLI credentials.
  Fresh selected auth must use the exact same-boundary selected token/env for
  the request and effect; a separate fresh read followed by a cached-token
  request is not proof.
- Proposed exact later provider seam: accept a source-owned fresh account-pinned
  ephemeral environment/token in the canonical qualified status/list request,
  within its existing deadline, with no ambient fallback. Preserve ordinary
  callers. No new credential provider, cache or helper modification is admitted
  yet; return this dependency before touching it.
- Named bindings/ambiguity use existing canonical account owners. Do not
  recreate selection inside the Gate or use a raw name/first cache row to
  override binding authority.

## Ownership, staging and next acceptance

The existing CandidateRead/read_candidates grant remains **additive only**:
reuse its actual routing/config/provider/selection values, keep `candidates`
ordering, precedence, callbacks and ordinary/concurrent behavior. It is not a
source-currentness revision and not permission to edit other #5761 discovery/
startup/lifetime code. #5761 remains open; fresh forge checks do not show a
release, and absence of another overlapping PR is not a release.

Suggested admission sequence:

1. Explicit pure policy/model refinements + injected Gate/store status tests;
   no production path activation or control effects.
2. Exact owner path/source setup and keep/drop/defer-rebuild hunks, with the
   canonical qualified reconcile/fresh-auth/page contract agreed first.
3. Actual control-effect and Option-F completed-batch integration, with real
   consumer work counts and owner/generation/cancellation controls.

Required selected-policy/consumer vectors for those admissions:

- Stopped rows at ages 59/61/299/300: separate fresh badge, stale display and
  inactive refresh eligibility; passive reads never dispatch remote work.
  At 300, refresh requires admitted demand or an already-running owner.
- A known disconnect gets 30 seconds of labelled history and immediate control
  invalidation; cold missing channels, restart and silence do not fabricate or
  renew that event/grace. At 31 seconds current status is unknown.
- Failure at t=0, existing owner cycles at 15/30/45, and a due request at 60:
  one initial attempt, no cooldown bypass, then one allowed coalesced attempt.
  Concurrent explicit refresh requests share one actual attempt.
- Missing/corrupt/wrong-source store, partial/error/page-limit accounts,
  duplicates and first-seen Unknown identities: retain history, surface
  uncertainty and prove no speculative factory/start/retry.
- Unknown status with healthy forwards retains them; unknown with a missing
  channel defers reconstruction without erasing hold intent or dropping other
  healthy channels. Fresh qualified stop permits owned cleanup, not stale
  cache-driven tenant release.
- Fresh selected credential rotation/provider failure and a poisoned legacy
  token cache cannot pass same-boundary recheck/effect admission. F runs once
  for a completed control batch, zero times for cached status/push-only data.
- Per-source/account coalescing, no hidden provider repeat on storage CAS
  conflict, owner/tenant replacement, cancellation/draining and slow-venue
  independence retain existing bounds. No byte/cardinality guard is a runtime
  venue cap or an excuse to call truncated coverage complete.

N2 sharing/crash/source-boundary fault qualification remains separately gated.
Its absence does not block designing/testing the selected pure policy or
status-only consumer. Push/no-reconnect/ACP adapter ownership and future
venue-local multi-session aggregation remain separate; no transport or remote
stack implementation is implied.
