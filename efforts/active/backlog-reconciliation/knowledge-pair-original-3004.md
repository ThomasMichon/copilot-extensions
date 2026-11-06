# Original issue 3004 — description and discussion

[Back to cohort overview](knowledge-pair-receiving-cohort.md) · [Campaign README](README.md)

<!-- ADMITTED_PACKET_BYTES_BEGIN -->
Source: https://github.com/ThomasMichon/copilot-extensions/issues/3004

Historical record copy of the admitted description and complete discussion. Qualification metadata is omitted; source control bytes other than tabs/newlines are displayed as literal \xNN escapes, not repaired commands. Historical OPEN, intake and receipt wording does not countermand a later explicit adoption or supply new authority. Original issue content and source/resource custody are unchanged.

## Original description

## Summary
When a stateless harness has finished bootstrap/plugin install but no external knowledge repository is yet bound, the first restarted session should proactively detect the unbound state and offer a supported create-or-link flow instead of waiting for a later stateful command to fail.

## Problem
Today an unbound harness can reach its first post-bootstrap restart with plugin support available but no actionable setup/link prompt. The missing binding then surfaces only later, when a stateful workflow needs the external state root.

That leaves the bootstrap boundary awkward in two ways:

1. the operator is not nudged to complete the required knowledge binding at the moment the harness becomes capable of doing so; and
2. the harness appears ready until some later command discovers that stateful workflows are still unavailable.

The generic stateless-checkout write guard belongs to agent-worktrees and is already tracked separately; this issue is about the missing first-restart binding/setup guidance owned by the harness/knowledge composition flow.

## Expected behavior
On the first restarted session after bootstrap/plugin install, if the harness requires an external state root and no usable knowledge binding is configured, the startup/setup flow should surface one clear prompt to either:

- create and register a new knowledge repository, or
- bind an existing adopted knowledge repository.

If the operator declines, the harness should remain explicitly unbound and stateful workflows should continue to fail closed with actionable diagnostics until binding completes.

## Acceptance criteria
- First post-bootstrap restart on an unbound machine produces one actionable knowledge-repo setup/link prompt.
- The prompt can create/register a new knowledge repo or bind an existing adopted one without requiring config-file internals.
- Declining leaves the harness explicitly unbound and stateful commands hard-blocked.
- Machine-local config writes needed to finish setup remain allowed.
- Clean-room coverage starts from a fresh clone with no machine-local harness config, restarts after plugin install, and exercises both the prompt and the still-blocked stateful path when the prompt is declined.

## Explicit, provider-aware knowledge onboarding cohort

*The following contribution was assisted using Copilot.*

I am expanding this existing OPEN first-restart onboarding tracker to receive
#3030, #4827 and #5528 as distinct unfinished workstreams in the same supported
create-or-bind flow. Their original substantive descriptions are preserved
below with private/example attribution references removed. The original
#3004 prompt remains the entry contract, not replaced by a settings panel.
This is planned-work consolidation, not an implemented fix.

### Direct Plan

