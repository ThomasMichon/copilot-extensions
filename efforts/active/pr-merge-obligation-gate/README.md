# PR-Merge Obligation Gate

- **Slug:** `pr-merge-obligation-gate`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase PRs against `dev`
- **Created:** 2026-09-27
- **Status:** In Progress (Phases 1-3 core landed; operator-authorization
  follow-up deferred, see Plan)
- **Vision:** `visions/agent-fabric` §§ `resource-claims` / `resource-accountability`
  — extends the `worktree-finality-and-obligations` effort's implementation of
  that vision (`efforts/2026/08/28 worktree-finality-and-obligations/`, Done)
  to a resource kind it registered but never wired up: the already-reserved
  `pr` `ResourceKind` (`tracking.py`'s `ResourceKind` literal). Governing
  pattern: `docs/patterns/README.md` Design Principle 0 (architectural change
  reconciles to the vision) and the resource-accountability release-gated-on-
  settlement principle it documents.
- **Umbrella issue:** #4375
- **Sub-issues:** _none yet — filed as work is scoped per phase_

## Guiding Intent

In a fully-agentic workflow, only the agent that opened a PR is positioned
to be accountable for it — and that accountability is scoped to its
worktree's lifetime. `finalize` currently lets a worktree tear itself down
while its PR is still open, based solely on the `pr.strategy` config value.
That single defense already failed once for real (a downstream repository's PR and
siblings): a duplicate config key silently shadowed the safe `keep-alive`
value with `detach`, and an *unset* `strategy` used to silently default to
`detach` too (fixed separately in copilot-extensions #4374). This effort
adds structural, config-independent defenses so a dangling, unowned open PR
becomes structurally hard to produce, not just correctly-configured-away.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving worktree | Design + implementation | this worktree |

## Coordination

- **Topology:** shared feature branch per phase, independent PRs.
- **Host (owns PRs):** the driving worktree for this effort.
- **Delegates:** none currently.
- **Handoff:** n/a (single participant for now).

## Context

### Where this came from

Discovered while investigating why a downstream repository's open PR (and 10 sibling
open PRs) had no owning worktree left on any reachable machine: the worktree
that authored #4328 had `finalize`d immediately after `create-pr`, per PR
mode's `detach` strategy — the mode the docs themselves call "the rare
opt-out" requiring operator approval, but which was silently in effect here
because `copilot-extensions`'s own `.agent-worktrees/config.yaml` had no
explicit `strategy` key (fixed in #4374's fallback-default flip) **and**,
after that same PR's own review caught it, a *second*, later, duplicate
`strategy: detach` key elsewhere in the same file that silently overrode an
explicit `keep-alive` under YAML's last-key-wins rule (also fixed in #4374).

That fix (#4374) closes the *default-value* and *duplicate-key* failure
modes. It does **not** close the general case: any repo, or any future
config edit, can still set (or silently regress to) `strategy: detach`, and
nothing structurally prevents a worktree from finalizing while its own PR
sits open and unowned. This effort adds the structural defenses so the
config value is a preference, not the *only* thing standing between an
opened PR and permanent orphanhood.

### Prior art to build on, not reinvent

- `plugins/agent-worktrees/src/agent_worktrees/obligations.py` — the
  existing resource-obligation vocabulary (`active` / `at-rest` / `released`
  / `abandoned`) already used for CodeSpaces, containers, and bridge
  sessions. `pr` is already reserved in `tracking.py`'s `ResourceKind`
  literal as a placeholder; this effort's Phase 2 wires up that **existing**
  kind, riding the same vocabulary, not a parallel mechanism.
- `finalize.py`'s `_assert_obligations_settled` — the existing gate that
  blocks finalize on unsettled obligations; a PR-obligation slots in here.
- `efforts/2026/08/28 worktree-finality-and-obligations/README.md` — the
  effort that built the obligation model this one extends; read its Journal
  for the design rationale on `active`/`at-rest`/`released`/`abandoned`
  before inventing new disposition semantics.
- `worktree` skill's `references/pr-workflow.md` § "Finalizing a PR-mode
  worktree" — the current, by-design "finalize is decoupled from merge"
  contract that Phase 1/2 below narrow (not remove — `detach` remains a
  legitimate, operator-approved opt-out).

## Request

Operator's ask, verbatim (2026-09-27, following the #4374 investigation):

> Let's...rework this. The original guidance was built around the idea of a
> PR being a durable *end result*. But in fully-agentic workflows, only the
> agent which created a PR can be accountable to it, and the agent is bound
> to the worktree lifetime. So finalizing a worktree without merging a PR
> leaves a dangling PR, which never makes it anywhere. Any agent which would
> sweep stale PRs would have no context on the change beyond the PR's own
> description (which admittedly should point at an effort, and said effort
> should mention the reasoning).
>
> We have two defenses against this:
> 1. When a worktree makes a PR, the worktree still has commits on top of
>    master/dev. This should block the worktree from finalizing. It would
>    require the agent to *reset* the branch to HEAD *without* merging the
>    PR in order to finalize, and we should expressly forbid agents from
>    doing this unless the PR "mode" is "detach". We might have a bug where
>    PR mode is "detach" for copilot-extensions, either intrinsically or in
>    a downstream repository, and that could be an issue (it shouldn't be the default
>    behavior)
> 2. When a worktree makes a PR, the PR tools should make a claim on that PR
>    on behalf of the worktree. The claim can only be released intentionally
>    or via PR merge. Finalization attempts should detect that PR as still
>    open and block the release. Since claims chain, an agent worktree
>    should be blocked from finalization if the PR is still open.
>
> A final defense should be agent-guidance, which should provide
> instructions telling all agents to be thorough about driving all PRs they
> create, either directly or via downstream worktrees or agents, through to
> merge. Only the operator may countermand that directive and ask a
> worktree to release its claim on an open PR, reset HEAD, and abandon the
> claim.

Defense 1 (the `pr.strategy` default bug) was diagnosed and fixed same-day,
directly, ahead of this effort — see copilot-extensions #4374 (merged).
This effort covers what remains: **defense 2** (the structural obligation
gate) and **defense 3** (the agent-guidance instruction) below, plus
formalizing defense 1's *policy* (never reset HEAD off unmerged commits
except under an explicit, operator-approved `detach`).

## Plan

### Phase 1 — Formalize the reset-forbidding policy (defense 1's policy half)

_(agent-recommended: defense 1's *mechanism* — the fail-safe default — is
already fixed via #4374. This phase is the remaining *policy* half: making
"never reset HEAD off unmerged commits except under explicit detach"
explicit and enforced, not just implied by the default.)_

- [x] Audit `finalize.py`/`sync`/`git_ops.py` for any path that can move
      `worktree/<id>` off unmerged commits without either (a) the PR having
      merged, or (b) `pr.strategy == "detach"` explicitly. Confirm today's
      actual behavior matches the stated contract before changing anything.
      **Findings:** every HEAD-moving path was audited and is already
      correctly gated:
      - `sync`/`fast_forward_worktree` (`git_ops.py`) refuses whenever the
        branch is `ahead > 0` of upstream — it never touches unmerged work,
        full stop.
      - `finalize`'s direct-push (non-PR) path requires
        `_is_content_on_upstream` before proceeding; unmerged work always
        blocks with "Unmerged work detected."
      - `finalize`'s PR-mode gate (`_pr_finalize_precondition`) only accepts
        an **open, unmerged** PR as sufficient under `strategy == "detach"`
        — the deliberate, already-correctly-scoped opt-out. Under
        `keep-alive` it requires actual merge or upstream-equivalent
        content; an open PR alone is refused ("tracked PR is not merged").
      - `finalize`'s pointer-reconciliation pass (`_reconcile_merged_pointers`)
        rebases non-destructively first (drops only already-upstream/empty
        commits, preserves genuinely new ones by construction) and falls
        back to a hard reset only after independently re-confirming content
        is already on upstream; `pr-complete`'s post-squash-merge
        realignment (`pr_complete.py`) follows the identical shape.
      - The remaining `reset --hard` call sites (`git_ops.squash_branch`,
        `pr_ops._rollback`) are squash/rollback-with-backup-ref mechanisms
        that restore to a *pre-operation* commit on failure, not paths that
        discard real unmerged work.
      **Conclusion (corrected across 16 review rounds on PRs #4388/#4400 —
      see Journal for the full sequence):** a real code gap *was* found,
      beyond the `strategy` config-value bug already fixed in #4374.
      `_pr_finalize_precondition`'s merged-PR fast path (both its
      upstream-content shortcut and its authoritative "PR merged" check)
      could certify a `keep-alive` worktree safe to prune while it still
      carried commits beyond its tracked PR's merged head — a worktree that
      kept working after merge (the normal keep-alive pattern), a stale
      local snapshot branch under the `snapshot` head scheme, an empty/
      revert commit whose net tree happened to match upstream, a legacy
      merged record missing `head_sha`, a false-positive after legitimate
      `pr-complete`/`sync` realignment, and an unconditionally-trusted
      checked-out ref (an unrelated branch, or a detached `HEAD` behind the
      tracked branch) all had to be independently resolved. Separately,
      `validate_and_finalize` itself lacked a **dirty-working-tree guard in
      PR mode** — a modified or untracked file at an otherwise-safe merged
      head would have been silently destroyed alongside the worktree. Fixed
      via `finalize_open_pr_gate.py`'s `resolve_precondition_ref` (resolves
      the LIVE checked-out ref only when neither tracked branch name carries
      commits beyond it, never a frozen snapshot or an unvalidated drift
      target), `content_exceeds_merged_head` (excludes commits already
      reachable from upstream, not just the pre-merge head, so a legitimate
      post-squash realignment never false-positives),
      `upstream_match_is_trustworthy`, `dirty_worktree_error`, and
      `merged_pr_block_message` (distinguishes a confirmed later commit from
      an unverifiable merge boundary) — all fail-closed on any inconclusive
      lookup. See `TestContentExceedsMergedHeadFailsClosed`,
      `TestMergedPrBlockMessageDistinguishesInconclusive`,
      `TestPrMergeStatusIndeterminateVsUnmerged`, and the full
      `test_precondition_*`/`test_finalize_refuses_dirty_*`/
      `test_finalize_reruns_precondition_*` regression-test family across
      `test_finalize_precondition.py` and `test_pr_ops.py` (exact count
      deliberately not pinned here -- it has repeatedly drifted stale across
      rounds; run the suite for the current count).
      **Phase 2 addendum:** since this audit, the claim gate below now ALSO
      runs ahead of `_pr_finalize_precondition` for any worktree holding a
      confirmed-open PR claim, so even the `detach` opt-out's own "open PR on
      the remote is early-ok" branch is only ever reached after the PR
      merges or an operator explicitly abandons the claim.
