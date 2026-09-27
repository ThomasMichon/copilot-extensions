<!--
  DRAFT -- NOT YET COMPILED. See efforts/active/promotion-failure-reactive-fix-agent/README.md
  (Phase 2) for full context. `gh aw compile` has not been run against this file yet --
  this session's `gh` account hit a SAML-SSO-enforcement wall trying to install the
  `github/gh-aw` extension (`gh extension install github/gh-aw` -> HTTP 403, SSO
  authorization required for the `github` org, which this account cannot grant). This
  file is a well-researched starting draft, not a verified-compilable source. See the
  effort's Journal for what was confirmed against gh-aw's public docs vs. what still
  needs live verification once `gh aw compile` is actually runnable somewhere.

  Design note: the effort's own Plan (Phase 2) originally preferred wiring this as a
  `workflow_call` reusable-workflow job invoked directly from `validate-and-promote.yml`,
  passing Phase 1's already-verified outputs (SHA, signature, log excerpt) as `with:`
  inputs. gh-aw's documented trigger surface (Trigger Events reference) covers standard
  GitHub Actions events -- issues, pull_request, schedule, workflow_dispatch, etc. -- but
  does NOT clearly document `workflow_call` as a supported `on:` trigger for an
  agentic-workflow source file. Rather than gamble on an unconfirmed shape, this draft
  uses the Plan's own documented FALLBACK shape instead: a real `issues: labeled` trigger,
  gated on the `ci-failure-signature` label `tools/ci_failure_watchdog.py` already applies.
  This sidesteps the whole `workflow_run` / dev-branch-filter footgun class entirely
  (report-failure's own hard-won `workflow_run` fixes do not even apply here), since the
  filed issue itself already carries every diagnostic fact Phase 1 extracted (signature,
  run link, commit SHA, log excerpt) directly in its body -- no re-derivation needed.
  Confirm this reasoning holds once `gh aw compile` is actually runnable; if
  `workflow_call` turns out to be supported after all, revisit which shape to use.
-->
---
description: "Attempts a scoped, reviewed fix for one tracked dev CI-failure signature (promotion-failure-reactive-fix-agent effort, Phase 2)."
intent: "Shorten how long a red dev validation run blocks every pending contributor's work, by attempting a bounded, intent-preserving fix through the repo's own ordinary contribution path -- never a privileged or unreviewed one. Realizes visions/ci-failure-remediation."
labels: ["automation", "ci", "reactive-fix"]

on:
  issues:
    types: [labeled]

# Only proceed for the exact label tools/ci_failure_watchdog.py applies -- never react
# to an arbitrary issue label, and never react to an issue this workflow's own fix
# attempt created (avoid a reactive loop; see the excluded-files/protected-files note
# below for why the fix-attempt's own output can't re-trigger this same watchdog path).
if: github.event.label.name == 'ci-failure-signature'

# TODO(successor): confirm engine auth path with the operator -- effort's own Plan
# names two options (org-billing `copilot-requests: write` vs. a `COPILOT_GITHUB_TOKEN`
# PAT under Account permissions -> Copilot Requests: Read). Neither is wired yet.
# Draft below assumes the PAT path (more likely to work without an org Copilot
# subscription with centralized billing) but this is NOT a made decision -- ask
# before merging.
engine: copilot

# Read-only baseline, explicit per Phase 2's own guardrail checklist -- only the
# safe-outputs stage below (a separate, permission-controlled job gh-aw generates)
# ever holds a write credential.
permissions:
  contents: read
  issues: read

# TODO(successor): confirm the exact bash allowlist needed to (a) check out the
# failed commit, (b) run the specific failing test(s) named in the issue body to
# reproduce and then confirm a fix, and (c) run this repo's own test-supervisor-
# equivalent invocation pattern (this repo runs `uv run --extra dev pytest -q`
# directly in CI, per validate-and-promote.yml's own `full` job -- confirm whether
# an unattended agent should run bare pytest here or route through some bounded
# wrapper before landing on a final allowlist).
tools:
  bash:
    - "git log *"
    - "git diff *"
    - "git blame *"
    - "uv run --extra dev pytest *"

safe-outputs:
  create-pull-request:
    title-prefix: "[ci-fix] "
    labels: ["automation", "ci-fix-attempt"]
    draft: true
    base-branch: "dev"
    max: 1
    fallback-as-issue: true
    auto-close-issue: false
    # TODO(successor): verify the exact protected-files / excluded-files schema
    # against the live `gh aw compile` output once compilable -- this draft's intent
    # is: reject (or fall back to a review issue, never silently drop) any patch
    # touching `.github/workflows/**` or a version field in `plugin.json` /
    # `pyproject.toml` / `marketplace.json`, per Phase 2's own explicit-out-of-scope
    # guardrail. Confirm gh-aw's built-in protected-file manifest already covers
    # `.github/workflows/**` by default (the docs suggest code-writing safe outputs
    # enforce *some* protected-file set by default) before assuming this config is
    # additive vs. redundant.
    protected-files: "fallback-to-issue"
---

# Attempt a scoped fix for a tracked CI-failure signature

You are responding to a single tracked `dev` validation failure, filed
automatically by `tools/ci_failure_watchdog.py`
(promotion-failure-reactive-fix-agent effort, Phase 1). This issue is your
**entire** scope. Do not look for, or touch, anything else.

## The diagnostic record (read it first)

Issue #${{ github.event.issue.number }}:

```
${{ github.event.issue.body }}
```

This body already carries the failing job name, the failing test node id (when
one was parseable), the run link and commit SHA, and a log excerpt. Treat this
as your starting evidence, not your only evidence -- confirm it against the
live repository state before acting (the `dev` branch has very likely moved
forward since this issue was filed).

## Your charter -- read this before touching anything

Your job is never "make the failing test green." Your job is to **preserve
the intent** of whatever change is judged responsible for this failure -- the
test's intent, the implementation's intent, or both.

**Default expectation: most failures are flaky tests, not real regressions.**
A test is flaky here specifically when it over-specifies its environment or
timing rather than the behavior it protects -- hardcoded OS assumptions,
an *incidental* real subprocess/`git` call where process startup isn't what's
under test, ambient env vars, real wall-clock time, or an unsynchronized
async/concurrency race. For this class, correct the *test* itself (isolate
the dependency, inject a fake, add proper synchronization/deterministic
timing) -- never weaken or delete the assertion, and never touch unrelated
implementation code.

**Not every real subprocess/`git` boundary is overreach.** This repo's
`TESTING.md` explicitly requires concurrency and process-lifecycle tests to
keep exercising *real* process boundaries -- check whether the failing test
is one of these before "fixing" it, or you will rewrite valid, intentional
integration coverage.

**But triage first -- do not assume "test's fault" by default.** Read: (a)
what invariant the failing assertion actually protects, not just its literal
condition; (b) recent history on both sides -- `git log`/`git blame` on the
failing test *and* on the implementation path it exercises -- to find
whichever changed most recently and whether that change was deliberate or an
accidental regression.

**Decision rule:** "deliberate" alone is not sufficient to justify updating
the test -- a deliberate implementation change can still be *wrong*: it can
violate a genuine pre-existing invariant or contradict established
intent/vision even though it was made on purpose. Update the test's
expectation only when the implementation's change was both deliberate *and*
itself consistent with established intent. In every other case -- an
accidental regression, or a deliberate change that conflicts with a genuine
invariant or established intent -- fix the *implementation*, not the test.
State which judgment you made and why in your pull request description.

**Explicitly forbidden, regardless of triage outcome:** deleting, skipping,
`xfail`-ing, or broadly loosening a test's assertion as a way to avoid making
that judgment.

**Stay within vision, not just within this task's own scope limits:** your
fix must never introduce new capability, behavior, or design the codebase
didn't already have -- it restores or aligns with already-established intent,
it never invents one. If the "obvious" fix would require a genuinely new
design decision, that is a signal to escalate, not decide unilaterally.

**If you cannot confidently determine which side's intent should win, do not
guess.** Comment on this issue explaining what you found and why the judgment
isn't clear, and stop -- do not open a pull request.

## Explicitly out of scope, permanently

Never touch `.github/workflows/**`, and never hand-edit a version field in
`plugin.json`/`pyproject.toml`/`marketplace.json` (add a changefile instead,
exactly like any other contributor, per `CONTRIBUTING.md`). If your diagnosis
seems to require either of these, stop and escalate instead -- do not attempt
a partial fix that avoids them by coincidence.

## Before opening a pull request

Actually run the specific failing test (and its immediate neighbors) to
confirm your fix resolves the real failure without introducing a new one.
Never open a PR for a fix you have not locally verified.

## Output

If you found and fixed the issue: open a pull request against `dev` with a
clear description naming which side's intent was wrong and why, per the
decision rule above. It will go through this repository's normal review —
you are never authorized to merge it yourself.

If you could not confidently resolve the triage judgment, or the fix would
require touching an out-of-scope path: comment on this issue explaining what
you found, and do not open a pull request.

