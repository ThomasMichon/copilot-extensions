# S3 — CodeSpaces owner-wave batching (#7949)

[Effort](README.md) · [Validation and ownership](validation.md)

## Authority amendment

The explicitly selected policy is
[`selected_then_full_publication`](selected-authority-contract.md): fresh
selected-account/source/configuration/credential checks admit observational
effects and retries; full live candidate/configuration/credential validation
gates actionable, bounded publication. An unrelated candidate change may allow
an observation or connection wake before its result is rejected. This accepted
tradeoff replaces whole-set revalidation at each observation; selected
authentication, no-wake/age protections, owner/tenant fencing and all performance
gates remain. The amendment owns the exact schedule and rejection semantics.

Paths below are relative to `plugins/agent-codespaces/`. New API names and
workloads are agent-recommended proposals.

## Causal hotspot map

`SessionForwards.probe` in `src/agent_codespaces/session_forwards.py:358-402`
walks due holds sequentially. `make_remote_mux_probe:728-802` calls
`list_codespaces()` for each venue and then executes `tmux has-session` once
per distinct mux. `lifecycle.list_codespaces:81-138` enumerates candidate
accounts and merges by name; partial per-account errors disappear when some
rows succeed. `_open_codespace` can resolve account again.

`make_remote_bridge_probe:683-724` and `TranscriptMirror._read:357-384`
open/disconnect their own managers. These are legitimate observations but
duplicate account/connection work. Current protections include unknown on
absent listing, no connection for listed non-Available venues, per-venue probe
rate limiting, confirmed-only negative release, tenant generation fencing,
single-flight mirroring and owed-push retries without remote access.

## S3a — One qualified inventory per due owner wave

Add `owner_inventory.py` with an immutable `OwnerInventory`:
wave ID, selected source/installation and account-candidate generation,
per-account success/error/completeness, bounded observation time and rows keyed
by account and venue identity. Refactor the existing account-list implementation
to expose this qualified result; retain `list_codespaces()` as the compatible
flattened-list API for callers that do not need completeness.

`SessionForwards.probe` selects due holds first. With no due remote probes, make
zero account listing calls. Otherwise obtain one inventory and pass it through
an explicit wave adapter into the default mux probe. Keep the injected legacy
two-argument `SessionProbe` shape supported; do not force unrelated custom
providers to understand a new global singleton.

- Account context is pinned per row/venue; never use a shared name-only lookup
  to silently choose another account. Ambiguous duplicate ownership is unknown.
- A partial/failed account result cannot prove absence or release a hold.
  A successfully observed non-Available venue still follows the existing
  confirmed-only release path without connecting.
- A complete absent result remains unknown unless a separately reviewed
  deletion contract proves otherwise; this change need not expand release power.
- The current `_list_codespaces_under` has a limit of 50. A result at that limit
  is not automatically exhaustive. Implement/verify pagination or report
  completeness unknown; never upgrade truncation to authoritative absence.
- Bind inventory to the wave, account-candidate/config generation and a bounded
  admission age. A queued slow venue cannot connect from arbitrarily old
  Available evidence. Expired proof returns unknown/deferred; any necessary
  refresh is explicit, deduplicated and counted, not hidden per-venue relisting.
- Selected source/account/configuration/credential changes or owner-generation
  changes invalidate effect admission; full candidate/configuration/credential
  changes invalidate actionable publication under the amendment above.
  No durable account token is stored in the inventory.

## S3b — Batch mux checks and bounded venue concurrency

Add a pure mux batch command builder/parser (prefer a focused
`owner_mux_probe.py` rather than further enlarging `session_forwards.py`).
Use exact mux targets, existing login-shell wrapping and safe quoting.
Return versioned per-mux records plus an explicit completion marker in one
remote exec per venue. Missing/duplicate/malformed records or command failure
remain unknown; never turn missing `tmux`, a failed transport or a partial
response into "all sessions gone." Each distinct mux is checked once even
when several tenants share it.

Run due venues under a small configured concurrency limit, provisionally four,
and retain per-venue execution bounds and owner cancellation. One stuck venue
must not consume another venue's admission/freshness window. Preserve tenant
generation checking at heartbeat/release and recheck that the hold still belongs
to this owner after awaits. No detached fire-and-forget probe threads.

## S3c — Optional borrowed manager, explicit lifetime

Only after S3a/b, introduce an owner-scoped `VenueProbeLease` carrying pinned
account/source, venue, inventory admission and owner generation. It is a context
manager over the existing authenticated transport, not a new global connection
pool. Bridge proof can borrow it after positive mux proof. Transcript read may
borrow only while the lease is retained by its actual mirror task; background
mirror launch cannot outlive an already-disconnected context.

The owner has exactly one close authority. Borrowers do not disconnect the
manager, and cancellation waits/drains or transfers the owned lease explicitly.
Preserve the standalone opener path for transcript retry callers.
**Shared file seam:** `TranscriptMirror._read` belongs to S4; agree and land the
optional borrowed-manager argument before S4 integrates it. S3a/b can ship
without S3c and must not claim connection-reuse savings prematurely.

## Same-workload acceptance

For ten due venues, three accounts and four muxes per venue, one successful
no-retry wave targets:

| Counter | Current structural model | S3a/b target | S3c target |
|---------|--------------------------|--------------|------------|
| Account listing calls | 10 x 3 = 30 | <=3 | <=3 |
| Mux remote execs | 10 x 4 = 40 | <=10 | <=10 |
| Manager opens for mux + bridge + mirror | Up to 30, plus any account lookup work | Count actual; no claimed improvement | <=10 if all three operations safely share |
| Unchanged second poll before due time | Rate-limited | 0 listing/mux calls | 0 listing/mux calls |

Counts exclude no hidden pagination or retry: record each explicitly. The <=3
target fixture stays below the per-account page limit. A multi-page workload
reports pages/account and still requires one inventory acquisition per wave,
not an impossible three-request claim.

Retain elapsed wave and per-venue p50/p95/max, child CPU, bytes, temporary
storage and actual connect/disconnect counts. Compare zero/one/ten due venues,
stopped venue, account failure, limit-sized response, one slow venue, owner
replacement and cancellation. For stopped/unknown venues expect zero connects
and no wake. Owed local push remains independent of remote inventory failure.

## Validation flow

Extend `tests/test_session_forwards.py` existing no-wake/unknown/rate-limit
contracts and lifecycle account tests; add pure inventory/mux parsing tests.

```text
python tools/run-plugin-tests.py agent-codespaces --test-file tests/test_session_forwards.py --test-file tests/test_transcript_mirror.py
```

Add an explicit local consumer scenario driving `SessionForwards.probe` with
the actual account-list and manager adapter boundaries pointed only at owned
fake executables/transport. Count actual command invocations and ensure cleanup
on timeout/replacement. This is stronger than a direct batch-helper test but
does not pretend to measure GitHub/SSH latency. Live venue qualification is
unavailable in this planning scope and requires separately authorized targets.
