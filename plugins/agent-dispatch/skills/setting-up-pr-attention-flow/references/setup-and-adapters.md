# Setup and adapters

Return to [the setup procedure](../SKILL.md).

## Intake and repository contract

Resolve the target repository through `agent-worktrees:working-cross-repo`,
read its root/nearest `AGENTS.md`, contribution guide and review rubric.
Confirm the configured provider, repository/account scope and actual actor
through the owning authentication adapter; a global active account is not proof.
Never put credentials in a charter, ledger, prompt or example.

Identify the original allowed effects: discovery, static review, comments,
official verdict, author notification and any specific normal merge backup.
Review authority is not authoring, consent, resource transfer or merge authority.
Bind each effect to its owner and repository policy.

When the operator requests a charter-refresh cadence, name one existing owner
and reuse its authorized cadence. Do not add another timer or refresh loop.
Observation cadence, charter refresh and nudge cooldown are separate choices.

For adopted Worktrees, its catalog command exposes:

```text
<agent-worktrees catalog argv[0]> --project <registered-project> get pr-profile
<agent-worktrees catalog argv[0]> --project <registered-project> get pr-provider
```

These query the selected project's resolved configuration; they are not a
remote-policy verifier. Read the target's own guide and live actor/forge gates
as well. Foreign PR observations use an explicit registered target from the
responsible caller's managed worktree, not another author's checkout:

```text
<agent-worktrees catalog argv[0]> pr-watch snapshot <owner/repo> <PR> --json
<agent-worktrees catalog argv[0]> pr-watch cursor <owner/repo> <PR>
<agent-worktrees catalog argv[0]> pr-watch wait <owner/repo> <PR> --since <returned-cursor> --until approved,commented,changes_requested --timeout 600 --json
```

The wait is a bounded example, not a universal latency or retry default.
Inspect the event's actual reviewer/revision before classifying a verdict.
When checks or terminal disposition are the remaining gates, select the
owner's documented relevant transitions, retaining the cursor it returned.
A single snapshot does not promise complete discussion/policy acquisition.

For an already adopted Dispatch task adapter, inspect the existing recipe:

```text
<agent-dispatch catalog argv[0]> recipes describe reviewer
```

`land=author` leaves updates and landing with the author and suspends the
review task for relevant changes. Do not select `land=self` for an independent
reviewer whose mandate forbids merging. A recipe's generic lifecycle is not
evidence that this repository has adopted a task-bound review-write tool.
If the command/runtime is unavailable, record it; do not install or start
anything to make this setup appear ready.

## Capability inventory

Fill this table with **actual selected** tools and contracts. It is a checklist,
not a new adapter API, marker schema or set of required method names.

| Need | Existing owner | Proof and explicit absence path |
|------|----------------|---------------------------------|
| Population discovery | PR/provider owner | Scope and complete pagination; error/limit preserves unknown membership |
| Candidate and source | PR/source-workspace owner | Current head/base/range, exact retained source, completeness; unavailable source blocks source conclusions |
| Reviews, threads, checks | PR/provider owner | Full addressable native IDs, replies, dismissal, policy evaluations; unsupported fields stay unknown |
| Task and lane binding | Task/host owner | Actual stable task/claim/reviewer handles, dedup and suspend/resume; absent task is null, not a fake ID |
| Review preparation/write | Existing protected review owner | Exact current candidate/signature/round fence, identity/self-review checks and preserved original receipts; missing fence blocks publication |
| Effect inspection | Same write owner/provider | Original operation and native IDs, confirmed versus unknown; inability to reconcile stops replay |
| Update observation | Existing watch owner | Real cursor/subscription, replay/ack/lifetime contract; unavailable watch is a named setup blocker or policy-permitted manual observation |
| Peer follow-up | Existing transport/host owner | Admitted domain, routable retained handle, idempotency/receipt support; unknown delivery stays unknown |
| Consent and landing | Author/repository merge owner | Effective profile, actual actor authority and current gates; refusal is not bypass permission |

`agent-pull-requests` is the PR-domain integration seam. Its workspace
capability descriptors and `workspace load/sync/submit/inspect` frontdoor
require an actual authenticated host binding. An ordinary **unbound** command
reports owner-unavailable; source interfaces do not imply installed tools,
live source preparation or adopted submission fences. Use only capabilities
the executing owner actually advertises. Worktrees compatibility/provider
adapters remain useful where supported; do not write another forge client
to erase gaps.

Minor adaptations belong in repository-owned policy or existing adapters:
population exclusions, rubric, native verdict semantics, capabilities and
authorized escalation. Name a missing adapter owner/operation and remaining
proof. Do not ship service repair, queue surgery, new messaging/state services
or a parallel scheduler as an adaptation.

## Merge policy mapping

| Repository flow | Required handling |
|-----------------|-------------------|
| Provider-native automatic merge | Arm only through its authorized existing handler; preserve explicit consent/authority requirements and observe actual completion |
| `pr-agent-merge` | Author signals consent using its supported operation after applicable review; automation/gate lands it, not the independent reviewer |
| `pr-self-merge` | Authorized submitter uses normal direct completion when review/checks permit; missing consent label is not a made-up gate |
| `pr-human-merge` | Named human approves/merges; retain observation and the human handoff, never substitute another actor |
| `direct` | No PR completion flow; report the mismatch for a PR-attention charter rather than direct-pushing its candidate |

Automatic execution is not a fifth permission profile. Resolve actual actors,
required reviews, stale/dismissed review policy, CODEOWNER/iteration rules,
required checks, thread resolution, mergeability, draft and holds separately.
Negative/ambiguous evidence remains blocking where required. Only confirmed
policy-compatible retained authority can avoid a redundant review; it never
authorizes a new stale-candidate post.

Do not copy an adopter's timing, labels, exceptions or human roster. Approval
is not consent; absence of a configured consent requirement is not permission.
Normal coordinator backup exists only when the original grant and current
existing handler authorize it for this target. Refusal means report/route to
the owner, not an administrative force-merge fallback.
