# Windows residual census: tools, Fleet, and published-only workflows

The original census is read-only evidence for the reviewed #7338 wave plan, not implementation,
publication authorization, performance evidence, or a model-routing evaluation.
The coordinator owns integration and the parent #5694/#5682 completion gate.
No source, preserved WIP, Git refs, or workflow configuration was changed;
no tests, installation, network operations, or native qualification were run.
Later landed dispositions below identify actual merges without changing the
immutable census inputs, hunk hashes or historical observations.

## Immutable inputs and census rules

| Input | Commit |
|-------|--------|
| Preserved source | `8b7b8778b69e6bd1b6a6f2b0018fa6e1f59d961f` |
| Recorded residual base | `6e2e59ceed9a5cb822bd02906f5d4e1173aeae71` |
| Original published head | `339dc00123964d84a117afd55197a192895ebac8` |
| Original merge base | `936926d76a7066ac6eb5a1b11a83c91716eddf69` |
| Current Dev comparison pin | `442be2d536fe9b77fb8336805f2bbe461199d94d` |

`S` identifies the recorded-base-to-preserved-source diff: **23 paths,
88 unified-3 hunks**. `W` identifies the separately read working-tree diff
against preserved source: **3 paths, 4 hunks**, not committed source evidence.
`P` identifies original-merge-base-to-original-head hunks in the two published
workflow paths absent from the 177-path residual: **2 paths, 7 hunks**.
Namespaces are deliberately not interchangeable.

Each row records its full emitted hunk header and the first 12 hexadecimal
characters of SHA-256 over the entire hunk, including header, context, changes,
and terminal LF, stopping immediately before the next hunk/file header.
The input is `git diff --no-ext-diff --unified=3` with LF normalization.
Rows have exactly one disposition, including mixed hunks blocked pending an
owner's finer partition; references to prospective sub-batches do not assign
the same hunk twice. Added-line comparisons were discovery aids, not proof of
equivalence. Actual Dev bodies, path history, blame and merge ancestry supply
the specific already-merged findings below.

### Verified landed foundations, not residual patches to republish

- **Private group parent:** Dev `tools/run-plugin-tests.py:499` creates the
  basetemp parent with mode `0o700`; Dev
  `tools/test_run_plugin_tests.py:520-548` retains
  `test_pytest_group_parent_is_created_without_shared_write`, including
  `umask(0o002)` and exact `0o700` assertion. Blame attributes both to #6116,
  `cbb7256268e6fb3943b88fffcb0b8fd12da2b5ba`, an ancestor of pinned Dev.
  This is separate from Windows parallel accounting and cancellation ownership.
  S058 adds creation of the group directory itself, not that already-live parent.
- **Incoming scandir protections:** Dev's cached `os.scandir` traversal and
  native storage-limit regression were introduced by #7119,
  `c335e85435df6f15cd386bc39ce5f63b8fe98f2d` (path `log -S`, plus ancestor
  verification). Preserve these and the source's explicit symlink refusal.
- **Optional PowerShell cleanup:** #7304,
  `55a93b92cda57031cc6f00d225da62b719f0de51`, actually merged and is an
  ancestor of Dev. Its changed-path history and actual Dev cleanup test prove
  S078's semantic contract is live. It is not byte-identical: Dev's negative
  control uses `SilentlyContinue`, verifies attempted autoload without
  incorrectly depending on an exit failure, and preserves the absent/loaded/
  autoload controls. Do not reintroduce the source's older negative control.
- **PowerShell module isolation:** C-MODULE merged in #7353 as
  `5500e756bd0add8a028d116aa97228d5bbe1c046`. S036 and the exact S081
  module-discovery/S080 import partition are no longer residual. The merged
  native cases retain both hosts and both tier settings and additionally
  check real application lookup and the actual parent environment; six
  portable cases cover absent/inherited paths and explicit-tier boundaries.
  This does not settle the other contracts in S081.
- **Fresh bounded storage accounting:** C-STORAGE merged in #7371 as
  `9ad401dd8c4827a44e2ca5ed1e3bcbd13f94eca8`. S035/S038/S039 and the
  separately admitted S081 storage definitions are no longer residual.
  Its added failure/cancellation cases join active file-holding readers;
  this does not prove process teardown or settle cleanup scheduling.
- **Verified process teardown:** C-TEARDOWN merged in #7376 as
  `6bf2b1892e2d9bd68aaae0ab8cf3460a930b9692`. S037/S040-S051,
  S053-S055/S083 and their admitted S081/S084 test definitions are no
  longer residual. Its final implementation additionally preserves uncertain
  Linux membership, handles non-ASCII process names, keeps the POSIX reaper
  active after owner death and retains all shared-sandbox consumers on
  unverified teardown. This is process ownership evidence, not aggregate
  cleanup or Windows performance qualification.
- **Reaped-group cleanup and aggregate accounting:** C-CLEANUP merged in
  #7382 as `422b4827e9b58d3bf04699415c0b3845a99d8e3c`.
  S052/S056-S059 and their admitted S084 test definitions are no longer
  residual. The merged implementation also includes preparation and final
  removal in the aggregate budget, joins queued cleanup through cancellation
  and preserves an unreaped-process error over cleanup cancellation so the
  sandbox remains available. This closes the four containment foundation
  slices, not the parent Windows performance gate.
- **Required Source CI:** actual Dev `ci.yml` is unit-only, from #7269
  `ce77da58017442f83bf3d82a5f80b111b63b9737`, followed by source-evidence reuse
  #7281 `6e2e59ceed9a5cb822bd02906f5d4e1173aeae71`; both are Dev ancestors.
  Actual Dev rotation has literal `${{ false }}` for its unavailable Windows
  job, unchanged 30-minute outer deadline, explicit no-evidence status, and a
  separate Linux container lane. These are current policy, not regressions to
  repair by restoring old hosted jobs.

## Named retained batches and explicit blocked owners

### Subsequent admission: G-PS-SCAN only

Admit S030/S031/S068 and only G-PS-SCAN's explicit S027/S029 subspans:
the `fnmatch`/`os` imports, error-propagating traversal helpers and two actual
scan consumers. The strict plugin sweep remains unpruned, including otherwise
ignored virtual-environment directories inside plugins. Only the repo-wide
backstop prunes the existing plugin subtree and ignored names; ignored
ancestors still suppress that backstop. No new exclusion is introduced.
Sorted candidates retain platform suffix matching, directory/file-link
behavior and script read errors. Directory links are
not followed.

The immutable scan definitions are reconciled to the current guard without
changing its lexical detector, runtime helper or error handling. Tests compare
legacy strict/backstop candidates and exact complete-guard diagnostics,
observe traversal, exercise real readable file/directory links and retain
script read failures. Enumeration failure is tested in both scan modes.
Seven scan cases plus the actual complete-guard retention consumer passed,
eight tests with no skips on native Windows and exactly two workers. The
standalone real guard also passed for 13 runtime plugins; combined time was
33.83 seconds including preparation/cleanup, with unchanged item deadline,
diagnostic bound and containment caps. Ruff and diff checks passed.

This independent below-altitude traversal change does not re-extract the
already-landed G-RETENTION function or alter native Bash/writer fixtures.
Helper documentation and the backstop comment record the boundary. Existing
usage/runtime docs remain accurate; no plugin payload, changefile, installer,
external venue, required-unit admission or workflow-policy change applies.
Fresh synthetic trees and actual guard execution cover the lightweight clean
consumer path, not full-module/plugin or parent timing qualification.
Review and required CI still gate merge.

