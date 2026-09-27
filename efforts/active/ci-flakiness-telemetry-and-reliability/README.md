# CI Reliability & Flakiness Telemetry

- **Slug:** `ci-flakiness-telemetry-and-reliability`
- **Repo:** ThomasMichon/copilot-extensions
- **Branch(es):** per-slice PRs against `dev`
- **Created:** 2026-09-27
- **Status:** Draft
- **Umbrella issue:** _pending — file once this effort's plan is reviewed and merged_
- **Sub-issues:** aperture-labs#7715 (flaky `test_first_use_provision_is_serialized`, filed on Gitea per facility convention, tracked here as the first known concrete item)

## Guiding Intent

`dev`'s CI should be trustworthy: a red run should mean a real regression, not
noise. This session found and fixed two genuine regressions that had gone
unnoticed because they merged despite red PR-time checks (#4239, #4242), plus
governance gaps that let that happen (now closed: CODEOWNERS + review-gate
rulesets). Separately, this session hit the *same* flaky test
(`test_first_use_provision_is_serialized`) fail independently on two
completely unrelated PRs, and observed an `identifier leak guard` check that
is red on every single PR, unconditionally (missing
`FORBIDDEN_IDS_FACILITY`/`FORBIDDEN_IDS_WORK` repo secrets — a config gap, not
a code defect). Both are exactly the kind of standing noise that erodes trust
in "red means something's actually wrong" and that this effort exists to find
and eliminate systematically, rather than one flake at a time as they happen
to be noticed.

## Participants

Solo effort — driven directly against `ThomasMichon/copilot-extensions` via
per-slice worktrees + PRs, no multi-agent coordination required at this
scope.

## Context

- This session's own investigation (2026-09-27) root-caused a multi-hour `dev`
  CI stall to two independent test regressions (#4239 agent-logger,
  #4242 copilot-extensions-harness), found and closed a governance gap that
  let both merge despite red PR-time checks (CODEOWNERS root scope + a new
  "dev branch policy: review required (maintainer bypass)" ruleset, `PR gate`
  aggregate required-check job), and along the way hit
  `libs/payload-invocation/tests/test_generate.py::test_first_use_provision_is_serialized`
  fail independently twice, unrelated to either PR's own diff — filed as
  aperture-labs#7715.
- Also observed, every single time this session checked `pr checks` on any
  PR: an `identifier leak guard` check that is **always red**, because
  `FORBIDDEN_IDS_FACILITY`/`FORBIDDEN_IDS_WORK` repository secrets are not
  configured (the check itself reports this plainly: "the trusted scan cannot
  enforce the identifier denylist yet"). It is not a required check today, so
  it doesn't block merges, but it is pure, permanent noise on every PR's
  check list. **Already fully tracked**: `efforts/active/ci-identifier-leak-guard/`
  (#3923) owns this secret setup end-to-end — this effort only needs to
  confirm the telemetry stops flagging it once that effort lands, not
  re-plan the fix.
- No existing effort covers this specifically. `promotion-failure-reactive-fix-agent`
  / the `ci-failure-remediation` vision cover *reacting to one red run at a
  time* (detection, dedup, a bounded fix-attempt agent) — this effort is
  complementary but distinct: it mines **historical run data** to find
  *recurring, high-impact* noise systematically, rather than reacting to
  whichever run happens to be red right now. Check for overlap/handoff points
  with that effort before duplicating any detection/dedup machinery it
  already has.
- GitHub Actions REST API surfaces used successfully this session for
  ad-hoc investigation (a starting point for the telemetry pipeline, not
  a final design):
  - `GET /repos/{owner}/{repo}/actions/runs?event=push&branch=dev` — per-branch
    run history with `conclusion` (`success`/`failure`/`cancelled`/...).
  - `GET /repos/{owner}/{repo}/actions/runs/{run_id}` — single run detail.
  - `gh run view <id> --job <job_id> --log-failed` — per-job failure logs
    (used manually this session; would need a scriptable equivalent, e.g.
    the same REST log-download endpoint, for automated parsing).
  - A **same-commit rerun that then succeeds** (observed directly this
    session, multiple times) is a strong, cheap flakiness signal worth
    building the telemetry around: same `head_sha`, first attempt fails,
    a later attempt (via `run rerun --failed`) succeeds with no code change
    in between.

## Request

Operator (2026-09-27, verbatim): "File it, and then prepare a handoff to
tackle getting the CI running reliably and clean. If we have any way to dig
up flakiness telemetry over run history, let's build out tracking for that,
find the noisiest and blocking issues, and fix them"

## Plan

### Phase 0 — Reconcile with the existing reactive-fix effort
- [ ] Read `efforts/active/promotion-failure-reactive-fix-agent/README.md`
  and the `ci-failure-remediation` vision in full; determine what detection/
  dedup machinery already exists (issue-filing on a red run) and whether this
  effort's telemetry should feed that mechanism, extend it, or stay separate.
  Avoid building a second, competing "notice CI is red" pipeline.

### Phase 1 — Telemetry pipeline _(agent-recommended shape; operator asked for "any way to dig up ... telemetry," not a specific design)_
- [ ] Design and land a script (likely `tools/ci-telemetry.py` or similar) that
  pulls historical workflow-run + job data via the GitHub Actions REST API for
  `dev` (and optionally PR-triggered runs) over a bounded lookback window.
- [ ] Persist results queryably (a checked-in SQLite db is the lightest option
  consistent with this repo's existing tooling conventions — check for
  precedent before inventing a new storage shape; a periodic refresh job is a
  later concern, not this phase's).
- [ ] Detect the **same-commit-reran-and-passed** signal precisely (matches
  this session's own empirical evidence of flaky vs. genuine failures) as the
  primary flakiness heuristic, distinct from a failure that persists until a
  real code fix lands.
- [ ] Surface, per distinct test/job/check identity: total failure count,
  rerun-recovery rate (flaky signal), and **blocking impact** — how many
  *other* PRs/commits were stalled behind each failure (tie back to the
  dev->main promotion-stall pattern this session found: a run that must reach
  `conclusion: success` to trigger promotion).

### Phase 2 — Find the noisiest and most blocking
- [ ] Run the pipeline over available history; produce a ranked report (by
  frequency, and separately by blocking impact — a rare-but-always-fatal
  failure ranks differently than a frequent-but-quickly-recovered one).
- [ ] Include the `identifier leak guard` misconfiguration explicitly in the
  ranking even though it isn't a "failure" in the traditional sense — it's
  100% noisy, 0% blocking (not a required check), which the ranking should be
  able to express, not just silently omit. **Do not re-plan its fix here** —
  it is already fully tracked in `efforts/active/ci-identifier-leak-guard/`
  (#3923), which owns the `FORBIDDEN_IDS_FACILITY`/`FORBIDDEN_IDS_WORK`
  secret setup end-to-end; this effort only needs to confirm the telemetry
  correctly stops flagging it once that effort lands.
- [ ] Confirm aperture-labs#7715 (`test_first_use_provision_is_serialized`)
  surfaces near the top given this session's direct, repeated observation of
  it; use it as a sanity check for the pipeline's own correctness.

### Phase 3 — Fix the top offenders
- [ ] Fix aperture-labs#7715 (tighten the lock-serialization test/harness so
  the race is deterministic under CI load, per that issue's own body).
- [ ] Resolve the `identifier leak guard` noise **by driving the existing
  `efforts/active/ci-identifier-leak-guard/` effort (#3923) to completion**,
  not by re-planning it here — that effort already owns the
  `FORBIDDEN_IDS_FACILITY`/`FORBIDDEN_IDS_WORK` secret setup.
- [ ] Work down the Phase 2 ranking, opening one PR per fix (or a small
  batch when fixes are trivially related), closing/updating aperture-labs
  issues as each lands.

## Validation Plan

- [ ] The telemetry pipeline's own output is spot-checked against this
  session's direct manual findings (the two genuine #4239/#4242 regressions,
  the #7715 flake, the identifier-leak-guard noise) before trusting it for
  anything not already manually confirmed.
- [ ] After Phase 3's fixes land, re-run the telemetry pipeline over a fresh
  window and confirm the fixed items' failure/noise rate actually dropped
  (not just that a fix merged) — a fix that doesn't move the needle in the
  telemetry itself is not yet proven.
- [ ] `dev`'s CI achieves a materially higher clean-run rate than the "0/10
  observed" baseline this session measured on 2026-09-27 (see the
  mux-daemon-fix session history for that measurement) over a comparable
  observation window.

## Proposal

_Pending — Phase 0/1 findings will shape the concrete pipeline design._

## Journal

### 2026-09-27 — Kickoff
- Effort created directly following a live session that root-caused and fixed
  two `dev`-CI-stalling regressions (#4239, #4242), closed a governance gap
  (CODEOWNERS + review-gate rulesets) that let them merge red, and along the
  way surfaced a repeat flaky test (aperture-labs#7715) and a permanently-red
  non-blocking `identifier leak guard` check. Operator asked to file the flake
  and hand off toward systematic flakiness telemetry + fixing the noisiest/
  blocking issues, rather than continuing to react one flake at a time.
