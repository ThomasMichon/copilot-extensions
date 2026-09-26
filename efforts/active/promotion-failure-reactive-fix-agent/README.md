# Promotion-Failure Reactive Fix Agent

- **Slug:** `promotion-failure-reactive-fix-agent`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-09-25
- **Status:** Active — Phase 1 implemented and landed; Phase 2 gated (see below)
- **Vision:** none yet — this effort establishes the standing policy itself
  (safety envelope + trigger contract for a reactive fix agent); revisit
  once Phase 1 proves out whether it deserves its own harness-guidance
  vision entry. **Reconciliation gate (added after review, see Journal):**
  choosing `gh-aw` as the Phase 2 mechanism is a material architecture
  decision made ahead of that vision reconciliation. It is a research
  finding and a provisional design choice, not a settled standing pattern —
  Phase 2 must not begin until this is either folded into an existing
  vision (`harness-guidance` was checked and does not fit — it covers
  ambient guidance delivery, not automated-fix mechanism selection) or
  captured in a new one. Phase 1 (detection+dedup, no fix-attempt
  mechanism yet) is unaffected and can proceed independently.
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
| GitHub Agentic Workflows (`gh-aw`) agent job | Performs the actual diagnosis + fix attempt, sandboxed, writes gated through `safe-outputs` | a compiled `.github/workflows/*.lock.yml` triggered by promotion failure (see Context) |
| GitHub Copilot cloud agent (fallback mechanism) | Same diagnosis+fix role, if `gh-aw` proves unworkable | issue assignment (see Context) |

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

**The primary mechanism chosen: GitHub Agentic Workflows (`gh-aw`,
`github/gh-aw`).** This is a *different* feature from "Copilot automations"
above — a `gh` CLI extension that compiles a Markdown+YAML-frontmatter
workflow definition into an ordinary `.lock.yml` **GitHub Actions**
workflow. Because it runs as plain Actions rather than through the gated
Automations UI, **it carries no public/private-repo restriction** (confirmed
from `gh-aw`'s own setup docs: the only prerequisites are write access,
Actions enabled, and an AI-engine account — GitHub Copilot itself qualifies).
Two things make it a materially better fit than issue-assignment alone:

- **"CI failure investigation" is one of its explicitly documented canonical
  use cases** — this effort isn't bending a general-purpose tool to fit, it's
  the tool's own intended shape.
- **Its security model is built-in, not hand-rolled.** Agent jobs are
  **read-only and sandboxed by default**; any actual write (opening a PR,
  commenting, editing a file) is buffered through a **`safe-outputs`** stage
  — a separate, deterministic, permission-scoped step that validates and
  applies the change, rather than the agent's own sandboxed job holding
  write credentials directly. That is a stronger, more legible version of
  this effort's own Phase 3 guardrails (below) than anything hand-built on
  top of raw issue-assignment would give us for free.

**Fallback mechanism, if `gh-aw` proves unworkable in practice:** assigning a
GitHub issue to `copilot` (the Copilot cloud agent identity) is a separate
entry point from "Automations" that also works regardless of repo
visibility — the same flow as manually assigning a backlog issue to Copilot
from the UI, done by script via the REST/GraphQL API or `gh` CLI instead of a
person clicking. Copilot cloud agent then researches the repo, plans, makes
changes in its own ephemeral sandbox, and opens a PR. Either mechanism lands
through this repo's **existing, unmodified** `dev`-targeting PR flow: no new
merge path, no new bypass, no elevated trust — the resulting PR is reviewed
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

### Phase 1 — Detection + dedup (no autonomous fix yet; report-only) — Done
- [x] Add a step to `validate-and-promote.yml`'s `full`/`guards-full-sweep`/
      `worktree-manager` jobs (or a new job gated on `if: failure()` after
      them) that, on any failure, extracts a compact failure signature: which
      job(s) failed, the specific failing test node id(s) (pytest's own
      `FAILED <path>::<test>` lines), and a short log excerpt.
      **Implemented:** a new `report-failure` job, `tools/ci_failure_watchdog.py`.
- [x] **Carry a verified commit SHA, never trust `workflow_run.head_sha`
      alone.** `validate-and-promote.yml` is itself `workflow_run`-triggered,
      and this repo already had to stop trusting that event's own
      `head_sha` and instead carry a SHA independently verified via
      `git merge-base` (see the dev-branch-release-pipeline journal, the
      exact regression fixed in this session). Phase 1's signature must
      carry that same already-verified SHA (or resolve/re-verify it from
      the run ID) through to Phase 2 — never re-derive it naively from a
      `workflow_run` payload, or a diagnosis can attach to, and a fix PR
      can target, the wrong commit.
      **Implemented:** `report-failure` passes `needs.gate.outputs.sha`
      directly as `--sha`; the script never re-derives it.
- [x] Dedup against existing open issues before filing anything new (search
      by the test node id, not just the plugin name) — reuse the exact
      pattern `health-diagnosis-filer`/`reality-drift-filer` already use
      (VEI + Gitea-style search, adapted to `gh issue list --search`), so a
      persistently-flaky test gets ONE tracked issue that accumulates
      occurrences, never a new issue per red run.
      **Implemented:** a hidden `Signature: <hash>` anchor line, searched
      via `gh issue list --search`, mirroring
      `module-health-watchdog.py`'s own `Module: <path>` pattern exactly.
- [x] File (or comment on) that issue, plain and factual: which run, which
      test(s), the log excerpt, a link back to the run. No fix attempt yet.
      **Implemented**, with an explicit "no fix attempted" line in the
      issue body.
- [x] Rate-limit: never file/comment more than once per N hours for the same
      signature (open question: N — start conservative, e.g. 6h).
      **Resolved: N = 6 hours** (`--rate-limit-hours`, default 6), anchored
      on the latest occurrence comment (or the issue's own filing time if
      none yet).