### Subsequent admission: G-RETENTION only

Admit S028/S033/S034 and only G-RETENTION's explicit S027/S029 subspans:
remove the old `tempfile` import and local retention function, import the
shared helper under the existing alias, and move its unchanged body plus
required imports into `install_contract_guard.py`. The production
`_check_runtime_retention(runtime)` call remains unchanged. This does not
admit any G-PS-SCAN import, helper, consumer or test; mixed S027/S029 remain
partitioned rather than becoming a combined scan/retention extraction.

An AST comparison proves the entire moved body identical to current Dev.
The existing real-runtime execution, construction-cleanup refusal and retained
incomplete-slot refusal cases remain; alias identity is asserted explicitly.
A new observing wrapper calls the real shared helper from the actual complete
guard and proves exactly one canonical runtime invocation. It does not mock
past any retention assertion.

All five retention cases and the real install guard passed on native Windows,
exactly two workers, no skips, 25.45 seconds including preparation/cleanup.
The 30-second item deadline, 240-second diagnostic bound and default caps
remain. Ruff and diff checks passed. These are focused consumer results,
not full-module or full-plugin qualification.

This is a below-altitude source relocation with no plugin payload,
installer/runtime behavior, workflow policy or required-unit admission change;
no changefile or external provisioning lane applies. The helper's module
description now reflects its shared scope. Synthetic temporary slots exercise
the clean consumer contract without touching an installed runtime. Existing
runtime/usage docs remain accurate; review and required CI still gate merge.

### Subsequent admission: G-FEED

This candidate admits only S025/S026/S062/S063 (`b6682f6cbc29`,
`46195f4b7025`, `a7dd046908d3`, `3cc22a6bba65`). Replace eight recursive
candidate globs with one error-propagating walk, pruning only the existing
excluded names. Retain the exact pattern set, relative-part exclusions,
platform case matching, hidden/nonexcluded trees, directory candidates,
deduplication and sorted output. The separate workflow scan remains shallow.
Unlike G-DOC, an excluded name in the repository root or its ancestors is not
a relative exclusion; tests preserve that distinction for all six names.

The complete module passed 22 tests with no skips on native Windows,
exactly two workers, together with the real whole-tree guard, in 23.81 seconds
including preparation/cleanup. Legacy sorted candidates and exact diagnostics
match, a scandir observer proves only the existing excluded trees are pruned,
and unreadable enumeration raises. Original URL/default/comment exemptions
and real-repository verification remain unchanged. Item deadline, diagnostic
bound and containment caps are unchanged; Ruff and diff checks passed.

This below-altitude guard change updates its module documentation, not a
plugin payload or runtime/install contract, so it needs no changefile.
Synthetic temporary trees and actual guard execution cover the lightweight
integration and clean consumer path. No external venue, workflow policy,
required-unit admission, rollout or parent timing qualification is changed.
Review and required CI still gate merge.

### Subsequent admission: G-DOC

This candidate admits only S023/S024/S060/S061 (`3a70b5997994`,
`eac47c7b012d`, `5f0b597c128c`, `7799a745b199`). Replace the whole-tree
Markdown glob with a walk that never enters the two already-excluded exact
directory names, `.worktrees` and `node_modules`. All other candidates remain:
hidden trees, virtual environments, similarly named directories, platform case
matching, Markdown directories and file/directory links. Directory links are
not traversed. Excluded ancestors still suppress the entire scan. Enumeration
failures propagate rather than silently reducing coverage.

Tests compare both sorted candidates and exact diagnostics against the legacy
glob/count loop, including file/pattern/match order and line numbers, directory
diagnostics, encoding/read failures and native links. A scandir observer proves
that excluded descendants are never enumerated while hidden and nonexcluded
descendants remain visited. Existing count suffix prefilters and navigation
checks are unchanged; the legacy comparator is reconciled to the current
three-field pattern records rather than replacing those prior improvements.

The complete module passed 131 tests without skips on native Windows with
exactly two workers; the actual repository guard also passed. The combined
run took 11.88 seconds including preparation and cleanup, retaining the
30-second item deadline, 240-second diagnostic bound and default containment
caps. Ruff and diff checks passed. These are focused functional results,
not complete-plugin or repeated same-head qualification.

Required CI initially refused the changed docs source bytes before execution.
Refresh only their two declared source hashes in the existing unit manifest;
its admitted node inventory and coverage targets remain unchanged. The new
filesystem traversal/link tests are not added to required unit-only CI.

This is a below-altitude guard implementation change, with no plugin payload,
installer or runtime behavior change and no changefile needed. The guard's own
module documentation records the traversal boundary. Fresh temporary-tree
tests and the actual repository guard exercise the simple integration and
clean consumer paths; no external service or development venue is involved.
Review and required CI still gate merge. This is not parent timing evidence.

### Subsequent admission: F-ALLOC

This candidate admits only S002-S019 in `test_allocation_release.py`.
Each worker prepares one active node/Gateway SQLite pair through the existing
real admission, acceptance and cooperative commit path. Each case receives
ordinary private file copies and reconstructs its actual journal objects;
no live lease, callback, connection or production ownership evidence is shared.
The seed's original database bytes must remain unchanged at session teardown.
The added two-copy regression retires/releases the left pair while proving the
right pair's active effect, node ownership, Gateway claim and absence of release
proofs remain unchanged. The original seed remains active too.

All 16 original test bodies and decorators are AST-identical after normalizing
only the first setup assignment to the copied fixture. Every existing parameter,
rollback, proof/index refusal, installed crash/lost-reply/concurrent-release
and successor assertion remains present. The complete module passed 87 tests
with no skips, native Windows, exactly two workers, 13.51 seconds including
preparation and cleanup. The original 30-second item/native child deadlines,
240-second diagnostic bound and default process/memory/temp caps remain intact.
Real SQLite creation and native installed subprocess operations run; no mock
substitutes for the consumer contract.

Existing runtime/usage documentation remains accurate. This ledger records
the test-only setup change; a scoped Fleet patch changefile accompanies it.
Fresh temporary databases and the independent-copy regression exercise the
lightweight clean consumer path. No installer/bootstrap behavior or external
fleet venue changes, no rollout and no parent timing qualification are claimed.
Review and required CI still gate merge; other residual contracts remain open.

### Subsequent admission: F-WHEEL and its encountered Gateway prerequisite

The F-WHEEL candidate admits S001/S020/S021/S022 (`cbba7d8b52ee`,
`141138e09b0e`, `7bcaefad4600`, `9918c6de8ef8`). The original wheel fixture
still exists at module scope in current Dev; the proposed conftest was never
landed, not missing immutable evidence. Move the current fixture to session
scope, retain the real build and assert unchanged wheel bytes at teardown.
Each xdist worker owns its own artifact; installations and durable client
state remain independent per test. Update both current artifact-staging
consumers and the subsequently added role-plan distribution consumer to use
the shared fixture. All other fixture imports now use conftest discovery too,
preserving their non-fixture helper imports. The destructive installed assembly
proof builds its own private artifact and deletes only that private source;
it additionally verifies the session artifact and library survived. Existing
source-removal assertions remain intact. No generic build helper, dependency
declaration, installed-client assertion or allocation fixture changes.

