# Consumer proposal: compatibility admission and characterization

Back to [effort](README.md). **Proposal only.** This records #7947's consumer
requirements for #1460's compatibility foundation and #3761's authoritative
mutation owner. Writing or merging this document does not accept their policy,
select a supported floor, or authorize execution, installation or implementation.
The closed #1915 is the still-unresolved durable/integration evidence subsection
of #1460, not evidence that the prerequisite is complete.

## Bounded scope and ownership

The request is to preserve an older **current, compatible** writer's usability
while excluding a genuinely **revoked** incarnation, including a newer binary.
Characterization is by operation, representation and authority semantics, never
`package_version >= minimum`. G1/G3 remain source-qualified only at their
reported isolated tiers. G2 writers, G4 loss/rebuild, G5 Bridge/installer
adapters and G6 publication/force/live backfill are not admitted.

Fresh read-only issue/task/PR checks retained #1460/#3761 ownership and existing
repairs, including #6704's Bridge exact-owner preflight and #6009's worktree
ledger changes. No pre-existing agent was contacted and no shared source was
edited. This is a consumer-side amendment to the existing effort, not a new
foundation effort or registry. Later shared implementation must return its exact
API/callsite/claim scope for admission.

## Real candidate provenance, not a version floor

Exact source, release asset IDs, archive and wheel hashes are in
[candidate-provenance.json](candidate-provenance.json). Public releases:

