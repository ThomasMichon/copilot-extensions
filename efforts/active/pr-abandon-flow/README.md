# PR Abandon Flow

- **Slug:** `pr-abandon-flow`
- **Repo:** copilot-extensions
- **Branch(es):** single worktree, one PR
- **Created:** 2026-10-05
- **Status:** Implemented (Phase 1 landed: provider `close_pull` + `pr-abandon`
  CLI verb + two-step confirm gate); Phase 2 (Azure DevOps/Gitea `close_pull`)
  and the supersession-evidence enrichment are tracked follow-ups, not blocking.
- **Vision:** `visions/agent-fabric` § `resource-accountability` — *abandoning
  a resource is rare and salvage-first, never a substitute for routine
  conflict resolution*.
- **Umbrella issue:** #4375 (`pr-merge-obligation-gate`)
- **Follow-up issue addressed:** #4411 (operator-authorization primitive for
  claim release/abandon) — addressed **pragmatically**, with a friction gate,
  not the deferred invoker-identity primitive (still open, still future work).
- **Sub-issues:** none yet

## Guiding Intent

`pr-merge-obligation-gate` (#4375) built the structural defense that blocks a
worktree from finalizing while its PR sits open and unclaimed. Its own
Phase 2 explicitly deferred one piece: an **explicit, attributed path to
abandon** that claim when a PR is genuinely superseded, distinct from the
generic `finalize --abandon --handoff-to` escape hatch, with real
operator-vs-agent authorization. That authorization primitive doesn't exist
at this layer (tracked separately at #4411) and is a larger design effort.

This effort does not build that primitive. It closes the *practical* gap a
different way, per the operator's explicit direction: a **friction gate**
instead of an identity check. The first (unconfirmed) call to abandon a PR
always refuses with a stern, alternatives-first warning and mutates nothing;
only a second, explicit `--confirm` call — which the warning itself
instructs should only ever be added after real operator sign-off for that
specific PR, never on the calling agent's own judgment — proceeds. This is a
procedural safeguard, not a cryptographic one: it raises the bar against
reflexive or casual abandonment without depending on infrastructure this
layer doesn't have.

Equally important (and the operator's primary motivating concern): **this
must not become a trapdoor agents reach for instead of doing the right
thing.** Two specific anti-patterns this effort explicitly guards against:

1. **Abandoning a PR just to avoid a hard rebase.** A heavy merge conflict
   is not a reason to abandon-and-reopen — it discards review history and
   attribution for nothing. The correct tool is `agent-worktrees git sync`
   (rebase) followed by a `git push --force-with-lease` once conflicts are
   resolved, updating the *same* PR.
2. **Abandoning a PR wholesale when only its *driving* idea is superseded.**
   Most PRs, even ones whose headline change is now moot, carry some
   salvageable remainder (a doc fix, a test, an unrelated correction bundled
   into the same diff). The default should be to whittle the PR down to that
   remainder and keep driving it to merge — not discard it outright. Full
   abandonment is for the rarer case where nothing of value survives.

## Context

`pr-merge-obligation-gate` (#4375, Done) is the direct precedent: it built
the structural defense blocking a worktree from finalizing while its PR
sits open and unclaimed, and its own Phase 2 explicitly deferred one piece
to a follow-up, #4411 — an attributed abandon path with real
operator-vs-agent authorization. #4411's own design-questions section
concluded that authorization primitive needs invoker-identity/provenance
infrastructure this repo doesn't have at the CLI-entry-point layer today,
and building one is a separate, larger effort. This effort is that
follow-up, scoped pragmatically around a friction gate instead.

### Prior art / duplicate-request check (done before any design work)

- **copilot-extensions:** #4375 and #4411 (above) are the only related
  issues/efforts found. No other open issue/effort requests this capability
  independently.
- **odsp-web-harness:** no matching issue found (`pr-abandon`, `superseded`,
  `close PR` searched).
- **Azure DevOps:** searched across this operator's accessible projects
  (ODSP-Web, ODSP Product Experiences, Taiger, SPIN, EFun, Sync, SPARC,
  OneBranch, WEX!, IDC) for "abandon pull request", "superseded PR", "PR
  abandon flow", "close superseded pull request", and related phrasing. One
  near-miss, ruled out: OneBranch\NEXUS #3270075, a one-off instruction to
  close two specific real PRs, not a request for general tooling. No
  existing ADO work item requests this capability.

## Request

Operator's ask, verbatim (2026-10-04, following a session that hit the
no-`pr-abandon`-verb gap directly while retiring a confirmed-superseded
`copilot-extensions` PR):

> Help drive a change to support a pr-abandon flow. Update vision, make an
> effort, and check for any similar requests or efforts in odsp-web-harness
> and copilot-extensions (there might be a request for it for ADO as well).
> Now, we don't want agents abandoning randomly; it's got to be for good
> reason, like a superseded PR. Heck, for most PRs that get superseded, it's
> worth whittling the remainder down to a still-useful contribution, even if
> it's just to update docs. It's rare that a whole effort will be completely
> overshadowed without something to contribute. We also don't want agents
> abandoning PRs just to re-create them; remote PR branches should be
> force-updated if a PR needed a heavy rebase, not abandoned. We should
> require `--confirm` as a flag, or something, that is only mentioned to an
> agent after they attempt the call and get a stern warning about their
> alternatives (rebase and drive remainder, force-push in case of heavy
> conflicts, etc).

## Design

### Why friction, not identity

#4411's own design-questions section concluded a real operator-vs-agent
authorization primitive needs invoker-identity/provenance infrastructure
this repo doesn't have at the CLI-entry-point layer today, and building one
is a separate, larger effort. Rather than block on that, this effort adopts
a narrower, immediately-available mechanism: **the warning IS the gate.**
`--confirm` is never printed, documented, or hinted at anywhere except
inside the refusal message itself — an agent cannot discover it except by
first triggering (and reading) the warning. The warning's own text then
instructs the agent to surface it to the operator and wait for explicit,
PR-specific sign-off before ever adding `--confirm`. This is weaker than a
real authorization check (nothing stops an agent from adding `--confirm` in
the same turn), but it closes the practical gap identified by #4411 without
pretending to be something it isn't; the harder, identity-backed version
remains tracked there.

### What `pr-abandon` does

`agent-worktrees pr-abandon [worktree_id] [--repo O/R] [--pr N] --reason
"<text>" [--confirm] [--json]`:

1. **`--reason` is always required** (both calls) — the abandonment is
   attributable in the claim-history ledger regardless of which call
   actually closes anything.
2. **Without `--confirm`:** always refuses. Does nothing else — no record
   read, no provider call, no claim mutation. The refusal is the full
   alternatives-first warning (rebase+force-push; whittle to a salvageable
   remainder; only then consider full abandonment, with operator sign-off).
3. **With `--confirm`:** live-reconfirms the PR isn't already merged (never
   trusts locally tracked state alone — the same discipline
   `_reconcile_active_pr` already established), closes it via the provider
   (posting `reason` as a PR comment, best-effort — a comment failure is a
   warning, not a reason to abandon the abandon), and settles the tracked
   `pr`-kind resource claim to **`abandoned`** (not `released` — mirrors
   `_release_pr_claim`'s existing "an unmerged close is abandoned work, not
   a clean hand-back" contract) with a durable `claim_history` event
   carrying `reason`.
4. **Does NOT reset the worktree's git HEAD or touch its branch.** That
   remains a separate, explicit, operator-approved action — consistent with
   `pr-merge-obligation-gate`'s own defense-1 policy (never reset HEAD off
   unmerged commits except under an explicit, approved action).

### New provider primitive: `close_pull`

No provider had a "close a PR without merging" operation at all — every
abandon path up to now had to shell out to a raw provider CLI directly,
losing this plugin's own attribution/provider/token resolution (and hitting
a cross-contributor safety hook meant for a different scenario: closing
*another* contributor's PR). Added `close_pull(repo, number, *, api_base,
token, comment)` to the `PRProvider` protocol, implemented for real on
GitHub (`gh pr close`, with an optional best-effort `gh pr comment` first),
and the in-memory `mock` provider (full conformance-contract coverage).
Azure DevOps and Gitea currently return the standard `_unsupported_close`
message — both have a straightforward native path for a future Phase 2
(ADO's PRs have a native `abandoned` status; Gitea's PR PATCH endpoint
accepts `state: closed`), left as a follow-up since GitHub coverage was the
operationally blocking gap.

## Plan

- [x] Add `close_pull` to `providers/base.py`'s `PRProvider` Protocol +
      `_unsupported_close` default message.
- [x] Implement `close_pull` for GitHub (`gh pr close`, optional
      best-effort comment first, `@copilot`-mention rejection reused).
- [x] Add `_unsupported_close` stubs for Azure DevOps and Gitea (tracked
      follow-up, not blocking).
- [x] Implement `close_pull` for the `mock` provider (test/conformance
      double).
- [x] Add `pr_ops.abandon_pr` — the two-step confirm gate, live re-check,
      provider close, and claim settlement to `abandoned` with a durable
      `claim_history` event. No network I/O inside the record lock (matches
      `_set_pr_locked`'s own convention).
- [x] Wire the `pr-abandon` CLI verb (`pr_state_cli.py` + `__main__.py`'s
      lazy dispatch table), mirroring `pr-ready`'s `--repo`/`--pr` selector
      convention (defaults to the worktree's active PR).
- [x] Tests: the full two-step gate (first call mutates nothing; confirm
      closes + settles `abandoned`; reason required on both calls; already-
      merged/already-closed edge cases; comment-failure-is-a-warning vs
      close-failure-is-fatal). See `tests/test_pr_ops.py::TestAbandonPr`.
      Provider-level: `close_pull` conformance across all four providers
      (`tests/test_pr_provider_conformance.py`) + GitHub-specific CLI-arg/
      comment/error-surfacing coverage (`tests/test_providers.py`).
- [x] Vision: extended `visions/agent-fabric` § `resource-accountability`
      with the salvage-first/friction-gate design principle (see its own
      Provenance entry, 2026-10-05).
- [ ] *Follow-up, not blocking:* Azure DevOps / Gitea `close_pull` real
      implementations.
- [ ] *Follow-up, not blocking:* a lightweight, best-effort "does this
      branch still carry unique, unmerged content worth salvaging"
      diagnostic surfaced in the warning text itself (today the warning is
      static guidance; a future enhancement could compute and show the
      actual diff-vs-base so the agent/operator has concrete evidence
      in-hand rather than having to check separately).
- [ ] *Follow-up, not blocking:* #4411's own deferred scope (a real
      invoker-identity/authorization primitive) remains open; this effort
      does not close it, only works around its absence pragmatically.

## Validation Plan

- [x] A first `pr-abandon` call (no `--confirm`) against a real tracked PR
      refuses, prints the full alternatives-first warning, and leaves the
      PR/claim/tracked state completely untouched.
- [x] A second, `--confirm`'d call closes the PR (verified via the mock
      provider's full lifecycle test) and settles its claim to `abandoned`
      with a `claim_history` event carrying the given reason.
- [x] `--reason` is enforced on both calls, not just the confirmed one.
- [x] A live-confirmed-merged PR refuses the abandon outright regardless of
      locally tracked state (never trusts `pr.state` alone).
- [x] An already-closed PR is not re-closed, but its claim still settles.
- [x] A provider comment-post failure degrades to a warning (the close +
      claim settlement still succeed); a genuine close failure is fatal and
      leaves the claim untouched.
- [x] `close_pull` is part of the `PRProvider` Protocol conformance suite
      across all four registered providers (github/gitea/azure-devops/mock).
- [x] Full existing `test_pr_ops.py` (242 pre-existing tests),
      `test_providers.py` (212 tests incl. new), and
      `test_pr_provider_conformance.py` (44 tests incl. new) suites pass
      with no regressions.

## Journal

### 2026-10-05 — Designed and implemented in one pass
- Checked for prior art/duplicate requests first (see §Context) — none
  found in copilot-extensions, odsp-web-harness, or ADO.
- Added `close_pull` to the `PRProvider` protocol; implemented for real on
  GitHub and `mock`; stubbed (tracked follow-up) for Azure DevOps/Gitea.
- Implemented `pr_ops.abandon_pr`'s two-step confirm gate; extracted it to
  `pr_abandon_ops.py` once the addition pushed `pr_ops.py` over its
  grandfathered module-size ceiling (209 lines moved out; the 5-line
  re-export shim needed a small, deliberate baseline widening alongside it).
  `providers/github.py` and `providers/gitea.py` also needed small,
  deliberate baseline widenings (single-class provider files have no
  established split convention, unlike `pr_ops.py`'s own `*_cli`/`*_ops`
  family) — both reviewed and justified in the same commit.
- Wired the `pr-abandon` CLI verb and ran the full affected test surface
  (557 tests across five suites) clean before landing.
- Updated `visions/agent-fabric` § `resource-accountability` with the
  salvage-first/friction-gate design principle this effort embodies.
