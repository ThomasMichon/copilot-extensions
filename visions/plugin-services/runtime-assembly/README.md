# Runtime Assembly & Artifact Admission — Vision

- **Subject:** How a host acquires, verifies, and assembles the immutable
  runtime generations that a selected release or standalone installation
  needs, from an approved artifact source, under one provenance identity.
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-24
- **Reality docs:** [`docs/install-contract.md`](../../../docs/install-contract.md) ·
  [`docs/pipelines.md`](../../../docs/pipelines.md) ·
  [`docs/architecture.md`](../../../docs/architecture.md)

## Purpose & Intent

The release pipeline already gives first-party runtime artifacts a stable,
content-bound identity and, through promotion, an attestation. That
identity is only worth something if the machines that run the code consume
it. The north star: **every runtime generation a host activates is
assembled from artifacts whose identity and provenance the host verified,
through one admission path, whatever the entry point.** A fresh bootstrap,
a routine update, a cache miss, a repair, and an image build all reach the
same verified result or the same explicit outcome.

Admission is consumption, not a second release system. It reuses the
builder, promotion, and attestation identity the pipeline produces. It never
invents a parallel provenance record, signature, or artifact naming scheme.

The generic suite stays neutral about where artifacts come from. An adopter
that must consume through a governed internal feed or other approved
artifact source routes acquisition there through its own desired state. The
suite honors that routing and never silently replaces it with a public
source.

## Concepts & Components

- **Release lock** — the exact, closed set of first-party artifacts and
  third-party dependency pins a release or standalone installation resolves
  to, partitioned into a core and optional heavy environments. It is the
  input to assembly, not something admission silently re-resolves. A
  source-build fallback selected by the same installation authority carries
  its own explicit exact installation lock; it never masquerades as the
  unavailable artifact lock.
- **Artifact identity and attestation** — the content-bound identity and
  promotion attestation the release pipeline assigns to first-party
  artifacts. Admission verifies against this identity rather than against a
  name or version string.
- **Approved artifact source** — wherever an installation is permitted to
  acquire artifacts: the public first-party release, a governed internal
  feed, an adopter accelerant, or a pre-seeded local cache. Which sources
  are approved is adopter desired state, not suite configuration.
- **Assembled generation** — an immutable runtime generation built only from
  admitted artifacts. It carries evidence of the lock it realizes and
  whether it is complete.
- **Admission outcome** — the single result of an acquisition attempt:
  assembled, already present, deferred until a named artifact becomes
  available from an approved source, or refused with a precise reason.

## Features

### adopter-supplied-complete-defaults

Direct invocation of a trusted harness setup flow by a user or an authorized
agent is the installation action that authorizes the dependencies required by
the selected harness configuration. That authority carries through the
installer and its selected components, including their transitive dependencies;
it does not require a separate approval service, per-package consent or an
adopter-authored approval map. An already-authorized controller applies the
same boundary for its addressed installation.

An adopting harness can supply a complete, reviewed selection policy that
works for operators who never customize local runtime settings. Its existing
installation authority applies that policy automatically: prefer compatible
verified release profiles, and select the separately governed source mode
when availability or admission prevents artifact mode. Routine fallback does
not require a human to approve individual dependencies or write an approval
file. Optional overlays may select deliberate capabilities or stronger
restrictions; they never weaken existing acquisition policy.

The baseline selects behavior, not a new trust root. A release profile still
requires authentic release approval and actual feed admission. Source mode
still binds independently verified source and freezes a complete exact closure
within declared constraints before assembly. Neither the presence of a
candidate nor its self-declared approval authorizes it. Adopting a default
never grants service activation, model download or unattended execution.
The invoked setup approves the required dependency operation, not arbitrary
packages, changed configuration or acquisition-policy exceptions. Signature,
integrity, source, seasoning and revocation checks enforce that authorization;
they are not another actor from whom ordinary setup must obtain consent.

### one-verified-acquisition-path