- [R191 release](https://github.com/ThomasMichon/copilot-extensions/releases/tag/agent-worktrees-v1.24.191-dev1):
  source `b1fced421588afad1e32bdadd7edea5f7b74468f`, manifest version
  `1.24.191-dev1`, plugin tree `14e0c68e5fbd725b6f6b8198a24d90ce453bf90c`.
- [R206 release](https://github.com/ThomasMichon/copilot-extensions/releases/tag/agent-worktrees-v1.24.206-dev1):
  selected promoted-Main source `f15589789226e7eb3a40b345f53a5bc8748ed83c`,
  manifest version `1.24.206-dev1`, plugin tree
  `68cfb4dddf77f944c2fa87d8df9cf9d6d80db36f`.
  Its release target is **different**:
  `9838a346d02a804d54b3c8f5c63334066885502f`; its plugin tree is byte-identical
  to the selected Main tree. Do not substitute whole-repository equivalence.

Each release has **two different digest-pinned archives and plugin wheels**.
Names/version labels alone cannot select the historical artifact. All four
archive SHA-256 values were independently computed and matched release metadata;
all 15 wheel-member hashes in each archive matched its manifest. Six inspected
Worktrees modules also matched their pinned Git bytes in every wheel. The six
modules are identical between R191 and R206; that is **not** proof that the whole
runtime/dependency closure behaves identically. Tags/URLs are locators; the
commit/tree and verified content digests are the retained identity.

R191's reported existing runtime label is not yet bound to either archive by
immutable installation provenance. That binding remains **unproven**, not
inferred from a runtime directory name or a successful cleanup command.
R206 is a promoted source candidate, not an installed-consumer witness.
R191-a/b have no G1/G3 modules; R206-a/b contain G1 facts/markers but no G3
inventory/doctor. Module presence is not a declared semantic write capability.

Only metadata, ZIP members, wheel bytes and Git source were read. No candidate
was extracted, imported, installed, started or executed. No third-party
dependency closure, target interpreter, native platform or historical behavior
was qualified. The archives are `py3-none-any` wheel sets; that tag is not a
native-platform acceptance result.

## Concrete existing seams to characterize

Paths below are under `plugins/agent-worktrees/src/agent_worktrees` at the two
pinned source candidates; full file hashes are retained in the evidence JSON.

| API | Static evidence | Evidence still required |
|---|---|---|
| `record_io.read_record_mapping` / `load_record_uncached` | Raw mapping enters the typed record parser | Actual reader output and original-byte preservation for old, additive, malformed and required-unknown records |
| `record_read.read_exact_record` | Exact project/id read; JSON limits and typed failures; no launch admission | Bounded actual historical read invocation without source substitution or mutation; unavailable versus absent versus unsupported distinctions |
| `tracking.parse_record_data` (1537–2116) | Typed construction with known fields and dedicated opaque controller/backend/execution-leg storage | General unknown top-level, nested claim/session and opaque-island round-trip behavior must be measured, not assumed from those selected opaque cases |
| `tracking._save_record_unlocked` (2293–2919) / `save_record` | Atomic persistence, current-record merges for known revision families, handoff reservations, explicit known-field serialization | Actual older/newer writers on independently authored additive inputs; preservation or scoped refusal, not silent field erasure. Its own comment warns that current-code revision merges do not protect against code unaware of a field |
| `tracking_claim_write.apply_claim_add` / release / settle | Existing record lock and project mutation check, protected reservations/producers, record save before best-effort history | Real owner-qualified operation boundary, return/ack semantics and claim metadata retention; no proposed G2 journal may silently replace this authority |
| `execution_spaces.require_project_record_mutation` | Receiving-side configuration and execution-space/owner qualification | These checks are not, alone, a proven current-versus-revoked runtime-incarnation fence. The owner must identify its actual custody/revocation enforcement path |
| `tracking_write.endpoint_from_rendezvous` (402–462), `write_with_boot`, `dispatch` | Existing verb and verb-payload-version checks before dialing; explicit same-code fallback only when execution could not have occurred; post-send ambiguity is not retried | Actual candidate-advertised verb support, compatible fallback and no duplicated effects after timeout. Verb version is not a package-version floor or generic G2 recovery promise |

This is a narrow trace of existing owner APIs, not a new audit or proof of
universal preservation/fencing. No Bridge/installer adapter code is proposed.
#1460 retains its own current/prior-runtime HostIndex, SpawnTarget, route,
remote-authority, relay/rendezvous and claim-adapter inventory; this Worktrees
consumer proposal does not fill or supersede that inventory.

## Proposed operation-scoped policy for owner decision

_(Agent recommendation; not accepted or executable policy.)_

Keep two independent answers: **may this existing operation proceed under its
current owner contract?** and **can its semantic state be reconstructed after
loss of these stores?** `recoverability=unknown` answers only the latter.

1. **Existing supported observation:** retain the ordinary exact-read path and
   its current source/permission checks. Lack of G1/G3 modules or a G2 journal
   does not make the resource foreign, force an upgrade, or disable unrelated
   reads. No new hidden history scan or reconstruction fallback.
2. **Existing legacy-compatible mutation:** where the actual current owner,
   exact target, representation and revision/merge semantics are already
   supported, retain the existing authoritative transaction/store. Keep
   recoverability unknown and the store non-disposable. Do not add a second
   journal writer or falsely acknowledge complete migration merely to permit
   ordinary work. This does not excuse a known unknown-field-loss hazard.
3. **New additive data:** qualify real writers before placing indispensable
   fields in a representation they may rewrite. If preservation fails, the
   owner chooses an independent immutable sidecar/new record kind or a scoped
   mutation refusal; do not retrofit a guessed schema into deployed old code.
   Existing unaffected operations/resources remain usable.
4. **Unsupported required semantics:** refuse/defer only the operation needing
   those semantics, with a precise compatibility/doctor reason. Observation of
   supported portions remains available. Capability absence is not revocation
   or proof of no obligations.
5. **Current versus revoked:** support is granted by operation evidence plus
   actual owner/custody, never by a greater package version. An older selected
   writer can be current; a newer explicitly replaced writer must be unable to
   commit at the real effect boundary. If an old artifact cannot enforce the
   needed revocation, retain the old current owner or defer the proposed
   takeover until existing custody can safely quiesce/fence it. Do not solve
   that gap by declaring two writers current, disabling a fence, or banning all
   existing work. Verified process quiescence is an owner-controlled transition,
   not a capability invented by changing a version label.
6. **Revision races:** preserve the owning API's actual CAS or documented
   monotonic/field-merge contract. Do not claim every legacy full-record save
   already implements a universal revision CAS. A candidate must not overwrite
   newer accepted semantic facts; failed or unknown outcomes do not authorize
   replay through another channel.
7. **Retention:** while reconstruction or compatibility is unknown, existing
   authoritative stores, backups, original unknown bytes and accepted/uncertain
   outcomes remain authoritative and retained. Owners choose support/rollback
   windows and terminal compaction/removal criteria; no age-based retention
   period or package-age cutoff is invented here. Terminal/transfer evidence,
   live obligations and uncertain effects prohibit premature disposal.

Consumer-visible evidence would distinguish `supported-existing-contract`,
`unsupported-required-semantics`, `unknown-preservation`, `revoked-incarnation`,
`revision-conflict` and `outcome-uncertain`, alongside the independent
recoverability result. These are proposed report vocabulary, **not** registered
capability tokens, protocol negotiation fields or production defaults.

An owner approval must identify the exact **operation + record kind + required
semantic features**, readable/patchable field families and unknown-field rule,
current-incarnation authority path, target/revision rule, artifact/dependency/
platform tuple, independent evidence packet, and support/rollback/retention
conditions. This is the requested review checklist for the existing owners'
contracts, not a new registry or wire schema. A package version alone satisfies
none of these fields.

## Proposed exact characterization matrix — execution not admitted

Fixture expectations must be independent of the candidate producers. Use the
actual digest-pinned artifact's reader/writer in an owned process, not current
code assigned a synthetic old/new label. First bind the observed historical
runtime to an artifact, or explicitly state that the run characterizes a public
candidate rather than that observed installation.

| Row | Actual candidate/code exercised | Independent fixture and required result |
|---|---|---|
| P0 provenance | R191-a, R191-b, R206-a, R206-b | Record archive/member/module hashes, source pin, full dependency/interpreter/OS/architecture tuple and import origins before behavior. No silent current-checkout import or network substitution |
| R1 legacy read | Each artifact's `read_exact_record` and raw loader | Real legacy authoritative record; useful supported output, zero modifications, exact-ID bounded I/O; recoverability remains unknown |
| R2 additive read | Same actual readers | Unknown top-level and nested scalar/map/list fields, opaque controller/backend/leg cases, precision/Unicode edges; retain original bytes and supported interpretation; unknown required semantics scoped, not foreign-owner inference |
| W1 existing compatible write | Each artifact's actual owner-approved `apply_claim_*` transaction and `save_record` | Native legacy fixture with current owner and existing custody; one legitimate mutation succeeds under unchanged semantics. Original store remains authoritative; no G2 completeness claim |
| W2 additive preservation | Each real writer against independent future-additive records | Unrelated known-field mutation preserves unknown fields/revisions/reservations or is refused without modification. Record exact unsupported shapes; do not rebaseline expected data to match a lossy serializer |
| W3 required semantics | Each real writer and its actual verb advertisement/dispatch | Missing/older operation capability does not call a peer that cannot understand it. Only the already-sanctioned same-handler fallback is eligible before any possible execution; no fallback after uncertain send |
| F1 version/authority reversal | Owner-selected R191/R206 pair, then reverse roles | Older artifact current + newer revoked must admit only the current compatible effect. Reverse labels and repeat. Exercise actual custody/transition guard and its accepted ticket, not only a stubbed boolean |
| F2 same-version control | Two incarnations of each admitted artifact | Current versus revoked distinguishes ownership without any software difference. Missing incarnation metadata is unknown, not a fabricated successful fence |
| C1 revision race | Current older/newer pair through actual effect path | Barrier-controlled stale revision and newer accepted update; one valid commit or documented safe field merge, no lost unknown facts. Retain exact loser/uncertainty reason |
| A1 lost response | Actual candidate transport plus owner transaction | Interrupt before send, after possible execution, after durable save and after reply admission. No duplicate effect; original authoritative store and uncertain result survive. No G2 journal completeness asserted |
| T1 retention/cold read | Same real readers over retained fixture copies | Removed disposable observations do not remove original authoritative stores. No reconstruction/loss test of unqualified stores; report unknown, no live ownership resurrection or lease recreation |
| B1 cost/preservation | Each candidate's scoped operation | Grow fixture history independently of active request size; count I/O/bytes and forbid hidden replay. Keep synthetic Git/index/stash/ignored/untracked/session/checkpoint/config/account/consent/credential bytes intact |

The four artifacts are not collapsed merely because their six inspected modules
match. A later owner may reduce duplicate rows only with documented whole-closure
equivalence and unchanged platform evidence. For F1/C1, the owner must select and
pin the actual pair; choosing R191-a/R206-a for convenience is not yet admitted.
Every row reports pass/fail/unsupported/unknown plus exact source/runtime
provenance. An unsupported row is evidence for scoped policy, not a pass.

## Containment and admission gate

Before any candidate import/install/execution, approve the exact matrix rows,
artifact pair(s), target tuple(s) and owner custody adapter. Use the repository's
existing bounded test supervisor and test-owned source/state roots, not a new
daemon or fixture authority service. Independently demonstrate that every home,
registry, database, credential source and endpoint resolves inside the fixture;
fail closed if an artifact cannot be redirected. No credentials, real accounts,
existing agents, live sessions/leases, external resource writes or inherited
service endpoints are exposed.

Use offline pinned dependencies, no normal updater/installer, startup hooks,
service registrations or shared runtime selection. Preserve headless process
and descendant cleanup bounds. Any OS custody/concurrency proof belongs in the
existing native qualification lane, not the unit-only PR gate. A partial
source-call fixture does not prove a full CLI/installed-runtime contract.
No old-runtime execution was performed for this proposal.

## Concrete decisions requested from the owners/admission coordinator

1. **Artifact binding:** which R191 artifact matches the observed runtime, and
   what immutable receipt/module/dependency evidence proves it? If neither is
   provable, admit a public-candidate characterization explicitly, not a false
   historical-installed witness.
2. **Legacy continuity:** approve or revise the proposed separation between
   supported existing operations and unknown reconstructibility. Specify
   exactly which existing stores remain authoritative and which future shapes
   require a sidecar or scoped refusal.
3. **Actual fence:** #3761's owner names the real current/revoked effect guard,
   target/revision checks and safe quiescence alternative. #1460 supplies its
   own compatible contract/retention rules; this document supplies no new
   authority token or package floor.
4. **Preservation and retention:** name supported field families, required
   semantics, rollback/support windows and terminal/uncertain-effect retention
   criteria. Current-code opaque islands are not a universal guarantee.
5. **Execution admission:** approve selected rows/artifact pairs/platform tuples
   and containment independently of this plan's review. Return any later shared
   API implementation scope for a fresh claim check; no G2/G4/G5/G6 follows
   automatically from merging this proposal.
