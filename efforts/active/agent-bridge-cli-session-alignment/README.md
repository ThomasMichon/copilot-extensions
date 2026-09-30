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
- **Umbrella issue:** [#4702](https://github.com/ThomasMichon/copilot-extensions/issues/4702)

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

> [operator, verbatim, contributor's name redacted for this public
> artifact — see Context above] "Yes, let's do historical review. I don't
> distrust [the reviewed contributor], but we rely on the Copilot review to
> sanity-check agent submissions, so everything gets shaken out. [They are]
> focusing on making agent-bridge CLI-based sessions shine on Codespaces,
> and [are] clearly trying to iron out bugs. I want to ensure that
> everything is consistent: ensure parameter naming is idiomatic, ensure
> agent-containers, normal machine-to-machine, cross-machine, and elevated
> bridging aren't left out (all should support CLI mode!), and ensure that
> we don't break expected invariants like keeping dynamic port reservations,
> good process hygiene, strong decoupling, minimal opinionated (i.e.
> themed, constrained, etc.) UX, etc. We'll have to review for ourselves
> here, and then come up with adjustments we want to make which still
> preserve the original vision-extensions of [their] work. And since I
> don't want to just blit over [their] contributions without [their]
> buy-in, we'll mostly want to write out our findings in an effort (like
> 'Proposed alignment/convergence of observable agent-bridge CLI sessions')
> that doesn't target anyone, just focuses on improving the product, and
> I'll send it [their] way for review."
>> [operator, follow-up, verbatim] "Specifically, we're focusing on the PRs
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

Full evidence: [`findings.md`](findings.md); review history that shaped
this proposal is in the Journal below, not repeated here. Five concrete
items plus one open design question, each naming what it preserves and
whether it's a pure addition/bugfix or a behavior change needing
compatibility handling:

1. **Align the CLI extension's WSL port-fallback with the Python client's
   already-retired special case** (`extension.mjs:resolveBaseUrl` still
   dials 9281 for a WSL guest; `models.py` retired that distinction and
   keeps only 9280 as the shared last-resort fallback). *Behavior change*
   for any environment still relying on the stale 9281 branch (should be
   none, per the Python client's own retirement, but flag as a compat
   check before landing).

2. **Give `agent-codespaces --forward` a daemon-reserved host port as the
   default**, with the current caller-supplied fixed port available as an
   explicit opt-in for the (real) case an operator wants a stable local
   port. Preserves the whole `--detach --forward`/Connection Owner design
   and its tests — this narrows one flag's default, not the mechanism.
   *Behavior change*: existing scripts relying on the current
   always-fixed-port default would need the explicit opt-in flag; needs a
   migration note.

3. **Key `agent-containers`' forward keeper to the full venue-qualified
   session scope (`<worktree identity>@<venue>`), not container name
   alone.** Preserves the standing `<worktree identity>@<venue>` design
   entirely — this only fixes the keeper's own tracking key so two
   sessions on the same container can no longer clobber each other's
   forwarding process. *Pure addition/bugfix, no behavior change for
   callers.*

4. **Fix the stale reservation left by a failed CLI-mode launch**
   (`inventory_cli.py:_launch_cli_mode_session`) so a launch failure
   releases its reservation instead of holding it until TTL expiry.
   *Pure bugfix.*

5. **Thread `agent-codespaces --detach`'s `--ttl-seconds` through to
   `cmd_detach`**, which currently hard-codes its own reservation TTL and
   silently ignores the caller's value on the detached path (only the
   attached path honors it today). *Pure bugfix — the flag already exists
   and is documented; this makes it work on both paths.*

**Open design question, not a proposed fix:** should CLI-mode sessions
extend to elevated-bridging targets at all? Today they deliberately don't
— the vision scopes symmetric CLI-mode launch to `agent-codespaces`,
`agent-containers`, and `agent-ssh`-reachable machines, and elevated
bridging is an architecturally different local headless relay with no
privileged mux/reattach contract for *any* interactive session yet (see
`findings.md` §1). If wanted, it's new scope requiring its own design, not
a wiring fix — raised here because it's exactly the kind of question the
reviewed contributor is positioned to weigh in on, not decided
unilaterally by this proposal.

