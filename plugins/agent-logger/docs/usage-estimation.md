# Transcript-only historical token estimates

Back to [architecture](architecture.md) and [preserved usage evidence](usage-evidence.md).

`agent-logger usage` exposes a read-only JSON pipeline. It does not modify
sessions, add a scheduler, assign payers, price usage, or create provider-credit
meters. It implements the session-intelligence vision's
`evidence-calibrated-historical-estimates` behavior using the existing standalone,
bounded live/archive session abstraction.

```text
agent-logger usage features --session <session-directory> --session <session-archive>
agent-logger usage calibrate --features <measured-features.json> --cohort <source-cohort>
agent-logger usage estimate --features <historical-features.json> --calibration <calibration.json>
```

Each command prints one JSON document to stdout. Save that output using the
shell's UTF-8 redirection mechanism for the next step. Input/read/schema failures
return exit status `2`, with a JSON `agent-logger.usage-error` on stderr and no
successful partial document. Successfully reported unknown estimates are data,
not command failures. No coefficients or real calibration corpus ship by default.

## Feature contract

The library entry point is `usage_features.extract_features(SessionRef)`.
`features_from_events(events, session_id)` implements the same deterministic
transform over already-admitted events. Output schema
`agent-logger.transcript-features`, version `1`, method
`root-turn-visible-replay-v1`, contains source identity/revision, count-only
root-call rows and coverage. The CLI wraps sessions in
`agent-logger.transcript-feature-set`, version `1`.

Root calls are identified by chronological root `assistant.turn_start`
segments and API-call IDs, not by a possibly repeated turn ID or the nearest
previous assistant message. Measured usage may precede its emitted response.
Native event IDs suppress identical replay; conflicting replay is an error.
Envelope `agentId`, legacy `data.agentId`, a nested `data.usage.agentId`
accounting marker and parent-tool ownership are filtered before changing any
root prompt, model, context or turn state. Child events do not become
root-call quantities, and preserved/SQLite evidence records carrying their own
`agent_id` are likewise excluded before pairing with root-call features.

Output counts Unicode characters in recorded response content (plain text,
``text``, or a text-block ``content`` array), one explicitly exposed plaintext
reasoning representation, and emitted tool arguments.
`reasoningText` takes precedence over the alternate `reasoningBlocks.blocks`
representation; blocks admit `text`, `thinking`, or textual `summary` entries.
Opaque/encrypted reasoning is excluded. Argument objects are serialized as
compact JSON once; tool-execution echoes are not emitted arguments.

Input exposure is the replayed visible-context character count at root turn
start: user/system text (plain text, ``text``, or a text-block ``content``
array), root emitted output and results of known root tool calls. Successful
compaction replaces that count with `summaryContent`; a missing/nontext
summary clears stale context, counts the compaction and warns.
`session.context_cleared` replaces it with `initialMessage`. Unlinked tool results
are counted as a coverage gap, not guessed into context. Hidden instructions,
tool definitions, nontext attachments, wire formatting and discarded context
are not recovered. This is a feature proxy, not a tokenizer or reconstructed
provider request.

Models require evidence in usage, emitted messages, or an explicit
`session.model_change` event (`newModel`, `modelId`, or `model`). Workspace/
global defaults are not historical model evidence. Missing attribution remains
unknown.

A root turn with no identified API calls attributes every ID-less ("unlinked")
emission in that turn to one aggregate anonymous call rather than replaying
the same prompt's input-character count once per emission; a turn mixing
exactly one named call with unlinked emissions merges them into that call,
and a turn with two or more named calls keeps each unlinked emission separate
(its call cannot be disambiguated).

## Measured pairing and copies

Extraction admits modern transcript per-call input/output tokens and, when
present, the session's bounded `usage-evidence.json`. Optionally pass
`--usage-store <session-store.db>` to read an explicitly selected producing
machine's store through the existing scoped, read-only WAL-aware reader.
Copying independent DB/WAL files is not a coherent snapshot and is not an
alternative source guarantee.

