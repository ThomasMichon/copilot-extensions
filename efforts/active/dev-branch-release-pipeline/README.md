# Dev/Main Release Pipeline

- **Slug:** `dev-branch-release-pipeline`
- **Repo:** copilot-extensions
- **Branch(es):** working branches off current `main` for Phase 1 tooling; the
  `dev` branch itself is created in Phase 2.
- **Created:** 2026-09-22
- **Status:** Draft
- **Vision:** none yet — this effort may spawn a `visions/release-pipeline`
  entry once the design settles; revisit at Phase 2/3 boundary.
- **Umbrella issue:** ThomasMichon/copilot-extensions#3336
- **Sub-issues:** ThomasMichon/copilot-extensions#182 (immediate pain this
  also resolves)

## Guiding Intent

copilot-extensions is a Copilot CLI plugin marketplace with exactly one
consumable branch (`main`): machines auto-update by polling its
`marketplace.json` for a version bump, with no ability to point at an
alternate channel. Today a PR author must hand-pick and bump per-plugin
versions across three files, hope CI is green, and merge — at which point the
change is immediately live for every consumer, with no final validation pass
between "merged" and "shipped." This also causes real collisions (see
copilot-extensions#182: parallel PRs colliding on hand-picked `-devN`
suffixes).

The goal: split into a `dev` trunk (continuous integration, changefile-based
version bumps, DRY references instead of copied vendored code) and a `main`
release channel that CI **wholly regenerates** on each cycle — accumulating
version bumps, materializing vendored code, and validating the result — so
`main` always represents a deliberately-finalized release snapshot, never an
in-flight merge. This removes the PR-time burden of precise version bumps and
vendoring, while giving us a real pre-release validation gate for the first
time.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Operator + driving agent | Design, build tooling, drive phases | `copilot-extensions` worktree on this machine |

_Expand this table if/when work is split across additional participants
(e.g. a dedicated worktree for the CI pipeline vs. one for CONTRIBUTING/AGENTS.md
rewrite)._

## Coordination

- **Topology:** independent per-slice PRs against current `main` for Phase 1
  (no branch split needed yet); Phase 2 onward requires care since it changes
  the repo's fundamental branch topology.
- **Host (owns PRs):** the participant above, until split further.
- **Delegates:** none yet.
- **Handoff:** journal every phase transition here before ending a session;
  Phase 2 (branch cutover) must not start until Phase 1 tooling is merged and
  proven idempotent against current `main`.

## Context

- Design conversation captured verbatim in **Request** below (operator +
  agent evaluation, two rounds).
- Today's mechanism (confirmed against the live repo):
  - `.github/plugin/marketplace.json` is the catalog Copilot CLI reads for
    version comparison; per-plugin `plugin.json`/`pyproject.toml` must stay
    aligned (`tools/check-version-consistency.py`).
  - `tools/check-version-bump.py` requires a manual version bump whenever a
    plugin's content changes, enforced in CI + an opt-in pre-push hook.
  - CONTRIBUTING.md § "Release & Versioning" and AGENTS.md § "Version Bump"
    document the current three-file manual-bump contract this effort
    replaces.
- copilot-extensions#182 already proposes "auto-derive `-devN`, remove human
  selection" as the real fix for version collisions — this effort
  generalizes that into the full dev/main split rather than a narrower patch.
- Known gap surfaced by the operator: the worktree-manager auto-updater
  (`agent-worktrees update`) is understood to auto-update `agent-*` plugins,
  but coverage for every copilot-extensions plugin linked into a harness —
  notably **`copilot-extensions-harness`** itself, which will carry the
  contributor-facing guidance for this new flow — is unconfirmed. Must
  verify and close this gap before or alongside cutover, or contributor
  agents will keep following stale guidance after the split ships.

## Request

