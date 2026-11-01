# Incremental rollout

Canonical contract and completion gate: [README](README.md).
The mechanisms and batching below are **agent-recommended**, not additional
operator-granted deployment authority.

## Terminal bar and migration states

Every tracked first-party `.py` path is classified from the first foundation PR,
including tests, scripts, tooling, fragments, templates and copies. A pending
classification is visible debt, not checked coverage. Unknown paths fail the
inventory guard. Adding a file to an activated project's owned paths brings it
under that project's current gate immediately.

| State | Check | What it means |
|-------|-------|---------------|
| pending | Explicit diagnostic probe; not a passing enforcement result | Owned scope and prerequisites are recorded, admission is not complete |
| basic | MyPy defaults plus `--check-untyped-defs` | Useful checking is enforced, but complete annotation/strictness debt remains |
| strict | Basic plus MyPy `--strict` | The project's complete declared scope and targets pass strict checking |

Move forwards through reviewed PRs. A necessary rollback is an explicit,
tracked exception reviewed on its own merits, never automatic weakening to
make a failing PR green. The final gate allows no pending/basic first-party
scope, unclassified path, omitted test family or unchecked generated copy.

`Any` may exist at genuinely dynamic third-party or serialization boundaries.
Validate/narrow it before using domain data and type the boundary's public
contract. Explicit, narrowly located casts must reflect an already-proved
runtime contract, not replace validation. New `Any`, ignores and stub exceptions
are reviewed. Global missing-import suppression, `ignore_errors`, blanket
ignores, unchecked first-party stubs and skipped first-party imports are not
completion strategies. Strict MyPy is static assurance, not a proof of every
runtime property; behavioral tests remain independent.

## One local and CI front door

Proposed command contract, to be implemented in Phase 1:

```text
python tools/typecheck.py --prepare
python tools/typecheck.py <project-name>
python tools/typecheck.py --enforced
python tools/typecheck.py --changed --base origin/dev
python tools/typecheck.py --all
python tools/typecheck.py --inventory
python tools/typecheck.py <project-name> --strict
```

- `--prepare` explicitly prepares checker/dependency environments through the
  existing seasoned dependency and contained/shared-environment owners. No
  service, startup self-provisioning, global package installation or product
  import/test collection is added.
- Per-project checking uses the manifest's current state. A missing environment
  fails with the exact preparation command, not a silent install.
- `--enforced` checks activated projects and the complete inventory. It reports
  pending/basic/strict totals so a green migration gate cannot look globally
  compliant.
- `--changed` includes staged, unstaged and added Python files plus activated
  dependent scopes. CI uses its pinned base/head diff; unsupported or ambiguous
  impact is conservative, not an empty-success result.
- `--all` diagnoses the complete declared portfolio, including pending projects.
  It must not report full compliance because only enrolled projects passed.
- `--inventory` reports every physical Python path, project, state and any
  canonical/composed correspondence. Final `--require-complete` combines
  inventory completeness with all projects strict.
- `--strict` measures a stricter project's delta without changing enrollment.
- Windows uses response files instead of unbounded command-line lists.

Use a pinned MyPy version and declared per-project dependencies/stubs with the
repository's seven-day seasoning. Bind each independent environment's import
roots and supported interpreter/platform targets explicitly. Do not reuse an
ambient runtime, conflicting combined environment or consumer's undeclared
extra. Checker-version/configuration changes recheck all activated projects.

The initial single generic checker command may wrap these details; simplicity
belongs at the front door, not in an under-specified shared environment.

## PR-sized work packets

Each row is a PR or repeated PR family. The lead opens one scoped issue per
selected packet and records its real owner. A reasonable packet changes one
component/typing boundary or admits a small, coherent group, with an independently
green gate. Rough size/diagnostic estimates are planning aids, never permission
to weaken behavior or omit files.

