# Client lifecycle admission - next serial slice

Back to [the canonical effort](README.md). **Agent-recommended plan under #6424,
not a delivered installer or permission to deploy.** The common authenticated
staging prerequisite merged in #6569. Product release authority remains with
#6240; Gateway/node integration remains with #6443.

The durable-admission slice merged in #7459. Its actual CLI and state contract
are documented in [client admission](../../../fleet-manager/docs/client-admission.md).
Construction and compatibility/local health merged in #7804; their actual
contract is documented in [client construction](../../../fleet-manager/docs/client-construction.md).
Owned runtime-selector activation with fresh local health and physically proved,
bounded forward retry merged in #7847. Its actual
contract is documented in [runtime activation](../../../fleet-manager/docs/client-runtime-activation.md).
Explicit [frontdoor registration](../../../fleet-manager/docs/client-frontdoor.md)
merged in #7866 using the shared publication and retained-execution primitives.
Physically proved registered/pending command withdrawal, with durable owned
retry and no runtime rollback, merged in #7882. Exact completed-runtime removal
with durable physical custody, interrupted-deletion recovery and reconstruction
generation fencing merged in #7900. Withdrawal from intact unfinished pending
producer proofs merged in #7958 for commands and selectors; acknowledged owners,
activation and forward retry retain strict receipt handling. Attempt-local
metadata journals and staged namespace retry merged in #8038. Initial publication
receipt-header recovery, retained-good policy and the complete managed
lifecycle remain serial obligations, not delivered behavior or permission for
live activation.
Owned selector withdrawal merged in #7891 and is documented in
[runtime activation](../../../fleet-manager/docs/client-runtime-activation.md#owned-selector-withdrawal).
It requires settled command unregistration, withdraws only proved selectors,
and retains all runtime/rollback bytes, admission and historical evidence.
It is not completed-runtime deletion or installation retirement.

## Scope and ownership

Implement the client-only managed lifecycle in independently reviewable serial
slices. Reuse the installed, RECORD-admitted canonical `versioned-runtime`
resource, existing selected release/preflight/authentication contracts and the
immutable snapshot staging API. Do not build another publisher, signature
verifier, operation ledger, scheduler or controller installer.

Ordinary workstation installation selects only `client`. Controller/node
requests explicitly refuse; no Docker, manager, service, connector enrollment,
credential issuance or ambient trust discovery is a prerequisite or side effect.
The default planner and doctor remain read-only and dependency-free.

Installation identity, state root, attributable selected-release decision,
current policy and artifact sources must be explicit local trusted inputs.
The product release authority selects the coordinated release; the adopter
explicitly selects it for a standalone install. The installer realizes that
decision, never selects a release because it is signed or has a higher sequence.
Installation receipts are not remote operation receipts or authorization tokens.
The operation ledger and node journal retain their own owners and are not
repurposed as installation databases.

## Durable admission before candidate execution

The first implementation slice is installation-owned release admission and its
crash/retry contract, without candidate execution or selector publication:

1. Require a private ordinary installation root, explicit authority,
   installation and client role, and an attributed initialization receipt.
   Existing nonempty foreign roots, links/reparse points, mismatched identity,
   corrupt state and unsupported state versions refuse. Ordinary inspection
   never initializes missing state or acquires credentials.
2. Require the explicit selected-release record/lock from that trusted decision
   owner. Bind its owner, decision identity/revision and content digest to the
   expected release authority, installation, client role, exact manifest
   digest, sequence and selected role closure. Refuse missing, stale,
   mismatched or unselected intent before changing admission state. A signing
   key's validity or a higher sequence does not supply a selection decision.
   This local intent input is neither a remote grant nor a new release selector.
3. Under the canonical installation-scoped exclusive ownership primitive,
   bind that selected intent to a fresh authentication of the exact manifest
   using current trusted policy/clock and consume the selected components'
   immutable snapshots. Do not treat a saved verification report, staged
   pathname or wheel RECORD as release authority or selection.
4. Persist accepted release sequence, exact manifest/content identity and the
   attributable selection binding atomically and durably before any candidate
   execution. A lower sequence or different content at the same accepted
   sequence refuses. A retry at the same sequence and identical release and
   selection binding is idempotent, not new authorization. Explicitly
   distinguish admitted, constructed, healthy and active outcomes.
5. Advancing admission is monotonic even if later construction fails. Preserve
   the previous active healthy client, and permit a bounded retry of the same
   admitted candidate under freshly verified policy. Never reduce the accepted
   floor as incidental cleanup or error recovery.
6. For this first slice, an older release cannot become a new candidate through
   an implicit downgrade. Existing active/retained-good runtime recovery is
   distinct from admitting a new older release. A separately scoped supported
   rollback policy and data/config compatibility proof are required before
   exposing a rollback command; no generic bypass flag is introduced.
7. Unknown commit outcome refuses further effects until durable state is
   reread under ownership. Interruption releases kernel ownership, but a
   surviving lease filename is not liveness or success evidence. Reports name
   uncertainty explicitly and never manufacture a completed receipt.

### Minimal persistence choice

The operator directed a working design without over-engineering. Use a small
installation-owned SQLite journal with the standard library, explicit schema
version, one installation identity and one highest accepted sequence/content
record carrying the selection owner, decision identity/revision and digest.
No generic persistence framework, remote database, operation/outbox tables or
daemon is added. This is distinct lifecycle state, not a second fleet operation
ledger. It records a supplied release selection; it does not generate one.

Reuse the existing local ledger's proven `BEGIN IMMEDIATE`, full synchronous
transaction, scope validation and rollback/connection-close idiom without
modifying its schema or exposing its executor authority. The canonical
installation ownership primitive still fences filesystem construction and
activation; SQLite serializes admission transactions rather than replacing
those leases. Read-only inspection must open existing state in read-only mode,
never create it. Missing/corrupt/unrecognized state refuses until an explicit
owned initialization or recovery flow establishes it.

The current runtime completion/selector helpers provide atomic publication,
but they are not a durable anti-rollback journal and must not be credited as
one. Cross-process commit/reopen and interruption tests must verify actual
SQLite persistence and conflict refusal, not just mock a returned commit.
Document the detailed state format with the implementation. A private local
root and kernel/SQLite locks do not isolate the journal from a hostile process
running as the same OS principal; do not claim that threat boundary.

## Construction, activation and removal follow-ons

After the admission slice passes, construct only from authenticated immutable
snapshots into a separately owned canonical candidate slot. Pin construction
and retention using the existing primitive, recheck policy/identity immediately
before execution, enforce the selected client closure's API/data-schema
compatibility against explicit target configuration and retained state, and bind
completion markers to actual candidate bytes and the installation receipt.
Revalidate compatibility before activation; authentication and candidate health
are not compatibility evidence. Never execute from the ephemeral stage.

Prove the installed client's real console entry point and local diagnostics
outside the checkout without starting a hosting role. Publication is a separate
owned transition after candidate health; failed health keeps the former active
selector/configuration intact. A selector change is not Gateway connectivity or
fleet readiness. Ordinary client configuration and supplied credential-provider
references remain separate from immutable runtime bytes.

Owned removal verifies its installation receipt and every registration before
removing anything. Preserve foreign artifacts, user configuration, backups and
retained rollback data unless a separate explicit policy authorizes their
removal. Do not erase accepted rollback state while retained candidates or
registrations can still be replayed. Whole-install identity retirement and
possible reinstall require their own tested generation/fencing contract.

The exact completed-candidate removal slice retains its physical tree proof
outside the candidate before deletion and uses canonical exclusive cleanup
custody. Pending deletion blocks construction and re-publication until the
same installation/candidate settles. A remaining partial tree may contain only
original captured objects or their absences; it is never recaptured as new
authority. Native deletion does not traverse link targets, completion evidence
is removed last, and historical outcomes precede marker acknowledgement.
The [runtime removal contract](../../../fleet-manager/docs/client-runtime-activation.md#completed-candidate-removal)
defines the API/CLI, refusal and preserved-state guarantees. Ordinary
reconstruction after settled removal still owes current policy, compatibility,
bytes and health; it does not erase or lower the admitted floor. This does not
settle whole-install retirement or retained-good rollback policy.

## Validation and review order

- Land this plan through current-head review and required CI before changing
  lifecycle behavior. It closes part of machine-fleet
  `unified-coordination-product` and the already reviewed standalone-system
  lifecycle intent; it does not extend the vision to workstation hosting.
- First slice: real durable state across distinct processes; first-touch empty
  root, rerun, same-sequence conflicting manifest, lower sequence, concurrent
  admission, wrong role/scope, changed/revoked policy, corrupted state and
  interruption before/after durable commit. Verify the persisted bytes, not
  only a returned report. No execution or selector mutation is permitted.
  A validly signed but unselected higher-sequence release must leave the
  journal unchanged. Also refuse missing or mismatched selection owner/scope,
  revision, manifest, sequence or role closure, and prove that a fresh retry
  binds the same selected intent rather than choosing a replacement.
- Construction follow-on: actual independently signed artifacts, immutable
  snapshot consumption, fresh installed wheel/client, failed build and failed
  health, incompatible client closure/API/data-schema refusal before execution
  or activation, retry/recovery, live held leases, preserved user config and no
  server effects. Incompatibility preserves the prior active client/configuration
  and does not lower durable admission. Native Windows and Linux are required;
  first-touch clean-room and an available real external venue remain distinct
  evidence tiers.
- Activation/removal follow-on: real selector and attributed registration
  ownership, retained-good recovery, current policy revalidation, foreign
  content refusal and explicit uncertainty. Capture every skipped evidence
  tier and its reason rather than converting synthetic evidence to readiness.
- Release and authorized rollout remain separate gates after every `dev`
  merge. No unpromoted payload substitution, signing workflow, key enrollment,
  live capacity acquisition, service migration or retirement follows from this
  plan or its unit/installed proofs.

The full fleet and routing efforts remain Active until their own complete Plan
and Validation Plan gates are resolved or transferred to named tracked work.