### Phase 1.5 — Live validation (real observation window, prerequisite for Phase 2)
- [ ] **Monitor real `validate-and-promote.yml` runs for a naturally-
      occurring red `full`/`worktree-manager`/`guards-full-sweep` failure**
      (this repo has enough concurrent PR/promotion activity that one is
      likely within hours, not days). When one occurs:
      - [x] Confirm `report-failure` actually ran (not skipped) and its
            conclusion. **Occurred 2026-09-26 08:34 UTC (run 36230190121):
            ran, but its own conclusion was `failure` — see the 2026-09-26
            Journal entry below. Real bug found and fixed (PR #3815). Not
            yet re-observed succeeding live end-to-end (only replay-
            verified against the same run's real data, see Journal) --
            keeping this parent item open until a subsequent natural (or
            probe) occurrence confirms a filed issue for real.**
      - [ ] Confirm one issue was filed per distinct failure signature
            (or, for a repeat outside the 6h rate-limit window
            specifically, an existing matching issue commented instead —
            **never** both a new issue for an already-tracked signature,
            and never a comment for a repeat still *within* the window,
            which `process_signature` deliberately does neither for) —
            not necessarily exactly one issue overall: a run with
            multiple distinct failing tests legitimately produces multiple
            `ci-failure-signature`-labeled issues, one per signature, per
            the watchdog's own design (`tools/ci_failure_watchdog.py`
            builds one `FailureSignature` per distinct failing test id).
            Each should have an accurate signature, correct run link/SHA,
            and a genuinely useful log excerpt — not truncated/garbled.
      - [ ] Confirm no duplicate issue was filed for the same signature on
            a second occurrence within the 6h window (only a real test of
            this, if the same failure recurs naturally or via the probe
            below).
- [ ] **If no natural red run occurs within a reasonable observation
      window (a few hours), inject one deliberately, carefully, and
      revert promptly:**
      - [ ] **Hard stop, non-negotiable:** the probe merge starts a clock.
            **Maximum 2 hours from the probe PR's merge to the revert
            PR's merge**, full stop — not "a reasonable observation
            window," an actual deadline. Set a real reminder/timer for it
            when the probe merges. If verification is still incomplete
            when the deadline arrives (checks stalled, the revert PR
            itself isn't merging, anything not going as planned), **open
            and merge the revert PR immediately anyway** — an incomplete
            observation is a fully acceptable outcome (record it as such
            in the Journal); leaving `dev` red indefinitely while chasing
            a clean observation is not. The revert PR is small and simple
            enough to prepare in parallel with the probe PR (same diff,
            inverted) so "the revert PR isn't merging" is never itself the
            blocker.
      - [ ] **Target `agent-worktrees` specifically, not just any
            "low-traffic plugin"** (a real review finding on this Plan
            itself): `ci.yml`'s Linux smoke tier only *collects* (imports,
            never executes) `agent-worktrees`' tests on push/PR --
            `HEAVY_PLUGIN: agent-worktrees` / `--collect-only` -- while
            every other plugin's tests actually *run* there. (`ci.yml`
            also has a separate `worktrees-windows-launch` job that DOES
            execute one specific, narrowly-filtered `agent-worktrees` test
            -- `-k status_daemon_console_root_contains_psmux_descendants`
            -- so the claim isn't that this plugin's suite is never
            executed on push/PR at all, only that neither existing path
            would catch a *new*, differently-named probe test.) A
            deliberately failing test added to any OTHER plugin's real
            (non-filtered) suite would fail `ci.yml` itself first; the
            `workflow_run` trigger itself still fires on that failed `CI`
            run (`types: [completed]` fires for any conclusion), but
            `gate`'s own `if:` requires `github.event.workflow_run.
            conclusion == 'success'` before doing anything -- so `gate`
            would produce `is_dev=false`/no-op and `report-failure` would
            never fire -- defeating the entire probe. `agent-
            worktrees` is the one plugin where a new, distinctly-named
            test evades both of `ci.yml`'s existing execution paths, so
            `ci.yml` stays green and `gate` proceeds normally, landing on
            the real target: the `full -
            agent-worktrees` job.
      - [ ] Add ONE new, obviously-synthetic, clearly-commented failing
            test to `agent-worktrees`' suite (e.g.
            `assert False, "Deliberate Phase 1 validation probe for
            promotion-failure-reactive-fix-agent -- safe to delete, see
            efforts/active/promotion-failure-reactive-fix-agent"`) — never
            touch existing test logic, never something with side effects,
            never `.github/workflows/**`.
      - [ ] **Add a changefile for `agent-worktrees`** (`python
            tools/changefile.py add --plugin agent-worktrees --type dev
            --comment "..."`) — this PR touches `plugins/agent-worktrees/`
            content, so the repo's changefile-presence guard requires one;
            the same is true for the follow-up revert PR below.
      - [ ] Land it via the normal PR flow to `dev` like any other change
            (small, honest PR description naming this as a deliberate,
            temporary probe for this effort — never disguised as a real
            bug).
      - [ ] Watch the resulting `full - agent-worktrees` failure and
            confirm `report-failure` behaves exactly as in the
            natural-occurrence
            checklist above.
      - [ ] **Before reverting, deliberately re-trigger the same
            validation run once more via a real `workflow_run` event** —
            merge a small, trivial no-op PR into `dev` (this repo blocks
            direct pushes to `dev`; every change lands through the normal
            PR flow, no exception here) while the probe test is still
            present (NOT `workflow_dispatch`: `report-failure` is
            restricted to `github.event_name == 'workflow_run'`, so a
            manual dispatch would re-run the failing `full` job but skip
            the watchdog entirely and validate nothing). Confirm the
            identical signature occurs a second time **within** the 6h
            window and that rate-limiting actually holds: **no** second
            issue and **no** new comment either (`process_signature`
            deliberately returns without commenting while
            `is_rate_limited(...)` is true) — a comment only appears once
            the window has genuinely expired, which isn't practical to
            wait out here. The probe as originally planned only ever
            produced one occurrence, which would let this checklist be
            marked complete without ever exercising the dedup path at
            all; a single synthetic run doesn't prove Phase 1 handles the
            exact repeat-failure case it exists for. If skipped for time,
            explicitly record dedup as **unvalidated** here and do not
            treat Phase 1 as fully proven / Phase 2's gate as unblocked on
            that basis.
      - [ ] **Revert immediately once confirmed** (a follow-up PR deleting
            the probe test) — this deliberately jams every pending
            promotion for the observation window, the exact cost this
            whole effort exists to shorten, so keep that window as short
            as the observation genuinely requires and never longer.
      - [ ] Note in the Journal whether the resulting issue/comment was
            left in place (as evidence) or closed once confirmed working.
