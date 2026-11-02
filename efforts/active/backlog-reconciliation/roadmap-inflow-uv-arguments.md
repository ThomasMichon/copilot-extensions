*The following contribution was assisted using Copilot.*

# Stabilization Roadmap: Native UV Argument Compatibility

Parent: [Backlog Reconciliation](README.md), Phase 2.
Related: [installation-owner boundaries](roadmap-inflow-late-owner.md) and
[cold-fixture assessments](roadmap-inflow-fixtures.md).

**Status:** Independently admitted, agent-recommended scoped **P1**
compatibility assessment for #9489, not implementation, technical completion,
an accepted receiving intake or a new source assignment. Source evidence is
fixed at published `664b4ee503ba31ec1c7b385cb3e7d3560bbd92db`.

## Supported source and attributed failure

The qualified original carries an AI-generated/no-human-review disclaimer,
which remains material when weighing its claims. It reports released
construction refusing before candidate activation with
`dependency-seasoning: UV seasoning configuration probe failed`, and a UV
0.10.12 read-only `pip install --show-settings` diagnostic rejecting the
global `--no-editable` option. That exact native/vendor-version behavior was
not independently rerun or audited here; no host, process or recovery state
was inspected.

The fixed source independently confirms six relevant injection sites:
the [shared engine](https://github.com/ThomasMichon/copilot-extensions/blob/664b4ee503ba31ec1c7b385cb3e7d3560bbd92db/libs/installer-engine/installer_engine.py#L330-L378),
the [canonical deployed-policy CLI](https://github.com/ThomasMichon/copilot-extensions/blob/664b4ee503ba31ec1c7b385cb3e7d3560bbd92db/libs/installer-engine/installer_dependency_policy.py#L968-L996),
and paired CodeSpaces
[POSIX](https://github.com/ThomasMichon/copilot-extensions/blob/664b4ee503ba31ec1c7b385cb3e7d3560bbd92db/plugins/agent-codespaces/scripts/install.sh#L545-L558) /
[Windows](https://github.com/ThomasMichon/copilot-extensions/blob/664b4ee503ba31ec1c7b385cb3e7d3560bbd92db/plugins/agent-codespaces/scripts/install.ps1#L789-L813)
and Logger
[POSIX](https://github.com/ThomasMichon/copilot-extensions/blob/664b4ee503ba31ec1c7b385cb3e7d3560bbd92db/plugins/agent-logger/scripts/install.sh#L1326-L1367) /
[Windows](https://github.com/ThomasMichon/copilot-extensions/blob/664b4ee503ba31ec1c7b385cb3e7d3560bbd92db/plugins/agent-logger/scripts/install.ps1#L1405-L1491)
callers. Correcting callers alone leaves the canonical CLI appending the same
option. The [native settings probe](https://github.com/ThomasMichon/copilot-extensions/blob/664b4ee503ba31ec1c7b385cb3e7d3560bbd92db/libs/installer-engine/installer_dependency_config.py#L49-L90)
passes arguments to the real UV operation and refuses a nonzero result.
That refusal is existing policy credit, not a bypass target.

## First existing-owner decision and finite exit

Continue the original claimant's **unsupported-option correction only**.
Reconcile supported native UV argument formation **and** positive
non-editable deployed-source semantics across the canonical helper and named
native callers. Do not prescribe an unverified replacement switch or merely
remove the spelling while leaving editable-source behavior implicit.

The [source-admission logic](https://github.com/ThomasMichon/copilot-extensions/blob/664b4ee503ba31ec1c7b385cb3e7d3560bbd92db/libs/installer-engine/installer_dependency_policy.py#L513-L529)
currently uses the flag/environment semantics when deciding whether
`tool.uv.sources` mappings are editable. Explicit `-e`/`--editable` CLI
refusal already exists, but is not the whole input contract. Preserve the
separate legitimate library development-editable path rather than introducing
a global editable ban. Installed references must not silently point into
disposable retention trees.

Retain immutable non-editable installed source, copied deployment artifacts,
configured feeds, seven-day seasoning of external/build dependencies,
retained-input ownership and existing failure/rollback behavior. Do not ignore
a failed native configuration probe, disable dependency resolution, edit
deployed copies or restart shared services to work around it. Existing source
and policy checks are credited; this bounded study does not independently
audit every rollback path.

The finite acceptance remains the original actual native CLI/retained
installation path **and Windows consumer**, including editable-input refusal
or equivalent safe non-editable behavior and unchanged policy/retention
controls. Canonical helper changes propagate through the existing
[engine generation](https://github.com/ThomasMichon/copilot-extensions/blob/664b4ee503ba31ec1c7b385cb3e7d3560bbd92db/tools/sync-installer-engine.py#L413-L438)
and [dependency-policy fanout](https://github.com/ThomasMichon/copilot-extensions/blob/664b4ee503ba31ec1c7b385cb3e7d3560bbd92db/tools/sync-versioned-runtime.py#L142-L170)
owners; neither generator was run here. This does not add a blanket every-host
matrix, deadline expansion, new scheduler or automatic runtime repair.

## Custody and explicit gaps

The scoped P1 reflects the reported construction/recovery blockage and shared
source exposure, not a measured live-fleet incident or P0. Existing installer,
source/feed, native and publication custodians retain their responsibilities.
Broader update-flow, source-wheel-cache and release-controller work are
explicitly outside this issue.

Twelve complete pinned Git bodies, seventeen exact source proofs and eleven
whole-clause mappings support the bounded assessment. Fresh whole metadata
and the full eleven-member roster were unchanged at terminal qualification.
The exact canonical effort ROOT, corrected candidate, reviewed merge,
containing release, installed/native acceptance and actual current incident
remain unestablished. A bounded home gap is not proof no effort exists or a
generic owner-ACK requirement. No product import, test, collection, build,
UV/native experiment, implementation, repair or rollout occurred.
