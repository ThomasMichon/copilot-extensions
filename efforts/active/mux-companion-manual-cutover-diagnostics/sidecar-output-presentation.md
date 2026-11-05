# Bounded source-output presentation

Parent: [complete effort](README.md).
Existing intent: [sparse correction](sidecar-live-timeline.md#operator-correction-after-viewing-progress-captures-2026-11-04).
Source boundary: [observation contract](sidecar-observation-contract.md).

**Status: reviewed source-presentation amendment; source implementation
validated locally, delivery pending.**
Plan #10689 merged as `e608ec4ba8163eab26f5db56b4b753a200c4cae9` after
actual current-head independent approval and required green CI.
This realizes the existing agenda-level/sparse intent without changing source
history, ownership, runtime policy or any original parent gate.

## Confirmed source gap

A bounded model-free probe used the actual Bridge EventLog/semantic frame,
Manager codec, bounded window and virtual layout. One tool start followed by
40 known message/thought chunks and a turn completion produced a latest frame
with 25 entries: 24 normal output-chunk rows and the turn completion. The
earlier tool start was outside that loaded window; older history remained
available. Readable work labels and actor attribution alone do not prevent
this low-level output from overwhelming an agenda overview.

The observation describes that fixture, not a universal page cardinality or
installed result. Frame byte bounds still govern actual page size.

## Reviewed presentation boundary

- Treat only explicitly recognized Bridge output occurrences such as
  `agent_message` and `agent_thought` as low-level output. Do not classify from
  summary prose, titles, display names or arbitrary unknown native kinds.
- Collapse adjacent same-source/same-reported-actor output into bounded
  expandable view groups, or an equivalently explicit Evidence disclosure.
  Keep meaningful work, input/permission, compaction, error/gap and unknown
  diagnostic occurrences distinct. Do not group across unrelated actors,
  source generations or intervening work merely because labels match.
- Grouping is ephemeral presentation, not a new owner event or persistent
  ledger. Retain every loaded source record, raw reference, native identity,
  hash, cursor/high-water proof and uncertainty marker. Do not rewrite
  immutable events, renumber history, consume delivery cursors or fabricate a
  productive milestone, current concurrency or lifetime count.
- A group count describes only its loaded source-output records. It is not
  an owner-qualified accumulated tool statistic or a unique-message total.
  Exact clocks/types and existing bounded Evidence remain inspectable;
  missing time and omitted payload retain their original qualification.
- Preserve selection and logical screen-row anchors when groups extend,
  merge or split during live append, backward prepend, expansion, resize or
  display-window eviction. Enter/mouse and keyboard/history navigation must
  operate on visible groups without stranding selection on hidden records.
- When collapsed output leaves a short canvas with older work available,
  retain a usable backward-history affordance. Any automatic fill has a fixed
  per-demand work/page budget, explicit continuation and cancellation; it
  must not self-poll or renew an unbounded search on each incoming chunk.
- Preserve the existing frame/page/window/cache/detail bounds and three
  permanent source workers. Rendering does no I/O, source scan, JSON parsing
  or transcript copying. Do not infer native performance from source tests.

## Plan and validation

- [x] Review this amendment through the normal PR flow before implementation.
  #10689 qualifies plan admission only, not the unchecked source/validation below.
- [x] Implement the bounded presentation projection using existing typed
  source/renderer seams; preserve legacy/unsupported providers and all views.
- [x] Exercise the actual owner-to-codec-to-layout/widget path with long known
  output bursts, interleaved meaningful work and multiple reported actors.
  Keep unknown native kinds and literal lookalike prose as negative controls.
- [x] Qualify snapshot/live/history splicing, group-boundary prepend/append,
  expansion/collapse, short-content history demand and stable anchors/focus
  at ordinary/narrow widths, including source loss and cancellation.
- [x] Retain deterministic visual comparisons and bounded work/resource
  controls. Preserve raw-history integrity and disclose any budget exhaustion;
  required unit-only CI is not expanded into an effectful matrix.
- [ ] Drive source delivery through independent current-head review, required
  green CI and actual merge, followed immediately by the full parent and
  original-contract readback. Journal the exact earned source tier.

Owner-qualified accumulated tool statistics remain a separate S2 obligation.
Telemetry's inferred/bounded tool-state map and ACP's per-turn buffers are not
substitutes for that source contract or proof of current running concurrency.
No installed rollout, live Mux binding, head/resource action, answer, SDK/model
actor, real-data operation or original manual/native acceptance is authorized.

The [source validation journal](journal/2026/11.05-sidecar-output-groups.md)
records the bounded projection and its earned tier. Every original parent gate
remains independent.
