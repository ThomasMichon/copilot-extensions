# Promotion-Failure Reactive Fix Agent

- **Slug:** `promotion-failure-reactive-fix-agent`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-09-25
- **Status:** Draft
- **Vision:** none yet — this effort establishes the standing policy itself
  (safety envelope + trigger contract for a reactive fix agent); revisit
  once Phase 1 proves out whether it deserves its own harness-guidance
  vision entry.
- **Umbrella issue:** _TBD — file once this effort's plan clears review_

## Guiding Intent

`dev`'s release pipeline (`.github/workflows/validate-and-promote.yml`, the
dev-branch-release-pipeline effort, #3336) only promotes a genuinely green
build — and a red one blocks **every** pending contributor's already-merged
work, not just whoever caused it (see `contributing-to-copilot-extensions`'s
new hard "fix every failing test, never leave it pre-existing" rule). That
rule depends entirely on a human or driving agent noticing the red build and
acting. This effort's goal: when the full validation suite fails on `dev`,
**something reacts automatically** — diagnoses the failure, and attempts a
targeted, narrowly-scoped fix through the repo's own normal contribution
path — so a jam gets a first response even when no one is watching, without
ever weakening the review/merge discipline every other change goes through.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent | Design + build the reactive trigger and its guardrails | `copilot-extensions` worktree |
| GitHub Copilot cloud agent | Performs the actual diagnosis + fix attempt, in its own sandboxed session | issue assignment (see Context) |

## Coordination

- **Topology:** independent per-slice PRs against `dev`; no shared branch.
- **Host (owns PRs):** the participant above, until split further.

## Context

**The literal trigger for this effort:** during a single session
(2026-09-24/25), `dev`'s own full validation suite failed
(`test_monitor_claim_handoff_cutover_stale_reclaim_is_single_winner` in
`agent-worktrees`, likely flaky/concurrency-timing, unrelated to the PR that
happened to be queued behind it) and sat there — blocking every promotion,
including an unrelated, already-verified fix — until a human noticed. That
is exactly the failure mode this effort exists to shorten.