> 1. Agree. We'll generate the entirety of main from the CI build, and apply
> it whole, which will produce a nice incremental diff, accounting for
> additions and removals.
>
> 2. There is no way to point downstream consumers at any other branch, as
> far as I know. So there's only *one* ever "current release" of our suite.
> My expectation was that the only thing sitting between the most-recent dev
> commit and main update was the CI pipeline, not a schedule. So a hotfix
> would only be for "dev build horked the CI pipeline and we need a fix out
> there now". Probably best just to fork of LKG of dev, run the snapshot
> tool, hot-patch main, cherry-pick back to dev, and hope the next CI build
> carries the fix.
>
> 3. Not a bad idea. Tagging works better in this model anyway, since the CI
> build can apply them at final main-update time. Of course, if we roll back
> main, we'll need to pause CI; the CI pipeline will need a guard against
> doing a "non-incremental" update, potentially.
>
> 4. I don't want an hours-long gate, though. Still need to keep it down to
> 10-30 minutes. Even 30 is pushing it. But do need a steady set of tests,
> longer than the set we currently run.
>
> 6. Maintainers would be blocked from self-merging to main, unless in
> admin-escalation mode. I still want continuous release, just gated a little
> more strongly initially. Over time, as we mature, we can walk back the
> release schedule.
>
> Traceability: love it. Idempotent generator: a must, agree. Dev-slot
> override: agree. Operator+agent agree to use dev mode to preview local
> change live, mode applies based on current local version, gets
> auto-blasted over by next increment, or has auto-expiration. And local dev
> agents should tidy up (using a claim!) and remove it when they are done.
> Version-bump retire: agree. This is also intended to take burdens off
> development side.
>
> To get started, we can create all the helper tools, pipelines, and other
> things which don't require forking the whole stack. Once we create the dev
> branch, we'll have a fork, and once we put a block on main, we better have
> the full contribution story clear. Hopefully when the cutover occurs,
> agents previously working in copilot-extensions will auto-discover the
> changes when they pull, or attempt to PR to main and get bounced.
>
> I realized we might have a hole: our worktree-manager auto-updater updates
> `agent-*` plugins, but might not auto-update *every* copilot-extensions
> plugin linked into a harness. This includes copilot-extensions-harness,
> which would be an essential companion guiding contributor agents through
> the flow.
>
> Sounds like we roughly agree on the plan. I like your improvements and
> notes. Let's make an effort, and start figuring out the sequencing.

Original framing (round 1), captured verbatim below since it was previously
only paraphrased in the umbrella issue:

> I would like to figure out how to improve the release process for
> copilot-extensions. It's now heavily used by team members in my org. I
> can't afford to let a breaking change quickly distribute to team members,
> who don't auto-update as frequently, and break their workflows.
> Unfortunately, Copilot doesn't have a release-based plugin marketplace or
> even a centralized publishing system: you point at a Git repo, and it only
> pulls from main, checking for version bumps. Right now, PR authors to
> copilot-extensions must pre-bump, hope the CI build is good, and then
> merge. There's no way to do a final validation pass before a release. And
> the last thing I want is checkins going into main, getting built, and
> *then* bumping the build. So I am trying to figure out a better way to do
> this.
>
> So, my idea: treat `main` in copilot-extensions as the "release" train, and
> target all development to a new branch. We'll check in continuously to the
> dev branch, but we'll use a monorepo versioning system like beachball to
> mark PRs as providing major, minor, patch, or dev increments to target
> packages. After a merge, and on a batched cycle, a CI pipeline will
> validate the current state of the dev branch, accumulate the version
> bumps, and then prepare a final commit to main, bumping all versions,
> vendoring code, and ensuring that `main` represents the proper "release
> snapshot" for consumption by copilot. In "dev", then, we can engage in DRY
> by avoiding copies of vendoring code, providing ref links and pointers,
> generator instructions, etc. and generally making it cleaner as a dev
> environment. We'll still need a way to build, test, and locally preview
> changes, but PRs should be easier since they won't need to deal with
> precise version bumps themselves, or pre-copying vendored code. We still
> can't pre-compile binaries or other heavy dependencies, but it will save us
> a lot of energy. CONTRIBUTING.MD and copilot-extensions-harness will get
> more complicated, as they will need to clarify the relationship and the
> process, and explain to agents that after they merge a change to dev,
> there's a wait time before the plugin is available locally. We'll have the
> "preview a release" tool available locally, so we could find a way to allow
> an impatient agent to run that, and then overwrite the local copilot's
> installed-plugins content with a dev version, until such time as we ran the
> normal auto-updater (or we offer a `dev` version-slot and config switch,
> formally allowing inline replacement for at least the tool/service side of
> our ecosystem).