External scalar records join only an unambiguous API-call ID. Missing/reused IDs
or observation multiplicity greater than one remain unmatched/ambiguous in
`evidence_join`; they are never assigned by proximity. Incompatible measured
tokens/models fail instead of summing competing sources. Input already includes
cache buckets, and reasoning tokens are not added to output. Calibration uses
only evidenced root-call pairs with observed emissions and positive features.

Original session identity comes from recorded session/accounting identity, else
the admitted session reference. Whole original identities are case-normalized.
Identical copies count once; divergent copies or identical transcript revisions
claiming different original identities are refused. Conflicting or separately
selected resumed revisions must be reconciled before calibration, not split
between training and validation.

## Calibration and uncertainty

`usage_calibration.calibrate(documents, cohort=..., policy=CalibrationPolicy(...))`
returns `agent-logger.transcript-calibration`, version `1`. A deterministic
SHA-256 of the lowercase original session identity selects the validation bucket
(modulo `5`, remainder `0`, by default). No turn from a held-out session becomes
training data. The default requires three training and two validation sessions
per model/quantity; CLI options expose these minima and the split modulus.

Separate input/output, per-model zero-intercept ratios use
`sum(measured tokens) / sum(feature characters)` from training pairs. Artifacts
record feature/method versions, cohort/revision, sample and rejected-call counts,
coefficient, training call/session applicability ranges and held-out errors.
Validation errors and actual/predicted ratio extrema are calculated on
**session-model matched-call aggregates**, not per-call percentiles. Incomplete
measured-session coverage is disclosed and does not assert accuracy for omitted
calls or an entirely different historical cohort.

Default policy is best effort with uncertainty: poor held-out accuracy remains
visible, not hidden behind an arbitrary universal cutoff. The optional
`--max-p90-relative-error` admission ceiling instead returns unknown for a
relationship that fails that explicitly chosen policy. Relative error divides
by `max(actual tokens, 1)`; the reported p90 is nearest-rank.

`usage_estimation.estimate(documents, calibration)` returns
`agent-logger.transcript-estimates`, version `1`. Each session/model/quantity has
separate measured token counts and estimated missing-call tokens. It never adds
them into an allegedly exact total. Unknown models, sparse or unusable
calibration, zero/unobserved features or incompatible artifacts cannot produce
an estimate. Extrapolation beyond training call magnitude, session aggregate or
call count adds a warning. Mixed measured/missing subsets warn that their range
was calibrated on matched-call session aggregates.

Empirical bounds multiply the estimate by held-out actual/predicted extrema,
including the point prediction. They are **not confidence intervals or
guarantees**; correlated repeated calls do not shrink uncertainty by
`sqrt(number of calls)`. The default reports confidence as `empirical-only`.
Large held-out error is disclosed even when a point estimate can be produced.
No calibration can prove historical accuracy where no measured ground truth
survives.

Token quantities are integers. `estimated_tokens` rounds the raw
`feature_total * coefficient` point prediction to the nearest whole token;
`empirical_range` floors its lower bound and ceils its upper bound outward
(never inward past the rounded point estimate), so the reported range always
encloses `estimated_tokens`.


## Resource and compatibility boundary

Transcript reading is bounded before member allocation: 64 MiB per transcript,
4 MiB per JSON line and 200,000 events. JSON inputs/outputs are bounded to
16 MiB, selection to 1,000 sessions, and calibration/estimation to 20,000 total
call rows. Invalid JSON, partial tails, changed live input and unsafe files are
explicit failures, not truncated success. This is a synchronous library/CLI,
not an event-loop operation.

Live directories and standard tar.gz/ZIP archives use the existing pinned file
and container boundaries without extracting into a temporary session tree.
Third-party codecs remain constructible; bounded reads explicitly refuse a
codec that has not implemented the optional bounded-member interface rather
than falling back to an unbounded read.

Consumer pricing, daily catalogs, HTML/chart presentation, process-log accounting
and runtime adoption remain separately owned contracts. Private transcripts,
calibration cohorts and consumer rate cards do not belong in upstream fixtures.
