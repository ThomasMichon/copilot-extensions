# Explicit retained-good client recovery

Back to [the canonical effort](README.md) and
[client lifecycle admission](client-lifecycle-admission.md).

**Contract reviewed in #8106 under #6424; client source implementation and
focused native qualification merged in #8185. Not permission for a live
rollback.** The operator selected `explicit-retained-good`, presented as
"Allow explicit retained-good recovery; keep the admission floor unchanged."
The alternative was requiring a newly selected higher-sequence release to run
older binaries. The requirements and sequencing below are **agent-recommended
implementation details** for the selected policy. The plan cleared review before
executable implementation; complete installer and fleet adoption gates remain open.

## Policy boundary

An explicit, attributed local recovery decision may restore a previously
acknowledged healthy client retained by this installation. It does not admit an
older release again, lower or rewrite the highest accepted release/selection,
select an arbitrary signed release, or authorize automatic fallback.

Fresh current release trust and explicit data/config compatibility are required
before executing or publishing the retained client. Prior health, a completion
marker, a `last-known-good` filename or an old receipt alone is not recovery
authority. Revocation, incompatibility or unknown state refuses rather than
supplying a downgrade bypass.

This is client-only local lifecycle work. It adds no release signer, credential
issuer, remote grant, controller/node role, provider resource, service migration,
new publisher or general operation ledger. Existing installation and publication
custody, immutable candidates and created-object receipts remain authoritative.
Identity retirement/reinstall fencing and the complete managed installer remain
separate obligations with their existing owners.

## Required recovery contract

1. **Explicit decision, exact expectations.** Receive a trusted local decision
   attributed to its owner, decision identity/revision and content digest. Bind
   it to the current installation, expected admitted floor/selection, expected
   active or settled withdrawn state, exact retained candidate/completion and
   current compatibility target. It is not a generic `allow-older` flag.
   Freshly revalidate the decision at execution/publication boundaries; a
   changed decision, admission floor, active owner or target invalidates it.
2. **Actual retained-good provenance.** Require an ordinary, exact completed
   candidate and complete acknowledged installation history proving prior
   healthy activation. Verify its current physical bytes and original release/
   selection binding. Require fresh authentication of supplied immutable release
   evidence under current policy and clock. Do not infer authorization from
   directory discovery or reconstruct a missing older runtime as recovery.
3. **Compatibility before execution.** Reuse the selected closure's API/data
   schema contracts and current authoritative target/state inputs. Bind the
   decision to the actual data/config compatibility being asserted and recheck
   that binding before effects. Refuse missing, stale, unsupported or unknown
   compatibility; do not migrate, replace, restore or weaken protection of user
   configuration or shared data to make an older executable fit.
4. **Monotonic admission, explicit active divergence.** Preserve the durable
   admitted floor and its original selected-release record byte-for-byte.
   Report that the active executable is an explicitly recovered retained release,
   separately from the admitted release. Ordinary admission/construction must
   still reject a lower sequence, and recovery must not become an alternate
   constructor or release selector.
5. **Owned transition, not historical-proof adoption.** Reacquire canonical
   installation, candidate retention and target publication custody. Refuse
   foreign or unsettled owners and incompatible in-flight lifecycle writers.
   Use fresh installed local health and current producer receipts for the new
   transition. Complete evidence precedes pending intent, which precedes effects.
   Older proofs establish historical provenance only; they do not own a current
   replacement object or repair missing/unframed evidence.
6. **One authority across selectors and commands.** Wire the recovered active
   context through the existing selector and command-registration/reuse
   boundaries. A lower active sequence is usable only under that exact recovery
   context and current decision/floor/compatibility, never through a weaker
   registration-only path. Keep command withdrawal and runtime removal
   execution-free under their existing physical proofs. A selector-only change
   is not proof that the user's installed command family has recovered.
7. **Bounded retry and truthful failure.** Bind recovery intent/outcome into the
   existing client lifecycle history rather than introducing a second ledger.
   A retry of the same unchanged decision may settle or revalidate its own
   outcome; a later admission, owner or target change cannot replay it. Retain
   original evidence and classify pre-effect refusal, unknown publication and
   completed recovery distinctly. Never erase the floor or foreign artifacts to
   unblock a retry.

The previous supported policy of preserving an already active healthy client
when new construction fails remains unchanged. No recovery command is triggered
merely because a candidate failed health or because a service is unavailable.
The existing literal last-good selector currently names a selected healthy
candidate; it must not silently be reinterpreted as permission to run another
version.

## Implementation and review sequence

- Review and merge this proposal before changing executable lifecycle behavior.
  It specializes the existing reviewed retained-good follow-on, not the scope
  of the fleet vision or a live deployment permission.
- Define the focused decision/active-context validation and refusal contract
  using existing trusted-input and history conventions. Reuse current release
  verification and compatibility checks rather than forking them.
- Implement the owned selector and command transitions together with their
  recovery-aware read/reuse boundaries. Keep ordinary admission and construction
  strict and preserve dependency-free planning/inspection.
- Expose a narrow explicit client recovery API/CLI only with the complete
  contract, diagnostics and native qualification. Controller/node requests and
  unsupported compatibility remain explicit refusals.
- Record actual source review/CI/merge separately from promotion and any
  separately authorized installed adoption. Do not run an updater, sign a
  release or acquire/enroll hosts to demonstrate this source slice.

## Validation plan

- Prove the complete old/new/recovered state with independently authenticated
  releases: prior healthy activation, a higher admitted floor, explicit retained
  recovery and exact repeat. Assert unchanged admission/selection bytes,
  preserved user config/data and truthful distinct admitted/active reports.
- Refuse no decision, wrong owner/installation/candidate, stale floor or active
  owner, changed/revoked decision, revoked/expired release trust, changed candidate
  bytes, missing acknowledged history and a merely signed but never retained/
  activated release. Historical proof must not authorize a same-byte foreign
  command or selector.
- Prove data/config/API incompatibility and unknown compatibility refuse before
  retained executable execution or publication. Actual compatible-state recovery
  must still run real installed health; mocks cannot supply native acceptance.
- Exercise interruption before pending intent, between selector/command effects,
  before acknowledgement and during a bounded retry. Fresh independent owners
  must observe real durable state, exclusion and uncertainty. No synthetic finish
  record or missing-header exception may be introduced.
- Prove ordinary lower-sequence admission/construction remain refused after
  recovery. A changed higher floor or a different active publication must fence
  a stale recovery decision; removal must still refuse an in-use retained
  candidate and preserve unrelated runtimes and backups.
- Run actual installed and independent-process consumers on native Windows and
  Linux, including both Windows launchers and owned teardown. Retain existing
  stage and caller deadlines; keep focused fault cases separate from expensive
  full/native portfolios rather than expanding the required fast CI matrix.
- Record fresh isolated first-touch and available live tiers separately, with
  an explicit reason for any skipped tier. A local recovery proof is not a full
  fresh OS, controller deployment, live fleet readiness or identity-retirement
  receipt.

The containing Phase 2 and full fleet Validation Plan stay open. This proposal
does not complete install/uninstall, remove retained data, retire an installation
identity or replace the standalone installation-driver owner.