The agent's round-1 evaluation (strengths, risks, and the six numbered
recommendations the operator responds to below) is *not* reproduced
verbatim here — it is agent analysis, not operator input; its substance is
folded into Context/Plan below and demarcated there as agent-recommended
where it originated the idea rather than the operator.

Round 2 (operator's response to that evaluation):

## Plan

### Phase 1 — Standalone tooling (no branch split required)
- [x] Evaluate and select the changefile-based monorepo versioning tool
      (beachball vs. alternatives); prototype the changefile format against
      1-2 real plugins. **Decided:** a native Python changefile tool
      ("our own agent-friendly beachball"), not the real `beachball` npm
      package — operator-confirmed; beachball's actual value here was as
      inspiration/UX reference, not a literal dependency.
- [ ] Build the **idempotent snapshot/materialization generator**: given a
      source tree with DRY references/generator-instructions for vendored
      code, produce the fully-materialized tree. Prove correctness by running
      it against the **current** single-branch `main` and diffing to zero
      (today's `main` already has no DRY pointers, so this is the generator's
      identity-case test before any pointers exist).
  - **Progress:** the repo already has a proven generator *pattern* for this
    (`tools/sync-installer-engine.py`: canonical -> copy, plus `--check`
    verify mode) for a couple of narrow surfaces. The biggest vendoring
    surface — shared Python libs (`libs/<lib>` canonical -> 2-11
    `plugins/<plugin>/libs/<lib>` copies each) — had no syncer at all, only
    a copies-vs-copies verifier (`check-vendored-libs-sync.py`). Shipped
    `tools/sync-vendored-libs.py` (`--check` / `--restore-canonical` /
    `--materialize`, version-ordering-gated so it can't silently regress a
    consumer) to close that gap; see Journal.
  - **Discovered and filed separately** (not fixed here, to keep this PR's
    diff reviewable): `libs/ssh-manager` and `libs/credential-relay`
    canonical sources had already drifted 8-9 dev-versions behind their real,
    mutually-in-sync vendored copies —
    ThomasMichon/copilot-extensions#3361. `--restore-canonical` fixes it;
    left as its own follow-up PR.
  - See `phase1-generator.md` (create when design work starts) for the
    generator's exact input/output contract once drafted.
- [x] Build the version-accumulation step: consume changefiles + the
      generator's output, compute final per-plugin versions
      (major/minor/patch/dev), write them into `plugin.json` /
      `pyproject.toml` / `marketplace.json`.
  - **Shipped:** `tools/changefile.py` (write/list changefiles under
    `.changefiles/`) + `tools/accumulate_bumps.py` (group pending
    changefiles per plugin, pick the highest requested bump type, apply the
    `MAJOR.MINOR.PATCH-devN` math, write all three files, bump
    agent-worktrees' catalog `metadata.version` per its special rule,
    consume the changefiles). 27 passing tests covering the bump math,
    highest-bump-wins grouping, and the full write-three-files integration.
    Smoke-tested `--dry-run` against a real plugin (`efforts`,
    `0.1.0-dev21 -> 0.1.1-dev1`), not applied.
  - Not yet wired into CI or required by any guard — that's Phase 2 (retiring
    `check-version-bump.py`'s manual-bump requirement in favor of a
    changefile-presence check).
