# Original-attempt Komodo SDK repair

Back to the [fixed substrate owner](komodo-owner.md).

The opt-in installed contract is
`fleet-manager.komodo-original-repair.v1`, exposed as
`komodo_control.REPAIR_CONTRACT`, `repair_bootstrap` and `repair_status`.
It verifies and settles one original, whole-process-settled UNKNOWN candidate
through a distinct current released SDK executor. It never invokes convergence,
apply, stop, remove, enrollment, a historical runtime, or a new operation.
The baseline `fleet-manager.komodo-substrate-owner.v1` contract and Gateway
routes are unchanged.

## Existing authority and distinct subjects

An embedding code-registers `KomodoRepairOwner` with its existing admission
authority and current repair resolver. The resolver receives the actual
verified JWT, enrolled-PoP or machine caller, canonical original intent, caller
role and installation. It must authenticate and authorize the current
repair-only transition through the existing configuration/release/runtime/
host/storage/credential/public-identity owners. A request cannot select a
resolver, module, callback or serialized grant.

Both authenticated host-runtime paths form part of the stable transition
binding. Moving both subjects to another root does not authorize continued
publication through the original root's already-open lock and journal.

`KomodoRepairSelection` is the resolver's checked result, not authorization:

| Subject or field | Required binding |
|---|---|
| `candidate` | Authenticated retained original selection; exact request deployment/generation and original release |
| `executor` | Separately authenticated current released executor; distinct release, same declaration/profile, fleet/installation, physical daemon/project, native tooling, material paths/inodes, credentials, compatibility and adoption |
| `request_digest` | Complete original `OperationIntent` digest, never a replacement request |
| `executor_runtime` | Actual selected executing immutable runtime's authenticated digest, not an asserted package version |
| `transition_reference`, `transition_revision`, `valid_until` | Explicit current repair-only appointment for this caller, request, candidate, executor/runtime and physical resource |

The embedding authenticates these subjects before returning the result;
constructing records, matching hashes or listing compatible deployments does
not do that. Finite candidate/executor/transition validity and current
exact-original operation admission are rechecked through the existing
`AdmissionAuthority` bound checkpoint. Changing scope or withdrawing either
the transition or ordinary request grant refuses the next publication.
Renewal cannot extend an existing permit's lifetime or substitute another
executor. Retained transition/admission revision rollback refuses too.

## Fresh checkpoint authorization

`komodo_control.CHECKPOINT_REPAIR_CONTRACT` declares
`fleet-manager.komodo-original-repair-checkpoint-auth.v1`. An embedding that
requires this behavior discovers that constant from the qualified, actually
executing SDK before constructing its repair owner. It code-registers the
optional `fresh_identity` producer on that same `KomodoRepairOwner`:

```python
fresh_identity(original_identity, original_intent, role, installation, action)
```

The producer authenticates a fresh proof with the existing signer/verifier for
the complete canonical original body and exact `status`, `succeeded` or `failed`
action. It returns only the verified caller identity, not a permit or authority
record. The current verifier/key-binding, configuration, source, runtime,
credential and material owners still supply the resolver and admission checks.
Requests and configuration cannot appoint this callable or select a verifier,
signer or module. Proofs and credentials never enter outcomes or diagnostics.

The SDK retains the initial identity profile/type, issuer, subject, audience,
proof key, enrolled kind and machine resource/key binding. Each fresh permit
must also retain the original authority, fleet, actor/account, role,
installation, enrollment generation and exact operation grant. A current
signature does not authorize substituting any of these bindings or the
candidate/executor/resource transition. Policy and transition revisions may
advance monotonically without extending their selected finite validity.

Initialization, native inspection, readiness, evidence construction, immutable
retention, state save, journal publication and cleanup use explicit admitted
phases under the same physical lock, journal and original selected deadline.
Each phase has one fresh decision and full custody/currentness checks before
work and after all work. The existing common owner issues a distinct permit
with its unchanged 30-second ceiling. Nested baseline checkpoint sites do not
repeat the phase owner's paired checks or mint another proof. A completed fresh
phase uses two resolver calls during authorization and two at each paired
boundary; initialization uses four in total. The final phase always closes
with full custody validation before consuming its exact permit through the
common completion boundary, which performs two further resolver calls.
Completing authority never suppresses the phase's closing custody checks.