**Constraint that shapes the whole design: `copilot-extensions` is a public
repository.** GitHub's native "Copilot automations" feature (scheduled or
event-triggered Copilot cloud agent runs, configured in the Agents tab) is
**explicitly unavailable on public repos** — confirmed via GitHub's own docs
(`About Copilot automations` § Availability and permissions: "The repository
must be private or internal"). So the turnkey UI-configured automation this
effort's name might suggest is a dead end here; the design has to use a
different, repo-controlled mechanism.

**The mechanism that IS available regardless of repo visibility: issue
assignment.** Assigning a GitHub issue to `copilot` (the Copilot cloud agent
identity) is a separate entry point from "Automations" and works on public
repos — it's the same flow as manually assigning a backlog issue to Copilot
from the UI, just done by a script via the REST/GraphQL API or the `gh` CLI
instead of a person clicking. Copilot cloud agent then researches the repo,
plans, makes changes in its own ephemeral sandbox, and opens a PR — landing
through this repo's **existing, unmodified** `dev`-targeting PR flow. No new
merge path, no new bypass, no elevated trust: the resulting PR is reviewed
and merged exactly like any other contributor's.

**Existing patterns this reuses rather than reinvents** (see the facility's
own custom-agent roster for the shape, even though those specific agents
aren't copilot-extensions-local): `health-diagnosis-filer` and
`efficiency-verdict-filer` are the precedent for "a headless agent reacts to
a signal, diagnoses root cause, dedupes via an existing tracked item, and
either reports or files — never force-fixes unilaterally." This effort's
agent is one step further (it's allowed to *attempt* a fix, not just file),
which is exactly why the safety envelope below matters more here, not less.

## Request

Operator (verbatim): "let's also look into (carefully) setting up a cloud
agent that will react to failed promotion runs and attempt targeted test
fixes."

## Plan

### Phase 1 — Detection + dedup (no autonomous fix yet; report-only)
- [ ] Add a step to `validate-and-promote.yml`'s `full`/`guards-full-sweep`/
      `worktree-manager` jobs (or a new job gated on `if: failure()` after
      them) that, on any failure, extracts a compact failure signature: which
      job(s) failed, the specific failing test node id(s) (pytest's own
      `FAILED <path>::<test>` lines), and a short log excerpt.
- [ ] Dedup against existing open issues before filing anything new (search
      by the test node id, not just the plugin name) — reuse the exact
      pattern `health-diagnosis-filer`/`reality-drift-filer` already use
      (VEI + Gitea-style search, adapted to `gh issue list --search`), so a
      persistently-flaky test gets ONE tracked issue that accumulates
      occurrences, never a new issue per red run.
- [ ] File (or comment on) that issue, plain and factual: which run, which
      test(s), the log excerpt, a link back to the run. No fix attempt yet.
- [ ] Rate-limit: never file/comment more than once per N hours for the same
      signature (open question: N — start conservative, e.g. 6h).

### Phase 2 — Assign to Copilot cloud agent (the actual "attempt a fix")
- [ ] Once Phase 1's detection+dedup is proven reliable (no false positives,
      no duplicate-issue spam) over a real observation window, extend the
      filed/updated issue to **also assign it to `copilot`** via the `gh`
      CLI (`gh issue edit <#> --add-assignee copilot`) or the equivalent
      GraphQL mutation — confirmed to work independent of the
      public-repo Automations restriction above.
- [ ] Write the issue body as a genuinely well-scoped Copilot cloud agent
      prompt, not just a human-facing bug report: name the exact failing
      test(s), the exact log excerpt, and an explicit **instruction to scope
      the fix narrowly** — fix the test or the minimal source regression it
      names, never touch `.github/workflows/**` (that's `main-gate`'s
      workflow-only bootstrap lane, a different mechanism entirely, and an
      autonomous agent must never have a path that even looks like it could
      qualify for that exception) and never modify `plugin.json`/
      `pyproject.toml`/`marketplace.json` version fields by hand (add a
      changefile per `CONTRIBUTING.md`, exactly like any other contributor).
- [ ] Confirm (read the actual cloud-agent-authored PR when the first one
      lands) that it lands as an ordinary PR against `dev`, subject to the
      same non-blocking Copilot review and the same required checks as
      every other PR — no special-case merge path introduced anywhere in
      this effort.

### Phase 3 — Guardrails and walk-back criteria (do not skip)
- [ ] **Never auto-merge the resulting PR.** A human or the driving agent
      reviews it like any other contributor's PR before merge — this
      effort automates the *diagnosis + fix attempt*, never the *acceptance*
      of the fix. This is the one guardrail everything else in this effort
      is downstream of; do not relax it as part of any later phase without
      an explicit, separately-reasoned decision.
- [ ] Cap attempts per signature (e.g., after 2 failed cloud-agent attempts
      at the same test, stop assigning and escalate to a plain human-facing
      issue instead of retrying indefinitely).
- [ ] Explicitly out of scope for this agent, permanently: workflow files,
      version fields, branch-protection/ruleset changes, anything requiring
      `--admin` — all of this same session's dev-branch-release-pipeline
      hardening exists specifically to make those paths *harder* to reach
      by mistake; this effort must not create a new one.
- [ ] Define a walk-back/expansion criterion analogous to
      dev-branch-release-pipeline's own Phase 6 (e.g., N clean cloud-agent
      fixes with zero reverts before considering any scope widening).

## Validation Plan

- [ ] Dry-run Phase 1's detection step against a deliberately-reproduced
      failing run (or the real `agent-worktrees` flake from this effort's
      own Context section, if it recurs) before wiring it to file anything
      for real.
- [ ] Confirm dedup actually prevents a second issue/comment for the same
      signature within the rate-limit window, using two synthetic failures.
- [ ] Confirm a Phase 2 cloud-agent-authored PR is indistinguishable, from
      `main-gate`'s and every other guard's point of view, from an ordinary
      contributor PR — no special-cased author/branch check anywhere.
- [ ] Confirm the explicit out-of-scope instructions actually hold: seed one
      trial where the "obvious" fix would touch a version field or a
      workflow file, and confirm the agent's resulting PR does neither
      (escalates instead).

## Proposal

_Pending._

## Journal

### 2026-09-25 — Kickoff
- Effort created from the operator's verbatim request (see Request). The
  same session had just hardened `main-gate` against exactly the class of
  mistake (an autonomous-feeling action reaching for more trust than it
  needed) this effort's Phase 3 guardrails exist to prevent by design,
  not by discipline alone — deliberately built that way rather than
  trusting a future session to remember the lesson.
- Researched GitHub's Copilot cloud agent mechanics directly (not from
  memory): confirmed the native "Automations" feature is unavailable on
  this public repo, and confirmed the viable alternative (issue-assignment
  to `copilot`, a separate entry point) works regardless of repo
  visibility.
- **Deliberately not implemented yet** — this is Phase 1's own design,
  captured durably per the operator's "carefully look into" framing, not a
  green light to wire live automation without a review pass first. Next
  session: submit this plan for review (this repo's own non-blocking
  Copilot pass, at minimum), then execute Phase 1 only.