- [x] Build the local **"preview a release"** CLI on top of the generator
      (must be the same code path CI uses, not a parallel implementation).
  - **Shipped:** `tools/preview_release.py` builds a scratch copy of one
    plugin, materializing its vendored `libs/<lib>` copies **into that
    scratch copy only** (never the real `plugins/<plugin>/libs/<lib>` in
    this checkout), and reports the version it would get if pending
    changefiles were consumed — read-only against the real repo otherwise.
    22 passing tests across the two tools.
  - **Caught and fixed a real bug before landing:** the first draft invoked
    `sync-vendored-libs.py --materialize` as a subprocess against the real
    repo, which would have silently mutated real `plugins/*/libs/*` content
    as a side effect of building a "preview." Verified the fix with a live
    `git status`-before/after smoke test against a real plugin with known
    drifted libs (`agent-bridge`) — confirmed zero real-repo diff.
- [x] Design and prototype the **dev-slot local override**: an opt-in,
      clearly-logged config switch that overwrites a machine's installed
      plugin content with a locally-generated preview; auto-expires or is
      superseded by the next real release pull; the enabling agent holds a
      claim and is responsible for tidying it up when done.
  - **Superseded by a concurrently-merged, better-designed pattern —
    withdrawn, not shipped.** Mid-session, while diagnosing an unrelated CI
    failure, discovered `ThomasMichon/copilot-extensions#3376` ("mutable
    dev slot") had just landed to `main`: a first-class `versions/dev/`
    runtime slot per plugin, rebuilt in place, gated by a
    `dev-claim.json` sidecar (schema `copilot-extensions.dev-slot-claim`,
    owner = absolute worktree path, no TTL, released explicitly) —
    integrated directly into `libs/versioned-runtime`'s existing
    immutable-slot machinery. See `docs/patterns/mutable-dev-slot.md` and
    `efforts/active/mutable-dev-slot/README.md`. This is exactly this
    effort's Phase 1 item, done more correctly (a real runtime-slot
    primitive, not a from-scratch installed-plugins-directory copy-and-claim
    hack) — and it predates my draft by the same session's timestamp.
    **Withdrew `tools/dev_slot.py` before merging** (removed from PR #3380
    prior to landing); this Plan item is resolved by cross-referencing that
    pattern rather than building a parallel one. `tools/preview_release.py`
    is kept — it answers a different question (what would this plugin's
    *promoted* payload + version look like) than mutable-dev-slot (iterate
    against the currently-deployed CLI with live, uncommitted code), so the
    two are complementary, not duplicative.
- [ ] _(agent-recommended; not explicitly re-confirmed by the operator)_
      Confirm Copilot CLI's actual update-detection behavior empirically
      (version-string diff only, no semver range awareness) — do not assume;
      verify against a controlled scratch bump.
- [ ] Draft CONTRIBUTING.md / AGENTS.md rewrite content in-repo as a doc
      (not yet the live contract) describing the new contributor flow:
      changefile-only PRs, no manual version edits, no vendoring copies.
- [x] Investigate and close the auto-updater coverage gap: enumerate which
      copilot-extensions plugins a harness worktree actually keeps current
      via `agent-worktrees update` (or equivalent), confirm whether
      `copilot-extensions-harness` is covered, and fix if not.
  - **Finding (no code fix needed):** read `_registered_plugin_targets` /
    `_update_registered_plugins` in
    `plugins/agent-worktrees/src/agent_worktrees/update_runtime.py`. The
    payload-refresh sweep is **not** filtered to `agent-*`-prefixed plugins —
    it iterates every enabled plugin from every registered `agent-worktrees`
    anchor's own `.github/copilot/settings.json` (plus user-global enabled
    plugins and installed inventory) and calls `copilot plugin update` for
    each. Since copilot-extensions itself is a registered anchor on this
    machine and its own settings.json enables
    `copilot-extensions-harness@copilot-extensions`, it is already swept.
    The suspected gap does not exist in the mechanism itself.
  - **Residual, real risk (operational, not code):** freshness still depends
    on someone actually running `agent-worktrees update` after cutover — this
    is already covered by the Phase 5 cutover-announcement item below, not a
    new fix.
