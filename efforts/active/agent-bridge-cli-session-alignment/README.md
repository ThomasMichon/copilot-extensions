# Proposed Alignment/Convergence of Observable agent-bridge CLI Sessions

- **Slug:** `agent-bridge-cli-session-alignment`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `dev`
- **Created:** 2026-09-30
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Vision:** [`visions/remote-interactive-sessions`](../../../visions/remote-interactive-sessions/README.md),
  child of [`visions/agent-fabric`](../../../visions/agent-fabric/README.md)
- **Related effort (origin, not superseded):**
  [`agent-bridge CLI-Mode Sessions`](../agent-bridge-cli-mode-sessions/README.md)
  — that effort designed and landed the CLI-mode session mechanism this
  effort reviews. This effort does not redo that work; it is a
  point-in-time consistency/coherence pass over the surface area that
  effort (and its continuation by another contributor) produced, informed
  by the fact that a run of the contributing PRs landed with **no
  automated review at all** (see Context) and so never had an independent
  sanity check.
- **Umbrella issue:** _TBD — open once the Plan below is confirmed with the
  contributor whose PRs this reviews._

## Guiding Intent

Keep the observable agent-bridge CLI-session surface — spanning
`agent-bridge`, `agent-codespaces`, `agent-containers`, and `agent-ssh` —
internally consistent as multiple people extend it concurrently: idiomatic,
non-drifting parameter naming; equal CLI-mode support across every bridging
transport (container, ordinary machine-to-machine, cross-machine, and
elevated); and preservation of the standing invariants the mechanism was
built on (dynamic port reservation, process hygiene, decoupling from
unrelated concerns, and a deliberately unopinionated/unthemed UX). This is a
product-coherence pass, not a critique of any one contributor — the trigger
is procedural (a review gap), and the output is a proposal, not a mandate.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| ThomasMichon | Reviews current state, drafts proposed adjustments | local worktree |

## Coordination

- **Topology:** single-owner review effort; no parallel implementation branches yet.
- **Host (owns PRs):** ThomasMichon.
- **Delegates:** none at present — implementation of any adjustment this
  effort proposes is deliberately deferred until the contributor whose work
  is reviewed here has seen and weighed in on the Plan.
- **Handoff:** n/a until the Plan is confirmed.

## Context

