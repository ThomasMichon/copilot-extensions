# Closed-wheel downstream Source preparation

Back to [Source-Managed Runtime Installs](README.md) and the
[protected native handoff](protected-native-installation.md).
Tracked within [#13504](https://github.com/ThomasMichon/copilot-extensions/issues/13504);
the broader Manager and provider/profile programs remain with #12905 and #12900.

**Status: source implementation and containing release qualified; consumer
qualification remains open.** `local_install_closed_wheels.prepare` composes the
existing owners without introducing another constructor or changing the
ordinary source-building path. The
[install contract](../../../docs/install-contract.md#explicit-manager-source-install-integration)
records its selected-provider declaration and effect boundary.

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

The explicit artifact-first input path uses the existing canonical
Source/profile and input-retention owners. It preserves the application's
original development metadata while admitting the independently selected
wheel-only runtime graph. The callable API and support declaration belong to
that provider, not to an adopter-specific wrapper.

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

- [x] C1: Review the explicit input contract and identify the existing
  Source/profile, retention, identity, application and lock owners to compose.
- [x] C2: Implement the bounded provider and selected-owner support declaration,
  retaining original development metadata and unchanged ordinary defaults.
- [x] C3: Qualify the actual owning functions and readers with focused inert
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

## Journal

The proposal merged in
[13669](https://github.com/ThomasMichon/copilot-extensions/pull/13669) as
`631f9bbeb4cdba5abdddad9cede06e124411240d` after current-head Copilot approval
and required checks. This completed C1 for the reviewed contract and existing
owner composition. The subsequent implementation and release below complete
C2/C3; C4 consumer adoption, protected P4 and the parent acceptance remain open.

The implementation retains the original manifest rather than projecting a
different one. An explicit `closed-wheels` input mode spans the canonical
freezer, retained Source readers and numbered constructor. Existing wheel,
source-member, profile/target, application-emission and finite process owners
produce the associated application and actual offline UV lock. Original artifact
provenance is distinct from the Source/runtime identity. Missing/foreign wheel
bytes, unsupported input modes, changed retained associations, occupied output
and partial/unknown lock outcomes refuse without construction or replay.
One independent application is supported; nonempty `application-sources`
requires its separate owning preparation and is explicitly refused.
The installed owner expands `closed-wheel-source/v1` before any consumer
migration; no old-provider fallback or native adoption is claimed.

Initial focused source validation passed 25 controls, strict Linux/Windows typing,
the existing install contract, documentation consistency, generated-engine
parity and changed-module size gates. The package-graph helper moved into the
existing retained-source owner to stay within the unchanged module-size cap;
the original engine import remains available. Required PR review/checks and a
containing release remain C3, and actual consumer adoption remains C4.

The implementation review identified two preservation gaps. Closed-wheel
freezing and retained construction hashing now keep all admitted application
files, including development artifacts under `build/` and `dist/`; ordinary
source exclusions remain unchanged. Emission canonicalizes the distribution
key while explicitly preserving the original primary-command choice.
The expanded packet passes 29 source controls plus 152 existing admitted units,
with both platform type checks and the unchanged contract/sync/size gates.
Static data admission retains canonical Git/LF bytes, not local newline
conversion. Required current-head review/CI and containing release remain open.

The next review correction completes the lazy lifecycle-helper closure in the
runtime-input-provider layout. Both packaged layouts now execute actual
synthetic preparation without checkout imports, not merely module loading.
The existing static source/wheel validator also admits explicit flat package
discovery with include selectors, preserving original metadata and exact member
binding. The expanded packet passes 186 controls, both platform type checks and
the unchanged contract/sync/size gates; required review/CI still applies.

Implementation [13673](https://github.com/ThomasMichon/copilot-extensions/pull/13673)
merged as `17078071201bd1f0ee122a8729194c4c87268925` after current-head Copilot
approval and passing required/observed functional checks. Automatic producing
run [44734566478](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44734566478)
admitted that Dev source and passed unit coverage, native Manager/Windows
Resume, artifact construction/attestation, immutable candidate-unit reuse,
publication/readback and normal exact-candidate merge. Promotion
[13682](https://github.com/ThomasMichon/copilot-extensions/pull/13682) landed
Main `91b633a4374f1fc2cff691978796da87ce5dd169`.
Nonexecuting Main readback confirms all nine canonical provider/asset files,
the selected support declaration and both materialized packaged-layout
provider/lifecycle-helper pairs. This closes C3's source/release scope,
not actual consumer preparation, construction, native protection or readiness.
C4 is next through independently admitted consumer inputs and effect authority;
the broader protected handoff and installation programs remain open.

### Consumer-driven source corrections

The protected-dispatch correction
[13735](https://github.com/ThomasMichon/copilot-extensions/pull/13735) merged
as `e9de67edd5fec67c882c8c74e0e40bcbef821dae` after fresh current-head Copilot
approval and passing required checks. It carries the selected closed-wheel
mode into the existing package-containment guard; ordinary discovery and
snapshot-escape refusal remain unchanged. The narrowed source packet passed
30 explicit controls and retained exact canonical provider copies.

Merged-source CI passed. Automatic containing validation
[44821705097](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44821705097)
then passed units/coverage, artifact construction and Windows Resume but
correctly refused the native Manager TTY consumer before its quit dialog.
The existing automatic tracker is
[13744](https://github.com/ThomasMichon/copilot-extensions/issues/13744).
That run did not publish a qualified containing release; this bounded provider
slice does not take over Manager readiness or dispatch another native attempt.

The separate optional-association correction
[13743](https://github.com/ThomasMichon/copilot-extensions/pull/13743) merged
as `a8c51397a8256104df1560c7b088f7f50460a2a8` after fresh current-head Copilot
approval, no active findings and successful required/functional CI.
It replaces the initial blanket association refusal with the existing
root/extra/target traversal over the inspected exact profile.
Unselected optional associations stay inert; selected missing or unassociated
inputs still refuse before prepared snapshot/application emission.
All 28 closed-wheel controls, strict typing and static gates pass.
The ordinary Manager changefile carries this tooling correction.

Containing release and actual consumer/native adoption remain separate
unqualified gates. C4, protected P4 and the parent installation acceptance
remain open; neither source correction authorizes installation or deployment.