Complete consumer validation encountered a separate required-input omission:
#8196 added `credential_references.py` and its Gateway worker consumer without
adding that module to the pinned Gateway proof's closed source list. The real
pinned-context wheel test failed on the missing archive member. This candidate
adds only that explicit module to the list; unknown sources and linked blobs
still refuse. The existing committed-module equality, source-refusal and
materialization tests cover the list without relaxing admission. This is a
directly encountered prerequisite, not a new F-ALLOC or installer extraction.

Validation first refused an unsuitable Worktrees environment lacking Fleet's
installed metadata and setuptools. The dedicated Fleet environment was then
prepared through the existing leased standalone preparation and seasoned
dependency helper. The existing native OpenSSL was exposed only in the owned
test child's PATH; no host tool, PATH or runtime was changed. The complete
four consumer modules plus five existing portable Gateway source cases passed
89 tests with six unchanged platform/privilege skips, native Windows, exactly
two workers. Complete Fleet collection also passed without errors; collection
and execution together took 90.00 seconds including preparation and cleanup.
The initial
properly prepared run reproduced the pinned wheel omission before correction.
All item/native deadlines, test selections and resource caps remain unchanged.
The real wheel/sdist builds, isolated installs, RECORD/dependency assertions,
client operations and fixture teardown ran; no mock substitutes for them.

These fresh temporary builds/installs exercise the lightweight clean consumer
boundary. No Docker/container proof or live fleet qualification is claimed;
that proof runner is Linux-owned, while the changed input closure is exercised
by real local pinned materialization and wheel construction. Existing public
usage documentation remains accurate. A scoped Fleet changefile, review and
required CI gate publication; the broader ledger and parent timing acceptance
remain open.

These are proposed extraction boundaries, not one implementation PR.
All require the reviewed plan, this census, a fresh Dev baseline, scoped
documentation/changefile where applicable, current-head review and required
CI. Dependencies below are behavioral, not a license to combine batches.
Commands name future validation targets only; none was executed for this census.

| Code | Single disposition / contract, assertions, dependencies and future evidence |
|------|------------------------------------------------------------------------|
| F-WHEEL | **Retained: Fleet immutable wheel sharing.** `fleet-manager/tests/conftest.py`, `test_artifact_staging.py`, `test_client_distribution.py`; move the module wheel fixture into session scope, preserve wheel bytes through teardown, retain actual build/install/staging and dependency/RECORD assertions. Depends only on unchanged build helpers and the real isolated per-case installation. Validate those two test modules plus fixture teardown; not the allocation batch. |
| F-ALLOC | **Retained: Fleet copied allocation seed.** `fleet-manager/tests/test_allocation_release.py`; copy `node.db`/`gateway.db` per case, prove seed bytes unchanged and left retirement/release cannot change right ownership/release state. Preserve all parameter values, installed crash/lost-reply/concurrent-release/successor operations, rollback and indexed-history assertions. Validate the complete module; no cached live lease, callback or ownership evidence. Independent of F-WHEEL. |
| G-DOC | **Retained: Markdown traversal parity.** `tools/check-docs-consistency.py` and its test module; candidate/diagnostic equality with legacy traversal, prune only `.worktrees`/`node_modules`, retain hidden/nonexcluded/case behavior, directory/read/encoding errors, ancestor exclusion and nonfollowed symlinks. Validate `tools/test_check_docs_consistency.py` and the real guard on the eventual candidate. No installer dependency. |
| G-FEED | **Retained: feed candidate traversal parity.** `tools/check-feed-neutrality.py` and its test module; existing pattern/exclusion set and workflow discovery unchanged, unreadable directory fails. Validate `tools/test_check_feed_neutrality.py` and actual guard invocation. Independent of G-DOC. |
| G-PS-SCAN | **Retained: install-guard PowerShell candidate scan parity only.** `_raise_scan_error`, `_powershell_paths`, the two actual scan consumers in `tools/check-install-contract.py`, and scan-parity definitions in `tools/test_check_install_contract.py`; exact candidates/diagnostics, strict plugin sweep versus pruned backstop, ignored ancestors, file/directory symlinks, unreadable enumeration/read failures. This is filesystem candidate discovery, not native PowerShell execution or Bash admission. Validate scan-parity cases and the actual guard. It has no established dependency on G-RETENTION; their shared S027/S029 hunks stay blocked pending partition. |
| G-RETENTION | **Retained: mechanical runtime-retention assertion relocation only.** Move the unchanged `_check_runtime_retention` body from `tools/check-install-contract.py` to `tools/install_contract_guard.py` as `check_runtime_retention`, preserving the imported alias and actual consumer call. Keep construction/retention leases, immutable completion, conflict refusal and reclamation assertions. Validate both the alias binding and real guard invocation against the runtime; importing a moved helper is not execution proof. Independent of PowerShell candidate traversal. The deletion and replacement function share S029 only as a diff accident; no combined extraction is authorized. |
| B-GUARD-SPLIT | **Blocked: #5694 integration coordinator must partition mixed import/function hunks S027/S029.** Assign only the explicit G-PS-SCAN and G-RETENTION subspans below to their respective independent contracts. Do not extract either whole hunk or use common file ownership to combine them. No new behavioral dependency is established. |
| G-BASH | **Retained: native Bash fixture admission.** `_working_bash` and its two added tests in `tools/test_check_install_contract.py`; reject System32/WindowsApps WSL launchers without probing them, preserve an explicit supported native override, and require headless real shell behavior. Imports S064 are owned here. Validate focused native-Bash admission and existing shipped helper tests on a real supported shell; unavailable native coverage is not a pass. Independent of traversal optimization. |
| G-WRITER | **Retained: prove shipped shell writer ran.** S067's marker-existence assertion in `test_every_shipped_shell_helper_reports_its_writer_status`; retain failure/return-status and hash-failure checks. Depends on a genuinely working admitted Bash (G-BASH if required on the validation host), not a mock passing past the helper. |
| C-MODULE | **Actually merged:** S036 and its exact S080/S081 module-discovery partition via #7353 `5500e756bd0add8a028d116aa97228d5bbe1c046`; see the landed-foundation entry above. Do not replay it with Windows storage accounting or the remainder of mixed S081. |
| C-STORAGE | **Actually merged:** S035/S038/S039 and its exact S081 storage-test partition via #7371 `9ad401dd8c4827a44e2ca5ed1e3bcbd13f94eca8`; see the landed-foundation entry above. Reader joins on failure/cancellation, fresh accounting and unchanged native storage-limit enforcement are live; do not replay them in process teardown or cleanup scheduling. |
| C-TEARDOWN | **Actually merged:** S037/S040-S051, S053-S055/S083 and their exact S081/S084 ownership-test partition via #7376 `6bf2b1892e2d9bd68aaae0ab8cf3460a930b9692`; see the landed-foundation entry above. Preserve verified membership/reaping, owner-death and cancellation handling, and sandbox retention in every consumer. No reader/sandbox cleanup merely on parent exit. C-CLEANUP remains separate. |
| C-CLEANUP | **Actually merged:** S052/S056-S059 and their exact S084 cleanup-test partition via #7382 `422b4827e9b58d3bf04699415c0b3845a99d8e3c`; see the landed-foundation entry above. One cleanup worker removes only verified-reaped private group directories, joins all queued work through cancellation and includes preparation and final removal in aggregate accounting. Shared seeds, private-parent #6116 and unreaped-sandbox retention remain intact. No parent performance qualification is claimed. |
| COVERAGE | **Retained: whole-file selection provenance producer.** S032/S070-S074 only in `execution.py` and `test_coverage_selected_execution.py`; require measured baseline, use real baseline-to-head history, whole-select only selected modules with proven upstream drift, retain originally requested node IDs, leave unselected upstream modules unexpanded, unknown nodes elsewhere still fail strict consumer. Future validation must exercise the actual selection runner: upstream parametrization gives all five current cases; missing measured baseline refuses. Depends on actual strict selection consumer/fixture, not historical Source CI jobs. Run producer/strict-consumer regression modules and a real exact-selection invocation; do not broaden admission to make old workflow fixtures green. |
| B-INSTALL | **Blocked: existing installer ownership reconciliation, not blanket ownership of editable metadata.** S077/S079's actual snapshot-copier assertions remain with #6909. S076 is one mixed native test: metadata subspans accompany the native ledger's independent W4-DEPENDENCY-METADATA contract; retained-source layout/immutability subspans require the existing owner/coordinator to establish their actual prerequisite, not assume a dependency on the PowerShell copier. S087 is canonical-support copy parity, coordinated with W4-DEPENDENCY-METADATA for metadata-only support changes; other copier-dependent support remains #6909. The complete S076/S087 hunks stay blocked until that partition is accepted. Exact symbol/assertion spans below prevent double ownership. Only reviewed publishable prerequisites count; no #6909 private source/materialized overlay may be adopted. S077's `re` import is already live via #7304; its snapshot docstring does not imply the copier test is live. |
| B-CONTAIN | **Blocked: #5694 residual/integration owner.** S080/S081/S084 are mixed imports or large test-addition hunks spanning independent module/storage/ownership/cleanup contracts. Partition by actual test definition and required imports before any extraction; no whole-hunk catch-all PR. Exact target assertions are listed under C-MODULE/C-STORAGE/C-TEARDOWN/C-CLEANUP above. In particular retain fresh accounting/cancellation and teardown-negative cases rather than dropping inconvenient tests. |
| B-POLICY | **Blocked: existing #6237 main-owned-workflow and #1303 portfolio owners.** S069/S075/S085/S086 and all W hunks; historical workflow fixtures must follow actual current unit-only/no-Windows-evidence contracts. Preserve canonical selected SHA/manual ref/report assertions and failures pending an owned repair. The reported 167 failures/11 fixture errors are unresolved evidence, not reproduced here. Never restore retired jobs, fabricate Main lineage, delete cases, or loosen positive unit admission. COVERAGE remains a distinct producer contract. |
| R-MACOS | **Proposed retirement, requires rationale review by #6237/#1303 owners.** S082/S088 and P001-P004 presume a required hosted macOS/integration Source CI job which #7269/#7281 retired. Retire that obsolete workflow/fixture coupling, not native POSIX ownership tests or their eventual eligible venue. W001/W004 reverse the old additions but are preserved WIP, not proof of a merged repair. |
| R-PROFILE | **Proposed retirement, requires #5694 and portfolio-owner review.** P005 adds diagnostic budget expansion input. That as-authored switch is incompatible with preserved acceptance; do not restore it to bypass 30/300/900 budgets. Timing diagnostics must remain explicitly nonqualification and within applicable limits. |
| B-NATIVE-CONFIG | **Blocked: #5694 with #6237/#1303 venue owners.** P006/P007 mix useful native configuration (exactly two Worktrees workers, duration reporting, same-version packaged native Git and execution-path checks) with now-disabled hosted routing and 60-minute/900-second-item/2700-second-plugin diagnostic expansion. Owner must separate native configuration from obsolete routing and forbidden overrides before admission. Retain native same-version/path/argument and worker-count assertions in the eventual valid lane; no Windows container capacity is invented. These hunks are not carried wholesale. |
| M-CLEANUP | **Actually merged:** S078's cleanup behavior/tests via #7304 `55a93b92cda57031cc6f00d225da62b719f0de51`; see exact semantic/negative-control differences above. No republishing. |