- [x] _(agent-recommended finding, operator-confirmed 2026-09-23)_ **beachball
      itself is the wrong literal tool.** Beachball hard-requires a
      `package.json` per versioned package (confirmed via its own docs); this
      repo has none — it's Python/PowerShell/bash-first (`plugin.json` +
      `pyproject.toml` + `marketplace.json`). Decision: keep beachball's
      *changefile UX* (a changefile per PR: target plugin(s) + bump type +
      comment) but implement a native Python accumulator tailored to this
      repo's schema and workflow ("our own agent-friendly beachball") rather
      than depending on the real npm package.

### Phase 2 — Cut the fork
- [ ] Create the `dev` branch from `main`.
- [ ] Retire `tools/check-version-bump.py`'s manual-bump requirement in favor
      of a changefile-presence check.
- [ ] Land the real CONTRIBUTING.md / AGENTS.md rewrite as the live contract.
- [ ] Add branch protection: block direct pushes/merges to `main` except
      through the CI-run promotion job (or explicit admin-escalation).

### Phase 3 — CI promotion pipeline
- [ ] Implement the validation gate (target 10-30 min; broader than today's
      guard set — steady test suite, not just guards/lint).
- [ ] Implement bump accumulation + generator materialization run against
      `dev`'s current state.
- [ ] Implement the "replace main's tree wholesale as a single generated
      commit" step (never merge `dev` into `main`).
- [ ] Tag every generated `main` commit; stamp traceability metadata (the
      `dev` commit range / changefiles consumed).
- [ ] Trigger: **decided** — CI success on `dev`, not a schedule (the
      operator's stated expectation, not an open question). Implement
      promotion as triggered directly by a green `dev` CI run.
- [ ] Gate promotion behind admin-escalation initially (maintainers blocked
      from ordinary self-merge to `main`); document the criteria for walking
      this back over time.

### Phase 4 — Rollback & hotfix procedures
- [ ] Implement the CI guard against producing a non-incremental (out-of-order)
      `main` update after a manual rollback.
- [ ] Add an explicit **pause-CI** step to the rollback procedure: the
      promotion pipeline must be paused before a rollback commit lands, not
      just guarded after the fact, per the operator's stated rollback flow.
- [ ] Document and rehearse the hotfix flow: fork last-known-good `dev`, run
      the snapshot tool, hot-patch `main` directly, cherry-pick the fix back
      to `dev`.
- [ ] Document the rollback flow: pause CI, `git revert` the generated commit
      + re-tag, never force-push, then resume CI.

### Phase 5 — Cutover
- [ ] Confirm the auto-updater coverage fix (Phase 1) is live before or with
      cutover, so existing harness sessions discover the new contribution
      flow on next pull rather than following stale guidance.
- [ ] Ensure a PR opened against `main` post-cutover is bounced with guidance
      pointing at `dev` (branch protection message, PR template, or a bot
      comment).
- [ ] Announce cutover; watch the first few real promotion cycles closely.

### Phase 6 — Maturity walk-back
- [ ] Define success criteria for relaxing the admin-escalation gate on
      promotion (e.g. N clean cycles, zero rollbacks in M weeks).
- [ ] _(agent-recommended)_ Revisit whether CI-triggered-on-every-green-build
      promotion remains workable once volume is understood, and consider a
      lightweight batching rule only if it proves necessary in practice — the
      decided default (Phase 3) is untriggered-by-schedule.

## Validation Plan

- [ ] Generator run against current (pre-split) `main` reproduces the
      existing tree exactly (idempotency/correctness baseline).
- [ ] Dry-run the full promotion pipeline against `dev` in a scratch
      branch/fork before flipping branch protection on real `main`.
- [ ] Empirically confirm Copilot CLI's update-detection mechanism (version
      string diff vs. semver-aware) before relying on assumptions about
      staged rollout.
