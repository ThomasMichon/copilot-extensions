# Session-local Usage Evidence and Historical Estimates

Parent: [Session Intelligence and Accounting](README.md). Coordination: #6439.
This is a proposed slice contract, not an implemented API.

## Scope and ownership

The primary contributor owns the accounting-evidence and estimation slice.
Existing process-log containment, publication and reclamation owners retain
their work. This slice realizes `preserve-accounting-evidence` and extends the
session-intelligence vision with explicit historical estimation; it retains the
independent, bounded, cross-platform runtime CLI shape and existing sync roles.

## Requested behavior

- Preserve per-session selections from the local CLI accounting store alongside
  durable session artifacts before they are synchronized or compacted. A shared
  store that is later corrupt, missing, or rebuilt cannot erase prior captures.
- Use newer measured per-session records to derive transcript-only historical
  token estimates. Measured evidence always remains distinct from estimates.
- Reuse the existing sync scope, transport, archive and incremental-signature
  paths rather than introduce a second scheduler or a mandatory service.

## Preservation contract

Capture uses read-only SQLite transactions, including committed WAL data, on
the producing machine. Copying DB/WAL files independently is not a coherent
snapshot. Capture only admitted sessions and an explicit scalar accounting
field allowlist: session/model/time/call lineage, integer token buckets,
duration and provider meters. No prompts, summaries, raw tool bodies, auth
metadata, account identities or consumer rate cards enter the capture.

A versioned per-session artifact records normalized event identities,
observations, source kinds and coverage. Replays and rebuilds merge additively;
null enrichment may fill missing facts but incompatible values are reported,
not summed or silently replaced. A database row number alone is not a durable
identity. Modern transcript `session.usage_record` observations are also
admitted without requiring the shared store.

Capture runs before incremental signatures and cold archival. Dry runs make
no artifacts. Existing raw session publication may continue when accounting
capture fails, but the failure is visible and the run cannot claim complete
accounting preservation. A missing legacy accounting source is an explicit
coverage gap, not a fabricated zero. Preserve last-known-good artifacts on
partial input, bounded-read refusal, writer contention or invalid schema.

## Estimation contract

Transcript-derived features are deterministic, count-only and versioned.
Visible output includes recorded textual response, explicitly exposed reasoning
fields already present in the transcript artifact, and emitted tool-call
arguments where available, not just prose. No hidden reasoning is requested,
decrypted, reconstructed or persisted. Input features account
for replayed visible context and observable compaction/resume/reset boundaries.
Hidden instructions, unrecorded reasoning, cache behavior and discarded
context are acknowledged limitations, not reconstructed billing facts.

Calibration pairs those features with measured usage for the same evidenced
calls/session-model intervals. Training and validation separate whole sessions
and their copies, never random turns from the same conversation. Calibration
artifacts contain coefficients, feature/schema versions, sample counts,
supported models, source cohorts, held-out errors and applicability ranges,
not transcript content.

Historical estimates identify their calibration and uncertainty. Unsupported
model attribution, insufficient samples, incompatible features, unsuitable
held-out accuracy or extrapolation cannot masquerade as measured tokens.
Provide explicit unknown/abstention results when no defensible calibrated
estimate exists. A global characters-per-token constant is not evidence of
repeated input, invisible output, prompt-cache usage or provider billing.
Estimates never create AI-credit meters, payer attribution or invoice values.
Consumer-specific dollar estimates remain an independently injected policy.

The operator selected best-effort estimates with measured error ranges and
explicit extrapolation warnings. Poor held-out accuracy therefore lowers
confidence and widens the disclosed empirical range; it is not a universal
accuracy cutoff. Unidentifiable models, unusable calibration or absent features
still remain unknown. Empirical validation ranges are not statistical confidence
intervals or guarantees for a historical cohort.

## Agent-recommended safeguards

- Bound session counts, SQLite work/time, records, member sizes and JSON lines;
  reject unsafe paths and special files. Serialize and atomically publish
  per-session merges, preserving byte-identical unchanged files.
- Treat unstable live tails as partial; preserve source/session identities and
  original timestamps across repeated syncs and live/archive representations.
- Make confidence/admission policy explicit and configurable rather than
  silently choose a universal acceptable-error threshold.
- Initially expose machine-readable library/CLI evidence and calibration
  products. Interactive HTML, service hosting and consumer rollout retain their
  separately owned follow-on contracts.

## Plan

1. Land this reviewed amendment before implementation.
2. Implement bounded normalized evidence readers and additive session-local
   preservation, then integrate before sync signatures and cold compaction.
3. Implement versioned transcript feature extraction and session-held-out
   calibration/estimation with explicit unknowns and error disclosures.
4. Validate synthetic preservation and estimation contracts and bounded private
   count-only comparisons; publish no real corpus or consumer pricing.
5. Land reviewed source and documentation; journal what is proved and retain
   remaining parent-effort obligations.

## Validation

- [ ] Committed WAL rows are captured; absent/corrupt/rebuilt stores retain old
  observations; duplicate and conflicting evidence has explicit semantics.
- [ ] Repo-scoped capture includes no out-of-scope rows. Missing/old schemas,
  read limits, lock contention and unsafe paths report honest failures.
- [ ] Dry run is side-effect free; capture precedes signatures/compaction;
  unchanged evidence does not trigger perpetual resync; failed publication
  retries without dropping accounting.
- [ ] Modern per-call records work without the shared store; checkpoints and
  shutdown totals remain separate from per-call rows and never double-count.
- [ ] Synced and archived session artifacts preserve the same usage evidence,
  with live/archive duplicate and continuation semantics unchanged.
- [ ] Synthetic fixtures prove feature counting, model intervals, emitted tool
  arguments, reasoning visibility, resets and partial/live-tail behavior.
- [ ] Held-out validation is session-disjoint and source-copy-disjoint; sparse,
  inaccurate or incompatible calibration abstains with explicit diagnostics.
- [ ] Known synthetic relationships recover expected estimates and bounded
  error; a misleading relationship fails admission rather than reports success.
- [ ] Real count-only validation reports measured coverage, held-out errors and
  failure/abstention cases without exposing transcript content.
- [ ] Focused native tests, lint and repository guards pass; source review and
  required checks complete before merge. Linux-only evidence is not invented.
