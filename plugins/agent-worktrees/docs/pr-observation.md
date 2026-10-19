# Repository-configured PR observation

`pr-watch` and `pr-status` use the same provider read and normalized gate
evidence. Azure DevOps observations include current reviewer votes, native vote
events, source-push/iteration evidence, discussion resolution, policy evaluations
and bounded build diagnostics. Callers do not construct forge requests or
acquire credentials themselves.

## Read and wait

Use the exact agent-worktrees command selected for the caller:

```text
pr-watch snapshot Project/repository 7 --json
pr-watch status Project/repository 7 --json
pr-status --repo Project/repository --pr 7 --json
pr-watch threads Project/repository 7 --json
pr-status --repo Project/repository --pr 7 --threads --json
pr-watch cursor Project/repository 7
pr-watch wait Project/repository 7 --since <cursor> --until any --timeout 600 --json
```

`snapshot` and `status` are one-shot reads, not another polling program. Their
`agent-worktrees.pr-snapshot` version-1 envelope identifies the provider,
endpoint, repository, PR and head, and carries the same `observation` that the
tracked `pr-status` live block and foreground wait report.
On-demand `threads` reads return discussion bodies, stable comment identities and
comparison-round context; persisted watch snapshots contain metadata only.
Each readable thread also reports a reply fingerprint or an explicit
`reply_error` when stricter mutation evidence is unavailable.
A push during a discussion read is reported as `candidate_changed`, not used to
erase older-round feedback. An explicit target status cannot mix tracked-worktree
options or resolve threads.
Tracked status remains available; `--no-live` deliberately skips live evidence.
A failed provider read is a visible `live_error`, not an empty live success.
One-shot reads do not acquire durable responsibility and can run in the shared
owner's non-checkout process context. An explicit target resolves through its
registered control-plane configuration; no project or target is guessed.
The CLI front door admits `pr-watch snapshot/status/threads` and explicit
`pr-status --repo ... --pr ...` in that context; tracked status, durable waits
and publication retain their project/worktree requirements.
The owner's internal `pr-watch deliver` callback also runs from its neutral
runtime directory, retaining its protocol, peer and durable-admission fences.
Generated callbacks use a kernel-validated contained worker whose Bridge peers
remain in the outer owner's cleanup scope. Standalone delivery supervises that
same worker through the canonical bounded process helper.
Ordinary waits retain their claimant requirement. A venue author using an
adopted anchor can explicitly select a finite `wait --foreground-only` instead:
the process is caller-owned, an unlimited timeout is refused, and no durable
watch/PR obligation is acquired. This is not a replacement for host-owned
until-closure stewardship.

The additive `observation` includes separate votes, approval events, discussions,
policies, required gates, completeness/errors and repository guidance.
`agent_actionable`, `pending`, `human_required` and `unknown` identify what
remains. `ready_for_human` requires complete evidence and no remaining
non-human work except a configured composite gate waiting solely on known
human prerequisites. `all_required_satisfied` is a different predicate.
Unknown resolution, unreadable policy evidence, drafts and holds prevent a
human-ready checkpoint.

Merge consent is separate. A native auto-completion request may already be
armed while policies are pending. This does not establish `clear_to_merge`:
when native gate evidence is available, that flag additionally requires every
required gate to be satisfied.
Direct-merge reports retain operation-specific review assessment and additionally
require the native gates before claiming `ready_for_direct_merge` or eligibility.
An armed consent request is not equivalent to either readiness predicate.

## Related-repository configuration

Keep repository requirements in its existing related definition:

```yaml
related:
  example:
    pr:
      enabled: true
      required: true
      provider: azure-devops
      repository: Project/repository
      api_base: https://dev.azure.com/example
      branch_update_strategy: merge
      observation:
        features:
          reviews: true
          comments: true
          policies: true
          builds: true
        requirements:
          minimum_approvals: 1
          non_author: true
          approval_after_push: true
          required_reviewers: true
          resolved_comments: true
          no_blocking_votes: true
        policy_rules:
          - context_name: human-confirmation
            kind: human
          - name: Composite compliance
            kind: composite
        guidance: |
          Fix author-actionable feedback and validation failures.
          Never manufacture a human approval or attestation.
```

An explicit `repository` binding allows PR observation without a local target
checkout. The related entry must supply its own provider and hosting endpoint;
an ambiguous target is refused. No caller checkout, credential binding or
default branch is invented. Already-resolved target machine configuration
retains precedence. Existing repository/related/knowledge/machine overlays keep
their normal authority and merge rules.
An anchorless machine `repos.<name>.pr` declaration is retained for these reads
and layered over that name's related policy. It does not register a managed
checkout, and a retargeted machine declaration cannot revive the old related
endpoint or credential selection.

