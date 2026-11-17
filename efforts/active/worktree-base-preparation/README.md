# Configured Worktree Base Preparation

## Goal

**Kind:** completion. Prepare a new worktree from its explicitly refreshed
configured default branch without fetching unrelated branch refs, while
preserving committed-policy discovery, safe-anchor behavior and honest
offline fallback.

**Acceptance:** a fresh local-Git consumer proves exact configured-base
selection even when the ordinary remote refspec excludes that branch,
unrelated refs remain untouched, and existing preservation/failure controls
pass. The finite source correction clears required typing, tests, review and
CI and actually merges; canonical acceptance and its issue claim are settled.

**Finalization criterion:** every Plan/Validation item below is accepted or
explicitly transferred, the finite issue is resolved and its claim completed,
and the source worktree's independent obligations are preserved. A plan merge
is not implementation completion, and source acceptance is not rollout.

**Now:** publish the consumer-qualified correction and obtain final-head review
and CI. The causal negative packet executes eight meaningful failures; exact
source `5b346b759f` qualifies all 189 controls, including operation-local failure
caching. Source acceptance remains open until actual merge and finite closeout.
No production authority or another contributor's seed-delivery files are changed.
**Return:** accept only the finite source scope, then record its actual
source result and return to the contributor's independent remaining work.

