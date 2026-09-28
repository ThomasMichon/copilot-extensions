# Automated CI-Failure Remediation — Vision

- **Subject:** the standing capability that reacts to a red validation run on
  the trunk branch — detecting it, avoiding duplicate noise, and attempting a
  scoped, reviewed fix through the repo's own ordinary contribution path
- **Scope:** leaf (concrete cross-cutting capability)
- **Status:** Active
- **Last revised:** 2026-09-26
- **Reality docs:** [`tools/ci_failure_watchdog.py`](../../tools/ci_failure_watchdog.py), [`.github/workflows/validate-and-promote.yml`](../../.github/workflows/validate-and-promote.yml)

## Purpose & Intent

A red validation run on the trunk blocks every pending contributor's
already-reviewed work, not just whoever's change happened to trigger it — and
a jam that sits unnoticed until a human happens to look is strictly worse than
one a system has already started diagnosing. The pipeline should carry a
standing, safe, reviewed feedback loop: recognize a genuine failure, avoid
spamming duplicate reports for the same recurring cause, and attempt a
scoped fix through the exact same contribution path every other change
already uses — never a special, unreviewed lane.

Success looks like: a red trunk gets a first response — diagnosis, or
diagnosis plus a proposed fix — without waiting on a human to notice it,
while every acceptance decision remains human-reviewed exactly like any other
contributor's change. This is a safety net for jams, not a replacement for
deliberate engineering: it exists to shorten how long a jam persists
unattended, never to substitute for a human deciding what should merge.

## Concepts & Components

- **Detection & dedup** — turning a red validation run into a durable,
  deduplicated record of the failure, so a persistently recurring cause
  accumulates as one tracked record instead of spamming a fresh report every
  run.
- **Fix-attempt mechanism** — a bounded, sandboxed agent invocation that
  reasons about one tracked failure and proposes a change through the
  ordinary review path, never a privileged one.
- **Intent triage** — the reasoning discipline the fix attempt applies before
  touching anything: judging whether a test's expectation or the
  implementation's behavior is the side that diverged from the correct,
  already-established intent, rather than mechanically forcing the run green.
- **Guardrails** — scope limits, review gates, and attempt caps that bound
  what the mechanism can ever propose or how persistently it retries.
- **Escalation** — the fallback whenever triage can't be resolved
  confidently, or the attempt budget is exhausted: control returns to a
  plain, human-facing report, never a silent failure and never a forced
  guess.

## Features

### automatic-detection-and-deduplication

The pipeline recognizes when a shared validation run reports a genuine
failure (as distinct from a transient reporting hiccup in the detection path
itself), and produces exactly one durably tracked record per distinct failure
identity — accumulating repeat occurrences as evidence on that same record
rather than duplicating it.

### bounded-fix-attempt

For a tracked failure, an agent may propose a fix through the repository's
own ordinary contribution path, scoped to the diagnosed failure and nothing
else — never granted a privileged or unreviewed route to land a change.

### intent-preserving-triage

Before proposing any change, the fix attempt diagnoses whether the failing
test's expectation or the implementation's behavior is the side that
diverged from the correct, already-established intent — and prefers
restoring or aligning with that established intent over inventing new
design. A deliberate recent change is not, by itself, proof that the change
was correct; the triage weighs it against the same established intent, not
against its own recency.

### bounded-retry-and-escalation

Repeated failure to resolve the same tracked failure under automated attempts
stops retrying after a bounded number of tries and escalates to a plain,
human-facing report instead of persisting indefinitely.

## Behaviors

### never-a-privileged-merge-path

An automated fix attempt's proposed change is indistinguishable, from every
gate's point of view, from an ordinary contributor's change — subject to the
same review, the same required checks, and the same merge policy. It never
merges itself, and no gate anywhere special-cases its authorship.

### never-touches-its-own-guardrails

An automated fix attempt cannot expand its own scope, alter the pipeline's
own workflow/automation definitions, or change release/version metadata —
those remain human/deliberate-change-only regardless of what a diagnosis
appears to call for. A fix that seems to require touching one of these is a
signal to escalate, not a signal to proceed.

### silence-is-a-designed-outcome

When a failure is already tracked and still within its own cool-down window,
the loop stays silent rather than re-notifying. Noise is treated as a real
cost to the people who depend on this feedback loop, not as an extra margin
of safety.

### untrusted-diagnostic-input-stays-data

Content drawn from a failing run's own output (logs, error text, anything an
untrusted dependency or test can print) is treated as data for the mechanism
to reason about, never as an instruction it obeys — a failure's own output
must never be able to steer what the mechanism does next.

### an-unresolved-judgment-escalates-rather-than-guesses

When the mechanism cannot confidently determine which side's intent should
win, it defers to a human rather than defaulting to either "fix the test" or
"fix the implementation."

## Non-Goals / Boundaries

- Not a general-purpose autonomous coding agent: scope is bounded to a single
  diagnosed validation-failure signature, never open-ended feature work,
  refactors, or unrelated cleanup.
- Never authorizes an unattended merge of its own output, at any confidence
  level.
- Not a substitute for deliberate test-portfolio design: this capability
  reacts to failures after the fact; it does not decide which tests should
  exist or replace the judgment that shapes the test portfolio itself.
- Does not own or modify the release/promotion pipeline's own definition
  (its workflow files, version/release policy, or branch protections) — those
  remain governed by that pipeline's own operating intent, not this
  capability.

## See Also

- Parent vision: none (leaf; a future broader release-pipeline vision may
  adopt this as a child once authored)
- Child visions: none
- Related vision: [`coverage-guided-ci`](../coverage-guided-ci/README.md) —
  a distinct capability at the same trunk-gate event (this vision reacts to
  a red run; that one earns and propagates the coverage baseline from a
  green one)
- Reality docs: [`tools/ci_failure_watchdog.py`](../../tools/ci_failure_watchdog.py),
  [`.github/workflows/validate-and-promote.yml`](../../.github/workflows/validate-and-promote.yml),
  the `promotion-failure-reactive-fix-agent` effort (realizing work)

## Provenance

- **2026-09-26** — Authored to resolve the `promotion-failure-reactive-fix-agent`
  effort's own reconciliation gate: that effort's design deliberately would
  not proceed to its fix-attempt mechanism until this material architecture
  decision was reconciled with standing intent rather than assumed. The
  intent-triage discipline (Features/intent-preserving-triage, Behaviors/
  an-unresolved-judgment-escalates-rather-than-guesses) was mined directly
  from operator guidance given in that same session.
