# Runtime Assembly & Artifact Admission — Vision

- **Subject:** How a host acquires, verifies, and assembles the immutable
  runtime generations that a selected release or standalone installation
  needs, from an approved artifact source, under one provenance identity.
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-12
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
  input to assembly, not something a host re-resolves.
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

### one-verified-acquisition-path

All entry points share one lookup, verification, and assembly path:
interactive install, unattended update, session-triggered detached update,
repair, cache or seed miss, filling a missing artifact, and container image
construction. Each verifies the content identity of every first-party
artifact and every third-party dependency against the release lock before it
contributes to a generation. A cache, seed, or accelerant speeds up
acquisition. It is never a separate trust root. While another approved
source for the same artifact is reachable, losing it changes only how long
acquisition takes; when none is, the outcome is deferred (see
*deferred-not-degraded*), never a different or unverified result. If an
update leaves the dependency set unchanged, assembly reuses artifacts already
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
another version.

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

An unavailable approved source, a lagging governed feed, or a missing
artifact produces an explicit deferred or refused outcome and leaves the last
healthy generation in service. It never produces a half-assembled runtime, an
unverified substitute, or a silently downgraded capability.

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
  ([`docs/pipelines.md`](../../../docs/pipelines.md)). This vision covers
  their consumption only.
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
