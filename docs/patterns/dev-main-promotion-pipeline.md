# Pattern: tiered dev/main promotion pipeline

A reusable template for turning any multi-component repo (a plugin
marketplace, a monorepo of packages, a harness with several installable
pieces) into one with **two audiences and two branches**: a small, stable
`main` for passive consumers, and a fast-moving `dev` for active
contributors — with a fully mechanical pipeline connecting them, so no
human ever hand-picks a version number or manually decides when something
"ships."

This is the pattern this repo (`copilot-extensions`) itself runs on. Treat
this document as the thing to port, not as documentation of this repo
specifically — every identifier below (`RELEASE_AUTOMATION_TOKEN`, `dev`,
`main`, `@maintainer-1`) is a placeholder for you to name.

## 1. The role of `dev` vs `main`

Two branches, two audiences, one direction of travel (`dev` → `main`,
never the reverse):

| | `dev` | `main` |
|---|---|---|
| **Audience** | Contributors — people actively working on the repo, willing to accept in-progress or occasionally-red state | Consumers — people who check out/install the repo and expect it to just work |
| **What lands here** | Every reviewed contributor PR, directly | Only a generated, wholesale **promotion** commit, never a hand-written one |
| **Review requirement** | Real human/CODEOWNERS review, every time | None (see §2.3) — main's real gate is *shape*, not review |
| **Update cadence** | Continuous | Only when `dev` is green and a promotion cycle fires |
| **Stability contract** | "Latest reviewed work, may be mid-feature" | "Whatever most recently promoted was fully validated" |

The key discipline this buys you: a consumer who only ever points at
`main` never sees an in-progress branch, a half-landed feature, or a commit
that broke someone else's afternoon — because nothing reaches `main`
except the output of the promotion pipeline itself, and that pipeline only
fires from a `dev` state that already passed full validation.

**Corollary — `main`'s tree is *regenerated*, not merged.** Don't `git
merge dev into main`. Promotion should build `main`'s next commit as a
**wholesale tree replace**: take `dev`'s current (validated) tree and make
it `main`'s next tree, as a brand-new commit whose only parent is `main`'s
previous tip. This has one sharp edge you must document loudly for
contributors: **anything that only ever touched `main` directly is
invisible to that diff and vanishes on the next promotion.** `main` must
never be a place anyone edits by hand — see §2.3.

## 2. Pipeline mechanics and branch policies

### 2.1 The four moving parts

1. **A changefile-driven version-bump system** on `dev` (§4) — contributors
   declare *intent* ("this plugin needs a patch bump"), never a literal
   version number.
