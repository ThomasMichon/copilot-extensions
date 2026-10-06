# Contributing to Copilot Extensions

## Contribution boundary

This marketplace accepts **general-purpose capabilities** that can be used
without a particular person's private state or a particular organization's
internal systems, identity, process, or data.

Not welcome here:

- Personal/operator-specific workflows, machine inventory, private state, or
  experiments whose audience is one harness. Keep those in the adopter's
  private control/knowledge repo.
- Organization-specific workflows, internal systems, or company-bound policy.
  Those belong in that organization's internal marketplace.

A generic engine may live here while an organization-specific policy/config
plugin lives in its internal marketplace. If removing personal and
organizational assumptions would change the capability's purpose, this is not
its destination.

## Contribution flow (PR-required)

**PRs target `dev`, not `main`.** `main` is regenerated wholesale by a CI
promotion pipeline (`.github/workflows/validate-and-promote.yml`,
dev-branch-release-pipeline effort, ThomasMichon/copilot-extensions#3336) —
it is never a place a PR merges into directly. `dev` is this repo's default
branch, so an ordinary PR already targets it without needing to specify a
base branch.

> ### Migrating from the old `main`-targeting flow
>
> If you (or a stale worktree/bookmark) still opens PRs against `main`, three
> things changed with this cutover that are easy to get bitten by:
>
> 1. **Retarget, don't fight it.** An open PR against `main` will show as
>    conflicting/blocked once `main` starts moving only through generated
>    promotion commits (it stops sharing history with ordinary `dev`-based
>    branches). Retarget it — `gh pr edit <#> --base dev` — then rebase your
>    branch onto current `dev` and resolve any conflicts for real (don't just
>    take one side; `dev` may have moved the same files). There is no need to
>    open a fresh PR; the existing one keeps its history/discussion.
> 2. **A broken `dev` build blocks *every* release, not just your change.**
>    The promotion pipeline only fires on a green `dev` CI run (a separate
>    cheap filter workflow reacts to `CI` completing and dispatches the
>    validate/promote workflow); if the run your
>    PR merged into is red, **nothing promotes to `main` until a later `dev`
>    commit is green again** — your fix included. Contributors are expected to
>    fix a CI failure on `dev` promptly (a revert is always acceptable if a
>    same-day forward fix isn't ready) rather than leaving it red, because
>    every other pending contributor's release is stuck behind it too.
> 3. **A release is not instant — expect roughly 10-20 minutes**, not the old
>    "merge = shipped" mental model: your merge has to (a) finish `dev`'s own
>    CI, (b) clear the `Validation Gate`, (c) get promoted into a generated
>    `release/promote-<run id>` candidate PR against `main`, and (d) have that
>    candidate PR's own (fast) check pass before it auto-merges. If you want to
>    confirm your change actually shipped, watch for that candidate PR
>    merging (`gh pr list --search "is:merged head:release/promote-"`, or just
>    watch `main`'s commit history) rather than assuming your `dev` merge was
>    the release -- and never via `git merge-base --is-ancestor <your-sha>
>    origin/main`, which can report false even after a real promotion
>    (`promote` replays the gate's pinned, validated `dev` snapshot into a
>    new commit, not necessarily `dev`'s current tip, so your original SHA
>    never becomes a literal ancestor). A separate **"clear consumed
>    changefiles on dev"** housekeeping PR follows on its own daily (or
>    on-demand) schedule, not per promotion — routine cleanup, not something
>    you need to review or act on, but don't be surprised to see one land
>    within a day.
>
> See [docs/pipelines.md § Promotion: dev → main](docs/pipelines.md#promotion-dev--main)
> for the full mechanics and how to preview a pending release without
> waiting for a real promotion.

**Every change lands through a pull request against `dev` — direct pushes
are blocked, and `main` accepts no direct push from anyone (including the
promotion pipeline itself); it only ever lands through that pipeline's own
generated PR, or explicit admin escalation.** This is enforced by four
independent,
agreeing layers (tooling, branch policy, review automation, and
workflow/CODEOWNERS lockdown), plus a stricter automatic-nothing default
for anyone not an invited collaborator. See
[docs/pipelines.md § PR gating: the four enforcement layers](docs/pipelines.md#pr-gating-the-four-enforcement-layers)
for the full mechanics of each layer, and
[docs/pipelines.md § Automated workflows reference](docs/pipelines.md#automated-workflows-reference)
for what every workflow in `.github/workflows/` actually does.

### The flow every agent (and human) uses

```bash
copilot-extensions create            # isolated worktree (no mux/session)
#   …edit in the returned worktree path…
#   Complete the documentation-impact review below.
copilot-extensions create-pr         # squashes the worktree, pushes pr/<slug>,
                                     # and (auto_open) opens the GitHub PR
#   → wait up to 10 minutes for Copilot's review to land (re-requesting a
#     review is itself not instant -- give it room to actually run)
#   → Contributor PR, Approved: merge. Owner-authored PR, clean Comment (no
#     Medium/High findings open): merge -- that's the passing verdict here,
#     Copilot structurally never renders Approve on this repo's own PRs.
#     Otherwise: address findings, push, wait up to 10 minutes for the
#     automatic post-push review; still not passing -> re-request review via
#     the API (see "Requesting a fresh review" below), wait up to 10 minutes
#     again. A Contributor PR gets up to 3 such rounds striving for a real
#     Approve before the maintainer bypass below may apply. See "Waiting for
#     a verdict" for the full loop.
copilot-extensions pr-merge ThomasMichon/copilot-extensions <#> --now   # MANUAL squash-merge (you own the merge)
copilot-extensions finalize          # clean up the worktree
```

- **Update an open PR** with `copilot-extensions push-changes` (it re-pushes the
  `pr/<slug>` head; it will NOT land on `main`).
- **Merge is deliberately manual.** No auto-merge label is bound (the repo's
  `pr-self-merge` profile authorizes the submitter to merge directly): once
  the wait-for-a-verdict loop below is satisfied, squash-merge with
  `pr-merge <#> --now` (equivalent to a plain `gh pr merge <#> --squash
  --delete-branch`, but it resolves the right account and squashes
  uniformly).

### Waiting for a verdict

> **TL;DR (the shared stopping rule both the author and reviewer converge
> on for Copilot's own verdict — a separate gate from required status checks
> and from merge authorization, see below):** `Approve` satisfies Copilot's
> verdict gate. `Comment` with **zero Medium/High-severity findings open**
> also satisfies it — on an owner-authored PR that's the passing shape
> outright; on a Contributor PR it's only the accepted stall-breaker **after
> up to 3 rounds** (step 5 below) of genuinely striving for a real `Approve`
> — a first-round clean `Comment` still needs another review attempt, not
> an immediate merge. Any Medium/High finding still open blocks proceeding
> at all, regardless of verdict shape. Once that condition holds, a
> still-open Low-severity finding stays whatever it already was — genuinely
> valuable, fix it; already considered and dismissed, don't spin a further
> review round solely to make the comment thread read zero. **Required
> status checks are a separate merge gate, not part of Copilot's verdict** —
> a clean review can land before or after checks finish; don't wait on
> checks to decide whether the verdict gate is satisfied. **Satisfying
> Copilot's verdict gate is never merge authorization by itself: a
> Contributor PR still requires a separate Maintainer-approval review before
> merging** (see [docs/pipelines.md § Review automation](docs/pipelines.md#3-review-automation)); only the repo owner's
> own bypassed PRs skip that second gate.
> The full loop below covers cursor hygiene, re-review requests, and the
> Contributor-vs-owner verdict-shape difference in detail — read it once,
> then apply this TL;DR on every subsequent round rather than re-deriving it.
> **Endeavor to get a genuine `Approve` on a Contributor PR** — the 3-round
> bound exists so an overly stubborn or cautious reviewer can't block a
> merge indefinitely, not as a target to race toward; a Maintainer may
> short-circuit earlier only when the stall is genuinely unresolvable (see
> step 5), not as a default shortcut.

**A finding that still shows "open" after you already fixed it is a known
reviewer limitation, not a new regression — say so instead of silently
re-fixing.** Copilot's review does not always re-validate a carried-over
finding against the latest diff before re-listing it (see `REVIEW.md`'s own
"re-validate every carried-over finding" directive, which addresses the
reviewer side of this). When your own push genuinely already addressed a
finding that reappears in the next round's overview unchanged, name the
specific commit/line that fixed it in your next push's commit message or a
reply on that finding's own thread, rather than spending another round
re-touching code that is already correct — this both documents the
discrepancy for anyone reading the history later and gives the next
automatic pass a concrete anchor to re-check against. The same applies to a
finding your PR already discloses as a deliberately out-of-scope, tracked
limitation (a concrete issue reference, not a vague "known issue"): if it
keeps getting re-raised at its original severity despite the disclosure and
a bounded mitigation already shipped, point back at that disclosure rather
than re-engineering the same already-accepted tradeoff every round.

**Copilot code review can only ever render two outcomes: `Approve` or
`Comment`.** (There is no "Request changes" capability in Copilot code
review at all — confirmed against GitHub's own current docs, which state
"By default, Copilot leaves a 'Comment' review, not an 'Approve' review or
a 'Request changes' review... if configured to do so, Copilot can leave
'Approve' reviews" ([Using GitHub Copilot code
review](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/request-a-code-review/use-code-review),
step 4) — the only configurable outcome beyond the default `Comment` is
`Approve`; [Configuring code review by GitHub
Copilot](https://docs.github.com/en/copilot/how-tos/copilot-on-github/set-up-copilot/configure-code-review)
documents no setting that adds a `Request changes` outcome; do not write or
expect a `CHANGES_REQUESTED` state from it.) Approvals are enabled in this repo
(Settings → Copilot → Code review → Auto-approval) — see [`REVIEW.md`](REVIEW.md)'s
directive requiring Copilot to render `Approve` whenever it has no blocking
findings, rather than habitually leaving a `Comment` review that just
narrates readiness. **That directive, and "a genuinely ready PR should come
back `Approve`," only holds for a PR authored by someone other than this
repo's owner.** This repo's owner-authored PRs (i.e. this repo's own,
self-merge PRs — the common case when driving work here) are a documented,
empirically confirmed exception: GitHub's Copilot code review structurally
never renders `Approve` there, only ever `Comment`
(`plugins/agent-worktrees/src/agent_worktrees/pr_contract.py`'s
`NONBLOCKING_VERDICT_STATES`; every review across every merged owner-authored
PR in this repo's history has been `Comment`, with zero `Approve`s ever
observed). On an owner-authored PR, the passing verdict is a **clean
`Comment`** — zero remaining Medium/High-severity findings — not a `Comment`
that is merely "waited out." Do not spend further review rounds chasing an
`Approve` that literally cannot land there.

> **This never-`Approve` quirk is specific to the literal GitHub repository
> *owner* account, not the wider Maintainer group.** The other Maintainers
> are ordinary (non-owner) accounts from Copilot's perspective — their own
> PRs should be treated like a Contributor's for verdict *shape* (wait for a
> genuine `Approve`, not a clean-`Comment` substitute) even though they
> don't need anyone else's approving review to merge (the ruleset bypass
> above).
>
> This is a bigger gap than verdict *shape* alone: without
> `MAINTAINER_REVIEW_PAT` (see [docs/pipelines.md § Review automation](docs/pipelines.md#3-review-automation)), the automatic request never
> reaches Copilot at all for a non-owner Maintainer's PR — `requestReviewers`
> silently no-ops under the default `GITHUB_TOKEN`, with no exception and no
> timeline event, so no verdict ever arrives to have a shape. Routing the
> automatic request through a licensed human account instead of the bot
> identity is what makes a verdict arrive at all; the "wait for a genuine
> `Approve`, not a `Comment`" guidance above still holds for whatever verdict
> lands once the request succeeds.

**Everyone — Contributor and Maintainer alike — waits for a verdict before
merging**, and no one merges past an open Medium/High-severity finding.
What differs is only the *shape* of the passing verdict: Contributor PRs
need `Approve`; the repo owner's own PRs need a `Comment` review with
nothing Medium/High left open (see the note above for the other
Maintainers). Each wait below uses `pr-watch wait <owner>/<repo>
<PR> --since <cursor> --until approved,commented,changes_requested
--timeout 600` — scope `--until` to actual review transitions (`--until
any` also wakes on unrelated transitions like checks or conflicts, which is
not itself a review result), **check `events[].review.user` before treating
a wake as Copilot's verdict** (this same `--until` set also wakes on an
`approved`/`changes_requested` review from a human reviewer, which is not
Copilot's verdict and follows the ordinary human-review path instead), and
**capture a fresh `<cursor>` immediately before each wait** (the cursor
`pr-watch`/`pr-status` returns, or `pr-watch cursor <owner>/<repo> <PR>`
right before waiting) — reusing a stale cursor (e.g. always passing
`--since r0`) can report an *old* review instead of waiting for the new
one, since `r0` is the lowest possible baseline, not a "from now" marker.
**Wait up to 10 minutes per attempt, not ~5** — triggering a review (an
initial open, a push, or an explicit re-request) is not instant, so give
each attempt real room to actually land before treating it as a timeout:

1. Open (or update) the PR, then wait **up to 10 minutes** (order of
   minutes, not hours) for Copilot's review to land. **Nothing landed (a
   timeout, not a review event):** there's nothing to address or push yet —
   skip straight to step 4's re-request action rather than inventing an
   unrelated commit.
2. **Contributor PR, `Approve` landed:** proceed to merge (subject to the
   separate required-approving-review gate for a Contributor's PR — see
   [docs/pipelines.md § Review automation](docs/pipelines.md#3-review-automation);
   Copilot's own `Approve` never substitutes for that).
   **Owner-authored PR, `Comment` landed with zero Medium/High findings
   open:** that *is* the passing verdict here — proceed to merge, stating
   which (Low-severity or already-addressed) findings were dismissed and
   why in the merge/commit message.
3. **`Comment` landed, and addressing it requires an actual change:**
   address the genuinely valuable findings, push the update, then wait up
   to 10 minutes for the automatic post-push review and go to step 4.
   **`Comment` landed, but every finding is dismissed/explained with no
   actual change needed:** there's nothing new for a re-review to see —
   skip the push and go straight to step 4's re-request action.
4. **Automatic post-push review landed `Approve` (contributor PR) or a
   clean `Comment` (owner-authored PR):** merge. **Still not there** (a
   `Comment` with a Medium/High finding still open, or the post-push wait
   also timed out with nothing landing): explicitly re-request a review —
   see "Requesting a fresh review" below — then wait up to 10 minutes again
   and return to step 2. Do not just keep pushing small commits hoping the
   next automatic pass flips on its own, and do not treat a timeout here
   differently from a `Comment` — both mean "not yet passing, re-request."
   **Count this as one round** (steps 2→4 once through) — a Contributor PR
   gets up to 3 rounds before step 5's bypass may apply; genuinely strive
   for a real `Approve` across those rounds rather than treating the bound
   as a target.
5. **Genuine unresolvable-finding stall, Contributor PRs only:** if the
   loop above has run through **up to 3 rounds** on a Contributor's PR
   without landing `Approve`, and the *current* `Comment` review's
   remaining findings are **all Low severity** (no Medium or High findings
   open), Copilot's *verdict-shape* requirement (this step) is satisfied
   without chasing a further `Approve` — state which findings were
   dismissed and why. This bound exists so a genuinely stubborn or overly
   cautious reviewer can't block a merge indefinitely — it is not license
   to invoke the bypass at round 1 just because a first pass came back
   `Comment`; use the full 3 rounds when the reviewer keeps surfacing
   findings worth engaging with. **This is
   strictly about Copilot's own verdict and does NOT touch the separate,
   always-required Maintainer-approval gate** for a Contributor's PR (see
   [docs/pipelines.md § Review automation](docs/pipelines.md#3-review-automation)) — some Maintainer still must actually
   approve the PR before anyone merges it; satisfying this step alone never
   authorizes a merge by itself. Any Medium or High finding still blocks
   proceeding past this step at all, regardless of Maintainer approval,
   until it's resolved and a *subsequent* review actually passes — merely
   re-requesting a review is not itself a verdict.
- **This is agent discipline, not yet tool-enforced.** `pr-merge --now`
  itself does not check Copilot's verdict before merging --
  `.agent-worktrees/config.yaml`'s `review_blocking: false` makes every
  `pr-merge` call pass `--admin` to the provider (bypassing GitHub's own
  review-gate check unconditionally), so nothing currently stops a driving
  agent from merging before this loop is actually satisfied. Follow the
  loop above deliberately; do not rely on the tooling to refuse a premature
  merge on your behalf. (Flipping `review_blocking` to `true` is a real
  lever for closing this gap, but is a separate decision with its own
  behavior-change risk -- e.g. this repo's own ruleset already exempts the
  maintainer from any required approving review count, so the practical
  effect for the maintainer's own PRs needs its own validation, not an
  assumption -- and is out of scope for this documentation change.)
- **`pr-status`/`pr-watch`'s `eligible: false` / `reason: "not yet
  approved"` fields still refer only to the codeowner/review-count gate**
  (see [docs/pipelines.md § Review automation](docs/pipelines.md#3-review-automation)), not to Copilot's own verdict — for the maintainer's
  own bypassed PRs those fields are a known tooling-wording gap
  (copilot-extensions#3638), not a live merge gate for this account. They
  are unrelated to whether Copilot has rendered a passing verdict yet;
  track that separately via the PR's reviews. Both `pr-status --json` and
  `pr-watch wait ... --json` surface a `self_merge_note` field (and, for
  `pr-watch`, an accompanying stderr line) precisely when a live read
  confirms the acting identity holds Maintainer-bypass rights on an
  otherwise-required review — read that field, not `eligible`/`reason`
  alone, before concluding a `pr-self-merge` repo's PR is genuinely blocked
  on approval.
- **Do not comment `@copilot review` (or similar) to request a fresh pass.**
  An `@copilot` mention on GitHub does not nudge the `copilot-pull-request-reviewer`
  bot -- it delegates a task to the separate Copilot **cloud coding agent**,
  which will start pushing its own commits directly to your PR branch (it can
  and will act on open review findings, which may or may not be what you
  want, and consumes its own credit budget independent of your session).

### Requesting a fresh review

A push alone re-triggers an automatic review, but if that automatic pass
still comes back `Comment`, explicitly re-request a review rather than
pushing another commit and hoping. This is the same "Re-request review"
action GitHub's own UI offers next to Copilot's name in the Reviewers
list, done via the API instead of clicking:

```bash
gh api repos/ThomasMichon/copilot-extensions/pulls/<PR>/requested_reviewers \
  -X POST -f "reviewers[]=copilot-pull-request-reviewer[bot]"
```

(Confirmed both by GitHub's own docs and by a direct live test in this
repo, not just cited: GitHub's docs describe this exact call —
["Using GitHub Copilot code review" § Requesting a re-review from
Copilot](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/request-a-code-review/use-code-review#requesting-a-re-review-from-copilot),
and [REST API endpoints for review
requests](https://docs.github.com/en/rest/pulls/review-requests#request-reviewers-for-a-pull-request)
— and this exact endpoint really does trigger a genuine fresh re-review of
an *already-reviewed, unchanged* commit, not just a no-op "add reviewer"
call: on 2026-09-27, PR #4328 got an initial review at 20:02:56 UTC on
commit `7ddb5dfc`; this call was issued against that same commit around
20:07 UTC with no intervening push; a second, distinct review (different
review ID) landed on that *same* commit `7ddb5dfc` at 20:12:47 UTC — the
next actual push's own CI run wasn't even created until 20:12:56 UTC, so
that second review could not have been triggered by a push.)
This prompts a genuinely fresh, full-PR assessment — not just a diff-only
pass against the latest push — which is what actually gives Copilot the
chance to flip from `Comment` to `Approve` once nothing substantive
remains.
- **Never** `git push origin main` or `push-changes` direct-to-`main`; both the
  tooling and the branch policy reject it. Break-glass (a genuine recovery)
  means temporarily relaxing the ruleset — not routing around it.

### Self-review against REVIEW.md before opening a PR

[`REVIEW.md`](REVIEW.md) is not reviewer-only reading. It is the same rubric
Copilot's automated review applies to your diff, so read it and self-check
your own change against its directives **before** opening the PR, not after
the first review round names what it would have caught. This is the single
highest-leverage step for reducing review rounds: a coding agent that opens a
PR "blind" to the rubric the reviewer will apply is guaranteed at least one
avoidable round on anything the rubric already names (changefile
completeness, Documentation impact, cross-platform parity, test coverage for
changed runtime logic, and so on).

Two failure modes to avoid once review findings start arriving, both of
which turn a bounded review loop into an unbounded one:

- **Whack-a-mole fixes.** When a finding names one instance of a bug class
  (a missing test, an unserialized race, a platform gap), check the rest of
  the diff for the *same class*, not just the flagged line — fixing one
  instance while a sibling function has the identical defect just spends the
  next review round rediscovering it.
- **Chasing zero comments instead of the actual bar.** Once the loop in
  "Waiting for a verdict" above says you've satisfied Copilot's own verdict
  gate (its TL;DR: `Approve`, or `Comment` with zero Medium/High findings —
  plus, on a Contributor PR, the one-full-loop qualifier and the
  still-separate Maintainer-approval gate; checks are their own independent
  merge gate, not part of this condition), stop iterating on that verdict and
  proceed to whichever merge step actually applies. A still-open
  Low-severity finding at that point is either genuinely valuable — fix it —
  or already considered and dismissed; spinning a further review round
  solely to make the comment thread read zero is optimizing for a bar
  neither this repo's contribution flow nor the automated reviewer's own
  directives actually require.

### Give the reviewer your context, not just your diff

Self-reviewing against REVIEW.md (above) closes the gap where both roles
apply the *same* rubric. It does not close a different, asymmetric gap: you
approach your own PR with whatever subject-matter context you built up while
authoring it — prior attempts, constraints that ruled out an obvious-looking
alternative, limitations accepted on purpose; Copilot's review approaches
every PR **fresh**, with no access to the session or conversation that
produced it. The PR description and whatever docs it links are the *entire*
context-transfer channel — not a formality, and not something the reviewer
can query you about mid-review the way a human reviewer might in a comment
thread.

When your diff makes a deliberate choice a reviewer might reasonably
question — you tried the more obvious approach and rejected it, a known
constraint (a platform limitation, an existing invariant, a prior incident)
shaped the design, or you're accepting a limitation on purpose rather than by
oversight — **say so explicitly in the PR description**, and cite the
doc/effort/vision/issue that grounds it. An unstated rationale is
indistinguishable, from the reviewer's side, from a gap nobody considered —
and costs a review round to resolve either way, the same round a single
sentence in the PR body would have pre-empted.

**This context transfer is bounded by the same public-repo rules as
everything else you publish here.** This repo is public, and "Contribution
boundary" above already requires proprietary organization/person-specific
context to stay in a private control repo. If the actual motivating
constraint (an incident, a private downstream system, an internal process)
isn't itself public, cite a **public, identifier-neutral** grounding artifact
instead — a public doc/effort/vision/issue in *this* repo describing the
constraint in general terms — rather than describing the private specifics
in the PR body to satisfy this section. When no such public grounding exists,
state the constraint generically (what class of limitation, not which private
incident or system) rather than omit it or leak it.

**This is context supply, not a request for deference.** Explaining a
decision does not pre-empt the reviewer's right to disagree with it, and
should not shrink the scrutiny applied to it — particularly for
vision-conformance and security-relevant choices, where the reviewer's
outside, fresh-eyes perspective is exactly the check this repo relies on
Copilot review to provide, precisely because proximity to one's own
implementation is a common source of blind spots the author cannot
self-review away. State your reasoning so the reviewer is evaluating your
*actual* tradeoff instead of a guessed-at one; expect it to still be
challenged on the merits.

### Parent trackers stay open across partial slices

Use `Refs` or `Part of` for an issue that a PR only advances. Do not put a
closing keyword next to that issue number, even in a sentence saying the PR
does *not* close it: GitHub recognizes the keyword/reference pair without
honoring the negation.

Before merging a partial slice, inspect its closing references:

```bash
agent-worktrees repos gh ThomasMichon/copilot-extensions -- pr view <number> --repo ThomasMichon/copilot-extensions --json closingIssuesReferences
```

The result must not contain an unfinished parent tracker. After merge, verify
both the PR's merged state and the parent issue's
expected open state. If accidental closure occurs, remove the closing phrase,
reopen the issue, and record the correction; a null `commit_id` in an issue
closure event is not by itself evidence that an agent called an issue-close API.

### Documentation impact (required before opening a PR)

Assess the final diff and repeat the assessment after material scope or
implementation changes. Apply this review to every change classification,
including bug fixes, compatibility repairs, and below-altitude work.

1. Update the authoritative documentation affected by changes to behavior,
   guarantees, interfaces, configuration, output/error handling, process
   lifecycle, operating procedures, or platform support. Keep already-accurate
   documentation unchanged.
2. Reconcile architectural changes with their governing vision and patterns.
   Revise a vision when intended behavior or guarantees change; put
   implementation details in architecture or operating documentation.
3. Include a **Documentation impact** statement in the PR description, linking
   the documentation updated or explaining why existing documentation remains
   accurate and complete.

Reviewers confirm that the statement and documentation match the final diff.
Treat a missing assessment or inaccurate affected documentation as unfinished
work.

### Graceful cutover impact (required for resident-daemon changes)

Any PR that introduces or materially changes a **long-lived resident daemon**
— usually a Runtime service plugin, but also any other plugin/tooling that adds
an always-on local process — must include a **Graceful cutover impact**
statement in the PR description.

1. Name the daemon(s) and the installer/update/activation seam that owns their
   rollout.
2. State how the change satisfies
   [`docs/patterns/graceful-daemon-cutover.md`](docs/patterns/graceful-daemon-cutover.md),
   including the safe cutover/drain boundary; or, if claiming an exemption,
   explain either **why the process is not a long-lived resident daemon** and
   which lifecycle pattern governs it instead, **or** why it fits the
   documented lighter
   [`service-lifecycle-supervision`](docs/patterns/service-lifecycle-supervision.md)
   singleton-handoff path (no shared endpoint and no in-flight request to
   drain).
3. Link the doc/effort updates that record the contract, or explain why
   existing documentation remains accurate and complete.
4. Self-check the diff against
   [`docs/patterns/graceful-daemon-cutover.md`](docs/patterns/graceful-daemon-cutover.md)'s
   "Common review findings" checklist **before** opening the PR — it enumerates
   the small set of concurrency-ordering, PID-identity-safety, and
   cross-platform gaps that recurred across this repo's own four
   graceful-cutover implementation PRs (6-14 review rounds each). Catching
   them here is materially cheaper than a review round.

Reviewers treat a missing or hand-wavy statement as unfinished work.

There is intentionally **no CI guard for this today**. This repo has no
reliable static signal for "a new resident daemon was introduced": heuristics
over names like `serve`/`daemon`, `while True` loops, vendored `zdd`, or
`plugin.json["zeroDowntimeUpdate"]` would both miss real daemon introductions
and flag unrelated code, while legitimate adopters already span plugin and
non-plugin surfaces (`worktree-manager`) plus both `install.*` and `init.*`
activation seams. Until the suite gains a manifest-level daemon declaration,
this PR-description statement is the review-time gate; reviewers also enforce
that any claimed singleton-handoff exception really matches the documented
`service-lifecycle-supervision` criteria above.

## Release & Versioning, and the dev → main promotion pipeline

Versioning is changefile-driven (a contributor never hand-edits a version
number), and `dev` promotes to `main` through an automated CI pipeline, not
a direct merge. See
[docs/pipelines.md § Release & Versioning](docs/pipelines.md#release--versioning)
for the full version scheme and changefile workflow, and
[docs/pipelines.md § Promotion: dev → main](docs/pipelines.md#promotion-dev--main)
for the promotion pipeline's timing, how to preview a pending release
without waiting for a real promotion, the `main`-admin-merge prohibition,
what to do if a PR was opened against `main` by mistake, and recovery after
a force-rewritten `main` history.

## Deploying: one command — `<repo> update`

**The canonical deploy is a single unified command: `<repo> update`**
(`agent-worktrees update`, or any repo binstub, e.g. `dotfiles update`). Run it
on each target machine (over SSH for remotes) after pushing. In one flow it:

- refreshes the marketplace catalog once, then updates **every** registered
  plugin's payload — runtime **and** payload-only (`efforts`, `visions`,
  `context-handoff`, `customizing-copilot`, `harness-*`) — by calling the plugin
  manager for you (`_update_registered_plugins`);
- rebuilds **every** runtime (agent-worktrees, agent-bridge, agent-codespaces,
  agent-containers, …) into a fresh versioned slot and cuts over;
- fast-forwards each managed repo's **anchor checkout** so in-repo config lands
  with the plugin;
- redeploys binstubs, Windows Terminal profiles, and shortcuts.

**Do not deploy plugin-by-plugin by hand.** Hand-running `copilot plugin update`
or a per-plugin `scripts/install.* update` / `scripts/init.*` is the wrong path:
it is easy to update one plugin and miss its runtime (or a sibling), and a
push without a version bump makes the payload refresh a silent no-op that *looks*
successful. The per-plugin "Deploying Agent X" sections below document the
**internals** `<repo> update` runs for you — plus the **local-testing /
recovery** path (running an installer from a local checkout before pushing).
They are not the normal deploy step.

> Prerequisite, every time: **add a changefile** (see
> [docs/pipelines.md § Contributing a change](docs/pipelines.md#contributing-a-change-add-a-changefile-dont-hand-pick-a-version)).
> `<repo> update` is version-gated — an un-bumped change deploys nothing, and
> only a consumed changefile actually bumps a version.

> **`<repo> update` does not validate an unmerged worktree's changes.** Per
> [install-contract.md § Source = where the installer runs
> from](docs/install-contract.md#source--where-the-installer-runs-from-no-flag),
> a machine's installed footprint remembers a `source.kind` (`marketplace` or
> `local`) from wherever its installer last ran, and `update`/`agent-worktrees
> update --force` **keeps pulling from that same source** — for a normal,
> already-onboarded machine that's `marketplace` (resolving `main`, typically
> via the registered **anchor checkout**, not any feature worktree). Running
> `agent-worktrees update --force` from inside a worktree with uncommitted or
> unmerged commits will silently redeploy the **unchanged** marketplace/anchor
> code — not your edit — and a version bump alone does not fix this, since
> there is nothing on `main` yet to bump to. To validate a real change **before
> merging**, run that specific plugin's own installer directly from the
> worktree (see each plugin's own "Local Testing"/"Install / Update" section
> below, e.g. `cd plugins/agent-codespaces; ./scripts/install.ps1 update`) —
> this switches that machine's footprint to `source.kind = local`, pointed at
> your worktree, until you run the unified `<repo> update` again (which flips
> it back to `marketplace`). Treat this as throwaway pre-merge validation, not
> a persistent local-dev mode: remember to run the unified `update` again after
> merging so the machine returns to tracking the marketplace normally.

## Deploying Agent Worktrees

Agent Worktrees is deployed from the `copilot-extensions` GitHub repo,
not from your project monorepo. Your project repo may contain a
parallel `worktree-manager` service that shares code but deploys
independently.

### The Deployment Pipeline

Changes follow this exact sequence — no shortcuts:

1. **Commit** changes in `plugins/agent-worktrees/`
2. **Add a changefile** for `agent-worktrees` (see [docs/pipelines.md § Contributing a change](docs/pipelines.md#contributing-a-change-add-a-changefile-dont-hand-pick-a-version))
3. **Open a PR targeting `dev`** — never push to `main` directly; `main` is
   regenerated by the CI promotion pipeline (`.github/workflows/validate-and-promote.yml`)
4. **Update on each machine** via `agent-worktrees update`
   (over SSH for remote machines)

The update command runs `copilot plugin update` to pull the latest
plugin from the marketplace, then executes the platform-specific
installer which deploys the package, regenerates `_build_info.py`
with the real commit hash, and refreshes instruction files.

### What NOT to Do

**Never copy source files directly into the deployed runtime directory
(`~/.agent-worktrees/lib/`).** This bypasses:

- Version tracking (`_build_info.py` won't reflect the real version)
- The installer's own setup steps (venv sync, wrapper generation,
  instruction file deployment, post-install hooks)
- Other machines — they won't get the update
- Rollback safety — there's no commit to revert to

If you need to test a change locally before pushing, use the installer
from the local checkout:

```powershell
# Windows — from the copilot-extensions checkout
cd plugins\agent-worktrees
.\scripts\install.ps1 update
```

```bash
# Linux/WSL — from the copilot-extensions checkout
cd plugins/agent-worktrees
./scripts/install.sh update
```

This runs the real installer against the local source, so the full
pipeline executes (build info, venv, wrappers, instructions) — just
from a local commit instead of a pushed one.

## Deploying Agent Bridge

Agent Bridge is a persistent HTTP service (not a per-session plugin).
It deploys via its **own installer scripts** in
`plugins/agent-bridge/scripts/`, not the Copilot CLI marketplace update
flow.

### The Deployment Pipeline

1. **Commit** changes in `plugins/agent-bridge/`
2. **Add a changefile** for `agent-bridge` (see [docs/pipelines.md § Contributing a change](docs/pipelines.md#contributing-a-change-add-a-changefile-dont-hand-pick-a-version))
3. **Open a PR targeting `dev`** — never push to `main` directly; `main` is
   regenerated by the CI promotion pipeline (`.github/workflows/validate-and-promote.yml`)
4. **Update on each machine** via the installer (see below)

The installer resolves the local checkout via `~/.git-repos`, installs
agent-bridge into a venv, deploys layered config, and restarts the
service. Project binstubs (e.g. `my-project services agent-bridge
update`) can also dispatch to the installer.

### Platform-Specific Deployment

| Platform | Installer | Service manager | Install location |
|----------|-----------|----------------|-----------------|
| Linux/WSL | `install.sh` | systemd | `/opt/agent-bridge/` |
| Windows | `install.ps1` | Scheduled task + PID file | `~/.agent-bridge/` |
| macOS | Planned | -- | -- |

### Local Testing

```powershell
# Windows
pwsh -File plugins\agent-bridge\scripts\install.ps1 install
```

```bash
# Linux/WSL
bash plugins/agent-bridge/scripts/install.sh install
```

### Keeping worktree-manager in sync

When fixing bugs or adding features that apply to both codebases:

1. Apply the fix in **both** `copilot-extensions` (agent-worktrees) and
   your project repo (worktree-manager)
2. Push copilot-extensions to GitHub
3. Push your project repo to its origin

The two codebases are forked — they share structure and much of the code,
but are not automatically synchronized.

## Deploying Agent Codespaces

Agent Codespaces is a session plugin with a CLI binstub. It provides the
`codespace:<name>` namespace resolver for agent-bridge and a standalone
`agent-codespaces` CLI for SSH transport, credential relay, and lifecycle
management.

### The Deployment Pipeline

1. **Commit** changes in `plugins/agent-codespaces/`
2. **Add a changefile** for `agent-codespaces` (see [docs/pipelines.md § Contributing a change](docs/pipelines.md#contributing-a-change-add-a-changefile-dont-hand-pick-a-version))
3. **Open a PR targeting `dev`** — never push to `main` directly; `main` is
   regenerated by the CI promotion pipeline (`.github/workflows/validate-and-promote.yml`)
4. **Update on each machine** via the installer

### Install / Update

```powershell
# Windows -- from the copilot-extensions checkout
cd plugins\agent-codespaces
.\scripts\install.ps1 install    # first time
.\scripts\install.ps1 update    # subsequent updates
```

```bash
# Linux/WSL -- from the copilot-extensions checkout
cd plugins/agent-codespaces
bash scripts/install.sh install
bash scripts/install.sh update
```

The installer creates a venv at `~/.agent-codespaces/`, deploys the
package and ssh-manager dependency, and places a binstub in
`~/.local/bin/`.

### Bootstrap (init)

For first-time setup on a new machine, the `init` scripts handle
everything including prerequisite checks:

```powershell
# Windows
pwsh -File plugins\agent-codespaces\scripts\init.ps1
```

```bash
# Linux/WSL
bash plugins/agent-codespaces/scripts/init.sh
```

### Version Files

Bump all three files for agent-codespaces before pushing (same rule as
other plugins):

| File | Field |
|------|-------|
| `plugins/agent-codespaces/plugin.json` | `version` |
| `plugins/agent-codespaces/pyproject.toml` | `version` under `[project]` |
| `.github/plugin/marketplace.json` | `plugins[2].version` |

## Deploying Agent Containers

Agent Containers is a CLI plugin with an `~/.agent-containers` runtime. It
provides the `container:<name>` namespace resolver for agent-bridge (installed
as a sibling package into the bridge venv) and a standalone `agent-containers`
CLI for local Docker dev-container fleet and lease management.

### The Deployment Pipeline

1. **Commit** changes in `plugins/agent-containers/`
2. **Add a changefile** for `agent-containers` (see [docs/pipelines.md § Contributing a change](docs/pipelines.md#contributing-a-change-add-a-changefile-dont-hand-pick-a-version))
3. **Open a PR targeting `dev`** — never push to `main` directly; `main` is
   regenerated by the CI promotion pipeline (`.github/workflows/validate-and-promote.yml`)
4. **Update on each machine** by re-running the init script

### Install / Update

The plugin ships only `init` scripts (no separate `install`); re-running `init`
with `--force` / `-Force` redeploys the runtime.

```powershell
# Windows -- from the copilot-extensions checkout
pwsh -File plugins\agent-containers\scripts\init.ps1            # first time
pwsh -File plugins\agent-containers\scripts\init.ps1 -Force     # redeploy
```

```bash
# Linux/WSL -- from the copilot-extensions checkout
bash plugins/agent-containers/scripts/init.sh                   # first time
bash plugins/agent-containers/scripts/init.sh --force           # redeploy
```

The init script creates a venv at `~/.agent-containers/` and places a binstub in
`~/.local/bin/`. So the bridge picks up the `container:` resolver, install
agent-containers **before** (re)running the agent-bridge installer.

## Deploying Agent MCP

Agent MCP is a standalone CLI plugin with an `~/.agent-mcp` runtime. Unlike the
other plugins it has **no** agent-bridge integration — an agent invokes the
`agent-mcp` binstub directly from its `mcp-servers` config to wrap an upstream
MCP server.

### The Deployment Pipeline

1. **Commit** changes in `plugins/agent-mcp/`
2. **Add a changefile** for `agent-mcp` (see [docs/pipelines.md § Contributing a change](docs/pipelines.md#contributing-a-change-add-a-changefile-dont-hand-pick-a-version))
3. **Open a PR targeting `dev`** — never push to `main` directly; `main` is
   regenerated by the CI promotion pipeline (`.github/workflows/validate-and-promote.yml`)
4. **Update on each machine** by re-running the init script

### Install / Update

Like agent-containers, agent-mcp ships only `init` scripts; re-run with
`--force` / `-Force` to redeploy.

```powershell
# Windows
pwsh -File plugins\agent-mcp\scripts\init.ps1            # first time
pwsh -File plugins\agent-mcp\scripts\init.ps1 -Force     # redeploy
```

```bash
# Linux/WSL
bash plugins/agent-mcp/scripts/init.sh                   # first time
bash plugins/agent-mcp/scripts/init.sh --force           # redeploy
```

The init script creates a venv at `~/.agent-mcp/` and places the `agent-mcp`
binstub in `~/.local/bin/`.

## Code Style

- **Code, comments, docstrings, and non-Journal documentation describe the
  system's current, timeless state — never the review process that shaped
  them.** Do not write "fixed per review feedback," "renamed X to Y (reviewer
  requested)," "previously did Z, now does W," or a parenthetical review-round
  citation into code, a docstring, a README, or a pattern doc. A future reader
  has no access to the review thread that motivated it, so it reads as
  unexplained clutter at best — and at worst references an intermediate state
  that was proposed, objected to, and fixed before ever being committed, so it
  describes something that never existed in this repo's actual history at
  all. A response to a review comment belongs in exactly one place: a reply on
  that comment thread (the PR body/commit message carry aggregate context) —
  never as prose baked into the artifact itself. The code/doc simply changes
  to its new correct state; nothing about *how* it got there needs to live
  inside it. The one durable exception is a project's own dated `## Journal`
  (e.g. an effort's own journal section) — that is explicitly a decision log
  by design, and "review round N caught X" is exactly what belongs in a dated
  entry there. Do not import that journaling habit into ordinary code
  comments, docstrings, or a doc's own current-state prose (including an
  effort's own Plan/Request sections, which describe the present plan, not a
  history of how it was revised).
  **Even inside that Journal exception, the justification itself must be
  self-contained** — record *why* the finding was correct (the invariant it
  protects, the bug it prevents, the constraint that required it), not merely
  that a review said so. "Review round N flagged X" citing only the review as
  authority, with no independent technical reasoning, creates the same
  circular-reference problem a Wikipedia article has when its only source is
  itself: a review comment is not guaranteed to stay inspectable, and even
  when it is, it was never itself the *reason* — it was only the trigger that
  surfaced a reason that must stand on its own regardless of whether that
  review ever happened.
- Python 3.10+, type hints encouraged
- **Linter: [ruff](https://docs.astral.sh/ruff/).** Each plugin configures its
  own `[tool.ruff]` in `pyproject.toml`. Run the full pass with `ruff check .`
  (and `ruff format` for formatting). The repo carries pre-existing style debt,
  so the committed `pre-commit` hook lints only **staged** files and only the
  high-signal `F` (pyflakes) + `E9` (syntax) rule groups — fix those as you go.
- Docstrings for public functions
- **Componentization: a 1,000-line hard cap per source module**
  (`tools/check-module-size.py`). A single module growing without bound is a
  real failure mode this repo hit in practice (`agent-dispatch`'s `queue.py`
  reached ~7,200 lines with no guard catching it) — a 1,000-line file is
  already a lot to hold in your head at once; split by responsibility (an
  adapter, an evaluator, a policy table) well before that, not after. Dozens
  of pre-existing files exceed the cap by a wide margin (some by an order of
  magnitude), so a **shrink-only baseline**
  (`tools/module-size-baseline.json`) grandfathers each one in at its current
  size as a temporary ceiling — the guard still fails if a baselined file
  grows even one line further, or if any non-baselined file newly crosses the
  cap. Shrinking a file is always fine and never itself a failure. Widening a
  baselined ceiling in the ordinary case is a **manual, reviewed edit** to
  the JSON, never something a plain refresh does silently — bare
  `--refresh-baseline` only lowers or removes entries, it never raises one.
  The one exception is the opt-in `--refresh-baseline --allow-widen` flag,
  restricted by convention to a scheduled/post-merge run against `main`
  (`.github/workflows/module-size-baseline-widen.yml`), which additionally
  ratchets a grown file's ceiling up to its current size and opens its own
  small, reviewable PR — never something a PR branch's own CI run applies to
  its own diff. A separate `--changed-since REF` flag scopes the ordinary
  (non-widening) check to files this branch's own commits actually touch
  (used by CI on `pull_request` events) — this repo's high concurrent-PR
  volume otherwise let one already-merged PR's growth in a shared,
  already-baselined module fail every *other* PR's guard until the widen job
  caught up, even ones that never opened that file; `--changed-since` fixes
  the attribution, not the underlying growth. When the diff itself touches
  `tools/module-size-baseline.json`, scope also includes every baseline
  entry the diff itself added, changed, or removed — a baseline edit could
  otherwise mismatch a file's actual size for an entry a diff's own file
  list wouldn't name, so that entry is always checked. This still never
  falls back to a fully unscoped, whole-tree sweep: a file whose own source
  *and* baseline entry the diff never touches is unrelated organic drift,
  not this PR's responsibility to fix. Test files (`tests/`, `test_*.py`,
  `conftest.py`) are exempt — `TESTING.md` already directs splitting those by
  behavioral contract, not arbitrary line count, a different rule for a
  different failure mode.
  - **The cap is a backstop, not a target.** Treat "a couple of related
    classes/functions per module" as the working ceiling in normal
    development, and split proactively as a module grows toward it — waiting
    for `check-module-size.py` to fail is already too late; by then the
    module has usually accreted several unrelated responsibilities that are
    now entangled and harder to separate than if each had landed in its own
    file from the start.
  - **CLI/registration surfaces are a named recurring shape, not a special
    case.** A large `__main__.py` (or any command/route/handler registry) is
    almost always several independent subcommands sharing one dispatch table,
    not one cohesive module. Split it into one module per subcommand (or
    cohesive subcommand family) plus a thin registrar that only imports and
    wires them — `agent-dispatch`'s extraction of `producers_cli.py`,
    `recipes_cli.py`, and `supervise_cli.py` out of its `__main__.py` is the
    model to follow for any other CLI that's grown the same way.
  - **This is a language-agnostic discipline**, not a Python-only rule. The
    same "one cohesive responsibility, split proactively, no giant CLI
    registration blob" standard applies to `.sh`, `.ps1`, and `.ts` sources
    even though `tools/check-module-size.py` currently only scans tracked
    `*.py` files — extending the guard to other extensions is tracked
    separately (see the `module-componentization-discipline` effort); do not
    treat the tool's current Python-only scope as license to let a large
    shell/PowerShell/TypeScript file grow unchecked in the meantime.
  - **How to actually do a split safely:** see the
    `customizing-copilot:componentizing-modules` skill (a runbook for
    identifying seams, extracting them, and re-validating — including the
    `--refresh-baseline` step once a baselined file shrinks below its prior
    ceiling). Use `python tools/rank-module-size.py` to find which
    already-grandfathered files are the biggest offenders (it folds identical
    vendored copies — e.g. the `installation-context`/`versioned-runtime`
    sync targets — into one row so the ranking reflects distinct real work,
    not duplicated line counts).
  - **A scheduled watchdog surfaces organic drift proactively**
    (`.github/workflows/module-health-watchdog.yml`,
    `tools/module-health-watchdog.py`, daily): no single PR is ever blamed
    for a module that grew past its cap/ceiling one small, individually
    reasonable contribution at a time — the watchdog finds the single worst
    offender (already-over-cap files always outrank merely-near-cap ones)
    and files (or leaves alone, if one is already open) a
    `needs-decomposition`-labeled tracking issue naming it, for a dedicated
    decomposition pass rather than diffuse pressure on whichever future PR
    happens to touch the file next.
- **Check-in size: a tight default cap, a generous image cap, no source
  maps/raw diffs ever** (`tools/check-large-files.py`). `main`'s history had
  accumulated ~300MB across 160+ oversized `.github/coverage-baselines/`
  blobs (up to ~14MB each) before that design moved to GitHub Release
  assets (see PRs #5078/#5085/#5097/#5098 and the `main-history-rewrite`
  effort, which purged the backlog) — this guard exists so a generated data
  dump, a bundled build output, or another large artifact can't quietly
  recur the same way. A full sweep of the tracked corpus at the time this
  guard was added found the largest legitimate non-image file at ~440KB and
  the largest legitimate image (a demo GIF) at ~1.7MB; both caps below sit
  comfortably above those with real headroom, while still catching a
  runaway blob outright:
  - **Images** (`.png`, `.jpg`/`.jpeg`, `.gif`, `.svg`, `.webp`, `.ico`,
    `.bmp`, `.avif`) get a generous **3MB** cap — a screenshot, a design
    preview, or a demo GIF is expected to be checked in.
  - **Everything else** gets a **1MB** cap — comfortably above any
    currently-tracked legitimate file, but well below the old
    coverage-baseline blobs this guard exists to prevent recurring.
  - **`.map`, `.diff`, and `.patch` files are never checked in, regardless
    of size** — a source map and a raw diff/patch are generated-or-derived
    build/workflow byproducts, not source a reviewer should see in a PR.
  - Like `check-module-size.py`, this only checks files a diff actually
    adds or modifies (staged files for pre-commit; the push/PR range for
    pre-push/CI) — a pre-existing large file you didn't touch never blocks
    an unrelated change. `--all` additionally runs an unconditional
    full-tree sweep in CI (`guards-full-sweep` on `dev`, and on any non-PR
    `ci.yml` trigger), so organic drift on trunk is still caught outside any
    single diff.

### Git Hooks

The repo ships git hooks under `tools/hooks/`:

- **`pre-commit`** — on staged files: `ruff check --select F,E9` on Python
  (unused imports/vars, undefined names, syntax errors),
  `tools/check-skills.py` on any staged `SKILL.md` (frontmatter validity, `name`
  rules, and the **1024-char `description` limit** the Copilot CLI enforces —
  over it, the loader silently drops the skill),
  `tools/check-effort-vision-structure.py` on any staged effort/vision
  `README.md`, and `tools/check-large-files.py` on every staged file (see
  below).
- **`pre-push`** — runs the repo-wide guards: `tools/check-install-contract.py`
  (the [install contract](docs/install-contract.md)),
  `tools/check-no-internal-identifiers.py`, `tools/check-vendored-libs-sync.py`,
  `tools/check-headless-launch.py`, `tools/check-picker-inbox-discipline.py`
  (the Picker's Textual UI may only marshal a background producer's result
  back to the render thread through `Inbox.post()` — never a raw
  `app.call_from_thread(...)`), `tools/check-skills.py`,
  `tools/check-docs-consistency.py`, `tools/check-runbook-references.py`,
  `tools/check-version-consistency.py` (every plugin's version identical across
  `plugin.json` / `pyproject.toml` / its `marketplace.json` entry — a one-file
  bump wedges the Picker's update indicator), `tools/check-feed-neutrality.py`
  (no config/Dockerfile/install-script/CI-workflow file may hardcode a public
  package-feed URL as the only usable endpoint — this repo runs on machines
  whose default feed is network-blocked and replaced with an internal mirror),
  `tools/check-module-size.py` (the 1,000-line-per-module cap and
  shrink-only baseline described above), and `tools/check-large-files.py`
  (the oversized-file cap described below).

CI also runs `tools/check-marketplace-isolation.py` in report-only mode. It
inventories legacy unqualified runtime roots, generic global plugin commands,
PATH-based sibling launches, fixed lifecycle identities, and operative bare
commands while the marketplace-installation-cell migration is active. Do not
enable `--strict` until the producing phases in #1096 have removed the baseline.

### Test Portfolio Discipline

Treat required pull-request CI as a fast, change-scoped contract gate. Do not
add exhaustive cross-products or repeated subprocess setup directly to that
lane. Subprocess-heavy suites must provide a focused smoke lane selected by
markers and path gating, with the complete portfolio retained in a scheduled or
manually dispatched workflow. Prefer broad canonical implementation coverage
plus representative adapter checks at real divergence seams; never pool the
process boundaries that a concurrency or lifecycle test exists to verify.

`TESTING.md` is the canonical source for the portfolio invariants, runner
mechanics, and the current smoke/exhaustive split.

### Windows Background-Launch Review

Any change that adds or modifies a background subprocess, scheduled launcher,
health probe, transport, or daemon must classify the launch using
[`windows-background-process-launch`](docs/patterns/windows-background-process-launch.md)
and reuse its shared primitive. `CREATE_NEW_CONSOLE` plus `SW_HIDE` is not a
headless mechanism: Windows Default Terminal may still display and focus it.
The user's configured default terminal is never a correctness dependency.

Review requires evidence at the real divergence seam, not only a mocked
`creationflags` assertion:

1. Start the path from a windowless parent such as `pythonw.exe` or the actual
   service launcher.
2. Exercise at least one real console-subsystem descendant; for SSH, include the
   configured `ProxyCommand` path when present.
3. Observe at least two periodic cycles and assert zero visible top-level
   windows, zero Default Terminal/`OpenConsole` acquisitions, and zero foreground
   transitions.
4. Exercise timeout/cancellation and confirm the complete child tree is reaped.
5. Verify local targets stay local so a same-machine health check cannot create
   avoidable SSH/process churn.

Keep this live Windows check focused; the required CI guard remains static and
fast.

### "POSIX" Is Not "Linux" — Name macOS Explicitly

A process-census or liveness primitive written against `/proc` or Linux
`pidfd` APIs is **Linux-specific**, not general POSIX support — macOS is
POSIX but has neither. If a change claims cross-platform daemon/process
coverage, name **Windows, Linux, and macOS** explicitly and state what each
one does: implemented, or an explicit and justified exemption (e.g. "no macOS
runners in this suite yet; falls back to X"). Do not let "POSIX" silently
stand in for "tested on Linux only."

### Ephemeral Process Reaping

Launching a background/detached process invisibly (the section above) is only
half the contract. Any change that adds or modifies a detached process, a
per-worktree/per-task helper, or anything else that must outlive its parent
invocation must also state **how it gets reaped** — classify it against
[`ephemeral-process-reaping`](docs/patterns/ephemeral-process-reaping.md) and
answer, in the PR description or a code comment at the reap site:

1. What is the real liveness signal for the unit this process serves (a
   worktree's mux + PID liveness, a service's lease file, ...) — not "a hook
   fired"?
2. Where is the polling-based reap that requires no cooperation from the
   dying process — a hook-only reap is a fast path, never the only path.
   Reuse an existing bounded sweep (e.g. `session_catalog.py`'s resident
   reconciler) rather than adding a new poller.
3. Is the reap idempotent and silent on an already-dead target?
4. Confirm it is **not** implemented inside a preservation-oriented lifecycle
   command (`finalize`/`cleanup`) — those must not also own process teardown.

A detached process with a described launch path but no described reap path is
an incomplete change, not a follow-up: #2265 and #2269/#2270 are what an
"it'll get cleaned up somehow" assumption costs in practice (a machine-wide
process/window leak discovered only once it made a laptop's fans and keyboard
noticeably hot).

**A second shape gets missed by the four questions above because it isn't
detached at all.** A process spawned per-invocation (per `task()` delegation,
per request, per session) whose real OS parent is a **longer-lived host
process** (a persistent top-level session, a resident daemon) can leak for
the opposite reason: stdin-EOF and parent-death correctly answer "is my
*physical* parent still alive?" — but never "has the *logical* operation I
exist to serve concluded?", when that operation is a bounded scope nested
*inside* the still-live parent. `agent-mcp`'s stdio `Bridge.run()` idle
self-reap (#3876) is the exemplar: a sub-agent
delegation finishing does not close the bridge's stdin or kill the top-level
session that holds it open, so neither existing signal ever fires. Any
change spawning a per-invocation child under a longer-lived host process
must additionally answer:

5. Is the process's true termination boundary a **logical** scope (one
   delegation, one request, one task) nested inside a physical parent that
   outlives it? If so, a poll-based reaper (question 2) has nothing external
   to observe — add an **idle-timeout self-check the process runs on
   itself**, dual-gated on elapsed inactivity *and* an authoritative
   in-flight-work signal (never idle-timeout alone; a slow in-flight call
   must never be reaped mid-flight) — see the pattern doc's *Variant*
   section and `agent_mcp.session.BridgeSession.has_pending` /
   `agent_mcp.bridge.Bridge.run`'s idle branch for a worked example.

### PID-Identity-Bound Termination Needs a Direct Test

Any code path that terminates or reaps a process by PID — a stale-daemon
reaper, a cutover repair action, a self-heal/`doctor` apply mode — must ship a
dedicated unit test in the **same PR** covering two separate safety layers:
(a) **identity-bound termination** — `zdd.diagnostics.process_start_time` /
`zdd.diagnostics.terminate_pid_if_identity` bind the actual signal to a
PID/start-time token and refuse on any mismatch (a stale or reused PID) — but
this pair alone does **not** validate ownership; and (b) **owner
validation** — confirming the candidate is the legitimate target, not merely
some other live process, which is the responsibility of the higher-level
`zdd.diagnostics.audit_daemon_health`/`apply_daemon_health` path. Reuse these
shared primitives rather than re-deriving a parallel mechanism — a
plugin-private equivalent (e.g. `agent_worktrees.locks`/`agent_worktrees.procs`)
is not importable from another plugin and should not be cited as *the* thing
to reuse. An end-to-end rehearsal test that happens to exercise the happy
path is **not** sufficient evidence of this on its own — both safety layers
need a direct test.

They are **not active until wired** per clone (git does not auto-enable a
committed hooks dir). Run the helper once per checkout:

```bash
tools/setup-hooks.sh          # macOS / Linux / Git Bash / WSL
tools\setup-hooks.ps1         # Windows PowerShell
# equivalent to: git config core.hooksPath tools/hooks
```

Bypass in a pinch with `git commit/push --no-verify` (discouraged). The
install-contract check fails until every runtime plugin's installer conforms —
see the contract doc for the rules.

## Gotchas

### The mux status bar must never compute on the render path

**Rule: nothing in a tmux/psmux `status-left` / `status-right` may spawn a
process per render.** No `#(agent-worktrees …)`, no `#(cat …)`, no `#()` that
shells out. The bar may read only precomputed values — the `#{@aw_ctx}` /
`#{@aw_seg}` user options plus `%H:%M`-style strftime. A detached
`status-updater` watcher computes the segments **off** the render path and
pushes them in via `set-option`.

**Why (the regression this exists to prevent).** tmux runs `#()` jobs
asynchronously and caches them between `status-interval` ticks, so it *mostly*
hides the cost. **psmux repaints synchronously** — it re-runs every `#()` in the
status line on each repaint, in the render/keystroke path. A bar that shelled
out to the (Python, cold-starting) `agent-worktrees` CLI cost ~600 ms per
repaint there; under Copilot's high-framerate TUI that turned keystroke echo and
re-render to molasses on Windows (worse under the double-ConPTY stack), while a
no-mux session stayed snappy. The fix moved the compute into one common
`status-updater` watcher feeding `#{@aw_*}` vars. See the *Off the paint path*
section of
[`plugins/agent-worktrees/docs/cli-reference.md`](plugins/agent-worktrees/docs/cli-reference.md).

**If you touch `terminal/psmux.conf`, `terminal/session-options.sh`, or a
launcher status path:** keep the bar on `#{@aw_*}` vars; keep the compute in the
shared cross-platform `status-updater` watcher (do **not** re-introduce a
per-mux shell writer or a render-path `#()`); the guard tests in
`plugins/agent-worktrees/tests/test_terminal_decoupling.py` (assert no
`#(agent-worktrees` / `#(cat` in the bar) will fail if you regress. Verified
mechanisms: psmux 3.3.6 and tmux 3.4 both support session-scoped `set-option -t`
(isolated per session) and `#{@user-option}` expansion.

### Hot-patching a deployed venv for fast pre-merge iteration

**A cached `.test-venvs/<platform>/<plugin>` venv installs a vendored
path-dependency lib (e.g. `agent-ssh-manager`) as a normal, non-editable
copy** — not an editable link. Editing `plugins/<p>/libs/<lib>/src/...` does
**not** change what that venv imports until you rebuild it. Two ways to see a
fresh edit without a full rebuild:

- **Fast unit-test iteration:** prepend the vendored copy's `src/` to
  `PYTHONPATH` so it shadows the stale installed copy, e.g. (PowerShell):
  `$env:PYTHONPATH = "plugins\<p>\libs\<lib>\src"` before invoking the venv's
  `python.exe -m pytest`. This is also how to run a shared lib's **own** test
  suite (`libs/<lib>/tests/`) against one specific vendored copy — that suite
  is not part of any plugin's `tests/` dir, so `tools/run-plugin-tests.py`
  never runs it; point `PYTHONPATH` at the copy you want to validate and
  invoke pytest against the shared lib's own `tests/` directory directly.
- **`--reinstall`:** `python tools/run-plugin-tests.py <plugin> --reinstall`
  forces a real rebuild from the current vendored source, for a true
  integration-level check of that plugin's own suite.

**Validating against the actual deployed CLI a human/agent would invoke** (not
just the test venv) needs one more step beyond the *local installer* gotcha
above (see *`<repo> update` does not validate an unmerged worktree's
changes*): even a `source.kind = local` install rebuilds from a **checkout on
disk**, so it still requires committing (or at least saving) your edit and
re-running that plugin's installer to pick it up.

**`agent-codespaces` has adopted the mutable-dev-slot pattern
(`docs/patterns/mutable-dev-slot.md`, #3376) as the preferred path** for this:
from your worktree, run `pwsh -File plugins\agent-codespaces\scripts\install.ps1
dev` (`./scripts/install.sh dev` on POSIX). This claims a protected, mutable
`versions/dev` slot, builds it as an **editable** install against your
checkout, and activates it -- a plain source edit is then reflected by the
deployed `agent-codespaces` CLI immediately, no rebuild needed; re-run `dev`
only when you change a dependency. Release when done with the DEPLOYED CLI's
own verb: `agent-codespaces dev-release` (works even without a checkout
present -- see the design doc's "Runtime accessibility" section), which
restores the machine to whatever version was active before you claimed dev
mode. `agent-worktrees finalize` warns (never silently releases) if your
worktree still holds a live dev-slot claim when you try to retire it.

For any plugin that has **not yet** adopted this pattern, or when you need the
fastest possible iteration loop on a live bug **before** even a dev-slot claim
is worth setting up, it remains acceptable to hot-patch the **already-deployed**
runtime's files directly (e.g. on Windows,
`~/.agent-codespaces/versions/<version>/Lib/site-packages/ssh_manager/*.py`) —
but treat this as strictly throwaway: it is silently overwritten by the next
real install/update, must never be treated as "shipped," and the actual fix
still needs to land through the normal commit → PR → merge → deploy flow
before you consider it done. Re-verify against a real (non-hot-patched)
deploy once your PR lands.

### Windows `ProxyCommand` bridge: a peer-gone-away read is not a failure

**If you touch `libs/ssh-manager` (any vendored copy)'s `proxy.py` or
`process.py`:** two Windows-only behaviors are load-bearing, not incidental,
and a "obvious" cleanup can silently reintroduce either:

- A read from a bridged loopback socket/pipe (`_pump()`) can raise
  `ConnectionResetError` (`WinError 64`, "the specified network name is no
  longer available") when the peer closes, instead of the POSIX-style empty
  read at EOF. This is a normal Windows ProactorEventLoop signal for "the
  other side is gone," not an error condition — treat it as clean end-of-stream,
  not something to log as a failure or propagate.
- The ambient background watcher that reaps a per-command proxy's spawned
  process (`run_process_cleanup`'s caller in `_watch_process`) must bound how
  long **it** waits for that cleanup (`timeout=` a modest ceiling, e.g. 10s),
  even though the underlying cleanup itself stays `shield()`-protected and
  keeps running to completion in the background. Without that bound, a slow
  remote process-tree kill (`taskkill /T /F`, observed 100+ seconds under
  endpoint-protection scanning) blocks the **entire hosting CLI process's
  exit** — `asyncio.run()`'s own shutdown sequence waits for every
  outstanding task, including a shielded one, so an unbounded wait here isn't
  contained to one code path; it stalls the whole invocation even after the
  real command's result was already returned to the caller.

Both were root-caused live diagnosing `agent-codespaces ssh` reliability
(ThomasMichon/copilot-extensions#3323, fixed in #3340) — differential
diagnosis (bare `gh codespace ssh` vs the wrapped command, `--no-relay` to
rule out the credential-relay prelude, replaying the exact `ssh` invocation
by hand) is what isolated these two behaviors from red herrings (a
misdiagnosed "ADO feed-token export hang", a misdiagnosed "network/VPN
outage" that a bare `gh` call disproved). Re-run that diagnosis shape —
strip layers one at a time against a known-good baseline — before assuming a
new hang/slowdown in this path is a repeat of either of these two fixed
causes.

## Commit Messages

- Descriptive, imperative mood: "Fix Unicode crash on cp1252 consoles"
- Reference this repo's GitHub issue numbers where applicable: "Fix #372: …"
- Include `Co-authored-by` trailer for Copilot-assisted commits
