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

- **Configured-vault continuation:** The attribution owner retains this effort;
  Worktrees owns setup/status/verification and Vault exclusively owns key,
  database, cache and unlock operations. Existing adopter/transport owners retain
  their resources and original approvals. Later two-target cache/envelope
  progress does not transfer custody or authorize additional operations; both
  unknown diagnostics retain their original identities. Local standardized setup
  remains independent of remote Gateway availability. No new transport, key,
  credential, installation or rollout action follows from this intake.

## Context

The root codename layer landed in #5458 and encrypted identity in #5578.
Originating control identity subsequently landed in #6080, native Windows
local cache protection in #6108, native portable token encryption in #6115,
and common vault-key sourcing in #6127. Provider-neutral preparation landed
in #6281 and repository enforcement in #6356.

The vault's opt-in persistent cache protects a local copy of the common owner
key. Windows protection is machine-bound; the identity key and AWI1 tokens
remain portable. Explicit initial population is distinct from routine
locked-vault reads. A dev merge alone does not prove main deployment,
required-rule activation, or synchronized fleet adoption.

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

Provider fallback clarification:

> "Emit a marker / prepare a PR body (recommended)"

The operator selected preparation for existing provider tools, not independent
remote PR mutation. Adopter labels are optional public invocation configuration.