- [ ] Simulate one full hotfix cycle end-to-end (fork LKG → patch → cherry-pick
      back) before depending on it during a real incident.
- [ ] Simulate one rollback (revert generated commit, re-tag) and confirm CI's
      non-incremental-update guard actually blocks a bad subsequent promotion.
- [ ] Confirm the auto-updater coverage fix: every harness worktree that
      depends on `copilot-extensions-harness` picks up its updates on the
      same cadence as `agent-*` plugins.

## Proposal

_Pending — Phase 1 design work will produce concrete tool choices and
generator contract details here or in a linked sub-doc._

## Journal

### 2026-09-22 — Kickoff
- Effort created from a two-round design conversation (see Request). Opened
  umbrella issue ThomasMichon/copilot-extensions#3336, linked
  ThomasMichon/copilot-extensions#182 as directly-related prior art.
- Confirmed current version-bump mechanics against the live repo
  (`tools/check-version-bump.py`, CONTRIBUTING.md § Release & Versioning,
  AGENTS.md § Version Bump) to ground Phase 2's retirement step.
- Next: begin Phase 1 — tool evaluation (beachball vs. alternatives) and the
  generator's identity-case proof against current `main`.

### 2026-09-22 — Capture correction
- Operator asked for a capture-validation pass; found and fixed real gaps:
  round-1 Request was only paraphrased in the umbrella issue, not preserved
  verbatim in this file — now quoted verbatim above. The rollback flow was
  missing the operator's explicit "pause CI" step (Phase 4). The Phase 3/6
  promotion trigger had been softened into an open question when the
  operator stated it as a decision (CI-success-triggered, not scheduled) —
  corrected and demarcated the remaining open follow-up as
  agent-recommended.