## Exact sub-hunk admission inventory

All line spans below are inclusive, in the immutable preserved-source commit
listed above; decorators belong to their test. Spans identify **source-added
definitions**, not permission to replay an entire enclosing hunk. Local helper
names are qualified by their enclosing test; they follow that test's contract.
Blank separators and unchanged hunk context carry no independent behavior.
These partitions do not create additional S/W/P census keys.

### Mixed containment imports and test hunks

S080 adds only imports in `tools/test_plugin_test_containment.py`:
`shutil` at line 4 → C-MODULE; `subprocess` at line 5 → C-MODULE's real
PowerShell launch and C-TEARDOWN's real descendant/usage fixtures;
`threading` at line 7 → C-STORAGE; `SimpleNamespace` at line 9 →
C-STORAGE's entry-stat fixture and C-TEARDOWN's Job/process fixtures.
An import needed by two separate test extractions may accompany each actual
consumer if absent from its fresh baseline; this does not duplicate a
behavioral hunk disposition. Whole S080 remains B-CONTAIN.

S081, `tools/test_plugin_test_containment.py`, partitions as follows:

| Added symbol | Source-added span | Contract |
|--------------|------------------:|----------|
| `test_native_powershell_module_discovery_is_host_detached` | 63-107 | C-MODULE; includes native-host skip marker and both `host`/`allow_host_state` parameterizations, not permission to add new skips |
| `test_tree_size_counts_nested_files_once` | 110-118 | C-STORAGE |
| `test_tree_size_uses_cached_entries_without_following_links` | 121-159 | C-STORAGE |
| `test_parallel_tree_size_overlaps_reads_with_two_threads` | 162-184 | C-STORAGE |
| `test_parallel_tree_size_rechecks_growth_and_removal` | 187-196 | C-STORAGE |
| `test_parallel_tree_size_counts_across_multiple_batches` | 199-204 | C-STORAGE |
| `test_parallel_tree_size_propagates_unexpected_read_failure` | 207-213 | C-STORAGE |
| `test_exited_process_cannot_hide_an_elapsed_wall_limit` | 216-247 | C-TEARDOWN; preserve elapsed-limit refusal even when exit is already observable |
| `test_windows_job_waits_for_every_member_before_closing_handle` | 250-273 | C-TEARDOWN |
| `test_windows_job_reports_unproven_termination_instead_of_claiming_reaped` | 276-292 | C-TEARDOWN |
| `test_posix_group_waits_until_no_live_members_remain` | 295-308 | C-TEARDOWN |
| `test_posix_usage_does_not_treat_zombies_as_file_holding_members` | 311-319 | C-TEARDOWN |
| `test_linux_empty_group_does_not_require_ps` | 321-327 | C-TEARDOWN |
| `test_zombie_heavy_group_exceeds_process_budget` | 329-348 | C-TEARDOWN; preserves existing POSIX-specific mark and 128-process refusal |
| `test_posix_group_verifies_members_after_sigkill` | 351-367 | C-TEARDOWN |
| `test_posix_teardown_refuses_stale_identity_before_each_signal` | 370-383 | C-TEARDOWN; retain both `changed_before_kill` values |
| `test_posix_exit_observation_does_not_reap_controller` | 386-394 | C-TEARDOWN |
| `test_posix_control_pipe_failure_is_explicit` | 397-401 | C-TEARDOWN; retain empty and malformed-result parameters |
| `test_controller_reports_status_then_stays_alive_without_leaking_control_env` | 404-433 | C-TEARDOWN |
| `test_file_holding_descendant_is_reaped_before_return` | 436-465 | C-TEARDOWN; retain both normal/timeout controller cases |

