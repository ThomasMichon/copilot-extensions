# Unlock-triggered cache warming

An opted-in protected cache and registered `cache_source` extensions allow the
selected service to warm configured credentials after a successful unlock.
Provider-based, explicit-password and attended unlocks use the same path.
Nothing enables the cache, unlocks a database or registers targets implicitly.

The existing extension registry owns the target allowlist. Sources receive the
ordinary optional machine hint as `None`; each source remains responsible for
returning the targets appropriate to that service. Warming never lists or scans
the database, starts a session ceremony, or adds a timer. Manual population
retains its existing uncapped collection behavior through the same collector.

## Work and lifetime

Verified unlocks enqueue a database/group context without waiting for backend
reads. Repeated already-unlocked requests coalesce with that unlock's work,
including completed or partial work; they do not retry failed targets or renew
the master password. A genuinely new verified unlock gets a new identity even
when the wall-clock timestamp is unchanged.

The serving generation's existing housekeeping loop starts at most one worker.
It admits at most 32 pending/retained contexts, 256 configured targets, 1024 raw
source items and 32 sources per context. The 60-second work deadline starts at
enqueue, including queue wait. Reaching a bound is a truthful partial outcome,
not evidence that unexamined targets were cached.

Source callbacks and their iterators run off the event loop and must return
cooperatively. The deadline prevents further admission; it does not forcibly
interrupt arbitrary trusted Python callbacks or an already admitted native
operation. Such an operation retains its existing bound and must actually
settle. No detached worker or timeout-based resource abandonment is used.

Each target uses the existing credential and backend custody. Warming requires
the already-unlocked master password: it never invokes another provider or
dialog if the password expires, including expiry while waiting for backend
custody. It does not extend password lifetime or an inherited handoff deadline.
The same internal deadline reaches the protected read owner, which checks it
again immediately before admitting a KeePass entry read after custody waits.

## Protected results and failures

`cached` counts only records read back from the existing service-owned protected
store with the expected binding, value and generation. A successful backend read
or a successful-looking cache write alone is insufficient. Standard field and
backend-equivalent entry aliases are deduplicated without merging root and
group-relative subjects.

Unknown rotation journals remain intact. Missing values, failed protection,
source errors and unverified writes produce failed or partial outcomes without
credential-bearing diagnostics. Warming does not invoke ordinary client
authoritative reconciliation or replace missing-only recovery state. Existing
protection, freshness, selection, generation and revocation rules still govern
later reads; a completed batch is not permanent readiness.

Shutdown closes the pending queue. An admitted worker belongs to both the
connection owner's credential-work and worker sets, so cutover cannot release
writer custody while it is still running. Cancelled closure still waits for
actual work settlement before executor and service cleanup.

## Status and compatibility

The existing full `ping` response adds `cache_warm` using contract
`agent-vault.cache-warm`, version `1`. It reports only state, a fixed reason,
`selected`, `cached`, `failed`, `remaining`, `failed_sources` and `truncated`.
Counts and status are scoped to the selected database/group; no entry names,
database paths, credential values or callback exception messages are included.
Full ping never reloads configuration to select warming status. An omitted group
uses the retained default-warming context; if none is known, status reports
`context-unavailable` rather than guessing from another group's work.

`agent-vault ping` renders this status conservatively. Unsupported, malformed or
inconsistent metadata is unknown, never a successful warming claim. Older
providers omit it, and older clients ignore the additive field. No new request
syntax or synchronized client update is required. Health-only ping remains
independent of credential work. Ordinary client `--cache-only` still never uses
IPC or an unlock.

Focused source coverage lives in `tests/test_cache_sources.py`,
`tests/test_cache_warm.py` and `tests/test_cache_warm_status.py`.
The fresh-process native-protection fixture also
covers unlock-triggered warming before expiry and a synthetic unreadable
caller. See the repository [testing guide](../../../TESTING.md) for execution
and native qualification.

See also: [persistent cache and sealed values](../README.md#persistent-cache-and-sealed-values).