2. **A promotion workflow**, triggered by a green `dev` build, that:
   - re-validates the exact commit that went green (never a bare re-fetch
     of `dev`'s tip — see the race called out in §2.4),
   - consumes pending changefiles into real version bumps,
   - regenerates `main`'s tree wholesale from that validated `dev` state,
   - opens a PR against `main` carrying that one generated commit,
   - lets it merge (ideally with zero manual intervention — see §2.3),
   - tags the real post-merge commit on `main`,
   - opens a small follow-up PR against `dev` that deletes the changefiles
     just consumed (so the *next* promotion doesn't re-read and re-bump the
     same already-shipped intent).
3. **Branch protection (GitHub Rulesets, or your platform's equivalent)**
   that makes the above the *only* path onto either branch — see §2.3.
4. **A generated-vs-human authorship gate on `main`** that checks *shape*,
   not identity — see §2.3.4. This is what lets `main` require **zero**
   approvals safely.

### 2.2 Branch rulesets — the concrete config

Use your platform's modern ruleset/protection system, scoped per branch.
Do not rely on a single repo-wide setting; `dev` and `main` need
*different* policies, and conflating them is the single most common way
this pattern breaks (see §5, "the incident that prompted this guide").

**`main`'s ruleset — "turnkey merge":**
```jsonc
{
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 0,
        "require_code_owner_review": false,   // <- MUST also be false; see §5
        "allowed_merge_methods": ["squash"]
    }},
    { "type": "required_status_checks", "parameters": {
        "required_status_checks": [{ "context": "<main-shape-gate-check-name>" }]
    }}
  ],
  "bypass_actors": [
    { "actor_type": "RepositoryRole", "actor_id": "<Admin role>", "bypass_mode": "always" }
  ]
}
```
No approvals, no code-owner review, no bypass actors besides the built-in
Admin role (kept only as a break-glass, never routine — see §2.3.3). The
*only* thing standing between arbitrary content and `main` is the shape
gate's required status check.

**`dev`'s rulesets — real review, for real contributors:**
```jsonc
// "dev: PR required"
{ "conditions": { "ref_name": { "include": ["refs/heads/dev"] } },
  "rules": [ { "type": "pull_request", "parameters": { "required_approving_review_count": 0 } } ] }

// "dev: review required (maintainer bypass)"
{ "conditions": { "ref_name": { "include": ["refs/heads/dev"] } },
  "rules": [ { "type": "pull_request", "parameters": {
      "required_approving_review_count": 1,
      "require_code_owner_review": true
  }}],
  "bypass_actors": [
    { "actor_type": "User", "actor_id": "<maintainer-1>", "bypass_mode": "pull_request" },
    { "actor_type": "User", "actor_id": "<maintainer-2>", "bypass_mode": "pull_request" }
  ]}

// "dev: workflow/CODEOWNERS lockdown" -- protects the pipeline's own
// definition from being changed by anyone but a maintainer, enforced as a
// required status check (author-identity check), not CODEOWNERS -- because
// a maintainer's own PR bypasses code-owner review entirely, so
// CODEOWNERS alone can't enforce "only a maintainer may touch these paths."
{ "conditions": { "ref_name": { "include": ["refs/heads/dev"] } },
  "rules": [ { "type": "required_status_checks", "parameters": {
      "required_status_checks": [{ "context": "<workflow-lockdown-check-name>" }]
  }}] }
```
`bypass_mode: "pull_request"` (not `"always"`) matters: it lets a named
maintainer merge their own PR without a second reviewer, but never lets
them bypass by direct push. **Important operational fact, confirmed
live:** a bypass-actor entry (by role *or* by named user) only actually
activates through your CLI/API's explicit "administrator merge" code path
(e.g. `gh pr merge --admin`) — an ordinary/auto merge call fully respects
the stated requirements even for a listed bypass actor. Don't assume
"they're on the bypass list" is enough on its own; the merge call itself
has to ask for the bypass.

### 2.3 Why `main` can safely require **zero** reviews

This is the part people get wrong by instinct ("surely the release branch
needs *more* protection, not less"). The resolution: **the review
happened already**, when the content was reviewed on its way onto `dev`.
Requiring a *second* review on the mechanical, generated promotion PR is
pure redundant friction with no safety benefit — and worse, it's often
structurally impossible to satisfy (nobody is a genuine "reviewer" of a
generated diff), so it just jams forever. `main`'s real protection is:

1. **It only ever moves via the promotion pipeline itself.** No other
   route in exists (see the ruleset above — no direct pushes, no arbitrary
   PRs unless they pass the shape gate below).
2. **A required-status-check "shape gate"** on every PR targeting `main`,
   checking — by diff/branch/author content, not by trusting whoever
   pushed it — that the PR is one of the small number of legitimate shapes:
   - the promotion pipeline's own generated PR (recognizable branch-name
     pattern + the automation's own author identity), or
   - a workflow-definition-only "bootstrap" PR from a maintainer (needed
     because a `workflow_run`-triggered workflow always resolves its own
     YAML from the target branch, so a fix to the pipeline's own
     definition occasionally has to land on `main` directly, ahead of the
     next promotion — the pipeline can't fix itself via its own
     not-yet-working path otherwise).

   Anything else — a PR that doesn't match any legitimate shape — fails
   this check and cannot merge, full stop, no override needed. This is
   deliberately enforced by **content**, not by trust in whoever has
   admin/bypass rights, precisely so that admin-merge capability (which a
   maintainer legitimately has, and shouldn't have revoked) never becomes
   something reached for routinely.
3. **`main`'s tree is wholesale-regenerated every cycle.** Even if
   something bad slipped through, the next promotion overwrites `main`'s
   tree entirely from `dev`'s current state — so a bad direct edit to
   `main` doesn't durably survive, it just gets silently discarded (which
   is a hazard in its own right — see the callout in §1 — but does mean
   `main` self-heals from anything except a change to the pipeline's own
   definition, which is exactly what the shape gate above protects).

### 2.3.1 Never admin-merge into `main` — not even once

State this explicitly in your contribution docs, in these words or close
to them: *once the shape gate exists, `gh pr merge --admin` (or your
platform's equivalent override) has no legitimate use against `main`.* If
the shape gate is rejecting your PR, that is the gate correctly telling
you the PR doesn't belong on `main` — retarget it to `dev`. A single
real incident is worth citing internally: a PR landed directly on `main`
via admin-bypass once, stranding content that the very next wholesale
promotion silently reverted, because nothing on `dev` knew that content
existed. Admin-merge capability on `main` is legitimate (maintainers are
maintainers) and shouldn't be revoked — but after the shape gate exists,
it should never actually be *used*.

### 2.4 A generated-PR merge race to design around up front

If your promotion PR is *already* fully mergeable (checks green, no
review needed) at the instant your CLI/API arms "merge when ready," the
provider can race finding nothing left to wait on and error instead of
just merging — rather than falling back to a direct merge. Confirmed live,
twice, in two different shapes:

- The **main-targeting promotion PR** can hit this if it happens to be
  clean the moment auto-merge is armed. Fix: catch the specific "already
  clean" error text from the arm call and fall back to an immediate direct
  merge on that specific error only.
- The **dev-targeting changefile-cleanup PR** is *always* generated,
  mechanical, and touches nothing but changefile deletions — but if `dev`
  tightens its review requirement for real contributor PRs (as it should),
  this generated PR now needs a review it can never organically receive.
  There is **no path-scoped review exemption** in GitHub's ruleset model
  (bypass is only by role/team/App, never by diff content) — so the fix
  has to live in your pipeline's own code: **re-verify the diff is
  confined to exactly the expected mechanical shape (e.g. changefile
  deletions only), and only if that holds, merge via the administrator
  bypass path** (exercising the maintainer's own named bypass-actor entry,
  §2.2). Anything that fails that check is left open for real human
  review — never force-merged blind.

### 2.5 Pinning what you validate

Re-resolving "the current tip of `dev`" at promotion time, after a real
multi-minute validation window, is a race: new commits (and new
changefiles) can land on `dev` while validation is still running, and a
bare re-fetch at promotion time would silently sweep never-validated
content into the promotion. **Pin the exact commit SHA your validation job
actually checked out and tested**, thread it through to the promotion
step as an explicit input, and never let the promotion step independently
re-resolve the branch tip.

## 3. Generic CONTRIBUTING guide (template)

Adapt this section wholesale into your repo's own `CONTRIBUTING.md`.

> ### Contribution flow
> All changes land via PR into `dev` — never push directly, never target
> `main`. Once merged to `dev`, your change ships to `main` automatically
> within roughly 10-20 minutes, via the promotion pipeline (§2) — not
> instantly, and not by anyone hand-deciding "this is ready to release."
>
> ### Adding a changefile
> You never hand-edit a version number. Once per touched component, run:
> ```
> <changefile-tool> add --component <name> --type patch --comment "<summary>"
> ```
> Default to `patch` (or your project's equivalent of a pre-release/dev
> bump for an iterative fixup within an already-in-flight change). Only
> request `minor`/`major` when a maintainer explicitly says so. Multiple
> changefiles may target the same component from different PRs; whichever
> carries the biggest bump type wins when they're consumed together — you
> never coordinate with another PR's author over the exact number.
>
> ### The wait, and how to preview past it
> A red `dev` build ships nothing — the promotion pipeline only ever fires
> from a green build, and it blocks every other contributor's already-merged
> work sitting on `dev` behind yours too. Treat a red `dev` build as your
> first priority.
>
> To confirm your change actually shipped (rather than assuming your `dev`
> merge itself was the release):
> ```
> gh pr list --repo <owner/repo> --search "is:merged head:<promotion-branch-prefix>" --limit 5
> ```
> A small, separate "clear consumed changefiles" housekeeping PR normally
> follows a few minutes after each successful promotion — routine cleanup,
> not something you need to review, but expected to appear.
>
> Two tools close the impatience gap without waiting on a real promotion:
> a **preview** tool that computes "what version would this become, and
> what would its shipped payload look like, right now" entirely read-only;
> and, if your platform supports it, a **mutable dev-slot** pattern for
> actually running your own uncommitted code against a real live install
> without waiting on any of the above.
>
> If something promoted turns out to be bad: pause the pipeline, revert
> the generated commit, then resume once `dev` has an actual fix — never
> hand-edit `main`.
>
> ### Never admin-merge into `main`
> See §2.3.1 above — reproduce it verbatim in your own `CONTRIBUTING.md`.

## 4. Vendoring, version bumps, and the changefile system

### 4.1 Changefiles: intent capture at PR time

A **changefile** is a small, uniquely-named data file (e.g. a JSON blob
under `.changefiles/`, named `<timestamp>-<slug>-<hash>.json`) that
captures *only*: which component(s) it affects, what bump size
(`patch`/`minor`/`major`/`dev`), and a human-readable comment. It is
committed alongside the PR that needs it and is **never hand-edited** to
contain a version number — the number is computed later, mechanically, by
the promotion pipeline. This is the structural fix for the classic
parallel-PR problem: two contributors touching the same component within
minutes of each other used to have to race to read-then-write the same
version field. With changefiles, both PRs just declare intent
independently; a "highest bump wins" merge (`accumulate_bumps`-equivalent)
reconciles them only when the pipeline actually consumes them.

### 4.2 Where the mechanical bump lands

For each component `<c>`, plan on (at minimum) three synchronized version
surfaces, all bumped together in the **same** generated promotion commit:

| File | Field | Purpose |
|---|---|---|
| `<c>`'s own manifest (e.g. `plugin.json`) | `version` | What your installer/updater reads to detect an available update |
| `<c>`'s own package definition (e.g. `pyproject.toml`) | `[project].version` | The version your runtime reports at `--version` / in logs |
| A central catalog file (e.g. `marketplace.json`) | that component's entry | What a remote consumer checking for updates actually reads over the network |

If your component exposes an in-code version constant too (a Python
`__version__`, a Node `package.json` alongside a hardcoded string
elsewhere), bump that in the same commit — it's a fourth, easy-to-miss
surface, and a stale one makes a correctly-deployed component misreport
its own version.

**Enforce, don't just document, synchronization:**
- A pre-push/CI check that fails if any of a component's version surfaces
  disagree with each other.
- A pre-push/CI check, scoped to the PR's diff against `dev`, that fails
  if a component's content changed **without** a pending changefile naming
  it. A change to a **shared, vendored** library needs a changefile for
  **every** component that vendors a copy of it (see §4.3) — a lib change
  reaches every consumer's payload.

### 4.3 Vendoring a shared library per consumer

If multiple components need to share code, prefer **vendoring a real copy
per consumer** (e.g. `components/<c>/libs/<lib>/`, installed as an
editable/path dependency from inside that component's own package
definition) over a single shared package dependency — it keeps each
component's payload self-contained and independently installable, at the
cost of needing an explicit sync mechanism:

- A `--list` tool that enumerates every real vendored copy of a shared lib
  (so contributors can find all of them before editing any one).
- A sync-check tool that fails loudly on drift between copies — edit every
  listed copy identically (or edit one and copy it byte-for-byte to the
  rest), then re-run the check to confirm.
- Watch for a **stale, unlisted legacy copy** sitting alongside the real
  vendored ones (e.g. a top-level `libs/<lib>/` no component actually
  consumes at runtime) — it's easy to mistake for "the" source since it
  sits next to the lib's own tests, but editing it silently does nothing.

### 4.4 The promotion tool itself

Your `promote_release`-equivalent tool, run only by the pipeline, should:
1. Check out `dev` at the exact pinned, already-validated SHA (§2.5) into
   an isolated scratch worktree — never mutate the caller's real working
   tree, never mutate `dev` itself.
2. Consume every pending changefile: compute the real per-component
   version bump (highest-bump-wins across all pending changefiles per
   component), write it into all the version surfaces (§4.2), and record
   which changefiles were consumed.
3. Materialize any vendored/DRY content that only needs to exist in full
   for a *shipped* payload (e.g. expanding a lib pointer into a full copy)
   if your repo separates "source of truth" from "what a consumer
   receives."
4. Produce **one new commit** whose tree wholesale-replaces `main`'s
   current tree with this result — never a merge commit.
5. Push that commit to a disposable, uniquely-named candidate branch and
   report back exactly which changefiles it consumed, so the pipeline's
   own cleanup step (§2.1, step 3) knows precisely what to delete from
   `dev` afterward.

Ship it with a **pause/resume/rollback** companion tool too: pause writes
a small marker the promotion tool itself checks and refuses to run past;
revert reverts the generated commit; resume clears the marker once `dev`
has an actual fix. Never require a human to hand-edit `main` to recover
from a bad promotion.

## 5. The incident that prompted this guide (worth reading once)

Two independent, one-field ruleset misconfigurations jammed this exact
pipeline in production, and are worth designing your own initial rollout
to explicitly rule out:

1. **`main`'s ruleset had `require_code_owner_review: true` alongside
   `required_approving_review_count: 0`.** These are *independent* flags —
   a platform's ruleset engine can enforce the code-owner-review
   requirement regardless of the approval count being zero. Every
   generated promotion PR sat blocked forever, waiting on a CODEOWNERS
   approval nothing was ever going to supply. **Check both flags
   explicitly when you stand this up; don't assume "0 approvals" implies
   "no review gate at all."**
2. **`dev`'s review requirement was tightened for real contributors, with
   no thought given to the generated changefile-cleanup PR that also
   lands there.** See §2.4's second bullet for the fix (path-verified
   admin-merge in the pipeline's own code) — this is the general lesson:
   **whenever you tighten a rule scoped to a branch, re-check every
   *mechanical, non-human* PR shape that also targets that branch.**

Both were pure configuration bugs, not design flaws in the pattern itself
— which is exactly why this document exists: get the ruleset shape right
once, up front, and this pipeline runs unattended indefinitely.

## 6. Bootstrap checklist for a new repo

1. Decide your component boundary (what's a "plugin"/"package"/"component"
   for versioning purposes) and design its manifest + catalog files (§4.2).
2. Stand up `dev` and `main` with the rulesets in §2.2 — **verify both
   `required_approving_review_count` and `require_code_owner_review`
   independently on every ruleset**, per §5.
3. Build the changefile tool, the accumulate/consume tool, and the
   per-component version-sync + changefile-presence checks (§4.1-4.2).
4. Build the promotion workflow: gate → full validation (pinned SHA,
   §2.5) → promote (wholesale tree replace, §4.4) → tag → changefile
   cleanup, with the path-verified admin-merge fallback for the cleanup
   PR (§2.4).
5. Build the `main`-targeting shape gate as a required status check
   (§2.3.4) before you ever rely on "zero review" being safe.
6. Mint a fine-grained automation token (a generic name like
   `RELEASE_AUTOMATION_TOKEN`) scoped to at least Contents: read/write and
   Pull requests: read/write on this one repo — and **Administration:
   write**, needed for the pipeline's own admin-merge fallback (§2.4) to
   actually work when called from a workflow rather than an interactive
   session. Store it as a repo secret; never hardcode it, never log it.
7. Write your `CONTRIBUTING.md` from §3, and a rollback tool per §4.4's
   last paragraph, before your first real contributor PR lands.
8. Do a dry run: land one real changefile-bearing PR to `dev`, watch a
   full promotion cycle complete with zero manual intervention, then
   confirm the changefile-cleanup PR *also* completes with zero manual
   intervention — that second half is the one most rollouts miss (§5).
