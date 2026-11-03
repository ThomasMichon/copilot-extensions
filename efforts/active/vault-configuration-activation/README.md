# Vault Configuration Activation

- **Slug:** `vault-configuration-activation`
- **Repo:** copilot-extensions
- **Branch(es):** independent managed plan and implementation PRs
- **Created:** 2026-11-02
- **Status:** Draft
- **Vision:** [Vault explicit adoption and standalone use](../../../visions/vault/README.md#central-fleet-and-standalone-local)
- **Umbrella issue:** #2350 (bounded Vault adopter only)

## Guiding Intent

Loading Agent Vault exposes a capability; it must not by itself require a
runtime, service or KeePassXC installation. Explicit configuration selects
automatic runtime participation without moving database or credential custody.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Vault source driver | Plan, source, validation and PR stewardship | Managed source worktree, public issue #2350 |

## Coordination

- **Topology:** sequential plan and implementation PRs.
- **Host (owns PRs):** Vault source driver.
- **Delegates:** none.
- **Handoff:** canonical effort and issue retain remaining acceptance.
- #6928 and #8826 retain installer-engine/watchdog ownership. This slice does
  not replace either change or implement the family-wide activation convention.

## Context

Vault already composes portable repository and bound-knowledge preferences
through the shared configuration-layer owner, but bootstrap and readiness do
not consult an activation declaration before requiring its runtime/backend.
This closes an explicit-adoption gap in the existing Vault vision; it does not
extend fleet, authentication or service-lifecycle authority.

## Request

Public-safe capture: keep Agent Vault enabled as an available plugin, with
runtime participation explicitly selected through repository or qualified
bound-knowledge configuration. Do not make KeePassXC a general plugin-loading
requirement. Preserve independent standalone use and existing credentials.

## Scope Approvals

Source changes, disposable validation and normal issue/PR publication are in
scope. Installed-runtime rollout, live service changes, unlock, credential
migration and new daemon deployment are not. A reviewed plan grants none of
those effects.

## Plan

### Phase 1 - Reviewed contract
- [ ] Review and merge this bounded proposal before implementation.
- [ ] Specify activation in Vault's existing configuration namespace, using
  existing qualified configuration owners and portable layers.

### Phase 2 - Implementation and documentation
- [ ] Gate automatic bootstrap, first-use/update participation and readiness
  before runtime/backend work; an unconfigured payload remains inert.
- [ ] Distinguish missing configuration from malformed, unsafe or refused
  configuration. Preserve explicit configured use, standalone operation,
  installation-cell boundaries and existing service/data custody.
- [ ] Document precedence, opt-in and withdrawal semantics; withdrawal does not
  silently stop a service used by another admitted scope.
- [ ] Add a Vault changefile and land source through ordinary review and CI.

## Validation Plan

- [ ] Focused resolver/consumer tests: absent, enabled, disabled, malformed,
  qualified knowledge configuration and unavailable/refused owner.
- [ ] Disposable native first-touch checks execute the actual payload startup
  and readiness entries with no Vault runtime or KeePassXC; no provisioning,
  service or backend action occurs when inactive.
- [ ] Configured controls reach the existing owned consumer path without
  bypassing its checks; existing Vault tests and touched-Python lint pass.
- [ ] Install-contract checks report zero violations.
- [ ] Exercise an available isolated external CI lane for platform parity, or
  record the exact unavailable boundary without claiming live qualification.
- [ ] Record merged source and release status separately from unapproved rollout,
  resolve every checklist item and archive this source effort.

## Proposal

Implementation details are agent-proposed within the captured scope. Reuse
`configuration_context` and `configuration_layers`; do not add a binding parser,
mandatory Worktrees dependency, parallel service owner or global diagnostic
environment-variable policy. Existing native database/service declarations
remain authoritative for their machine-local custody.

## Journal

### 2026-11-02 - Proposal
- Claimed the bounded Vault slice on #2350 after checking open installer PR
  overlap. No source implementation or installed-state mutation has occurred.
