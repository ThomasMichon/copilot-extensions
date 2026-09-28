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
        and `pr-complete`'s post-squash-merge realignment (`pr_complete.py`)
        each independently re-verify content is already confirmed on
        upstream (via ancestry or patch-id/tree equivalence) *before*
        rebasing or hard-resetting anything — never on assumption.
      - The remaining `reset --hard` call sites (`git_ops.squash_branch`,
        `pr_ops._rollback`) are squash/rollback-with-backup-ref mechanisms
        that restore to a *pre-operation* commit on failure, not paths that
        discard real unmerged work.
      **Conclusion:** the policy this phase set out to formalize was already
      correctly implemented in code — the only real gap was the `strategy`
      **config value** silently resolving to `detach` by accident (config
      default + duplicate-key bugs), both fixed in #4374. No `keep-alive`
      worktree can currently reset HEAD off unmerged, unowned-PR content.
      **Phase 2 addendum:** since this audit, the claim gate below now ALSO
      runs ahead of `_pr_finalize_precondition` for any worktree holding a
      confirmed-open PR claim, so even the `detach` opt-out's own "open PR on
      the remote is early-ok" branch is only ever reached after the PR
      merges or an operator explicitly abandons the claim.
- [x] Where a gap exists, add an explicit guard (not just documentation)
      that refuses a HEAD-reset-past-unmerged-commits operation unless
      `strategy == "detach"`, naming the blocking reason. **No code gap
      found** (see above) — the existing `_pr_finalize_precondition` branch
      already names the blocking reason correctly for `keep-alive`
      ("tracked PR is not merged and no feature branch is on '{remote}'" /
      "In 'keep-alive' mode finalize verifies only alignment with
      {upstream}"). Phase 2's claim gate adds a second, independent layer
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
  `reset --hard` call sites). Conclusion: the "never reset HEAD off unmerged
  commits except explicit `detach`" policy was **already correctly
  implemented in code** end-to-end; the only real defect was the `strategy`
  config value silently resolving to `detach` by accident, already fixed in
  #4374. No new code guard was needed.
- Found and fixed real doc drift while documenting the audit:
  `references/pr-workflow.md` stated "finalize is decoupled from merge"
  universally, when that was only ever true under `detach`. Corrected both
  the workflow-step summary and the "Finalizing a PR-mode worktree" section
  to state the actual `keep-alive`/`detach` split.

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