- [ ] Record findings (false positives, dedup accuracy, issue quality)
      here before treating Phase 1 as proven and touching Phase 2's gate.

### Phase 2 — Wire the reactive fix attempt (the actual "attempt a fix")
- [ ] **Gate (blocks the rest of this phase):** resolve the Vision
      reconciliation noted in the header above — decide whether this
      mechanism choice belongs under an existing vision or needs its own,
      and record that decision before any `gh-aw` workflow file is merged.
- [ ] Once Phase 1's detection+dedup is proven reliable (no false positives,
      no duplicate-issue spam) over a real observation window, author a
      `gh-aw` agentic workflow (Markdown + YAML frontmatter, compiled via
      `gh aw compile` into a checked-in `.lock.yml`). **`gh aw compile`
      owns that `.lock.yml` as its own generated output — do not hand-edit
      it into `validate-and-promote.yml`'s existing job list; compilation
      will overwrite it.** Instead, give the agentic workflow explicit
      `workflow_call` inputs and invoke the compiled lock workflow **as a
      reusable-workflow job** from `validate-and-promote.yml`, passing
      Phase 1's detection job outputs (the already-verified SHA, the
      failure signature, the dedup decision) as explicit `with:` inputs —
      the same `needs.<job>.outputs` → next-job-input wiring
      `validate-and-promote.yml` already proves works between its own
      `gate`/`full`/`promote` jobs, just crossing a reusable-workflow
      boundary instead of a same-workflow job boundary. This preserves the
      same-run data flow without a second, independently-triggered
      workflow to keep in sync. Prompt it with exactly the compact
      signature Phase 1 already extracts: which job(s) failed, the failing
      test node id(s), and the log excerpt — not a vague "go fix CI."
  - [ ] **If a separate `workflow_run`-triggered workflow is used instead
        (not the preferred shape above): never filter it on
        `branches: [dev]`.** This repo already documents that
        `workflow_run` reports the **default branch** as `head_branch`
        regardless of which branch actually triggered the upstream run
        (see `validate-and-promote.yml:58-67` and the
        dev-branch-release-pipeline journal) — a `dev` branch filter here
        is the known **dead-trigger pattern**: it can silently prevent
        every run from firing at all. Gate on the carried, independently
        verified SHA/merge-base ancestry check instead of any branch-name
        filter.
  - [ ] **Bootstrap gotcha (this session already hit the identical bug
        once — see `ci.yml:71-74` and the dev-branch-release-pipeline
        journal):** a `workflow_run`-triggered workflow is read from the
        repo's **default branch (`main`)**, not the `dev` commit that adds
        it. Merging the compiled `.lock.yml` to `dev` alone leaves the
        trigger inert until `main` also has it. Ship it with the same
        main-bootstrap companion PR pattern this repo already uses for
        every other workflow-file change, and verify the compiled lock
        file is actually present on `main` before relying on a live
        failure to prove it works.
  - [ ] **Prompt-injection boundary (the log excerpt is untrusted input):**
        a failing test or its dependency can print imperative text
        specifically to steer the agent — `safe-outputs` limits *which
        operation* the agent can perform (open a PR against `dev`), not
        *what the patch contains*. Do not rely on the agent to interpret
        the log excerpt safely by instruction alone. Isolate the untrusted
        excerpt from the agent's own instructions in the prompt structure
        `gh-aw` provides for this, and add the machine-enforced check
        below as the actual backstop — never trust the excerpt-derived
        content to self-limit.
  - [ ] **Machine-enforced allowed/protected-path check before PR
        creation — not prompt text alone.** The "never touch
        `.github/workflows/**` or version fields" rule two bullets below
        is currently only policy language; `safe-outputs` constrains the
        write *operation* (PR-against-`dev`) but not *which files* land in
        it. Add an independent, code-level check (a dedicated
        `safe-outputs` step, or a required-status-check job on the
        resulting PR) that inspects the actual changed-file list and
        rejects/blocks the PR if it touches any protected path — a
        compromised or merely confused agent must not be able to propose
        those files no matter what the prompt says.
  - [ ] Configure its `safe-outputs` stage narrowly: the only permitted
        write is **open a pull request against `dev`** (no direct push, no
        issue/PR comments beyond what's needed, no repo-settings access).
        This is `gh-aw`'s own enforcement of this effort's Phase 3
        guardrails, not a substitute for them — keep Phase 3's explicit
        scope checks too.
  - [ ] Pin the `gh-aw` extension/action to a specific reviewed version (it
        is an actively-developed external tool; do not float on `latest`)
        and set up **Copilot-engine authentication using one of `gh-aw`'s
        two documented paths** — pick one deliberately, don't assume:
        (a) **org-billing path:** add `copilot-requests: write` to the
        workflow's own permissions and let it use the per-run
        `GITHUB_TOKEN` for inference (requires org Copilot subscription
        with centralized billing); or (b) **PAT path:** a fine-grained
        Personal Access Token with **Copilot Requests: Read** under
        Account permissions, stored as the `COPILOT_GITHUB_TOKEN` repo
        secret. (a) grants an Actions-token *workflow permission* named
        `copilot-requests: write`; (b) grants a *PAT account permission*
        named `Copilot Requests: Read` — these are two different
        permission systems with similarly-named entries; don't conflate
        them or assume one satisfies the other. Whichever path is chosen,
        follow this repo's normal `secrets`-skill vaulting discipline for
        any token involved, never hardcode it.
  - [ ] Give the agent job read-only repo access by default (its baseline
        posture) — only the `safe-outputs` PR-creation stage should hold
        any write credential at all. **This must be an explicit job-level
        `permissions:` block on the agent job itself, not implicit.**
        `validate-and-promote.yml` (the caller invoking this reusable
        workflow) already grants `contents: write`/`pull-requests: write`
        at *workflow* scope for its own `promote` job — a reusable-workflow
        `workflow_call` job inherits the caller's broad token unless the
        callee explicitly declares its own narrower `permissions:`. Set
        the agent job's permissions to read-only explicitly in the
        callee, and verify at review time that no broader permission
        leaks through from the caller side.
- [ ] Fallback, only if `gh-aw` proves unworkable in practice (e.g. auth
      friction, engine limitations): extend the filed/updated issue to
      **also assign it to `copilot`** via the `gh` CLI (`gh issue edit <#>
      --add-assignee copilot`) or the equivalent GraphQL mutation, and write
      the issue body as a genuinely well-scoped Copilot cloud agent prompt
      (same narrow-scope instructions as below, adapted to issue-body form).
  - [ ] **The fallback path has no `safe-outputs` stage — the same
        machine-enforced protected-path check is mandatory here too, not
        optional.** Issue-assignment gives the Copilot cloud agent no
        equivalent write-scoping: nothing stops it from opening a PR that
        touches `.github/workflows/**` or a version manifest beyond the
        issue body's own prompt text. Add the identical changed-file
        validation (a required-status-check job, applied uniformly to
        *any* PR against `dev`, not just `gh-aw`-authored ones) so the
        fallback path is never weaker than the primary one.
- [ ] Whichever mechanism is used, the resulting PR must never touch
      `.github/workflows/**` (that's `main-gate`'s workflow-only bootstrap
      lane, a different mechanism entirely, and an autonomous agent must
      never have a path that even looks like it could qualify for that
      exception) and must never modify `plugin.json`/`pyproject.toml`/
      `marketplace.json` version fields by hand (add a changefile per
      `CONTRIBUTING.md`, exactly like any other contributor). **This is
      enforced by the required-status-check job above, not by prompt text
      alone, regardless of which Phase 2 mechanism produced the PR.**
- [ ] Confirm (read the actual agent-authored PR when the first one lands)
      that it lands as an ordinary PR against `dev`, subject to the same
      non-blocking Copilot review and the same required checks as every
      other PR — no special-case merge path introduced anywhere in this
      effort.

### Phase 3 — Guardrails and walk-back criteria (do not skip)
- [ ] **Never auto-merge the resulting PR.** A human or the driving agent
      reviews it like any other contributor's PR before merge — this
      effort automates the *diagnosis + fix attempt*, never the *acceptance*
      of the fix. This is the one guardrail everything else in this effort
      is downstream of; do not relax it as part of any later phase without
      an explicit, separately-reasoned decision. (If using `gh-aw`: its
      `safe-outputs` PR-creation stage already enforces "buffered write,
      never a direct merge" structurally — this guardrail restates the same
      constraint at the review-policy level, since `safe-outputs` bounds
      *what* can be written, not whether it gets merged unreviewed.)
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

### 2026-09-25 — Mechanism correction: GitHub Agentic Workflows (`gh-aw`)
- Operator asked directly whether "GitHub Agentic Workflow" was available —
  a distinct feature from the "Copilot automations" this effort's Context
  originally evaluated and ruled out. Researched `gh-aw` (`github/gh-aw`)
  directly from its own docs (introduction/architecture, setup/quick-start),
  not from memory.
- Confirmed `gh-aw` compiles to ordinary GitHub Actions (`.lock.yml`), so it
  carries **no public/private-repo restriction** — unlike native
  Automations. Confirmed "CI failure investigation" is one of its own
  documented canonical use cases, and its `safe-outputs` mechanism gives
  read-only-by-default agent sandboxing with writes gated through a
  separate, scoped validation stage — a stronger built-in version of this
  effort's own Phase 3 guardrails than issue-assignment alone would give.
- Promoted `gh-aw` to the **primary** Phase 2 mechanism; kept
  issue-assignment-to-`copilot` as an explicit fallback if `gh-aw` proves
  unworkable in practice (auth friction, engine limitations). Updated
  Participants, Context, and Phase 2/3 accordingly. Still deliberately not
  implemented — Phase 1 (detection+dedup) remains the next concrete step
  regardless of which Phase 2 mechanism is eventually used.
- **Copilot PR review (#3678) caught two real gaps, both addressed:**
  (1) the identical `workflow_run` default-branch bootstrap bug this
  session already fixed once in `validate-and-promote.yml` would silently
  recur here — added as an explicit Phase 2 checklist item citing
  `ci.yml:71-74`; (2) promoting `gh-aw` to *primary* is a material
  architecture decision made ahead of any vision reconciliation — checked
  `harness-guidance` (does not fit; it's about ambient guidance delivery,
  not fix-mechanism selection), and added an explicit pre-Phase-2 gate so
  the choice stays provisional/research-backed rather than quietly
  becoming settled design without that reconciliation ever happening.
- **Second Copilot review pass caught four more, all addressed:** (1) the
  same `workflow_run.head_sha`-trust bug this session already fixed once
  in `promote.yml`/`validate-and-promote.yml` would recur in Phase 1's own
  signature-carrying if not made explicit — added as a Phase 1 checklist
  item requiring the already-verified SHA to be carried through, never
  re-derived naively; (2) the raw log excerpt is untrusted input and could
  prompt-inject the fix-attempting agent — added an explicit
  prompt-injection-boundary checklist item, not just a policy sentence;
  (3) the "never touch workflows/version fields" rule was only prompt
  text, with no machine-enforced backstop if the agent ignored it — added
  an explicit machine-enforced allowed/protected-path check as its own
  checklist item, independent of `safe-outputs`'s write-operation scoping;
  (4) this effort was missing from `efforts/README.md`'s canonical Active
  index — added.
- **Third Copilot review pass caught two more, both addressed:** (1) a
  `workflow_run`-triggered example still risked the known dead-trigger
  pattern — `workflow_run` reports the **default branch** as
  `head_branch` regardless of which branch actually ran, so a
  `branches: [dev]` filter can silently prevent every run from firing
  (this repo already documents the identical gotcha in
  `validate-and-promote.yml`); reworked the checklist to prefer same-job
  invocation and explicitly prohibit that branch filter as a fallback;
  (2) the planned Copilot-engine auth conflated two different, similarly-
  named permission systems (`copilot-requests: write`, an Actions-token
  *workflow permission* for the org-billing path, vs. `Copilot Requests:
  Read`, a PAT *account permission* for the token path) — re-verified
  both against `gh-aw`'s own setup docs (both are genuinely documented,
  for two different auth paths) and rewrote the checklist to name both
  paths explicitly rather than picking one ambiguously.
- **Fourth Copilot review pass caught one more (severity now down to
  medium, converging):** the "prefer direct invocation from the same
  job" language was technically imprecise — `workflow_call` is
  reusable-workflow plumbing at the *caller's job* level, not a step-level
  mechanism, so it can't inherit in-process state either; reworded to the
  concrete, already-proven pattern this repo uses in
  `validate-and-promote.yml` itself: explicit `needs.<job>.outputs`
  between a same-workflow detection job and fix-attempt job. The one
  remaining open finding (persistent low-severity "Documentation impact"
  thread) was replied to in-thread — the PR description has carried that
  section since the first revision; treating this as addressed rather
  than iterating further on a stale/non-re-scanned finding.
- **Fifth Copilot review pass caught two more (doc-impact thread
  confirmed resolved by the reply above):** (1) `gh aw compile` owns the
  generated `.lock.yml` as its own output — hand-adding its agent job into
  `validate-and-promote.yml`'s job list is not implementable, compilation
  would overwrite it; corrected to the actually-implementable shape: give
  the compiled workflow `workflow_call` inputs and invoke it as a
  **reusable-workflow job** from `validate-and-promote.yml`, passing
  Phase 1's job outputs as explicit inputs; (2) the mandatory
  machine-enforced protected-path check was only specified for the
  `gh-aw` path — the issue-assignment fallback has no `safe-outputs`
  equivalent at all, so it would be strictly weaker; made the same check
  mandatory for the fallback path too, applied uniformly to any PR
  against `dev` regardless of which mechanism produced it. Five rounds in,
  severity is converging toward zero real findings; this is expected
  scrutiny depth for a not-yet-implemented design doc going through the
  same non-blocking review every code PR gets in this repo.
- **Sixth Copilot review pass caught one more, addressed — a reusable-
  workflow permission-inheritance gap:** a `workflow_call` job inherits
  its caller's broad workflow-scope permissions (`validate-and-promote.yml`
  itself grants `contents: write`/`pull-requests: write`) unless the
  callee explicitly declares its own narrower `permissions:` block; made
  the agent job's read-only posture an explicit job-level `permissions:`
  requirement rather than an implicit assumption. Stopping the review
  loop here: checks have stayed green throughout, findings have converged
  from high-severity/architecture-level to a single narrow permissions
  detail, and this repo's Copilot review is explicitly non-blocking —
  merging now; any further hardening surfaces during actual Phase 1/2
  implementation instead.

