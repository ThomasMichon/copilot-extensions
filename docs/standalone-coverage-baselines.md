# Native standalone coverage baselines

Fleet Manager's baseline/full promotion lane earns coverage with the same
installed-package preparation, environment lease and native host containment
as `tools/run-standalone-tests.py`. It does not change that runner or activate
per-PR selection. The existing coverage engine's context driver and schema-v3
test-to-line attribution/duration data are reused.

From a checkout at the exact revision being measured:

```powershell
python tools/coverage_guided_selection/standalone.py fleet-manager --prepare --admission-wait 60 --timeout 720 --measured-commit <full-HEAD-SHA> --out fleet-manager-windows.json
```

Linux uses the same command with `--out fleet-manager-linux.json`. These are
deliberate **full** validation runs: environment and project pytest selection
overrides are removed; incomplete, deselected, failed, empty or inconsistent
universes cannot produce a baseline. Instrumentation dependencies are provisioned
through the existing `uv` configuration; no alternate feed is selected.
The dedicated full lane allows 720 seconds for instrumented execution inside its
15-minute job budget; ordinary required CI budgets are unchanged.

## Source equivalence and provenance

Before and after collection, installed Python modules must match the canonical
checkout byte-for-byte and have the exact expected inventory. The wheel's
generated `_versioned_runtime.py` maps to its canonical library, not a nonexistent
source mirror. Only verified installed paths receive repository line coordinates.
Source/build/test inputs must be clean at the named `HEAD`.

The `collection` record includes native platform, Python version, source/test-input
hashes, collector hashes, complete test-universe digest, test markers and pytest summary. It
contains no installation paths or machine identity. Test node IDs remain relative
to the component root; covered file keys are repository-relative POSIX paths.

An exclusively leased venv `.pth` hook starts coverage in native child interpreters,
including `python -I`. Each test passes a distinct context configuration to its
children so late startup cannot misattribute a child to the next test. Child data
uses digest contexts (not config-interpolated node IDs) and is combined with parent
attribution. This covers interpreters using that venv
and inherited configuration, **not** arbitrary unrelated interpreters, newly
created environments or children deliberately scrubbing coverage configuration.
The hook and temporary instrumentation files are removed before releasing the lease,
including on collection failure.

Windows uses digest-named `tmp_path` directories beneath a short checkout-local
sandbox to preserve native artifact path headroom. Extended/UNC prefixes are not
used: those prefixes change SQLite's file-URI semantics. Other collectors and
platforms retain their ordinary pytest temporary-path behavior.

## Publication and the bootstrap gate

`validate-and-promote.yml` earns separate Linux and Windows evidence under
`fleet-manager-linux` and `fleet-manager-windows`. Both jobs must succeed and both
artifacts must exist before promotion. Artifact names retain the existing
`coverage-baseline-` prefix; JSON filenames match the embedded baseline identity.
The existing publisher writes the small main-history pointers and publishes their
full JSON maps as Release assets at `coverage-baselines-<measured-dev-SHA>`.

This is **stage 1**. A locally produced JSON is validation evidence, not an admitted
production baseline. Stage 2 may activate strict required selection only after a
real promotion has published both assets and qualifying main-history pointers.
It must resolve the appropriate platform identity, verify freshness and attribution,
and fail visibly on unavailable/untrusted evidence rather than run a full suite
or treat a null decision as a successful empty run. Existing shadow consumers
remain unchanged.
