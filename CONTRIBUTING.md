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
>    The promotion pipeline only fires on a green `dev` CI run
>    (`workflow_run` → validate → promote, all one workflow); if the run your
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
>    the release. A small, separate **"clear consumed changefiles on dev"**
>    housekeeping PR normally follows each successful promotion a few minutes
>    later — that one is routine cleanup, not something you need to review or
>    act on, but don't be surprised to see it land right after yours.
>
> See "The wait, and how to preview past it" below for the full mechanics and
> how to preview a pending release without waiting for a real promotion.

**Every change lands through a pull request — direct pushes to `dev` are
blocked, and `main` accepts pushes only from the promotion pipeline (or
explicit admin escalation).** This is enforced on four layers that agree:

1. **Tooling** — `.agent-worktrees/config.yaml` sets `pr.required: true`, so
   `agent-worktrees push-changes` refuses direct-to-`dev` and the PR-workflow
   git-hooks block committing to `dev` / pushing a worktree branch directly.
2. **Branch policy** — a GitHub repository ruleset ("dev branch policy:
   PR-required") carries a `pull_request` rule (+ `non_fast_forward`) that
   blocks direct pushes to `dev` server-side, for everyone (no bypass). A
   separate branch-protection rule on `main` restricts pushes to the
   promotion pipeline's own identity, with repo-admin escalation retained for
   genuine emergencies (see Release & Versioning below).
3. **Review** — `.github/workflows/copilot-review-gate.yml` requests a
   Copilot review automatically, but **only** for a PR authored by a
   **Maintainer** (the CODEOWNERS root roster, owner included) — not merely
   any invited collaborator. A Contributor's PR gets no automatic request
   (a Maintainer can still request one manually via the "Reviewers"
   sidebar), and an uninvited outsider's PR gets no automatic review at all.
   This scoping exists because GitHub's `requestReviewers` call for
   `copilot-pull-request-reviewer[bot]` silently no-ops — no exception, no
   timeline event — when made by the default `GITHUB_TOKEN`
   (`github-actions[bot]`) for a PR whose author isn't this repo's owner (a
   personal, non-org account has no equivalent of the org-only "members
   without a Copilot license" carve-out). `MAINTAINER_REVIEW_PAT`, a
   fine-grained PAT for the owner scoped to only this repo (`Pull requests:
   write` + `Metadata: read`), makes the request instead, as a licensed
   account rather than the bot — used only for Maintainer-authored PRs,
   since Maintainers already hold ruleset self-merge bypass (this extends
   no new trust), and a Contributor's PR still needs a Maintainer's own
   approving review regardless of Copilot's verdict. (The ruleset-native
   `copilot_code_review` auto-review rule has no such condition — it would
   fire for literally anyone — so this repo has removed that rule; see
   "Everyone else" below for why that matters.) Like
   `workflow-lockdown-guard.yml` above, this workflow runs on
   `pull_request_target`, so it can't fire until its own file has reached
   `main` via a promotion — request a review manually for any PR opened
   before that first promotion completes.
   Whether a review (Copilot's or anyone else's) must formally *approve* the
   PR before merge is governed by a second ruleset ("dev branch policy:
   review required (maintainer bypass)") plus `.github/CODEOWNERS`
   (root-scoped to the full **Maintainer** group — currently `@ThomasMichon
   @JakeSchieber @anarmawala @namankanakiya`; CODEOWNERS review satisfaction
   is OR across listed owners, so any *one* Maintainer's approval counts, not
   all of them):
   - A PR authored by anyone **other than** a Maintainer requires **some**
     Maintainer's own approving review before it can merge — Copilot's review
     alone is never sufficient for a Contributor's PR, however clean it comes
     back, so a change never lands without a Maintainer being aware of it.
     This holds even though the repo's **"Allow Copilot to approve pull
     requests"** setting (Settings → Copilot → Code review → Auto-approval)
     is enabled, letting Copilot submit a genuine `Approve` review that
     counts toward `required_approving_review_count`: that count and
     `require_code_owner_review` are independent, both-must-pass gates, and
     Copilot is deliberately **not** listed in CODEOWNERS, so its approval
     alone can never satisfy the codeowner-specific half of the requirement
     for a Contributor's PR.
   - Each Maintainer is a named `User`-actor `bypass_actors` entry on the
     ruleset (`bypass_mode: pull_request` — still requires a real PR and all
     required status checks; only the *review* requirement is exempted),
     which in practice lets them self-merge their own PRs without a second
     approving review. Deliberately, this bypass is a per-`User` ruleset
     entry, **not** a bump to GitHub's `Maintain`/`Admin` repository role —
     a Maintainer here keeps their ordinary `Write` permission (no
     repo-settings, Actions-secret, or collaborator-management access) and
     gains only the self-merge capability.
     > **Read the bypass mechanism precisely: it is bound to the *merging*
     > actor, never to the PR's author.** GitHub ruleset bypass has no "only
     > my own PRs" concept: `bypass_mode: pull_request` means "when this
     > named actor performs the merge, this rule doesn't apply to them," full
     > stop — regardless of whose PR it is. In practice this means any
     > Maintainer *could* merge a Contributor's still-unapproved PR
     > themselves, bypassing the review-count/codeowner requirement meant
     > for that Contributor. This is not new to this change — it was already
     > true for ThomasMichon alone before Maintainers existed — this PR only
     > extends the same structural trust to three more named accounts. There
     > is no GitHub-side technical control for "bypass only when merging your
     > own PR"; the mitigation is the same one that already applied to the
     > sole owner: Maintainers are
     > trusted not to merge past a Contributor's required review, and every
     > bypass is visible in the ruleset insights / audit log after the fact.
   - Required CI status checks (`PR gate`, a fixed-name aggregate — see its
     own definition in `.github/workflows/ci.yml` for why a fixed anchor job
     exists rather than naming dynamic matrix jobs directly) apply to
     everyone with no bypass, including every Maintainer.
4. **Workflow/CODEOWNERS lockdown** — `.github/workflows/`, `.github/actions/`,
   and `.github/CODEOWNERS` itself are locked to **this repo's owner alone**
   (`ThomasMichon` here, derived from the immutable `github.repository_owner`
   context value rather than a repository secret/variable -- a repository
   *variable* is writable via the API/CLI by ordinary Write access, the same
   Maintainer tier this lockdown restricts, which would let a Maintainer
   defeat it by simply re-pointing the value, so a user-owned fork's own
   owner is protected automatically with zero configuration instead --
   this policy is scoped to user-owned repos only; an organization-owned
   fork needs its own authorization mechanism, since no individual PR
   author can ever equal an org login), not
   the wider Maintainer group (workflow changes can exfiltrate secrets/PATs,
   a materially different risk than an ordinary code change). A Maintainer's
   review-bypass above does *not* cover this: a required status check
   (`workflow-lockdown-guard`, from `.github/workflows/workflow-lockdown-guard.yml`,
   run via `pull_request_target` so a PR can't neuter its own trusted
   definition) in its own ruleset ("dev branch policy: workflow/CODEOWNERS
   lockdown") fails whenever a protected path is touched unless the **PR's
   registered author** (`pull_request.user.login`) matches
   `github.repository_owner` (`ThomasMichon` on this repo). This
   deliberately checks the PR's submitter, not individual commit metadata:
   per-commit `author`/`committer` login is just GitHub's resolution of the
   commit's plain-text git identity (name + email) against an account, not
   a cryptographic proof — any contributor could set
   `git commit --author="ThomasMichon <NNN+ThomasMichon@users.noreply.github.com>"`
   locally and pass it with zero real involvement, so validating commit
   metadata would buy nothing but complexity. A PR's `user.login`, by
   contrast, is an authenticated fact GitHub sets once at PR-creation time
   (you cannot open a PR as another account) — there's no equivalent way to
   forge it, and unlike an event's `sender` (whoever triggered *that*
   webhook delivery), it doesn't change on close/reopen, so it isn't
   vulnerable to a "fail once, then close+reopen to launder a pass" game.
   **Known, accepted residual risk:** this checks who *opened* the PR, not
   who pushed every commit in it — an already-invited Write collaborator
   with push access to the same repo could still push a follow-up commit
   directly onto ThomasMichon's own already-open PR branch, and this check
   would still pass. Closing that fully would need commit-signature
   verification, which this repo has decided against setting up (too much
   operational hassle for the residual risk — see
   ThomasMichon/copilot-extensions#4519, declined). This is a narrower,
   more unusual threat (an already-trusted collaborator actively pushing an
   unwanted commit onto someone else's PR) than an arbitrary outsider or a
   Contributor's own PR, both of which this check fully closes.
   **Known, accepted structural limitation:** GitHub's
   `required_status_checks` rule matches purely by context name
   (`workflow-lockdown-guard`), not by which workflow file produced it. A
   Write Maintainer could modify an existing, untrusted-`pull_request`
   workflow (e.g. `ci.yml`) within their own PR to add a trivially
   succeeding job of the same name — GitHub does not distinguish that forged
   check from this one by app identity (both run as ordinary GitHub
   Actions). Closing this fully needs a check reported by a distinct,
   separately trusted GitHub App pinned in the ruleset by `integration_id`
   — real additional infrastructure this repo hasn't built. Until it does,
   treat this lockdown as a strong deterrent against an ordinary
   Contributor's PR or an unsophisticated mistake, not a cryptographically
   hard guarantee against a Write Maintainer deliberately trying to defeat
   it — the same category of trust already accepted in the actor-vs-author
   bypass note above.
   This ruleset has **no bypass actors at all** — not even ThomasMichon —
   because the check's own pass condition already grants exactly the
   intended exemption; a bypass actor here would let the exemption apply to
   *whichever PR ThomasMichon merges*, not only PRs he authored, which is
   the same actor-vs-author gap described above and unnecessary to accept
   for this specific lockdown. The practical effect: ThomasMichon can merge
   his own workflow-touching PRs freely; adopting anyone else's such PR
   requires re-authoring/re-pushing it under his own account first — a
   deliberate friction, not an oversight. (This is a required-status-check
   workaround, not GitHub's native `file_path_restriction` ruleset rule: that
   rule type returns `Validation Failed` on this personal, non-Enterprise
   account — it's an Enterprise-only feature.)
   > **Rollout note:** `workflow-lockdown-guard.yml` runs on
   > `pull_request_target`, which always executes the workflow definition
   > from the repository's ACTUAL default branch — **`main`**, per GitHub's
   > own repo settings, not `dev` (this repo's separate "contribution
   > default" convention). That means the check structurally cannot report
   > at all until `main` has its own copy, which only happens after this
   > repo's own promotion pipeline next promotes `dev` to `main` (routinely
   > ~10-20 minutes after a `dev` merge — see "The wait, and how to preview
   > past it" below). The enforcing ruleset is created but left `disabled`
   > until after that promotion completes and a subsequent PR confirms the
   > check actually reports `workflow-lockdown-guard` successfully — only
   > then is it flipped to `active`. Until that flip, this specific
   > lockdown is docs-and-workflow-only, not yet server-enforced.

**Everyone else — anyone who hasn't been invited as a collaborator at all —
gets no automatic CI, no automatic Copilot review, and no agentic-workflow
support**, only the strictest built-in fork-PR-approval gate (Settings →
Actions → General → "Fork pull request workflows" → **"Require approval for
all outside collaborators"**), which already blocks every Actions run
(including CI) from starting until a Maintainer manually approves it, plus
`copilot-review-gate.yml`'s own collaborator check (above) for review. A
Maintainer can still manually approve a run or request a review for an
outside PR at their discretion — this only removes the automatic path for
someone the repo owner never invited.

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
> merging** (see "Review" earlier in this section); only the repo owner's
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
> `MAINTAINER_REVIEW_PAT` (see "Review" above), the automatic request never
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
   "Review" above; Copilot's own `Approve` never substitutes for that).
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
   "Review" earlier in this section) — some Maintainer still must actually
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
  (see "Review" above), not to Copilot's own verdict — for the maintainer's
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

## Release & Versioning

### Marketplace architecture

This repo is a **Copilot CLI plugin marketplace** — a GitHub-hosted
registry of plugins that machines install via `copilot plugin marketplace
add ThomasMichon/copilot-extensions`. The marketplace catalog lives at
`.github/plugin/marketplace.json` and lists every plugin with its current
version. The Copilot CLI reads this file to determine available updates.

> **Deploy with `<repo> update` — never hand-run `copilot plugin update`.**
> `copilot plugin update` on its own refreshes only a plugin's *payload* (cached
> source + skills) — it does **not** rebuild a runtime (venv/binstubs/service),
> and if the version wasn't bumped it silently no-ops ("already at latest"). Do
> not chase that gap with per-plugin installers by hand; use the one unified
> flow: **`<repo> update`** (`agent-worktrees update`, or any repo binstub such
> as `dotfiles update`). It refreshes **every** registered plugin's payload
> (invoking the plugin manager for you), rebuilds **every** runtime
> (agent-worktrees, agent-bridge, agent-codespaces, …), and fast-forwards the
> anchor checkouts — in a single command, per machine. The per-plugin
> `scripts/install.*` / `scripts/init.*` documented below are the internals it
> runs for you (and a local-testing / recovery path), **not** the normal deploy
> path. See
> [docs/install-contract.md → Plugin update ≠ runtime install](docs/install-contract.md#plugin-update--runtime-install).

### Version scheme

Agent Worktrees follows [PEP 440](https://peps.python.org/pep-0440/)
compatible versioning:

```
MAJOR.MINOR.PATCH[-devN]
```

- **Patch** bumps (`1.0.1 -> 1.0.2`) — bug fixes, small improvements,
  new skills/docs that don't change runtime behavior.
  > Only a change **inside a plugin folder** (its `src/`, `skills/`, or its own
  > `docs/`) ships in that plugin's payload and needs a bump. A **repo-root**
  > `docs/` change (this repo's `docs/`, `CONTRIBUTING.md`, `README.md`) is not
  > vendored into any plugin and needs **no** bump — see
  > [install-contract.md § What the marketplace vendors](docs/install-contract.md#what-the-marketplace-vendors-copied-vs-loaded).
- **Minor** bumps (`1.0.x -> 1.1.0`) — new features, behavioral changes,
  new CLI subcommands. **Only when the maintainer decides.**
- **Major** bumps (`1.x -> 2.0`) — breaking changes. **Only when the
  maintainer decides.**

### Contributing a change: add a changefile, don't hand-pick a version

**You never hand-edit a version number.** Instead, once per touched plugin,
run:

```bash
python tools/changefile.py add --plugin <name> --type patch --comment "<summary>"
# one PR touching two plugins with one shared reason:
python tools/changefile.py add \
  --plugin agent-worktrees --type patch \
  --plugin agent-bridge --type dev \
  --comment "Shared fix for Y"
python tools/changefile.py list   # see what's pending
```

Default to **`patch`** (or `dev` for an iterative fixup within an
already-in-flight patch). Do **not** request `minor`/`major` unless the
maintainer explicitly says so. Multiple changefiles may target the same
plugin (e.g. two different PRs merged close together); whichever carries the
biggest bump type wins when they're all consumed together
(`tools/accumulate_bumps.py`'s `highest_bump`) — you never need to
coordinate with another PR author over the exact number, which is the
structural fix for ThomasMichon/copilot-extensions#182's parallel-PR
`-devN` collisions.

This closes a changefile's "PR at PR-time" side of the story; consuming it
into a real version number is Release & Versioning's next concern, not
yours as a contributor — see "The wait, and how to preview past it" below
for what actually happens between your merge to `dev` and a real version
landing on `main`.

### Where the mechanically-applied bump lands (reference — you never edit these by hand)

Each plugin has its own version triplet. The CI promotion pipeline's
`tools/accumulate_bumps.py` is what actually writes these, consuming
whatever changefiles are pending; nothing here is something a contributor
edits directly.

> **Mechanical shortcut:** `python tools/accumulate_bumps.py --from-diff origin/main --apply`
> bumps exactly what `check-version-bump.py` requires for your branch -- every
> touched plugin (all three files plus literal `__version__` fallbacks), every
> plugin that vendors a changed lib, and the lib itself in all its copies --
> each only when it is not already ahead of `origin/main`, so re-run it after a
> rebase in which `main` consumed your `-devN`. `--dry-run` shows the plan.

> **General rule (applies to every plugin, present and future).** For a plugin
> `<p>`: bump `plugins/<p>/plugin.json` (`version`), `plugins/<p>/pyproject.toml`
> (`[project].version`, runtime plugins only — payload-only plugins have none),
> and `<p>`'s entry in `.github/plugin/marketplace.json` (find it **by name**,
> not a hardcoded index). **agent-worktrees** additionally bumps
> `metadata.version`; **adding a new plugin** appends a `plugins[]` entry and
> bumps `metadata.version`. The per-plugin tables below are concrete examples for
> the original plugins — the same rule covers agent-logger, agent-dispatch,
> context-handoff, efforts, visions, customizing-copilot,
> copilot-extensions-harness, and anything added later.
>
> **Keep any in-package `__version__` in sync.** A runtime plugin that exposes a
> Python `__version__` (e.g. `agent-dispatch`'s `src/agent_dispatch/__init__.py`,
> surfaced by `--version` and the coordinator's `/health`) must bump it to match
> the `pyproject.toml` version in the **same** commit — it is a *fourth* file for
> that plugin, easy to miss because the marketplace doesn't read it. A stale
> `__version__` makes a correctly-deployed runtime misreport its own version.
>
> **Enforced by `tools/check-version-consistency.py`** (pre-push): it fails the
> push if any plugin's `plugin.json` / `pyproject.toml` / `marketplace.json`
> versions disagree — the guard added after #65 bumped only `pyproject.toml` and
> wedged the Picker's "Update available" indicator into a permanent loop.
>
> **Enforced by `tools/check-changefile-presence.py`** (pre-push + CI,
> PR-diff scoped, against `dev`): it fails the push/PR if a plugin's content
> changed **without** a pending changefile naming it. A change to **any file
> under `plugins/<p>/`** (its `src/`, `skills/`, `agents/`, own `docs/`,
> tests, manifests) requires a changefile for `<p>`; a change to a **shared,
> vendored `libs/<lib>/`** requires one for **every** plugin that vendors it
> (a lib change reaches every consumer's payload — see
> `check-vendored-libs-sync.py`). This closes the silent stale-deploy gap
> where new code ships under an unchanged version and the version-gated
> runtime install never redeploys it (dotfiles #1025). Repo-root files not
> vendored into any plugin (`tools/`, `.github/`, repo-root `docs/`,
> `CONTRIBUTING.md`, `README.md`) need no changefile. Build artifacts under a
> plugin are ignored.
>
> **`worktree-manager` follows the same rule, even though it is not a
> marketplace plugin.** It is a top-level, out-of-plugin consumer tree with
> no `plugin.json` at all — its release version lives directly in its own
> `pyproject.toml` (`[project].version`), and its `src/*/__init__.py`
> `__version__` fallback is the "fourth file" equivalent above. A change to
> **any file under `worktree-manager/`**, or to a **shared lib it consumes
> in either form** — a real, vendored `libs/<lib>/` copy, **or** a `uv`-editable
> canonical-reference pointer in its own `pyproject.toml`
> `[tool.uv.sources]` (an escaping `{ path = "../libs/<lib>", editable =
> true }` entry -- no local copy at all; see `tools/uv_editable_ref.py`'s
> own module docstring for the full mechanism, part of the
> vendor-pointer-generalization effort) — requires a changefile naming `worktree-manager` the same
> way a plugin's own content change does (`python tools/changefile.py add
> --plugin worktree-manager --type patch --comment "..."` — the `--plugin`
> flag name is historical; it accepts any recognized consumer identifier).
> It has no `marketplace.json` entry and no instruction-projection
> ownership, so those two surfaces never apply to it.
>
> **Before editing a shared lib, find every REAL copy first: `python
> tools/check-vendored-libs-sync.py --list`.** A shared lib such as
> `ssh-manager` is vendored **per consuming plugin**, at
> `plugins/<plugin>/libs/<lib>/` — each copy is installed and imported
> independently (`[tool.uv.sources] <lib> = { path = "libs/<lib>" }` in that
> plugin's own `pyproject.toml`). Some repos also carry a legacy top-level
> `libs/<lib>/` directory alongside these — it is easy to mistake for "the"
> source since it sits next to the lib's own `tests/`, but for a lib with
> only real copies, `--list` enumerates just those **real, physical**
> consumer-local copies (`plugins/*/libs/*`, plus a registered standalone
> consumer's own top-level `libs/*`, e.g. `worktree-manager/libs/*`) — not
> the complete consumer map. **A top-level canonical `libs/<lib>/` is not
> automatically inert just because `--list` doesn't name it as a copy**:
> for a lib with any `uv`-editable pointer-only consumer (vendor-pointer-
> generalization effort, e.g. `worktree-manager`'s `plugin-resolve`), that
> canonical tree IS the real source materialized into those consumers at
> promotion time (`tools/materialize_main.py`) — editing it changes their
> real, shipped payload. When such a lib ALSO carries one or more real
> copies, `check-vendored-libs-sync.py` cross-checks canonical against
> those real copies the same way it would a `VENDOR_POINTER.json` copy, and
> `--list` names the editable-pointer consumers alongside the real ones
> (`plugin-activation` is the live example: real copies in
> `agent-worktrees`/`customizing-copilot`, editable-pointer consumers
> everywhere else). The one case that genuinely has no copy to cross-check
> is a lib with editable-pointer consumers and **zero** real copies at
> all -- canonical is their only payload, so neither `--list` nor
> `verify()` has a second copy to compare it against. Find pointer-only
> consumers with `python tools/check-version-bump.py --list` (their entry
> names appear even without a local copy) or by grepping every
> `pyproject.toml`'s `[tool.uv.sources]` for an escaping `path`. For a lib
> with real copies, edit every listed real copy identically (or edit one
> and copy it to the rest byte-for-byte) plus canonical if a pointer form
> is mixed in, then re-run `check-vendored-libs-sync.py` to confirm.

**agent-worktrees:**

| File | Field | Purpose |
|------|-------|---------|
| `plugins/agent-worktrees/plugin.json` | `version` | Copilot CLI reads this to detect updates via `copilot plugin update` |
| `plugins/agent-worktrees/pyproject.toml` | `version` under `[project]` | Python package version at runtime; shown in `--version` output |
| `.github/plugin/marketplace.json` | `metadata.version` AND `plugins[0].version` | Marketplace catalog; Copilot CLI reads this from GitHub to check for updates |

**agent-bridge:**

| File | Field | Purpose |
|------|-------|---------|
| `plugins/agent-bridge/plugin.json` | `version` | Plugin version for marketplace detection |
| `plugins/agent-bridge/pyproject.toml` | `version` under `[project]` | Python package version; shown in `agent-bridge version` output |
| `.github/plugin/marketplace.json` | `plugins[1].version` | Marketplace catalog entry for agent-bridge |

**agent-codespaces:**

| File | Field | Purpose |
|------|-------|---------|
| `plugins/agent-codespaces/plugin.json` | `version` | Plugin version for marketplace detection |
| `plugins/agent-codespaces/pyproject.toml` | `version` under `[project]` | Python package version; shown in `agent-codespaces version` output |
| `.github/plugin/marketplace.json` | `plugins[2].version` | Marketplace catalog entry for agent-codespaces |

**agent-containers:**

| File | Field | Purpose |
|------|-------|---------|
| `plugins/agent-containers/plugin.json` | `version` | Plugin version for marketplace detection |
| `plugins/agent-containers/pyproject.toml` | `version` under `[project]` | Python package version; shown in `agent-containers version` output |
| `.github/plugin/marketplace.json` | `plugins[3].version` | Marketplace catalog entry for agent-containers |

**agent-mcp:**

| File | Field | Purpose |
|------|-------|---------|
| `plugins/agent-mcp/plugin.json` | `version` | Plugin version for marketplace detection |
| `plugins/agent-mcp/pyproject.toml` | `version` under `[project]` | Python package version; shown in `agent-mcp status` output |
| `.github/plugin/marketplace.json` | `plugins[4].version` | Marketplace catalog entry for agent-mcp |

**All version files for a plugin must be bumped together in the same commit.** If any
file is out of sync:

- Stale `plugin.json` — `copilot plugin update` reports "already at
  latest" even when new code is available.
- Stale `marketplace.json` — the marketplace registry shows the old
  version; machines checking for updates won't see the new version.
- Stale `pyproject.toml` — runtime `--version` output is wrong.

### When to add a changefile

- After a set of changes is committed and ready to push — one changefile per
  PR is fine; don't add one on every commit.
- **A hot plugin with concurrent agents is no longer a coordination
  problem.** Under the old hand-bump scheme, several agents landing PRs to
  the same plugin within minutes of each other (`agent-worktrees` was the
  frequent case) had to race to read-then-write the same version number.
  Changefiles remove that race entirely: each PR just declares its own
  intent (`patch`/`minor`/`major`/`dev`), several changefiles for the same
  plugin can coexist peacefully, and `tools/accumulate_bumps.py` merges them
  (biggest bump wins) into one real version only when the CI promotion
  pipeline actually consumes them — you never need to re-fetch `dev` and
  guess at a number before pushing.

## The wait, and how to preview past it

Merging to `dev` is not the same as shipping. Every consumer still only ever
polls `main`. The CI promotion pipeline
(`.github/workflows/validate-and-promote.yml`) is **triggered by a
green `dev` build, not a schedule** — but there is a real wait between your
merge landing on `dev` and a promotion actually shipping it to `main`, not
an instant release.

### The pipeline is a hard chain — a red `dev` build ships nothing

The `gate` job only runs `if: github.event.workflow_run.conclusion ==
'success'` on `CI`, and the full validation suite + `promote` job only run
once `gate` confirms the commit is genuinely on `dev`'s history. If
your merge leaves `dev`'s CI red, the chain simply never fires for that
commit — **no candidate PR, no promotion, no release** — and this blocks
every other contributor's already-merged work sitting on `dev` behind yours
too, since the next successful trigger promotes everything accumulated on
`dev` so far. Treat a red `dev` build as your first priority: land a forward
fix immediately, or revert your own merge, rather than leaving it red while
you investigate at leisure.

### Expect roughly 10-20 minutes, and know what to watch

Budget on the order of **10-20 minutes** from a green `dev` merge to a real
`main` release, not an instant one: `CI` on `dev` (a few minutes) → `Validation
Gate` → `Promote` opens a generated `release/promote-<run id>` candidate PR
against `main` → that candidate PR's own fast `main source gate` check → auto-merge
(via the pipeline's own `APERTURE_RELEASE_TOKEN`, not your account). To confirm
your change actually shipped rather than assuming the `dev` merge itself was
the release:

```bash
gh pr list --repo ThomasMichon/copilot-extensions --search "is:merged head:release/promote-" --limit 5
```

A small, separate **"clear consumed changefiles on dev"** housekeeping PR
normally follows a few minutes after each successful promotion (it deletes
the changefiles that promotion just consumed) — routine cleanup, not
something you need to review, but expected to appear.

Two tools close the impatience gap without waiting on a real promotion:

- **`python tools/preview_release.py <plugin>`** builds a scratch copy of
  that plugin's payload — with its vendored `libs/<lib>` materialized from
  canonical, and the version it would get if its pending changefiles were
  consumed right now — entirely read-only against your real checkout. Good
  for "what would ship" without touching anything.
- **For actually running your own uncommitted/unmerged code against the real
  deployed CLI**, use the **mutable-dev-slot** pattern:
  `docs/patterns/mutable-dev-slot.md` (ThomasMichon/copilot-extensions#3376).
  It gives each plugin a claimed, first-class `versions/dev/` runtime slot
  rebuilt in place, GC-protected by a `dev-claim.json` sidecar.

If something promoted to `main` turns out to be bad, see
`tools/rollback_release.py` (pause the pipeline, revert the generated
commit, then resume once `dev` has an actual fix) rather than hand-editing
`main`.

### Never admin-merge a PR into `main` — not even "just this once"

`main` only ever moves via a `release/promote-*` PR or a genuine
workflow-file-ONLY bootstrap PR (see the `main-gate` job in `ci.yml`), and
`main-gate` recognizes and passes **both** of those on its own —
unassisted, no override needed. That means `gh pr merge --admin` (or the
equivalent `--admin` flag on any PR-merge tool) has **no legitimate use
against `main`** once this gate is in place: if `main-gate` is failing your
PR, that is the gate correctly telling you the PR doesn't belong on `main`
— retarget it to `dev`, don't override the check. This is not a hypothetical
risk: a PR landed directly on `main` via admin-bypass once
(ThomasMichon/copilot-extensions#3622-erratum), stranding content that the
next wholesale dev→main promotion would have silently reverted, because
`main`'s tree is regenerated entirely from `dev`'s current tip on every
cycle — anything that only ever touched `main` is invisible to that diff
and vanishes the next time anything else promotes. If you're an agent about
to reach for `--admin` against this repo's `main`: stop, re-read this
section, and retarget to `dev` instead.

#### If you opened a PR against `main` by mistake

`main-gate` (above) only ever runs as part of `ci.yml`, a plain
`pull_request`-triggered workflow — for a first-time or otherwise
unapproved external contributor, GitHub holds that *entire* workflow run in
`action_required` until a maintainer manually approves it, so such a
contributor can get zero automated feedback at all. `.github/workflows/
base-branch-reminder.yml` closes that specific gap: a narrow,
`pull_request_target`-triggered job (not subject to the fork-approval gate)
posts a one-time comment asking the author to retarget to `dev`, for any
PR against `main` not authored by the repo owner. It never checks out or
executes the PR's own code and never runs tests — its only effect is that
one comment, so it adds no capability a non-collaborator didn't already
have.

### If `main`'s history is force-rewritten

`main` may occasionally have its history rewritten (e.g. a deliberate,
operator-approved purge of accumulated large blobs from old promotion
commits — see the dev-branch-release-pipeline effort's own Journal for any
specific instance). This is a one-shot, `main`-only operation, never
routine, and never something an agent decides to do on its own initiative.

If your local checkout/worktree's `main` ends up non-fast-forward against
`origin/main` after one of these (`git fetch` reporting diverged history, or
a push to `main` rejected for a reason that isn't the ordinary gate checks
above): **don't merge, rebase, or try to reconcile the two histories.**
`main` is a generated artifact (wholesale-replaced every promotion anyway,
per the section above) — just discard your local `main` and recreate it from
the new one:

```bash
git fetch origin main
git checkout main && git reset --hard origin/main
# or, for a worktree whose own branch merely based off the old main:
git rebase --onto origin/main <old-main-tip> <your-branch>
```

**`dev` is never affected** — it forked long before any such rewrite and
has its own independent, untouched history; only checkouts that track `main`
directly need this. Nothing downstream that actually *consumes* this repo
needs to know or care either: `copilot plugin install`/`update` (both the
direct-repo and marketplace paths) and `worktree-manager`'s own self-updater
fetch **by branch name** (`git fetch --depth 1 <repo> main` + `checkout
FETCH_HEAD`, or an equivalent GitHub codeload tarball keyed by ref) — never
a pinned commit SHA — so they transparently pick up whatever is currently on
`main`, rewritten or not. This was confirmed live (installed a real plugin
from a throwaway test repo, force-rewrote its `main`, re-ran `copilot plugin
update`/`copilot plugin marketplace update`: both picked up the rewritten
content with no error, no warning, nothing to work around).

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

> Prerequisite, every time: **bump the version** (see *Where the version lives*).
> `<repo> update` is version-gated — an un-bumped change deploys nothing.

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
2. **Add a changefile** for `agent-worktrees` (see "Adding a changefile")
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
2. **Add a changefile** for `agent-bridge` (see "Adding a changefile")
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
2. **Add a changefile** for `agent-codespaces` (see "Adding a changefile")
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
2. **Add a changefile** for `agent-containers` (see "Adding a changefile")
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
2. **Add a changefile** for `agent-mcp` (see "Adding a changefile")
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

### Git Hooks

The repo ships git hooks under `tools/hooks/`:

- **`pre-commit`** — on staged files: `ruff check --select F,E9` on Python
  (unused imports/vars, undefined names, syntax errors), and
  `tools/check-skills.py` on any staged `SKILL.md` (frontmatter validity, `name`
  rules, and the **1024-char `description` limit** the Copilot CLI enforces —
  over it, the loader silently drops the skill).
- **`pre-push`** — runs the repo-wide guards: `tools/check-install-contract.py`
  (the [install contract](docs/install-contract.md)),
  `tools/check-no-internal-identifiers.py`, `tools/check-vendored-libs-sync.py`,
  `tools/check-headless-launch.py`, `tools/check-skills.py`,
  `tools/check-docs-consistency.py`, `tools/check-runbook-references.py`,
  `tools/check-version-consistency.py` (every plugin's version identical across
  `plugin.json` / `pyproject.toml` / its `marketplace.json` entry — a one-file
  bump wedges the Picker's update indicator), `tools/check-feed-neutrality.py`
  (no config/Dockerfile/install-script/CI-workflow file may hardcode a public
  package-feed URL as the only usable endpoint — this repo runs on machines
  whose default feed is network-blocked and replaced with an internal mirror),
  and `tools/check-module-size.py` (the 1,000-line-per-module cap and
  shrink-only baseline described above).

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