### 2026-09-26 — Way forward: execute Phase 1, defer the vision question
- Operator: "let's work the effort and determine a way forward." Decided
  **not** to force the vision-reconciliation gate now: the original Plan
  always deferred that decision until *after* Phase 1 proved out (see the
  Kickoff entry above), and the "gate" language a later review pass added
  was about blocking Phase 2 specifically, not about blocking all forward
  motion on this effort. Phase 1 was already explicitly noted as
  unaffected. Forcing a premature vision decision with zero real operating
  signal would be guessing; executing Phase 1 for real is what actually
  generates the signal needed to answer it honestly later.
- **Implemented and landed Phase 1 in full:** `tools/ci_failure_watchdog.py`
  (signature extraction, dedup via a hidden `Signature: <hash>` anchor
  mirroring `module-health-watchdog.py`'s own pattern, rate-limited
  occurrence comments) plus a new `report-failure` job in
  `validate-and-promote.yml`, gated on a genuine `dev`-commit failure and
  carrying `needs.gate.outputs.sha` (never re-derived). 26 new tests (grew
  from an initial 21 as review passes surfaced more edge cases), all
  passing. Resolved the effort's own open question: rate-limit window =
  6 hours.
- Status moved Draft -> Active. Phase 2 remains explicitly gated on the
  vision-reconciliation decision; that decision is deferred until Phase 1
  has run for real against a genuine red build and its behavior (false
  positives, dedup accuracy, issue quality) can be judged on evidence.