- **Slug:** `worktree-base-preparation`
- **Repo:** copilot-extensions
- **Branch(es):** separate managed plan and implementation PRs against `dev`
- **Created:** 2026-11-17
- **Status:** Active
- **Vision:** below-altitude correction consistent with
  [repository/worktree identity](../../../visions/plugins/agent-worktrees/README.md#repository-and-worktree-identity)
- **Umbrella issue:** #13013

## Guiding Intent

The configured creation base should determine this operation's fetch scope.
Reuse the existing explicit-branch fetch owner rather than introducing a
timeout knob, retry mechanism, service or second freshness policy.

## Participants

| Participant | Role in this effort | Reached via |
|---|---|---|
| `patient-knob` | Finite planning, source delivery and publication owner | Assigned managed source worktree and #13013 |

## Coordination

- **Topology:** one owner, one managed source worktree, sequential plan/code PRs.
- **Host (owns PRs):** `patient-knob`; original issue/claim publisher ThomasMichon.
- **Delegates:** none.
- **Handoff:** preserve this finite gate and claim through native continuation;
  no independent contributor's work transfers through this plan.
- The [discussion-only reservation](https://github.com/ThomasMichon/copilot-extensions/issues/13013#issuecomment-6937621536)
  remains active until its original publisher records completion or release.
  No native queue reservation is claimed.

## Context

`worktree_creation._prepare_worktree_source` delegates to
`git_ops.prepare_worktree_base`. The latter currently calls `fetch` without
the configured default branch, although `fetch(branch=...)` already supports
an explicit remote-tracking refspec that overrides an excluding ordinary
remote fetch configuration.

#4093 accepted reporting failed-fetch/last-known freshness while retaining
offline creation. Do not silently replace that minimum-warning alternative
with a new fail-closed policy. #4303 concerns an existing requested branch's
resume, not new default-base preparation.

The current contributor changes in #7178 concern failed-create branch cleanup
and a start-point docstring. #11828 changes encoded URL resolution and installer
recovery. Their inspected runtime hunks do not change this fetch call.
Preserve their implementation and shared tests; reconcile actual drift before
publication rather than pre-patching or replacing either PR.

## Request

Agent-derived finite request, captured publicly in #13013:

> Refresh the configured remote/default branch explicitly for new-worktree base preparation. Select that fetched head through the existing start-point owner without refreshing unrelated branch refs merely for this operation.
>
> Preserve the accepted warning/offline fallback from #4093, safe eligible-anchor advancement, local-work protection, authentication and the existing network deadline. No new CLI, schema, timeout policy or dependency is needed.

## Scope Approvals

Source-only planning, focused synthetic validation and the normal contribution
flow are in scope. This plan grants no installed rollout, service intervention,
live repository experiment, cleanup, credential change or other-owner takeover.
Existing-branch resume, finalize/GC fetch policy and cross-machine allocation
custody remain independent.

## Plan

### Phase 1 - Reviewed contract

- [x] Land this finite proposal through the repository's actual review/CI gate.
- [x] Reconcile the current default-base helper and independent contributor
  hunks without reopening their scopes or changing original acceptance.

### Phase 2 - Source delivery and finite acceptance

- [x] Add a few fail-first, fully typed controls using disposable local Git.
- [x] Explicitly refresh the configured creation base and its related policy
  bootstrap through the existing fetch implementation; preserve all subsequent
  start-point and safe-anchor decisions.
- [x] Satisfy whole-file strict changed-code checks without suppressions or
  pending-project admission. Limit necessary typing corrections to unchanged
  behavior; reconcile any material contributor overlap before proceeding.
- [x] Resolve the initial review's actual refmap, policy-discovery,
  fixture-isolation and moved-source-closure findings. Prove the new behavioral
  negatives before revising the fetch, then repeat impacted validation.
- [x] Resolve the subsequent policy-head, source-namespace, independent-ref
  failure and stale-declared-hop findings with real-Git regressions.
- [x] Resolve the later deadline/authentication, unavailable-policy-probe,
  aliased-tracking-source and truthful caller-warning findings.
- [x] Resolve current repository custody, pre-write policy conflicts, effective
  refspecs and cached-bootstrap findings while preserving existing fetch callers.
- [x] Resolve unusable-ref fallback, overlapping remote namespaces,
  command-scoped credentials and native short-source resolution.
- [ ] Pass affected tests, lint, install contract, changefile/version/size and
  exact-head required review/CI, then actually merge the source correction.
- [ ] Record the finite accepted result, settle #13013 and its original claim,
  and preserve every independent workspace and deployment obligation.

## Validation Plan

- [x] **Exact base, fail first:** use a nonstandard configured default branch
  omitted by the ordinary fetch refspec; advance it remotely and prove the real
  helper refreshes/selects its exact head.
- [x] **Ref scope:** advance an unrelated branch too and prove this operation
  does not refresh its remote-tracking ref, including alternate and mirror
  configured mappings.
- [x] **Policy discovery:** the actual offline committed-config reader sees a
  changed bootstrap declaration and the following preparation selects that
  declared branch, without refreshing unrelated refs.
- [x] **Preservation:** retain eligible-anchor fast-forward and existing
  dirty/ahead/diverged/detached/non-default/no-remote controls.
- [x] **Offline reporting:** the actual preparation caller reports the literal
  fetch failure and selected last-known base, preserving the existing fallback.
- [x] **Fresh consumer:** local bare repositories and isolated Git configuration
  exercise actual subprocess/ref behavior without network credentials, operator
  state, installed runtimes or product checkouts.
- [x] **Policy edge cases:** refresh the configured base with a non-symbolic
  optional HEAD; refresh surviving refs in either deletion direction; align a
  non-origin creation remote with the reader's policy namespace; refresh a
  newly declared but already tracked policy source before consuming it.
- [x] **Remaining boundaries:** delayed authentication cannot launch a fetch
  after the shared deadline; unavailable policy probes are not certified
  absent; aliased policy tracking refs retain their configured sources; the
  actual caller reports a refreshed base accurately after policy-only failure.
- [x] **Repository and policy authority:** inherited Git selectors cannot redirect
  any preparation stage; conflicting sources cannot overwrite the actual policy ref.
- [x] **Effective refs:** normalize equivalent sources, apply exclusions before
  ambiguity, reuse a matching refreshed base, refresh a surviving cached-bootstrap
  hop, and preserve the public fetch/reload consumer.
- [x] **Native inputs and fallback:** an unusable tracking ref is never selected;
  overlapping namespaces cannot overwrite either source; authorized scoped
  credentials survive without repository selectors; HEAD/tag short sources
  retain Git's actual resolution.
- [ ] **Publication:** whole-file typing and all applicable exact-source gates
  pass; genuine final-head review and actual merge precede acceptance.
- [x] **Evidence tiers:** document why the local-Git consumer is the relevant
  end-to-end/first-touch proof; use genuinely available supported lanes without
  treating source/CI evidence as installed or live-venue qualification.

## Proposal

Share the existing fetch implementation without changing ordinary
`fetch(branch=...)` callers' configured policy mappings. Only creation preparation
suppresses unrelated refmaps. Keep the existing network bound and literal
base-failure fallback, result field layout and offline policy reader.

**Agent-derived preservation refinement:** scope the complete preparation
operation to its supplied repository. Resolve policy destinations and equivalent
source names before writes; a conflicting base/policy destination must not lose
its authoritative policy contents. Apply ordinary exclusions before judging
ambiguity, while permitting reuse of the matching base that was explicitly
refreshed. If a HEAD refresh fails, the available last-known bootstrap may still
identify a surviving first hop; retain the warning rather than calling it fresh.

This remains a below-altitude correction under the accepted repository/worktree
identity intent and right-sized, cross-platform, truthful-failure patterns. It
adds no public API, schema, service, dependency, timeout knob or rollout.

The current agent-derived refinement additionally excludes unusable tracking
refs from selection, using a verified local default branch or refusing creation.
Destination checks use actual ref names and remote/source identity, not a remote
name equality shortcut. Preserve already-authorized credential configuration
without restoring repository selectors. Git, rather than a blanket branch
prefix, defines short source identity: narrowly resolve explicitly configured
short names when needed within the same acquisition deadline. The ordinary
committed reader remains offline; no default-branch discovery is introduced.

## Journal

- [2026-11-17 - Native source resolution and safe fallback](journal/2026/11.17-native-source-and-fallback.md)
- [2026-11-17 - Policy custody and effective-refspec controls](journal/2026/11.17-policy-custody-controls.md)
- [2026-11-17 - Preparation and qualification history](journal/2026/11.17-preparation-qualification.md)
