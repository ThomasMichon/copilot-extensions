# CI/CD Pipelines

This is the mechanical reference for how a change actually moves through
this repo: the automated gates a pull request passes before it merges to
`dev`, how `dev` promotes to `main`, how to preview a pending release
without waiting for a real promotion, and the complete list of scheduled
and reactive automation this repo runs. `CONTRIBUTING.md` owns contributor
*process* (how to open a PR, the review-verdict waiting loop, documentation
requirements); this doc owns the *infrastructure* those steps run on top
of — link here rather than restating any of it.

## Branch model at a glance

- **`dev`** is this repo's default branch and the only one an ordinary PR
  targets. All review, status-check, and merge-approval policy below
  applies to `dev`.
- **`main`** is a **generated, release-only** branch. It is never a PR merge
  target for ordinary contribution — a CI promotion pipeline wholesale-
  regenerates it from `dev`'s current tip on a green build (see
  [Promotion: dev → main](#promotion-dev--main) below). `main` is PR-gated,
  not push-gated: direct pushes are blocked for everyone, and only three
  narrow, verified PR shapes are accepted — the promotion pipeline's own
  PR, a workflow-file-ONLY bootstrap PR, or
  `module-size-baseline-widen.yml`'s own automated PR (see
  [Never admin-merge a PR into `main`](#never-admin-merge-a-pr-into-main)
  below for exactly what each shape requires).

If you (or a stale worktree/bookmark) still have a PR open against `main`,
retarget it — `gh pr edit <#> --base dev` — then rebase onto current `dev`
and resolve conflicts for real; there's no need to open a fresh PR.

## PR gating: the four enforcement layers

Every change lands through a pull request against `dev`. This is enforced
on four independent layers that agree:

### 1. Tooling

`.agent-worktrees/config.yaml` sets `pr.required: true`, so `agent-worktrees
push-changes` refuses direct-to-`dev` and the PR-workflow git-hooks block
committing to `dev` / pushing a worktree branch directly.

### 2. Branch policy (GitHub rulesets)

A GitHub repository ruleset ("dev branch policy: PR-required") carries a
`pull_request` rule (+ `non_fast_forward`) that blocks direct pushes to
`dev` server-side, for everyone (no bypass). A separate branch-protection
rule on `main` restricts pushes to the promotion pipeline's own identity,
with repo-admin escalation retained for genuine emergencies (see
[Never admin-merge a PR into `main`](#never-admin-merge-a-pr-into-main)
below).

### 3. Review automation

`.github/workflows/copilot-review-gate.yml` requests a Copilot review
automatically, but **only** for a PR authored by a **Maintainer** (the
CODEOWNERS root roster, owner included) — not merely any invited
collaborator. A Contributor's PR gets no automatic request (a Maintainer
can still request one manually via the "Reviewers" sidebar), and an
uninvited outsider's PR gets no automatic review at all. This scoping
exists because GitHub's `requestReviewers` call for
`copilot-pull-request-reviewer[bot]` silently no-ops — no exception, no
timeline event — when made by the default `GITHUB_TOKEN`
(`github-actions[bot]`) for a PR whose author isn't this repo's owner (a
personal, non-org account has no equivalent of the org-only "members
without a Copilot license" carve-out). `MAINTAINER_REVIEW_PAT`, a
fine-grained PAT for the owner scoped to only this repo (`Pull requests:
write` + `Metadata: read`), makes the request instead, as a licensed
account rather than the bot — used only for Maintainer-authored PRs, since
Maintainers already hold ruleset self-merge bypass (this extends no new
trust), and a Contributor's PR still needs a Maintainer's own approving
review regardless of Copilot's verdict. (The ruleset-native
`copilot_code_review` auto-review rule has no such condition — it would
fire for literally anyone — so this repo has removed that rule.) Like
`workflow-lockdown-guard.yml` below, this workflow runs on
`pull_request_target`, so it can't fire until its own file has reached
`main` via a promotion — request a review manually for any PR opened
before that first promotion completes.

Whether a review (Copilot's or anyone else's) must formally *approve* the
PR before merge is governed by a second ruleset ("dev branch policy:
review required (maintainer bypass)") plus `.github/CODEOWNERS`
(root-scoped to the full **Maintainer** group — CODEOWNERS review
satisfaction is OR across listed owners, so any *one* Maintainer's
approval counts, not all of them):

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
  entry, **not** a bump to GitHub's `Maintain`/`Admin` repository role — a
  Maintainer here keeps their ordinary `Write` permission (no
  repo-settings, Actions-secret, or collaborator-management access) and
  gains only the self-merge capability.
  > **Read the bypass mechanism precisely: it is bound to the *merging*
  > actor, never to the PR's author.** GitHub ruleset bypass has no "only
  > my own PRs" concept: `bypass_mode: pull_request` means "when this
  > named actor performs the merge, this rule doesn't apply to them," full
  > stop — regardless of whose PR it is. In practice this means any
  > Maintainer *could* merge a Contributor's still-unapproved PR
  > themselves, bypassing the review-count/codeowner requirement meant for
  > that Contributor. There is no GitHub-side technical control for
  > "bypass only when merging your own PR"; the mitigation is that
  > Maintainers are trusted not to merge past a Contributor's required
  > review, and every bypass is visible in the ruleset insights / audit
  > log after the fact.
- Required CI status checks (`PR gate`, a fixed-name aggregate — see its
  own definition in `.github/workflows/ci.yml` for why a fixed anchor job
  exists rather than naming dynamic matrix jobs directly) apply to
  everyone with no bypass, including every Maintainer.

### 4. Workflow/CODEOWNERS lockdown

`.github/workflows/`, `.github/actions/`, and `.github/CODEOWNERS` itself
are locked to **this repo's owner alone** (`ThomasMichon` here, derived
from the immutable `github.repository_owner` context value rather than a
repository secret/variable — a repository *variable* is writable via the
API/CLI by ordinary Write access, the same Maintainer tier this lockdown
restricts, which would let a Maintainer defeat it by simply re-pointing
the value, so a user-owned fork's own owner is protected automatically
with zero configuration instead — this policy is scoped to user-owned
repos only; an organization-owned fork needs its own authorization
mechanism, since no individual PR author can ever equal an org login), not
the wider Maintainer group (workflow changes can exfiltrate secrets/PATs,
a materially different risk than an ordinary code change). A Maintainer's
review-bypass above does *not* cover this: a required status check
(`workflow-lockdown-guard`, from
`.github/workflows/workflow-lockdown-guard.yml`, run via
`pull_request_target` so a PR can't neuter its own trusted definition) in
its own ruleset ("dev branch policy: workflow/CODEOWNERS lockdown") fails
whenever a protected path is touched unless the **PR's registered author**
(`pull_request.user.login`) matches `github.repository_owner`.

This deliberately checks the PR's submitter, not individual commit
metadata: per-commit `author`/`committer` login is just GitHub's
resolution of the commit's plain-text git identity against an account,
not cryptographic proof — any contributor could set a commit's author
string locally and pass it with zero real involvement, so validating
commit metadata would buy nothing but complexity. A PR's `user.login`, by
contrast, is an authenticated fact GitHub sets once at PR-creation time
(you cannot open a PR as another account) — there's no equivalent way to
forge it, and unlike an event's `sender`, it doesn't change on
close/reopen.

**Known, accepted residual risk:** this checks who *opened* the PR, not
who pushed every commit in it — an already-invited Write collaborator
could still push a follow-up commit directly onto the owner's own
already-open PR branch, and this check would still pass. Closing that
fully would need commit-signature verification, which this repo has
decided against setting up (too much operational hassle for the residual
risk). This is a narrower, more unusual threat than an arbitrary outsider
or a Contributor's own PR, both of which this check fully closes.

**Known, accepted structural limitation:** GitHub's `required_status_checks`
rule matches purely by context name (`workflow-lockdown-guard`), not by
which workflow file produced it. A Write Maintainer could modify an
existing, untrusted-`pull_request` workflow (e.g. `ci.yml`) within their
own PR to add a trivially succeeding job of the same name — GitHub does
not distinguish that forged check from this one by app identity. Closing
this fully needs a check reported by a distinct, separately trusted GitHub
App pinned in the ruleset by `integration_id` — real additional
infrastructure this repo hasn't built. Until it does, treat this lockdown
as a strong deterrent against an ordinary Contributor's PR or an
unsophisticated mistake, not a cryptographically hard guarantee against a
Write Maintainer deliberately trying to defeat it.

This ruleset has **no bypass actors at all** — not even the owner —
because the check's own pass condition already grants exactly the
intended exemption. The practical effect: the owner can merge their own
workflow-touching PRs freely; adopting anyone else's such PR requires
re-authoring/re-pushing it under the owner's own account first — a
deliberate friction, not an oversight. (This is a required-status-check
workaround, not GitHub's native `file_path_restriction` ruleset rule: that
rule type returns `Validation Failed` on this personal, non-Enterprise
account — it's an Enterprise-only feature.)

> **Rollout note:** `workflow-lockdown-guard.yml` runs on
> `pull_request_target`, which always executes the workflow definition
> from the repository's ACTUAL default branch — **`main`**, not `dev` —
> so the check structurally cannot report at all until `main` has its own
> copy, which only happens after the next promotion. The enforcing
> ruleset is created but left `disabled` until after that promotion
> completes and a subsequent PR confirms the check actually reports
> successfully — only then is it flipped to `active`.

### Everyone else

Anyone who hasn't been invited as a collaborator at all **gets no
automatic CI, no automatic Copilot review, and no agentic-workflow
support**, only the strictest built-in fork-PR-approval gate (Settings →
Actions → General → "Fork pull request workflows" → **"Require approval
for all outside collaborators"**), which already blocks every Actions run
(including CI) from starting until a Maintainer manually approves it, plus
`copilot-review-gate.yml`'s own collaborator check (above) for review. A
Maintainer can still manually approve a run or request a review for an
outside PR at their discretion — this only removes the automatic path for
someone the repo owner never invited.

**This also means an unapproved fork PR against `main` gets zero automated
feedback**, including from `main-gate` (below) — GitHub holds the *entire*
`ci.yml` run, every job included, in `action_required` until a maintainer
approves it. `.github/workflows/base-branch-reminder.yml` exists
specifically to close that gap: see
[If you opened a PR against `main` by mistake](#if-you-opened-a-pr-against-main-by-mistake).

## Automated workflows reference

Every workflow under `.github/workflows/`, what triggers it, and what it
does. Grouped by role; see each file's own header comment for full
rationale and any known limitations.

**PR-related gating** (not all run on every PR — `ci.yml` runs on every
PR; the review and lockdown workflows target only `dev`; the reminder
targets only `main`; `trusted-ci.yml` is path-scoped and opt-in):

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | `pull_request`, `push` (`main`/`dev`), `workflow_dispatch` | The fast smoke-CI suite (guards, lint, per-plugin tests) that gates every PR. Contains `main-gate` (rejects a non-promotion PR against `main`) and the `PR gate` fixed-name required-check aggregate. |
| `copilot-review-gate.yml` | `pull_request_target` → `dev` | Requests an automatic Copilot review, Maintainer-authored PRs only (see [Review automation](#3-review-automation)). |
| `workflow-lockdown-guard.yml` | `pull_request_target` → `dev` | Required check: fails if a protected path (`.github/workflows/`, `.github/actions/`, `.github/CODEOWNERS`) is touched by anyone but the repo owner (see [Workflow/CODEOWNERS lockdown](#4-workflowcodeowners-lockdown)). |
| `base-branch-reminder.yml` | `pull_request_target` → `main` | Posts a one-time comment asking a non-owner author to retarget a PR against `main` to `dev` (see [If you opened a PR against `main` by mistake](#if-you-opened-a-pr-against-main-by-mistake)). Never checks out or executes PR code. |
| `trusted-ci.yml` | `pull_request_target`, paths-scoped to `.github/workflows/trusted-ci.yml`/`libs/**`/`agent-bridge`/`tools/**` | Opt-in (`vars.TRUSTED_SELF_HOSTED_CI`), collaborator-gated self-hosted CI lane for paths too sensitive/expensive for the standard runner pool; includes its own file so a change to the gate itself also re-runs it. |

**Reactive, post-CI (triggered by `ci.yml` completing):**

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `identifier-leak-guard.yml` | `workflow_run: ["CI"]` | Scans a PR's changed-file contents (as inert `git show` data, never executed) for configured private identifiers; reports a Check Run. GitHub's documented fork-safe "run untrusted work under `pull_request`, react from a separate privileged `workflow_run`" pattern. |
| `promote-trigger.yml` | `workflow_run: ["CI"]` | Cheap, unthrottled filter: decides whether a completed "CI" run was a genuine `dev`-branch advancement, and if so fires the `repository_dispatch` signal `validate-and-promote.yml` waits on. Kept separate from that workflow so this filter never queues behind the full validation suite's own concurrency group. |

**The promotion pipeline (dev → main):**

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `validate-and-promote.yml` | `repository_dispatch: [dev-advanced]`, `workflow_dispatch` | The pipeline itself: `gate` (confirm the dispatch really reflects `dev`'s current tip) → full validation suite → `promote` (open a generated `release/promote-<run id>` PR against `main`, auto-merge via `APERTURE_RELEASE_TOKEN`). See [Promotion: dev → main](#promotion-dev--main). |
| `purge-consumed-changefiles.yml` | `schedule` (daily), `repository_dispatch: [purge-changefiles-requested]` | Opens the routine "clear consumed changefiles on dev" housekeeping PR after a promotion consumes them. |

**Scheduled sweeps and watchdogs** (not triggered by PR events — though
`module-size-baseline-widen.yml` itself opens/updates a PR as its own
output, and `stale-branch-sweep.yml` acts on already-merged PRs' branches):

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `module-size-baseline-widen.yml` | `push` → `main` | Post-merge, main-only companion to the module-size guard: widens the shrink-only baseline when a legitimate growth landed, via its own reviewable PR — never inside a PR branch's own CI run against its own diff. |
| `module-size-baseline-widen-verify.yml` | `workflow_dispatch` | Manual verification harness for the above. |
| `module-health-watchdog.yml` | `schedule` (daily), `workflow_dispatch` | Scans for module-size drift and other structural health signals; files tracking issues. |
| `stale-branch-sweep.yml` | `schedule` (weekly), `workflow_dispatch` | Deletes merged-PR branches still lingering on `origin` — the automated form of the one-time 3000+-branch cleanup. |
| `installation-context-full.yml` | `schedule` (daily), `workflow_dispatch` | Full-sweep variant of the installation-context contract smoke check `ci.yml` runs per-PR. |
| `context-handoff-exhaustive.yml` | `schedule` (weekly), `workflow_dispatch` | The real-git/child-process/lock-race exhaustive suite for `context-handoff`'s exhaustive test tree — deliberately its own workflow so its `schedule` trigger doesn't apply to the rest of `ci.yml`'s matrix. |
| `crash-diagnostics-stress.yml` | `schedule` (daily), `workflow_dispatch` | Stress-tests `context-handoff` crash-diagnostics paths. |
| `ci-failure-fix-attempt.lock.yml` | `issues: [labeled]`, `workflow_dispatch` | Generated agentic-workflow (`gh-aw` compiled) that reacts to a labeled CI-failure tracking issue; also manually dispatchable with inputs. |

## Promotion: dev → main

Merging to `dev` is not the same as shipping. Every consumer still only
ever polls `main`. The promotion pipeline
(`.github/workflows/validate-and-promote.yml`, fired by
`promote-trigger.yml`) is **triggered by a green `dev` build, not a
schedule** — there is a real wait between a merge landing on `dev` and a
promotion actually shipping it to `main`, not an instant release.

### A red `dev` build ships nothing

The pipeline is a hard chain: the `gate` job only proceeds once the
triggering "CI" run genuinely reflects `dev`'s current tip and was green,
and the full validation suite + `promote` job only run once `gate`
confirms that. If a merge leaves `dev`'s CI red, the chain simply never
fires for that commit — **no candidate PR, no promotion, no release** —
and this blocks every other contributor's already-merged work sitting on
`dev` behind it too, since the next successful trigger promotes everything
accumulated on `dev` so far. Treat a red `dev` build as the first
priority: land a forward fix immediately, or revert the breaking merge,
rather than leaving it red at leisure.

### Expect roughly 10-20 minutes, and know what to watch

Budget on the order of **10-20 minutes** from a green `dev` merge to a real
`main` release: `CI` on `dev` (a few minutes) → `Validation Gate` →
`Promote` opens a generated `release/promote-<run id>` candidate PR
against `main` → that candidate PR's own fast `main source gate` check →
auto-merge (via the pipeline's own `APERTURE_RELEASE_TOKEN` — a personal
fine-grained PAT minted under the maintainer's own account, not the
default `GITHUB_TOKEN`/Actions-bot identity). To confirm a change actually shipped rather than
assuming the `dev` merge itself was the release:

```bash
gh pr list --repo ThomasMichon/copilot-extensions --search "is:merged head:release/promote-" --limit 5
```

A small, separate **"clear consumed changefiles on dev"** housekeeping PR
(opened by `purge-consumed-changefiles.yml`) normally follows a few
minutes after each successful promotion — routine cleanup, not something
that needs review, but expected to appear.

### Previewing without waiting for a real promotion

- **`python tools/preview_release.py <plugin>`** builds a scratch copy of
  that plugin's payload — with its vendored `libs/<lib>` materialized from
  canonical, and the version it would get if its pending changefiles were
  consumed right now — entirely read-only against the real checkout. Good
  for "what would ship" without touching anything.
- **For actually running uncommitted/unmerged code against the real
  deployed CLI**, use the **mutable-dev-slot** pattern:
  `docs/patterns/mutable-dev-slot.md`. It gives each plugin a claimed,
  first-class `versions/dev/` runtime slot rebuilt in place, GC-protected
  by a `dev-claim.json` sidecar.

If something promoted to `main` turns out to be bad, see
`tools/rollback_release.py` (pause the pipeline, revert the generated
commit, then resume once `dev` has an actual fix) rather than hand-editing
`main`.

## Never admin-merge a PR into `main`

`main` only ever moves via a `release/promote-*` PR, a genuine
workflow-file-ONLY bootstrap PR, or `module-size-baseline-widen.yml`'s own
automated PR (see the `main-gate` job in `ci.yml`), and `main-gate`
recognizes and passes **all three** of those on its own — unassisted, no
override needed. That means `gh pr merge --admin` (or the
equivalent `--admin` flag on any PR-merge tool) has **no legitimate use
against `main`** once this gate is in place: if `main-gate` is failing a
PR, that is the gate correctly telling you the PR doesn't belong on `main`
— retarget it to `dev`, don't override the check. This is not a
hypothetical risk: a PR landed directly on `main` via admin-bypass once,
stranding content that the next wholesale dev→main promotion would have
silently reverted, because `main`'s tree is regenerated entirely from
`dev`'s current tip on every cycle — anything that only ever touched
`main` is invisible to that diff and vanishes the next time anything else
promotes.

`main-gate`'s own diff of "what does this PR actually change" is
**merge-base-relative** (`git diff --name-only "$(git merge-base base
head)" head`), matching GitHub's own "Files changed" tab and the
`pulls.listFiles` API — not a literal `base_sha..head_sha` diff, which
would disagree with those on a diverged branch (e.g. wrongly rejecting a
workflow-only PR solely because `main` independently gained an unrelated
file after the branch forked).

### If you opened a PR against `main` by mistake

`main-gate` (above) only ever runs as part of `ci.yml`, a plain
`pull_request`-triggered workflow — for a first-time or otherwise
unapproved external contributor, GitHub holds that *entire* workflow run
in `action_required` until a maintainer manually approves it, so such a
contributor can get zero automated feedback at all.
`.github/workflows/base-branch-reminder.yml` closes that specific gap: a
narrow, `pull_request_target`-triggered job (not subject to the
fork-approval gate) posts a one-time comment asking the author to retarget
to `dev`, for any PR against `main` not authored by the repo owner. It
never checks out or executes the PR's own code and never runs tests — its
only effect is that one comment, so it adds no capability a
non-collaborator didn't already have.

It recognizes `main-gate`'s own three legitimate automated-PR shapes (the
release pipeline's own PR, a workflow-only bootstrap PR, and
`module-size-baseline-widen.yml`'s automated PR) by the same branch-name
and diff-content signature `main-gate` itself checks, not merely by
"author is the repo owner" — so if this reminder is ever widened to cover
every PR against `main` rather than only non-owner authors, it still can't
mistake the release pipeline's own automated PRs for ones that need a
nudge.

## If `main`'s history is force-rewritten

`main` may occasionally have its history rewritten (e.g. a deliberate,
operator-approved purge of accumulated large blobs from old promotion
commits). This is a one-shot, `main`-only operation, never routine, and
never something an agent decides to do on its own initiative.

If a local checkout/worktree's `main` ends up non-fast-forward against
`origin/main` after one of these (`git fetch` reporting diverged history,
or a push to `main` rejected for a reason that isn't the ordinary gate
checks above): **don't merge, rebase, or try to reconcile the two
histories.** `main` is a generated artifact (wholesale-replaced every
promotion anyway) — just discard the local `main` and recreate it from the
new one:

```bash
git fetch origin main
git checkout main && git reset --hard origin/main
# or, for a worktree whose own branch merely based off the old main:
git rebase --onto origin/main <old-main-tip> <your-branch>
```

**`dev` is never affected** — it forked long before any such rewrite and
has its own independent, untouched history; only checkouts that track
`main` directly need this. Nothing downstream that actually *consumes*
this repo needs to know or care either: `copilot plugin install`/`update`
(both the direct-repo and marketplace paths) and `worktree-manager`'s own
self-updater fetch **by branch name**, never a pinned commit SHA — so they
transparently pick up whatever is currently on `main`, rewritten or not.

## Release & Versioning

### Marketplace architecture

This repo is a **Copilot CLI plugin marketplace** — a GitHub-hosted
registry of plugins that machines install via `copilot plugin marketplace
add ThomasMichon/copilot-extensions`. The marketplace catalog lives at
`.github/plugin/marketplace.json` and lists every plugin with its current
version. The Copilot CLI reads this file to determine available updates.

> **Deploy with `<repo> update` — never hand-run `copilot plugin update`.**
> `copilot plugin update` on its own refreshes only a plugin's *payload*
> (cached source + skills) — it does **not** rebuild a runtime
> (venv/binstubs/service), and if the version wasn't bumped it silently
> no-ops ("already at latest"). Use the one unified flow: **`<repo>
> update`** (`agent-worktrees update`, or any repo binstub such as
> `dotfiles update`). It refreshes **every** registered plugin's payload,
> rebuilds **every** runtime, and fast-forwards the anchor checkouts — in a
> single command, per machine. See
> [docs/install-contract.md → Plugin update ≠ runtime install](install-contract.md#plugin-update--runtime-install).

### Version scheme

PEP 440-compatible versioning: `MAJOR.MINOR.PATCH[-devN]`.

- **Patch** bumps (`1.0.1 -> 1.0.2`) — bug fixes, small improvements,
  new skills/docs that don't change runtime behavior.
  > Only a change **inside a plugin folder** (its `src/`, `skills/`, or its
  > own `docs/`) ships in that plugin's payload and needs a bump. A
  > **repo-root** `docs/` change is not vendored into any plugin and needs
  > **no** bump — see
  > [install-contract.md § What the marketplace vendors](install-contract.md#what-the-marketplace-vendors-copied-vs-loaded).
- **Minor** bumps (`1.0.x -> 1.1.0`) — new features, behavioral changes,
  new CLI subcommands. **Only when the maintainer decides.**
- **Major** bumps (`1.x -> 2.0`) — breaking changes. **Only when the
  maintainer decides.**

### Contributing a change: add a changefile, don't hand-pick a version

**A version number is never hand-edited.** Instead, once per touched
plugin, run:

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
plugin (e.g. two different PRs merged close together); whichever carries
the biggest bump type wins when they're all consumed together
(`tools/accumulate_bumps.py`'s `highest_bump`) — no need to coordinate
with another PR author over the exact number.

This closes a changefile's "PR at PR-time" side of the story; consuming it
into a real version number is this pipeline's concern, not the
contributor's — see [Promotion: dev → main](#promotion-dev--main) for what
actually happens between a merge to `dev` and a real version landing on
`main`.

### Where the mechanically-applied bump lands (reference only)

Each plugin has its own version triplet. The promotion pipeline's
`tools/accumulate_bumps.py` is what actually writes these, consuming
whatever changefiles are pending; nothing here is hand-edited directly.

> **Mechanical shortcut — release/recovery tooling only, not for an ordinary
> contributor PR:** `python tools/accumulate_bumps.py --from-diff
> origin/main --apply` bumps exactly what `check-version-bump.py` requires
> for a branch — every touched plugin (all three files plus literal
> `__version__` fallbacks), every plugin that vendors a changed lib, and
> the lib itself in all its copies — each only when it is not already
> ahead of `origin/main`. This writes version manifests **directly**, the
> same way the promotion pipeline itself does, and does **not** consume or
> even look at pending changefiles — using it on an ordinary `dev` PR
> bypasses the changefile workflow above entirely. It exists for the
> promotion pipeline's own use and for manual release-recovery scenarios,
> not as a contributor-facing alternative to adding a changefile.
> `--dry-run` shows the plan.

> **General rule (applies to every plugin, present and future).** For a
> plugin `<p>`: bump `plugins/<p>/plugin.json` (`version`),
> `plugins/<p>/pyproject.toml` (`[project].version`, runtime plugins
> only), and `<p>`'s entry in `.github/plugin/marketplace.json` (found **by
> name**, not a hardcoded index). **agent-worktrees** additionally bumps
> `metadata.version`; adding a new plugin appends a `plugins[]` entry and
> bumps `metadata.version`.
>
> **Keep any in-package `__version__` in sync.** A runtime plugin that
> exposes a Python `__version__` must bump it to match the
> `pyproject.toml` version in the **same** commit — it is a *fourth* file
> for that plugin, easy to miss because the marketplace doesn't read it.
>
> **Enforced by `tools/check-version-consistency.py`** (pre-push): fails
> if any plugin's `plugin.json` / `pyproject.toml` / `marketplace.json`
> versions disagree.
>
> **Enforced by `tools/check-changefile-presence.py`** (pre-push + CI,
> PR-diff scoped, against `dev`): fails the push/PR if a plugin's content
> changed **without** a pending changefile naming it. A change to **any
> file under `plugins/<p>/`** requires a changefile for `<p>`; a change to
> a shared, vendored `libs/<lib>/` requires one for **every** plugin that
> vendors it. Repo-root files not vendored into any plugin (`tools/`,
> `.github/`, repo-root `docs/`, `CONTRIBUTING.md`, `README.md`) need no
> changefile. **Also exempt, even under `plugins/<p>/`** (delegated to
> `tools/check-version-bump.py`'s own ignore list): build/venv/cache
> artifacts (`build/`, `dist/`, `.venv*/`, `.test-venvs/`, `__pycache__/`,
> `.pytest_cache/`, `.ruff_cache/`, `*.pyc`/`*.pyo`) and dev-hygiene files
> that never ship (`.gitignore`) — none of these change the runtime
> payload, so none force a bump or need a changefile.
>
> **`worktree-manager` follows the same rule, with a narrower footprint.**
> It is a top-level, out-of-plugin consumer tree with no `plugin.json` and
> no marketplace entry at all — its release version lives directly in its
> own `pyproject.toml` (`[project].version`), with its `src/*/__init__.py`
> `__version__` fallback as the "fourth file" equivalent. Any non-ignored
> change under `worktree-manager/` (or to a shared lib it consumes, as a
> real vendored copy or a `uv`-editable canonical-reference pointer)
> requires a changefile naming it the same way a plugin's own content
> change does (`python tools/changefile.py add --plugin worktree-manager
> --type patch --comment "..."` — the `--plugin` flag name is historical;
> it accepts any recognized consumer identifier).
>
> **Before editing a shared lib, find every consumer first — two commands,
> not one.** `python tools/check-vendored-libs-sync.py --list` enumerates
> every REAL, physical vendored copy (`plugins/*/libs/*` and a standalone
> consumer's own top-level `libs/*`), but a lib consumed *solely* through a
> `uv`-editable canonical-reference pointer (no local copy at all) is
> invisible to it. `python tools/check-version-bump.py --list` additionally
> names those pointer-only consumers — check both before assuming you've
> found every plugin a shared-lib change reaches.

**All version files for a plugin must be bumped together in the same
commit.** If any file is out of sync: a stale `plugin.json` makes
`copilot plugin update` report "already at latest" even when new code is
available; a stale `marketplace.json` shows the old version to machines
checking for updates; a stale `pyproject.toml` makes runtime `--version`
output wrong.

### When to add a changefile

After a set of changes is committed and ready to push — one changefile per
PR is fine; don't add one on every commit. Changefiles remove the old
hand-bump coordination problem entirely: each PR just declares its own
intent (`patch`/`minor`/`major`/`dev`), several changefiles for the same
plugin can coexist peacefully, and `tools/accumulate_bumps.py` merges them
(biggest bump wins) into one real version only when the promotion pipeline
actually consumes them.

## See also

- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — contributor process: opening a
  PR, waiting for a review verdict, documentation-impact requirements.
- [`docs/install-contract.md`](install-contract.md) — what the marketplace
  vendors and the plugin-update vs. runtime-install distinction.
- [`docs/patterns/mutable-dev-slot.md`](patterns/mutable-dev-slot.md) —
  running unmerged code against the real deployed CLI.
