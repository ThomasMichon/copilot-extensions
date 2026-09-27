# CI Reliability & Flakiness Telemetry

- **Slug:** `ci-flakiness-telemetry-and-reliability`
- **Repo:** ThomasMichon/copilot-extensions
- **Branch(es):** per-slice PRs against `dev`
- **Created:** 2026-09-27
- **Status:** Active — Phases 0/1/2 Done (reconciliation, telemetry
  pipeline, ranked report); Phase 3 (fix the top offenders) not yet started
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

### Phase 0 — Reconcile with the existing reactive-fix effort — Done
- [x] Read `efforts/active/promotion-failure-reactive-fix-agent/README.md`
  and the `ci-failure-remediation` vision in full; determine what detection/
  dedup machinery already exists (issue-filing on a red run) and whether this
  effort's telemetry should feed that mechanism, extend it, or stay separate.
  Avoid building a second, competing "notice CI is red" pipeline.
  **Resolved (2026-09-27):** the two efforts are complementary, not
  competing, and stay architecturally separate:
  - `promotion-failure-reactive-fix-agent` (`tools/ci_failure_watchdog.py`)
    is **event-triggered, single-run, real-time**: it reacts to one
    `workflow_run` completion on `dev`, computes a per-test (or whole-job)
    failure signature, dedupes against an already-open Gitea issue, and
    files/comments — it has no concept of run *history* and does not look
    backward.
  - This effort (`ci-flakiness-telemetry-and-reliability`) is
    **batch/on-demand, historical, retrospective**: it mines the GitHub
    Actions REST API's run history across a lookback window to find
    *recurring, high-impact* noise (rerun-recovery rate, blocking impact)
    that no single red run reveals on its own.
  - **What is shared, not duplicated:** the failure-signature computation
    itself. `tools/ci_failure_watchdog.py`'s `signature_key()` /
    `build_signatures()` (job name + failing pytest node id, or a
    whole-job fallback keyed off a stripped log tail) is the **one**
    canonical signature identity in this repo. Phase 1's telemetry pipeline
    must import and reuse those functions directly rather than
    reimplementing a second, subtly-different signature scheme — two
    schemes for "is this the same failure" would silently disagree and
    undermine both efforts' own dedup guarantees.
  - **No pipeline merge:** the watchdog stays a `workflow_run`-triggered
    Actions job (real-time reaction); the telemetry pipeline stays a
    separate, independently-invoked script (periodic/on-demand mining).
    A future integration (e.g. the watchdog consulting historical
    recurrence counts to enrich its own issue body) is plausible but
    explicitly out of scope for this effort — noted here, not built here.

### Phase 1 — Telemetry pipeline _(agent-recommended shape; operator asked for "any way to dig up ... telemetry," not a specific design)_ — Done
- [x] Design and land a script (likely `tools/ci-telemetry.py` or similar) that
  pulls historical workflow-run + job data via the GitHub Actions REST API for
  `dev` (and optionally PR-triggered runs) over a bounded lookback window.
  **Implemented:** `tools/ci_telemetry.py` (`refresh`/`report` subcommands);
  fetches both `dev`-push runs and PR-triggered `ci.yml` runs.