**Out of scope, noted for continuity, not proposed here:** the
in-container precondition-check gap (`findings.md` §7) is real but
pre-existing and outside this effort's zero-review-PR scope.

Every numbered item above is either a pure addition/bugfix or an
explicitly-flagged compatibility-affecting change with its own migration
note — none proposes silently removing functionality the reviewed PRs
added.

## Journal

### 2026-09-30 — Second correction round, after the effort's own review (PR #4696)
- A second Copilot review pass (against the first correction round) caught
  more: (1) the "elevated bridging left out" framing was itself wrong —
  the standing vision scopes CLI-mode's venue set to `agent-codespaces`/
  `agent-containers`/`agent-ssh` only; elevated bridging is an
  architecturally distinct local relay with no privileged interactive mux
  contract at all, so this is an open design question, not a gap, and is
  no longer a numbered Proposal item; (2) the forwarding-flag-grammar nit
  was wrong — `--reverse-forward`/`--forward` already share a consistent
  listening-side-first convention; retracted; (3) the `--ttl-seconds`
  naming nit was actually masking a real bug — the flag is silently
  ignored on the detached path (`copilot_venue.py` hard-codes
  `_RESERVATION_TTL` in `cmd_detach` instead of forwarding
  `args.ttl_seconds`) — replaced the naming proposal with this concrete
  fix; (4) the Proposal/findings docs were rewritten to state current
  conclusions directly rather than narrating the prior review rounds
  inline (that history now lives only here in the Journal); (5) opened the
  umbrella issue (#4702) the local effort convention requires before
  treating a stretch as Active. Widened `copilot-review-gate.yml`'s own
  confirmation-poll window in the same PR (unrelated fix, bundled because
  this PR's own checks needed it) — flagged in the PR description per the
  same review round.

### 2026-09-30 — Corrected after the effort's own review (PR #4696)
- Copilot's review of this effort's own PR caught real errors in the first
  draft: (1) a personal name in the public Request quote, now redacted;
  (2) an in-container-precondition item that was pre-existing and out of
  this effort's stated scope, moved out of the Proposal; (3) "purely
  additive" was inaccurate for two items that are real behavior/compat
  changes, now labeled and given migration notes; (4) the cross-machine SSH
  evidence cited the wrong code path (a generic ACP-dispatch gap tracked as
  #566, unrelated to CLI-mode sessions) — corrected to cite
  `agent-ssh/copilot_detach.py`, which does support CLI-mode sessions; (5)
  the port-fallback finding was reframed from "hardcoded ports" to the
  actual, narrower bug (a stale WSL-specific fallback the Python client
  already retired); (6) the `agent-containers` venue-qualified scope
  (`<worktree>@<venue>`) was wrongly flagged as breaking CWD-uniqueness —
  it's the standing, documented design; only the forward keeper's own
  tracking key was the real bug; (7) the "decoupling violation" finding
  against `agent-containers`' detached launch was retracted entirely — the
  standing vision explicitly makes that venue preparation part of the
  single `copilot` verb's contract. Findings and Proposal rewritten
  accordingly. This is exactly the kind of catch this effort exists to
  demonstrate is worth having — including on itself.

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
  **Superseded by the next entry** — several of these findings didn't
  survive this effort's own review pass.

### 2026-09-30 — Kickoff
- Effort created following a routine Copilot-review-gate audit (see
  `CONTRIBUTING.md`) that surfaced a run of Maintainer-authored PRs which
  landed with zero review of any kind. Scope narrowed to those PRs on
  operator direction. Bounded to the CLI-mode-session-observability arc;
  five unrelated zero-review PRs excluded.
