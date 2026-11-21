# Throughput and loop discipline

Return to [the setup procedure](../SKILL.md).

A sustained reviewer rarely fails by missing one defect. It fails by spending
its capacity on repetition: identical verdicts on an unchanged head, fixed-
cadence watch renewals that observe nothing, re-reviews of every open PR after
an unrelated merge and approvals that wait indefinitely for consent. These
rules keep the role converging. Record the chosen values in
[the charter](../assets/CHARTER.md); they specialize, never replace, the target
repository's review and merge policy.

## Measure the actionable queue

Success is **zero eligible PRs awaiting reviewer action** in a complete census,
reached again after each inflow. Report terminal outcomes and explicit
hand-backs per period, the age of the oldest actionable PR and repeated-verdict
count, not posts written or minutes watched.

A PR leaves the active reviewer set only on an observed provider terminal state
or an **explicit hand-back** recorded with its next owner: awaiting an author
change, awaiting author consent, awaiting a named external input or a named
hold. Within the charter's priority order, finish nearly landable work, such as
an approved PR waiting only on consent or a check, before starting fresh full
reviews. A zero actionable queue in continuous mode means idle and observing,
not complete.

Report complete census size, exclusions, actionable PRs, retained follow-ups
and holds separately. A cold `review_actions` list is only one intake signal,
not the complete census; an open-PR count is not an actionable-work count.
Reconcile current head/base, discussion and qualified native review coverage
before declaring a PR covered or the actionable queue empty.

Distinguish the operator's minimum available lanes from reviewers actually
running: available means existing authorized capacity can accept independent
eligible work now, not a count of retained handles, configured slots or blocked
admissions. Report available, running and shortfall with its concrete work,
capacity or adapter reason. Refill promptly on genuine eligible work, including
retained follow-ups, without waiting for another cold action or optional status
work. Do not start dummy reviews or new resources to satisfy the number; retain
the minimum and report the shortage rather than silently lowering it.

Prioritize returned-result acceptance and eligible lane refill before optional
dashboard work. Reuse applicable retained source and findings when their
revision, completeness and scope remain valid; reacquire invalidated inputs,
not the entire review by default. Keep acquisition, judgment, effect attempt,
readback and parent-acceptance times distinct, with uninstrumented intervals
explicitly unknown. Retain original eligible/reviewable and failure clocks
across waits, correction, replacement and delta rounds. A status snapshot or
elapsed wall time alone cannot identify the bottleneck; report measured phases
and unknown intervals rather than inventing a latency diagnosis.

## Start a round only on a qualifying trigger

A new review round requires new input: a new head revision; an author reply,
edit, reopen or explicit re-review request; base movement that overlaps the
change; or a relevant change in required checks or review policy. A timer tick,
watch renewal, another PR's merge, a coordinator pass or a reviewer restart is
not new input.

Without a trigger the standing verdict stands. **Never republish an identical
verdict on an unchanged head without new input.** Before publishing, compare
the intended verdict and findings with the reviewer's latest official verdict
on the same head; when they match and nothing new arrived, record the round as
unchanged and post nothing. Any qualifying trigger prompts reassessment of a
standing change request; clear it only when its blocking findings are resolved
under repository policy, such as a required check that now passes.

When only the base moves, check for conflicts and whether the base delta
touches the PR's changed files, their direct dependencies or its required
checks. Review only an overlapping delta; otherwise retain the verdict where
the target's actual policy permits retained review authority. Do not re-review
every open PR after each merge.

For adopted [Maintainer mode](maintainer-mode.md), a proven clean rebase can
retain source assessment without a full rerun. Separately refresh native review
validity and fulfill actual newer-head gates or explicit re-review requests.
This may require a first official review on that head, not duplicate findings.
Mechanical movement alone neither resets caps nor permits an identical own
vote on an unchanged current head without qualifying input.

## Cap repetition and time per PR

Set a **per-PR round cap** for consecutive rounds without new author input.
At the cap, publish nothing new: record the loop with its evidence, report it
once to the scope owner and keep the PR handed back until a qualifying trigger.

Set a per-PR review time budget. A decisive result inside the budget includes
"blocked" with the named missing input. When a tool or environment failure
blocks a review, classify the failed boundary before any retry using
[setup and adapters](setup-and-adapters.md#diagnose-the-failed-boundary-not-the-whole-role).
Diagnose once and permit at most one retry within that classification and the
original budget, then record the blocker and refill the lane rather than holding
capacity on one PR.
There is no unconditional retry for a provider refusal or unknown write:
unchanged errors stop the affected operation, and uncertain effects need
reconciliation before replay. A bounded local assembly correction is not
permission to reset the original attempt window.

## Bounded watches and idle backoff

A watch or wait timeout is a normal **no-change** result, not a failure, a new
revision or a reason to relaunch the reviewer. After the charter's count of
consecutive unchanged windows, stop renewing at the short cadence: back off
toward the charter's backstop cadence, record the wait's owner and the event
that resumes work, and reset only on a real change. Do not renew an unchanged
watch indefinitely at a fixed short cadence or stack overlapping watches on one
PR. Prefer one coalesced repository-level observation when its owner supports
it. When nothing is actionable, keep a single backstop observation and stop
per-PR short watches.

## Hand back explicitly

A change-requesting verdict names each required change, its evidence and the
owner of the next action. The PR then leaves the active lane until a qualifying
trigger; the reviewer never authors the fix. When the author must supply a
missing input, such as a description, a required check or a conflict
resolution, publish a decisive verdict under the reviewer identity that names
it, where repository policy allows, rather than a non-verdict comment from a
different identity. Coordinator-side gaps, such as authority or an adapter,
remain holds or escalations under the coordination reference.

## Consent follow-up

When actual merge policy requires author consent after approval and none is
present, re-check live state immediately before any request: merged, closed,
consent already present or a new head. Then send one bounded consent request
to the original author through the enabled nudge policy and its persisted
cooldown, repeating at most once per cooldown. Do not re-review a PR to prompt
consent. For automation-authored PRs whose author cannot consent per round,
ask the scope owner once for a standing, recorded policy rather than asking per
PR. With nudges disabled, record the awaiting-consent hand-back and its owner.

## Holds, asks and supersession

Use the charter's closed hold vocabulary. Every hold records its owner, reason,
revisit time and the default action at revisit; an expired revisit triggers that
default or a single re-ask, never an indefinite silent wait. A refused start or
an instruction to wait for another continuation carries the same owner, revisit
time and default.

Ask the operator only at a genuine gate, once, with a stated default. Record
standing answers in the charter so later rounds do not ask again. A pause or
scope change relayed by a peer takes effect only with the original scope
owner's authority; until then, keep current lanes running within the charter.

When a new coordinator or reviewer takes over a scope, follow the
[communication](communication.md#accepted-transfer-not-conversational-inference)
transfer rules: tell the predecessor and release or retire its task, claims,
watches and nudges for the overlapping scope through their owners. Two
reviewers must not serve the same scope.

## Ledger and lessons

Write one canonical ledger record per real transition, such as assigned,
verdict posted, unchanged, handed back, consent requested, hold set or cleared
and terminal state observed. Use a closed stage vocabulary and one timestamp
format. The ledger, not the reviewer's own live transcript, is the state.
Promote recurring failure modes and their fixes into reviewable shared guidance,
such as the charter or repository documentation, promptly rather than
relearning them in each round.