- **Copilot PR review (#3746) caught four real bugs before this ever ran
  for real, all fixed:** (1) the new `report-failure` job's `if:` had no
  status-check function, so GitHub's implicit `success()` requirement
  would have silently skipped it on the exact red run it exists to
  report — added the same `always()` this workflow's own `promote` job
  already needed for the identical reason; (2) `signature_key()` hashed
  only the test id for a parseable failure, so the identical test node id
  failing in two different vendored-lib plugin jobs (`full -
  agent-bridge` vs `full - agent-mcp`, a real shape in this repo) would
  wrongly collapse into one issue — job name is now always part of the
  hash basis; (3) `gh issue list --json comments` returns a comment
  *count*, not comment objects with timestamps (that shape only exists on
  `gh issue view` for a single issue) — the rate-limit anchor was
  simplified to just `updatedAt`, which GitHub already bumps on any new
  comment, rather than adding a second API round-trip to recover what it
  already tracks; (4) the new test file wasn't wired into `ci.yml`'s
  per-file test enumeration (no wildcard discovery in this repo) —
  added alongside `module-health-watchdog.py`'s own entry. Also deleted a
  flaky smoke test that shelled out to the real `gh` CLI (network/auth-
  dependent) in favor of the equivalent, already-present monkeypatched
  coverage, and added an explicit cross-job dedup regression test.
- **One reviewer finding was checked against live evidence and found
  incorrect, not applied:** the claim that `gh api .../jobs/{id}/logs`
  returns a ZIP archive. Fetched a real job's log with that exact command
  live against this repo — it returns plain text directly (the ZIP format
  is real, but only for the *run*-level logs endpoint,
  `.../runs/{run_id}/logs`, which downloads every job's logs bundled
  together; the *job*-level endpoint this script uses is documented and
  observed to return plain text on its own). Replied on the review thread
  with the evidence rather than silently "fixing" correct behavior.
- **Still deliberately not landed on `main`:** `validate-and-promote.yml`
  is `workflow_run`-triggered, so per this repo's now-familiar bootstrap
  gotcha, `report-failure` will not actually execute until this same diff
  also reaches `main` via a workflows-only companion PR — queued as the
  next action once this PR merges to `dev`.
- **Second review pass caught one real parsing bug and one real dedup
  bug, both fixed:** (1) parametrized node ids can contain spaces (e.g.
  `test_case[a b]`), which the original `\S+` pattern truncated at the
  first one; matched the full line and split on the last `` - `` (the
  real node-id/reason boundary) instead; (2) that same broad match also
  swallowed `run-plugin-tests.py`'s own non-test `FAILED plugins: <name>`
  wrapper line, which would have produced a misleading extra
  signature/issue — now requires a genuine `::` node-id shape before
  accepting a match, falling through to the whole-job signature
  otherwise. Regression tests added for both.
- **Third review pass caught a real dedup-breaking bug:** the whole-job
  fallback signature hashed the raw log excerpt, which the Actions log
  timestamps on every line — so the *identical* non-pytest failure
  (a `guards-full-sweep` script crash) got a different hash, and a
  different issue, on every single occurrence, silently defeating the
  entire point of that fallback path. Strip the per-line ISO-8601
  timestamp prefix before hashing (keeping the real, timestamped excerpt
  for the human-facing issue/comment body); regression test confirms two
  identical failures with different timestamps now produce the same key.
- **Fourth review pass caught the most severe bug yet, now fixed:** a
  real Actions job log timestamps **every** line, including pytest's own
  `FAILED <nodeid>` summary line -- so `^FAILED` (anchored at true line
  start) would **never match a single real log**, making Phase 1's
  detection a complete no-op in production despite 22/22 tests passing.
  The tests passed because the hand-written `SAMPLE_PYTEST_LOG` fixture
  didn't actually include a timestamp prefix on that line -- an
  unrealistic fixture hid a bug real logs would have hit every time.
  Fixed by matching against the already-existing `_strip_timestamps()`
  helper's output (reused, not duplicated) before applying the FAILED-line
  regex; rewrote the fixture to timestamp every line realistically and
  added a direct regression test. Worth remembering: a green test suite
  only proves what the fixtures actually exercise.
- **Fifth review pass caught three real, smaller issues, all fixed:**
  (1) `gate`'s `is_dev` output is `true` for every `workflow_dispatch` run
  regardless of ref (pre-existing `promote` behavior, not something this
  effort should alter) — a manual dispatch from `main` or any other ref
  could have filed an issue wrongly claiming a `dev` validation failure;
  added a `report-failure`-local `github.ref == 'refs/heads/dev'` check
  (skipped for `workflow_run` events, which `gate` already verifies) that
  closes this without touching `gate`'s own shared logic; (2) this
  effort's own status change to Active hadn't propagated to
  `efforts/README.md`'s canonical Active index (still said Draft) —
  fixed; (3) the test count cited here (21) was already stale by the time
  it was written (26 by then) — corrected.
- **Sixth review pass caught two more real bugs, both fixed:** (1) the
  node-id/reason split used `rsplit(" - ", 1)`, which cuts at the LAST
  `` - `` — a failure reason that itself contains `` - `` (e.g.
  `AssertionError: left - right`) would wrongly swallow part of the
  reason into the node id; replaced with a single regex that captures the
  actual node-id shape directly (`path::name` plus an optional
  `[params]` suffix) instead of capturing the whole line and splitting
  after the fact — structurally safe against this, not just
  better-tuned; (2) a job that exceeds its own `timeout-minutes` gets
  conclusion `timed_out`, not `failure` (the exact class of failure this
  effort was kicked off by) — the job-filter only checked for `failure`,
  so a timed-out validation job would make the run red but the watchdog
  would report "nothing to report." Now checks a `REPORTABLE_CONCLUSIONS`
  set (`failure`, `timed_out`). 28 tests now, all passing.
- **Seventh review pass caught the most serious finding of the whole
  Phase 1 build, plus two smaller real ones, all fixed:** (1) **security:**
  `report-failure` checked out `needs.gate.outputs.sha` -- the just-failed
  `dev` commit itself, i.e. the very thing being diagnosed -- and executed
  `tools/ci_failure_watchdog.py` *from that checkout*, while the job held
  `issues: write` and a live `GH_TOKEN`. A commit on `dev` could therefore
  smuggle its own modified copy of this exact script to exfiltrate the
  token or file arbitrary issues, defeating the entire `workflow_run`
  trust boundary this pipeline depends on -- the same class of mistake
  `trusted-ci.yml` was hardened against earlier this session. Fixed by
  removing the explicit `ref:` override on this job's checkout entirely:
  with no `ref:`, `actions/checkout` resolves the workflow_run's own
  natural ref, which -- exactly like the workflow file itself -- is the
  trusted default branch. The diagnosed SHA still reaches the script, but
  only ever as a `--sha` **data** argument, never as code that gets
  checked out and run; (2) the tracking label's description was 123
  characters against GitHub's 100-character cap, which would have failed
  the label-creation step outright before the watchdog ever ran -- much
  shorter description now; (3) both `gh` failure paths (job-list fetch,
  per-job log fetch) unconditionally returned/continued with exit 0 even
  in `--file-issue` mode, meaning a completely broken watchdog could
  report success while detecting and filing nothing — now returns 1 in
  that mode specifically (dry-run stays 0 regardless, matching the
  documented contract). 31 tests now, all passing.
- **Eighth review pass caught the deepest structural gap yet, plus two
  smaller real ones, all fixed:** (1) **the trusted-checkout fix from the
  previous round created a genuine bootstrap chicken-and-egg problem**:
  `tools/ci_failure_watchdog.py` is a brand-new file that only exists on
  `dev` right now, and — unlike a workflow-file change — it can't ride
  `main-gate`'s workflow-only bootstrap lane (it isn't a workflow file);
  it only reaches the default branch the normal way, via the next
  ordinary green `dev`->`main` promotion. Until that happens, the trusted
  (main) checkout genuinely won't have the file, and the step would
  hard-fail on every red run in that window. Fixed by degrading
  gracefully: the step now checks the file exists before invoking it,
  logging a notice and exiting 0 instead of failing the job — a
  permanent safety net against any future main/dev script-presence
  mismatch, not just this one-time gap, not merely a wait-and-hope; (2)
  the trusted-checkout fix itself had a trigger-type gap: omitting `ref:`
  resolves to the default branch for `workflow_run` events, but for
  `workflow_dispatch` (which this job's own `if:` explicitly permits from
  `refs/heads/dev`) it resolves to whichever ref was manually dispatched
  — reopening the exact hole for that one path. Now pins an EXPLICIT
  `ref: ${{ github.event.repository.default_branch }}`, correct
  regardless of trigger type; (3) `process_signature`'s own
  `LookupFailed` handler (a per-signature dedup-lookup failure, distinct
  from the two `main()`-level `gh` failure paths fixed last round) still
  returned 0 unconditionally, even though by the time that branch is
  reached `file_issue` is always `True` (the dry-run case already
  returned earlier) — fixed to return 1, matching the same "never mask a
  broken watchdog behind a green step" contract. 31 tests, all passing.