All entry points share one lookup, verification, and assembly path:
interactive install, unattended update, session-triggered detached update,
repair, cache or seed miss, filling a missing artifact, and container image
construction. Each verifies the content identity of every first-party
artifact and every third-party dependency against the release lock before it
contributes to a generation (or against the authority's exact installation
lock for a selected source fallback). A cache, seed, or accelerant speeds up
acquisition. It is never a separate trust root. While another approved
source for the same artifact within the selected lock/path is reachable,
losing it changes only how long acquisition takes. Exhausting those sources
defers that path; admission never substitutes a different or unverified
result within it. The installation authority may instead select the separate
governed source-build path and its explicit exact installation lock (see
*governed-client-source-fallback*). If neither permitted path succeeds, the
outcome is deferred or refused (see *deferred-not-degraded*). If an update
leaves the dependency set unchanged, assembly reuses artifacts already
admitted on the host and needs no access to a package index or feed.

### first-party-only-public-distribution

Public releases carry first-party code and the release lock only. Third-party
dependencies are acquired by each installation from its approved sources
according to the lock, never bundled into a public artifact. Any
redistribution of third-party code happens only through a governed channel
whose terms permit it.

### governed-consumption-without-public-fallback

An installation routed to a governed source acquires runtime dependencies
only from that source. A public version that resolves from a public index,
or that has passed a minimum-age policy there, is not evidence that the
governed source has mirrored it. When the governed source
lags the lock, the outcome is **deferred**. It names the missing artifact and
the action that would unblock it, and leaves the current generation serving.
It never falls back to a public runtime index, relaxes a pin, or substitutes
another version within that lock. The installation authority may instead
select the governed local-source fallback below; if that cannot satisfy
policy either, the prior healthy generation remains serving.

### governed-client-source-fallback

Prebuilt first-party artifacts accelerate installation; their availability
is not a prerequisite for a working client. When artifact discovery,
acquisition or dependency admission is unavailable, the same installation
authority can select local assembly from independently verified source for
the selected release. Clients use that permitted fallback rather than
treating artifact unavailability as a terminal installation failure.
The installation authority establishes an exact fallback installation lock
within declared dependency constraints and approved-source policy before
assembly, and records the mode and reason rather than claiming to have
realized the unavailable artifact lock.

Fallback uses the same immutable staging, health, activation and rollback
contract. It never bypasses a blocked package, seasoning requirement or
governed feed, and an artifact-integrity or source-provenance failure is not
an ordinary availability fallback. If no permitted dependency closure can
be assembled, the outcome is explicit and the current generation survives.

### selected-development-source-assembly
An authority-selected, clean merged development snapshot can be assembled
directly, without waiting for a generated release-branch payload or installing
Copilot plugin content from that checkout. This explicit source mode is not
an artifact-availability fallback and does not claim promotion attestation.
It retains the same source integrity, exact dependency admission, immutable
completion, health, activation and rollback guarantees.

Assembly materializes the complete declared first-party dependency closure
from the selected snapshot, including development-only source indirections.
Materialization and version composition use the canonical source
representation rather than a second approximation of promotion. The
assembled runtime contains no live dependency on the source checkout.
Source identity, package version, optional capability profile and exact
assembly/dependency identity remain separately observable.

Interactive development also freezes source and build inputs before assembly;
it never deploys an editable venv linked to the working checkout. The complete
source hash contributes to generation identity even when the package version
is unchanged. A new edit requires a new immutable generation or exact healthy
reuse, not mutation of the selected environment. This does not relax the clean,
merged-source admission required for unattended Dev deployment.

Selecting a generation initiates ordinary start and best-effort handoff.
The displaced owner is identified by inequality with current selection, never
by numeric version or hash ordering. Forward and compatible reverse selection
prefer the same healthy baton-passing contract. Incompatible or failed handoff
permits prompt predecessor retirement and cold startup, even before incoming
readiness; a breaking development generation is not refused merely because
its predecessor cannot cooperate. The service lifecycle owns identity-checked
termination and exclusive ownership, and consumers tolerate the outage while
the newcomer recovers durable work. Immutable construction, source admission
and state-safe recovery are not waived.

### optional-client-artifacts-complete-promotion

Artifact consumption is optional for install and update: an authorized,
governed local-source build does not depend on successful artifact
discovery, matching or publication. Availability evidence required to
complete a promotion is not an additional client installation gate.

