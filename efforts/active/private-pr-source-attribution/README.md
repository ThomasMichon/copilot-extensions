# Private PR Source Attribution

- **Slug:** `private-pr-source-attribution`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice PRs targeting `dev`
- **Created:** 2026-10-10
- **Status:** Active
- **Vision:** below-altitude completion of existing encrypted attribution and credential-cache contracts
- **Umbrella issue:** #6068

## Guiding Intent

Let a key holder identify the originating control worktree and machine from
a PR without exposing those identifiers to public readers or requiring a
vault unlock for every lookup. Public codenames remain independent handles.
All of an owner's machines use the same portable key shared through the vault;
only protection of each machine's local copy is machine-bound.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| attribution owner | planning, sequential implementation and validation | this effort's managed worktree |

## Coordination

- **Topology:** independent per-slice PRs
- **Host (owns PRs):** attribution owner
- **Delegates:** none
- **Handoff:** effort pointer, current slice and outstanding validation

## Context

The root codename layer landed in #5458 and the encrypted identity layer in
#5578. Encryption currently records the immediate product worktree, not the
originating control worktree. Portable file-key custody exists but requires
manual provisioning. Native Windows ARM64 omits encryption when its optional
platform dependency is unavailable.

The vault has a synced-database entry surface and a separate opt-in persistent
cache. Its Windows KEKs are machine-bound: they must not become the portable
PR identity key. Existing cache encryption also depends on a package that is
not normally available on native Windows ARM64.

## Request

Public-safe capture of the operator's continuation:

> "getting the source ([control]) worktree info and machine info into PRs in a
> way only the owner can know."

The bracketed term replaces the downstream repository's descriptive name;
the identity and privacy requirements are unchanged.

> "As a worst-case, let's store the key using agent-vault so that it syncs via
> OneDrive to most user's machines"

> "The ARM64 encryption should only be used for the local vault cache, so that
> the user isn't forced to unlock vault just to do the lookup from PRs, ideally"

> "Great. Continue driving"

> "Note that all machines should use the same key, shared via vault. Don't
> have a key per machine. That would make it hard for the user on one machine
> to figure out which of their other machines sources a PR"

Settled design constraints from earlier clarification: keep codenames;
portable user-held key custody, not a GitHub Secret or mandatory heavyweight
vault workflow; include full identity inside ciphertext. The final clarification
selects one vault-shared owner key for this workflow. Existing file-key adopters
remain compatible, but this workflow must not create independent machine keys.

## Plan

### Phase 1 — Reviewed completion contract
- [x] Review and land this proposal before implementing new behavior (#6072).

### Phase 2 — Originating identity
- [ ] Include the root/control worktree's machine, project and worktree ID
  inside the encrypted payload, retaining the immediate producer identity.
- [ ] Preserve codename/root fields, attribution opt-outs and existing token
  decoding. Unresolved or unsafe ancestry must not invent an origin.
- [ ] _(agent-recommended)_ Reuse validated ownership-chain traversal and
  define capture behavior across handoffs, reaped ancestors and changed chains.

### Phase 3 — Portable key and protected local cache
- [ ] Use one owner-level key shared through a synced vault entry on all
  machines; never allocate a PR identity key per machine. Retain file-key
  compatibility only as a reference to that same shared key, not independent
  machine key generation.
- [ ] Do not use machine-bound KEKs as the shared identity key. Machine-bound
  protection wraps only the local cached copy of the common vault key.
- [ ] Serve routine PR publication and lookup from a protected local cached key
  without unlocking the vault after successful initial population.
- [ ] Missing or invalid cache must require explicit provisioning/unlock;
  never create a divergent replacement key or fall back to plaintext storage.
- [ ] _(agent-recommended)_ Reuse the vault's cache contract where possible,
  with native Windows user-bound protection that works on ARM64 without a
  source-build prerequisite. Keep vault access at the declared tool boundary.
- [ ] _(agent-recommended)_ Make shared-key PR encryption/decryption work on
  native Windows ARM64 independently of local cache wrapping. Cache protection
  and portable token encryption are different operations; neither substitutes
  for the other.
- [ ] _(agent-recommended)_ Define bounded key-fetch failures, cache lifetime,
  rotation and old-token lookup before publishing the integration.

### Phase 4 — Provider fallback attribution
- [ ] Add a configurable, provider-neutral fallback attribution path for PRs
  opened outside managed `create-pr`, including Azure DevOps.
- [ ] Keep adopter-specific tags and repository names in adopter configuration,
  never in the public plugin's defaults.

## Validation Plan

- [ ] Root identity round trips from a multi-repo ownership chain while public
  marker fields contain no raw origin identifiers.
- [ ] Cover opt-outs, malformed/cyclic ancestry, remote or missing ancestors,
  handoffs and record recreation; document deliberate limitations.
- [ ] Existing encrypted tokens remain readable; wrong key or tampered data
  produces an explicit error without publishing plaintext or secret diagnostics.
- [ ] Prove a synced portable key from a disposable vault/database fixture can
  decode tokens from multiple source machines on another installation identity
  without probing each source machine for a key.
- [ ] Prove lookup with vault locked after cache population, including a
  process restart; cache miss does not prompt invisibly or fabricate success.
- [ ] Native Windows ARM64 exercises real encryption/decryption and protected
  cache reads, not only mocks or platform skips.
- [ ] Concurrent key/cache writes, malformed cache, rotation and permissions
  preserve recoverability and do not overwrite authoritative key material.
- [ ] Exercise initial, refreshed and foreign-PR publish paths plus provider
  fallback behavior with disposable fixtures.
- [ ] Run focused unit, real subprocess and applicable clean-room checks.
  Live synced-fleet checks use explicit authorization and disposable material;
  record any unavailable tier and its reason.
- [ ] Land every implementation PR, reconcile this plan and distinguish
  merged-to-`dev`, promoted-to-`main` and locally installed states.

## Proposal

This proposal specifies behavior and validation
obligations, not a preselected encryption implementation or a deployment action.
No production secret is read or written by the planning slice.

## Journal

### 2026-10-10 — Reviewed plan and origin implementation

- Proposal #6072 cleared review and merged. Implementation starts with private
  local-root capture; shared vault-key custody/cache and provider fallback remain
  separate upcoming slices.
- Origin metadata is an optional nested payload field. Each publication captures
  the validated current ownership chain; unresolved ancestry or root opt-out
  omits it. Earlier tokens remain historical snapshots.

### 2026-10-10 — Continuation

- Identified the remaining immediate-versus-originating identity gap after
  #5578 and captured the initial vault-fallback proposal plus unlock-free cache intent.
- The operator subsequently specified one common key shared through the vault,
  superseding the earlier fallback-only direction for this workflow. Existing
  portable file-key compatibility is retained without divergent machine keys.
- Kept provider fallback after the identity/key work. No immediate updater or
  manual instruction sync is part of merge completion.