| Packet | Scope | Merge condition |
|--------|-------|-----------------|
| P0 | This plan, baseline and active index | Reviewed intent; no execution before merge |
| P1 | Typed front door, manifest, full inventory, focused checker tests and a few already-clean libraries | Real local basic/strict negative controls; complete inventory, selected libraries strict |
| P2 | Source PR CI enforcement through the existing required aggregate | An activated type regression fails; local/CI parity; existing unit selection unchanged |
| P3 | Exact-candidate release qualification through the existing controller contract | Static result is bound to the candidate actually released; required checks and governed Main admission unchanged |
| P4 | Canonical fragment composition/source map and copy qualification | Every fragment line/copy accounted for; negative drift controls; no runtime behavior change unless explicitly reviewed |
| P5 | Package/environment/platform and tooling identity repairs | No duplicate module names or ambient import resolution; legitimate platform code gets a faithful target |
| L1 | Near-clean leaf libraries: config-migrate, dropin-registry, endpoint-rendezvous, then similarly small scopes | Local narrowing/generic fixes; directly strict where possible |
| L2 | Remaining libraries, one boundary or library at a time | Basic admission when needed, then separate strict-delta closure; affected consumers tested |
| R1 | Budget Guidance and other small leaf runtimes/services | Independent complete project admission, then strict; declared targets/extras preserved |
| R2 | Logger, Index, SSH, Machines, Vault, MCP and Pull Requests | One plugin/subsystem per PR, not a combined annotation sweep |
| R3 | Bridge, Dispatch and Containers | Shared prerequisites first; component-level callable/state/data boundaries, then project strictness |
| R4 | Worktrees, Worktree Manager and Fleet Manager | Subsystem/entrypoint-sized slices; preserve lazy globals, native interfaces and compatibility behavior |
| T1 | Fixtures/test helpers followed by dependent package test families | Type fixtures/doubles without weakening assertions or replacing useful tests |
| T2 | Repository/CI tools, installers, hooks, payload-only scripts and remaining Python paths | Complete scope admission, then strict; no scripts/tests exclusions |
| F1 | Final completeness, exception cleanup, standards and live gates | All paths qualified, all projects strict, actual PR/release checks verified; archive only then |

P1/P2/P3 may split further if review or provider qualification makes them too
large. Library and runtime rows repeat as many small PRs as needed; they are
not a promise that all plugins fit into fourteen changes. Test admission starts
alongside source cohorts once fixtures are ready, rather than becoming an
unbounded final annotation dump.

## Static views are qualified source, not escape hatches

The baseline exposed fixed source fragments loaded into one namespace with
`exec`. Independent MyPy invocation cannot see that composition.

Prefer a faithful checker-only concatenation of the fixed declared fragments
with a line/source map, or a genuinely modular typed implementation when it
reduces complexity. The former can avoid an unrelated runtime rewrite, but it
must prove complete membership, order and exact source correspondence. A
fragment added or changed without the corresponding view invalidates the gate.
Type-check the resulting shared namespace and every caller's public contract.

Byte-identical generated/vendored copies may inherit qualified canonical
checking only through existing exact materialization/copy correspondence.
Modified copies are independent targets. Release checking validates the
actual candidate's complete correspondence, not only Dev's canonical modules.
Templates with placeholders get their declared materialized Python view and
appropriate negative controls; they do not disappear from inventory.

## Validation and contribution discipline

Every packet records basic/strict diagnostics before and after, pending scope,
the active policy and the exact local command. Diagnostic totals include
cascades and copy duplicates; do not equate them with edits or runtime bugs.

Use the smallest existing focused unit suite for behavior changed by typing,
then the directly relevant integration/native checks. Protocols, guards and
safe casts should normally preserve runtime behavior. A change to validation,
serialization, state structure or lifecycle is a behavioral change requiring
its own focused tests/review. Whole-runtime rewrites are not annotation fixes.

The checker itself has real subprocess controls, not only mocked command
construction. Prove fresh setup separately from checking. MyPy must never import
product code as an execution shortcut or collect tests to discover targets.
Contain checker processes, caches and temporary views through the existing
owners; leave no daemon or recurring poller.

Keep PR, source-CI, candidate-release and installed-runtime states distinct.
Source workflow changes may require the existing Main controller owner's
reviewed adoption before the revised release gate is live. This effort does not
resume a paused controller, bypass a required gate or install/restart runtimes
to claim type-checking success.
