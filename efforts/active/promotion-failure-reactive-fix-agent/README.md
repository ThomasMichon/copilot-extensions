# Promotion-Failure Reactive Fix Agent

- **Slug:** `promotion-failure-reactive-fix-agent`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-09-25
- **Status:** Draft
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

### Phase 1 — Detection + dedup (no autonomous fix yet; report-only)
- [ ] Add a step to `validate-and-promote.yml`'s `full`/`guards-full-sweep`/
      `worktree-manager` jobs (or a new job gated on `if: failure()` after
      them) that, on any failure, extracts a compact failure signature: which
      job(s) failed, the specific failing test node id(s) (pytest's own
      `FAILED <path>::<test>` lines), and a short log excerpt.
- [ ] **Carry a verified commit SHA, never trust `workflow_run.head_sha`
      alone.** `validate-and-promote.yml` is itself `workflow_run`-triggered,
      and this repo already had to stop trusting that event's own
      `head_sha` and instead carry a SHA independently verified via
      `git merge-base` (see the dev-branch-release-pipeline journal, the
      exact regression fixed in this session). Phase 1's signature must
      carry that same already-verified SHA (or resolve/re-verify it from
      the run ID) through to Phase 2 — never re-derive it naively from a
      `workflow_run` payload, or a diagnosis can attach to, and a fix PR
      can target, the wrong commit.
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

### Phase 2 — Wire the reactive fix attempt (the actual "attempt a fix")
- [ ] **Gate (blocks the rest of this phase):** resolve the Vision
      reconciliation noted in the header above — decide whether this
      mechanism choice belongs under an existing vision or needs its own,
      and record that decision before any `gh-aw` workflow file is merged.
- [ ] Once Phase 1's detection+dedup is proven reliable (no false positives,
      no duplicate-issue spam) over a real observation window, author a
      `gh-aw` agentic workflow (Markdown + YAML frontmatter, compiled via
      `gh aw compile` into a checked-in `.lock.yml`) triggered off the
      dedup'd failure signal from Phase 1 (e.g. `workflow_run` on
      `validate-and-promote.yml` conclusion `failure`, or invoked directly
      from the same job). Prompt it with exactly the compact signature
      Phase 1 already extracts: which job(s) failed, the failing test
      node id(s), and the log excerpt — not a vague "go fix CI."
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
        and set up the Copilot-engine auth it needs (`COPILOT_GITHUB_TOKEN`
        fine-grained PAT scoped to Copilot Requests: Read, per its docs) —
        follow this repo's normal `secrets`-skill vaulting discipline for
        that token, never hardcode it.
  - [ ] Give the agent job read-only repo access by default (its baseline
        posture) — only the `safe-outputs` PR-creation stage should hold
        any write credential at all.
- [ ] Fallback, only if `gh-aw` proves unworkable in practice (e.g. auth
      friction, engine limitations): extend the filed/updated issue to
      **also assign it to `copilot`** via the `gh` CLI (`gh issue edit <#>
      --add-assignee copilot`) or the equivalent GraphQL mutation, and write
      the issue body as a genuinely well-scoped Copilot cloud agent prompt
      (same narrow-scope instructions as below, adapted to issue-body form).
- [ ] Whichever mechanism is used, the resulting PR must never touch
      `.github/workflows/**` (that's `main-gate`'s workflow-only bootstrap
      lane, a different mechanism entirely, and an autonomous agent must
      never have a path that even looks like it could qualify for that
      exception) and must never modify `plugin.json`/`pyproject.toml`/
      `marketplace.json` version fields by hand (add a changefile per
      `CONTRIBUTING.md`, exactly like any other contributor).
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