Only after the preceding phase's final validation succeeds may the SDK discard
that exact local handle and reacquire. Expiry between completed phases is
allowed; no effect occurs in the permit gap. Expiry or withdrawal during a
phase aborts the invocation without an implicit retry or cached-identity
fallback. Fresh authorization cannot excuse a failed final boundary, refund
consumed proof evidence or reset the native deadline. Journal callbacks finish
the current phase before freshly admitting append and commit work inside the
existing transaction. Post-commit cleanup preserves the single immutable
terminal decision and follows its existing supported recovery.

Without the producer, the original whole-operation permit and expiry refusals
are unchanged. Opt-in status cannot convert an aborted custody/authority phase
into a drift report under another permit: it raises the original refusal,
preserving UNKNOWN or the already-recorded terminal receipt. Missing or
unsupported installed checkpoint support refuses this new requirement before
effects; provider rollback must retain the required support or refuse, never
substitute baseline convergence or silently disable the producer.

## Original physical custody and publication

Repair opens only the existing original physical lock and schema-4 NodeJournal.
It neither creates a missing owner root/lock/journal nor migrates an older
journal. It requires the exact held original UNKNOWN sequence-2 receipt,
matching original attempt, settled whole-process evidence, managed fixed
profile and original immutable profile bytes. Missing/corrupt history, another
resource/request/generation, changed material or unproved native custody
refuses without replay or uncertainty clearing.

