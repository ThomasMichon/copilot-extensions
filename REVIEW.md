# Review guidance — copilot-extensions

This file is read by GitHub Copilot code review specifically (this repo's
`.github/workflows/copilot-review-gate.yml` requests it, non-blocking, on
every PR targeting `dev` from an already-invited collaborator — see
CONTRIBUTING.md's "Contribution flow" for why the ruleset-native
`copilot_code_review` auto-review rule was removed instead of used;
`main`'s ruleset never requests Copilot review at all, since only the
promotion pipeline's own automated snapshot PR ever targets `main`, and
re-reviewing regenerated, already-validated content there is redundant) —
see [Customizing Copilot's reviews with custom
instructions](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/request-a-code-review/use-code-review#customizing-copilots-reviews-with-custom-instructions).
Unlike [`.github/copilot-instructions.md`](.github/copilot-instructions.md)
(which also shapes Chat and the coding agent), this file's guidance is
review-only.

The full guides remain [`AGENTS.md`](AGENTS.md) (development guide),
[`CONTRIBUTING.md`](CONTRIBUTING.md) (contribution boundary, versioning &
release), and [`docs/patterns/README.md`](docs/patterns/README.md) (the
numbered design principles and binding design invariants) -- those are the
standing review authority; this file is a lens for applying them
specifically when reviewing a pull request, not a replacement for them.

## Directives for the automatic code-review agent

- **Flag any PR whose base is `main` directly.** `main` is regenerated
  wholesale by this repo's own CI promotion pipeline
  (`.github/workflows/validate-and-promote.yml`, dev-branch-release-pipeline
  effort, ThomasMichon/copilot-extensions#3336) via an automated snapshot PR: base
  `main`, head branch matching `release/promote-<run id>`, authored via the
  `APERTURE_RELEASE_TOKEN` fine-grained PAT (a personal token minted under
  the maintainer's own account -- this repo has no separate bot identity --
  so the PR's author is `ThomasMichon`, not a bot login). The **only other**
  legitimate shape is a workflow-file-ONLY bootstrap PR by the repo owner
  (workflow_run-triggered workflows resolve their own YAML from `main`, so a
  fix to `.github/workflows/*.yml` occasionally must land there directly) --
  `main-gate` (ci.yml) enforces this by diff content, not by admin bypass;
  see ThomasMichon/copilot-extensions#3622-erratum for the incident that
  prompted the hardening. Contributors always target `dev` — a PR whose
  base is `main` and that matches NEITHER shape is not a legitimate use of
  this repo's PR flow (the repo's own CI has a hard guard checking exactly
  this, but that only fails the build; this comment is the visible signal
  on the PR itself, since Copilot review is disabled on `main`'s ruleset --
  the hard block is the sole gate). Post this comment, verbatim, exactly
  once per PR (do not repeat it on subsequent review passes of the same PR):

  > This PR targets `main` directly. `main` only ever moves via this
  > repo's own CI promotion pipeline (dev-branch-release-pipeline effort,
  > `.github/workflows/validate-and-promote.yml`) from a `release/promote-*`
  > branch, or a workflow-file-ONLY bootstrap PR. Please retarget this PR's
  > base branch to `dev` — see `CONTRIBUTING.md` § Release & Versioning.
- **Scope to the diff.** Review the code the PR actually changes. The repo
  carries pre-existing style debt — do **not** demand repo-wide cleanup or
  flag untouched code.
- **Concrete over cosmetic.** Prefer flagging concrete violations of
  `AGENTS.md`'s and `CONTRIBUTING.md`'s standards over stylistic nitpicks.
- **Lead with the highest-signal miss: the changefile requirement.** For any
  changed plugin *payload* in a PR targeting `dev`, verify a pending
  changefile names it (`python tools/changefile.py add --plugin <name>
  --type <major|minor|patch|dev> --comment "..."`) — a missing changefile
  means the CI promotion pipeline has nothing to bump for that plugin when
  it next regenerates `main`, so the marketplace silently keeps serving the
  old version. This replaced the old three-file hand-bump convention:
  contributors no longer hand-edit `plugin.json` / `pyproject.toml` /
  `marketplace.json` version fields themselves — the promotion pipeline's
  `tools/accumulate_bumps.py` writes the real version numbers mechanically
  when it consumes pending changefiles. Do not ask for a hand-bumped
  triplet on an ordinary PR; that is now only correct on the pipeline's own
  generated snapshot commit.
- **Tests for runtime logic.** Flag PRs that change a runtime plugin's logic
  without adding or updating that plugin's `tests/`.
- **Test portfolio growth.** Flag new exhaustive matrices, repeated process
  startup, or specialized suites added unconditionally to required PR CI.
  Require path gating, a focused smoke contract, and a scheduled/manual
  exhaustive lane. Do not recommend pooling where real process boundaries
  are the behavior under test.
- **Cross-platform parity.** When a PR edits an installer/launcher `.sh` (or
  `.ps1`), flag a missing matching change to its `.ps1` (or `.sh`)
  counterpart.
- **Identifier neutrality.** Flag any newly introduced internal
  organization/account/project names, private hostnames, or personal
  aliases — this repository is public. PR titles, bodies, labels, commit
  messages, and hidden comments count; the default `codename` marker (only
  the worktree's assigned codename, decodes to nothing on its own) is
  expected and not a violation, but flag any attempt to enable the full raw
  `pr.source_attribution: true` here.
- **Contribution boundary.** Flag a change whose value or implementation
  depends on a particular person's private state or a particular
  organization's internal systems, identity, process, or data — that
  belongs in the adopter's private control repo or that organization's
  internal marketplace, not here (`CONTRIBUTING.md`'s "Contribution
  boundary").
- **Architectural changes reconcile to both layers.** A design change (not a
  below-altitude lint/typo/dependency bump) should reconcile with the
  relevant `visions/` entry *and* check against `docs/patterns/README.md`'s
  design principles and invariants -- flag a design change that does
  neither.
- **Fresh eyes are the point — apply full independent scrutiny regardless of
  a PR's own stated rationale, especially for vision-conformance and
  security.** A PR description explaining *why* a design choice was made is
  context about a constraint the author faced, not a substitute for your own
  judgment on whether that choice is actually sound — evaluate the tradeoff
  on its merits, and challenge it if you disagree, the same as you would an
  unexplained one. Treat an unexplained deliberate-looking choice (an
  obvious-looking alternative visibly not taken, a limitation visibly
  accepted) as a cue to **investigate**, not as evidence it was already
  vetted — silence is not proof of due diligence. Consistent with "Concrete
  over cosmetic" below: only actually comment once that investigation
  surfaces a concrete concern, not as a speculative open question raised
  from the absence of an explanation alone. This scrutiny matters most
  exactly where the author's proximity to their own implementation is a
  likely source of blind spots: new privilege/trust boundaries, credential
  or token handling, authentication/authorization assumptions, and any
  divergence from a documented vision or invariant.
- **Documentation impact.** Confirm the PR description's required
  Documentation-impact statement actually matches the final diff
  (`CONTRIBUTING.md`, "Documentation impact") -- flag a missing or
  inaccurate one.
- **Graceful cutover impact.** When a PR introduces or materially changes a
  long-lived resident daemon, confirm the PR description's required
  **Graceful cutover impact** statement exists and matches the diff
  (`CONTRIBUTING.md`, "Graceful cutover impact"). Flag a missing statement, a
  daemon change with no named activation seam/safe cutover point, or an
  exemption claim that does not fit one of the documented alternatives:
  `graceful-daemon-cutover`, the lighter
  `service-lifecycle-supervision` singleton-handoff path, or a demonstrated
  non-daemon lifecycle governed by another pattern such as
  `ephemeral-process-reaping`.
- **Daemon-lifecycle concurrency & ordering.** For a PR touching cutover,
  drain, promotion, or process-repair logic, check it against
  `docs/patterns/graceful-daemon-cutover.md`'s "Common review findings"
  checklist: overlapping cutover attempts must be serialized under one
  lease/guard; a successor's promotion must be *confirmed* before its
  predecessor retires (never the reverse); the drain boundary must close
  admission **and** wait out every already-admitted concurrent request, not
  just the periodic sweep; and any repair/self-heal path must re-validate its
  target's identity immediately before acting, not only at snapshot time.
- **PID-identity-bound destructive code needs a direct test.** Flag any
  change that terminates or reaps a process by PID without a dedicated unit
  test proving the terminator (a) matches only a live, identity-verified
  target and (b) refuses on identity mismatch (stale/reused PID, wrong
  owner). An end-to-end rehearsal alone does not satisfy this.
- **Cross-platform completeness beyond installer scripts.** The existing
  "Cross-platform parity" bullet below covers `install.sh`/`install.ps1`
  pairs; separately, flag a process-census/liveness primitive that
  implicitly conflates "POSIX" with "Linux" (e.g. `/proc`- or
  `pidfd`-based code presented as general POSIX support) — a change
  claiming cross-platform daemon/process support should name Windows,
  Linux, and macOS explicitly, each either implemented or explicitly and
  justifiably exempted.
- **ruff signal, not noise.** Hold changed Python to at least the `F`/`E9`
  groups; do not block on pre-existing style debt in code the PR did not
  touch.
- **Timeless code and docs — flag review-artifact language baked into the
  diff itself.** Code, comments, docstrings, and non-Journal documentation
  should read as the system's current state, not a trace of the review that
  produced it. Flag a code comment, docstring, or doc-prose edit that
  references the review process ("per review feedback," "reviewer
  requested," "(review round N)") or a prior, possibly-never-committed
  version of itself ("previously X, now Y") — that response belongs on the
  review comment thread, not in the artifact (`CONTRIBUTING.md`, "Code
  Style"). The one exception is a project's own dated `## Journal` section,
  which is a decision log by design — but even there, flag an entry whose
  only stated justification is that a review said so, with no independent
  technical reasoning (the invariant, bug, or constraint actually involved):
  a review comment citing itself as authority is the same circular-reference
  problem as a Wikipedia article sourcing only itself, and a review thread
  is not guaranteed to stay inspectable.
- **Render `Approve` when ready — on a contributor's PR.** Copilot code
  review can only ever submit `Approve` or `Comment` (there is no
  `Request changes` capability in Copilot code review at all — see
  CONTRIBUTING.md § "Waiting for a verdict" for the current GitHub-docs
  citation). Approvals
  are enabled in this repo (Settings → Copilot → Code review →
  Auto-approval), so for a PR authored by someone other than this repo's
  owner, once there is no remaining Medium/High-severity finding and the
  overview's own readiness assessment says it's ready to merge, **submit
  that as a genuine `Approve` review**, not a `Comment` review whose text
  merely says the PR looks ready. A `Comment`-only verdict there is the
  submitter's and maintainer's signal that real, unresolved findings
  remain (see `CONTRIBUTING.md` § "Waiting for a verdict") — do not leave
  that PR in `Comment` limbo once nothing substantive is left to flag.
  **This repo's own PRs authored by its owner are a documented exception**
  (`plugins/agent-worktrees/src/agent_worktrees/pr_contract.py`'s
  `NONBLOCKING_VERDICT_STATES`, empirically confirmed: every review on
  every owner-authored PR in this repo's history has been `Comment`, never
  `Approve`) — on those, a `Comment` review with zero remaining
  Medium/High-severity findings **is** the passing verdict; do not
  attempt to force an `Approve` there, and do not treat a clean `Comment`
  on an owner-authored PR as an unfinished review.
- **Make every comment count.** Copilot review comments should each be
  actionable and worth the author's attention, whether the review's overall
  verdict ends up `Approve` or `Comment`.
- **State plainly whether remaining findings are blocking.** When a
  `Comment` verdict's remaining findings are all Low severity (no Medium or
  High open), say so explicitly in the overview — e.g. "remaining findings
  are Low-severity and non-blocking" — rather than leaving severity icons as
  the only signal. On an **owner-authored PR**, this is Copilot's own passing
  verdict shape outright. On a **Contributor PR**, note additionally that
  this reflects only Copilot's verdict gate — it is the accepted
  stall-breaker after a full review loop, per `CONTRIBUTING.md` § "Waiting
  for a verdict" step 5, and never substitutes for the separate,
  always-required Maintainer-approval review. Stating this in plain language
  removes the need for the author to infer it from severity counts alone,
  and avoids an unbounded loop of the author chasing zero remaining comments
  past the point where this repo's own contribution flow already treats
  Copilot's verdict as satisfied.
