# Reliable Git and Daemon Environments

## Goal

Make Git-dependent consumers reliable when a long-running process has a stale
environment: use one shared explicit-aware absolute executable resolver and
fresh per-child daemon environments. Preserve the canonical Git/Copilot
mapping model without turning the repair into another copied identity or
repository-policy qualification layer.

- **Slug:** `reliable-git-environments`
- **Repo:** copilot-extensions
- **Branch(es):** managed source worktree; sequential plan and implementation PRs
- **Created:** 2026-11-16
- **Status:** Done for finite source scope; implementation #12974 merged, archive and native worktree/claim settlement retained in #12939
- **Vision:** [Worktree identity and native mapping](../../../../visions/plugins/agent-worktrees/README.md#repository-and-worktree-identity);
  [authoritative runtime replacement](../../../../visions/installer/README.md#authoritative-runtime-replacement)
- **Umbrella issue:** #12939
- **Related owners:** #12427 / #12433 and #10212

## Guiding Intent

An installed Git executable must not become unusable merely because a resident
inherited an older PATH. Explicit selection and portable/nondefault installs
remain first-class. Fix the real dependency and launch context, not a local
ProgramFiles guess, permanent absence cache or a requirement to restart
Copilot. Executable discovery must not replay a possibly started operation.

Git owns checkout/worktree membership; Copilot owns sessions and workspace CWDs.
The proactive operational map is derived from those facts, while durable
workflow state and serialized mutation remain intact.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source owner, `hinged-chisel` | Shared Git/environment implementation, tests and PR stewardship | #12939 and this effort |
| Existing registration-policy contributor | Retains #12427 / #12433 | Existing issue and PR |
| Existing checkout-membership owner | Retains #10212 | Existing issue |

## Coordination

- **Topology:** Sequential plan and implementation PRs in one managed source worktree.
- **Host (owns PRs):** Source owner.
- **Delegates:** None.
- **Handoff:** Existing overlapping contributions retain custody; cooperate on
  their actual PR rather than replacing it. Public source delivery and any
  separately authorized installation remain different completion facts.

## Context

#12939 records the generic defect and claim. The current Worktrees Git wrapper
starts bare `git`; plugin activation independently caches Git lookup, including
absence. The current daemon environment starts from inherited process state
and then scrubs selected credentials/session fields.

#12433 changes registration-policy handling, and #10212 addresses Git-backed
checkout membership. This effort must not compete with either. Their
policy/mapping frontier is retained explicitly; refreshing PATH alone does not
declare the broader registration model corrected.

## Request

Public-safe request summary: provide the immediate repair through the existing
installation owner and deliver a durable source fix for shared Git resolution
and fresh daemon environments. Honor explicit Git selection, process PATH,
fresh Windows user/system PATH and installation-registry discovery; cache
successful resolution and rediscover confirmed executable disappearance.
Preserve native mapping reconstruction, write serialization and unknown effects.

This source effort contains only the generic implementation and qualification
scope. It does not publish private operational context or claim a deployment
grant. The immediate operational repair and any adoption evidence have their
own effect owners.

## Scope Approvals

Implement, test and land the generic source correction through the normal
dev PR, review, CI and changefile flow. Synthetic native lifecycle evidence
uses an isolated fixture and its normal lease/cleanup owner. No production
daemon restart, global PATH/account/access change, runtime-portfolio rollout,
installed-code patch or unknown-operation replay follows from this plan.

## Plan

### Phase 1 - Reviewed intent

- [x] Land this source plan and the clarified native-mapping vision before runtime realization.
- [x] Coordinate the mapping/policy boundary with the existing #12433 contributor, preserving that PR's ownership.

### Phase 2 - Durable source delivery

- [x] Select one cohesive shared discovery owner and migrate the addressed real Git consumers to resolved absolute executables.
- [x] Preserve explicit selection and portable PATH semantics; add Windows fresh OS PATH and installation-registry fallbacks without per-app installation guesses.
- [x] Cache successful resolution; do not retain absence forever. Rediscover only confirmed executable disappearance before launch, not missing CWD, timeout or ambiguous creation.
- [x] Build fresh per-child daemon environments with intended runtime overrides and existing credential/session scrubbing, without global environment mutation.
- [x] Keep registration-model correction coordinated with its existing owner; do not extend Git-backed policy reconstruction to solve executable lookup or claim that PATH repair alone implements the native-mapping model.
- [x] Land focused implementation through required source checks, changefiles, review and CI; distinguish merge from promotion and installation.

### Phase 3 - Source closeout

- [x] Record source/test results and remaining independently owned mapping/adoption gates, then archive this finite source effort through the normal flow.

## Validation Plan

- [x] Focused unit controls cover explicit selection, stale/process/portable PATH, Windows fresh PATH and nondefault registry discovery, missing-then-installed Git and positive-cache disappearance.
- [x] No retry follows a possibly started command, timeout or a missing CWD; concurrent consumers do not mutate global environment.
- [x] A real child consumer exercises absolute Git invocation under a stale inherited PATH rather than testing only a resolver return value.
- [x] An isolated native service fixture proves fresh environment plus preserved selectors and credential/session scrubbing, using its existing lifecycle/lease cleanup owner.
- [x] Mapping acceptance distinguishes Git/Copilot reconstruction from copied machine/platform policy and preserves journals, claims and unknown operations; coordinate the existing registration tests rather than weakening them silently.
- [x] Record focused source gates and required PR CI separately from optional integration/native evidence, with honest platform limitations.
- [x] Confirm merged source; report promotion, installation and live adoption separately.
- [x] Deferred to `#12939`: Confirm final source-worktree/claim settlement after this archive change lands through its own review and required CI.

## Proposal

Prefer an existing suitable dependency-discovery owner. If none is cohesive,
use a shared process/dependency helper rather than application-local Git
installation lists. Windows registry metadata is a fallback, not the sole
portable-install path. Fail explicit invalid selection honestly instead of
silently switching to another executable.

Use the existing daemon lifecycle and cutover contracts. This changes neither
endpoint/protocol semantics nor cancellation/retirement authority. Evaluate
[authoritative runtime replacement](../../../../visions/installer/README.md#authoritative-runtime-replacement)
by incoming readiness and all current selections, preserving sessions/data
and treating outgoing retirement independently. A synthetic fixture is not a
production rollout.

## Journal

### 2026-11-17 - Source completion and bounded closeout

#12974 merged to `dev` at `2026-11-17T23:04:07Z`, squash
`6c23b5e9c98a50c23f3c2e03c1c3410afb9b8516`. The accepted source head
`9e45864446dd60f1c7c9f9ee47dfba8811a6c909` received Copilot approval
`5948087440`, with no unresolved threads. Required CI `44192746900`
passed admitted and whole-file strict type contracts, full source guards,
exact-input unit evidence with positive production call coverage, and the
required PR gate. Native `44192805343` passed Linux and Windows publication,
actual-client and generation-handover controls; legacy monitor acceptance
`44192733625` also passed on that exact head.

The final combined contained Git, daemon and credential-isolation packet
passed 148 tests with one explicit platform skip. All 31 actor-free
composition controls remain admitted. The real leased Windows child now
exercises a portable copy of the real GitHub CLI, production account-token
acquisition and actual Git credential-helper lookup against a private
synthetic account, with no server request or real credential access.
Only the selected executable's anchored directory accompanies fresh OS PATH;
the existing non-secret profile selectors and token/session exclusions remain.
The fixture waits for its exact child before parsing its result, closing a
created-but-unwritten-file race without enlarging its bounds.

Earlier failed native attempts remain diagnostic evidence. The separately
owned fixture correction landed through #13056; broader #12357 is not a
remaining dependency. No accepted-flake exception, retrospective pass or
competing MCP correction is claimed. The earlier local canonical typecheck
dependency-preparation failure is retained; the final exact-head CI gate
supplies its required canonical qualification.

Unit and real-consumer tiers passed. The disposable native Linux/Windows
workflow supplied available external-venue evidence. A fresh OS image and
native macOS lane were unavailable, so neither is claimed; fresh fixture
roots do not substitute for first-machine provisioning. This finite change
does not install a runtime or add a new provisioning path.

Existing mapping/registration work remains with #12433 and #10212. Normal
promotion remains with #3336; installation and any live adoption require
their own authorized owners. The current issue #12939 retains only this
archive's publication and subsequent native worktree/claim settlement.
Those post-publication effects are not claimed complete in this commit.

### 2026-11-17 - Composition and consumer follow-through

Mechanical process/launch-context extraction meets the unchanged module caps.
The contained Git/environment/nested-isolation packet passed 139 tests with
external configuration unchanged. Required CI then passed on `06d064d88f`.
Fresh review identified three further consumer edges: launcher-only mux PATH,
POSIX relative Git PATH under a different child CWD, and environment failure
escaping detached-spawn recovery.

The corrections preserve the absolute mux selection, distinguish automatic
POSIX lookup from launcher-relative explicit selection and native Windows
lookup, and retain the guarded spawn refusal. Seventeen actor-free controls
pass; the corrected contained packet passes 145 tests with one Windows-only
skip of POSIX empty-PATH semantics. Nine addressed whole-file strict contracts
pass for both platforms. Updated-head review and required CI remain open.

The separate Windows native publication failure is the exact fixture teardown
race tracked in #13057, already owned by #12357. Failed evidence is retained
and attached to that issue; no competing correction or accepted-flake exception
is claimed. Fresh qualification remains required after its owner's correction.

### 2026-11-16 - Source plan

Captured the generic dependency/environment defect, clarified the native
mapping intent and separated overlapping registration-policy ownership.
No runtime source implementation is included in this planning change.

### 2026-11-17 - Reviewed base and source implementation

#12941 merged after current-head Copilot approval and required green CI.
Implementation uses `plugin_resolve.git` for shared absolute discovery and
`plugin_resolve.environment` for fresh per-child OS environments. Worktrees
and plugin activation adopt the resolver; only future Windows monitor launches
adopt fresh environments. Existing contained-test roots remain authoritative
inside their cleanup boundary.

The focused discovery packet passed eight controls. The focused Worktrees
packet passed 145 controls, including actual Git invocation and the isolated
leased native daemon plus existing native interpreter composition.
Activation's existing symlink case requires traversal, not creation alone;
this Windows execution policy refuses traversal, recorded as a skip. Its
cross-process memo assertion now distinguishes unchanged input evidence from
concurrent shared-ancestor changes while preserving all A-to-B-to-A
invalidation controls. No production identity-policy relaxation is included.

Linux/macOS native execution and a disposable fresh OS image were unavailable
in this local Windows lane; real Windows child/daemon fixtures supplement
portable unit controls, not those unrun platform/clean-room tiers. No external
deployment lane is needed or claimed for source-only delivery. Required PR CI
and review remain separate publication gates.

Review corrections keep the shared implementation in the existing lightweight
`plugin_resolve` dependency, synchronize real payload copies, preserve
unavailable discovery results, tolerate a deleted caller CWD and anchor
relative configured paths before daemon CWD changes. Whole-file strict typing
of the addressed production wrappers and tests now passes without suppressions;
the final canonical contained Worktrees packet passed 152 tests with repository
configuration unchanged. Text/binary return contracts and typed callback/mock
boundaries retain the assertions and no-replay behavior.

The credential negative fixture no longer assumes a caller's basetemp is
outside every checkout: its Git probe is substituted and any attempted
credential-write command fails the unit. A separate real nested-consumer
regression uses the canonical environment and containment owners, deliberately
places basetemp inside a disposable enclosing repository, and checks private
home/config/project bindings, a successful synthetic inner-repo pin and an
unchanged external sentinel configuration. The contained regression passed;
no live configuration is used as the sentinel.
