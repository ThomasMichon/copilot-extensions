# Author publication and metadata convergence

Return to the [owning effort](README.md#author-publication-and-metadata-convergence).
This is a bounded implementation proposal within the existing checkout-free
and client-convergence lanes, not a second PR effort or watcher.

## Request and authority

Public-safe capture of the requested outcome:

> Compare repository-specific PR guidance and helpers with the central toolkit,
> and identify what is needed for agents to use the central tools for posting,
> updating, checking status and waiting on Azure DevOps PRs.
>
> Help build out or extend a relevant effort for this work, with any appropriate
> vision build-outs.

Private names and the concrete downstream motivation are intentionally omitted.
The scope is a reviewed plan and appropriate standing-intent amendments.
Implementation, live forge mutations, credential changes and rollout are not
authorized by this planning request. The decomposition below is
**agent-recommended**, not an additional operator instruction.

## Baseline and reuse

The source audit used `78d562784d698cba676f95d50169a994f33b932d`;
the relevant sources were unchanged at proposal base
`8ac0df44a2d0c7b29029d5fc33ff6da7c4f4c19b`.

| Surface | Credit existing behavior | Remaining finite acceptance |
|---|---|---|
| Foreign already-pushed creation | `pr-create --from-branch` accepts a managed caller without a target checkout; retains target resolution, attribution and claiming. | `pr_foreign_create.py` refuses configured publication checks. Qualify delegated evidence rather than disable those checks or remove caller custody. |
| Source publication | Worktrees owns source-control publication and managed-worktree records. | An adopted workspace and an exported checks configuration are not automatically a managed publication context. Support that real caller/source arrangement explicitly. |
| Metadata repair | Foreign `pr-publication-labels` already exists. | Title/body, requested expert reviewers and work-item relations need a reachable shared mutation contract, not another helper selected by the agent. |
| Description persistence | The native wrapper preserves argument bytes. | `AzureDevOpsProvider.create_pull` supplies one description argument to the CLI and does not prove exact persisted Markdown. Transport preservation is not storage verification. |
| Observation and remediation | Native status/snapshot/threads, cursor waits, shared subscriptions, owner cancellation, thread-fenced replies and existing-evaluation requeues are already central. | Preserve their evidence and lifetime semantics through author-side adoption; do not rebuild them. |

Primary source boundaries are the existing `agent-pull-requests` native client,
Worktrees' `finalize_cli.py`, `pr_foreign_create.py`, `finalize.py`,
`pr_publication_labels_cli.py`, `pr_worker_config.py`, and Azure DevOps provider.
Source locations guide implementation; this proposal does not claim their
current behavior remains unchanged indefinitely.

## Ownership and exclusions

- The existing effort host coordinates this finite client-convergence slice.
  Worktrees retains publication, repository policy and caller/resource custody;
  #7051 retains provider conformance; #7188 retains service integration.
- The plan author owns this proposal only. No runtime files or deployment
  resources are reserved or reassigned. Future implementation assignments use
  bounded, nonoverlapping source claims and normal review.
- Native reviewer votes, required groups, presence/approval attestations and
  completion authority remain with their current owners. A publication profile
  or green author checks cannot grant a human action or bypass a native gate.
- Native client usefulness must not depend on completing fleet deployment.
  Existing compatibility paths remain until their replacement acceptance passes.
- Product templates, rollout choices and validation-pipeline policy belong to
  the consuming repository. The shared toolkit supplies mechanisms and policy
  seams, not organization-specific rules.
- An optional private owner-evidence projection is not a second watcher,
  scheduler, ownership store or prerequisite for ordinary PR observation.

## Plan (agent-recommended)

### A0 - Contract and target policy

- [ ] Reconcile the public front doors and their installed capability reporting
  with this matrix. Distinguish managed caller, delegated source workspace,
  target repository, credential scope and publication profile.
- [ ] Specify supported inputs and outcomes for an already-pushed candidate,
  delegated validation evidence and metadata-only repair. Resolve the target's
  publication enablement, branch conventions and human-completion policy
  explicitly; never inherit the caller's unrelated merge authority.

### A1 - Policy-qualified foreign creation

- [ ] Extend the existing foreign creation path to qualify the delegated
  candidate against the resolved publication policy. Validate evidence identity,
  source/target binding and applicable freshness before effects.
- [ ] Refresh authoritative remote source identity at publication. Preserve
  caller claims, idempotent existing-PR recovery, explicit unsupported outcomes
  and honest uncertainty where the provider cannot supply atomic fencing.
  Do not solve the current refusal by disabling publication checks.

### A2 - Verified author metadata

- [ ] Reuse or extend the central provider operations for full descriptions,
  title, explicit reviewer requests and work-item links; compose the existing
  additive publication-label operation.
- [ ] Preserve exact multiline/quote-heavy Markdown readback, unrelated metadata
  and reviewer sets, revision-guarded cross-project work-item linking and
  verification from both relation endpoints.
- [ ] Retain created PR identity and per-action confirmed/unknown outcomes after
  partial failure. Repair the same PR without repeating a source push or create;
  reconcile uncertain effects before retrying.

### A3 - Delegated source publication

- [ ] Extend the existing source-publication owner for admitted adopted
  workspaces or a supported adapter, without pretending `--workspace` performs
  adoption. Verify the canonical push destination and reviewed candidate.
- [ ] Preserve target branch naming, hooks, existing remote changes and
  merge-forward versus rebase policy. Do not copy unrelated caller commits,
  force a managed branch scheme onto the product, or move Git authority into
  the remote observation service.

### A4 - One documented front door

- [ ] Update generic PR-workflow guidance and capability-directed examples for
  create, source update, metadata repair, status and waiting.
- [ ] Inventory consumer skills, dispatch seeds, templates and competing helper
  triggers. Route supported operations through the same domain contract;
  compatibility helpers become forwarding adapters rather than another choice.
- [ ] Deliver repository-owned policy and separately authorized authentication
  to each venue. A configuration export is not custody, credentials or merge
  permission. Missing capability blocks dependent publication rather than
  authorizing raw forge calls, token workarounds or an automatic runtime update.

### A5 - Qualification and adoption

- [ ] Complete the validation matrix below and record source, release and
  installed-consumer evidence separately.
- [ ] Assign any downstream migration and authorized rollout to named tracked
  owners before closing this finite slice. Closing it does not close #6007,
  #7051, #7188 or their broader acceptance.

## Validation (agent-recommended)

- [ ] **Caller and target:** exercise a managed host with no target checkout,
  an already-pushed foreign branch, adopted source workspaces and invalid/
  ambiguous bindings. Prove no caller-provider fallback or lost PR claim.
- [ ] **Publication evidence:** accept the correct source/policy/workspace
  evidence; refuse stale source, altered receipt/configuration, wrong target,
  wrong identity and source movement during preflight without weakening checks.
- [ ] **Metadata:** verify full Markdown including blanks/quotes/Unicode,
  reviewer preservation, cross-project links on both sides, additive labels,
  and a metadata-only change with zero source pushes or duplicate creations.
- [ ] **Uncertain effects:** inject timeouts before and after each write,
  failed readback and partial metadata/claim settlement. Preserve confirmed
  PR identity and reconcile the same operation rather than promising exactly
  once or blindly replaying it.
- [ ] **Source updates:** preserve hooks, user branch naming, existing remote
  work and published-history policy; reject wrong destinations and unrelated
  caller source. Check actual publication, not only a review receipt helper.
- [ ] **Observation continuity:** retain native policy/vote/thread semantics,
  honest bounded-wait timeouts, shared subscription restart/replay, independent
  cancellation and verified terminal disposition. Do not add a per-task poller.
- [ ] **Consumer routing:** test first-touch host/worker instructions and
  actual frontend invocation for create/update/status/wait. Unsupported or old
  installations fail explicitly; adapters may use internal provider transports,
  but agents are not sent back to raw forge commands.
- [ ] **Evidence tiers:** run shared unit/adapter and lightweight end-to-end
  checks, fresh-configuration consumers, native platform argument boundaries,
  and a real isolated ADO lane when separately authorized and available.
  Record unavailable lanes and named follow-ups; mocks, source merge or a
  healthy service do not establish installed acceptance.

## Planning completion and next action

This amendment is ready for implementation assignment only after normal plan/
vision review and merge. Its next bounded step is A0, not a runtime update.
The existing effort remains Active and every broader Plan/Validation row stays
in force. This document contains no implementation-completion claim.
