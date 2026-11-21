# Windows Machine-Protected Cache

## Goal

Provide an additive, explicitly enabled LocalMachine-DPAPI credential-cache
copy that remains usable by its owning account in noninteractive Windows
logons when user-bound DPAPI is unavailable.

- **Slug:** `windows-machine-cache`
- **Repo:** copilot-extensions
- **Created:** 2026-11-16
- **Status:** Done
- **Vision:** [independent cache](../../../../visions/vault/README.md#independent-cache-and-standard-auth-providers)
- **Umbrella issue:** [#13009](https://github.com/ThomasMichon/copilot-extensions/issues/13009)

## Guiding Intent

Close the independent-cache availability gap without requiring a live unlocked
service, changing the existing user-protected cache, or concealing the weaker
machine-scope threat model. This is not a hardware-backed key solution.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Cache driver | Plan, source, tests and reviewed delivery | Owning source worktree |

## Coordination

Independent proposal and implementation PRs. The cache driver owns this bounded
slice; the broader Vault service, provider and installation owners remain
unchanged. Deployment and credential migration require adopter authority.

## Context

Windows NETWORK logons can lack the interactive user's DPAPI master key.
[#12216](https://github.com/ThomasMichon/copilot-extensions/issues/12216)
addresses selected-service fallback, not independent disk-cache availability.
The portable cache owner is `plugins/agent-vault`; downstream compatibility
front doors should not add another cache implementation.

## Request

Public-safe capture of the requested behavior:

1. Implement a redundant LocalMachine-DPAPI copy alongside the existing
   user-protected cache. Accept administrator/SYSTEM decryptability explicitly.
2. Populate it through unlock warming and explicit cache population. Prefer
   the user cache on reads, then the machine copy when user decryption fails.
3. Give every new machine-cache directory, ciphertext and temporary file its
   owner-and-SYSTEM-only DACL at creation. Keep credentials out of argv and env.
4. Add tests and qualify synthetic plaintext under a noninteractive Windows
   logon. Deliver through reviewed PRs; TPM-backed CNG is optional future work.

## Scope Approvals

This public plan authorizes no live credential mutation, service restart,
unlock, elevation or deployment. Adopters retain those effect decisions.
Source qualification uses synthetic plaintext in an owned temporary directory.

## Proposal

Machine scope lets any local user who obtains the ciphertext decrypt it.
The protected owner/SYSTEM DACL supplies the other-user boundary; administrators
can take ownership, and same-user code remains trusted. Optional entropy stored
with the application is not another secret boundary. Reboot/relock survival
must be distinguished from actual observed native qualification.

_(Agent-recommended implementation details:)_

- Explicit persistent opt-in in the selected cache store, default off; no
  identity-bearing environment variable and no automatic legacy conversion.
- Reuse the existing Windows security helper for restrictive creation and
  security validation, including private temporary files and atomic publication.
- Bind the encrypted redundant store to the exact primary ciphertext digest.
  Publish the redundant candidate before the primary atomic replacement:
  interrupted writes or older writers then cause a mismatch/refusal, never
  resurrection of an older credential. Missing/unreadable primary bytes,
  unsafe ACLs, schema or selection errors do not authorize fallback.
- All successful cache mutations, including rotation journals, invalidation and
  service-cache records, share the redundant writer. Decryption-unavailable
  fallback is read-only and never rewrites the primary.
- Provide explicit additive preparation and a non-secret verification command
  that exercises machine-copy reads without renaming/deleting the primary,
  fetching new credentials or requesting a ceremony.

### Alternatives

| Primitive | Assessment |
|-----------|------------|
| LocalMachine DPAPI + DACL | Chosen fast path; machine-bound, not user-bound cryptography; administrator/SYSTEM access accepted |
| TPM-backed CNG | Optional future hardening; non-exportability and NETWORK-token access need native qualification; TPM reset requires recovery |
| Software CNG | Export policy and DACL help, but independence from unavailable user protection is unproven |
| SSH identity/agent | Signing is not generic decryption/KEK derivation; would add an availability and protocol dependency |
| Daemon-sealed KEK | Memory survives relock but not process exit/reboot; still needs an independent unattended protector |
| Credential Manager | Not a dependable NETWORK-logon replacement; native credential sets and underlying protection remain logon-dependent |

Primary references: [DPAPI scope](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata),
[creation security](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew),
[NETWORK credential sets](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credreadw).

## Plan

### Phase 1 - Review the bounded proposal
- [x] Merge this proposal before implementing the portable behavior.

### Phase 2 - Implement and qualify
- [x] Add explicit opt-in, secure redundant writes, user-first fallback and safe verification.
- [x] Preserve selection/recovery, rotation, invalidation and old-reader behavior.
- [x] Land source, documentation and changefile through normal review and CI.

### Phase 3 - Release and acceptance
- [x] Confirm the implementation reaches the normal release branch.
- [x] Record native noninteractive synthetic proof and remaining adopter-owned rollout gates.

## Validation Plan

- [x] Focused tests cover default-off, both primary formats, user-first ordering,
  decryption-unavailable fallback, stale/corrupt/mismatched copies and ACL refusal.
- [x] Mutation tests cover interrupted publication, older writers, pending
  replacement, invalidation and cache clearing without stale fallback.
- [x] Native Windows proof covers machine-scope roundtrip, creation-time DACL,
  atomic replacement and the actual noninteractive token/session context.
- [x] Existing targeted cache/credential-read tests, lint and install contract pass.
- [x] Public artifacts remain generic; merged, released and deployed remain distinct.

## Journal

### 2026-11-16 - Proposal

Claimed #13009 and resolved the portable cache owner. This proposal is pending
review; no implementation or live cache change is claimed.

### 2026-11-16 - Reviewed source gate

Proposal #13011 merged after approval and required checks. Implementation is
the next slice. An isolated native Windows process qualified two machine-DPAPI
roundtrips in session 0 with NETWORK-token membership and owner/SYSTEM-only
creation/replacement DACLs. This does not qualify reboot, another user's token
or an installed consumer.

### 2026-11-16 - Source qualification

Implemented the additive copy and explicit preparation/verification boundary.
The final bounded packet passed 289 tests with one file per subprocess,
including real Windows DPAPI/DACL controls, both primary formats and rotation,
invalidation, stale-source, ACL, schema and interrupted-publication cases.
Lint and the install contract passed.

A combined-process run exposed a cache-verify extraction bug: capturing the
cache factory at import time lost the caller's selected factory. Restored
operation-time lookup and verified the existing service/alias tests.
Some combined-process native first writes also returned false intermittently;
isolated native controls and the final module-isolated packet passed. The
underlying native first-write discrepancy is not claimed resolved by that
observation. Source delivery, release and adopter qualification remain open.

### 2026-11-16 - Degraded-read and migration corrections

A machine-copy miss must retain the user's decryption-unavailable state so
ordinary selected-service reads skip impossible writeback, while cache-only and
authoritative refresh preserve their refusals/reconciliation requirements.
An absent redundant file has that same degraded state only after source, private
directory and recovery checks pass. Other protection errors still refuse.
Legacy plaintext-key migration refreshes the copy from the store reread under
the migration lock, keeping its new wrapped-key digest current.

The expanded bounded cache packet passed 303 tests, including 14 focused
regressions for these cases. The owned PR's mechanical rebase also preserved
the concurrent attachment-import capability and its admitted source inventory;
77 affected actor-free units and 259 population/recovery/service cases passed.

### 2026-11-16 - Bound ordinary opted-in reads

Independent re-review confirmed the previous three corrections and identified
unbounded primary/key reads before the redundant reader's size check. Opted-in
stores now use the same bounded input reader before decryption and during key
migration; default-off input handling is unchanged. The bounded packet passed
308 tests, with one inapplicable wrapped-key case skipped for the native format.
Whole-file strict typing passed both platform contracts and focused lint passed.

### 2026-11-16 - Preserve concurrent maintenance ownership

The source received a clean independent approval, then required a rebase onto
the selected-service maintenance change in #13008. The extracted verifier
retains that configured service route. Machine preparation and forced machine
verification reject service-owned maintenance rather than reading or mutating
an unrelated local store. Both capabilities and the upstream admitted unit
inventory remain intact; only changed source witnesses are refreshed.

The rebased packet passed 331 tests with one inapplicable native-format case
skipped, plus 87 admitted transport/target/attachment units and 62 recovery/target
cases. Nine-file strict typing passed both platform contracts. A concurrent
test-environment preparation hit a Windows executable-sharing refusal; after
the owned interpreter user completed, the bounded sequential retry passed.
This does not change the separately disclosed native first-write observation.

### 2026-11-16 - Source merged; release pending

Implementation #13022 merged to `dev` as `809add5d69` after independent
current-head approval and required checks. The final source packet passed
331 cache cases with one inapplicable native-format case skipped, 87 admitted
units and 62 recovery/target cases; strict typing passed both platform contracts.
Normal release-branch promotion remains open. No installation, live credential
copy, service restart or adopter acceptance is claimed by this source merge.

### 2026-11-17 - Native release-gate prerequisite

Promotion passed source qualification but remains blocked. After the tracked
artifact-publication rate limit in #13052, the next run exposed the native
Resume failures tracked by #12850, #10945 and #10946. The selected-runtime
observer now uses a finite `--observe` invocation with JSON stdin; the native
fixture owner still admitted only its older description invocation.

The bounded fixture correction preserves the real observer, namespace,
duplicate-creation fence and deadlines. Five native owner/transport/dependency
controls pass, including actual observation-protocol stdin forwarding; strict
typing passes both platform contracts. The full local packet also encountered
the production census refusal for unrelated live servers outside its private
root. Those three cold/reattach cases are not claimed qualified locally, and
the fresh-runner native promotion gate remains required. No live sessions were
changed to manufacture a passing result.

The overlapping fixture correction landed independently in #13059 while the
follow-up was awaiting its required checks. Rebase preserves that complete
implementation and its typed native fixture. The remaining follow-up adds a
separate real-executable refusal control that does not require the global
absence census, plus these source/release records; it does not reapply or
replace the already merged correction. Fresh native release qualification
remains open.

The next fresh producing run, 44052200417, passed observation forwarding and
then reached the unchanged 3.4 creation-version gate. Its generated private
adapter did not carry its selected backend's file-version resource. The bounded
follow-up now projects that actual complete resource and refuses unreadable
metadata, rather than advertising a guessed minimum. Four native controls pass,
including real compiled below/above-floor versions with nonzero revisions,
the original production guard and structured observation refusal. Full fresh
native Resume qualification and release remain required.

The metadata implementation also landed independently in #13070 during the
follow-up's review window. Rebase preserves it completely, including its
unversioned-adapter compatibility for unreadable backends and unchanged
production refusal. The remaining follow-up supplies real compiled
version-resource/creation-floor and observer-refusal controls, plus source
completion records; neither independently merged repair is replaced.

### 2026-11-20 - Release confirmed; bounded source effort complete

The normal Main release
`e3b27e01febf4f7edbf58425011e2379f1c48d83` publishes `agent-vault`
`0.1.313-dev1`, including the machine-copy implementation, packaged readiness
declaration and `windows-machine-cache-v1` invocation capability. Release
confirmation resolves the remaining source-plan checkbox; it is not inferred
from ancestry of the original squash-merged implementation.

The reviewed implementation and native source qualification remain the basis
for this bounded source completion. Deployment, real credential custody,
service transitions and any migration remain adopter-owned, with their own
authority and acceptance records. The disclosed intermittent CurrentUser
first-write observation is not reclassified as fixed, and no reboot,
other-user execution or universal deployment claim is added by this archive.