- [x] Where a gap exists, add an explicit guard (not just documentation)
      that refuses a HEAD-reset-past-unmerged-commits operation unless
      `strategy == "detach"`, naming the blocking reason. **A real gap was
      found and fixed** (see above) — `_pr_finalize_precondition` now names
      the "tracked PR merged, but further commits exist beyond its head"
      case explicitly, distinct from the pre-existing "not merged yet"
      message. Phase 2's claim gate adds a second, independent layer
      ahead of it (see addendum above), not a replacement.
- [x] Document the guard in `references/pr-workflow.md` and this effort's
      Journal. **Found and fixed real doc drift while documenting:**
      `pr-workflow.md`'s "Finalizing a PR-mode worktree" section (and its
      step-7 summary earlier in the same file) stated "finalize is
      decoupled from merge" and "the PR does not need to be merged first"
      as if universally true — this was only ever accurate for `detach`.
      Rewrote both sections to state the actual `keep-alive`/`detach` split
      explicitly, matching the code audited above.

### Phase 2 — Wire up the existing `pr` claim kind

_(Correction from Copilot review on the plan PR: `tracking.py`'s
`ResourceKind` literal already reserves `"pr"` as a placeholder — "the rest
are placeholders the ledger view already understands so later phases can
journal them without a schema change." This phase defines that existing
kind's PR identity and lifecycle; it must not introduce a second,
incompatible claim type alongside it.)_

