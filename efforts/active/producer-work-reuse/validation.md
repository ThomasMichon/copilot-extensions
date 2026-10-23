# Measurement, evidence and file ownership

[Effort](README.md) · [Bridge](bridge.md) · [Dispatch](dispatch.md) ·
[CodeSpaces](codespaces.md) · [Transcripts](transcripts.md)

## Same-workload evidence contract

Every implementation retains both baseline and candidate receipts, not only
percentages. Use the same synthetic input seed, roster, account/namespace count,
event schedule, lane configuration, sample interval and cache-start condition.
Record baseline/candidate source commit, selected runtime identity, OS, clock
source, complete command, expected/completed observations and errors.

Separate cold initialization, warm unchanged, changed input, unavailable source
and recovery phases; also retain the whole fixed-duration aggregate. Scheduled
reconciliation belongs inside the aggregate, not outside a convenient warm
window. Count child processes, provider/API calls, retries, pages, connects and
disconnects, metadata operations, parsed documents/records, bytes read/written/
transferred and retained storage. Wall duration, aggregate CPU and counts are
different dimensions. Lower call count is not measured CPU improvement.

Use existing telemetry adapter/stage contracts where available. Test-local
counters may wrap existing injectable executable/manager/filesystem boundaries;
they do not add a public metrics API. Enabled instrumentation must remain
bounded/fail-open, without synchronous exporter IO on the producer hot path.
Retain disabled/enabled/unavailable-sink comparisons with the telemetry owner.

For CPU, normalize to one logical core and include descendant process coverage;
missing children are incomplete evidence, not zero. Retain individual latency
samples, sample size and quantile calculation. Clock-domain mismatch or too few
independent events means insufficient evidence, not a p95 pass.

## Source, release and installed cells

| Scope | Source observation at plan time | Release | Installed/native |
|-------|---------------------------------|---------|------------------|
| #7948 | Structural unconditional-cycle/full-scan model read; actual counts unmeasured | No candidate implementation | Unavailable; no existing roster contacted |
| #2110 declaration/health | Repeated decode/future-submission paths read; current CPU and latency unmeasured | No candidate implementation | Unavailable; no production lane changes or event injection |
| #7949 | Structural C x A listing and per-mux exec model read | No candidate implementation | Unavailable; no remote inventory/probe |
| #7950 | O(N x K) offset lookup and repeated local catalog scan read; delta/debt foundation retained | No candidate implementation | Unavailable; no live transcripts read |

On implementation publication, replace a cell only with evidence for that cell:
merged source PR and source tests; promoted release/artifact identity; then
separately authorized installed/running identity and the actual consumer sample.
Plan merge is none of these performance receipts. Source green cannot qualify
release or installed behavior; no updater or service restart is authorized here.

Represent outcomes as `pass`, `fail`, `partial`, `unavailable`, or
`insufficient-samples`, with coverage and reason. Partial provider listing,
unreadable declaration tier, missing service, stale revision, unproven process
identity, locked mirror or missing completion marker never become successful
empty/zero observations.

## Exclusive file contracts for later admitted workers

| Slice | Owns | Boundary requiring sequencing |
|-------|------|-------------------------------|
| S1 | Bridge namespace scheduling helper, cache/discovery integration, roster tests | `agent_registry_namespace.py` with #5761; optional provider revision contract; no telemetry adapter edits |
| S2a | Dispatch declaration decode cache, discovery integration, registrar tests | Supplier/template/activation dependency revisions; preserve runtime/recovery authority |
| S2b | Dispatch health scheduler and daemon integration, health tests | `registrations.py`, `companion.py` snapshot/schema and `supervisor_daemon.py` with runtime/recovery and telemetry owners |
| S3a/b | CodeSpaces qualified inventory, mux batching, `SessionForwards.probe`, tests | `lifecycle.py` account listing shared with namespace enumeration; preserve its consumer API |
| S3c | Owner-scoped borrowed transport lifetime | Mirror `_read` optional lease argument jointly agreed with S4 |
| S4 | Transcript catalog/cursors, mirror/script/parser and tests | Only the agreed S3c borrowed lease; no competing transport owner |

Telemetry adapters/core, reporting/OTLP, launch readiness/static preparation,
Manager startup, worktree feed/projection/receipts, handoff listener, mention
hooks and state recovery are outside these file claims. Do not change their
interfaces to make this plan easier. If a shared declaration or telemetry
signature must change, publish the exact proposed signature and dependent tests
to the coordinating owner before implementation; a parent/owner sequences it.

## Validation tiers and commands

1. **Plan now:** source-to-design review, relative-link/exact-symbol checks,
   `python tools/check-docs-consistency.py`, `git diff --check`, independent
   plan review and ordinary PR checks. No runtime tests are claimed because no
   runtime changes are made.
2. **Pure unit implementation gates:** focused tests in each linked slice;
   use the repository's positively admitted unit packet for required CI.
   Counters/fake clocks establish structural bounds, not native latency.
3. **Explicit real-consumer integration:** owned provider executable + real
   roster HTTP route; actual companion command boundary; actual owner-wave
   adapter; real Bash mirror round-trip. Use the existing isolated runner.
   Stage new test files before snapshot-based runners. Native matrices are
   explicit periodic/manual lanes, never silently added to the required packet.
4. **Clean-room:** fresh empty cache/index, absent sibling, unsupported old
   provider and owner replacement in isolated fixtures; do not deploy.
5. **Authorized native qualification later:** retain full workload and
   source/release/installed identity. #2110's aggregate CPU and carrier-event
   latency stay distinct gates. Remote venue validation needs explicit targets
   and ownership; its absence here is intentional, not a pass.

For changed runtime code also run focused Ruff and
`python tools/check-install-contract.py`; add changefiles for each touched
plugin through `tools/changefile.py`. This effort-only PR changes no plugin
payload/version and needs no plugin changefile.
