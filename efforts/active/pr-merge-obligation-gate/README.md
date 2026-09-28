# PR-Merge Obligation Gate

- **Slug:** `pr-merge-obligation-gate`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase PRs against `dev`
- **Created:** 2026-09-27
- **Status:** Draft
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

- [ ] Audit `finalize.py`/`sync`/`git_ops.py` for any path that can move
      `worktree/<id>` off unmerged commits without either (a) the PR having
      merged, or (b) `pr.strategy == "detach"` explicitly. Confirm today's
      actual behavior matches the stated contract before changing anything.
- [ ] Where a gap exists, add an explicit guard (not just documentation)
      that refuses a HEAD-reset-past-unmerged-commits operation unless
      `strategy == "detach"`, naming the blocking reason.
- [ ] Document the guard in `references/pr-workflow.md` and this effort's
      Journal.

### Phase 2 — Wire up the existing `pr` claim kind

_(Correction from Copilot review on the plan PR: `tracking.py`'s
`ResourceKind` literal already reserves `"pr"` as a placeholder — "the rest
are placeholders the ledger view already understands so later phases can
journal them without a schema change." This phase defines that existing
kind's PR identity and lifecycle; it must not introduce a second,
incompatible claim type alongside it.)_

- [ ] Define the `pr`-kind claim's identity (provider + repo + PR number,
      matching how `PRRecord` already identifies a tracked PR) and lifecycle
      within the existing vocabulary: what puts it into `active` (**not**
      merely a `PRRecord` being saved — see the numberless-`creating`-state
      design question below), what moves it to `at-rest`/`released` (the PR
      merges, or the operator explicitly releases it), and how it
      round-trips through the existing claim ledger
      (`tracking.ResourceClaim`) the same way CodeSpace/container/bridge
      claims already do.
- [ ] Wire `create-pr` to place the claim; wire `_assert_obligations_settled`
      (or a new gate alongside it) to check it before `finalize` proceeds.
- [ ] Explicit, attributable release path: an operator-directed
      `--abandon`-style flag (mirroring the existing obligation-gate
      abandon/`--handoff-to` shape) that releases a `pr`-kind claim on an
      explicitly named PR, distinct from the general obligation abandon
      path, since abandoning a PR claim means "give up on getting this PR
      merged," a materially bigger decision than re-homing a CodeSpace. This
      path must enforce the operator-only boundary itself (see design
      question below) — a bare CLI flag callable by any invoker does not
      satisfy defense 3's "only the operator may countermand" contract.
- [ ] Tests: claim created on an actually-opened PR (not a numberless
      `creating` record), blocks `finalize` while open, auto-releases on
      merge observed through **every** existing merge-detection path (see
      design question below), explicit-release path works, is attributable
      (who released it, when, why), and is rejected for a non-operator
      invoker.

#### Design questions to resolve before implementation (from Copilot review)

- **Claim timing vs. `create-pr` failure modes.** `create-pr` persists a
  `creating`-state `PRRecord` *before* the provider actually opens the PR,
  and supports `--no-open` and provider-failure paths that can leave a
  numberless record. The claim must not go `active` until the PR is
  confirmed actually open (a real provider number exists) — otherwise a
  failed/`--no-open` create-pr call wrongly blocks `finalize` forever.
- **Manual association via `set-pr`.** The documented workflow also allows
  opening a PR out-of-band and recording it via `set-pr`, not just via
  `create-pr`. The claim design must cover this path too, or a
  manually-associated open PR goes unclaimed. `set-pr --state open`
  persists that state **without a provider read**, so a non-null PR number
  alone does not confirm the PR is actually open — an already-merged or
  closed out-of-band PR recorded this way could otherwise create an
  `active` claim and block `finalize` indefinitely. Require a successful
  provider observation of a non-terminal state (or equivalent evidence from
  the auto-open response) before the claim goes `active`, not the presence
  of a number.
- **Unify every merge-observation path.** `pr_ops`, `pr_reconcile`, and
  `prune` each have their own reconciliation logic, and `sweep.py` currently
  marks a merged `pr`-kind claim `abandoned` (not `released`/`at-rest`) —
  inconsistent with this effort's intent. All paths that can observe an
  external merge must settle the claim through one shared path before
  `_assert_obligations_settled` runs, or an externally-merged PR can leave
  the claim `active` and wrongly block `finalize`.
- **Operator-only enforcement, not just a CLI flag.** Defense 3's "only the
  operator may countermand" boundary needs an actual authorization/
  provenance mechanism on the abandon path (not merely a flag any invoker
  can pass) plus a regression test that an agent-only invocation is
  rejected.
- **Generic `claims release`/`claims settle` are an existing bypass.** These
  pre-existing verbs already accept any claim kind, including `pr`, and can
  currently make an open PR claim non-blocking without going through any
  PR-specific path. Phase 2 must gate those generic verbs for `kind=pr` (or
  route them through the same operator-provenance check as the dedicated
  abandon path) — otherwise the operator-only boundary has a pre-existing
  side door.

### Phase 3 — Agent-guidance instruction

- [ ] Add or extend a static-fallback `.instructions.md` (matching this
      repo's own pattern — see `plugins/*/instructions/*.md`) directing
      every agent to drive every PR it opens, directly or via a downstream
      worktree/agent, through to merge — and that only the operator may
      direct releasing that claim, resetting HEAD, and abandoning it.
- [ ] Cross-reference from the `worktree` skill (`pr-workflow.md`'s
      "Default conduct: drive every PR you open through to merge" section
      already states this in prose; this phase makes it load-bearing
      ambient guidance, not just skill prose an agent might not load).

## Validation Plan

- [ ] A worktree that opens a PR and attempts `finalize` before merge is
      **blocked** by the new obligation gate even when `pr.strategy` is
      unset or misconfigured to `detach` by mistake (regression coverage
      for the exact failure class that produced #4328 and siblings).
- [ ] A worktree cannot reset `worktree/<id>` off unmerged commits without
      either the PR merging or an explicit, attributable `detach`/abandon
      action.
- [ ] Once the PR merges, the obligation auto-settles and `finalize`
      proceeds without any manual claim release.
- [ ] An operator-directed release (abandon the PR claim, reset HEAD) works,
      is attributable in the claim ledger, and is refused without explicit
      operator direction.
- [ ] Existing `worktree-finality-and-obligations` obligation-gate tests
      (CodeSpace/container/bridge-session claims) show no regression.

## Proposal

_Pending._

## Journal

### 2026-09-27 — Kickoff
- Effort created following the #4374 investigation and the operator's
  three-defense design. Defense 1's mechanism (fail-safe `pr.strategy`
  default) already shipped same-day via #4374; this effort covers the
  remaining reset-guard policy plus defenses 2 and 3.
- Filed umbrella issue #4375.