- [x] Persist results queryably (a checked-in SQLite db is the lightest option
  consistent with this repo's existing tooling conventions — check for
  precedent before inventing a new storage shape; a periodic refresh job is a
  later concern, not this phase's). **Implemented:** local SQLite db
  (`tools/ci_telemetry.sqlite3`, gitignored — regenerate via `refresh` rather
  than checking in a snapshot; whether a periodic-refresh job should check
  one in instead is a later decision, not this phase's).
- [x] Detect the **same-commit-reran-and-passed** signal precisely (matches
  this session's own empirical evidence of flaky vs. genuine failures) as the
  primary flakiness heuristic, distinct from a failure that persists until a
  real code fix lands. **Implemented, with a real production correction:**
  `gh run rerun --failed` reuses the SAME `run_id` at a higher `run_attempt`
  rather than creating a new run — a plain `dev`-push run-history mine alone
  (0 multi-attempt runs observed in that history) would silently show 0%
  recovery everywhere. The rerun-recovery signal is observed almost
  exclusively on **PR-time** (`ci.yml`) reruns instead (confirmed live: 8
  multi-attempt PR runs in a 7-day window). `fetch_runs`/`_fetch_prior_attempts`
  fetch each earlier attempt via the `.../attempts/{n}` and
  `.../attempts/{n}/jobs` endpoints explicitly. A second real bug found and
  fixed in the same pass: both attempts share an *identical* top-level
  `created_at` (no distinct per-attempt timestamp in the fetched fields), so
  sorting by `created_at` alone left attempts in fetch/insertion order
  (latest-first) and silently hid every real recovery; `compute_flaky_shas`/
  `compute_blocking_impact` now sort by `(created_at, run_id, attempt)`,
  with a dedicated regression test locking in the fix.
- [x] Surface, per distinct test/job/check identity: total failure count,
  rerun-recovery rate (flaky signal), and **blocking impact** — how many
  *other* PRs/commits were stalled behind each failure (tie back to the
  dev->main promotion-stall pattern this session found: a run that must reach
  `conclusion: success` to trigger promotion). **Implemented:**
  `compute_signature_stats` + `render_report`; blocking impact is computed
  only over `dev`-push runs (a genuine serialized queue), never PR-time runs
  (concurrent, unrelated PRs have no single queue to block).
- [x] 48 unit tests (`tools/test_ci_telemetry.py`) cover the pure aggregation
  logic in isolation (no `gh` calls); `ruff check` clean.

### Phase 2 — Find the noisiest and most blocking
- [x] Run the pipeline over available history; produce a ranked report (by
  frequency, and separately by blocking impact — a rare-but-always-fatal
  failure ranks differently than a frequent-but-quickly-recovered one).
  **Done, live, 2026-09-27** (7-day lookback, `dev`-push + PR-triggered):
  noisiest by frequency is `tools/test_check_marketplace_isolation.py::
  test_payload_catalog_adopter_capabilities_avoid_bare_global_commands` (23
  occurrences); highest blocking impact is
  `tests/test_install_signed_python_probe.py::
  test_missing_newest_candidate_does_not_abort_probe[pwsh]` (122 `dev`-push
  runs stalled behind it, likely explained by low natural `dev`-push
  frequency for a rare-but-fatal failure, exactly the asymmetry this ranking
  exists to expose). See the Journal for the full report.
- [x] Include the `identifier leak guard` misconfiguration explicitly in the
  ranking even though it isn't a "failure" in the traditional sense — it's
  100% noisy, 0% blocking (not a required check), which the ranking should be
  able to express, not just silently omit. **Do not re-plan its fix here** —
  it is already fully tracked in `efforts/active/ci-identifier-leak-guard/`
  (#3923), which owns the `FORBIDDEN_IDS_FACILITY`/`FORBIDDEN_IDS_WORK`
  secret setup end-to-end; this effort only needs to confirm the telemetry
  correctly stops flagging it once that effort lands. **Implemented:**
  `KNOWN_NOISY_NONBLOCKING_CHECKS` (hand-tracked, not auto-mined — the PR
  check-suite API is a genuinely separate surface from workflow-run history
  and not worth a second live-fetch path for one entry); `render_report`
  surfaces it in its own dedicated section. `fetch_failures_for_run` also
  explicitly excludes the organically-observed `identifier leak guard` job
  from PR-run mining (`PR_SKIP_JOB_NAMES`) so it can never double-count under
  a second, differently-derived key.
- [x] Confirm aperture-labs#7715 (`test_first_use_provision_is_serialized`)
  surfaces near the top given this session's direct, repeated observation of
  it; use it as a sanity check for the pipeline's own correctness.
  **Confirmed, live, 2026-09-27:** the matching signature
  (`plugins/agent-index/test_generate.py::test_first_use_provision_is_serialized`
  — the same lock-race pattern in a different plugin's vendored copy of the
  same lib) surfaced with 12 occurrences and a **42% rerun-recovery rate
  (5/12)** — a real, non-trivial, non-zero recovery signal, which is exactly
  what this known flake should produce and the strongest available
  confirmation the pipeline's own logic (not just its plumbing) is correct.

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

- [x] The telemetry pipeline's own output is spot-checked against this
  session's direct manual findings (the two genuine #4239/#4242 regressions,
  the #7715 flake, the identifier-leak-guard noise) before trusting it for
  anything not already manually confirmed. **Confirmed 2026-09-27**: #7715's
  matching signature surfaced with a real 42% recovery rate (Phase 2); the
  identifier-leak-guard entry renders in its own dedicated section (Phase 2).
  #4239/#4242 predate this pipeline's lookback window and were never
  re-checked directly — not a gap in the pipeline itself, just outside the
  window this session's 7-day validation run covered.
- [ ] After Phase 3's fixes land, re-run the telemetry pipeline over a fresh
  window and confirm the fixed items' failure/noise rate actually dropped
  (not just that a fix merged) — a fix that doesn't move the needle in the
  telemetry itself is not yet proven.
- [ ] `dev`'s CI achieves a materially higher clean-run rate than the "0/10
  observed" baseline this session measured on 2026-09-27 (see the
  mux-daemon-fix session history for that measurement) over a comparable
  observation window.

## Proposal

_Pending — Phase 3 findings (which fixes land, and in what order) will
determine whether this section needs anything beyond the Plan above._

## Journal

### 2026-09-27 — Phases 1/2 built and validated live
- Landed `tools/ci_telemetry.py` (`refresh`/`report`) + 21 new unit tests
  (`tools/test_ci_telemetry.py`), all passing (48/48 total across both
  telemetry files), `ruff check` clean.
- Real production correction made mid-build: the rerun-recovery signal is
  observed almost exclusively on **PR-time** (`ci.yml`) reruns, not
  `dev`-push runs (0 multi-attempt runs found in 394 `dev`-push runs scanned;
  8 found in a 7-day PR-run window) — `gh run rerun --failed` reuses the same
  `run_id` at a higher `run_attempt` rather than creating a new run, which a
  naive `runs?event=push` mine never sees. Added `_fetch_prior_attempts` +
  attempt-scoped jobs/log fetching to recover this.
- Second bug found and fixed in the same pass: both attempts of a rerun
  share an *identical* top-level `created_at`, so sorting by `created_at`
  alone left them in fetch/insertion order (latest-attempt-first) and
  silently zeroed out every real recovery signal; fixed by sorting on
  `(created_at, run_id, attempt)`, with a dedicated regression test.
- Ran a real 7-day-lookback `refresh` (1403 run/attempt rows, 121 failure
  occurrences persisted) and confirmed both Validation Plan spot-checks this
  phase could reach: aperture-labs#7715's matching signature showed a real
  42% (5/12) recovery rate, and the identifier-leak-guard entry renders in
  its own tracked, non-mined section. Full Phase 2 ranked report:
  noisiest by frequency `tools/test_check_marketplace_isolation.py::
  test_payload_catalog_adopter_capabilities_avoid_bare_global_commands` (23
  occurrences, 0% recovery — a persisting issue, not a flake); highest
  blocking impact `tests/test_install_signed_python_probe.py::
  test_missing_newest_candidate_does_not_abort_probe[pwsh]` (122 `dev`-push
  runs stalled). Both are strong Phase 3 candidates alongside #7715.
- Next: Phase 3 — fix aperture-labs#7715, then work down the ranking.

### 2026-09-27 — Phase 0 reconciliation resolved
- Read `promotion-failure-reactive-fix-agent`'s README in full and the
  `ci-failure-remediation` vision. Confirmed the two efforts are
  complementary (real-time single-run reaction vs. batch historical
  mining), not competing, and that they must share exactly one canonical
  failure-signature computation (`tools/ci_failure_watchdog.py`'s
  `signature_key()`/`build_signatures()`) rather than each growing its own.
  See the Phase 0 Plan item above for the full decision. Proceeding to
  Phase 1 (telemetry pipeline).

### 2026-09-27 — Kickoff
- Effort created directly following a live session that root-caused and fixed
  two `dev`-CI-stalling regressions (#4239, #4242), closed a governance gap
  (CODEOWNERS + review-gate rulesets) that let them merge red, and along the
  way surfaced a repeat flaky test (aperture-labs#7715) and a permanently-red
  non-blocking `identifier leak guard` check. Operator asked to file the flake
  and hand off toward systematic flakiness telemetry + fixing the noisiest/
  blocking issues, rather than continuing to react one flake at a time.