- Also demarcated the one Plan item (Copilot's update-detection semantics)
  that originated from the agent's own round-1 analysis and was never
  explicitly re-confirmed by the operator, per the same request.
- Prompted a durable process fix: updated the `planning-efforts` skill
  itself (in this repo, `plugins/efforts/skills/planning-efforts/SKILL.md`)
  to require validating an effort capture against the operator's actual
  words after every README write, demarcating agent-recommended content, and
  introducing an `inception-transcript.md` sidecar for accumulated
  multi-round verbatim input once it would otherwise dominate the README.

### 2026-09-22 — Phase 1 driving session
- **Auto-updater coverage gap: investigated, no code fix needed.** Read
  `agent-worktrees`' `_registered_plugin_targets` /
  `_update_registered_plugins`; the payload-refresh sweep already covers
  every enabled plugin from every registered anchor (not filtered to
  `agent-*`), so `copilot-extensions-harness` is already swept on this
  machine. Closed the Plan item; residual risk is operational (someone must
  run `update`), already covered by Phase 5.
- **Materialization generator: real progress, plus a live bug found.**
  Confirmed the repo's existing `sync-installer-engine.py`-style
  canonical -> copy pattern, then found the largest vendoring surface
  (`libs/<lib>` shared Python libs) had no syncer at all — only a
  copies-vs-copies verifier. Shipped `tools/sync-vendored-libs.py` with
  `--check` / `--restore-canonical` / `--materialize` modes, gated by
  version ordering so `--materialize` can never silently push a stale
  canonical down over newer copies (8 passing tests,
  `tools/test_sync_vendored_libs.py`). Running `--check` against the real
  repo surfaced genuine drift: `libs/ssh-manager` was missing an entire
  module and 9 dev-versions stale relative to its real vendored copies;
  `libs/credential-relay` similarly drifted. Filed
  ThomasMichon/copilot-extensions#3361 rather than fixing it inline, to keep
  this PR's diff reviewable; `--restore-canonical` is the fix, left as a
  follow-up.
- **beachball-fit finding:** confirmed (via beachball's own docs) it hard-requires
  a `package.json` per versioned package. This repo has none — recommend
  keeping beachball's changefile UX but implementing a native Python
  accumulator instead of depending on the real npm package. Flagged as
  agent-recommended, not yet operator-confirmed.
- Deferred (not risked without a scratch environment): empirically confirming
  Copilot CLI's update-detection semantics. No safe way to test this against
  the live global marketplace without a disposable scratch plugin/fork;
  folding it into the Phase 3 dry-run validation instead, which already needs
  a scratch environment.
- Next: operator confirmation on the beachball-fit call, then build the
  version-accumulation step and the local preview CLI on top of
  `sync-vendored-libs.py`'s pattern.

### 2026-09-23 — Native changefile tool
- Operator confirmed: build a native, agent-friendly, beachball-*inspired*
  Python tool rather than depending on the real npm package (beachball was
  only ever meant as a well-known reference point, not a hard dependency).
- Shipped `tools/changefile.py` + `tools/accumulate_bumps.py` (see Plan).
  This is the last Phase 1 item with no open design question; remaining
  Phase 1 work (preview CLI, dev-slot override, CONTRIBUTING/AGENTS.md
  draft) can now build directly on `sync-vendored-libs.py` +
  `accumulate_bumps.py` without further operator input.
- Next: the local "preview a release" CLI (compose `sync-vendored-libs.py
  --materialize` + `accumulate_bumps.py --dry-run` into one preview command),
  then the dev-slot local override design.

### 2026-09-23 — Preview CLI + dev-slot override
- Shipped `tools/preview_release.py` and `tools/dev_slot.py` (see Plan).
  Only two Phase 1 items remain: the CONTRIBUTING.md/AGENTS.md draft, and
  empirically confirming Copilot CLI's update-detection semantics (still
  deliberately deferred to Phase 3's dry-run, per the earlier Journal entry
  — no safe scratch environment for it yet).
- Self-caught, before landing, a real design bug: the first `preview_release`
  draft would have run the materializer against the real checkout as a
  subprocess, silently writing real vendored-lib changes as a side effect of
  building a "preview." Fixed by loading `sync-vendored-libs.py`'s helpers
  in-process and scoping every write to the scratch preview copy; verified
  with a live before/after `git status` smoke test against a plugin with
  known-drifted libs.
- Deliberately did not exercise `dev_slot.py install`/`clean` against this
  machine's own real `~/.copilot/installed-plugins` — too risky to mutate a
  live, currently-loaded plugin tree from within this session. Fully covered
  by tests against fake target roots instead.

### 2026-09-23 — Superseded discovery: withdrew tools/dev_slot.py
- While diagnosing a CI "guards + lint" failure on PR #3380 (traced to an
  unrelated, pre-existing baseline break on `main` — see below — not caused
  by this effort), found `ThomasMichon/copilot-extensions#3376` had just
  merged the "mutable dev slot" pattern: a proper `versions/dev/`
  runtime-slot primitive with claim-file GC protection, built into
  `libs/versioned-runtime` itself. This resolves the same Phase 1 item my
  `tools/dev_slot.py` was built for, more correctly. Removed
  `tools/dev_slot.py` + its tests from PR #3380 before landing; kept
  `tools/preview_release.py` (a distinct, complementary concern). Recovered
  cleanly from a self-inflicted `git stash pop` mishap while investigating
  (accidentally popped an unrelated stash entry belonging to a different
  worktree, sharing this repo's stash ref; `git reset --hard HEAD` restored
  a clean state with no damage to that other worktree's stash).
- **Separately confirmed the CI failure itself is pre-existing on `main`,
  not introduced by this effort's PRs**: `check-version-consistency.py`
  (worktree-manager version skew) and `check-module-size.py` (several
  `versioned_runtime.py` copies now over their grandfathered line-count
  ceiling — plausibly from #3376's own +174-line change) both fail
  identically against `origin/main` directly, with none of this effort's
  files in the diff. Not fixing this here (out of scope, unrelated,
  someone else's baseline to repair) — but it currently blocks *any* PR's
  "guards + lint" required check from going green, including PR #3380.
  Flagging for operator awareness rather than merging around it.