Inspection uses the original pinned images/profile, retained keys and physical
network. Current authenticated health uses the
[identity and native-observation rules](komodo-owner.md#native-profile-adoption-and-custody).
Material, keys, journal inode/receipt, saved original selection, process/start
identity and network are checked across readiness and publication. A successful
terminal receipt requires complete current readiness. Explicit failed settlement
requires attributable selected/retained native results and unavailable readiness;
it retains the failed data candidate and never rolls back.

The final paired container/process observation, network fingerprint and complete
endpoint membership/attachments remain in memory only. Publication checkpoints reread and compare them across
immutable retention, state publication and the journal transaction before append
and commit, within the original deadline. Current authority is revalidated
after those reads. Endpoint observations also span authenticated health in repair
and healthy status, rather than taking the first snapshot only after health.
The shared verifier requires every running container in the bridge membership
before readiness; two observations with the same missing member cannot prove
successful repair. The existing pending-created and stopped-container custody
exceptions remain separate from running readiness.
Running attachment identities must agree between container and bridge
observations, not just across two bridge observations. Immutable outcome and
history reads precede the checkpoint's final native/network observation;
original key, profile, state, journal and material custody are checked again
afterward. Current admission and transition revalidation is the final boundary,
with no subsequent evidence reads before the checkpoint returns. A reconnect,
withdrawal or expiry during immutable-evidence work must refuse inside the
original journal transaction, not after committing a terminal receipt.
A key change during a native read likewise refuses before commit.
Every checkpoint rereads current/pending immutable outcome references and the
original historical references; the just-retained outcome is compared with its
in-memory expected value before state publication. Missing or corrupted evidence
refuses without reconstruction. Pre-commit drift preserves UNKNOWN and rolls back an attempted
terminal append; any pending immutable/state outcome retains the original
attempt for fresh verification. Post-commit cleanup resumes only the already
recorded terminal decision and does not append another receipt.

Outcome evidence keeps `deployment` and `selection.release` as the original
candidate, adds the immutable `original_attempt`, links exact existing
`prior_outcomes` (including a prior failed candidate), and adds separate
`repair` executor/runtime/transition/admission provenance. Historical files and
append-only journal rows remain unchanged. Old data compatibility must still
be explicitly present in the original selection.

The new original terminal receipt is sequence 3. Admission runs immediately
before outcome/state publication and within the same journal transaction before
append and before commit; withdrawn checkpoints roll back that terminal append.
Pending immutable/state publication resumes the same decision after fresh
verification. Interrupted post-commit cleanup reuses the exact existing terminal
receipt and clears only its original attempt metadata; original evidence remains
in the immutable outcome. There is no second terminal row, new claim or new
deployment advertised as installed. These are attributable observed checkpoints,
not a distributed transaction or a promise of permanent future health.

Mutable selection saves use unique owner-private write staging and atomically
replace the selected record only after complete content and custody checks.
An interrupted unpublished write remains retained evidence, not a shared retry
prerequisite: a renewed transition/admission revision can produce a fresh
verified outcome while the original UNKNOWN remains selected. After terminal
publication, renewal reuses the exact immutable outcome and receipt instead.
Legacy `selection.pending` content still requires exact prefix completion;
foreign or conflicting pending content is never overwritten or ignored.

Resuming a failed terminal decision verifies its retained bridge and current
container attachments as well as its native fingerprints, without repeating
the readiness decision. The resulting paired observation spans final cleanup.
Clearing original attempt metadata is followed by another full publication
checkpoint before current completion returns. Late key, process or endpoint
drift refuses that completion while preserving the already committed original
terminal receipt; it never changes the decision back to UNKNOWN or appends a
second terminal outcome.
The same failure decision is retained before terminal journal publication;
improved health does not reclassify it during an original repair retry.
A native-owner failure receipt retained on the original attempt instead
requires that native owner's reconciliation. Conversely, the native owner
refuses an interrupted repair-owned outcome. These entry points do not
transfer terminal-decision custody or reinterpret each other's provenance.

Successful terminal retries, terminal status and interrupted pending-outcome
retries/status bind the retained outcome's bridge, not the pre-apply attempt's
network-before observation.
Pending-outcome success/failure retries, successful terminal retries and
pending-outcome status also compare every current container with the retained
native fingerprints before credentialed health. A replacement with the same
fixed profile and bridge cannot receive a readiness login as the original
installation. Refusal preserves the exact UNKNOWN or terminal receipt; pending
status retains UNKNOWN and reports unproved custody. Restoring the exact
retained resources permits fresh verification of the same original decision.
A missing or replaced bridge refuses before credentialed health, even when
all current container attachments consistently point at the replacement.
Only the original UNKNOWN's attributed creation path can recognize a bridge
where network-before was absent; a terminal observation cannot establish a new
bridge baseline.
The completed status observation also spans the final authority/custody
checkpoint before return. A bridge replaced immediately after its final
inspection snapshot cannot be reported as a healthy installation.

Final custody checks also compare current storage/credential path identities
and credential content with the original in-memory material observation after
immutable-evidence reads. Late material drift before commit preserves UNKNOWN,
not a terminal receipt followed by a post-commit refusal. A terminal retry or
status call accepts pending attempt metadata only when it exactly matches that
terminal outcome's original attempt; conflicting metadata refuses unchanged
rather than being ignored or cleared.

## Status, support and rollout

`repair_status` reports schema `fleet-manager.komodo-repair-status`, version 1,
separating the original `candidate`/`candidate_release` and `observed` deployment
from current `desired`, `executor_release` and `executor_runtime`. Accepted
status, current readiness and unresolved effects remain separate. Repair does
not converge the newer desired executor deployment or authorize a later update.

Discover the repair contract from the actual executing immutable SDK's imported
control module after its existing runtime/release qualification, never from a
newer source/payload or package-version equality. Expand the provider first,
then register the admitted embedding. A caller whose selected SDK lacks the
contract must refuse before repair effects; there is no manual installer,
baseline-convergence substitution or weaker-permit fallback. Rollback is
supported only by a selected provider that understands the retained evidence
and required contracts. Legacy exact native hashes remain readable by the new
SDK; older readers that cannot understand `mounts-v2:` must refuse rather than
reinterpret it or apply over unproved custody.

The selected [installed compatibility packet](../../TESTING.md) uses
noneditable pre-repair, pre-checkpoint repair and current SDK cells with
checkout reads denied. It covers baseline caller/current SDK, required new
caller/unsupported repair SDK refusal before effects, and supported checkpoint
status/terminal publication with distinct real machine proofs. Fresh-process
interrupted publication and terminal verification retain the original journal,
profile, native identities and data. Provider rollback that lacks required
checkpoint support refuses rather than silently resuming with a whole permit.

Contained synthetic process/HTTP/journal controls and noneditable installed-SDK
qualification are development evidence, not official-image startup, production
adoption or rollout authorization. The
[reviewed lifecycle plan](../../efforts/active/linux-role-fleet/komodo-substrate-lifecycle.md#original-attempt-sdk-repair-amendment)
keeps those independent gates open.
