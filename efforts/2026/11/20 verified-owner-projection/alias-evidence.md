# Historical alias evidence decision

[Canonical effort](README.md)

## Measured boundary

The reviewed plan #13569 actually merged as
`91a55c383956b38a3e0406c61367d2f0847bd4c7`.
Against that production source, the original historical/qualified-consumer
fixture passes both acceptance-to-scan positive cases. Four negative cases
fail: a `foreign` or `unverified` machine using the same receiving project and
worktree ID is rejected by handoff actor equivalence but is accepted by the
projection's receiving-subject fallback. Two other-project and two
other-worktree controls pass. This is four failures and six passes, not a
failed prerequisite or a proxy string test.

The existing four single/subsequent-rename projection controls separately pass.
They deliberately preserve an old actor even after its custom key disappears
from current topology. Requiring every historical actor to resolve through
today's topology would regress that delivered contract.

All tests use disposable fixture state and the canonical contained runner.
The observation controls reject cached-record reads, writes, provider discovery
and subprocesses, and compare fixture bytes. No live ledger, provider, claim,
resource, service or installed runtime was operated on.

## Source of the ambiguity

`producer_views._resolve_group.resolve` first matches literal record owners,
then admits any qualified machine spelling with a unique matching receiving
project/worktree ID. That preserves historical naming, but the alias fallback
has no independent accepted-actor evidence.

The existing `claim-handoffs.yaml` intent store retains the actual accepted
bundle/source/consumer and original producer claim snapshot. `accept_producers`
persists the recipient and source tombstone before committing that bundle to
`accepted`. It is existing transaction evidence, not a new identity registry.
Its read-only use could distinguish an original historical actor from a
substituted actor without consulting a provider or requiring current-name
enrollment. Missing, malformed, contradictory or changed evidence must remain
unknown, not trigger repair.

## Alternatives

1. **Existing accepted-transaction evidence, agent recommendation.** Only
   where a transfer actor requires historical-name resolution, read and
   stamp-check the existing handoff store and match accepted bundle, exact
   actors, token, callback ref and immutable producer identity against the
   receiving chain. Preserve original bytes and arbitrary renames. No
   evidence means unresolved/incomplete; no repair, new store or native probe.
2. **Current verified aliases only.** Reject unknown historical names even
   when their receiving token matches. This needs an explicit change to the
   later rename guarantee or retained alias enrollment, not a silent fix.
3. **Receiving subject remains sufficient.** Keep current behavior and
   explicitly revise the older same-ID unverified-actor requirement. The
   existing source could then be credited, but the four current failures
   cannot honestly be called satisfied under the unchanged receiver.

The general boundary is historical operation evidence versus current naming:
an old label is neither automatically invalid nor a grant of present execution
authority. Observation must preserve truthful uncertainty independently of
handoff, settlement and cleanup authority.

## Decision scope

Choose the evidence contract for this finite OA lane only. Any selected source
change still needs focused regressions, original rename controls, no-effect and
changed-input negatives, whole-file checks, review/CI and actual merge.
This decision cannot settle an obligation, close the whole parent or authorize
live scans, services, cleanup or rollout.

## Accepted decision

The operator selected `accepted-handoff-evidence`: alternative 1. Use only
the existing accepted transaction to qualify historical transfer actors.
Missing or contradictory proof yields an incomplete snapshot. No new registry,
live resource operation, settlement, cleanup or rollout is authorized.
The current-name-only and receiving-subject-only alternatives are not selected.
