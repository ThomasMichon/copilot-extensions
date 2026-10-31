# Wheel-first environment construction — measured report

Back to the [canonical effort](README.md) and
[transition proposal](wheel-first-transition.md).

This is one environment-only paired experiment, not a released installer,
application-readiness test, rollout result or statistically isolated study.

## Inputs and method

- 21 base owners, including the separate indexing engine; 44 unique first-party
  distributions in their required closure.
- Local experimental source marker:
  `81248be2ddc3932acf22d138799b285a396fa244`.
- Canonical Main version seed:
  `cfa2e8dbfb7d20b998f5da6bd77381f5e95b4729`.
- These are reproducibility associations, not a claim that the prototype was
  merged/released or that a public link to its local code exists.
- Canonical release version seeding/changefile handling preceded source and
  wheel locking/building. uv generated real locks; no lock text or wheel metadata
  was rewritten to satisfy constraints.
- A generic Windows container with an already-present image/toolchain used uv
  0.13.6. Initial Python was 3.12.10; actual runtime probes in both variants used
  3.11.18. Interpreter acquisition/discovery was not separately profiled.
- Order: source-1, wheel-1, source-2, wheel-2. Source and wheel runtime caches
  started separately empty; promotion cache was separate. Each second pass
  deleted exactly its 21 owned version-slot leaves, preserved its cache and
  toolchain, recopied the same projects, and initialized absent environments.
- Both variants used the same configured package index and base closure.
  Network, upstream caches, OS caches and toolchain effects were not independently
  isolated; source-first ordering can affect later observations.
- Probes compiled installed Python and read distribution metadata only.
  No application import/setup/startup, models, optional native profiles or Linux
  execution were qualified. Linux evidence was locked dry-run resolution.
- Independent Git checkout/relocation removed original input paths; 42 genuine
  uv locks and zero wheel binaries were tracked in the portable metadata fixture.
  Associated artifacts were local; GitHub release download was not tested.

## Size and preparation costs

| Measurement | Result |
|---|---:|
| Unique first-party wheels | 44 |
| Compressed wheel bytes | 10,910,503 |
| Uncompressed wheel member bytes | 37,263,380 |
| Per-consumer duplicated wheel closure bytes | 24,883,639 |
| Source copied-project bytes, summed across owners | 491,668,072 |
| Wheel copied-project bytes, summed across owners | 27,023,254 |
| Existing local source-input wheel bytes | 57,885 |
| Aggregate first-party wheel-build time | 78.516 s |
| Wheel-promotion phase, including those builds | 96.266 s |
| Whole dual-representation materialization | 330.063 s |
| In-container source staging | 14.453 s |

Copied-project size includes each consumer's duplicated closure and metadata,
before environment initialization. Copy timing includes artifact acquisition.
Registry dependency bytes and outer host transport time were not instrumented.
All observed first-party tags were `py3-none-any`; this does not promise that
future native artifacts are universally compatible.

## Fresh-environment results

| Variant/pass | Copy | uv init + probe | Qualified/fresh/hardlinked | Local first-party build events |
|---|---:|---:|---:|---:|
| Source 1 | 46.015 s | 179.359 s | 21/21 | 173 |
| Wheel 1 | 0.328 s | 25.375 s | 21/21 | 0 |
| Source 2 | 46.546 s | 163.812 s | 21/21 | 172 |
| Wheel 2 | 0.300 s | 14.797 s | 21/21 | 0 |

Cold source-to-wheel initialization improved **7.068x**, or **85.85% less
time**. Copy plus initialization improved **8.768x**: 225.374 s to 25.703 s.
Median per-project cold initialization speedup was 16.211x, with no cold A/B
regressions. The engine was an important outlier: 19.125 s source versus
15.875 s wheel, only 1.205x, reflecting work beyond first-party packaging.

After exact-slot deletion/re-copy, wheel initialization improved from 25.375 s
to 14.797 s (**41.69% less**, 1.715x). Its median per-project rebuild speedup was
1.124x; three small per-project regressions remained. All 21 root cached wheel
artifacts retained native file identity and were reused.

Source rebuild initialization improved only 8.67% in aggregate; eight
per-project regressions remained and no root artifact identity was reused.
Repeated local build events were observed, but their precise invalidation cause
was not independently established. That is not proof every source cache missed.

Both wheel passes matched installed source distribution/version sets for all
21 owners. All 84 fresh environments qualified and were hardlinked; final
source and wheel Linux dry-runs resolved for all 21 each. No nonlocal registry
build lines were observed in the measured runtime logs. Existing-environment
warm probes were separate postchecks, not the fresh-slot cache evidence.

## Lessons and limits

Pure-Python wheels still remove runtime build-backend/metadata work, provided
the complete first-party closure is prebuilt. They do not automatically bundle
registry dependencies, eliminate environment installation or prove service
readiness.

Earlier attempts exposed two distinct issues: temporary advertised metadata
unavailability at the configured package index, and omitted canonical release
version finalization that made source-path overrides appear compatible while
real wheel constraints failed. Preserving native promotion history and canonical
version composition resolved the prototype's composition omission; this is
not a reason to weaken dependency, integrity or feed policy.

The next acceptance gate is the real install/source-change/reinstall/cutover
and recovery matrix in the [transition proposal](wheel-first-transition.md),
not repetition of this construction benchmark alone.
