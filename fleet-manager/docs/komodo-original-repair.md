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

Contained synthetic process/HTTP/journal controls and noneditable installed-SDK
qualification are development evidence, not official-image startup, production
adoption or rollout authorization. The
[reviewed lifecycle plan](../../efforts/active/linux-role-fleet/komodo-substrate-lifecycle.md#original-attempt-sdk-repair-amendment)
keeps those independent gates open.
