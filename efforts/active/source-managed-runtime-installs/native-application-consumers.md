# Standard environments and native application consumers

Back to the [canonical effort](README.md).

**Agent-proposed bounded execution plan; review precedes behavior changes.**
Coordination stays on #7030 under #7028. The implementation source handle is
`weathered-diode`; existing construction, artifact/profile, application,
command, cache and historical-migration owners retain their scopes.

## Request and reconciliation

The portable operator direction is:

> Ideally we standardize on modern uv and venv compliant conventions for everything

The bounded request combines that direction with plugin delivery independent of
service installation. This slice addresses native Worktrees consumers of the
released application model, not immediate migration of every application.

It extends the Runtime Assembly vision's
[standard-environment intent](../../../visions/plugin-services/runtime-assembly/README.md#standard-python-environments)
and closes part of the existing
[managed-deployment contract](deployment-policy.md).
The [application support pattern](../../../docs/patterns/fast-application-install-and-cutover.md#common-forward-environment-contract)
owns concrete layout conventions.
[Authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
remains unchanged: consumer readiness checks do not become an old-footprint
veto on explicit incoming installation, and no new installer, supervisor or
source registry is introduced.

## Evidence and scope

A fresh Linux consumer independently installed the ordinary default-head
Worktrees payload and a Git-backed application. Direct installed commands,
imports and authenticated monitor serving worked. Installation mode and the
documented managed-deployment deny policy validated, but the delivered catalog
reported unresolved binding while advertising its direct command as ready.
That exact command's `--help` exited 1 without diagnostics. The aggregate
hook/first-use/end interval also added bytecode inside the selected slot.
Native payload refresh was not reached.

The inspected native resolver uses flat-slot interpreter paths; the application
has a nested project environment. This is an observed integration mismatch,
not a unique established cause of every symptom. An unadopted fixture's lifecycle
warning is not proof of a product registration defect.

Prior art is credited, not replaced: canonical Python `versioned_runtime`
already recognizes nested application environments, Dispatch has physical
flat/nested consumer controls, and the parent effort has an external-bytecode
cache owner. Remaining native adapters must converge on these foundations.
Historical global-command recovery, other app startup/cutover work, construction
and retained-wheel/profile changes are not taken over.

## Plan

- [ ] Land this intent and bounded plan through normal source review before
  changing runtime behavior.
- [ ] Trace the advertised catalog entry, native shell/PowerShell adapters,
  command binding and hook execution against the actual installed generation.
  Establish normal preparation/adoption prerequisites through their owners;
  do not manufacture metadata to silence an unqualified fixture.
- [ ] Reuse canonical physical-environment selection in affected consumers,
  including standard POSIX/Windows interpreter paths and real venv prefix
  behavior. Preserve completion, explicit source-generation selection,
  physical-path and foreign-scope checks; do not flatten or alias the project.
- [ ] Resolve application-owned command/readiness integration through the
  existing installation and command owners. Do not synthesize a legacy
  deployment receipt or infer ownership from a name or package-version match.
  Prefer the existing operation and immutable installed descriptor; if the
  boundary needs expansion, declare it provider-first under the
  [contract-evolution pattern](../../../docs/patterns/service-contract-evolution.md)
  before migrating consumers.
- [ ] Preserve valid managed-deployment denial and supported standalone
  behavior. A healthy selected application remains callable despite payload
  skew; unsupported or missing requirements produce actionable diagnostics,
  not silent exits, runtime reconstruction or whole-hook suppression.
- [ ] Route affected hook/command imports through the existing external-cache
  owner. Preserve caching and normal non-deployment hook duties without new
  slot files, cache exemptions, site-startup hooks or deletion of prior state.
- [ ] Qualify the exact native consumer, then publish source changes through
  review/CI and actual Main promotion. Record released-consumer results and
  remaining adoption scope separately; no host rollout is included.

## Validation plan

- [ ] Focused controls distinguish completed physical nested environments,
  supported flat compatibility, bad/redirected environments, source-qualified
  selection and unchanged fallback meaning. Use real UV-created venvs to check
  `pyvenv.cfg`, `sys.prefix`/`sys.base_prefix`, installed imports and both
  platform interpreter layouts; no activation-policy or symlink privilege
  changes.
- [ ] Exercise the actual command/binding parser boundary across supported
  old/new callers and providers, explicit rollback, missing or incompatible
  metadata, managed refusal and strict unknown-argument rejection. Do not add
  a whole native matrix to the required unit-only packet.
- [ ] A fresh disposable consumer acquires Copilot content through ordinary
  default-head or actual-local-checkout declarations and records what it
  receives, independently of backend Git inputs. Execute the exact delivered
  catalog argv and real hook/first-use entries after canonical preparation.
  A copied snapshot or plugin-directory injection is not plain native evidence.
- [ ] Compare complete runtime files, generations, selectors and redirects
  before/after the relevant consumer interval. Observe positive external
  bytecode creation/reuse; do not prewarm, disable caching or exclude new
  bytecode to manufacture slot equality. Preserve failed attempts separately.
- [ ] Count an actual frontend-update witness only when native refresh delivers
  changed payload input. An initial install or unchanged refresh is not that
  proof. Record unsupported/unavailable inputs explicitly rather than pinning
  the frontend to match its backend.
- [ ] Retain exact guest/process cleanup and honest platform/serving limits.
  Source merge, release, isolated native acceptance and installed-host rollout
  remain different outcomes. This slice does not close #7028, #7030 or every
  application in the contributor matrix.

## Journal

### 2026-11-04 - Bounded proposal

The native consumer failure and standard-environment direction motivate this
reviewed intent slice. Existing nested-environment and cache work is retained.
No runtime source implementation, installed adoption or rollout is claimed.