- **Ninth review pass found the deepest issue in this whole sequence:**
  the previous round's checkout-ref fix only protected which *script*
  runs, not the *job definition itself* — `workflow_dispatch` loads the
  ENTIRE workflow YAML from the dispatched ref (unlike `workflow_run`,
  which always resolves the workflow file from the default branch), so
  permitting a manual dispatch against `dev` at all meant the whole job
  -- steps, permissions, everything -- could be attacker-controlled by a
  `dev` commit, no matter how carefully the checkout step itself was
  pinned. There is no way to harden a job against an untrusted copy of
  its own definition. Fixed the only way that actually closes it: made
  `report-failure` `workflow_run`-only, full stop -- it never needed
  manual dispatch (it exists to react automatically to real validation
  runs), so the fix is removing the path entirely rather than trying to
  defend it. No Python change this round, workflow-only.
- **PR #3746 merged to `dev`, `report-failure` bootstrapped onto `main`
  via workflows-only PR #3776 (main source gate passed cleanly).** Phase
  1 is live end-to-end. Confirmed via the pipeline's own subsequent runs
  that the new job is picked up correctly.

### 2026-09-26 — Next: live validation (Phase 1.5)
- Operator: "let's monitor, and potentially cause a careful build/test
  break somehow. Inevitably one will go in on its own." Added a new
  Phase 1.5 to the Plan above: monitor real `validate-and-promote.yml`
  runs for a naturally-occurring red build first (this repo has enough
  concurrent activity that one is likely soon); only if none occurs
  within a reasonable window, deliberately inject one small, obviously-
  synthetic, clearly-labeled failing test via the normal PR flow, watch
  `report-failure` react, verify issue quality, then revert **promptly**
  (a follow-up PR) — the deliberate probe still jams every pending
  promotion for its whole window, the exact cost this effort exists to
  shorten, so that window must stay as short as the observation
  genuinely requires.