The promotion flow produces, verifies and establishes availability of the
exact candidate's artifacts before merging its PR or declaring completion.
Build, signing or publication failure leaves promotion incomplete and its
candidate unmerged, with visible, idempotent retry rather than a
source-only success claim. Source validation, signer/source binding, digest
verification and immutable-publication requirements remain strict.
Source promotion, asset publication and runtime activation have distinct
outcomes; success in one never impersonates the others.

### complete-or-rejected-generations

A generation is activatable only when assembly finished and its evidence
matches the lock it claims. An interrupted or partial candidate is never
selected and never mistaken for a usable runtime. Generations assembled under
an earlier layout stay readable and selectable through a compatible resolver,
so adopting a new release shape does not invalidate a working runtime. Which
environment a command or service runs in is an explicit property of the
generation, never inferred from a path convention.

### host-and-image-parity

The same admission and assembly path produces a native host installation and
an OCI image. Neither requires Copilot, a plugin marketplace, a plugin
registry, or a session to exist. An image built from a lock is the same
verified generation a host would assemble, configured through ordinary
installation configuration and mounted state.

## Behaviors

### deferred-not-degraded

When neither the artifact path nor an authorized governed-source fallback can
satisfy admission, the outcome is explicitly deferred or refused and the last
healthy generation remains in service. An unavailable source or lagging feed
never produces a half-assembled runtime, an unverified substitute, a policy
bypass or a silently downgraded capability.

### no-parallel-provenance

Admission reads the provenance the release pipeline already produces. A new
consumer, cache, or entry point reuses that identity and its verification
rather than adding its own record of what an artifact is or where it came
from.

### feed-neutral-core

The generic suite names no specific internal feed, organization, mirror, or
credential provider. Source routing, approved-source policy, and credentials
come from the adopter's external desired state and credential providers.

### observe-credentials-repair-on-consent

Acquisition reports the health of the credentials and sources it depends on.
It does not enforce a credential layout, and it does not rewrite working
authentication configuration. Cleanup of stale or conflicting credential
configuration is offered as an explicit, opt-in action, never performed
silently.

## Non-Goals / Boundaries

- **Not the builder or the promotion pipeline.** Building, promoting, and
  attesting artifacts belong to the release pipeline
  ([`docs/pipelines.md`](../../../docs/pipelines.md)). This vision owns
  consumer admission and the cross-boundary pre-merge availability
  requirement, not the pipeline mechanism that satisfies it.
- **Not release selection.** Which release, role closure, or optional
  environment a host should run is decided by the release authority
  ([plugin-services *one-authority-per-lifecycle-decision*](../README.md#one-authority-per-lifecycle-decision)).
  Admission realizes a selection. It never chooses one.
- **No public runtime-index fallback** for an installation routed to a
  governed source, and **no third-party code in public releases**.
- **No adopter-specific feeds, organizations, mirrors, or credentials** in
  the generic suite.
- **Not a format specification.** This vision does not pin lock, manifest,
  receipt, or attestation schemas, environment layouts, cache locations, or
  command surfaces.

## See Also

- Parent vision: [Plugin Service Model](../README.md)
- Sibling: [Marketplace Installation Cells](../installation-cells/README.md)
- Related: [machine-fleet *unified-coordination-product*](../../machine-fleet/README.md#unified-coordination-product)
- Child visions: none (leaf)
- Reality docs: [`docs/install-contract.md`](../../../docs/install-contract.md) ·
  [`docs/pipelines.md`](../../../docs/pipelines.md)
- Patterns:
  [`durable-vs-versioned-runtime`](../../../docs/patterns/durable-vs-versioned-runtime.md) ·
  [`uniform-runtime-resolution`](../../../docs/patterns/uniform-runtime-resolution.md) ·
  [`standalone-service-layout`](../../../docs/patterns/standalone-service-layout.md)

## Provenance

- **2026-10-12** — Authored to give the existing builder, promotion, and
  attestation identity one verified consumer path, so that no entry point,
  cache, or seed becomes a parallel provenance record or trust root, and so
  that a governed source lagging a public pin yields an explicit deferred
  outcome rather than a public fallback.
