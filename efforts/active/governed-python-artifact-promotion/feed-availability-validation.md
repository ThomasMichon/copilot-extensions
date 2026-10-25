# Bounded consumer-feed availability validation

- **Owner:** [Governed Python Artifact Promotion](README.md)
- **Tracking:** [#7529](https://github.com/ThomasMichon/copilot-extensions/issues/7529)
  (construction/launch qualification) and
  [#4876](https://github.com/ThomasMichon/copilot-extensions/issues/4876)
  (artifact promotion and admission).
- **Status:** proposed implementation contract; documentation-only design slice.
- **Intent:** [release-time feed availability](../../../visions/release-pipeline/README.md#consumer-feed-availability-before-promotion)
  and [runtime admission authority](../../../visions/plugin-services/runtime-assembly/README.md#release-time-feed-evidence-without-client-authority-transfer).

## Scope and existing owners

Trusted CI must not promote a candidate whose dependency requirements are
unavailable through the consumer-governed feeds declared for that release.
Public-runner success and dependency age are not substitute evidence.
Redundant signed-in workers with actual access to those feeds perform bounded
validation; feed credentials never leave their local provider/transport.
No language model, designated leader or new leader-election platform is needed.

This extends the existing effort rather than starting another resolver,
publisher, queue platform or service-lifecycle owner. The generic mechanism is
authenticated submission, subscription, durable atomic claiming and result
correlation. Provider adapters supply feed observations; adopter policy supplies
approved feed scopes, eligible identities, supported probes and limits.
Neither an adapter nor a CI caller can edit promotion policy or workflows.
No provider, CI brand, host runtime or personal machine inventory is prescribed.

The production [installer dependency contract](../../../docs/install-contract.md#external-dependency-seasoning)
owns retained resolver inputs, index ordering/authentication, TLS, cutoffs and
source admission. In particular, `run_uv_compile`/`retain_uv_compile` and
`retain_uv_source_compile` are candidate exports, not artifact admission;
`prepare_uv_install` alone is not the installation boundary. Validation must
integrate these canonical owners, including their unsupported-setting refusals,
not approximate their configuration in a second HTTP or package resolver.
The existing exact artifact profile and separately authorized source-fallback
lock remain distinct selections; validation cannot relax either lock.

The existing [Gateway](../../../fleet-manager/docs/gateway-runtime.md#admission-and-wire-contract)
and [host-resource provider](../../../visions/host-resource-providers/README.md)
boundaries own authenticated reach and capability scope. Preserve signature,
issuer, tenant where applicable, audience, expiry, proof/enrollment and
authenticated-role checks, including `GatewayClient` role admission where
required by the selected ingress. The SDK class of that name is not itself a
role grant. Do not substitute a permanent human-only identity, a shared secret
or a new trust issuer for the selected authentication contract.

Existing Gateway request/session queues do **not** establish a durable work API:
the documented runtime invalidates ephemeral sessions/queues on restart.
Implementation must identify the existing durable work/effect owner to extend,
review its bounded operations, and qualify its retention and restart behavior.
An admitted CI workload identity and route also require explicit design,
authorization and end-to-end qualification; neither is assumed available today.
Gateway routing does not grant feed access, installation or release authority.

## Exact scope and evidence

The trusted gate derives the expected coverage from the immutable candidate,
manifest and authoritative environment inventory, not a caller-supplied
"complete" flag. A request binds the candidate/manifest identity or complete
closure digest and its source/composition binding; each result must match:

- supported OS, architecture, interpreter/ABI and marker environment;
- capability profile and selected extras, bootstrap/build inputs and every
  transitive runtime/build dependency, including the applicable build-isolation
  and artifact variants;
- the selected exact pins and hashes where a lock exists; an unclosed
  requirement range is not exact-artifact admission;
- the admitted feed scope and resolver/security/seasoning policy revision,
  adapter/probe semantics and coverage, observation time and expiry.

Unsupported cross-target resolution, ambiguous marker/extras selection,
unclassified environments, partial closures and missing artifact variants are
blocking outcomes. A worker may only validate a target/probe it can actually
support. A native-host result does not silently cover another platform.
Changed candidate bytes, closure, target, policy or probe semantics invalidate
incompatible evidence; freshness is checked again at the promotion boundary.

Workers return minimal credential-free reasons and evidence references, not
raw resolver logs, authenticated URLs or configuration. The gate independently
validates origin, current ownership fence, bindings and full expected coverage.
An authenticated caller cannot self-certify coverage or mint a passing result.
Observations are not a second provenance authority or authorization to install.
Consumer admission still runs at acquisition, including after policy changes.

## Probe admission and outcomes

Metadata-only observation is preferred only **after empirical qualification**
shows that this feed/probe establishes admission of the exact selected artifact
under the production contract. A version search/list, package age, cached hit
or successful HEAD is not automatically proof of retrievable/admitted bytes.
The probe's proven scope and limitations are part of the evidence.

Where metadata cannot establish that property, an explicitly configured and
approved policy may allow bounded fetch-and-discard of the exact artifact,
with digest/identity and resolver-policy verification. This is not execution,
installation, repackaging, publication or redistribution of third-party bytes.
Temporary byte custody, cleanup and local storage limits belong to that policy;
no artifact bytes or credentials return to CI. If neither probe can prove the
required scope, validation remains indeterminate and promotion is blocked.

| Outcome | Meaning and gate effect |
|---|---|
| Available/admitted | Exact supported artifact scope established under the bound policy; usable only with complete, fresh, validated coverage. |
| Unavailable/refused | Actual feed observation denies the required artifact/scope. Preserve the precise reason; promotion blocks. |
| Indeterminate | Authentication failure/expiry, no eligible worker, network failure, timeout, unsupported probe/target, ambiguous scope or unknown result; promotion blocks. |

Absence and quarantine may be observationally indistinguishable. Preserve
what the feed actually said, its reason/evidence and the probe's limits;
do not invent a quarantine diagnosis from a missing version. A public-source
fallback, weaker cutoff, changed pin or unrelated cache cannot turn refusal
or uncertainty into a pass.

Availability evidence is a necessary dependency gate, not proof of successful
construction or first launch. The existing #7529 fresh configured-feed
construction/launch obligation remains separate and cannot be replaced by a
metadata-only or fetch-only worker result. Public build-toolchain exceptions
remain limited to their already approved tooling scope.

## Durable first-ready worker protocol

1. **Submit:** trusted CI sends a bounded batch through the selected existing
   authenticated Gateway/transport. The durable owner binds an original request
   ID to immutable scope, submitting authority, deadline and policy. Duplicate
   submission with identical identity/scope returns that operation; reuse with
   different scope refuses. Untrusted candidate code cannot submit arbitrary
   commands, URLs, credentials or gate changes.
2. **Subscribe and claim:** eligible signed-in workers subscribe through the
   admitted route. A notification is only a hint; readiness includes current
   identity, supported target/probe and actual feed access. First-ready workers
   compete for an atomic durable claim, not a priority machine or elected
   leader. Duplicate/out-of-order hints do not create duplicate authoritative
   work.
3. **Lease and fence:** each claim receives a durable renewable lease and a new
   monotonically changing ownership fence. Renewal and completion compare
   current request, lease and fence atomically. Lease expiry, sleep, process
   loss or revoked eligibility allows reassignment within the original deadline
   and retry budget. The displaced worker cannot renew or complete, even if
   its probe finishes later; overlapping bounded reads may occur, but only the
   current owner can publish an accepted result.
4. **Complete:** persist accepted results and their exact bindings before
   acknowledging. Retry of the same completion is idempotent; a conflicting
   completion refuses. Recovery after request/worker/service restart reloads
   durable state and fences rather than accepting an old session's authority.
5. **Recover and consume:** lost submission/completion ACKs are reconciled by
   querying the **original ID**, never by inventing a fresh request to bypass
   unknown state. CI consumes the authoritative terminal record, revalidates
   freshness and coverage, and leaves promotion blocked on unknown state.

The owner configures finite batch/item sizes, outstanding requests, queue
duration, overall/probe time, retry/renewal counts, parallelism, network
requests, transferred bytes, local storage and result retention. Renewal
cannot extend the original operation deadline indefinitely. Cancellation and
expiry fence completion and stop owned activity within its bound, without
claiming a remote effect stopped merely because a message was lost.
Retention covers the advertised retry/CI-consumption horizon; expired
original IDs return an explicit expired/unknown result, never a new success.
No unbounded polling, per-request daemon or silent replay after restart.

## Lifecycle and implementation boundary

No implementation, service registration, CI workflow edit, credential grant
or deployment is authorized by this design slice. The future worker capability
must be installed and configured through its existing service owner, using
the [install contract](../../../docs/install-contract.md) and
[graceful/cold takeover pattern](../../../docs/patterns/graceful-daemon-cutover.md).
It must support independently testable install, health, update, rollback,
disable and stop, with restart recovery and explicit cleanup/retention.
Healthy replacement does not clear another owner's live obligation.

## Implementation validation plan

These are **future gates**, not tests credited to the documentation PR.
Execution stays with #7529/#4876 and their existing participants; accepting
this design does not complete or transfer their broader effort.

- [ ] Unit contracts: trusted coverage derivation, exact binding/freshness,
  unsupported and ambiguous scope, authentication/role denial, probe admission,
  outcome classification, budgets, idempotency and conflicting retry refusal.
- [ ] Real durable-owner races: simultaneous first-ready claims, lease renewal
  versus expiry/reassignment, late old-holder completion, duplicate delivery,
  lost ACK/original-ID recovery, cancellation, restart and retention expiry.
- [ ] Empirical feed probes: known allowed, known refused and uncached exact
  artifacts; absence/quarantine ambiguity; metadata sufficiency or its refusal;
  configured fetch/discard bounds and cleanup; expired authentication, network
  loss and no eligible worker. No warm-cache or public-index false positives.
- [ ] Production resolver integration: exact release and source selections,
  native targets/extras/marker variants, transitive/bootstrap/build closure,
  stricter cutoffs/TLS/index settings and unsupported configurations. Exercise
  fresh configured-feed construction and installed first launch separately.
- [ ] Live route: an actual trusted CI identity submits via the selected
  Gateway/transport; two eligible feed-access workers race; sleep/terminate the
  holder and prove real reassignment, stale-fence rejection and CI result
  consumption. Deny unauthorized submission/completion and forged coverage.
- [ ] Lifecycle: clean install/configure, health, update with outstanding work,
  rollback, service restart, disable/stop and bounded byte/record cleanup.
  Preserve original IDs/fences and last healthy serving state where supported.

Required PR testing retains the repository's bounded unit portfolio.
Multi-process, feed, CI-route and lifecycle checks use explicitly selected
integration/clean-room/live lanes; they are not relabeled as unit evidence.
Promotion's already declared construction/launch exception remains unchanged.