- Not yet executed — this is the durable plan for whichever session
  picks up the monitoring next (a recurring scheduled check, or a fresh
  session resuming this file).
- **Copilot review caught a real design flaw in the probe plan itself:**
  targeting "any low-traffic plugin" would have made the probe self-
  defeating for most plugins — `ci.yml`'s smoke tier actually *executes*
  every plugin's tests on push/PR except `agent-worktrees` (collect-only
  there, per `HEAVY_PLUGIN`), and `gate`'s own `if:` requires the
  upstream `CI` run to have concluded `success` before it does anything
  (the `workflow_run` trigger itself fires on any conclusion — it's
  `gate` that no-ops on a failure, not the trigger declining to fire). A
  failing test in any other plugin would fail `ci.yml` first, `gate`
  would produce `is_dev=false`, and `report-failure` would never fire.
  Corrected the plan to name `agent-worktrees` specifically as the
  only plugin where the probe actually reaches the intended `full -
  agent-worktrees` job.
- **Second review pass caught three more real refinements, all fixed:**
  (1) the "confirm exactly one issue was filed" checklist item was too
  strict — the watchdog legitimately files one issue per distinct
  failure signature, so a multi-failure run can correctly produce
  several; corrected to "one issue per distinct signature"; (2) the
  Linux-collect-only rationale was factually incomplete — `ci.yml` also
  has a `worktrees-windows-launch` job that executes one specific,
  narrowly `-k`-filtered `agent-worktrees` test, so the accurate claim is
  that a *new, differently-named* probe test evades both existing
  execution paths, not that the suite is never executed at all;
  corrected the wording; (3) the probe PR (and its follow-up revert)
  touch `plugins/agent-worktrees/` content, so both need a pending
  `agent-worktrees` changefile per this repo's changefile-presence guard
  — added as an explicit checklist item so "the normal PR flow" doesn't
  quietly omit it.
- **Third review pass caught one wording nit, fixed in both places it
  appeared:** `validate-and-promote.yml`'s `workflow_run` trigger itself
  fires on ANY upstream `CI` conclusion (`types: [completed]`); it's
  `gate`'s own `if:` that requires `success` before doing anything. The
  earlier wording conflated "the trigger doesn't fire" with "gate no-ops"
  — corrected both occurrences.
- **Fourth review pass caught the deepest gap in the probe design
  itself:** as planned, the single synthetic probe produced exactly one
  occurrence, then got reverted — meaning the checklist could be marked
  complete without ever exercising the 6h dedup/rate-limit path at all,
  the exact behavior that stops a persistently-flaky failure from
  spamming a new issue per run. Added an explicit step to deliberately
  re-trigger the same probe-induced failure once more before reverting
  (confirming a comment lands on the existing issue, not a second one),
  with an honest fallback: if skipped for time, record dedup as
  unvalidated rather than silently treating Phase 1 as fully proven.
- **Fifth review pass caught two real self-contradictions in that same
  new step:** (1) a second occurrence *within* the stated 6h window
  should verify **no** new issue and **no** new comment either —
  `process_signature` deliberately holds off commenting while
  `is_rate_limited(...)` is true, so "confirm a comment lands" was
  simply wrong for a re-trigger that happens minutes later, not hours;
  (2) `workflow_dispatch` was suggested as one re-trigger option, but
  `report-failure` is now `workflow_run`-only (this session's own earlier
  security fix) — a manual dispatch would re-run the failing job but
  skip the watchdog entirely, validating nothing. Corrected to: merge a
  trivial no-op PR into `dev` (a real `workflow_run` event) and expect
  silence (rate-limited), not a comment.
- **Sixth review pass caught two more real inconsistencies, both fixed:**
  (1) the *first* checklist item (natural occurrence) still said a repeat
  gets "commented" unconditionally — made explicit that a comment only
  happens for a repeat *outside* the 6h window, matching the more
  precise wording already added to the dedup step; (2) the re-trigger
  step said "push a trivial no-op commit to `dev`," but this repo blocks
  direct pushes to `dev` entirely — corrected to a small no-op PR merged
  the normal way.
