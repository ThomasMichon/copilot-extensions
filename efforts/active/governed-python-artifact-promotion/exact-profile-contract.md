# Exact Runtime Profile Selection Contract

[Artifact effort and completion gate](README.md). Reviewed contribution from
[#6234](https://github.com/ThomasMichon/copilot-extensions/issues/6234);
engine application remains
[#6737](https://github.com/ThomasMichon/copilot-extensions/issues/6737).

**Status: Design approved in #6795; inert record implemented in #7044.**
Release approval adapter implemented in #8568; installation-owned policy intent
in #8494. Their authority/source composition, real profile publication, consumer
admission, deployed engine selection and production rollout remain open.

## Reconciliation and existing boundaries

This realizes the reviewed Runtime Assembly vision's release lock,
`governed-client-source-fallback` and `no-parallel-provenance` boundaries.
It retains the installation-cell, immutable-generation and one-authority-per-
lifecycle-decision invariants. It adds no daemon, credential provider, public
runtime-feed exception or second promotion/signature system.

The existing `tools/build_python_artifacts.py` schema-3 manifest binds
first-party wheel digests, actual payload bytes, wheel tags and the exact
build-toolchain provenance to `artifact_id`. Its `build_toolchain` is **not**
the consumer's runtime closure. Do not use its seasoned-public tooling
exception, universal first-party wheel tags or package versions as proof that
a particular runtime dependency profile is approved, available or executable.

## Authority and approval

The **installation authority** is the existing actor selected by the
installation's desired state: the operator for an explicit standalone install,
or the already-authorized controller for a managed installation. Both reach
the same selector and construction boundary. Copilot discovery, the running
engine, artifact admission and incidental environment variables do not become
selection authorities.

A release producer supplies candidate runtime locks through the existing
verified release channel. An adopter may instead supply an approved lock
through its already-trusted installation configuration. Local configuration
selects which existing source/actor may approve profiles for that installation.
An arbitrary JSON file, its self-declared authority or a matching digest does
not establish approval. The selector obtains the approved profile from that
trusted channel/configuration; it does not accept an untrusted caller's
`approved: true` flag.

An adopting harness may supply a complete reviewed baseline through its trusted
configuration, so ordinary users need no personal approval file or hand-built
approval map. The existing installation authority applies that policy:
prefer authentic release-approved profiles, and automatically select permitted
source mode for allowed availability/admission failures. This is explicit
authority-owned mode selection, not an admission-layer retry or a new signing
authority. Optional overlays may narrow permissions or deliberately select
capability; they cannot weaken existing acquisition restrictions. The bounded
[zero-configuration plan](../lean-runtime-dependencies/zero-configuration-defaults.md)
owns the adapter/consumer amendment before enabling it.

Direct execution of that trusted setup flow by a user or an authorized agent
is the installation authority's approval of the package dependencies required
by the selected harness configuration, including the installer and selected
plugins. The component installers inherit that scoped operation; they do not
seek a separate approver for each dependency or exact lock. The authority
adapter may bind the resulting complete source-mode lock to that invocation
after independently checking the selected source, declared constraints and
existing acquisition policy. No prior list of exact dependency hashes is
required from the operator for source resolution.

This operation authorization is distinct from authentication of a
producer-supplied release profile and from admission of its actual wheel bytes.
The invocation does not authorize arbitrary packages, configuration outside its
selection, a public-feed escape or bypass of integrity, provenance, seasoning
or revocation checks. Reading configuration, discovering a candidate or
presenting a digest is not evidence that this setup operation was invoked.
The adapter must compose with the existing installation/custody owner rather
than accepting a caller's self-declared approval flag.

Public profiles contain only dependency identities/hashes and portable target
constraints, never private feed identities, credentials or third-party bytes.
Private feed-compatibility evidence remains adopter-owned. Public approval
cannot certify that a private mirror carries a wheel or permits its acquisition.

## Closed input and selected result

The inert implementation versions a single profile record, validated before
construction. No runtime loader or new CLI is introduced by this slice.

| Record component | Required meaning |
|------------------|------------------|
| Selected release | Existing verified first-party source release identity; artifact mode additionally binds the selected schema-3 manifest digest and `artifact_id` |
| Capability | Explicit owning component and required extras: index engine, CPU or an explicitly requested accelerator family; never inferred from an index URL |
| Target | Actual Python implementation/version/ABI, OS/architecture and compatible device/backend constraints; first-party `py3-none-any` cannot replace these |
| Exact closure | Every applicable first- and third-party distribution, exact version, required extras, evaluated markers, admitted wheel filename/tags and SHA-256 |
| Approval origin | Reference to the existing trusted release or adopter approval source; no self-supplied proof or new signing authority |
| Acquisition policy | References to adopter-approved source policy and applicable seasoning/revocation rules; no embedded credentials or public-index fallback |

The lock identity is a content digest of the canonical versioned record.
Identity comparison proves unchanged input, not authority. Approval must cover
the **whole** exact record, including its release, capability and target.
Unknown versions, malformed records, conflicting/duplicate normalized
distribution identities and incomplete applicable closures are explicit errors.
Required extras cannot disappear when a dependency is replaced by a wheel URI.

The selector returns one selected immutable record and its approved-source
reference, or an explicit unavailable/refused outcome. It does not enumerate
arbitrary historical versions, silently resolve another closure, install first
and swap Torch, or retry a different backend after a failed requested profile.
When several approved profiles match, desired state selects their explicit
identity or preference order; unresolved ambiguity is refused rather than
dependent on discovery order.

For the engine, a new standalone installation requests CPU unless the operator
or its existing controller explicitly selects an accelerator profile
_(agent-recommended default reviewed in #6795)_. An update
retains the persisted capability selection; an existing unprofiled engine needs
an explicit migration selection rather than guessing from installed Torch.
Device eligibility is checked before download/construction. A missing requested
accelerator or approved CPU closure leaves the existing engine intact and does
not block installation of the lightweight client.

## Feed evidence and distinct assembly modes

After authority selection, the existing admission owner checks that the
**exact** selected wheels are permitted and available through approved sources.
Evidence includes actual wheel identity/hash/metadata and the effective
seasoning, yanked/withdrawn and redirect-source policy. A trusted index address
alone does not authorize every linked host. Admission never relaxes a selected
pin to accommodate feed lag or changes the selected target.

If artifact discovery or dependency availability prevents artifact-mode
assembly, the same authority may explicitly select the existing verified-source
fallback. It binds the independently verified source release, resolves a
permitted closure within that source's declared constraints through the
approved feed, and freezes its **own exact installation lock before assembly**.
Its receipt records source mode and the actual fallback trigger, not realization
of the unavailable artifact lock. A source-mode lock needs no artifact manifest
or successful publication, so optional artifacts remain genuinely optional.

An artifact closure blocked by package policy may trigger authority-selected
source fallback only when its distinct exact closure satisfies every existing
acquisition, seasoning and revocation rule. It cannot reuse a blocked dependency
or bypass that block. Integrity, source-provenance and unauthorized-source
failures are not availability fallbacks; a blocked fallback closure also refuses.
If neither approved mode can succeed, return the actual cause without activation
or an alternate public feed.
Already-admitted unchanged wheels can be reused without online feed access;
explicit policy change or supplied revocation invalidates that reuse.

## Consumer seam and lifecycle custody

The engine consumer receives the authority-selected record; it does not own
profile approval or feed resolution. The shared installer engine owns
acquisition/metadata validation and immutable construction, composing the
held-construction work in #6598 and runtime-admission work in #6707.
Implement no parallel slot allocator or fallback constructor.

Complete the selected closure before health/completion publication. Persist
mode, lock identity, effective capability/target and existing source/artifact
provenance in the generation's owned receipt. The installation's profile
selection changes only through its existing atomic authority-owned update;
failed construction never commits new selection or rewrites a live generation.
Rollback selects a compatible completed generation under existing engine/state
and cutover ownership. It does not regenerate or edit its historical lock.

## Ordered implementation and acceptance

- [x] Review this design under #4876 before writing the selector or engine
  consumer (#6795).
- [ ] Integrate the existing authority/approval-source adapters before enabling
  any consumer. Support a complete adopting-policy baseline without a personal
  approval file or hand-built approvals map; no new globally trusted profile
  directory is presumed.
- [x] Add the inert versioned record, canonical identity and pure selection
  contract using existing source/manifest validators. Unit fixtures cover
  wrong release, unknown schema, incomplete/duplicate closure, extras/markers,
  wrong ABI/device, unapproved source, ambiguous selection and capability
  retention. Fixtures use synthetic hashes, not released dependency claims
  (#7044); source authentication and inspected-byte validation remain separate.
- [ ] Prove real permitted resolution freezes a complete exact CPU closure
  before assembly, plus a declared accelerator closure where its allowed feed
  is available. Measure compressed downloads separately from installed
  logical/allocated files. No model or deployed-engine mutation is necessary.
  An unavailable accelerator lane is documented, not fabricated.
- [ ] Integrate acquisition/admission through the existing installer owner:
  inspected wheel metadata and hashes, closed exact resolution, unchanged
  offline reuse, bounded acquisition and invocation-owned cleanup. Determine
  measured limits before enabling the consumer; this proposal guesses none.
  The repository-only actual-byte comparator merged in #8399; authenticated
  acquisition, policy evidence, receipts/reuse and constructor integration
  still need their actual consumer proof.
- [ ] Apply the reviewed seam to Windows/POSIX engine constructors under #6737.
  Remove default-install/torch-only-swap; prove first install, retained explicit
  capability, wrong target refusal, source fallback, failed-build retention,
  immutable rollback and concurrent installer exclusion with real consumers.
  Prove a blocked artifact closure can select a distinct admissible source
  closure, while blocked fallback dependencies and integrity/provenance failures
  refuse without altering the prior generation.
- [ ] Validate native supported lanes, including Darwin before claiming new
  parity, and two independent marketplace installations before cross-cell
  reuse. Preserve existing standalone/warm-engine ownership under #5768.
  Publication and explicitly authorized rollout remain the artifact effort's
  existing gates; design approval is not their completion.

## Inert record and pure API

`tools/runtime_profile.py` implements schema
`copilot-extensions.exact-runtime-profile`, version 1. `validate_profile(record)`
returns immutable canonical ASCII JSON bytes and their `sha256:` identity.
Mapping keys are sorted with compact separators; array order remains part of
the approved record. `record()` returns a detached copy, never mutable lock
state. Unknown fields, including self-declared approval flags, refuse.

Each wheel record carries name, exact version, basename, SHA-256,
`requires_python`, `provides_extras`, `selected_extras` and `requires_dist`.
An absent optional Requires-Python header sets `requires_python` to `""`,
not an invented restriction; non-string and untrimmed values refuse.
The graph starts at explicit `roots`, propagates extras through cycles and
evaluates markers against the full recorded target environment. It rejects
missing/incompatible edges, unknown or dropped extras, duplicate normalized
distributions, unreachable packages, direct URL/VCS/path requirements and wrong
wheel/Python tags. These are assertions in an approved record, **not proof of
the actual wheel metadata**; acquisition/admission must inspect the bytes and
compare all those fields before construction.

`select_profile` accepts candidate records and an independent
`approvals` mapping from whole-record digest to trusted approval-source
reference. The calling authority adapter authenticates that mapping using the
existing release/configuration channel; neither this tool nor a record creates
such authority. It requires exact independently supplied release, target,
component, roots and mode. Backend intent is explicit, retained on update, or
CPU for a fresh install. Existing unprofiled installations require an explicit
migration choice. Multiple matching locks need a digest preference order; a
missing requested choice refuses instead of taking an unlisted match.

Target facts include actual interpreter compatibility tags, a complete marker
environment and an opaque device constraint. References identify existing
approval/acquisition policy without embedding URLs, paths or credentials.
Artifact mode additionally binds artifact/manifest digests; independently
selected source mode requires no artifact identity. There is no automatic
fallback, resolver, download, signature verifier, profile directory, installer
adapter, activation or runtime payload fan-out. The record API does not itself
prove device detection, feed compatibility, approval origin or source
provenance. Those remain prerequisite owner integrations, not success claims.

### Release approval adapter prerequisite

`tools/runtime_profile_release.py` extends the existing promotion index and its
pinned hosted Main attestation verifier, not installation-mode or deployment
publication permission. Its optional versioned `runtime_profiles` extension is
validated against the exact first-party manifest before signing. The adapter
authenticates retained subject bytes before deriving whole-record approvals and
matches independently selected release, manifest, capability and target facts.
It exposes no raw approval map as user configuration.

This is only the release-channel adapter. Real profile generation/publication,
composition with #8494's existing `resolve_runtime_profile_policy` baseline,
governed acquisition and independent
source-mode selection/locking remain open. No installer or engine is enabled,
and the authority-integration Plan item above is not complete.

`tools/runtime_profile_policy.py` composes that existing policy resolver with
release authentication. It freezes the resolved owner/policy intent, uses its
backend selection, and prefers a matching authenticated release profile.
`ProfileUnavailable` distinguishes an absent matching/preferred approved record
from malformed, ambiguous or unapproved input. Lock preference validation
precedes every availability-dependent path. Only explicit discovery absence
or that typed absence can request a policy-permitted source fallback. A supplied
index still authenticates if no artifact was discovered; I/O, integrity,
provenance and cancellation failures propagate rather than requesting fallback.
This repository-only plan does not authenticate source bytes, freeze the separate
source lock, admit acquisition or grant publication permission. Source-mode
construction and production enablement remain open.

### Actual-byte verification prerequisite

`tools/runtime_profile_wheels.py` compares that exact record with a complete
retained wheel set using the existing promotion wheel inspector. Archive
digests, actual dependency/Python/extra metadata, wheel identity/tags and
RECORD content must agree. Explicit total archive/unpacked byte budgets and
per-metadata-file limits refuse before an oversized member is read.
No wheel is extracted or executed. First-party promotion retains its separate
locked-generator check; an arbitrary runtime wheel's generator is not trust.

The verifier leaves binary streams and their immutable custody with the caller.
It produces no approval or reusable admission receipt. This is one acquisition/
admission prerequisite, not completion of that multi-part Plan item: trusted
policy/source adapters, actual governed acquisition/seasoning/revocation,
measured production budgets, independent target/device proof, real CPU and
accelerator closures, offline construction and native consumers remain open.
