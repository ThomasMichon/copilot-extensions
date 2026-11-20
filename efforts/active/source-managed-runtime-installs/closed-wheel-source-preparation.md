# Closed-wheel downstream Source preparation

Back to [Source-Managed Runtime Installs](README.md) and the
[protected native handoff](protected-native-installation.md).
Tracked within [#13504](https://github.com/ThomasMichon/copilot-extensions/issues/13504);
the broader Manager and provider/profile programs remain with #12905 and #12900.

**Status: proposal; no new preparation API or native qualification is claimed.**
This source prerequisite supports capable-consumer adoption without introducing
another constructor or changing the ordinary source-building path.

## Gap and intent

An independent Python application can have a valid development-only
`tool.uv.sources` graph while its deployment uses an independently admitted,
complete wheel closure. The existing prepared Source/profile path freezes the
development package graph before emitting the wheel-first application.
Local wheel overrides can therefore refuse that preparation even when the
caller already possesses the intended runtime artifacts.

Deleting the development overrides, changing their versions or hand-authoring
a prepared snapshot is not equivalent to preparing the original application.
Moving the overrides into `uv.toml` is not a supported UV alternative:
dependency sources belong to the project's `pyproject.toml`.
Component archive/profile inspection helpers alone are not a supported
downstream preparation entry.

Add an explicit artifact-first input path through the existing canonical
Source/profile and input-retention owners. It must preserve the application's
original development metadata while admitting the independently selected
wheel-only runtime graph. The exact callable API and support declaration belong
to the implementation, not to an adopter-specific wrapper.

This closes the installer's
[independent installation intent](../../../visions/installer/README.md#purpose--intent)
and supports
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement).
Follow the existing [architecture invariants](../../../docs/patterns/README.md#design-invariants-binding-contracts)
and [provider-first contract evolution](../../../docs/patterns/service-contract-evolution.md).
No new vision, supervisor, acquisition service or installation architecture
is proposed.

## Required owner contract

1. **Independently selected incoming inputs.** Consume one admitted immutable
   downstream project, its associated already-built first-party wheel and the
   selected complete runtime closure. Source/feed approval remains with the
   caller. Package equality, self-described hashes or an approval boolean do
   not substitute for that authority.
2. **Original source and explicit runtime graph.** Extend the existing source
   retention/association owner to distinguish development inputs from the
   explicitly selected closed-wheel runtime. Retain the original source bytes
   and their first-party artifact association. Do not silently drop troublesome
   metadata, stop performing source admission or merely bypass package discovery.
   The ordinary source-building path retains its existing local-artifact and
   dependency-age policy.
3. **Complete closure before construction.** Reuse the actual archive,
   dependency, marker, extras, target and exact-closure validators. Source,
   selected local packages, wheel bytes and runtime roots must agree through
   the existing construction bindings. A selected SDK or companion package is
   an explicit runtime input, not an inferred substitution for an unselected
   development extra.
4. **Canonical identity and emission.** Let the existing owners compute the
   real Source, assembly, profile and runtime identities. Preserve the
   artifact's original source/build provenance separately. Compose the existing
   application emitter and actual UV lock producer; generated locks and Source
   records must not be handwritten or relabeled from an older selection.
5. **Preparation, not installation.** This path performs no first-party
   backend build, dependency acquisition, tooling/application environment
   construction, selector/command publication, setup/start callback or application
   invocation. It uses explicitly prepared tooling and the existing process,
   deadline and retained-input lifetime. A missing prerequisite refuses rather
   than provisioning or switching to an ambient tool.
6. **Finite outcomes and compatibility.** Preserve occupied-output refusal,
   partial state, accepted/unknown child outcomes and existing settlement
   ownership. Required support belongs to the actual selected provider, not a
   package-version comparison or newer staged payload. Missing or withdrawn
   support refuses the affected operation before effects, without trying a new
   form and replaying a legacy one.

Keep incoming source admission separate from construction/setup. Reuse the
existing retained evidence within its valid operation lifetime rather than
adding repeated fingerprint or attestation rejection gates.
No incumbent provenance, recovery capability or stale transaction may become
a new manual-update veto.

## Plan

- [ ] C1: Review the explicit input contract and identify the existing
  Source/profile, retention, identity, application and lock owners to compose.
- [ ] C2: Implement the bounded provider and selected-owner support declaration,
  retaining original development metadata and unchanged ordinary defaults.
- [ ] C3: Qualify the actual owning functions and readers with focused inert
  controls; complete normal review, required CI, merge and containing release.
- [ ] C4: Migrate a capable consumer through the released entry, recording
  preparation, construction, native protection and readiness separately.

These are a preparation sub-slice of protected-handoff consumer adoption,
not replacements for that parent's P4 or the main effort's completion gates.

## Validation plan

- Exercise the real owning preparation functions and existing Source/profile
  readers with a synthetic downstream project, independently associated wheel
  and complete closed runtime inputs. Verify original metadata retention and
  the actual generated application/lock association.
- Refuse changed or missing source/wheels, a foreign component, incomplete
  dependencies, conflicting selected extras or native target, an unassociated
  first-party artifact and an invented runtime identity before construction.
- Preserve the ordinary source-building path and its local-wheel release-age
  refusal. Explicit wheel-only consumption must not become a backend-build,
  resolver, dependency-download or tooling-environment fallback.
- Test old caller/new provider, required new contract/old or withdrawn provider,
  new/new behavior and supported rollback through the real selection boundary.
  Keep strict parser rejection and truthful unsupported outcomes.
- Cover collision non-replay, retained partial output, mutation during retained
  use, deadline/cancellation and independently settled owned processes.
  Do not reinterpret a failed child as proof of no effects.
- Keep focused actor-free CI controls separate from contained filesystem/process
  qualification and any native adoption. Add no exhaustive effectful matrix to
  the required unit packet.

## Acceptance boundary

A reviewed proposal is not an implemented entry; source tests are not a prepared
application; a prepared application is not native protection or installed
readiness. Actual execution retains the caller's separate authority over
files, tooling, acquisition and construction. This proposal grants none.

The existing protected native owner still independently admits its bootstrap,
policy, source, interpreter/toolchain and addressed installation.
Provider/profile persistence and the Manager reference remain separately owned.
Installation success remains incoming readiness and complete current selection,
with preserved user state and independently scoped outgoing retirement.