### Phase 1 — Reviewed completion contract
- [x] Review and land this proposal before implementing new behavior (#6072).

### Phase 2 — Originating identity
- [x] Include the root/control worktree's machine, project and worktree ID
  inside the encrypted payload, retaining the immediate producer identity.
- [x] Preserve codename/root fields, attribution opt-outs and existing token
  decoding. Unresolved or unsafe ancestry must not invent an origin.
- [x] _(agent-recommended)_ Reuse validated ownership-chain traversal and
  define capture behavior across handoffs, reaped ancestors and changed chains.

### Phase 3 — Portable key and protected local cache
- [ ] Use one owner-level key shared through a synced vault entry on all
  machines; never allocate a PR identity key per machine. Retain file-key
  compatibility only as a reference to that same shared key, not independent
  machine key generation.
- [x] Do not use machine-bound KEKs as the shared identity key. Machine-bound
  protection wraps only the local cached copy of the common vault key.
- [x] Serve routine PR publication and lookup from a protected local cached key
  without unlocking the vault after successful initial population.
- [x] Missing or invalid cache must require explicit provisioning/unlock;
  never create a divergent replacement key or fall back to plaintext storage.
- [x] _(agent-recommended)_ Reuse the vault's cache contract where possible,
  with native Windows user-bound protection that works on ARM64 without a
  source-build prerequisite. Keep vault access at the declared tool boundary.
- [x] _(agent-recommended)_ Make shared-key PR encryption/decryption work on
  native Windows ARM64 independently of local cache wrapping. Cache protection
  and portable token encryption are different operations; neither substitutes
  for the other.
- [x] _(agent-recommended)_ Define bounded key-fetch failures, cache lifetime,
  rotation and old-token lookup before publishing the integration.

### Phase 4 — Provider fallback attribution
- [x] Add a configurable, provider-neutral fallback attribution path for PRs
  opened outside managed `create-pr`, including Azure DevOps.
- [x] Keep adopter-specific tags and repository names in adopter configuration,
  never in the public plugin's defaults.

### Phase 5 — Required dev PR source metadata

- [x] Review and implement the repository-specific required metadata gate,
  automation origins, manual repair guidance and silent operator-only
  exception in [dev-pr-enforcement.md](dev-pr-enforcement.md) (#6295).

### Phase 6 — Standardized configured-vault setup

- [ ] Review and deliver the reusable local-agent setup/status/verification
  contract in [standardized-vault-setup.md](standardized-vault-setup.md).
  An existing configured agent-vault KeePass database is the starting point;
  private adopter scripts and remote transport are not required.

#### Configured-vault consumer and continuing adoption clarification

- [ ] **S6068-P1.** Reconcile the delivered standardized local setup source with
  remaining promoted-release and authorized installed-consumer acceptance rather
  than treating #6782 as missing implementation. Preserve the exact owning vault
  invocation, named database/group/installation cell and nonsecret persistent
  consumer configuration across fresh processes. Report conflicting overrides and
  configured-source failure without legacy-key fallback. Verify the actual normal
  cache-only key reader, locked-vault fresh-process consumption and portable
  encryption; a bypassing probe or populated cache alone is insufficient.
- [ ] **S6068-P2.** Retain first-owner initialization ONLY after separately
  confirmed authoritative absence on the designated synchronized owner database,
  using serialized create-only behavior and authoritative reread after ambiguous
  writes. Subsequent machines reuse the common owner key. Missing, locked,
  malformed, failed or unsynchronized state is not absence; never allocate a
  divergent machine key, overwrite existing key material, export the master
  password or expose secrets in argv, logs, receipts or plaintext files.
- [ ] **S6068-P3.** Preserve the two historically authorized protected-cache,
  common-key challenge and persisted-envelope partials without claiming persistent
  normal-consumer configuration or fresh-process readiness. Resolve that remaining
  evidence through existing custody. Reconcile both unknown diagnostic submissions
  under their original identities before any separately authorized retry; do not
  substitute new transport, credential or secret operations. Original approvals
  retain their bounds, not a fresh grant inferred from these records.

##### Configured-vault consumer local validation

- [ ] **S6068-V1.** Distinguish configured local setup, existing-entry cache
  population, exact vault/database/group/cell persistence and override visibility,
  actual fresh-process locked-vault cache-only reading/portable encryption,
  promoted release and authorized installed readiness. Preserve explicit
  authoritative-absence initialization, serialized create-only/reuse-only behavior,
  no-overwrite/UNKNOWN controls, legacy-token compatibility and secret-free errors;
  record unavailable clean-room/native tiers honestly.
- [ ] **S6068-V2.** Retain the independent shared-key multi-installation proof and
  continuing adoption obligation. Existing remote cache/envelope results are not
  persistent normal-consumer configuration proof. Verify retained evidence and
  original unknown diagnostic custody before any separately authorized retry;
  neither local fixture success nor a transport failure changes adoption authority.

## Validation Plan

- [x] Root identity round trips from a multi-repo ownership chain while public
  marker fields contain no raw origin identifiers.
- [x] Cover opt-outs, malformed/cyclic ancestry, remote or missing ancestors,
  handoffs and record recreation; document deliberate limitations.
- [x] Existing encrypted tokens remain readable; wrong key or tampered data
  produces an explicit error without publishing plaintext or secret diagnostics.
- [ ] Prove a synced portable key from a disposable vault/database fixture can
  decode tokens from multiple source machines on another installation identity
  without probing each source machine for a key.
- [x] Prove lookup with vault locked after cache population, including a
  process restart; cache miss does not prompt invisibly or fabricate success.
- [x] Native Windows ARM64 exercises real encryption/decryption and protected
  cache reads, not only mocks or platform skips.
- [x] Concurrent key/cache writes, malformed cache, rotation and permissions
  preserve recoverability and do not overwrite authoritative key material.
- [x] Exercise initial, refreshed and foreign-PR publish paths plus provider
  fallback behavior with disposable fixtures.
- [ ] Run focused unit, real subprocess and applicable clean-room checks.
  Live synced-fleet checks use explicit authorization and disposable material;
  record any unavailable tier and its reason.
- [ ] Land every implementation PR, reconcile this plan and distinguish
  merged-to-`dev`, promoted-to-`main` and locally installed states.

- [ ] **S6068-G.** Resolve the configured local setup, exact vault/database/group/
  cell and override, normal fresh-process locked-vault cache-only/portable-token,
  explicit first-owner/create-only/reuse-only and secret-free acceptance above AND
  the independent shared-key multi-installation/adoption obligations. Credit
  delivered source and historical two-target cache/envelope partials separately
  from promoted release and authorized installed/normal-consumer readiness. Both
  unknown diagnostics retain original custody and require reconciliation before a
  separately authorized retry; no new transport or credential operation is granted.

## Proposal

This proposal specifies behavior and validation
obligations, not a preselected encryption implementation or a deployment action.
No production secret is read or written by the planning slice.

## Journal

### 2026-11-13 - Configured consumer and adoption evidence retained

The additive S6068 Plan/local/global Validation retains the configured local
setup and continuing adoption contract without changing incumbent checks.
#6782's delivered standardized setup source remains credited; source,
containing release, authorized installed readiness and normal fresh-process
consumer proof are distinct. Earlier pending-review/source-progress statements
remain historical evidence, not grounds to recreate the delivered capability.

Qualified #6068 comment6175373765 records later authorization and bounded
protected-cache population on two remote targets, a common-key challenge and
persisted-envelope decoding. Those partials supersede earlier no-population/
no-authorization observations for that recorded operation, not the remaining
persistent consumer-configuration and fresh-process proof. The two later
diagnostic submissions remain UNKNOWN and must be reconciled by original
identity before a separately authorized retry.

The original approvals retain their resource, operation and safety bounds.
Neither the historical blanket wording nor this receipt supplies a new grant:
transport failure is not a standing adoption prohibition, and recorded success
does not authorize fresh credential, transport, cache or key effects. Parent
Active status, shared-key adoption and every remaining validation duty persist.

### 2026-10-10 — Reviewed plan and origin implementation

- Proposal #6072 cleared review and merged. Implementation starts with private
  local-root capture; shared vault-key custody/cache and provider fallback remain
  separate upcoming slices.
- Origin metadata is an optional nested payload field. Each publication captures
  the validated current ownership chain; unresolved ancestry or root opt-out
  omits it. Earlier tokens remain historical snapshots.

### 2026-10-10 — Local cache protection slice

- Origin identity landed in #6080; shared owner-key sourcing and portable token
  encryption remain separate upcoming work.
- Added a native Windows local-cache protection path for environments without
  cryptography. Legacy Fernet caches are retained rather than silently reset.
- Disposable native ARM64 cache protection/read validation passed without vault
  unlock. Existing Fernet and new DPAPI cache tests passed together.

### 2026-10-10 — Continuation

- Identified the remaining immediate-versus-originating identity gap after
  #5578 and captured the initial vault-fallback proposal plus unlock-free cache intent.
- The operator subsequently specified one common key shared through the vault,
  superseding the earlier fallback-only direction for this workflow. Existing
  portable file-key compatibility is retained without divergent machine keys.
- Kept provider fallback after the identity/key work. No immediate updater or
  manual instruction sync is part of merge completion.

### 2026-10-11 — Shared source and publication review follow-up

- Native local cache protection landed in #6108 and portable native Windows
  token encryption landed in #6115. Disposable native ARM64 exercises prove
  protected cache reads and common-key token round trips, not actual owner
  vault provisioning or synchronized fleet rollout.
- Shared-source integration uses explicit payload argv and the same common vault
  entry on every machine. A configured source miss never selects another key,
  unlocks implicitly, or allocates a divergent identity key. Initial population,
  explicit refresh after rotation and prior-key retention remain operator actions.
- Successful POSIX reads can leave no process group to reap; accept that normal
  absence silently while retaining sanitized warnings for actual cleanup failures.
  Real output-bound and descendant-timeout tests remain in the focused selection.
- Coupled stale-publication recovery only retries an ordinary fast-forward push
  after a stale expected-head lease rejection under the existing publication lock
  and destination identity checks. Divergent heads are not force-adopted.
- Actual synced-vault population, locked-cache lookup across the real fleet,
  provider fallback and final validation reconciliation remain incomplete.

### 2026-10-12 — Shared source landed; provider preparation slice

- Shared owner-key sourcing landed in #6127 after current-head approval and
  required CI. Publication deadlines include recovery attempts; POSIX cleanup
  retains the group leader's identity until termination. Unsupported older
  macOS observer primitives omit the optional layer with sanitized diagnostics.
- The common vault entry and one protected local cache were populated with
  explicit operator authorization. Fresh-process cache-only reads and native
  token round trips passed without publishing key material. Actual locked-vault
  and synchronized multi-machine deployment evidence remains separate.
- The operator chose a provider-neutral preparation command for existing
  GitHub/Azure DevOps tools, not a second authenticated remote-posting path.
  It reuses the existing codename/root/encrypted marker builders, strips stale
  source markers, honors provenance/opt-outs, and takes any public adopter
  label explicitly rather than embedding a private repository default.
- Merge to `dev`, promotion to `main`, and authorized local rollout remain
  distinct states; no immediate force-update or manual projection sync is
  part of this continuation.

### 2026-10-13 — Implementation and local owner-key proof

- #6281 merged after review. Preparation preserves authored prose and emits
  provider-neutral source metadata without creating a PR or requiring a
  particular hosting provider.
- One authoritative owner key was populated explicitly in the synced vault
  after verified absence. Routine publication and lookup then used its
  protected cache with the vault daemon locked, including a fresh process.
  No divergent machine key was generated and no existing key was replaced.
- Real native Windows ARM64 exercises proved protected-cache reads and AWI1
  round trips for two distinct fixture source-machine identities using one
  shared key. The persisted description of #6356 was independently fetched
  and decoded in memory; its private immediate and originating identities
  matched the expected ownership chain. Neither key nor raw identifiers
  were emitted into public evidence.
- Focused identity/source, legacy-token/cache, native protection, subprocess
  timeout/output-bound, and provider-fallback selections passed. The latter
  covered 17 provider-neutral fallback contracts. Actual synchronized
  other-machine adoption remains unproved: disposable multi-identity fixtures
  and a successful local cache are not a fleet rollout.
- #6356 merged on 2026-10-13 after its current-head approving review and CI
  run [38388369063](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38388369063).
  Dev-push CI [38391631615](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38391631615)
  subsequently passed. Main promotion, live negative/body-only repair and
  shared-head departure evidence, no-bypass rule activation, and final
  validation reconciliation are still completion gates.
- Host installation is owned by the adopter's authorized maintenance lane.
  This effort did not force-update plugins or manually synchronize projections.
- An explicitly authorized read-only fleet inspection found no existing
  protected-cache files or owner-key source configuration on either reachable
  remote Windows target. Two other declared targets could not be reached
  through their configured tunnel profiles. No remote cache was populated,
  vault unlocked, software updated, or key created or replaced. The operator
  selected retaining fleet deployment as an open, blocked item in this effort,
  rather than provisioning or transferring it. Local success does not close
  that item.
- The first post-implementation promotion run
  [38395806291](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38395806291)
  failed in unrelated receiving-authority fixtures. A later run
  [38474138013](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38474138013)
  uncovered incomplete obligation-settlement fixtures, tracked in #6589-#6592
  and owned by #6593. No required release gate was bypassed; the trusted source
  validator is not yet present on main.

### 2026-10-14 — Trusted deployment and mandatory dev gate

- Normal promotion #6631 actually merged at
  `f5e4e30353a076aec74781e6ef5cfe5df474e939`. The validator, refresh-budget
  helper, public dependency lock and both trusted source workflows match the
  reviewed #6356 blobs at that exact main commit.
- Covering run
  [38508930963](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38508930963)
  subsequently failed in post-merge Python artifact publication. This does
  not erase the verified source/workflow deployment, but neither the run nor
  this effort claims successful artifact publication or installed fleet rollout.
- The live missing-marker, same-head body-only repair, shared-head refusal and
  untouched remaining-PR departure recovery all passed their stated contracts;
  exact trusted-run evidence is recorded in the enforcement sidecar.
- Existing active no-bypass ruleset `24152679` now additionally requires
  `source-attribution`. Fresh readback and effective dev branch rules confirm
  the requirement; every existing rule, check, condition, enforcement and
  bypass field was preserved.
- The whole effort remains Active. The operator explicitly retained actual
  synced-fleet provisioning/adoption as blocked here, without authorizing
  remote unlocks, cache population or replacement keys. Local and fixture
  evidence does not resolve that remaining Plan/Validation gate.