Exact local helpers in S081 (prefix abbreviations below refer to the full
test symbol in the table, not a different definition):

- Under `test_tree_size_uses_cached_entries_without_following_links`:
  `Entry` 124-138 (`is_dir` 127-129, `is_file` 131-133, `stat` 135-138);
  `Link` 140-143 (`is_file` 141-143);
  `Removed` 145-147 (`is_dir` 146-147);
  `Entries` 149-154 (`__enter__` 150-151, `__exit__` 153-154).
  All → C-STORAGE.
- `test_parallel_tree_size_overlaps_reads_with_two_threads.read` 168-180
  and `test_parallel_tree_size_propagates_unexpected_read_failure.read`
  208-209 → C-STORAGE.
- Under `test_exited_process_cannot_hide_an_elapsed_wall_limit`:
  `Process` 217-221 (`poll` 220-221), `Job` 223-231 (`__init__` 224-225,
  `assign` 227-228, `close` 230-231) → C-TEARDOWN.
- `test_windows_job_waits_for_every_member_before_closing_handle.active`
  256-259 → C-TEARDOWN.
- `test_zombie_heavy_group_exceeds_process_budget.Process` 331-335
  (`poll` 334-335) → C-TEARDOWN.
- Under `test_controller_reports_status_then_stays_alive_without_leaking_control_env`:
  `command` 412-416 and `waiting` 424-426 → C-TEARDOWN.

There are no added top-level shared helper definitions in S081. The unchanged
following test at 468 onward is context, not an added test. Whole S081 remains
B-CONTAIN; in particular storage cases are not automatically part of the
process-ownership extraction.

S084, `tools/test_run_plugin_tests.py`:

| Added symbol | Source-added span | Contract and exact local helpers |
|--------------|------------------:|----------------------------------|
| `test_plugin_budget_includes_final_sandbox_cleanup` | 1101-1128 | C-CLEANUP; local `Directory` 1105-1109, `Directory.__exit__` 1106-1109 |
| `test_budget_exhausted_between_groups_still_reports_final_cleanup` | 1130-1162 | C-CLEANUP; `Directory` 1134-1138, `Directory.__exit__` 1135-1138, `run` 1149-1152 |
| `test_reaped_group_cleanup_overlaps_the_next_group` | 1165-1215 | C-CLEANUP; `Directory` 1172-1178, `Directory._rmtree` 1173-1178, `run_contained` 1183-1196; local `threading` import at 1166 belongs here |
| `test_unreaped_tree_retains_sandbox_and_never_schedules_group_cleanup` | 1218-1247 | C-TEARDOWN; `run_contained` 1224-1227; verifies no cleanup on uncertain ownership, not an independent cleanup-speed change |

The following `test_admission_wait_rejects_infinite_value` is unchanged
context. Whole S084 remains B-CONTAIN. No C-MODULE or C-STORAGE definition
is hidden inside this hunk.

### Editable metadata versus retained-input/copier ownership

The native ledger's **W4-DEPENDENCY-METADATA** owns canonical
`libs/installer-engine/installer_dependency_policy.py` plus its thirteen
derived plugin copies, not a #6909 private overlay. Dev's current
retained-input helper is the rebasing target. This report supplies its
test-side partition; it does not acquire or reassign those production hunks.

S076 adds **one** top-level test, not separate metadata-only and copier
tests: `tools/test_installer_dependency_policy.py::
test_native_real_setuptools_editable_keeps_generated_metadata_private`,
decorator/body **799-890**, with nested `run` **839-858**.
There is **no added metadata-only test definition to extract wholesale**.
The full-hunk B-INSTALL disposition therefore remains until the coordinator
accepts an assertion-level split preserving both `authored_config` values:

| Source-added subspan / symbol | Exact assertion or support | Owning contract / dependency |
|------------------------------|----------------------------|------------------------------|
| 799-838, test setup | Real setuptools source, optional authored `setup.cfg`, clean environment, actual UV venv, interpreter and retained-root capture | Shared native fixture support for the W4 metadata consumer; preserve existing dependency seasoning and genuine native execution |
| `run` 839-849, 858 | Capture actual retained `cwd`; read retained `setup.cfg`; require `egg_base == "."`; preserve authored `tag_date == "0"` and uninterpreted alias text | **W4-DEPENDENCY-METADATA** test assertions, not PowerShell copier behavior |
| 850-851 | Windows retained-root parent relation `retained_base.parents[2] == source.parent` | Separate retained-source layout/admission assertion; B-INSTALL owner/coordinator must identify its current publishable prerequisite. Not evidence that the PowerShell copier is required |
| 852-857 | Mutate the live manifest to an unapproved URL immediately before the actual install command | Separate immutable retained-input/dependency-graph assertion; not metadata rewriting itself. Preserve the negative control and admission protections; existing owner must verify prerequisite rather than import #6909's overlay |
| 860-866 | Hook only at the actual subprocess boundary; call production `run_uv_install` with real editable source and trusted root; require success | Shared real consumer harness; W4 metadata needs it, but success with the layout/mutation controls also depends on the preceding independent contracts |
| 867 | Retained staging root no longer exists after install | Retained-input lifetime cleanup assertion; B-INSTALL pending exact owner prerequisite, not a standalone metadata assertion |
| 868-872 | No source `*.egg-info`; authored `setup.cfg` unchanged, or absent if not authored | **W4-DEPENDENCY-METADATA** observable privacy/preservation assertions |
| 873-890 | Modify live module twice, import installed editable distribution and verify `direct_url.json` editable truth, invoke actual console entry point and verify changed value | **W4-DEPENDENCY-METADATA** no-regression consumer evidence; must remain real rather than a mocked metadata-only check |

No S076 line directly calls or asserts `Copy-InstallSnapshotItem`.
Accordingly, claiming all S076 metadata evidence for #6909 would conflict
with the independent canonical metadata contract. Conversely, its existing
layout/mutation/lifetime assertions cannot silently be annexed to that
contract simply because they share the test. Whole S076 remains blocked,
with the listed metadata subspans retained for W4 integration and the
remaining assertions awaiting the named existing-owner prerequisite.

The direct copier-dependent test is S079,
`tools/test_installer_finalizers.py::
test_native_snapshot_copy_preserves_complete_nested_payload` **113-141**
(including the two-host parameterization): actual
`Copy-InstallSnapshotItem`, full hidden/empty/bracketed/binary/bytecode
payload parity, silent success and missing-source failure. It stays #6909.
S077's snapshot docstring follows that test, whereas its `re` import is
already live via #7304.

