# Observed Windows bytecode attribution

Back to [Test Portfolio Rationalization](README.md#finite-observed-windows-bytecode-attribution-from-13598).
P13598/V13598 and [actual global G13598](README.md#validation-plan) retain
the completion states; this canonical detail has no duplicate checkboxes.
Existing Phase 7 migration-intake and domain review apply.

## P13598: Exact source binding

Retain #13598's reported CPython 3.11.9 Windows collection refusal before the
selected tests execute: `not an eligible unit effect: unattributable Python
bytecode`, with nested `__pycache__ not bottom-level directory`. This is a
failed original-profile packet, not a passing native run or an accepted flake.
The reported standard-library comparison computes paths without creating them:
ordinary spelling round-trips, while the extended spelling under the active
relocated `sys.pycache_prefix` raises `ValueError`.

At the existing `UnitRun.audit` source-attribution boundary, preserve an exact,
unambiguous binding between declared source and its bytecode representation,
including supported extended Windows spellings and the active relocated
cache prefix. Retain the actual collection chain through
`test_source_witness.py`, `runtime_source_lock`, `installer_engine` and the
co-located primitive loaded by `versioned_runtime_source._runtime`.
Do not treat equivalent spelling as permission to admit a different source.

Unknown, stale, mismatched and unadmitted bytecode must remain refused.
Do not broadly allow `.pyc` reads, skip the observer, preload production code
outside observation, enlarge the original packet deadline or switch to a
longer Windows-native profile to manufacture acceptance.

## V13598: Original-profile qualification

Through the existing separately authorized bounded lane, qualify genuine
observed Windows collection on CPython 3.11.9 at the original profile and
deadline. Cover ordinary and supported extended spellings with the actual
relocated cache prefix, correct declared-source binding and meaningful
unknown/stale/mismatched/unadmitted negatives. A pure standard-library path
diagnostic is useful causal evidence, not the observed collector's pass.
Record collection, selected-test outcomes, skips and cleanup separately.

The operator-selected separate #13598 tracking from #6237's bounded source-PR
lookup diagnostics remains. The diagnostic change's complete ordinary Linux
CI remains required; its observability work does not fix or waive this
Windows boundary. The existing P13294 collector family retains its own
original versions, inventory and native/actor-free obligations; neither
family's broader packet is silently substituted for this finite selection.

All source, admission, observer, fixture, runner, native, release and resource
owners/holds remain. Planned receiving supplies no import, collection, test,
dependency acquisition, runtime change, native run or resource authorization.
