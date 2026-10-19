# Remote ACP Provider Contributions

- **Slug:** `remote-acp-provider-contributions`
- **Repo:** copilot-extensions
- **Branch(es):** Independent reviewed proposal and implementation PRs targeting `dev`
- **Created:** 2026-10-18
- **Status:** Draft
- **Vision:** [externally-hosted-acp-agents](../../../visions/plugins/agent-bridge/README.md#externally-hosted-acp-agents), [topology-aware-agent-resolution](../../../visions/plugins/agent-bridge/README.md#topology-aware-agent-resolution)
- **Umbrella issue:** #7218

## Guiding Intent

An already-running ACP agent should be addressable by a configured name or
explicit endpoint through the same bridge catalog and conversation contract.
An enabled capability plugin should supply reusable connection requirements;
the adopter supplies private endpoint and approved credential bindings.
Discovery is not connection, authorization or evidence of readiness.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| drowsy-piston | Proposal, integrated implementation, validation and PR stewardship | Public issue #7218 and its claimed managed worktree |

## Coordination

- **Topology:** Serial, independent PRs targeting `dev`; proposal merges before implementation.
- **Host (owns PRs):** drowsy-piston.
- **Delegates:** None assigned implementation ownership.
- **Handoff:** Record the current slice, evidence and unresolved gates here and transfer the public issue claim explicitly.
- Existing remote transport/session owners and dispatch-catalog efforts retain
  their scopes. This effort consumes the transport from #6908/#6997, not a new
  ACP stack. It does not absorb the independent SSE repair in #7186 or
  dispatch-extension catalog work in #7091/#7143.

## Context

The existing remote ACP configuration, transport and session manager can attach
to configured named endpoints without owning their remote process or workspace.
The ordinary agent roster does not yet expose those targets consistently.
`provider_sources.py` already resolves declarative command-provider manifests
with enabled-plugin provenance. Plugin activation and configuration layering
also have existing owners; installed directories alone do not prove activation.

Remote authentication already has origin/identity/generation isolation and an
injector seam, but daemon-created remote sessions do not yet compose an approved
provider-qualified source with a configurable constrained token scheme.
The shared credential relay owns credential sourcing and HTTP injection.

The proposal extends the vision's explicit target contribution and binding
contract. It reuses existing configuration, plugin activation, provider
discovery, credential relay and remote session abstractions. Concrete adopter
systems, secrets and deployment decisions are outside this public effort.

## Request

**Public-safe summary of the operator request:** Implement generic agent-bridge
URL targets, provider discovery and scoped authentication, so enabled harness
plugins can supply `.agent-bridge/config.yaml` and `providers.d` connection and
authentication requirements and any agent can address the contributed target.
Keep concrete endpoint and credential bindings adopter-private. Publish through
normal reviewed generic-source and consumer-plugin PR flows.

**Execution constraint:** CI availability does not lift the separate
`dev`-to-`main` promotion hold. Do not bypass promotion, update installed
payloads/runtimes, restart a shared daemon, grant live credentials or infer
deployment from a merged PR.

The summary removes consumer-specific identifiers without changing intent.
The implementation decomposition and additional failure-case coverage below
are **agent-recommended**.

## Plan

### Phase 1 - Reviewed contribution contract

- [x] Deduplicate and claim the public coordination issue.
- [ ] Review and merge this effort and the vision delta before implementation.
- [ ] Sync execution forward onto the reviewed plan without dropping ownership.

### Phase 2 - Declarative configuration and target resolution

- [ ] Compose enabled-plugin remote target requirements through existing
  activation/config/provider seams; retain command-provider compatibility.
- [ ] Define and document the minimal target declaration, adopter binding,
  precedence, conflict diagnostics and canonical endpoint identity.
- [ ] Expose named and contributed remote targets through ordinary roster,
  detail and project-context resolution, including explicit `requires-binding`
  or unavailable state for incomplete bindings.
- [ ] Resolve direct HTTPS/WSS targets through an explicitly matching local
  policy and the same remote session manager; refuse unmatched URLs before
  network, credential lookup or workspace/process acquisition.

### Phase 3 - Scoped source binding and conversations

- [ ] Wire an explicitly approved provider-qualified credential source and
  constrained token scheme into daemon-created remote sessions through the
  existing credential relay injector.
- [ ] Preserve origin, identity, resource, scope and generation boundaries;
  reject unknown/ambiguous sources and unsafe header values without fallback.
- [ ] Exercise ordinary create/send/resume, correlated manual approvals and
  remote session identity through the same connector for all target forms.
- [ ] Preserve named-target compatibility, bounded pre-admission auth retry,
  non-replay of uncertain prompts and remote process/workspace non-ownership.

### Phase 4 - Reviewed delivery and closure

- [ ] Update bridge/credential-source configuration docs and agent-facing usage;
  add required plugin changefiles and shared-library consumer validation.
- [ ] Satisfy the validation matrix, address review findings and merge the
  implementation PRs to `dev`.
- [ ] Journal merged evidence, resolve every plan/validation item and archive
  this effort. Transfer any release or adopter acceptance obligation to a
  named tracked owner; never describe a `dev` merge as a running capability.

## Validation Plan

All cases below are **agent-recommended** acceptance detail.

- [ ] Unit tests cover active/inactive/indeterminate plugins, verified payload
  roots, supported empty providers, malformed declarations, duplicate target
  ownership and project/operator precedence. One broken contribution does not
  hide healthy unrelated providers or become an empty-success result.
- [ ] Discovery and detail tests assert zero credential-source calls and zero
  network connections; unbound contributions remain visible and non-runnable.
- [ ] URL tests cover accepted secure schemes, invalid/ambiguous endpoints,
  matching policy, origin/path identity and refusal before side effects.
- [ ] Auth tests inspect actual constructed header bytes for a synthetic token
  scheme and prove source approval, identity/origin/resource/scope/generation
  isolation, missing/ambiguous bindings and bounded 401 retry.
- [ ] A synthetic TLS peer exercises the normal catalog-to-create/send/resume
  path for configured, URL and plugin-contributed targets, with approval,
  denial, reconnect and no-prompt-replay checks; assert no local spawning or
  worktree/filesystem acquisition.
- [ ] Existing remote ACP, provider discovery, roster/routes/client-connect
  and shared credential-relay suites remain green using the canonical isolated
  runner; lint, install-contract and applicable shared-library vendoring gates
  pass without weakening thresholds or skipping encountered failures.
- [ ] Run a fresh-environment contribution/binding check when practical;
  record exact tier coverage and reasons for any omitted clean-room lane.
- [ ] Record live-venue validation availability explicitly. Any real endpoint,
  source grant, promotion or installed-runtime proof requires separate
  authorization; blocked acceptance is transferred, not claimed successful.
- [ ] Public issues, docs, fixtures, commits and PR descriptions contain only
  generic examples and approved public attribution.

## Proposal

**Agent-recommended design:**

1. Extend existing bridge configuration composition, not the transport or
   service lifecycle. Enabled plugins contribute inert target requirements and
   provider manifests from attributable payload-owned roots. Canonical
   `.copilot-extensions/agent-bridge` and compatible `.agent-bridge` paths keep
   their documented authority. Do not create a second independent merge order.
2. Separate contribution identity from adopter binding. A contribution may
   name required endpoint/profile and credential-source slots without supplying
   secrets. Operator/project settings bind them explicitly. Plugin contributions
   cannot approve a source, execute an auth command at discovery, supply
   arbitrary headers or turn ambient credentials into authority.
3. Feed one normalized remote target representation into ordinary list/detail
   and session resolution. An explicit URL must match approved configuration;
   a URL is an address, not permission. Keep named-target compatibility and
   durable remote identity. Resolve endpoint identity consistently, including
   path distinctions; reject ambiguous policy rather than guessing.
4. Reuse the relay's registered source contract and HTTP injector. A constrained
   validated token scheme is data, not an arbitrary header/template language.
   Retain exact credential context checks and existing refresh/retry ownership.
5. Keep target state honest: discoverable, bound, connectable and running are
   different facts. Missing/invalid activation, declaration or binding has a
   diagnostic that refuses the affected target before consequential work.

Schema details must reconcile with current configuration and provider patterns
during Phase 2; any material expansion returns through proposal review first.

## Journal

### 2026-10-18 - Proposal and public coordination

- Claimed #7218 as drowsy-piston after checking adjacent public work.
- Source evidence identified reusable remote connector/session, plugin
  activation/provider discovery and credential-relay seams. No implementation
  or runtime changes have started.
- Submitted intent is generic and consumer-neutral. The contribution branch
  remains `dev`; release promotion, installed rollout and live grants are not
  authorized by this effort.