S087's `tools/test_sync_installer_engine.py::
test_shipped_dependency_support_matches_canonical_for_every_runtime`
**564-579** is support-byte parity, not a new copier assertion. Its
metadata-only canonical/copy requirement accompanies W4-DEPENDENCY-METADATA;
the whole broad support-file hunk remains B-INSTALL until any other support
dependencies are reconciled with their actual owner. No production copy is
adopted from a private comparison.

### Independent scan and retention-relocation subcontracts

S027/S029 are now **B-GUARD-SPLIT**, not a retained combined installer batch:

- **G-PS-SCAN:** S027's source additions `fnmatch` **42** and `os` **46**;
  S029's `_raise_scan_error` **685-686** and `_powershell_paths`
  **689-712**; S030/S031's production scan consumers; S068's scan tests below.
- **G-RETENTION:** S027's deletion of old-base `tempfile` import **47**;
  S028's source alias import **59**; S029's deletion of old-base
  `_check_runtime_retention` **683-719**; S033's shared-helper module
  description/import support and S034's source
  `install_contract_guard.check_runtime_retention` **213-249**.
  The body preserves the same assertions, and the production call remains
  `_check_runtime_retention(runtime)` at source **949**. Old-base coordinates
  are explicitly deletion coordinates, not source-added spans.

S068, all in `tools/test_check_install_contract.py`, owns only G-PS-SCAN:
`_legacy_powershell_paths` **944-951**; `_script_tree` **954-993**;
`test_powershell_candidates_match_legacy_and_prune_only_backstop`
**996-1022**; `test_powershell_backstop_preserves_ignored_ancestor_semantics`
**1025-1034**;
`test_powershell_candidates_preserve_readable_file_and_directory_symlinks`
**1037-1055**; `test_install_contract_diagnostics_match_legacy_scan`
**1058-1079**; `test_powershell_scan_surfaces_directory_errors`
**1082-1091**; `test_install_contract_surfaces_script_read_errors`
**1094-1110**. Exact local helpers are
`test_powershell_candidates_match_legacy_and_prune_only_backstop.recording_walk`
**1007-1010**,
`test_powershell_scan_surfaces_directory_errors.failing_walk` **1085-1087**,
and `test_install_contract_surfaces_script_read_errors.guarded_read`
**1103-1106**; each stays with its test.
The preceding no-final-newline normalization does not relocate retention
assertions. G-RETENTION introduces no new test definition in S068: its
preserved self-asserting runtime helper and real guard consumer must actually
be exercised when that separate relocation is implemented.

Native Bash executable discovery remains G-BASH, and real PowerShell
module-discovery behavior remains C-MODULE. Neither is merged into G-PS-SCAN's
filesystem path enumeration. Sharing a hunk or file establishes no dependency.

## Complete recorded-source ledger

Every heading is a repository-relative path. The final token on each row is
its single disposition code from the table above.

### `fleet-manager/tests/conftest.py`

- S001 `cbba7d8b52ee` `@@ -0,0 +1,18 @@` — F-WHEEL

### `fleet-manager/tests/test_allocation_release.py`

- S002 `c3c765b5e6be` `@@ -2,6 +2,7 @@ from concurrent.futures import ThreadPoolExecutor` — F-ALLOC
- S003 `282d6b8bac73` `@@ -57,6 +58,27 @@ def prepared(tmp_path):` — F-ALLOC
- S004 `046f5954adbb` `@@ -67,8 +89,26 @@ def forbidden(_):` — F-ALLOC
- S005 `289c7385b1f9` `@@ -103,8 +143,8 @@ def test_retirement_release_and_successor_preserve_original_operations(tmp_path)` — F-ALLOC
- S006 `04a26c098883` `@@ -122,8 +162,8 @@ def test_released_generation_cannot_be_reused_across_identities(tmp_path, change` — F-ALLOC
- S007 `a9605e1f237d` `@@ -137,8 +177,8 @@ def test_mismatched_proof_never_releases_gateway_claim(tmp_path, field, value):` — F-ALLOC
- S008 `55cfe4cea609` `@@ -151,8 +191,8 @@ def test_release_shape_is_strict_and_immutable(tmp_path, changes):` — F-ALLOC
- S009 `2c4cd7c25378` `@@ -176,8 +216,8 @@ def test_release_binding_and_legacy_acceptance_do_not_retire(tmp_path):` — F-ALLOC
- S010 `3895fdad06de` `@@ -200,8 +240,8 @@ def test_retirement_or_tombstone_failure_rolls_back_everything(tmp_path, failure` — F-ALLOC
- S011 `0dcb7adea1e4` `@@ -218,8 +258,10 @@ def test_gateway_release_audit_failure_is_atomic(tmp_path, monkeypatch):` — F-ALLOC
- S012 `58125e286cf7` `@@ -257,8 +299,8 @@ def run(n, g, intent, body):` — F-ALLOC
- S013 `c4532e8bee0f` `@@ -292,8 +334,8 @@ os._exit(26)` — F-ALLOC
- S014 `9ef5f40eec6d` `@@ -315,8 +357,8 @@ print(p.encoded.decode())` — F-ALLOC
- S015 `e33f7c899439` `@@ -352,8 +394,8 @@ except PlanError:` — F-ALLOC
- S016 `99a66a569cae` `@@ -371,8 +413,10 @@ def test_generation_high_water_survives_multiple_retirements(tmp_path):` — F-ALLOC
- S017 `4dc90f3b1e8d` `@@ -402,8 +446,10 @@ def test_release_upgrade_refuses_reserved_table_collision_without_mutation(tmp_p` — F-ALLOC
- S018 `acd4410023d0` `@@ -433,8 +479,10 @@ def test_callbacks_cannot_mutate_older_tombstones_or_core_state(tmp_path, action` — F-ALLOC
- S019 `80a9fd4f55c8` `@@ -463,8 +511,10 @@ def test_indexed_generation_lookup_is_bounded_with_large_permanent_history(tmp_p` — F-ALLOC

### `fleet-manager/tests/test_artifact_staging.py`

- S020 `141138e09b0e` `@@ -18,7 +18,7 @@ import fleet_manager.artifact_staging as staging` — F-WHEEL
- S021 `7bcaefad4600` `@@ -633,7 +633,7 @@ def test_descriptor_is_closed_when_stream_construction_is_interrupted(` — F-WHEEL

### `fleet-manager/tests/test_client_distribution.py`

- S022 `9918c6de8ef8` `@@ -73,16 +73,6 @@ def build(project, output, operation="build_wheel"):` — F-WHEEL

### `tools/check-docs-consistency.py`

- S023 `3a70b5997994` `@@ -28,11 +28,13 @@ Exit 0 = consistent, 1 = drift (suitable for a pre-push hook / CI).` — G-DOC
- S024 `eac47c7b012d` `@@ -354,11 +356,26 @@ COUNT_PATTERNS = [` — G-DOC

### `tools/check-feed-neutrality.py`

- S025 `b6682f6cbc29` `@@ -43,6 +43,8 @@ from __future__ import annotations` — G-FEED
- S026 `46195f4b7025` `@@ -76,15 +78,23 @@ _SHELL_DEFAULT_PREFIX_RE = re.compile(r"\$\{?\w+:-\s*['\"]?$")` — G-FEED

### `tools/check-install-contract.py`

- S027 `70ff96d72c21` `@@ -39,12 +39,13 @@ Exit code 0 = conformant, 1 = violations (suitable for a pre-push hook).` — B-GUARD-SPLIT
- S028 `0566a32e3102` `@@ -55,6 +56,7 @@ from install_contract_guard import (` — G-RETENTION
- S029 `abbdbbb626f3` `@@ -680,43 +682,34 @@ def _uses_engine_manifest_writer(text: str, ext: str) -> bool:` — B-GUARD-SPLIT
- S030 `a85de50952bc` `@@ -744,7 +737,7 @@ def check() -> int:` — G-PS-SCAN
- S031 `a29fe76c2b58` `@@ -786,11 +779,9 @@ def check() -> int:` — G-PS-SCAN

### `tools/coverage_guided_selection/execution.py`

- S032 `43c800629837` `@@ -85,6 +85,22 @@ def build_execution_payload(` — COVERAGE

### `tools/install_contract_guard.py`

- S033 `3c0ae204ca2f` `@@ -1,8 +1,10 @@` — G-RETENTION
- S034 `831bb502bad1` `@@ -208,6 +210,45 @@ def is_ignored_scan_path(path) -> bool:` — G-RETENTION

### `tools/plugin_test_containment.py`

- S035 `bb32a0dcc731` `@@ -15,6 +15,7 @@ import signal` — C-STORAGE
- S036 `3ddc2ec7e34f` `@@ -39,6 +40,7 @@ _ROOT_ENV = {` — C-MODULE
- S037 `68e1b5af64db` `@@ -135,6 +137,10 @@ class ContainmentError(RuntimeError):` — C-TEARDOWN
- S038 `72cefb854dc6` `@@ -264,11 +270,12 @@ def isolated_environment(` — C-STORAGE
- S039 `b7b67aa98fb6` `@@ -276,17 +283,48 @@ def _tree_size(path: Path) -> int:` — C-STORAGE
- S040 `7079cbd83b2c` `@@ -299,7 +337,7 @@ def _linux_group_usage(pgid: int) -> tuple[int, int] | None:` — C-TEARDOWN
- S041 `189bfd43002a` `@@ -309,12 +347,12 @@ def _linux_group_usage(pgid: int) -> tuple[int, int] | None:` — C-TEARDOWN
- S042 `0ab3c88057e7` `@@ -325,10 +363,10 @@ def _ps_group_usage(pgid: int) -> tuple[int, int] | None:` — C-TEARDOWN
- S043 `09cf93495098` `@@ -340,23 +378,68 @@ def _ps_group_usage(pgid: int) -> tuple[int, int] | None:` — C-TEARDOWN
- S044 `1f510b0bd5f4` `@@ -437,6 +520,7 @@ class _WindowsJob:` — C-TEARDOWN
- S045 `1a6e210ef223` `@@ -492,10 +576,29 @@ class _WindowsJob:` — C-TEARDOWN
- S046 `4359deaaac3a` `@@ -508,6 +611,10 @@ class _WindowsJob:` — C-TEARDOWN
- S047 `a79d5d32dfcd` `@@ -520,7 +627,7 @@ class _WindowsJob:` — C-TEARDOWN
- S048 `666ebd0db939` `@@ -550,9 +657,19 @@ def _worker_main(argv: Sequence[str]) -> int:` — C-TEARDOWN
- S049 `d274cc6dfbd8` `@@ -568,15 +685,34 @@ def _run_contained_process(` — C-TEARDOWN
- S050 `908b148b1710` `@@ -586,19 +722,29 @@ def _run_contained_process(` — C-TEARDOWN
- S051 `3927d1572987` `@@ -633,21 +779,28 @@ def _run_contained_process(` — C-TEARDOWN

### `tools/run-plugin-tests.py`

- S052 `3057487c6056` `@@ -53,6 +53,7 @@ import sys` — C-CLEANUP
- S053 `26b212f2aa5d` `@@ -64,6 +65,7 @@ from plugin_test_selection import (` — C-TEARDOWN
- S054 `58df0feae2ea` `@@ -81,6 +83,18 @@ EXTERNAL_TEST_DEPENDENCIES = {` — C-TEARDOWN
- S055 `1b10b791a7fd` `@@ -429,7 +443,7 @@ def run_plugin(` — C-TEARDOWN
- S056 `b35e4192692b` `@@ -440,9 +454,10 @@ def run_plugin(` — C-CLEANUP
- S057 `aaeff3cb1f8a` `@@ -464,7 +479,8 @@ def run_plugin(` — C-CLEANUP
- S058 `5d7e20866817` `@@ -474,6 +490,7 @@ def run_plugin(` — C-CLEANUP
- S059 `4cc5ec45821c` `@@ -509,17 +526,32 @@ def run_plugin(` — C-CLEANUP

### `tools/test_check_docs_consistency.py`

- S060 `5f0b597c128c` `@@ -2,6 +2,8 @@` — G-DOC
- S061 `7799a745b199` `@@ -289,3 +291,183 @@ def test_legacy_readers_also_report_markdown_directories(tmp_path, monkeypatch):` — G-DOC

### `tools/test_check_feed_neutrality.py`

- S062 `a7dd046908d3` `@@ -15,12 +15,36 @@ from __future__ import annotations` — G-FEED
- S063 `3cc22a6bba65` `@@ -28,6 +52,16 @@ def test_bare_dockerfile_pin_is_flagged():` — G-FEED

### `tools/test_check_install_contract.py`

- S064 `7d50b7c20e1a` `@@ -8,6 +8,8 @@ import re` — G-BASH
- S065 `88ad67c23a89` `@@ -725,12 +727,22 @@ def _bash_environment(bash: str) -> dict[str, str]:` — G-BASH
- S066 `dedc4e046551` `@@ -744,6 +756,49 @@ def _working_bash() -> str | None:` — G-BASH
- S067 `e191a057fdee` `@@ -818,6 +873,8 @@ def test_every_shipped_shell_helper_reports_its_writer_status(` — G-WRITER
- S068 `3c60aff9c482` `@@ -881,4 +938,173 @@ def test_shipped_payload_hash_helpers_fail_closed(tmp_path, tools) -> None:` — G-PS-SCAN

### `tools/test_coverage_selected_execution.py`

- S069 `2623f86d3d86` `@@ -3,7 +3,6 @@ from __future__ import annotations` — B-POLICY
- S070 `e9bd42110f2c` `@@ -17,6 +16,8 @@ import pytest` — COVERAGE
- S071 `a2c0a443dcd1` `@@ -31,6 +32,13 @@ def history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str,` — COVERAGE
- S072 `6d1e8d30912f` `@@ -93,7 +101,7 @@ def test_empty_diff_still_executes_the_measured_cross_cutting_floor(history):` — COVERAGE
- S073 `7cc4d657c87f` `@@ -107,6 +115,54 @@ def test_changed_new_test_files_and_out_of_scope_changes_remain_visible(history)` — COVERAGE
- S074 `f59de225f1e2` `@@ -198,6 +254,18 @@ def test_empty_curated_evidence_is_a_hard_error_not_only_guards(history):` — COVERAGE
- S075 `6848aa5a808b` `@@ -328,44 +396,34 @@ def test_workflow_policy_never_executes_function_calls():` — B-POLICY

### `tools/test_installer_dependency_policy.py`

- S076 `2ed76331be11` `@@ -796,6 +796,100 @@ def build_editable(wheel_directory, config_settings=None, metadata_directory=Non` — B-INSTALL

### `tools/test_installer_finalizers.py`

- S077 `565e66d2e708` `@@ -1,7 +1,8 @@` — B-INSTALL
- S078 `9753f18f2b27` `@@ -12,6 +13,69 @@ REPO = Path(__file__).resolve().parents[1]` — M-CLEANUP
- S079 `0a32759cd446` `@@ -46,6 +110,37 @@ def _purge_ps(tmp_path, host, root, body):` — B-INSTALL

### `tools/test_plugin_test_containment.py`

- S080 `090f0d3e74ee` `@@ -1,8 +1,12 @@` — B-CONTAIN
- S081 `c0236e565bcd` `@@ -56,6 +60,411 @@ def test_native_temp_budget_still_terminates_the_process_tree(tmp_path, capfd):` — B-CONTAIN

### `tools/test_public_toolchain_source.py`

- S082 `d1010ee476ea` `@@ -60,7 +60,7 @@ def test_workflow_host_pytest_provisioning_is_seasoned(workflow):` — R-MACOS

### `tools/test_run_plugin_tests.py`

- S083 `53ba4caef85f` `@@ -527,14 +527,22 @@ def test_containment_owner_launch_preserves_process_contract(monkeypatch, tmp_pa` — C-TEARDOWN
- S084 `0d6f56f54c91` `@@ -1090,6 +1098,155 @@ def test_host_state_requires_explicit_tier_opt_in(capsys) -> None:` — B-CONTAIN

### `tools/test_select_windows_rotation.py`

- S085 `b5a2b9a62c5f` `@@ -7,6 +7,8 @@ import importlib.util` — B-POLICY
- S086 `d1c472d40787` `@@ -77,3 +79,40 @@ def test_cli_github_output_format(capsys) -> None:` — B-POLICY

### `tools/test_sync_installer_engine.py`

- S087 `bd93adc4c809` `@@ -561,6 +561,24 @@ def test_sync_repairs_missing_local_copy_when_wrappers_are_local(fake_repo):` — B-INSTALL

### `tools/test_verify_promotion_workflows.py`

- S088 `442d77545011` `@@ -438,12 +438,17 @@ def test_native_guard_rejects_self_consistent_off_main_target_before_checkout(re` — R-MACOS

## Separate uncommitted WIP ledger

These four hunks were read from the three explicitly assigned working files,
not substituted for source hunks. W001 and W004 reverse S082/S088's macOS
assumptions; their resulting files equal the corresponding recorded-base and
Dev blobs, but that is **not** a merged-WIP claim. W002 changes old worker/
profiling assertions to literal disabled-Windows/no-evidence assertions.
W003 asserts current Linux-full/report dependencies and canonical SHA.
All remain with B-POLICY pending the existing owners' complete fixture repair;
none resolves the 167/11 failures by itself. WIP bytes must be preserved.

### `tools/test_public_toolchain_source.py`

- W001 `7faf74281f0c` `@@ -60,7 +60,7 @@ def test_workflow_host_pytest_provisioning_is_seasoned(workflow):` — B-POLICY

### `tools/test_select_windows_rotation.py`

- W002 `dab0ce3ea666` `@@ -81,25 +81,24 @@ def test_cli_github_output_format(capsys) -> None:` — B-POLICY
- W003 `e6da00885192` `@@ -113,6 +112,9 @@ def test_canonical_manual_ref_and_schedule_pin_the_same_matrix_sha() -> None:` — B-POLICY

### `tools/test_verify_promotion_workflows.py`

- W004 `4c40d032fe49` `@@ -438,17 +438,12 @@ def test_native_guard_rejects_self_consistent_off_main_target_before_checkout(re` — B-POLICY

## Published-only residual and original-scope cross-check

The original merge-base comparison changes the same **23 tools/Fleet paths
in 84 hunks**; the later source uses 88 differently partitioned hunks.
The two additional published workflow paths below contribute seven hunks
and are not silently omitted because absent from the 177-path continuation.
They are not additions to the S count.

For the shared 23 paths, direct original-head-to-preserved-source comparison
finds only four changed files: explicit symlink protection in containment;
replacement of the historical CI pilot assertion/import; incoming scandir/
symlink/native storage-limit regressions; and upstream installer-capability
probe updates. The latter are baseline changes, not extra candidate patches.
Comparing actual added/deleted change lines in the original 84 hunks against
the 88 residual hunks exposes the original cached-scandir conversion:
`tools/plugin_test_containment.py`,
`@@ -264,22 +270,59 @@ def isolated_environment(`,
original hunk SHA-256 prefix `cc157d5f4234`.
Its baseline conversion is already merged by #7119
`c335e85435df6f15cd386bc39ce5f63b8fe98f2d`; the residual parallel-reader
extension stays C-STORAGE. The other unmatched original lines are
`Path`/`pytest` imports already in the base/Dev containment test, not
unclassified behavior. Thus absence from S is not inferred from file names
or ancestry alone. The old CI-pilot expectations replaced in the continuation
are proposed retirement under the current #7269/#7281 policy, with the
#6237/#1303 owners; keep the tests' actual coverage-producer contracts.

### `.github/workflows/ci.yml`

- P001 `002d8253e221` `@@ -5,7 +5,7 @@ run-name: ${{ inputs.promotion_run != '' && format('CI promotion {0} at {1}', in` — R-MACOS
- P002 `98727928ce87` `@@ -1316,6 +1316,45 @@ jobs:` — R-MACOS
- P003 `bac293e03254` `@@ -1965,6 +2004,7 @@ jobs:` — R-MACOS
- P004 `5154f5088466` `@@ -2039,6 +2079,7 @@ jobs:` — R-MACOS

### `.github/workflows/windows-coverage-rotation.yml`

- P005 `95fdfa32ef1b` `@@ -55,6 +55,11 @@ on:` — R-PROFILE
- P006 `e5bb49f8c042` `@@ -141,7 +146,12 @@ jobs:` — B-NATIVE-CONFIG
- P007 `b73ef0414f04` `@@ -159,7 +169,36 @@ jobs:` — B-NATIVE-CONFIG

## Reconciliation totals, gaps and unchanged acceptance

| Namespace | Paths | Hunks | Dispositions |
|-----------|------:|------:|--------------|
| Recorded residual S | 23 | 88 | 72 retained; 13 blocked; 2 proposed retirement; 1 actually merged |
| Separate working-tree W | 3 | 4 | 4 blocked, WIP preserved |
| Additional published workflows P | 2 | 7 | 2 blocked; 5 proposed retirement |

The bounded refinement preserves every original path/header/hash key and
merged reference. It adds symbol/assertion-level admission partitions without
unblocking the mixed containment or installer hunks. Separating independent
scan and retention contracts moves only S027/S029 from retained to explicitly
blocked split: hence 72 retained/13 blocked, rather than the initial 74/11.

Read-only self-check regenerated all three diffs and verified all **99 rows**
against exact path, full header and LF-normalized hunk hash, with no duplicate,
missing or surplus key. Separate before/after whole-file SHA-256 checks
confirmed all three WIP files unchanged. This is census validation, not test
execution.

The already-live private-parent/scandir foundations are explanatory original/
baseline reconciliation, not duplicated S keys. There are no
blocked-unclassified hunks: explicit integration/fixture/installer owner gates
are named instead. Exact test-level subdivision of B-CONTAIN, installer
ownership acceptance and policy-correct historical fixture repair remain
blocked; no branch, test or current-head performance result was manufactured.
Proposed retirement is subject to review, never deletion authority.

Qualification remains repeated **complete same-head Windows runs at or below
900 seconds including preparation and cleanup**, **every group at or below
240 seconds**, and **exactly two Worktrees workers**. Preserve 30-second item,
300-second runner-group, 900-second plugin, 128-process, 4096 MiB process-tree
memory and 2048 MiB temporary-storage limits, native child deadlines and
existing marks. No skip/exclusion/reduced selection/waiver, Linux result,
isolated case or sum of batch timings establishes that gate. No extraction
closes #5694/#5682 or the parent effort. The coordinator must integrate this
census with the separately owned native/library, Worktrees and documentation
ledgers before admitting any implementation batch.
