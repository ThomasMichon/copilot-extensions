# PR-service artifact construction

Back to the [canonical effort](README.md) and
[fleet service plan](fleet-pr-service.md). Coordination remains #7188 under
#6007. This is an implementation proposal, not an available image or rollout.

The operator selected `extend-shared-artifacts`: extend the shared artifact
path for PR-service image inputs rather than require a supplied wheelhouse.
The decomposition and acceptance below are **agent-recommended**.

## Reconcile and preserve

This closes the existing first-class PR-service and independent-delivery intent;
it does not introduce another installer, resolver, release authority or fleet
controller. Reuse the locked wheel builder, candidate composition, provenance
verification and canonical dependency/installation contracts. Preserve the
[governed artifact campaign](../governed-python-artifact-promotion/README.md),
including its exact-lock and source-fallback decisions.

Public release bundles contain first-party wheels only. The public-runner
build-toolchain exception does not authorize public runtime dependency
resolution, distribution of third-party bytes or certification of an adopter's
governed feed. An offline image context containing third-party wheels is an
admitted construction input at its owning build venue, not a new public release
bundle. No new credential, placement, capacity or deployment permission follows
from this proposal.

## Construction boundaries

1. **First-party release inputs.** Extend the existing artifact builder for the
   explicitly registered `services/pr` and `fleet-manager` distributions needed
   by the service. Reuse its shared locked toolchain, wheel-generator checks,
   source/content identity and wheel hashes. Extend the existing candidate
   index/bundle and verifier contracts explicitly for standalone identities;
   never masquerade a standalone as a plugin or relax old plugin validation.
   Keep existing plugin and vendored-library artifacts compatible.
2. **Selected package graph.** Bind the service, PR core, Fleet SDK and local
   first-party dependencies to their approved source/release identities. Reuse
   canonical source retention and declared dependency mappings, including
   required non-project build resources. The native snapshot command remains a
   separately selected Worktrees artifact; a version requirement must not select
   another installation or fetch a same-named package as a substitute.
3. **Runtime closure admission.** Compose the existing exact-profile and
   installation authority contracts for the target Linux platform/interpreter.
   Record the selected first-party release, exact external versions and artifact
   hashes, applicable seasoning and configured-feed evidence. Missing approval,
   unavailable exact artifacts, a blocked dependency or unprovable compatibility
   refuses this construction; it does not silently re-resolve or loosen the lock.
   Any needed lock-export or acquisition seam belongs in the canonical owner,
   with the same retained-input and resolver-policy checks, not a PR-only resolver.
4. **Offline context.** Produce a dedicated context containing only the existing
   PR Dockerfile, the admitted platform-matching wheel closure and its complete
   exact-version SHA-256 requirements lock. Reject duplicate distribution
   identities, missing/extra or changed artifacts, unsafe paths and incompatible
   wheel metadata before publication. Keep feed credentials, host configuration,
   source checkouts and replay volumes out of the context and its diagnostics.
   A generated hash or manifest proves byte identity, not approval by itself.
5. **Image and consumer proof.** Build with an explicitly selected digest-pinned
   Linux base and no installation-time network. Exercise the actual image
   without Copilot, marketplace hooks, host source mounts or a Docker socket.
   Reuse the service's real Gateway and native snapshot contract with synthetic
   identities/provider inputs; do not replace the packaged runtime with stubs.
   Fresh provision, durable restart, original-ID replay/ACK, duplicate-writer
   refusal and graceful drain remain separate scored controls.

## Delivery sequence

- [ ] Review and merge this construction proposal before changing artifact
  schemas, release selection or dependency acquisition.
- [ ] Deliver standalone first-party build/verification through the shared
  artifact path, initially scoped to the PR service's required distributions.
  Preserve existing release consumers and optional artifact/source fallback.
- [ ] Deliver approved target-specific runtime closure and offline-context
  construction through existing authority/installer contracts.
- [ ] Build and exercise the real isolated image through an available
  source-validation lane. Keep subprocess-heavy/container acceptance in the
  bounded manual portfolio, not required unit-only PR/promotion CI.
- [ ] Record exact candidate/release and image identities, actual evidence tiers
  and remaining provider, storage-transfer, update/rollback and fleet gates.

## Validation

- [ ] Unit controls cover standalone identity/schema handling, unchanged plugin
  manifests, duplicate sources, source/resource containment, generator/toolchain
  mismatch, changed hashes, candidate omission/substitution and signature refusal.
- [ ] Real builds verify the standalone wheels against the shared toolchain and
  install them from outside a checkout. Prove the selected native Worktrees
  command is the packaged approved artifact, not an ambient executable.
- [ ] Real governed-feed/profile admission proves exact platform-compatible
  acquisition and every dependency's policy evidence. Negative controls refuse
  missing approval, blocked/unavailable pins and attempted public fallback.
  A public CI run or synthetic profile alone does not pass this gate.
- [ ] A fresh offline image installs the complete lock with hash enforcement;
  missing or modified wheels fail. Check non-root execution, read-only image
  filesystem, isolated persistent volume, mounted read-only references and
  absence of source/credential material in the built context/image.
- [ ] Actual container processes exercise provision, register/observe/replay/ACK,
  same-volume restart, second-writer refusal and bounded graceful termination.
  Capture the image digest and selected inputs; a plan or native wheel test is
  not container proof.

These checks do not close real credential-provider qualification, cross-host
fencing, interrupted update/rollback, authorized installed rollout, fleet role
placement or the parent effort's retained client/dispatch acceptance.
