# Two-Consumer Helper Closure Evidence

[Effort and completion gate](README.md) /
[shared-runtime decision gate](shared-runtime.md).

## Scope and reproducibility

This is a bounded source/artifact experiment, not a production installer,
artifact trust authority, shared service or rollout. The measured source snapshot
is `89dbda89750237aca7bf6c685ee06df71e9ad855`. Two real plugin closures,
Containers base and Bridge, execute their actual CLI in separate processes
outside the checkout. First-party helpers must resolve inside each measured
environment; editable-source escape is not accepted. The retained probe derives
required first-party module names from each measured source, checks the actual
isolated interpreter's module origins and rejects escaping RECORD paths.
An in-runtime metadata directory alone is not evidence of owned helper code.
Real disposable-venv controls cover an in-runtime `.dist-info` plus `.pth`
that imports external code, and an escaping RECORD entry that must not be
silently omitted. The stronger checks were also applied to the retained
9/30 source environments and the surviving 30-package artifact environment.
The already-retired first artifact consumer was not recreated for that recheck;
the original lifetime operation and measurements remain identified below.

The retained probes require Python 3.11+, uv, the source checkout and disposable
directories. They use configured package acquisition, but do **not** establish
governed-feed admission, seasoning, authority-approved exact locks, signatures or
production artifact provenance. Run only as an explicitly selected source
experiment with the operator's allowed feed and release-age policy configured,
never as a replacement for the supported installer. Feed and uv policy
environment settings are preserved; only project-venv and Python import
overrides are removed.
Evidence checks and retirement guards raise explicit errors even under
optimized Python; `-O` must not turn a rejected run into a passing report.

1. Run `experiments/helper_closure_probe.py --repo <checkout> --uv <uv-path>
   --root <new-source-directory>`.
2. Run the same probe with `--artifacts --reference-report
   <source-directory>/closure-results.json --root <new-artifact-directory>`.
   It builds each first-party library once and preserves each consumer's
   required dependency extras in wheel overrides. `--reuse-wheels <wheelhouse>`
   selects an already-built experimental set; hashes are recorded.
3. Run `experiments/helper_lifetime_probe.py --root <artifact-directory>
   --uv <uv-path>`. This **uninstalls all first-consumer packages and cleans only
   the experiment's explicitly selected cache**. It refuses missing report,
   closure mismatch or redirected top-level paths.
4. Run `python -m unittest discover -s
   efforts/active/lean-runtime-dependencies/experiments -p
   test_helper_closure_probe.py` for measurement controls without installation.

Use separate new directories under an operator-selected disposable task root.
Keep both reports until the result is captured; then remove only those known
experiment directories. No installer, active pointer, daemon, route, lease,
model, corpus or production cache is touched.

## Measurements

Windows file-stream allocation comes from `FileStandardInfo.AllocationSize`,
counted once per `(st_dev, st_ino)`. It is **not** `GetCompressedFileSizeW`
(which returned logical bytes for the tested files). A 4,097-byte control
allocates 8,192 bytes: a second hardlink adds zero allocation, while an independent
copy adds 8,192. Filesystem directory/MFT metadata, the shared base interpreter,
retained installed generations and model/cache data are excluded.

| Measurement | Local-source helpers | Build-once helper wheels |
|-------------|----------------------|-------------------------|
| Containers / Bridge distributions | 9 / 30 | 9 / 30, exact name/version parity |
| Shared identities across runtimes | 23 | 95 |
| Sum of separately counted runtime allocation | 21,075,264 bytes | 21,078,544 bytes |
| Union, shared identities counted once | 20,546,760 bytes | 19,721,512 bytes |
| Difference between sum and union | 528,504 bytes | 1,357,032 bytes |
| Containers / Bridge install phase | 13.48 / 19.07 seconds | 7.66 / 9.82 seconds |

The artifact lane reuses 13 first-party wheels built once. These timing
observations are **not an end-to-end speedup**: serial wheel preconstruction took
about 65 additional seconds, and the two consumers have different closures.
Download compression, build tooling and cache occupancy are separate from the
runtime-union rows. Only file identity sharing within this controlled pair is
established; no fleet-wide reclaim estimate follows from it.

A preliminary override incorrectly removed Bridge's `agent-procutil[metrics]`
extra, producing 29 distributions. That run is rejected. The accepted lane
retains `psutil`, asserts full name/version equality against the 30-distribution
source baseline, and counts all helper files rather than development metadata
that points outside the environment.

## Reference lifetime check

After accepting the artifact lane, the probe removed all nine Containers
distributions and cleaned its isolated uv cache. Bridge still had its identical
30-distribution closure, its installed ZDD module retained SHA-256
`0c7d8a3b0d064709272960685366858d5c9c64b02608db18ba2428f321615336`,
two distinct real ZDD routing-table fixtures returned their own contents, and
the actual Bridge CLI still ran. This establishes file-reference survival for
this bounded uninstall/cache-clean operation, not general installer concurrency,
authorization isolation, crash recovery or two-marketplace compatibility.

## Decision and acceptance boundary

| Candidate | Evidence-led decision | Outstanding owner/acceptance boundary |
|-----------|-----------------------|---------------------------------------|
| Promotion-built immutable helper artifacts, independent venvs/processes | Preferred reuse unit: measured file sharing and reference survival without a new resident process | #4876 retains artifact identity, approved locks/feed admission, verified source fallback, consumer construction and retention; #6697 retains publication follow-through |
| One reusable helper venv, separate processes | Do not introduce it for these consumers: whole closures differ, existing file sharing already saves bytes, and no equivalent repeated construction workload with positive end-to-end benefit was demonstrated | Any later proposal must identify a real shared closure and justify added owner/reference/ABI/cell machinery; this experiment does not approve that mechanism |
| One shared routing-read/ZDD front process | Reject this measured candidate; retain direct routing and independent process/lifecycle ownership | The existing [process probe](shared-runtime.md#bounded-two-consumer-process-probe---2026-10-12) has direct p95 below 0.5 ms versus roughly 18-26 ms over resident RPC; ZDD is a routing table, not a resident front proxy |

The routing-only screen remains p95 no more than twice the same-host direct
baseline; both resident RPC lanes fail. For artifact reuse, an acceptable
consumer implementation must preserve the **entire** admitted closure and
extras, establish standalone first install, retain another consumer after
uninstall/cache retirement, and show positive hardlink-aware savings for a
matching pair. No install-time threshold is accepted from these single runs;
end-to-end claims must include preconstruction and use repeated matched trials.

Before enabling a production artifact fast path, #4876 must bind identity to
verified source/content, platform/architecture/ABI, selected exact dependency
profile and allowed feed/trust scope. Installation-cell ownership and immutable
generation rules still apply. Different marketplace/trust domains cannot reuse
a candidate just because its distribution names/versions match.
Missing/unavailable eligible artifacts use the existing authorized verified-source
path; invalid integrity or blocked dependency policy remains an explicit refusal.
Two concurrent installers, two independent marketplace installations, rollback,
failure recovery, native platform lanes and publication/release attribution
remain that implementation's gates, not passing claims from this experiment.

No shared-runtime implementation or production migration is authorized by this
decision. The effort remains Active until design review and every remaining
Plan/Validation item are resolved or accepted by a named tracked objective.
