# Network acquisition and offline source-wheel work

Parent contract: [Cached Source Wheel Assembly](README.md), #9290.

This is a proposed, **agent-recommended implementation slice**, not a change to
installed runtimes or permission to replay an interrupted update.

## Request

> We need to separate the parts that do download from the parts that are CPU and local-IO only.

## Problem and scope

The ordinary `retained_source_wheels` path currently shares one constructor
child deadline across build-tool provisioning, source copying, offline wheel
builds, archive inspection and repeated source/build-tool verification.
Requirement-group provisioning is interleaved with per-project construction.
An expiry while hashing a local tool tree can therefore be reported through
the generic wheel-inspection deadline error.

The first adopting boundary is this ordinary source-wheel path in the shared
installer engine. Keep its network-capable work separate from offline work,
with independent phase accounting and precise failure attribution. Do not
describe subsequent runtime dependency installation, unsupported isolated-build
fallbacks or the complete update as offline merely because this boundary is.

The existing cold-copy owner retains `_tree` and `_compact` batching.
This slice owns phase orchestration, not a competing copy optimization.
Existing dependency-admission, source-retention and release-lock owners retain
their contracts.

## Execution boundaries

### Local preparation

Determine the supported source projects and distinct declared build-requirement
groups from the retained input graph. Resolve existing tool/cache locations
without downloading, changing a selected generation or executing a build hook.
An unsupported graph keeps its existing explicit fallback classification.

### Network-capable build-tool acquisition

Provision every required build-tool group before the first offline source build.
Use the existing retained UV installation and dependency-source policy rather
than another downloader, resolver, acquisition receipt or approval authority.
Each group retains its own original requirement set and installed environment;
do not union unlike requirements.

This boundary may include local environment setup as well as approved-source
resolution and downloads. Its label is explicitly network-capable, not a claim
that all elapsed time is network transfer. Record its own duration and outcome.

### Offline build and verification

After acquisition finishes, perform source/tool identity reads, source copying,
backend requirement inspection, wheel construction, archive verification and
cache publication as local work. No dependency installation or later group's
acquisition is hidden inside that loop. Build commands retain explicit offline
selection and the retained build configuration.

Preserve all existing source, build-tool, archive, mode, size, file-count and
custody checks. A successful download is not source or artifact admission.
Completing an offline build is not installed health or activation.

## Clocks and diagnostics

The existing configured constructor child allowance supplies the initial
process allowance for each named phase; it is not one shared acquisition-plus-
assembly stopwatch. Acquisition time must not consume the offline phase's
allowance. This changes the phase boundary, not the numerical defaults or any
outer request's separately selected lifetime.

Report phase start, completion/failure and monotonic elapsed time through the
existing stderr/progress surface without corrupting machine-readable results
or exposing feed credentials. Distinguish acquisition, offline build and
verification failures. Do not call a local source/tool-tree deadline a wheel
archive inspection timeout or silently convert integrity refusal into a cache
miss.

Preserve the shared progress/communication versus process-timeout contract.
Phase reporting does not manufacture target keepalives, authorize uncertain
replay, extend an outer deadline or replace owned-child cancellation.

## Acceptance

- [ ] All distinct build-tool groups are acquired before any offline project
  build; each retains independent dependency selection.
- [ ] Controlled acquisition work can consume its own allowance without
  shortening the local phase's allowance; local expiry remains bounded and
  identifies the actual phase.
- [ ] A real retained-source consumer completes offline wheel building and
  verification after the fixture feed becomes unavailable, with no hidden
  acquisition in that boundary.
- [ ] Existing cache-hit, source-edit invalidation, backend mutation, corrupt
  archive and interrupted-publication controls remain effective.
- [ ] Phase progress, failure attribution and final result framing are covered.
- [ ] Ordinary source installation and its unsupported fallback retain truthful
  network-capability classification; this slice makes no whole-update latency
  or deployment claim.
- [ ] Shared consumer materialization, source admission, focused native/POSIX
  qualification and required publication gates pass without weakening checks.

## Delivery boundaries

Review this contract before constructor edits. Coordinate the function boundary
with the current cache/cold-copy owner; preserve independent source and tool
revalidation rather than trading it away for timing. No host update, queue
release, cache purge or service restart follows from proposal or source merge.

Return to the [parent effort](README.md) for broader artifact consumption,
portfolio adoption, actual update latency and rollout ownership.