**Why this effort exists.** A gap in `copilot-review-gate.yml` (see
`CONTRIBUTING.md`'s "Contribution flow" and its own history) meant GitHub's
`requestReviewers` call for Copilot's review silently no-op'd for any PR not
authored by this repo's account owner — with no exception and no visible
signal — from whenever that automation was introduced until it was fixed.
In practice this meant a run of PRs from a non-owner Maintainer landed with
**zero independent review of any kind** (not Copilot, not human) for weeks.
That gap is now closed (a licensed-account PAT path with request
verification), but the PRs that landed during the gap never got the sanity
check the review gate exists to provide. This effort is that sanity check,
applied retroactively and constructively.

**Whose work, and what it's building toward.** The reviewed PRs are one
contributor's (a Maintainer) continuing push to make agent-bridge's
CLI-mode sessions (the mechanism [`agent-bridge CLI-Mode
Sessions`](../agent-bridge-cli-mode-sessions/README.md) designed) shine
specifically for CodeSpaces-hosted work: observable live sessions, a Picker
UI surface for supervised remote workers, detached/forwarded CLI sessions,
and venue healing (relay re-establish, bridge serving checks, Connection
Owner semantics). The work is real, wanted, and already partially validated
by that origin effort's own Phase 4 — this review exists to make sure it's
*consistent*, not to relitigate whether it should exist.

**Scope is bounded to the zero-review merged PRs**, identified via GitHub's
review API (a PR with zero entries in its `reviews` list, regardless of
`reviewDecision`, which reflects required-approval state and not whether
anyone — bot or human — actually looked at the diff):

| PR | Title |
|----|-------|
| #3909 | agent-bridge UI: task modes, earlier-task history, simpler filters |
| #3758 | live-session extension: announce 'loaded' once per session |
| #3744 | live sessions: a delivered message retires a BLOCKED milestone |
| #3741 | agent-bridge ui: a task control surface |
| #3710 | agent-containers: show a detached worker on its supervising worktree's row |
| #3707 | Live sessions record the worktree that supervises them |
| #3695 | venue-copilot: container and SSH workers start on the caller's own model |
| #3694 | Picker: send a message to a supervised worker |
| #3693 | agent-codespaces: detached sessions start on the caller's own model |
| #3692 | Picker: act on a supervised worker from its worktree row |
| #3690 | Picker: show the remote workers a worktree supervises on its row |
| #3688 | Resolve claim owners across worktrees |
| #3686 | Fix agent-bridge liveness after steered live-session work |
| #3658 | visions(venue-pivots-ux): worktree rows surface supervised remote workers |
| #3653 | Observable dispatch: container/SSH detached sessions + `--ref-file`, `--stop --keep-claim`, steered ref notes, ssh-manager transport fix |
| #3549 | agent-codespaces `copilot --detach --forward`: host-to-CodeSpace local forwards kept by the Connection Owner |

(#3689, #3536, #3535, #2848, #2845 were also zero-review but are unrelated
topics — instruction-projection budgets, CLI input-box compatibility,
config overlays, an older skill-review plan — and are out of scope here.)

## Request

> [operator, verbatim] "Yes, let's do historical review. I don't distrust
> Naman, but we rely on the Copilot review to sanity-check agent
> submissions, so everything gets shaken out. He's focusing on making
> agent-bridge CLI-based sessions shine on Codespaces, and is clearly trying
> to iron out bugs. I want to ensure that everything is consistent: ensure
> parameter naming is idiomatic, ensure agent-containers, normal
> machine-to-machine, cross-machine, and elevated bridging aren't left out
> (all should support CLI mode!), and ensure that we don't break expected
> invariants like keeping dynamic port reservations, good process hygiene,
> strong decoupling, minimal opinionated (i.e. themed, constrained, etc.)
> UX, etc. We'll have to review for ourselves here, and then come up with
> adjustments we want to make which still preserve the original
> vision-extensions of his work. And since I don't want to just blit over
> his contributions without his buy-in, we'll mostly want to write out our
> findings in an effort (like 'Proposed alignment/convergence of observable
> agent-bridge CLI sessions') that doesn't target anyone, just focuses on
> improving the product, and I'll send it his way for review."
>
> [operator, follow-up, verbatim] "Specifically, we're focusing on the PRs
> which went in without *any* review."

## Plan

### Phase 1 — Evidence gathering (per subsystem)
- [x] `agent-bridge` core + Picker UI (#3909, #3758, #3744, #3741, #3707,
      #3694, #3692, #3690, #3688, #3686, #3658): parameter naming,
      decoupling, UX.
- [x] `agent-codespaces` (#3693, #3549, #3653 shared): CLI-mode parity, port
      forwarding/reservation, process hygiene.
- [x] `agent-containers` (#3710, #3653 shared, #3695 shared): CLI-mode
      parity, process hygiene.
- [x] `agent-ssh` / cross-machine + elevated bridging (#3653 shared, #3695
      shared): CLI-mode parity across transports, port/process hygiene.
      Full findings: [`findings.md`](findings.md).

### Phase 2 — Synthesis
- [x] Cross-reference the four subsystem findings for consistency (does a
      naming/behavior choice in one subsystem contradict another?). See
      [`findings.md`](findings.md), organized by invariant rather than by
      subsystem/contributor.
- [x] Draft proposed adjustments as a reviewable list, each traceable to a
      specific finding, framed as product coherence rather than correction.
      See Proposal below.

### Phase 3 — Handoff for buy-in
- [ ] Operator reviews the drafted findings/proposal.
- [ ] Share with the reviewed contributor for feedback before any
      implementation PR is opened.

## Validation Plan

- [x] Every finding cites the exact file/function/flag it concerns and the
      specific invariant or convention it's checked against — no
      unsubstantiated "this feels off."
- [x] Every proposed adjustment states which of the reviewed PRs'
      vision-extensions it preserves, so a reader can confirm nothing here
      proposes rolling back wanted functionality.
- [x] The final document reads as product-focused throughout — no line
      attributes a finding to the contributor as a personal shortcoming.

## Proposal

Full evidence: [`findings.md`](findings.md). Summary, ordered by invariant,
each item naming what it preserves alongside what it adjusts:

1. **Wire elevated bridging into CLI-mode sessions**, matching the parity
   the container/CodeSpace/SSH-mesh transports already have
   (`session_targeting_cli.py`'s `--cli` scope currently rejects a bare
   elevated target outright). Preserves the existing elevated-ACP-daemon
   lifecycle as-is (it's sound on its own terms) — this only extends CLI
   reservation/launch to reach it, the way the other three transports
   already work.

2. **Make the CLI extension's daemon-discovery fallback fail loud instead
   of dialing a hardcoded port** (`extension.mjs:resolveBaseUrl`). Preserves
   the existing discovery-first behavior (`active.json` lookup stays the
   primary path) — only removes the silent fixed-port fallback that
   contradicts the documented ephemeral-port contract.

3. **Give `agent-codespaces --forward` a daemon-reserved host port as the
   default**, with the current caller-supplied fixed port available as an
   explicit opt-in for the (real) case an operator wants a stable local
   port. Preserves the whole `--detach --forward`/Connection Owner design
   and its tests — this narrows one flag's default, not the mechanism.

4. **Have `agent-containers`' detached-session scope key off the same
   worktree/CWD identity the local and CodeSpace cases use**, dropping the
   container-name salt from `scope_id`, and **key the forward keeper the
   same way** rather than by container name alone. Preserves per-container
   session hosting entirely (a container can still host a session) — only
   restores the "one current session per working directory" guarantee
   across containers, matching the other two venues already reviewed clean
   here.

5. **Split `agent-containers`' detached launch into a thin dispatch path
   plus an explicit, separately-invokable provisioning step** (the
   `ensure_agent_worktrees()`/workspace-registration/ADO-shim/credential-
   relay work), rather than bundling both behind one launch call. Preserves
   every one of those provisioning capabilities and their tests — this is a
   sequencing/API-boundary change, not a capability removal, and it's the
   one place the reviewed surface drifted from the origin effort's own
   stated decoupling intent.

6. **Add the in-container `copilot`/`tmux`/`agent-worktrees` precondition
   check** `installer_readiness.inspect_toolchain()` was already missing
   before this review (an origin-effort Phase 4 finding, still open, not a
   regression from the reviewed PRs). Bundled here because it shares a
   failure shape with #4/#5.

7. **Reconcile the two forwarding-flag grammars** in `agent-codespaces`
   (`--reverse-forward VENUE_PORT:HOST_PORT` vs. `--forward
   PORT[:VENUE_PORT]`) to one consistent port-pair ordering, and **rename
   `agent-containers`' `--ttl-seconds` to visibly match its underlying
   `reservation_ttl`/`register_timeout` concepts**. Cosmetic; preserves all
   existing behavior.

Nothing above proposes removing or rolling back functionality the reviewed
PRs added — every item is additive (extend a transport, add a guard, split
an API boundary, rename a flag) rather than subtractive.

## Journal

### 2026-09-30 — Evidence gathered, proposal drafted
- Four bounded, parallel evidence passes (agent-bridge core + Picker UI;
  agent-codespaces; agent-containers; cross-machine SSH + elevated
  bridging) completed against the stated invariants. Full findings in
  `findings.md`. Headline results: elevated bridging has no CLI-mode wiring
  at all (transport-parity gap); `agent-containers`' detached session scope
  and forward-keeper tracking aren't CWD-keyed, breaking the
  one-current-session-per-worktree guarantee across containers;
  `agent-containers`' detached launch couples session dispatch to remote
  resource provisioning (agent-worktrees install, workspace registration,
  ADO/git shims); two dynamic-port-reservation departures (a hardcoded
  extension fallback port, and `agent-codespaces --forward`'s
  caller-fixed host port); a stale reservation on a failed CLI-mode launch;
  two minor naming nits. Everything else reviewed clean. Drafted a
  7-item, purely-additive Proposal. Next: operator review, then share with
  the reviewed contributor before any implementation PR.

### 2026-09-30 — Kickoff
- Effort created following a routine Copilot-review-gate audit (see
  `CONTRIBUTING.md`) that surfaced a run of Maintainer-authored PRs which
  landed with zero review of any kind. Scope narrowed to those PRs on
  operator direction. Bounded to the CLI-mode-session-observability arc;
  five unrelated zero-review PRs excluded.
