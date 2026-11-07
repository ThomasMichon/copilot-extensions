# Author publication and metadata convergence

Return to the [owning effort](README.md#author-publication-and-metadata-convergence).
This is a bounded implementation proposal within the existing checkout-free
and client-convergence lanes, not a second PR effort or watcher.

**Disposition:** the selected source contribution merged in #10402. Live,
installed-consumer and remaining remote-only evidence duties are transferred
to #10412, owned by ThomasMichon. The broader effort remains Active.

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
The original request authorized planning only. A subsequent operator request
selects implementation of the Azure DevOps authorship tools through an adopted
effort agenda, following the reviewed vision and this decomposition. This
extends source implementation and validation authority, not live forge test
mutations, credential changes or runtime rollout. The decomposition below
remains **agent-recommended**, not an additional operator quotation.

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
- The implementation driver owns the finite A0-A5 source contribution under
  #10383. Metadata/provider and adopted-source implementation are bounded
  delegates; the driver owns frontend integration, validation and PR stewardship.
  This does not transfer the broader service or provider-conformance campaigns.
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

- [x] Reconcile the public front doors and their installed capability reporting
  with this matrix. Distinguish managed caller, delegated source workspace,
  target repository, credential scope and publication profile.
- [x] Specify supported inputs and outcomes for an already-pushed candidate,
  delegated validation evidence and metadata-only repair. Resolve the target's
  publication enablement, branch conventions and human-completion policy
  explicitly; never inherit the caller's unrelated merge authority.

The implementation contract keeps these identities separate:

| Input / owner | Contract |
|---|---|
| Managed caller | The current tracked worktree owns the resulting PR obligation; selecting a source workspace never changes its identity. |
| Target | An explicit registered repository selects provider, endpoint, credentials, configured base, publication checks and completion policy. |
| Delegated source | An explicit exact Git root, branch and expected commit are admitted against the target's canonical push destination. A path alone is not adoption. |
| Reviewed candidate | Existing built-in review/scope checks run against the admitted source and current target policy, with authoritative source-ref observations around publication. |
| Metadata repair | An existing PR identity plus individually requested changes; no create, source push, reviewer vote or completion operation. Per-action readback distinguishes confirmed, failed and unknown effects. |
| Source update | The source owner publishes the pinned admitted commit using ordinary hooks and fast-forward protections, retaining user branch naming and remote work. |
| Public front door | Native `agent-pull-requests` author commands forward to the target-qualified Worktrees operations. Old peers refuse unsupported operations without raw forge or GitHub fallback. |

Reachable delegated workspaces are the initial validation adapter. Exported
worker configuration is policy, not source custody. A self-supplied receipt,
timestamp or matching digest does not establish a remote worker's authority;
remote-only evidence must have a qualified owner transport before it can
replace direct source verification. Record that acceptance separately rather
than weakening checks to make checkout-free creation appear supported.

### A1 - Policy-qualified foreign creation

- [x] Extend the existing foreign creation path to qualify the delegated
  candidate against the resolved publication policy. Validate evidence identity,
  source/target binding and applicable freshness before effects.
- [x] Refresh authoritative remote source identity at publication. Preserve
  caller claims, idempotent existing-PR recovery, explicit unsupported outcomes
  and honest uncertainty where the provider cannot supply atomic fencing.
  Do not solve the current refusal by disabling publication checks.

### A2 - Verified author metadata

- [x] Reuse or extend the central provider operations for full descriptions,
  title, explicit reviewer requests and work-item links; compose the existing
  additive publication-label operation.
- [x] Preserve exact multiline/quote-heavy Markdown readback, unrelated metadata
  and reviewer sets, revision-guarded cross-project work-item linking and
  verification from both relation endpoints.
- [x] Retain created PR identity and per-action confirmed/unknown outcomes after
  partial failure. Repair the same PR without repeating a source push or create;
  reconcile uncertain effects before retrying.

### A3 - Delegated source publication

- [x] Extend the existing source-publication owner for admitted adopted
  workspaces or a supported adapter, without pretending `--workspace` performs
  adoption. Verify the canonical push destination and reviewed candidate.
- [x] Preserve target branch naming, hooks, existing remote changes and
  merge-forward versus rebase policy. Do not copy unrelated caller commits,
  force a managed branch scheme onto the product, or move Git authority into
  the remote observation service.

### A4 - One documented front door

- [x] Update generic PR-workflow guidance and capability-directed examples for
  create, source update, metadata repair, status and waiting.
- [ ] Inventory consumer skills, dispatch seeds, templates and competing helper
  triggers. Route supported operations through the same domain contract;
  compatibility helpers become forwarding adapters rather than another choice.
- [ ] Deliver repository-owned policy and separately authorized authentication
  to each venue. A configuration export is not custody, credentials or merge
  permission. Missing capability blocks dependent publication rather than
  authorizing raw forge calls, token workarounds or an automatic runtime update.

The remaining consumer adapter, venue policy/authentication delivery and
installed replacement acceptance belong to #10412. Generic source guidance
does not close those rows.

### A5 - Qualification and adoption

- [ ] Complete the validation matrix below and record source, release and
  installed-consumer evidence separately.
- [x] Assign any downstream migration and authorized rollout to named tracked
  owners before closing this finite slice. Closing it does not close #6007,
  #7051, #7188 or their broader acceptance.

Source validation is recorded below. The operator selected source-PR completion
with live/installed adoption tracked separately; #10412 carries the remaining
matrix rows and consumer work under the same retained domain owners.

## Validation (agent-recommended)

- [x] **Caller and target:** exercise a managed host with no target checkout,
  an already-pushed foreign branch, adopted source workspaces and invalid/
  ambiguous bindings. Prove no caller-provider fallback or lost PR claim.
- [x] **Publication evidence:** accept the correct source/policy/workspace
  evidence; refuse stale source, altered receipt/configuration, wrong target,
  wrong identity and source movement during preflight without weakening checks.
- [x] **Metadata:** verify full Markdown including blanks/quotes/Unicode,
  reviewer preservation, cross-project links on both sides, additive labels,
  and a metadata-only change with zero source pushes or duplicate creations.
- [x] **Uncertain effects:** inject timeouts before and after each write,
  failed readback and partial metadata/claim settlement. Preserve confirmed
  PR identity and reconcile the same operation rather than promising exactly
  once or blindly replaying it.
- [x] **Source updates:** preserve hooks, user branch naming, existing remote
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

Checked rows describe source/adapter and disposable real-Git controls, not
live ADO or installed acceptance. The unchecked continuity, consumer and tier
rows, plus remote-only transport qualification, are explicitly retained in
#10412; they have not been silently waived.

## Implementation journal

### 2026-11-04 - Candidate source qualification under #10383

The bounded source candidate adds target-qualified author capabilities, checked
foreign creation from a reachable delegated workspace, verified ADO metadata
repair and admitted source publication. Native frontend operations reuse the
same Worktrees owners. Body files remain files across frontend forwarding,
avoiding Windows command-line expansion before exact provider JSON readback.

Focused source packets passed 150 tests in total: 4 central author CLI,
26 foreign-create, 5 authoritative candidate/policy controls, 39 metadata,
21 relevant ADO provider/source-publication, 1 actual central source-push
integration, and 54 native frontend tests. The real-Git packet exercises hooks,
canonical destinations, leases, remote races, caller isolation and merge-forward
reconciliation. It passed within the existing 300-second group budget.
An earlier combined group exceeded that budget; smaller selectors resolved
the packet without increasing limits. Initial fixture failures were corrected
and their focused packets rerun.

Validation tiers are distinct:

- **Unit/adapter:** the focused packets above, touched-source Ruff F/E9,
  install-contract and documentation-consistency checks.
- **Lightweight end to end:** the actual central `pr-push` frontend publishes
  into a disposable local remote with real hooks and exact-head readback;
  central/native parsing and long Unicode/CRLF descriptions are exercised.
- **Fresh configuration:** disposable source/caller fixtures and unsupported
  runtime capability controls. No installer/bootstrap behavior changed, so a
  fresh-machine installation scenario is not this slice's qualification.
- **Live external / installed:** not exercised. No isolated writable ADO target
  or runtime rollout was authorized. #10383 retains those acceptance and
  downstream-consumer migration duties; source merge or a subsequent release
  must not be reported as installed adoption.

Remote-only evidence is explicitly unadvertised pending an owner-qualified
transport. Existing consumer helpers remain supported until replacement
acceptance; the generic workflow is not evidence that every consumer has
migrated. #10383 and this amendment remain open through those dispositions.

Current-head review identified missing ADO target-ref propagation, inherited-PAT
repair, human-output qualification warnings, terminal-PR label selection and
encoded destination handling. The source corrections have focused controls:
84 Worktrees tests and 56 frontend/status tests passed after the fixes.
Parser/dispatch ownership and pure presentation were extracted to stay within
the existing hard/shrink-only module limits; no ceiling was widened.
Required CI and another current-head review remain separate publication gates.

### 2026-11-04 - Source merged; adoption transferred

#10402 merged as `b036978b37993bdefee11e2d3f4c18557b5da4fb` after current-head
Copilot approval, all five corrected review threads resolved, and successful
required CI run `41810303402`. The tested source head was
`75c96135bdd233823b195628983babce472f3127`. Two additional AAD-only controls
passed: token construction and creation plus exact description readback with no
PAT. PATs are not a prerequisite.

The operator selected completion of the source PR with live/installed
acceptance tracked separately. #10412 is the named, assigned continuation for
remaining A4-A5 consumer migration, live/installed and remote-only evidence
qualification. No rollout occurred, and no existing consumer helper was retired.
This closes the finite source contribution, not #6007, #7051 or #7188.

## Completion and next action

The selected source contribution is merged. Continue separately authorised
adoption from #10412; resolve the remaining A4-A5 and validation rows against
actual installed/live evidence before declaring this whole amendment complete.
The existing effort remains Active and every broader Plan/Validation row stays
in force. This document contains no installed or rollout completion claim.