- [x] Define the `pr`-kind claim's identity (provider + repo + PR number,
      matching how `PRRecord` already identifies a tracked PR) and lifecycle
      within the existing vocabulary: what puts it into `active` (**not**
      merely a `PRRecord` being saved — see the numberless-`creating`-state
      design question below), what moves it to `at-rest`/`released` (the PR
      merges, or the operator explicitly releases it), and how it
      round-trips through the existing claim ledger
      (`tracking.ResourceClaim`) the same way CodeSpace/container/bridge
      claims already do. Landed as `pr_ops._pr_claim_ref`/`_ensure_pr_claim`/
      `_release_pr_claim`.
- [x] Wire `create-pr` to place the claim; wire `_assert_obligations_settled`
      (or a new gate alongside it) to check it before `finalize` proceeds.
      `_assert_obligations_settled` needed **no changes at all** — it
      already blocks on any unsettled claim of any kind (only `session` is
      excluded); Phase 2 only needed to make sure a `pr` claim actually gets
      **created**. `_open_via_provider` claims immediately on provider-
      confirmed open; `finalize` itself now also calls
      `pr_ops._reconcile_active_pr` before the gate runs (closes the
      out-of-band-`set-pr` gap — see design questions below).
- [ ] Explicit, attributable release path: an operator-directed
      `--abandon`-style flag ... — **not done as its own dedicated verb.**
      The existing general `finalize --abandon --handoff-to <recipient>`
      escape hatch already releases ANY unsettled claim (including a `pr`
      one) attributably (it's logged + requires a named recipient); a
      *PR-specific* abandon flag distinct from that path, plus real
      operator-vs-agent authorization (see the design question below), is
      deliberately deferred — this repo has no invoker-identity mechanism at
      this layer to enforce it cryptographically, and inventing one is a
      separate, larger effort. Tracked as a follow-up, not silently dropped.
- [x] Tests: claim created on an actually-opened PR (not a numberless
      `creating` record), blocks `finalize` while open, auto-releases on
      merge observed through the shared `_reconcile_active_pr` path (the one
      every other PR-workflow verb already funnels through) and through the
      sweep self-heal path, is NOT released on an unmerged close. See
      `tests/test_pr_ops.py::TestPrClaimHelpers`/`TestReconcileActivePrSelfHeal`,
      `tests/test_finalize_gate.py::test_active_pr_claim_blocks_finalize_regardless_of_strategy`,
      `tests/test_obligation_sweep.py::test_sweep_settles_merged_pr_claim_as_released_not_abandoned`.
      **Not covered:** an attributable/rejected-for-non-operator release
      path (deferred with the bullet above), and a full live-git
      `validate_and_finalize` end-to-end run (the unit-level coverage above
      already exercises every individual seam).

#### Design questions to resolve before implementation (from Copilot review)

- **Claim timing vs. `create-pr` failure modes.** ... **Resolved:**
  `_ensure_pr_claim` requires both `pr.number is not None` AND
  `pr.state == "open"` (a real provider-observed value, never a bare
  default) — a numberless/`creating`/failed/`--no-open` record is never
  claimed.
- **Manual association via `set-pr`.** ... **Resolved:** `_ensure_pr_claim`
  is deliberately NEVER called directly from `set_pr`/`_set_pr_locked` (which
  persists state with no provider read) — only from `_open_via_provider`
  (a real provider response) and `_reconcile_active_pr` (a real provider
  read). `finalize` now calls `_reconcile_active_pr` itself before the
  obligation gate runs, so an out-of-band `set-pr`'d PR gets its first real
  provider confirmation — and its claim — at the latest possible/soonest
  necessary moment: right before finalize would otherwise check the ledger.
- **Unify every merge-observation path.** ... **Resolved:** `_reconcile_active_pr`
  is the one shared path (`create-pr`, `pr-ready`, `pr-status`, `pr-nudge`,
  the Picker's background sweep, `pr_reconcile.py`, and now `finalize`
  itself all funnel through it) and now settles the claim to `released` on
  a confirmed merge. The independent sweep/self-heal crash-recovery path
  (`sweep.py`'s `pr_merged` + `tracking_claims.sweep_abandoned_obligations`)
  is fixed to settle a `pr`-kind claim as `released` (a clean hand-back)
  instead of the generic `abandoned` it used for every other kind.
- **Operator-only enforcement, not just a CLI flag.** **Not resolved** —
  deferred with the abandon-path bullet above. This needs its own design
  (an actual invoker-identity/provenance signal this layer doesn't have
  today), not a bolt-on flag that would look enforced without being so.
- **Generic `claims release`/`claims settle` are an existing bypass.**
  **Not resolved** — same reason as above (gating them meaningfully needs
  the same authorization primitive). Still an open side door; noted here so
  it isn't rediscovered as a surprise later.

### Phase 3 — Agent-guidance instruction

- [x] Add or extend a static-fallback `.instructions.md` (matching this
      repo's own pattern — see `plugins/*/instructions/*.md`) directing
      every agent to drive every PR it opens, directly or via a downstream
      worktree/agent, through to merge — and that only the operator may
      direct releasing that claim, resetting HEAD, and abandoning it.
      Extended `plugins/agent-worktrees/instructions/head-claim-fallback.instructions.md`
      (already the ambient, always-loaded obligation-gate fallback) with a
      new "If you opened a pull request" section, rather than adding a
      redundant sibling file.
- [x] Cross-reference from the `worktree` skill (`pr-workflow.md`'s
      "Default conduct: drive every PR you open through to merge" section
      already states this in prose; this phase makes it load-bearing
      ambient guidance, not just skill prose an agent might not load).

## Validation Plan

- [x] A worktree that opens a PR and attempts `finalize` before merge is
      **blocked** by the new obligation gate even when `pr.strategy` is
      unset or misconfigured to `detach` by mistake (regression coverage
      for the exact failure class that produced #4328 and siblings). See
      `test_finalize_gate.py::test_active_pr_claim_blocks_finalize_regardless_of_strategy`.
- [x] A worktree cannot reset `worktree/<id>` off unmerged commits without
      either the PR merging or an explicit, attributable `detach`/abandon
      action. Covered structurally: the claim gate runs before
      `_pr_finalize_precondition`'s `detach`-only early-ok branch can ever be
      reached (see Phase 1's finding above); `--abandon --handoff-to` is the
      attributable escape hatch, pre-existing and unchanged.
- [x] Once the PR merges, the obligation auto-settles and `finalize`
      proceeds without any manual claim release. See
      `TestReconcileActivePrSelfHeal::test_merge_releases_pr_claim` /
      `test_zombie_heal_to_merged_also_releases_claim` and
      `test_sweep_settles_merged_pr_claim_as_released_not_abandoned`.
- [ ] An operator-directed release (abandon the PR claim, reset HEAD) works,
      is attributable in the claim ledger, and is refused without explicit
      operator direction. **Partially covered**: the pre-existing generic
      `--abandon --handoff-to` path already does the first two (attributable,
      logged); "refused without explicit operator direction" needs the
      deferred authorization primitive from Phase 2's design questions — not
      yet implemented or tested.
- [x] Existing `worktree-finality-and-obligations` obligation-gate tests
      (CodeSpace/container/bridge-session claims) show no regression. Full
      `test_finalize_gate.py`/`test_obligation_sweep.py`/`test_pr_ops.py`/
      `test_sweep.py`/`test_finalize_precondition.py` suites pass (274 tests).

## Proposal

_Pending._

## Journal

### 2026-09-27 — Kickoff
- Effort created following the #4374 investigation and the operator's
  three-defense design. Defense 1's mechanism (fail-safe `pr.strategy`
  default) already shipped same-day via #4374; this effort covers the
  remaining reset-guard policy plus defenses 2 and 3.
- Filed umbrella issue #4375.

### 2026-09-28 — Phase 1 done
- Audited every HEAD-moving code path (`sync`/`fast_forward_worktree`,
  `finalize`'s direct-push and PR-mode gates, `_reconcile_merged_pointers`,
  `pr-complete`'s post-merge realignment, and the remaining squash/rollback
  `reset --hard` call sites). First-pass conclusion (merged as #4388 before
  the real gap below was found, then corrected in this same entry once
  #4400 landed): "already correctly implemented, no code gap" — **not
  quite true**; see the corrected account below.
- Found and fixed real doc drift while documenting the audit:
  `references/pr-workflow.md` stated "finalize is decoupled from merge"
  universally, when that was only ever true under `detach`. Corrected both
  the workflow-step summary and the "Finalizing a PR-mode worktree" section
  to state the actual `keep-alive`/`detach` split.
- **The operator merged #4388 directly** (non-blocking `COMMENTED` verdict,
  entirely within their `pr-self-merge` rights) while review was still in
  progress and before the real gap below had landed on it — so #4388's
  merged content carried the first-pass "no code gap found" narrative.
  Carried forward as a direct follow-up, #4400, landing the real fix and
  correcting this README's narrative to match (see the Plan section above
  for the final, corrected account).
- **#4400 went through six review rounds, each catching a genuinely
  distinct real gap** in `_pr_finalize_precondition`'s merged-PR handling:
  (1) the fast path trusted `_pr_is_merged` alone with no check for commits
  added to the SAME keep-alive worktree after its PR merged; (2) the
  content ref used for that check preferred a frozen local snapshot branch
  under the `snapshot` head scheme instead of the live checkout; (3) the
  pre-existing upstream-content fast path had the identical stale-snapshot
  hole, running *before* the merged-head check and bypassing it entirely;
  (4) that same fast path could also be fooled by an empty/revert commit
  whose net tree matched upstream despite being a real commit past the
  merged head; (5) a legacy merged record missing `head_sha` was wrongly
  treated as "nothing to compare, trust it" instead of "can't verify,
  don't"; (6) the `head_sha..content_ref` range itself was too strict — it
  counted every commit reachable from `content_ref` that wasn't an ancestor
  of the *pre-squash* `head_sha`, including a legitimate post-merge
  `pr-complete`/`sync` realignment (a squash commit or forward rebase that
  is safely on upstream but not literally descended from `head_sha`),
  producing a false "further commits" block. Each was fixed with a
  dedicated regression test (six `test_precondition_*` tests plus
  `TestContentExceedsMergedHeadFailsClosed`'s four fail-closed unit tests),
  and confirmed by temporarily reverting each fix locally to prove the
  corresponding test actually fails without it. Final design:
  `finalize_open_pr_gate.py`'s `resolve_precondition_ref` (LIVE checked-out
  ref, never a snapshot), `content_exceeds_merged_head` (excludes commits
  reachable from *either* `head_sha` *or* `upstream`), and
  `upstream_match_is_trustworthy`, shared by both the fast path and the
  authoritative merged-PR check.
- **Reconciling with #4406 (Phase 2/3, merged concurrently):** the operator
  independently widened `finalize.py`'s module-size ceiling (2161 → 2183)
  as part of that PR's own `validate_and_finalize` addition; #4400 landed
  cleanly within that same widened ceiling, then extracted a second round of
  new logic (below) to the sibling module to stay within it.
- **A seventh review round caught two more, both worth calling out
  explicitly for how different in kind they are from rounds 1-6:** (1) a
  genuine remaining safety gap -- `_pr_finalize_precondition` only ever
  inspects *commits*; a worktree with a **modified or untracked file** at an
  otherwise-safe merged head passed every check above and would then be
  destroyed by `validate_and_finalize`'s `git worktree remove --force`,
  discarding that file. Fixed with a dedicated dirty-tree guard
  (`finalize_open_pr_gate.dirty_worktree_error`) run before
  `_pr_finalize_precondition` in PR mode, mirroring the existing direct-push
  path's own dirty check. Regression test:
  `test_finalize_refuses_dirty_worktree_at_merged_head` (confirmed to fail
  -- and destroy the uncommitted file -- without the fix). (2) a **false
  positive**, not a safety hole: `content_exceeds_merged_head`'s
  `head_sha..content_ref` range counted every commit reachable from
  `content_ref` that wasn't a literal ancestor of the *pre-squash*
  `head_sha` -- including a legitimate `pr-complete`/`sync` realignment
  (the squash commit itself, unrelated to `head_sha`'s own history, but
  safely on `upstream`), wrongly blocking finalize. Fixed by excluding
  commits also reachable from `upstream`, not just `head_sha`. Regression
  test: `test_precondition_passes_after_pr_complete_realignment_past_squash`
  (confirmed to fail without the fix). Both new checks extracted to
  `finalize_open_pr_gate.py` (`merged_pr_block_message`,
  `dirty_worktree_error`) to stay within the module's ceiling, matching the
  established pattern from prior rounds.
- **An eighth review round caught the sharpest gap yet in
  `resolve_precondition_ref` itself:** it trusted *any* currently-checked-out
  branch unconditionally as the safety ref -- but `validate_and_finalize`
  deletes the TRACKED branch (`record.branch`/`worktree/<id>`) on cleanup
  regardless of what's checked out. A checkout moved to an unrelated branch
  that happened to be clean and already matching upstream (accidentally or
  otherwise) would make the fast path trivially pass, while the real,
  still-unmerged `worktree/<id>` commits got deleted anyway. First fix
  attempt: only trust the live checkout when it is one of the two
  legitimate branch names (`worktree/<id>`, or the tracked `feature` branch
  under the legacy flow), or a genuinely detached `HEAD`. Regression test:
  `test_precondition_blocks_when_unrelated_clean_branch_checked_out`
  (confirmed to fail without the fix).
- **A ninth review round caught that the detached-HEAD branch of that same
  fix was itself incomplete, plus one more, unrelated message-accuracy
  issue:** (1) unconditionally trusting a detached `HEAD` had the identical
  hole as the unrelated-branch case -- detaching to an ancestor commit
  (matching upstream) while `worktree/<id>` itself still carried a later,
  unmerged commit let the detached commit stand in for that later work.
  Fixed by unifying both cases under one rule:
  `_tracked_branch_ahead` checks whether EITHER tracked branch name
  (`feature` or `worktree/<id>`) carries commits beyond whatever is
  currently checked out (named branch or detached `HEAD`); only trust the
  checkout when neither does. Regression test:
  `test_precondition_blocks_when_detached_head_behind_tracked_branch`
  (confirmed to fail without the fix). (2) `merged_pr_block_message` only
  checked that `head_sha` was a non-empty *string* before claiming "carries
  further commits" -- but `content_exceeds_merged_head` also fails closed on
  an unresolvable `head_sha` (present but invalid), producing a misleading
  message that sends an operator hunting for a commit that may not exist.
  Fixed by re-verifying `head_sha` actually resolves (`ref_exists`), not
  merely being non-empty. Regression tests:
  `TestMergedPrBlockMessageDistinguishesInconclusive`'s two cases (confirmed
  the unresolvable-head_sha case previously reported the wrong message).
- **A tenth review round caught that round 8-9's own fallback path
  reintroduced rounds 2-3's original stale-snapshot hole:** when the current
  checkout can't be trusted (round 7-9's `_tracked_branch_ahead` rule) and
  falls back, that fallback had been delegating to
  `finalize._resolve_content_ref` -- which prefers the frozen `feature`
  snapshot BEFORE `worktree/<id>`, exactly the ordering rounds 2-3 fixed
  `resolve_precondition_ref` to avoid in the first place. Fixed: the
  fallback no longer delegates to `_resolve_content_ref` at all; it inlines
  its own `worktree/<id>`-before-`feature`-before-`HEAD` order directly.
  Regression test:
  `test_precondition_blocks_stale_snapshot_via_untrusted_checkout_fallback`
  (confirmed to fail without the fix).
- **An eleventh review round caught the deepest gap yet: every merged-head
  check (both the step-1 fast path via `upstream_match_is_trustworthy` and
  step-2's authoritative check) validated only ONE chosen ref, but
  cleanup unconditionally force-deletes EVERY tracked PR's local feature
  branch (`record.prs[*].branch`), not just whichever ref was picked as the
  live-content safety ref.** A merged PR whose `worktree/<id>` matched the
  merged head exactly could certify "safe to finalize" while a separate,
  still-tracked feature branch carrying its own later, unmerged commit sat
  right next to it -- cleanup would force-delete that branch's real work
  without ever having validated it. Fixed by introducing
  `content_exceeds_merged_head_any` (checks `content_ref` AND every ref
  `_cleanup_branch_refs` names) and wiring both the fast path and the
  authoritative check through it instead of the single-ref
  `content_exceeds_merged_head`. `merged_pr_block_message` was similarly
  widened to check every one of those refs (plus an explicit `upstream`
  resolvability check) before reporting "confirmed" rather than
  "unverifiable" -- fixing a second, related finding that the message could
  misreport an unresolvable-`upstream` case as a confirmed extra commit.
  Also added a dedicated `rev-list`-failure-via-unresolvable-`upstream` unit
  test (previously only reachable incidentally through an unresolvable
  `head_sha`) per a third, coverage-only finding. Regression tests:
  `test_precondition_blocks_merged_pr_extra_commit_on_tracked_feature_branch`
  and `TestContentExceedsMergedHeadFailsClosed::test_failed_rev_list_unresolvable_upstream`
  (both confirmed to fail without their respective fixes).
- **A twelfth review round caught that `upstream_match_is_trustworthy`'s
  missing-`head_sha` fail-closed check only consulted the LOCAL,
  possibly-stale `record.pr.state` field, not the same authoritative source
  `finalize._pr_is_merged` uses.** A provider-confirmed merge can predate a
  stale/failed local reconcile (`state` still `"open"`); in that case the
  fast path could certify a missing-`head_sha` record "trustworthy" purely
  because `state != "merged"` locally, even though the PR genuinely merged
  and its merge boundary is unverifiable. Fixed by threading `repo` into
  `upstream_match_is_trustworthy` and asking `finalize._pr_is_merged`
  (the network round trip happens only in this specific missing-`head_sha`
  case, never on the common fully-populated-record path). Also corrected a
  second finding: the comment above the PR-mode finalize branch still
  described the pre-obligation-gate contract ("safe to prune as soon as the
  feature branch is pushed -- the PR may still be open"), contradicting
  `assert_no_live_pr`'s actual refusal of any live tracked PR. Regression
  test: `test_precondition_blocks_provider_confirmed_merge_missing_head_sha`
  (confirmed to fail without the fix). Two further findings this round --
  "add regression tests for fail-closed guards" and "Draft to Active" -- were
  re-confirmed as already resolved by prior rounds (stale repeats, not
  reflected in "Resolved since last review" for reasons internal to the
  reviewer's own tracking), not further action.
- **A thirteenth review round caught round 12's own boolean collapsed an
  indeterminate lookup into a permissive result, plus a second legacy-name
  gap in the cleanup-ref set.** (1) `_pr_is_merged` (and round 12's new
  usage of it) returns `False` both for a *confirmed unmerged* PR and for a
  *provider/network error* on a genuinely tracked PR -- collapsing those two
  meant `not _pr_is_merged(...)` reported "trustworthy" on an outage too,
  exactly the unverifiable case this whole check exists to catch. Fixed by
  introducing a tri-state `pr_merge_status` (`True`/confirmed-merged,
  `False`/confirmed-NOT-merged -- including "no PR ever tracked", `None`/
  indeterminate -- a tracked PR whose provider call raised) in
  `finalize_open_pr_gate.py` (moved there rather than `finalize.py`, which
  had zero line budget left; `_pr_is_merged` there is now a thin delegating
  wrapper). `upstream_match_is_trustworthy` now trusts ONLY an exact `False`
  status. (2) `_cleanup_branch_refs` still only checked `worktree/<id>` plus
  PR feature branches -- but `validate_and_finalize`'s actual branch
  deletion resolves the tracked branch via `_worktree_branch`, which prefers
  `record.branch` (a legacy/non-canonical name) over `worktree/<id>` when
  set. Fixed by resolving the SAME tracked-branch name `_worktree_branch`
  would. Regression tests:
  `test_precondition_blocks_indeterminate_merge_status_missing_head_sha` and
  `test_precondition_blocks_merged_pr_extra_commit_on_legacy_record_branch`
  (both confirmed to fail without their respective fixes).
- **A fourteenth review round caught three further gaps: a TOCTOU race
  between precondition and cleanup, an incomplete-record misclassification,
  and a shared merge-boundary across unrelated PRs.** (1) The merged-head/
  dirty precondition ran only BEFORE `FinalizeLock` was acquired, with no
  re-check after -- a commit landing in that window between preflight and
  the destructive cleanup would be silently discarded. Fixed by re-running
  the SAME precondition (and dirty-tree guard) immediately after the lock
  is held, before cleanup proceeds. Regression test:
  `test_finalize_reruns_precondition_after_lock_acquired` (a monkeypatched
  `FinalizeLock.acquire` injects a race commit; confirmed the worktree was
  force-finalized -- discarding it -- without the fix). (2) `pr_merge_status`
  collapsed "no PR ever tracked" (neither `number` nor `repo` set -- nothing
  COULD have merged, correctly `False`) and "a PR record exists but is only
  PARTIALLY populated" (exactly one of `number`/`repo` present -- a broken/
  incomplete write, genuinely indeterminate) into the same `False` return.
  Fixed by only returning `False` when NEITHER field is present; exactly one
  present now returns `None`. Regression tests:
  `TestPrMergeStatusIndeterminateVsUnmerged`'s four cases (two confirmed to
  fail without the fix). (3) `_cleanup_branch_refs`/`content_exceeds_merged_
  head_any`/`merged_pr_block_message` validated every cleanup branch --
  including OTHER tracked PRs' own feature branches (`record.prs`) --
  against the ACTIVE PR's `head_sha` alone; a historical/parallel PR's own
  branch must be checked against ITS OWN `head_sha`, never a shared one that
  can mask real content via unrelated ancestry. Fixed by threading each
  entry's own `head_sha` through `_cleanup_branch_refs`'s now-`(ref,
  head_sha)` pairs. Regression test (deterministic, spies on
  `_extra_commit_count`'s call arguments rather than relying on a specific
  git-ancestor topology): `test_content_exceeds_merged_head_any_checks_each_
  pr_branch_against_own_head` (confirmed to fail without the fix).
- **A fifteenth review round caught round 14's own TOCTOU fix was still
  incomplete, plus one further gap in the confirmed-unmerged shortcut.**
  (1) Round 14's re-check ran once, immediately after the finalize lock was
  acquired -- but further code still ran AFTER that point and BEFORE the
  actual destructive removal (record reload, pointer reconciliation,
  live-session checks, process termination), leaving a residual window a
  commit could still land in. Fixed by moving the re-check to the LAST
  possible step, immediately before the destructive `git worktree remove`
  call (this narrows, but per the reviewer's own framing can never fully
  eliminate without a write-blocking hook the commit path itself would
  honor -- out of scope here). Regression test:
  `test_finalize_catches_race_commit_landing_during_process_termination`
  (races a commit in via a monkeypatched `procs.terminate_processes_under`;
  confirmed against a reconstructed round-14-only check position that it
  catches an EARLIER race but misses this LATER one, precisely demonstrating
  the improvement). (2) `upstream_match_is_trustworthy`'s missing-`head_sha`
  branch trusted ANY confirmed-NOT-merged status unconditionally -- but
  `assert_no_live_pr` permits a terminal CLOSED (rejected, never merged) PR
  through as not "live," while cleanup still force-deletes every tracked
  PR's own feature branch regardless of merge outcome. Fixed by adding
  `other_pr_branches_unreachable_from_upstream` (checked against `upstream`
  directly, since there is no merged head to compare against) for exactly
  this confirmed-NOT-merged case -- the worktree's own tracked branch still
  needs no extra check, since step 1's TREE-level match already covers it
  (squash-safe). Regression test:
  `test_precondition_blocks_closed_pr_with_unmerged_commits_on_other_tracked_branch`
  (confirmed to fail without the fix).
- **A sixteenth review round confirmed round 15's TOCTOU narrowing but
  identified `_reconcile_merged_pointers`'s own rebase as a further,
  reconciliation-internal drop point.** Reconciliation's rebase can itself
  silently DROP a commit whose patch git considers "already applied"
  upstream -- a race commit landing right before or during that call could
  vanish with no trace, before even round 15's last-moment check ever saw
  it. Considered (and rejected) fixing this via `git rebase --empty=keep
  --reapply-cherry-picks`: verified empirically this DOES stop the drop, but
  it would ALSO retain every ordinarily-redundant post-squash-merge commit
  as a permanent empty commit on `worktree/<id>` -- defeating
  reconciliation's whole point (the picker no longer reads a merged
  worktree as clean) for the COMMON case, not just the rare race. Landed
  instead the narrower, real fix available without that regression: the
  full precondition (dirty-tree guard + `_pr_finalize_precondition`) now
  ALSO re-runs immediately before `_reconcile_merged_pointers` is invoked
  (previously it ran only after lock acquisition and, per round 15, at the
  very last moment before removal) -- extracted into a single shared
  `finalize_open_pr_gate.pr_precondition_recheck` helper (moved out of
  `finalize.py`, which had zero line budget, mirroring the round-13
  precedent) to avoid duplicating the check body across all three call
  sites. This closes the WALL-CLOCK-significant portion of the window (the
  record reload / pointer reconciliation / live-session checks / process
  termination steps that run between lock acquisition and reconciliation)
  -- the literal same-instant "during reconciliation's own git calls"
  sliver remains an accepted residual, honestly documented rather than
  claimed fixed: by construction, a commit reconciliation's rebase actually
  drops there is patch-empty relative to upstream, so no unique file
  content is lost, only the commit object's own message/audit trail.
  Regression test: `test_finalize_rechecks_precondition_before_reconciliation`
  -- a deterministic call-order spy (not a full git-level race
  reconstruction, since reliably forcing git's "already applied" patch-id
  heuristic is version-fragile) proving the recheck runs strictly before
  reconciliation is invoked (confirmed to fail without the fix). The
  fail-closed "add regression tests"/"Draft to Active" findings continue to
  recur as stale repeats (see round 12's note); no further action.

### 2026-09-28 — Phases 2-3 core landed (concurrent with a "backup open-PR
gate", #4389, landed independently the same day -- see below)

- Landed the structural claim gate: `_open_via_provider` claims a PR the
  instant the provider confirms it open; `_reconcile_active_pr` (the one
  path every PR-workflow verb already shares) both ensures the claim on a
  confirmed-still-open read and releases it to `released` on a confirmed
  merge; `finalize` now calls that same reconcile before its (pre-existing,
  unchanged) generic obligation gate runs, closing the out-of-band `set-pr`
  gap. Fixed `sweep.py`'s crash-recovery self-heal path
  (`tracking_claims.sweep_abandoned_obligations`) to settle a merged `pr`
  claim as `released` instead of `abandoned` — the exact inconsistency
  flagged in this effort's own kickoff.
- Net effect: an open, unmerged PR now blocks `finalize` **regardless of
  `pr.strategy`** through the pre-existing generic obligation gate, once the
  new `pr`-kind claim exists on the ledger.
- Extended the ambient `head-claim-fallback.instructions.md` (Phase 3)
  rather than adding a new file, since it already carries the general
  obligation-gate fallback guidance this is one more case of.
- **Landed independently the same day, discovered on rebase:** #4389 added
  `finalize_open_pr_gate.py`, a SECOND, independent "backup" gate that
  live-re-reconciles every tracked PR (not just the active one) and refuses
  finalize on any still-open one directly off `record.prs`/`has_live_pr()`
  — deliberately not a replacement for the claim-ledger mechanism above, per
  its own docstring. The two now run back-to-back in `validate_and_finalize`
  (claim gate via `_assert_obligations_settled`, then the backup gate) —
  genuinely complementary, not duplicative: the claim gate is the
  structural, ledger-integrated defense (auto-releases on merge, composes
  with every other resource kind); the backup gate is a live, PR-record-
  direct re-check independent of whether a claim was ever correctly placed
  in the first place. No conflict to reconcile beyond this Journal entry and
  a straightforward rebase (both touched adjacent code in `finalize.py` but
  auto-merged cleanly).
- **Deliberately deferred** (design questions never fully resolved, tracked
  above rather than dropped): a PR-specific `--abandon`-style verb distinct
  from the general obligation abandon path, and any actual operator-vs-agent
  authorization primitive gating it (or the pre-existing generic `claims
  release`/`claims settle` side door) — this layer has no invoker-identity
  signal to enforce that boundary on today, and inventing one is its own,
  separate effort. Surfaced explicitly rather than silently narrowing scope.