- [ ] KO1 (#3004) - On the actual first restarted model session after
  bootstrap/plugin installation, offer one actionable create/register-new
  OR bind-existing-adopted knowledge flow without config-file internals.
  Honor prior topology, explicit selection and knowledge versus executable
  harness roles. A produced guidance file, receipt, optional Manager panel
  or native first-touch fixture without an AI is not model admission.
  Decline leaves the harness explicitly unbound; stateful commands remain
  hard-blocked with actionable diagnostics and no fallback into another
  repository. Legitimate machine-local setup/registration writes stay allowed.
  Compose current source-owned/exact-session guidance, not a retired hook.
- [ ] KO2 (#4827) - Make the confirmed knowledge-binding offer a normal
  setup phase once core prerequisites are available. When no prior choice
  exists, present the consuming harness's conventional private-repository
  candidate as the preselected safe default for an operator without special
  requirements. Generic plugin guidance stays repository-name-neutral.
  Preserve confirmation, explicit prior roles, existing checkout, remote
  clone, new dedicated repository and advanced/non-GitHub/provider choices.
  Explain ordinary default versus advanced exceptions. Normal offering is
  not compulsory authority, automatic binding or a new identity store.
- [ ] KO3 (#3030) - Expose and consume one shared NON-MUTATING registration/
  landing-policy preflight before choosing authentication or changing the
  local default branch. Distinguish canonical registration from fallback,
  unresolved and unverified discovery; resolve canonical remote/provider
  and the correct scoped account owner, not a short-name assumption or
  personal issue-routing provider. Report effective server landing mode
  only when proven, with explicit unknown, insufficient-permission and
  unsupported outcomes. Direct-commit defaults and writable-account
  readiness do not override enforced PR policy. Reuse shared discovery,
  not parallel harness-side provider logic.
- [ ] KO4 (#3030) - If a push was already rejected after local mutation,
  preserve the commits and offer the owning provider's bounded supported
  review/landing recovery. No reset, data loss, force push, policy bypass,
  mismatched ambient identity or machine-global account switch. Partial
  preflight cannot appear ready for an unsupported mutation or wrong
  authentication ceremony.
- [ ] KO5 (#5528) - For NEW GitHub knowledge repositories, after the first
  push establishes the default branch, create OR verify effective active
  deletion and non-fast-forward/force-push protection targeting that branch.
  Recognize equivalent existing protection/rulesets idempotently; add no
  agent-usable bypass and no PR requirement. Keep ordinary direct commits/
  fast-forward writes and separately unprotected feature branches usable.
  Inspect adopted GitHub repositories and report equivalent/missing/unknown
  protection with an actionable recommendation/readiness result, not an
  automatic rewrite of every historical repository.
- [ ] KO6 (#5528) - Use repository-scoped GitHub identity without global
  account switching. Document actual non-GitHub/provider-specific outcome:
  equivalent provider implementation, explicitly tracked follow-up OR
  unsupported readiness is acceptable. Non-GitHub work must not block the
  finite GitHub safeguard or silently report a repository protected.
  The recommended direct-write setup does not bypass server PR policy.

### Direct Validation

- [ ] KOV1 - Retain #3004's full clean-room fresh clone/no machine-local
  harness config -> bootstrap/plugin install -> actual first restart case.
  Cover prompt visibility, both create/register and existing-adopted choices,
  decline/unbound/blocked stateful commands and allowed local setup writes.
  Preserve prior roles/selection and current guidance delivery. Separate
  helper/native first-touch results from model-facing and installed evidence;
  #7205's recorded no-AI tests do not satisfy this whole case.
- [ ] KOV2 - For the finite #4827 guidance contract, verify normal setup
  invocation, actual consumer-supplied conventional/safe-default wording,
  confirmation and advanced/provider alternatives. Do not invent a fresh
  native or whole future state-authority gate for that documentation lane.
  #4830 was closed unmerged; its proposal/merge-commit field is not delivery.
- [ ] KOV3 - Cover canonical/fallback short-name resolution, scoped GitHub
  and non-GitHub origins, proven direct and server-enforced-PR policy,
  missing/permission-denied/unsupported evidence and rejected-push recovery
  preserving local commits. Registration/role readiness from #1479/#7205
  is partial credit, not proof of server landing policy or recovery.
- [ ] KOV4 - At the supported creation/adoption/protection consumers, verify
  new GitHub default-branch deletion/force-push refusal after first push,
  repeated setup/equivalent-policy recognition, permitted direct/fast-forward
  default writes and unaffected unprotected feature branches. Cover existing
  repository readiness and scoped identity plus explicit provider outcomes.
  Do not test destructive commands against an operator repository here.
- [ ] KOV5 - Keep source fixtures, actual provider/native/installed results,
  required review/CI, receiving acceptance and any separately authorized
  rollout distinct. Each lane retains its own original evidence altitude;
  this intake supplies no implementation, live setup or protection result.

### Continuing ownership and nonmembers

Existing knowledge binding, canonical registration, configuration, account/
provider policy and consuming-harness setup owners retain their implementation
and resource duties. Assignments, reservations, holds, claims, credentials,
prior selection and accepted or uncertain operations are unchanged. Earlier
comments retaining these independent outcomes are preserved as direct lanes,
not silently declared completed or handed to the optional Manager.

#3005's broader first-class related/lifecycle synthesis remains independently
OPEN and is NOT subsumed by this onboarding contract. No knowledge repository
must become an executable launch project, no issue backend is imposed and no
operator-specific repository name is introduced.

The parent remains OPEN and every product/validation item above is unchecked.
No actual auth, registration, binding, repository creation, branch/policy
mutation, clone, push, clean-room/model launch, installed update, source/
resource transfer or cleanup was performed or authorized by this consolidation.


### Preserved original scope from #3030 (sanitized examples)

## Summary

Fresh knowledge-repo onboarding needs a provider-aware preflight in the shared `binding-knowledge` / `agent-worktrees` registration flow. Today a short repository name can be treated as if it were enough to pick GitHub auth and a local `direct` landing profile, even when the canonical remote resolves elsewhere and the server requires pull requests on the default branch.

## Why this belongs upstream

The harness can sequence the checks, but it should not reimplement provider discovery or landing-policy inspection. The shared registration surface needs to expose enough preflight facts for callers to:

- resolve canonical registration and remote provider before starting an auth flow,
- route auth/readiness to the correct provider (for example GitHub vs Azure DevOps),
- treat missing landing policy as unknown instead of defaulting to direct push, and
- detect provider-enforced PR requirements before mutating the local default branch.

## Requested contract

Please expose a provider-aware, non-mutating preflight result from the registration / landing-policy interfaces that includes at least:

- canonical remote/provider identity,
- whether the repo is canonically registered vs fallback-discovered,
- the effective landing mode when known,
- an explicit `unknown` / `insufficient-permission` state when the landing mode cannot be proven, and
- a bounded recovery path when a push is rejected after local mutation has already happened.

## Notes

Consuming-harness orchestration is separate; this issue tracks the shared interface gap rather than a provider-specific workaround.


### Preserved original scope from #4827 (sanitized examples)

Make knowledge-repository binding a normal phase of stateless-harness onboarding instead of a separately discovered optional step.

The binding flow should still ask for confirmation, but present the conventional private dotfiles repository as the preselected default. Operators who already know they need a separate repository or another provider can choose the advanced alternative. The generic skill must not hardcode a particular repository name; consuming harness guidance supplies the conventional candidate.

Acceptance:
- setup/onboarding guidance invokes knowledge binding after core prerequisites are available;
- the prompt clearly says the default is safe when the operator has no special repository requirement;
- existing checkout, remote clone, new dedicated repo, and non-GitHub/provider-specific alternatives remain supported;
- documentation distinguishes the conventional default from advanced exceptions.

### Preserved original scope from #5528 (sanitized examples)

## Problem

The `binding-knowledge` skill can create a new private GitHub repository for an operator's personal knowledge/state, seed its default branch, and adopt it as a direct-commit, low-ceremony repository. The creation flow does not currently establish any server-side protection against deleting or force-pushing that default branch.

This leaves a common agent-operated repository, especially a personal `dotfiles` knowledge repo, unnecessarily exposed to an unexpected destructive command. An agent with repository contents write access could rewrite the visible default-branch history with a force push. Recovery may be possible from another clone or retained reference, but that should not be the primary safety boundary.

This is independent of requiring pull requests. Knowledge repositories can remain direct-commit and low-ceremony while still rejecting branch deletion and non-fast-forward updates to the default branch.

## Proposed behavior

For newly created GitHub knowledge repositories, after the initial commit has established the default branch, `binding-knowledge` should create or verify an active repository ruleset targeting the default branch with:

- deletion prohibited;
- non-fast-forward updates / force pushes prohibited;
- no pull-request requirement;
- ordinary feature and pull-request branches unaffected;
- no agent-usable bypass added by default.

The flow should be idempotent and should recognize equivalent existing branch protection or rulesets rather than creating duplicates. Existing GitHub repositories being adopted should at least be inspected and surface a clear recommendation or readiness result when these protections are absent.

## Provider scope

GitHub repositories, particularly private personal dotfiles repositories, are the high-value initial path and should be implemented concretely.

Other providers need equivalent consideration. For example, an Azure DevOps knowledge repository would need its matching branch-policy configuration rather than a GitHub ruleset. It is reasonable for maintainers to decide whether non-GitHub providers should be implemented in the same change, reported as a provider-specific follow-up, or surfaced as unsupported readiness. The GitHub safeguard should not be blocked on resolving every provider.

## Acceptance criteria

- A newly created GitHub knowledge repository receives effective default-branch deletion and force-push protection after its first push.
- Re-running the setup is idempotent and does not duplicate equivalent policy.
- Direct commits and fast-forward pushes to the default branch remain allowed.
- Feature branches remain force-pushable unless separately protected.
- Existing-repository adoption reports whether equivalent protection is present.
- Repository-scoped GitHub identity is used; setup does not switch the machine-global active account.
- Documentation and validation cover the protection behavior and the non-GitHub/provider-specific outcome.


## Complete discussion

### Comment 6315611482

*The following contribution was assisted using Copilot.*

Retain the first-post-bootstrap ambient setup and declined-path qualification, while crediting the existing guidance. state_root already renders an explicit unbound warning, a binding-knowledge create/link action and a statement that declining leaves state writes blocked. The setup skill and native canonical-registration/first-touch machinery are real. Those facts do not prove that a fresh first restarted model session receives one actionable setup prompt, or that the complete fresh-clone/install/restart/decline case has been qualified.

Residual acceptance:
- [ ] From a fresh stateless harness clone with no machine-local harness config, complete bootstrap/plugin installation and restart through the supported launch path. Establish actual first-session visibility/admission of one clear create-or-link prompt, not only that a guidance file or a resolved local path exists.
- [ ] Offer supported create/register-new and bind-existing-adopted knowledge flows without requiring configuration-file internals. Preserve an operator's explicit prior topology/selection and valid knowledge versus executable-harness roles; do not guess a companion or turn knowledge into a launch project.
- [ ] Declining leaves the harness explicitly unbound. Stateful commands remain hard-blocked with actionable diagnostics and no fallback into the harness or another repository; a hygiene warning alone is not that command contract.
- [ ] Permit the machine-local configuration/registration writes needed to complete binding while preserving the stateless committed harness and external-state write boundary.
- [ ] Retain clean-room coverage of the actual first post-install restart, both setup choices and the declined/blocked stateful path. Distinguish helper/native first-touch fixtures from model-facing startup and from an installed/adopter qualification; no AI was launched in #7205's recorded first-touch tests.
- [ ] Compose current source-owned/exact-session guidance delivery rather than restoring a retired additionalContext aggregation method. A producer write or receipt is not by itself proof of model admission or an operator prompt.

This is the ambient/setup member of the triad: #3030 still owns provider/landing-policy preflight and #3005 still owns first-class related/lifecycle synthesis. None is completed merely by the others resolving a path.

Evidence references:
- Complete body/comments/timeline: https://github.com/ThomasMichon/copilot-extensions/issues/3004 ; open, unassigned, unlocked, zero comments. Fresh clone/no local config, restart and declined stateful path are explicit original acceptance.
- https://github.com/ThomasMichon/copilot-extensions/blob/9c4efed28c44a9b2644dee723d997a7c7eccbcab/plugins/agent-worktrees/src/agent_worktrees/state_root.py#L949-L978 : concrete unbound conduct, supported create/link action, no personal-state fallback and declined-binding warning.
- plugins/agent-worktrees/src/agent_worktrees/session_context.py:178-219 resolves state/pair and renders bounded checkout/state facts. A computed snapshot is not the complete first-restart model-admission proof.
- plugins/harness-knowledge/README.md: payload-only plugin has no independent runtime/hook/installer; agent-worktrees session conduct owns live binding/pairing/routing. Existing setup is not a second startup authority.
- Provider-confirmed MERGED #1479, d5b677f569fc94d815d084c9921955bd6307a7be, 2026-09-01T00:48:34Z: https://github.com/ThomasMichon/copilot-extensions/pull/1479 ; canonical knowledge-binding readiness, not the full ambient first-restart case.
- Provider-confirmed MERGED #7205, 991cfd8e23750b3c82af92fd537034b1914b5da8, 2026-10-19T05:57:11Z: https://github.com/ThomasMichon/copilot-extensions/pull/7205 ; explicit harness/knowledge roles and real native first-touch consumers, with no AI launched and broader adoption/installed-runtime qualification expressly separate.
- plugins/harness-knowledge/skills/binding-knowledge/SKILL.md preserves prior selection, native canonical registration, knowledge-class identity, machine-local configuration and unsupported-reader gates.
- efforts/active/gateway-ssh-fleet-onboarding/README.md retains first-touch role/configuration owner boundaries; visions/plugins/agent-worktrees/README.md distinguishes state/worktree hygiene and durable lifecycle ownership.
- Frozen Dev 9c4efed28c44a9b2644dee723d997a7c7eccbcab and main eed1c608b31fba60e805b1a654de60e1d9a4637d have equal inspected binding/guidance implementation bodies. Only the harness-knowledge plugin manifest version differed in the compared subtree; no live update/restart or clean-room run was performed.

[Inspected released source](https://github.com/ThomasMichon/copilot-extensions/tree/eed1c608b31fba60e805b1a654de60e1d9a4637d). Reconciliation: #7556.

<!-- backlog-reconciliation:7556:batch-21:issue-3004 -->

### Comment 6358920274

*The following contribution was assisted using Copilot.*

Retain the actual first-post-bootstrap restarted-session setup prompt and declined-path qualification, while crediting existing unbound conduct and native first-touch readiness. A produced guidance string, exact-session file, resolved path or receipt does not establish that the first real model session receives one actionable prompt. Actual7205 explicitly launched no AI and did not claim complete installed/adopter qualification.

Preserve create/register-new OR bind-existing-adopted without config internals, prior topology/selection and knowledge versus executable-harness roles. Decline stays explicitly unbound, stateful writes fail closed without fallback, and legitimate machine-local setup writes remain allowed. Qualify fresh clone/no local config -> install -> actual restart -> both choices/decline, using current source-owned session guidance rather than a retired aggregation hook. Keep provider-policy3030 and related/lifecycle3005 independent.

Evidence references:
- Fresh complete paginated collaborator-roster verification admitted the original author before body access. Eligible comments were filtered before rendering; metadata and membership are rechecked before publication. Nonmember-origin issues remain untouched and count in the whole-queue census.
- https://github.com/ThomasMichon/copilot-extensions/issues/3004 : full original/member six-row residual discussion/timeline/no edits; final OPEN/unlocked/unassigned.
- https://github.com/ThomasMichon/copilot-extensions/pull/1479 : actual MERGEDd5b677f569fc94d815d084c9921955bd6307a7be; FULL body null. Current readiness/conduct and real7205 consumer establish partials, not title-based ambient completion.
- https://github.com/ThomasMichon/copilot-extensions/pull/7205 : FULL actual MERGED991cfd8e23750b3c82af92fd537034b1914b5da8, dev; typed roles, canonical registration/config namespace, real ordinary/bare first-touch pairing and matching/conflict refusal. No AI launched; complete stock-OS/installed adoption explicitly separate.
- https://github.com/ThomasMichon/copilot-extensions/blob/7653a19411947e5ec0f059f374b5e1377d3a9518/plugins/agent-worktrees/src/agent_worktrees/state_root.py#L973-L983 : actual create/link conduct and declined-state-write warning; text generation is not model admission.
- https://github.com/ThomasMichon/copilot-extensions/blob/7653a19411947e5ec0f059f374b5e1377d3a9518/efforts/active/gateway-ssh-fleet-onboarding/README.md : complete Plan315-367 AND Validation368-400/Coordination read. Existing typed-role/native first-touch credit, not the full ambient prompt or permission to take fleet/config owners.
- https://github.com/ThomasMichon/copilot-extensions/blob/7653a19411947e5ec0f059f374b5e1377d3a9518/plugins/harness-knowledge/skills/binding-knowledge/scripts/bind_knowledge.py#L256-L308 : canonical registration/readiness facts remain owner-provided, not a second harness discovery authority.

[Inspected released source](https://github.com/ThomasMichon/copilot-extensions/tree/9489a0fe8e4284538c3e00d5aaad2ac0cf474f75). Reconciliation: #7556.

<!-- backlog-reconciliation:7556:batch-78:issue-3004 -->

### Comment 6394208950

*The following contribution was assisted using Copilot.*

I retain the independent ambient first-restarted-model knowledge-binding outcome. The optional Manager workbench does not receive this whole contract. Preserve one actual first-post-install create/register-new OR bind-existing-adopted prompt, prior topology/knowledge roles, explicitly unbound decline, hard-blocked stateful writes without fallback and legitimate machine-local setup writes. Original fresh-clone/no-local-config/install/restart/both-choice/decline qualification remains; guidance production, native first-touch without an AI or a binding panel is not that proof. #3030/#3005 remain separate.

Evidence references:
- https://github.com/ThomasMichon/copilot-extensions/issues/3004
- https://github.com/ThomasMichon/copilot-extensions/pull/8874

[Reviewed graph-first consolidation plan](https://github.com/ThomasMichon/copilot-extensions/pull/8687). No new implementation or released-runtime completion is claimed. Reconciliation: #7556.

<!-- backlog-reconciliation:7556:batch-103:issue-3004 -->

### Comment 6678410713

I am taking a narrow guidance correction from source handle `dusty-contraption`: remove binding-knowledge's unconditional "no PR gate" instruction and require resolved provider identity before authentication and repository/provider policy before publication. This is a below-altitude contradiction fix within KO3/KO4, not implementation or acceptance of the shared server-policy preflight. Unknown or insufficient policy evidence stays explicit, and existing commits, registration/binding owners and live-operation holds remain unchanged. I will not create or bind a repository, change protection or credentials, or perform a provider write trial for this documentation change. The larger #3004 Plan and Validation items remain open.

> 🤖 *AI-generated by Copilot on behalf of the operator; not human-reviewed. Weigh its claims accordingly.*

### Comment 6678684209

The bounded guidance correction #10908 is confirmed merged at 06:23:53 UTC as `bc9ad742a90d106de52927ab4ca7b253a288a8ee`. Genuine current-head Copilot approval5796507028, empty inline comments and all required CI42173364030 pass; normal scoped merge authority is used without bypassing CI. The skill no longer instructs that every knowledge repository has no PR gate, and it distinguishes provider/authentication, binding readiness and publication policy. Local independently permitted binding is not blocked.

This completes only that contradiction correction. The shared non-mutating preflight, server-policy/provider consumers, rejected-push recovery and the actual first-restarted-model/decline cases remain under the existing KO/KOV Plan. No API, authentication, binding, repository/protection mutation, live publication trial, installation or rollout is credited. Containing release remains pending. #3004 remains open.

> 🤖 *AI-generated by Copilot on behalf of the operator; not human-reviewed. Weigh its claims accordingly.*

<!-- ADMITTED_PACKET_BYTES_END -->

[Back to cohort overview](knowledge-pair-receiving-cohort.md) · [Campaign README](README.md)