- **Seventh review pass caught a real safety gap: no hard stop.**
  "A reasonable observation window (a few hours)" and "revert promptly"
  are both soft language — if verification stalled (checks jam, the
  revert PR itself doesn't merge cleanly), nothing in the plan actually
  bounded how long `dev` could stay red. Added a genuine, non-negotiable
  2-hour maximum from probe-merge to revert-merge, with an explicit
  instruction to merge the revert anyway if the deadline arrives mid-
  observation (an incomplete observation is acceptable; an unbounded red
  `dev` is not), and a note to prepare the revert PR in parallel so it's
  never itself the source of delay.

### 2026-09-26 — Phase 1.5's first natural occurrence: a real bug, found and fixed live
- A scheduled monitoring tick found a genuine naturally-occurring red
  `validate-and-promote.yml` run: **36230190121** (2026-09-26 08:34 UTC),
  two distinct real failing tests in the SAME run —
  `agent-worktrees::TestRetireRecord::test_concurrent_both_reaped_
  hard_delete_does_not_deadlock` (`full - agent-worktrees`) and
  `production_picker::test_streaming_paints_rows_and_summary`
  (`worktree-manager (out-of-plugin, full)`), both apparent
  concurrency/timing flakes. `dev` self-recovered on its own within the
  same hour (later commits pushed forward and passed cleanly) — no
  intervention was needed on the underlying flakes themselves, and this
  observation never left `dev` red longer than it already was.
- **`report-failure` ran (not skipped), but its own conclusion was
  `failure` — and it filed NOTHING for either signature.** Root cause,
  confirmed against the real job logs: `gh api repos/{repo}/actions/
  jobs/{id}/logs` (hosted runner ships GitHub CLI 2.101.0) refuses to
  print a raw non-JSON response body containing ANSI escape sequences
  unless `--allow-escape-sequences` is passed — a CLI terminal-safety
  guard (`pkg/cmd/api/api.go`, `iostreams.CopyGuardedContent`/
  `ErrEscapeSequence` upstream in `cli/cli`), not an API restriction.
  Real job logs are timestamp-prefixed *and* frequently carry ANSI
  color codes (confirmed real ESC bytes present in the raw captured
  log), so this tripped on both failing jobs' log fetches, `_fetch_job_
  log` raised `LookupFailed` for each, and `main()`'s own contract
  (`continue` past an unfetchable job, no signature built) meant zero
  issues were filed for a real double-test-failure run — exactly the
  jam-goes-unnoticed failure mode this whole effort exists to shorten.
  This is precisely why Phase 1.5 (a real observation window before
  trusting Phase 1, let alone building Phase 2 on top of it) mattered:
  a purely offline review of the code would not have caught a `gh`-CLI-
  version-dependent runtime behavior difference between the hosted
  runner (2.101.0) and every dev machine's own older `gh`.
- **Fixed via PR #3815** (two review rounds, both catching real,
  additional gaps beyond the direct fix):
  - `_fetch_job_log` now passes `--allow-escape-sequences`.
  - **First review round found two more real bugs the direct fix
    introduced/left standing:** (1) **high severity** — the flag
    itself doesn't exist before `gh` ~2.94; this repo's own clean-room
    helper (`tools/clean-room/lib/clean-room-lib.sh`) still provisions
    `gh` 2.62.0 by default, which would reject the flag as "unknown
    flag" and break the call in a completely different way on that
    environment. Fixed with a graceful fallback: retry once without the
    flag on an "unknown flag" stderr match (an old `gh` has no escape-
    sequence guard to begin with, so the plain call still succeeds; a
    genuinely broken `gh` still fails the same way on the retry, so
    this can never mask a real error). (2) **medium severity** —
    allowing escape sequences through without stripping them means a
    colored `FAILED tests/x.py::test_y` line no longer matches
    `_FAILED_TEST_RE` at all (the ANSI codes break the `^FAILED `
    anchor/shape), silently falling back to a whole-job signature and
    losing the exact per-test dedup behavior this fix was meant to
    restore. Fixed by stripping ANSI CSI sequences (`_strip_ansi`)
    unconditionally in `_fetch_job_log`, before any caller ever sees
    the text — verified this is a real risk (not hypothetical) by
    checking the actual captured bytes from run 36230190121: genuine
    ESC bytes were present in the raw log (in a `Run <command>` echo
    line in this specific instance, not the `FAILED` line itself this
    time — but PR ci steps do sometimes force-color pytest output onto
    the FAILED line too, so unconditional stripping is the only safe
    fix, not "only strip if observed on that line"). **Second review
    round: Findings: None** — both fixed correctly, merged.
  - Also added the CONTRIBUTING.md-required "Documentation impact"
    statement to the PR body (a real process gap in the first
    submission draft) — this being an internal report-only script's
    own `gh`-CLI interaction, no user-facing doc changes were needed,
    but the statement itself is mandatory regardless.
  - 4 new regression tests (35 total): the flag is passed on the happy
    path; the version-fallback retry fires and succeeds on "unknown
    flag"; a colored `FAILED` line still parses correctly into a proper
    per-test id after stripping; a genuine non-flag-related `gh`
    failure still raises `LookupFailed` (never silently swallowed).
  - **End-to-end confidence, without waiting for another live
    occurrence:** re-ran the *fixed* script locally (dry-run) directly
    against the real failed run's data — `python tools/ci_failure_
    watchdog.py --run-id 36230190121 --sha cc88f...` — and it correctly
    produced both real per-test signatures
    (`test_streaming_paints_rows_and_summary`,
    `test_concurrent_both_reaped_hard_delete_does_not_deadlock`). (This
    exercised the version-fallback path locally, since the local `gh`
    predates the flag too — not the exact hosted-runner
    `--allow-escape-sequences`-succeeds path — so it is strong
    supporting evidence, not full live proof of that specific path.)
- **Left intentionally open in the Plan above:** the "one issue filed
  per distinct signature," "accurate signature/log excerpt," and "no
  duplicate within the 6h window" sub-items are still unconfirmed live
  — this incident proved and fixed a real detection-side bug, but
  didn't get far enough to observe a real filed issue or a real dedup
  cycle. Continuing to monitor (schedule still armed) for either
  another natural occurrence or, failing that, the deliberate probe
  already specified in the Plan, to close out the remaining sub-items
  with genuine live evidence rather than declaring Phase 1.5 done on
  partial signal.