An absent observation block selects the ADO driver's defaults above. An explicit
`enabled: false` opts into metadata-only legacy reads; it is not native
merge-policy proof. Other provider defaults are unchanged.
Features control additional reads; disabling a read does not turn an applicable
requirement into a passed gate. Set requirements deliberately and retain native
policy evidence. Native required policies are never disabled by this block.

Policy selectors are conjunctive and case-insensitive: `name`, `policy_id`,
`context_name` and `context_genre`. Each rule needs a selector; conflicting
classifications are errors. Kinds are `agent`, `human` or `composite`, never
`ignore`. A composite failure may wait on evidenced prerequisite gates, but it
is not passed or waived. If its prerequisites clear and it remains failed,
it is actionable rather than indefinitely excused.
Unknown keys and malformed observation values are configuration errors.

## ADO review and discussion semantics

Current ADO votes persist across source pushes. Observation does not reset them
or require an approval's commit tag to match the head. Provider-reset required
reviews are reflected by the current native votes, not a client-side code-owner
heuristic.

The non-author minimum and approval after the latest source push are independent.
A native approval event from anyone, including the author, can satisfy the latter
without satisfying a separate non-author minimum. Freshness comes from structured
native vote-update events and iteration/push timestamps, not a current positive
vote, local observation time or parsed comment prose.
An approved native policy explicitly requiring approval on the latest/every
iteration can also establish freshness when its evaluation is bound to the
current iteration or started after the source push. A stale evaluation or a
minimum-reviewer policy without that requirement is not such proof.

Required reviewer/group votes are evaluated independently. One person may
contribute to multiple native group rollups; no disjoint assignment is imposed.
An association alone does not override a native required group's reset vote.

Discussions retain their native ID, comparison iteration, last comment identity
and resolution status. An author reply does not resolve an active thread, and
moving to another iteration does not silently resolve older feedback.
Unknown statuses remain unknown. A thread-wide revision fingerprint also
detects edits or deletions before the last reply, without persisting comment
bodies in observation state.

ADO vote changes do not use synthetic chronological review IDs. The existing
opaque wait cursor additively retains target-bound observation fingerprints and
the native vote-event watermark. New transitions include `review_changed`,
`threads_changed`, `policies_changed`, `gates_changed` and `ready_for_human`.
Fresh same-actor approval and resolution/policy changes remain observable even
when the aggregate verdict or CI rollup is unchanged. Selected `review_changed`
waits include native vote-history changes without duplicating that transition
when both current votes and history change. Existing legacy cursors
remain readable; a foreign or corrupt observation cursor is refused.
Rejection inference from current votes requires a new actor/vote pair, not an
unchanged blocker alongside another reviewer's approval. The optional cursor
cache retains at most 16 negative actor/vote digests and is omitted when it
cannot fit the cursor limit. Incomplete observations retain unknown actor/vote
state instead of treating an unavailable reviewer read as an empty vote set.
The driver reports `votes_known` separately from overall read completeness;
intentionally disabled reviews also leave the cursor's actor/vote state unknown.
Enabling reviews later cannot turn an unchanged existing vote into a new rejection.
Legacy or over-capacity evidence does not
synthesize rejection events; native negative events and general
`review_changed` notifications remain available.

## Evidence and transport limits

The native driver reads the documented Git 7.1 PR/iteration/thread/status APIs,
the policy 7.1-preview.1 evaluation API and current build metadata/timelines.
The Git target-branch policy-configuration roster uses repository/ref scope and
documented continuation tokens; policy evaluations use top/skip paging.
An empty evaluation list is not proof of no required policies. Missing required
unconditional branch-policy evaluations or configuration-revision mismatches
remain unknown. The branch roster also includes repository-only settings and
path-filtered policies; their mere presence does not prove they apply to this
PR. Native PR evaluations decide conditional applicability, including
`notApplicable`; no client-side path-glob approximation creates requirements.
An applicable conditional policy still requires current configuration evidence.
Evaluation and native-status recency use parsed, timezone-normalized timestamps,
not lexical date strings or arbitrary evaluation IDs. Missing timestamps or
conflicting records at the latest instant leave policy evidence unknown.
Unhandled continuation,
repeated pages, record/byte limits and malformed data produce explicit incomplete
evidence, never a complete empty result.

Source/merge-candidate movement during traversal triggers a fresh complete read.
Stale build/policy evidence cannot green the current source push. Configured
human/composite policies are not flattened into an independent build failure.
HTTP reads enforce the remaining absolute budget before each raw socket receive,
below HTTP buffering, header parsing and chunk framing. A slow stream cannot
renew the budget by sending fragments. Redirects are rejected; authentication
stays in memory rather than child-process arguments. No broad retry, pipeline queue,
approval, presence action or policy bypass is performed by observation.
