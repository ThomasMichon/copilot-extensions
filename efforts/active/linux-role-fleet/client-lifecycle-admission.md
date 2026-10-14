# Client lifecycle admission - next serial slice

Back to [the canonical effort](README.md). **Agent-recommended plan under #6424,
not a delivered installer or permission to deploy.** The common authenticated
staging prerequisite merged in #6569. Product release authority remains with
#6240; Gateway/node integration remains with #6443.

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

Installation identity, state root, current policy and artifact sources must be
explicit local trusted inputs. Installation receipts are not remote operation
receipts or authorization tokens. The operation ledger and node journal retain
their own owners and are not repurposed as installation databases.

## Durable admission before candidate execution

The first implementation slice is installation-owned release admission and its
crash/retry contract, without candidate execution or selector publication:

1. Require a private ordinary installation root, explicit authority,
   installation and client role, and an attributed initialization receipt.
   Existing nonempty foreign roots, links/reparse points, mismatched identity,
   corrupt state and unsupported state versions refuse. Ordinary inspection
   never initializes missing state or acquires credentials.
2. Under the canonical installation-scoped exclusive ownership primitive,
   re-authenticate the exact manifest using current trusted policy/clock and
   consume the selected component's immutable snapshot. Do not treat a saved
   verification report, staged pathname or wheel RECORD as release authority.
3. Persist accepted release sequence and exact manifest/content identity
   atomically and durably before any candidate execution. A lower sequence or
   different content at the same accepted sequence refuses. A retry at the
   same sequence and identical identity is idempotent, not new authorization.
   Explicitly distinguish admitted, constructed, healthy and active outcomes.
4. Advancing admission is monotonic even if later construction fails. Preserve
   the previous active healthy client, and permit a bounded retry of the same
   admitted candidate under freshly verified policy. Never reduce the accepted
   floor as incidental cleanup or error recovery.
5. For this first slice, an older release cannot become a new candidate through
   an implicit downgrade. Existing active/retained-good runtime recovery is
   distinct from admitting a new older release. A separately scoped supported
   rollback policy and data/config compatibility proof are required before
   exposing a rollback command; no generic bypass flag is introduced.
6. Unknown commit outcome refuses further effects until durable state is
   reread under ownership. Interruption releases kernel ownership, but a
   surviving lease filename is not liveness or success evidence. Reports name
   uncertainty explicitly and never manufacture a completed receipt.

The implementation must reuse an established atomic-write/durable persistence
primitive after inspecting its actual consumer contract. If the available
primitive cannot supply the required persistence or installation-wide fencing,
coordinate that missing shared seam with its owner before adding another
engine. The detailed state format and primitive choice must be documented with
the implementation; this proposal does not assume a current path lock is an
isolated boundary against another process running as the same OS principal.

## Construction, activation and removal follow-ons

After the admission slice passes, construct only from authenticated immutable
snapshots into a separately owned canonical candidate slot. Pin construction
and retention using the existing primitive, recheck policy/identity immediately
before execution, and bind completion markers to actual candidate bytes and
the installation receipt. Never execute from the ephemeral stage.

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
- Construction follow-on: actual independently signed artifacts, immutable
  snapshot consumption, fresh installed wheel/client, failed build and failed
  health, retry/recovery, live held leases, preserved user config and no server
  effects. Native Windows and Linux are required; first-touch clean-room and
  an available real external venue remain distinct evidence tiers.
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
