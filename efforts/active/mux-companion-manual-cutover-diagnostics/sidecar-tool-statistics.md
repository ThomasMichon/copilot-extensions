# Owner-qualified accumulated tool observations

Parent: [complete effort](README.md), Step 7.
Source obligation: [S2](sidecar-live-timeline.md#s2---worktree-and-agenda-owner-observations).
Existing boundary: [observation contract](sidecar-observation-contract.md).

**Status: bounded owner/provider/Manager source implementation merged;
installed/native and parent acceptance remain separate.**
Plan #10704 merged as `9c5a68a52abbee9fd2679fb3acab2c6af0390705`
after current-head independent approval and green CI, before runtime code.
The [admission readback](journal/2026/11.05-sidecar-tool-statistics-plan.md)
retains the complete parent and original contracts.
The [owner-projection readback](journal/2026/11.05-sidecar-tool-statistics-owner.md)
records #10707's bounded internal source tier without closing this amendment.
The [provider readback](journal/2026/11.05-sidecar-tool-statistics-provider.md)
records #10712's negotiated parser/HTTP/CLI source tier; consumer acceptance
remains separate.
The [Manager source validation](journal/2026/11.05-sidecar-tool-statistics-manager.md)
records #10718's conditional consumer, sparse-label controls and actual
reviewed merge.

## Confirmed gap and selected scope

The viewer retains a bounded subset of native records. Summing that subset
cannot establish accumulated statistics. Bridge's telemetry state map can
evict tools and infer terminal transitions; ACP's call buffer resets per turn.
Neither establishes lifetime totals or current running concurrency.

The first owner-qualified statistic is deliberately narrower: the number of
native `tool_call_start` occurrences admitted to one identified EventLog range.
This is a count of reported source records, not unique tool calls, completed
tools, currently running work or session-lifetime activity. A distinct replayed
start record remains a distinct occurrence, not proof of a new live call.
The label and Evidence must state that basis; unqualified lifetime and current
concurrency remain unknown.

Use the existing EventLog owner and its actual append, DB hydration, captured
tail, rebuild and represented-log restoration seams. Derive a content-free
counter while those paths already traverse their records, then read it in
bounded work. Do not scan retained history for every page, update or repaint;
do not add a persistent statistics ledger, polling worker or service.

## Required boundary

- Bind the count to the exact source/log continuity, observed interval and
  native head from the same atomic owner cut. A changed log/rebuild cannot
  retain the old range's counter. Captured tails, gaps, represented memory-only
  evidence and pending persistence keep their actual coverage/durability.
- Empty or unqualified source continuity is unknown, not zero tools.
  A qualified nonempty range with no start record may report zero observed
  starts **in that range only**. Native IDs and counters are exact bounded
  integers, not booleans, rounded text or inferred session totals.
- Count each native record once during source construction, not again when
  another viewer subscribes or loads history. Duplicate delivery, reconnect
  and display eviction neither increase nor reset the owner statistic.
  Source replacement/rebuild changes its declared scope instead of silently
  preserving a lifetime-looking number.
- Ancillary counter failure or representational exhaustion is explicit
  unknown/unavailable statistics, not failure of an otherwise admitted event
  append, hydration or rebuild. Preserve canonical records and ordinary readers.
- Expand the owning provider before callers through a separately negotiated
  optional capability on the existing selected-session observation reader.
  Old syntax/fields and strict parser behavior remain valid. Missing support
  retains usable legacy history with unknown statistics, never a fallback
  transcript scan or a fabricated zero.
- Statistics are current owner projection, independent of delivered page ends.
  Their exact native head cannot be confused with a consumer's live/history
  cursor. Backward pages cannot overwrite newer statistics; coalesced deltas,
  stale replies, head/source replacement and hide/reopen retain existing fences.
- Manager consumes the admitted value rather than recomputing it. Use a short
  explicitly qualified label and bounded Evidence for interval/basis/coverage.
  Loaded output-group counts remain separately named loaded records.
  Retain the sparse work canvas, three source workers and existing wire,
  page/window/cache/detail bounds. Add no render I/O or JSON parsing.

## Plan and validation

- [x] Review this amendment through a normal PR before source execution.
  #10704 admits this plan only, not the unchecked implementation below.
- [x] Implement the bounded owner projection at all actual construction and
  lifecycle seams, with no second history scan, persistence owner or actor.
  #10707 qualifies append, hydration, captured-tail, rebuild and represented
  restoration, atomic range/page reads and ancillary failure/exhaustion.
  It adds no optional HTTP/CLI statistic or consumer behavior.
- [x] Expand and qualify the optional provider/parser/CLI boundary first.
  Preserve old/new peer behavior and source-bound protocol compatibility.
  #10712 delivers independent optional support, exact atomic range fields,
  unknown/zero distinction and source-bound HTTP-52 qualification. It does
  not enable a Manager caller or complete the combined validation below.
- [x] Consume the negotiated projection on the existing held Manager reader,
  preserving current-state/history separation and truthful sparse labels.
- [x] Exercise actual append/hydration/captured-tail/rebuild/restoration,
  replay/duplicate delivery, unknown/zero, malformed counters and atomic races.
  Include ancillary failure/exhaustion without undoing source delivery.
  Verify history loading/eviction cannot manufacture or reduce an accumulated
  source count; source replacement retains explicit new scope.
- [x] Qualify the actual owner-to-codec-to-widget path with enough source
  records to exceed the latest page and display window. Retain deterministic
  work-count, cancellation and ordinary/narrow visual controls, not a new
  effectful required-CI matrix or an inferred native-performance pass.
- [x] Obtain current-head independent review, required green CI and actual
  merge; immediately read back the full parent and original contracts, then
  journal only the earned source tier and select the next parent gate.
  #10718 merged as `742c755ff45fb75c960900d499c8955ed35ff856` after
  current-head approval and all required green checks. The complete parent,
  all three original contracts and both presentation/statistics amendments
  were read immediately before journaling the earned source tier.

This adds no installed rollout, live Mux binding, SDK/model actor, head repair,
answer, collection, relocation or deletion authority. Complete source coverage,
qualified pre-compaction evidence, native performance, archival, manual/head
and complementary Relay acceptance remain independent.
