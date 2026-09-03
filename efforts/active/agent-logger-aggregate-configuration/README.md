# agent-logger Aggregate Configuration

- **Slug:** `agent-logger-aggregate-configuration`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees and pull requests
- **Created:** 2026-09-03
- **Status:** Draft
- **Vision:** [`repository-owned-aggregate-configuration`](../../../visions/plugins/agent-logger/README.md#repository-owned-aggregate-configuration) · [`explainable-machine-plan`](../../../visions/plugins/agent-logger/README.md#explainable-machine-plan) · [`resolve-before-side-effects`](../../../visions/plugins/agent-logger/README.md#resolve-before-side-effects) · [`reject-cross-repository-collisions`](../../../visions/plugins/agent-logger/README.md#reject-cross-repository-collisions) · [`configuration-is-authorization`](../../../visions/plugins/agent-logger/README.md#configuration-is-authorization)
- **Umbrella issue:** #1817
- **Sub-issues:** Pending decomposition

## Guiding Intent

Make one installed agent-logger safely serve several repository owners on the
same machine. Each repository should be able to publish portable collection and
rendering intent, specialize that intent for a machine, and accept a
repository-scoped local override without gaining authority over another
repository's claims.

The runtime must compile those declarations into one deterministic,
explainable plan before it performs any collection, pruning, rendering, or
landing. Ambiguous ownership is an error to repair, never a reason to guess
which repository wins.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Primary contributor | Owns design, implementation slices, pull requests, and validation | Isolated copilot-extensions worktrees |
| Copilot reviewer | Reviews each proposed or implemented slice | Repository pull-request review |

## Coordination

- **Topology:** Independent per-slice pull requests, merged serially.
- **Host (owns PRs):** Primary contributor.
- **Delegates:** None initially; bounded implementation or review slices may be added here before dispatch.
- **Handoff:** The effort README, issue state, merged pull requests, and Journal are the durable relay.

## Context

The revised [agent-logger vision](../../../visions/plugins/agent-logger/README.md)
defines repository-owned declarations, same-repository precedence,
collision-free cross-repository composition, an explainable normalized machine
plan, and fail-closed authorization before side effects. #1817 is the public
coordination issue for closing the delta between that intent and the current
runtime.

Current reality is centered on one layered `Config`:

- built-in defaults;
- one machine-local `$AGENT_LOGGER_HOME/config.yaml`;
- one current-repository `.agent-logger.yaml`, intentionally restricted to
  log-presentation fields;
- selected environment overrides.

Session sync and the chronicler consume that merged object directly.
`agent-logger config` reports a summary of the one resolved object, target
implementations expose narrow doctor checks, and there is no aggregate
repository discovery, declaration provenance, normalized claim model, or
cross-repository collision analysis.

The implementation must preserve the safety boundary of the existing
repository-local log configuration while introducing a distinct,
versioned repository policy surface. It must remain useful without embedding a
particular control-repository name, machine inventory, or private topology.

## Request

> “This sounds good. Get this revision added upsream, then make an effort to start building it out.”

## Plan

### Phase 1 — Specify the resolution contract

- [ ] Define the versioned repository declaration and repository-scoped local-override contracts without overloading the existing log-presentation-only file.
- [ ] Define a portable discovery-provider seam and the first concrete discovery path, including stable repository identity, declaration provenance, and an explicit machine-local admission registry.
- [ ] Require one admitted authoritative checkout per canonical repository identity; treat declarations from unadmitted repositories and secondary worktrees as inert but diagnosable.
- [ ] Define source selectors with canonical repository identities and an anchored, decidable pattern grammar; retire substring matching from ownership claims and specify how legacy substring lists map during compatibility.
- [ ] Define explicit exclusions or cession, machine selectors, collection claims, rendered-log claims, targets, profiles, and landing policies.
- [ ] Define deterministic same-repository precedence and reject equally specific or otherwise ambiguous machine matches.
- [ ] Model the machine-default fallback as a first-class bounded claim that participates in overlap analysis rather than an implicit catch-all.
- [ ] Define declaration-schema compatibility, including how unknown versions are quarantined or disabled machine-locally without granting another repository precedence.
- [ ] Make aggregate resolution independent of process cwd and ordinary environment overrides; only admitted, provenance-bearing inputs may create or suppress claims.
- [ ] Define canonical normalization, overlap witnesses, error taxonomy, and the versioned resolved-plan JSON contract.
- [ ] Define ownership-change semantics for already-rendered records and reservation state; default to applying new ownership only to newly due work unless an explicit migration is requested.
- [ ] Carve implementation slices into public sub-issues before code changes begin.

### Phase 2 — Build the aggregate compiler

- [ ] Add typed declaration, provenance, selector, claim, target, and resolved-plan models.
- [ ] Load committed declarations and repository-scoped machine-local overrides through the discovery seam.
- [ ] Resolve each repository independently, then aggregate declarations without cross-repository precedence.
- [ ] Deduplicate multiple checkouts and worktrees of one admitted repository identity against its authoritative checkout before claim analysis.
- [ ] Canonicalize repository and target identity so basename aliases or path spelling cannot evade collision checks.
- [ ] Detect exact, wildcard, exclusion, target, and ownership-dimension collisions with actionable witnesses.
- [ ] Produce byte-stable normalized JSON for equivalent inputs independent of discovery order.

### Phase 3 — Add diagnostics and operator surfaces

- [ ] Extend `agent-logger config` with an explicit resolved aggregate JSON surface while preserving compatibility for existing callers.
- [ ] Add an aggregate `agent-logger doctor` command that uses the compiler result rather than reinterpreting configuration.
- [ ] Report declaration provenance, selected and inactive machine clauses, overrides, normalized claims, canonical targets, sink readiness, and conflicts.
- [ ] Define stable exit behavior for empty/passive, valid/authorized, invalid, unavailable, and conflicting plans.
- [ ] Repoint or deprecate `agent-logger chronicle status` so it reports the same compiler result rather than a third interpretation of configuration.
- [ ] Keep diagnostics read-only and safe to run while scheduled work is disabled or maintenance-gated.

### Phase 4 — Preserve compatibility and observe the delta

- [ ] Map legacy sync allowlists, denylists, chronicle routes, skips, and default sinks into explicit compatibility claims with visible provenance.
- [ ] Ship the compiler and diagnostics in observe-only mode before they control side effects.
- [ ] Report the normalized aggregate plan alongside the legacy plan and highlight any behavioral delta, ambiguity, or future collision.
- [ ] Add an explicit enforcement switch and documented rollback that restores legacy execution without discarding declarations or diagnostic evidence.
- [ ] Provide setup and migration guidance before any release can make an empty aggregate passive.

### Phase 5 — Gate collection and chronicling

- [ ] Adapt session sync to consume only authorized collection claims from the resolved plan.
- [ ] Adapt chronicler construction and routing to consume only authorized rendered-log claims and sinks.
- [ ] Refuse scheduling, copying, pruning, reservation, rendering, and landing when aggregate resolution fails.
- [ ] Ensure force or recovery flags cannot bypass an unauthorized aggregate plan.
- [ ] Preserve existing input fencing, catch-up idempotency, transaction rollback, and landing serialization guarantees.
- [ ] Preserve already-landed records across ownership changes; route only newly due work to a new owner unless an explicit migration workflow is invoked.

### Phase 6 — Adoption, migration, and deployment

- [ ] Document declaration ownership, machine specialization, local overrides, explicit wildcard exclusions, and collision repair using synthetic examples.
- [ ] Update configuration and architecture docs to distinguish legacy runtime configuration, repository log presentation, and aggregate operational policy.
- [ ] Add setup or migration guidance that never writes committed repository policy during install or update.
- [ ] Bump all required agent-logger versions with each implementation pull request.
- [ ] Deploy the merged runtime through the unified update flow and validate a passive machine before enabling real collection policy.

## Validation Plan

- [ ] Two disjoint repository declarations resolve identically regardless of discovery order.
- [ ] A declaration in an unadmitted checkout is inert and reported as unadmitted.
- [ ] Three worktrees of one admitted repository do not self-collide, and changes in a non-authoritative worktree do not alter the machine plan.
- [ ] An exact claim colliding with another exact claim fails with both provenance records and the overlapping source.
- [ ] A wildcard plus a specific claim fails unless an explicit exclusion or cession makes the claims disjoint.
- [ ] Prefix-overlapping repository names remain distinct and cannot cross-capture through legacy substring behavior.
- [ ] Collection and rendered-log ownership are checked independently and may resolve to different owners.
- [ ] A machine-specific clause overrides only its repository's default; a repository-scoped local override cannot affect another declaration.
- [ ] Equally specific machine clauses, malformed selectors, and unstable repository identities fail closed.
- [ ] Unknown declaration versions are quarantined with actionable diagnostics, and a machine-local disable cannot transfer the claim to another repository silently.
- [ ] Repositories with the same checkout basename but different canonical identities remain distinct.
- [ ] Canonically equivalent targets collide even when configured with different path spellings or aliases.
- [ ] The machine-default fallback cannot overlap another repository's explicit claim.
- [ ] Invalid or unavailable targets make the whole plan unauthorized before any filesystem, network, reservation, pruning, rendering, git, or scheduling side effect.
- [ ] Empty configuration leaves the runtime passive with a successful, explainable diagnostic result.
- [ ] Aggregate resolution produces the same plan from any cwd, and environment variables cannot forge or suppress repository claims.
- [ ] Resolved JSON is schema-versioned, deterministic, provenance-rich, and free of secrets.
- [ ] Existing log-presentation-only repository configuration remains bounded to log organization.
- [ ] Existing single-config sync and chronicler tests remain green through the compatibility window.
- [ ] Observe-only rollout reports legacy-versus-aggregate differences without changing collection; enforcement is explicit and reversible.
- [ ] `--force` and equivalent recovery paths still refuse an unauthorized aggregate.
- [ ] Changing ownership leaves existing landed records and reservations stable while newly due work follows the new plan.
- [ ] Linux and Windows path, identity, override, and diagnostic behavior remain equivalent.
- [ ] Equivalent declarations and machine identity produce byte-identical normalized JSON across Linux and Windows apart from explicitly machine-scoped fields.
- [ ] The agent-logger plugin suite, changed-plugin guards, install-contract checks, and relevant clean-room scenario pass before deployment.

## Proposal

_Pending review. Begin implementation only after this effort plan merges._

## Journal

### 2026-09-03 — Kickoff

- Landed the aggregate-configuration vision revision through #1818.
- Confirmed the repository has adopted canonical target-owned efforts.
- Grounded the plan in the current `Config`, CLI, sync, chronicler factory,
  repository organization configuration, and deployment documentation.
- Created this effort against #1817; implementation remains behind the effort
  review gate.
- Incorporated a pre-publication review: added admitted authoritative
  repositories, decidable non-substring claims, multi-worktree handling,
  observe-only migration before enforcement, fallback collision treatment,
  schema quarantine, cwd independence, and stable ownership-change behavior.
