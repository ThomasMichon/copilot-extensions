# Worktrees residual hunk census

Read-only evidence for the reviewed Windows wave plan, not implementation,
retirement approval, test execution, or performance qualification.

## Immutable inputs and method

- Historical base: `6e2e59ceed9a5cb822bd02906f5d4e1173aeae71`.
- Preserved source: `8b7b8778b69e6bd1b6a6f2b0018fa6e1f59d961f`.
- Original publication: `339dc00123964d84a117afd55197a192895ebac8`;
  original comparison base: `936926d76a7066ac6eb5a1b11a83c91716eddf69`.
- Current Dev: `442be2d536fe9b77fb8336805f2bbe461199d94d`.
- Scope: `plugins/agent-worktrees`, excluding its `scripts/` subtree:
  **70 paths, 330 unified-three hunks**, 9,930 insertions, 7,504 deletions.

The ledger uses repository-relative POSIX Git paths, not local checkout paths.
Each heading is relative to `plugins/agent-worktrees/`. Each numbered entry
identifies the path-local hunk ordinal, its exact range header, and the first
12 lowercase hex characters of SHA256 of the **entire** original hunk
(including Git's section suffix, context, and final LF). Displayed range
headers factor the optional section suffix into the exact suffix dictionary
below; concatenate them to recover every full header. The ordinal, immutable
input OIDs and hash bind the full header and body without ambiguity. Diffs used
`--no-ext-diff --no-renames --unified=3`; CRLF was normalized to LF before hashing.
No patch was applied, no repository code imported, and no test collected/run.

Every source after-image was compared with the corresponding pinned Dev
file, not with a floating branch or a working-tree file. None of the
nontrivial context-inclusive after-images matched exactly. The sole match
was `finalize_ref.py:6`, a trailing-blank-line deletion. This is **not**
evidence that all other hunks are wholly unmerged: overlapping or relocated
changes require further reconciliation. There are deliberately **no
already-merged dispositions** in this ledger. In particular, recent log
entries and a matching filename are not accepted as landing evidence.

The source and Dev history for finalization includes actual commits
`7b09c01fb15caf7e995c57425afcc7c1c58eed70` (#5609) and
`b978b3b3f41520df75c4b1e5d7828d141dfe4191` (#5561); these establish
the prior finalization contract, not merger of the batching hunks below.
The already-extracted #7304 is outside this scope and receives no credit here.

## Single-disposition legend and narrowly bounded candidate batches

An entry's final token is its **only** disposition. The original 48
candidate assignments and whitespace-retirement proposal remain unchanged.
The later refinement below gives the other 281 hunks named behavioral
ownership. `MX-*` identifies a lossless symbol/subspan ownership partition,
**not** a combined implementation batch: split it into the named constituent
contracts before publication. No existing-author owner is invented.
Retained means candidate ownership, not reviewed, passing or already landed.

| Token | Wave / exact contract | Observable assertions and exclusions |
|---|---|---|
| Y | 3 / safe YAML loader | `config.py` and `test_config.py:1`: C safe loader when available, safe Python fallback, scalar/non-map/error returns unchanged. Excludes committed-ref candidate selection. |
| H | 3 / one hook protection decision | `hooks.py` and `test_hooks.py`: one in-repo read per decision including empty mappings; retain machine-local > committed > registry priority; later calls re-read. No persistent authority cache. |
| C | 3 / committed-config absence probe | `inrepo_config_source.py`, `test_config.py:2`: literal NUL `ls-tree` candidate probe, no unsafe path globbing; absent candidates avoid blob reads; failure retains bounded ordered reads, deadline, precedence and diagnostics. Separate from Y/H. |
| S | 3 / squash tree object reads | `finalize_ref.py:2`: one ordered two-tree probe; require exactly two complete SHA-1/SHA-256 IDs; empty base/no change/error retain refusal. Focused regression still needs extraction from finalization tests. |
| F | 3 / upstream blob comparison | `finalize_ref.py:5`, `test_finalize_blob_comparison.py`: raw tree diff and exact object maps include deletion/type/path cases, malformed reads fail closed with warning. Requires actual `git_tree_diff` consumer availability, not an inferred filename match. |
| G | 3 / creation-point gating | `finalize_open_pr_gate.py`, `finalize_ref.py:3-4`: skip unnecessary creation inference only after fresh zero-ahead proof; malformed counts/unknown creation remain conservative; local source shadowing, remote tips/reflogs and subsequent reset history still constrain publication. Separate from S/F; regression extraction unresolved. |
| P | 3 / publication transport evidence | `README.md`, `pr_publish.py`, `pr_rewrite.py`, `test_pr_remote_evidence.py`: one fresh fetch/push snapshot per decision; same slug on another host refused; literal remote-key regex, NUL framing, repeated/multiline/valueless settings and order retained; read errors prevent push; pinning, leases and later mutation checks remain real. |
| R | 3 / replay tree evidence | `pr_rebase.py`, `test_pr_rebase_trees.py`: pinned complete commits, exact ordered raw trees, notes/signature/color/replacement/foreign Git context cannot substitute evidence; malformed/truncated/duplicate rows and timeout preserve refusal; parent reconstruction and raw author/message comparisons remain independent checks. |
| A | 3 / atomic recovery retention | `pr_recovery.py`, `test_pr_recovery_batch.py`: fresh NUL-input commit resolution on each call; full SHA-1/SHA-256 IDs, missing/noncommit rejection; create-only transaction retains all refs or none on collision. Excludes remote publication authority and replay authorization. |
| I | 2 / immutable PR seed semantics | `test_pr_repo_fixture.py`, `test_pr_seed_cache.py`: copied object stores/config/hooks/refs/records and repaired worktree links remain private; changed sibling cannot mutate seed; failed/incomplete/redirected readiness fails closed; process lock interruption never publishes readiness; two processes build once. Implementation `conftest.py` is separately partitioned below rather than silently expanding I. |
| T | proposal / retirement, not enacted | `finalize_ref.py:6`: remove whitespace-only residual from a future extraction after review; no behavioral or merged claim. |

Production imports in `finalize_ref.py:1` combine dependencies of multiple
contracts; MX-FINAL names their split. `conftest.py` combines no-mux isolation,
record-only fixtures, run-local seed publication, base copying, published
copying, rebase copying and fork copying; MX-SEEDS names their independently
owned symbols. `pr_test_helpers.py` similarly separates relocated helpers
from snapshot adaptation through MX-HELPERS.

Real dependencies: I needs the actual wave-1 runner-owned sandbox and
containment contract; published/rebased/fork fixture consumers additionally
need their own copy/identity-repair semantics. P-related fixture stubs that
now intercept `_remote_snapshot` need P, not just a test file move. R/A
share the existing bounded isolated `_git_bytes` transport but do not
authorize one another's decisions. S/F/G require separately scoped
regression ownership before publication. Serial publication remains a
review rule, not a fabricated runtime dependency.

Native fixture ownership below is reconciled against the native census
report only; this report neither reads those neighboring implementation
bodies nor claims #6909's installer work.
No hunk is assigned to a catch-all wave-6 implementation batch. Wave 6 is
the still-open #5694/#5682 qualification and residual-settlement obligation.

## Admitted fixture-semantic batches

### B-TERMINAL seed/copy foundation

Baseline: the actual #7674 merge. Split the existing `_worker` helper
into a template-free Git builder and fresh record setup. Its old
consumers still build their own Git topology; none is converted yet.
`_worker_seed` runs the same builder under the existing hermetic
environment and run-local seed lock. `_copy_worker` physically copies
Git stores and linked checkouts, repairs administrative links,
relocates origin and creates fresh tracking/session records.

Two isolation cases verify initial Git bytes, private physical file
identity, origins/common directories and fresh records. Commit,
push, configuration and record mutation plus removal of the first
checkout leave every seed/sibling byte unchanged. The second copy
retains its complete linked-worktree topology; no object alternates
or shared files are introduced.

All existing test functions/classes, assertions and decorators remain
AST-identical; only `_worker` is split into the shared preparation
helpers. Complete native Windows terminal-conclusion module:
142 passed, zero skips, 114.94 seconds pytest / 121.12 preparation-cleanup
inclusive. Exactly two work-stealing workers retain the original
30-second item watchdog, marks and resource ceilings under a
240-second diagnostic group. This is functional integration, not
quiet-host, whole-suite or repeated same-head qualification.

The consumer conversion, including reciprocal-disposal `_harness`,
remains a separate next slice. Live/foreign/dirty/claim/finality and
disposal decisions are unchanged. This below-altitude test preparation
refactor changes no production, command, installation, configuration
or architecture contract; documentation impact is this ledger.
Clean-room provisioning and external-venue lanes are inapplicable.

### B-RESOURCE private finalization inputs

Actual disposition: #7674 merged as
`4a18e654de6c47a83484de38a238154bc1ffe319`; full Copilot review
`5606924751` approved exact head
`9e59060f9888e05a59a07eed078b6e9f02c14c9a`. Overview, inline/general
channels and threads had no findings; required CI was green. The child
worktree is finalized.

Baseline: the actual #7668 merge. `_final_owner_seed` runs the original
transitive-finalization `_env` builder under the existing run-local
seed lock and hermetic setup environment. `_copy_final_owner` copies
only the anchor and origin stores, relocates origin and constructs
fresh configuration, tracking records and project/install paths.
`final_owner` never copies mutable owner records or caches finalization
authority.

Two repeated isolation cases compare copied Git bytes and physical
file identity, then commit and push only in the first copy, reserve
and close a real producer claim, and actually finalize its owner.
Every seed/sibling byte and sibling record remains unchanged. Both
configurations and their nested repository objects are independent;
all origins and administrative paths name the current copy.

The four existing test functions, their decorators, all assertions,
the cross-process lock regression and `_reserve` remain AST-identical.
Complete native Windows resource-producer-finalization module:
17 passed, zero skips, 58.68 seconds pytest / 77.56 preparation-cleanup
inclusive. Exactly two work-stealing workers preserve the 30-second
item watchdog, all original marks and resource limits under a
240-second diagnostic group. This is functional integration, not
quiet-host, whole-suite or repeated same-head qualification.

This below-altitude test setup extraction changes no production,
command, installation, configuration or architecture contract.
Documentation impact is this ledger only. Clean-room provisioning
and external-venue lanes do not apply to test-only preparation.
Other fixture batches and S/F/G/P/R/A remain separately owned.

### B-CACHE-SEED private empty-Git workspaces

Actual disposition: #7668 merged as
`ffc220ecfdcec8397ce8d313f0cb3648e650ec80`; full Copilot review
`5606637029` approved exact head
`a569f57d69c9d35a5af063d58ea7179ef3395643`. Overview, inline/general
channels and threads had no findings; required CI was green. The child
worktree is finalized. N-STAT remains separately owned.

Baseline: the actual #7665 merge. Prepare only an empty repository
under the existing run-local seed lock and hermetic setup environment.
Each `copy_workspace` physically copies that store and creates its own
empty HOME. Provider payloads, settings, output caches and all current
refresh/launch decisions remain test-local; no authority is cached.

Two repeated guard cases compare every seed/copied byte and reject
shared physical files. They then change the first copy's worktree,
config, info/exclude, index, objects and refs through an actual commit.
Every seed/sibling byte remains unchanged and the isolated home remains
empty; no object alternates are introduced.

All 45 other existing functions/classes and decorators remain
AST-identical, including the native-stat/FIFO simulation. N-STAT is not
absorbed. Complete native Windows cache-refresh module: 94 passed,
four unchanged native-POSIX-FIFO skips, 46.67 seconds pytest /
62.50 preparation-cleanup inclusive. No new skip or selection change.
Exactly two work-stealing workers preserve the original 30-second
watchdog, T2/process/filesystem marks and resource ceilings under a
240-second diagnostic group. Actual discovery/copy/index protection/
refresh/launch consumers execute. This is functional integration, not
quiet-host, whole-suite or repeated same-head qualification.

Documentation impact is this ledger only; runtime, command, install,
configuration and architecture contracts stay unchanged. Clean-room
provisioning and external-venue lanes do not apply to test-only setup.

### B-LEASE-SEED private released histories

Actual disposition: #7665 merged as
`85ff7d66cddb174a5ff91aa45b9d68bfbc2345aa`; full Copilot review
`5606458404` approved exact head
`f34ae3c0aeb29b67765da655994f16e2b2a4191c`. Overview, inline/general
channels and threads had no findings; required CI was green. The child
worktree is finalized. B-LEASE-ASSERT remains separately owned.

Baseline: the actual #7662 merge. Extract `_settings` without changing
its values; prepare one real acquire/renew/release history per worker
session under the existing hermetic setup environment. The source
fixture is process-private, not a mutable run-wide lease authority.
`_copy_released_history` physically copies its bare Git store and
deep-copies all three snapshots; `released_history` returns fresh
settings naming only that case's private remote.

Only the collapse, kind-filter and mid-sweep race consumers reuse this
history. The race acquires/inspects the prepared `stale-history` key;
the real force-with-lease race, generation/token and live-holder checks
remain intact. All old test names, assertions and decorators remain.
The other 23 functions are AST-identical, including the standalone
idempotence and dry-run cases. B-LEASE-ASSERT consolidation is excluded.

Two repeated isolation cases check exact acquire/renew/release ancestry,
all copied remote bytes, independent origins and nested mutable snapshot
records. They then execute real squash and reacquisition against the
first copy. Every seed/sibling byte and snapshot must stay unchanged,
without alternates or shared object files. Each consumer performs its
own current observation and mutation; no lease/store authority is cached.

Complete native Windows lease-store module: 21 passed, zero skips,
70.92 seconds pytest / 81.75 preparation-cleanup inclusive. Exactly
two work-stealing workers preserve the 30-second item watchdog, original
marks and resource ceilings under a 240-second diagnostic group.
This is functional Git integration, not quiet-host, whole-suite or
repeated same-head qualification. Production and other fixture contracts
are unchanged.

Documentation impact is this ledger only; no command, runtime, install,
configuration or architecture contract changes. Clean-room provisioning
and external-venue lanes are inapplicable to test-only preparation.

### B-LANDING private-copy consumers

Actual disposition: #7662 merged as
`86f055d12f95f5162d1adc8ff681cf071fe5a493`; full Copilot review
`5606270816` approved exact head
`c5636a9e62c713b922350683e3fd871bf4618eb6`. Overview, inline/general
channels and threads had no findings; required CI was green. The child
worktree is finalized. Assertion consolidation and unused-OID cleanup
remain separate, not waived.

Baseline: the actual #7661 foundation merge. The function-scoped `env`
now copies the private base seed. Add `merged_env` and `handmade_env`
to copy their derived seed plus the known original published-head marker.
Six consumer functions (seven parametrized cases) use those prepared
inputs instead of rebuilding the identical merge history. Their real
landing, provider-base, refusal, pull-forward, report and finalization
decisions still execute separately with fresh provider/tracking state.

All existing test names, assertions and decorators are retained. The
37 other existing functions/classes are AST-identical, including the
OID-returning commit helper, creation-point/reset/shadowing helpers and
the three foundation isolation regressions. No external test module
imports this module. In particular `test_the_probe_moves_no_ref` and
the default-base/provider-call-zero test remain separately collected:
B-LANDING-ASSERT consolidation is not included.

Complete native Windows landing module: 33 passed, zero skips,
145.53 seconds pytest / 159.91 preparation-cleanup inclusive. Exactly
two work-stealing workers retain the original 30-second item watchdog,
marks and resource ceilings under a 240-second diagnostic group.
This is real per-case Git consumer integration, not quiet-host,
whole-suite or repeated same-head performance qualification. No production
S/F/G/P/R/A/B-INCONCLUSIVE, selection, skip or timeout change is included.

Documentation impact is this ledger; commands, runtime, configuration,
architecture and installation are unchanged. Clean-room and external
venue lanes are inapplicable to test-only fixture reuse.

### B-LANDING foundation: private base, merged and handmade inputs

Actual disposition: #7661 merged as
`a51b337947f6d1a1f0a6430b624c267e6d958086`; full Copilot review
`5606060042` approved exact head
`16d755f32a855f064e8c09d9926b9fe8b5bbb31e`. Overview, inline/general
channels and threads had no findings; required CI was green. The child
worktree is finalized. This settles the foundation, not its consumers
or whole-suite qualification.

Baseline: the actual #7653 merge. Add only the named `_landing_seed`,
`_build_landing_seed`, `_copy_landing_env`, `_merged_landing_seed` and
`_handmade_landing_seed` foundation plus three copy-isolation cases.
The existing `env` fixture, all 37 old functions/classes and their
decorators remain AST-identical, including `_commit`'s OID return.
Consumer conversion is a later B-LANDING slice. B-LANDING-ASSERT's
consolidation is excluded: all old tests remain separately collected.

Use the existing hermetic environment and run-local seed lock to build
real main/dev history. Copy complete physical object stores, refs and
creation reflogs without alternates or hardlinks, relocate both origin
URLs, and retain each derived topology's known `published-head` marker.
The base has main/dev/worktree at one commit; the merged topology has
main behind dev and the worktree reset to dev after its two-commit PR;
the handmade topology keeps the worktree at main while `pr/hand-made`
holds that PR's original head. No landing decision is cached.

Each of the three cases makes two private copies, checks every seed byte
except the relocated Git configs, checks branch/content/OID relationships,
then commits and pushes from both the first checkout and its publisher.
Every seed and second-copy byte must remain unchanged; copied object
files must not share identity with either source. The original consumer
tests continue building and evaluating their own real Git histories.

Complete native Windows landing module: 33 passed, zero skips,
177.77 seconds pytest / 192.09 preparation-cleanup inclusive. Exactly two
work-stealing workers preserve the 30-second default item watchdog,
original marks and resource limits under a 240-second diagnostic group.
This is functional real-Git integration, not quiet-host or repeated
same-head whole-suite performance qualification. Production S/F/G/P/R/A,
B-INCONCLUSIVE, selection changes and all other fixture batches are excluded.

Documentation impact is this ledger only: no runtime, command, install,
configuration, architecture or adopter behavior changes. Clean-room and
external-venue lanes are inapplicable to test-only seed preparation.

### B-REFSPEC private unlanded finalization inputs

Actual disposition: #7653 merged as
`56574e1510e0c2a24e7dfe49924b82e55d8b4c32`. Copilot's full review
`5605763035` approved exact head
`5cf9caa13260f3bc342288bae5aabc26ce711bee`; overview, inline and
general channels had no findings, required CI was green, and the child
worktree was finalized. This settles this fixture slice only, not the
whole-suite qualification gate.

Baseline: the actual #7649 merge. Add only the named
`_refspec_worktree_seed`, `_copy_refspec_worktree`, adapted
`refspec_worktree` and copy-isolation regression with its snapshot helper.
Build actual init/commit/push/clone/fix history under the existing hermetic
environment and run-local seed lock. Copy every object/ref/reflog into
independent stores, relocating the publisher and worktree origin URLs.
The copied input remains unlanded: origin/master is the base, worktree
HEAD is its one fix child, and neither local nor remote PR branch exists.
Each test still performs its actual landing/refusal/finalization decision.

Two repeated cases require exact branch/content/base topology and every
copied input byte except the intentionally relocated Git configs.
Commit/push from the first worktree advances only its origin/master;
commit/push from its publisher creates only that copy's publisher ref.
Every seed and second-copy byte remains unchanged, with no alternates
or shared object files. All 54 other existing top-level functions/classes
and all original decorators are AST-identical. In particular the five
inconclusive-evidence cases remain separate: no B-INCONCLUSIVE, G/S/F,
production, selection or timeout change is absorbed.

Complete native Windows precondition module: 65 passed, zero skips,
81.71 seconds pytest / 97.89 preparation-cleanup inclusive. Exactly two
work-stealing workers retain the original 30-second default item watchdog,
marks and resource limits under a 240-second diagnostic group.
Ruff F/E9 and the 13-plugin install contract pass.
This is real Git consumer integration, not quiet-host, whole-suite or
repeated same-head performance qualification.

Documentation impact is this ledger only; runtime/command/install/config
contracts are unchanged. Provisioning clean-room and external deployment
are inapplicable to this fixture-only change. Parent #5694/#5682 and
complete repeated same-head native qualification remain open.

### B-DOCTOR isolate unrelated daemon reporting

**Actually merged:** #7649 as
`b6ff25d16a2d237e122aaf5d06e26614c1fccd5f`.

Baseline: the actual #7647 merge. Add only the named local
`daemon_health.doctor_report` replacement to
`test_doctor_json_repairs_pair_from_untracked_knowledge_cwd`.
The case's subject is actual persisted pair-integrity repair and JSON
projection from an untracked knowledge CWD, not host daemon diagnosis or
repair. Its existing records, invalid project entry, project resolution,
repair counts and resulting knowledge tracking-record assertion remain
unchanged. Removing the one added patch restores the exact original
module AST. Production daemon reporting and its dedicated tests are untouched.

Complete native Windows pair-integrity module: three passed, zero skips,
4.10 seconds pytest / 14.42 preparation-cleanup inclusive, exactly two
work-stealing workers with unchanged 30-second item watchdog and resource
limits under a 240-second diagnostic group. Ruff F/E9 and the 13-plugin
install contract pass. This is actual in-process doctor/record integration,
not daemon-health, quiet-host or whole-suite qualification.

Documentation impact is this ledger only; runtime/command/install/config
contracts are unchanged. Provisioning clean-room and external deployment
are inapplicable to this fixture-only change. Parent #5694/#5682 and
complete repeated same-head native qualification remain open.

### B-CREATION private native creation anchors

**Actually merged:** #7647 as
`bf3099d764aadc2155c209817aaa7b53f25eeae0`.

Baseline: the actual #7644 merge. Add only the named
`_creation_anchor_seed`, adapted `_creation_fixture`/`native_creation` and
`test_creation_anchor_copies_isolate_native_git_mutations`. Build the real
template-free initial commit once under the existing hermetic environment
and run-local seed lock; each native fixture physically copies independent
harness/knowledge stores and assigns its own origin.
Records, retained prompts, permissions, project configuration, tracking,
worktree creation and actual creation-tail repair remain per-case.

The fixture's empty machine topology is explicit on disk, not a reader
stub or cache. Preserve the existing raw `native_git=True` entry point
used by retained-allocation tests, including tuple/suffix identity and
later legacy machine-config changes; it still builds its own actual Git
history and does not acquire a shadowing empty topology.
Two repeated guard cases prove local origins/config/topology, retained
prompt, absent companion tracking and independent object files. Config,
new-object and ref mutations in the harness leave every seed and knowledge
byte unchanged. All 38 other existing functions and all original
decorators remain AST-identical.

Complete native Windows creation and retained-allocation modules: 66 passed,
zero skips, 86.14 seconds pytest / 91.61 preparation-cleanup inclusive.
Exactly two work-stealing workers retain the 30-second default item
watchdog, existing native deadlines/marks and resource limits under a
240-second diagnostic group. This includes real subprocess/kernel lease,
first-touch CLI, native pairing and tuple/historical-allocation consumers.
Ruff F/E9 and the 13-plugin install contract pass.
No quiet-host, whole-suite or repeated same-head qualification is claimed.

Documentation impact is this ledger only; runtime, command, installation
and configuration contracts remain unchanged. The fresh actual CLI check
is included above; provisioning clean-room and live external deployment
are inapplicable to this test-fixture-only change. Parent #5694/#5682 and
complete repeated same-head Windows qualification remain open.

### B-CONTEXT private context-resolution topology

**Actually merged:** #7644 as
`b724784e9325fd3d4e90d5fbb93e9812fe51351a`.

Baseline: the actual #7639 merge. Add only the named session
`_context_repo_seed`, `_copy_context_repo`, adapted `adopted_repo` and
`test_context_repo_copies_preserve_seed_and_sibling`. Construction uses the
existing hermetic HOME/Git/identity environment and run-local seed lock.
Each case copies real Git stores and repairs its relocated linked worktree;
project/repos registration, configuration and tracking remain fresh per case.
Actual CWD/project/worktree resolution is not cached or substituted.

Two repeated cases require exact private registration and tracking inputs,
real relocated roots and branch identity, then mutate the first copy's
branch, bare config, file and tracking bytes. Every seed and sibling file
remains unchanged. All 49 other existing functions and original decorators
are AST-identical; no production, selection, skip, timeout or resource-limit
change is included.

The complete affected module passes on native Windows: 49 passed, zero
skips, 19.16 seconds pytest / 31.00 preparation-cleanup inclusive, exactly
two work-stealing workers, unchanged 30-second item watchdog and resource
limits under a 240-second diagnostic group. Ruff F/E9 and the 13-plugin
install contract pass. This is functional evidence without a quiet-host,
whole-suite or repeated same-head performance qualification claim.

Documentation impact is this ledger only; runtime, command, installation and
configuration contracts remain unchanged. Provisioning clean-room and
external deployment are inapplicable to this test-only fixture slice.
Parent #5694/#5682 and complete repeated same-head Windows qualification
remain open.

### B-ADVANCED private upstream advancement for recovery consumers

**Actually merged:** #7639 as
`6170fd59e1a4cc4e2d3f2ba1790fc011fbd5b7ab`.

Baseline: the actual #7634 merge. Add only the named recovery seed,
module-private copied `pr_repo`, raw `legacy_pr_repo`, extracted upstream
advancement and setup adapter in `test_pr_recovery.py`. Construction uses the
existing hermetic HOME/Git/identity context and run-local seed locking.
It executes actual initial publication and upstream commit/push once.
The copy preserves the recorded old head/base and newer shared remote-tracking
default tip; synchronization/rebase/checkpoint/publication remains each case's
operation, not shared completed authority.

Unlike the preserved candidate's generic `Add feature` published input,
construction retains the original `Owned change` title and
`feature/owned-change-aaaa` branch. Legacy inputs remain unpublished raw
copies, so the actual custom-head publication, legacy record adjustment and
upstream advancement still occur independently in those cases.
Two adapter cases prove zero/one actual create calls for prepared/legacy
inputs, exact title/branch and legacy base/patch metadata.

Two repeated copy cases check exact published/source/base topology, relocated
record/remote identity, absent recovery checkpoints and private state.
Changing one copy's hook, config, source commit, remote ref and record leaves
all seed and sibling bytes unchanged. The existing unrelated-branch sync
consumer additionally preserves seed record and refs through actual sync and
publication. Existing recovery/refusal bodies remain unchanged apart from
that additive preservation check and the legacy fixture argument/input.
The separate atomic-retention failure stub remains on `git_ops.git`; no
A/R/P batching or dependent stub conversion is absorbed.

The complete affected module passes on native Windows: 21 passed, zero
skips, 191.28 seconds pytest / 198.56 preparation-cleanup inclusive, exactly
two work-stealing workers under a 240-second diagnostic group with unchanged
30-second default item watchdog, 180-second module mark and resource limits.
Fifteen existing function bodies and all original decorators are
AST-identical; the only other old-body changes are the named setup adapter,
legacy fixture input and additive seed-preservation assertions.
Ruff F/E9 and the 13-plugin install contract pass.
The anchor's actual push updates refs shared with its linked worktree.
The cases require the newer tip and the exact common original
base of the published and upstream commits, while source HEAD stays old.
No quiet-host, full-suite or repeated same-head qualification is claimed.

Documentation impact is this ledger only; runtime, command, installation and
configuration contracts remain unchanged. Provisioning clean-room and
external deployment are inapplicable to this test-only fixture slice.
Parent #5694/#5682 and repeated complete same-head native qualification
remain open.

### B-AUTH-PROFILE private authority installation profile

**Actually merged:** #7634 as
`af49d9ee8d183ff46097a7be789e06250114cc4d`.

Baseline: the actual #7559 merge. Add only the named function-autouse
`_isolated_authority_profile` fixture in `test_pr_publication_state.py`.
It points `cfg.install_dir` at the case's own temporary authority profile;
it does not substitute the authority guard, file lock, publication operation
or persisted record. Every existing test body and decorator remains unchanged,
including real subprocess contention, lock-through-I/O and post-wait
authority revalidation checks.

Two repeated cases require the exact private profile to start absent, acquire
the real guard, observe its actual lock file and retain only their own case
marker. No process/native boundary, test selection, skip, timeout, resource
limit, production or publication contract changes are included.

The complete affected module passes on native Windows: 23 passed, zero
skips, 74.19 seconds pytest / 95.43 including host launch, preparation and
owned cleanup. Exactly two work-stealing workers retain the 30-second default
item watchdog, original 120-second module mark and resource limits under
the 240-second diagnostic group. All 16 existing functions/classes and their
decorators are AST-identical. Ruff F/E9 and the 13-plugin install contract
pass. This is bounded functional evidence without a quiet-host or whole-suite
performance qualification claim.

Documentation impact is this ledger only; runtime, command, installation and
configuration documentation stays accurate. Clean-room provisioning and
external deployment are inapplicable to this test-only profile isolation.
Parent #5694/#5682 and complete repeated same-head Windows qualification
remain open.

### B-PARTIAL private accepted-head fixture and refusal consumers

**Actually merged:** #7559 as
`842038a9a54d0c085b19a28311c5ed337c0226bc`.

Baseline: the actual #7544 merge. Add only the named session
`_partial_success_seed` and per-case `partial_success_repo` in
`test_pr_rebase.py`. Construction uses the existing hermetic HOME/Git/identity
context, run-local seed lock and real lost-acknowledgement publication helper.
The seed contains the actual accepted remote commit, stale saved PR head,
rebased unpublished source and saved JSON inputs; copying repairs private
worktree/remote/record identity and restores the refspec config binding.

The four original refusal parameter cases consume independent copies.
Unavailable/different provider, unrelated foreign remote and racing update
checks retain real publication refusal, remote-head and stale-record
assertions. The foreign case additionally proves the provider is never
observed; every refused outcome preserves the immutable seed record and refs.
The positive recovery case still constructs its own real partial publication,
so fixture reuse cannot replace that behavior under test.

Two repeated-copy cases check exact saved JSON, old/accepted/rebased ancestry,
feedback messages/content, config and relocated ownership. Mutating a private
copy's JSON, hook, Git config, source commit, remote head and record leaves all
seed and sibling bytes unchanged. All other 30 existing function bodies and
all original decorators/parameter selections are unchanged. No production
batching, transport snapshot adaptation, R/P absorption, new skip, selection
or deadline change is included.

The complete partial-success family passes on native Windows: seven passed,
zero skips, 86.76 seconds pytest / 91.42 including preparation and cleanup.
Exactly two work-stealing workers retain the 30-second default item watchdog,
original module marks and resource limits under a 240-second diagnostic group.
Initial new copy-regression failures were incorrect ancestry expectations:
fresh publication squashes both schemes, leaving one `Own change` commit
before accepted and unpublished feedback, not the two original work commits.
The corrected regression checks the exact three-message sequence and base.
Ruff F/E9, unchanged-body AST comparison and the 13-plugin install contract
pass. This focused family is functional evidence, not full-module or
whole-suite qualification; the outstanding complete rebase-module budget
remains tracked by #5682.

The combined coordination window including normal commit/publication and
turn gaps exceeded its separate 240-second total cap: 15,990.83 seconds
at release. The original message claiming that total was under 240 was
explicitly corrected to both peers. No wait time was excluded and no
total-window, quiet-host or whole-suite performance credit is taken.
The 91.42-second functional test-group result is separate.

Documentation impact is this ledger only: command, runtime, installation and
configuration contracts remain unchanged. Provisioning clean-room and external
deployment are inapplicable to this test-only fixture conversion. Parent
#5694/#5682 and repeated complete same-head native qualification remain open.

### B-FORK prepared helper and rerun consumers

**Actually merged:** #7544 as
`855ffa066206401e0b02bfddfac375ca5dc5ca1c`.

Baseline: the actual #7539 foundation merge. The existing
`pr_workflow_setup.PRWorkflowSetup._fork_headed_rerun` helper recognizes the
private prepared fork record/directory and verifies its feedback file before
returning. Every unprepared input retains the original real `create_pr`,
fork migration and feedback-commit path, including already-published inputs.
This slice does not absorb the independent published-input shortcut from
the preserved source.

Eleven named fork cases stay at their original `test_pr_ops.py` paths and
select `fork_pr_repo` instead of the unpublished base. AST comparison
confirms all 38 existing functions, names, decorators and bodies are
unchanged after normalizing only the fixture argument and corresponding
helper input. Later actual rerun publication, remote repoint, push-URL,
locked identity, ownership and exact-lease refusal assertions remain real.
No transport stub, P-dependent snapshot adaptation, production function,
test selection, skip or deadline changes.

Five adapter cases cover unpublished, already-published and prepared-fork
inputs plus missing/changed prepared feedback. The first two paths call the
actual publication once and build the original topology. The prepared path
performs no publication or file mutation; malformed prepared feedback fails
before any publication and leaves all private files unchanged.

Both complete affected modules pass on native Windows in separate bounded
groups: fixture/adapter 24 passed, zero skips, 61.86 seconds pytest / 74.39
including preparation and cleanup; PR consumers 33 passed, zero skips,
64.21 / 69.50 seconds. Exactly two work-stealing workers, the 30-second
default item watchdog and unchanged marks/resource limits are retained
under the 240-second diagnostic group. AST preservation, Ruff F/E9 and
the 13-plugin install contract pass. These are functional diagnostics,
not whole-suite budget qualification.

Documentation impact is this ledger only; command, runtime, installation
and configuration contracts remain unchanged. Provisioning clean-room and
external deployment are inapplicable to this test-only adapter. Real-Git
consumer/adapter contracts exercise the changed setup. Parent #5694/#5682
and full repeated same-head native qualification remain open.

### B-FORK foundation: private fork-only head, no consumer conversions

**Actually merged:** #7539 as
`e7583912a79a6a14d5c7025afd4526d7e06d4ec2`. Helper and consumer
conversions are not included in that merge.

Baseline: the actual #7535 consumer merge. Add only `_fork_pr_seed` and
`fork_pr_repo`, using the existing private published snapshot and hermetic
HOME/Git/identity context under run-local seed locking. The prepared topology
keeps the old publication only in the copied fork, not origin; the fork has
no default-branch head. The private worktree contains one unpublished
feedback commit on top of the old snapshot. The persisted legacy record
selects `fork` and has no rewrite ownership/identity.

Two repeated-copy regression cases assert the exact old head, feedback
parent/message/content, snapshot scheme, config/load binding, relocated
record and origin/fork URLs, missing origin publication and missing fork
default head. A second private sibling has the same initial history and
independent physical fork files. Changing the first copy's fork config,
hook, source commit, published head and record leaves every seed and sibling
byte unchanged. Existing fixture/test functions and decorators are unchanged;
no helper adapter, fork consumer or transport stub changes are included.

Both complete fixture-copy and seed-cache modules pass on native Windows:
48 passed, zero skips, 62.20 seconds pytest / 75.28 including preparation
and cleanup, exactly two work-stealing workers, 30-second default item
watchdog and unchanged resource limits under the 240-second diagnostic
group. Ruff F/E9, unchanged-existing-body AST comparison and the 13-plugin
install contract pass. This is not a whole-suite budget qualification.

Documentation impact is this ledger only. Runtime, command, installation
and configuration contracts do not change. Provisioning clean-room and
external deployment are inapplicable to this test-only foundation; real-Git
fixture-copy contracts exercise first construction and private copies.
No Linux/container or parent whole-suite qualification is claimed.
Consumer conversions and original #5694/#5682 remain open.

### B-REBASED rewrite and legacy consumers

**Actually merged:** #7535 as
`810bbc09c81e23516c94371f3155000dfcac5742`. B-FORK foundation
and consumer conversions are not included in that merge.

Baseline: the actual #7523 foundation merge. Twelve rewrite test functions
and four legacy-rewrite functions now take private completed real post-rebase
fixtures instead of invoking `publish_and_rebase` as unrelated setup.
Parameterized cases still select their actual snapshot/refspec scheme;
literal cases use the matching concrete fixture.

Only fixture arguments and the corresponding setup assignment change.
AST comparison confirms all 23 rewrite and seven legacy functions,
decorators, names and remaining bodies are unchanged. In particular, the
later squash, ordinary-push refusal, explicit confirmation, exact lease,
hook rejection, fork repoint, process race and record-generation checks
still exercise the real consumer operations. The three rewrite cases that
alter identity or destination before publication, or test legacy ownership
refresh, keep their actual `publish_and_rebase` calls. That helper and the
shared fixture implementation remain unchanged. The now-unused legacy
module helper import is removed.

No production batching, transport snapshot adaptation, record-only
conversion, new skip, test selection or deadline change is included.
Both complete consumer modules pass on native Windows in separate bounded
groups: rewrite 40 passed in 157.34 seconds pytest / 170.84 including
preparation/cleanup; legacy rewrite 21 passed in 55.63 / 59.38 seconds.
Both use exactly two work-stealing workers, the 30-second default item
watchdog and unchanged module marks/resource limits under the 240-second
diagnostic group. There are zero skipped cases. The rewrite run overlapped
unrelated artifact-extraction I/O; these are functional diagnostics, not
quiet-host measurements or a whole-suite budget qualification.

Documentation impact is this ledger only; runtime, command, installation
and configuration contracts do not change. Provisioning clean-room and
external deployment tests are inapplicable to this test-only conversion.
The real-Git consumer modules are the direct integration check. Parent
#5694/#5682 qualification remains open.

### B-REBASED foundation: private completed setup, no consumer conversions

**Actually merged:** #7523 as
`f56434e4564fb3680fffc3f52460c4c445df4b92`. Consumer conversions
are not included in that merge.

Baseline: the actual #7521 adapter merge. Add only
`_rebased_refspec_pr_seed`, `_rebased_snapshot_pr_seed`,
`_copy_rebased_pr_repo`, both concrete fixtures and `rebased_pr_repo`.
Both seeds invoke the existing unchanged
`test_pr_rewrite.publish_and_rebase` against a copied unpublished base.
That helper performs the actual first publication, advances the bare remote
and rebases the source while retaining the old published head. Session setup
uses the already-proven hermetic HOME/Git/identity context and run-local
seed lock, not ambient host configuration.

Four new cases (two schemes, two independent copies each) verify the old
record/remote publication head, different rebased source head, exact new
upstream parent and source/upstream content. They check copied config/load
bindings and destination identity, and preserve the old snapshot-only local
PR-head ref. Each makes a second sibling copy, then commits/pushes a new
private ref and updates only its own record; all seed and sibling bytes
must remain unchanged. Existing fixture tests and setup helper bodies are
unchanged. No rewrite/rebase consumer, test selection, skip, item mark,
deadline or production function changes.

Both complete fixture-copy and seed-cache contract modules pass on native
Windows: 46 passed, zero skips, 51.45 seconds pytest / 55.64 including
preparation/cleanup, exactly two work-stealing workers, 30-second default
item timeout and unchanged process/memory/temp limits under the 240-second
diagnostic group. This does not qualify the parent suite budget.
Documentation impact is this ledger only; runtime/command/install/config
contracts do not change. Provisioning clean-room and external deployment
are inapplicable to these test-only fixtures; the real-Git fixture contracts
exercise first construction and independent copies directly. No Linux or
container result is claimed. Consumer conversions remain a separate wave,
and parent #5694/#5682 remains open.

### B-PUBLISHED rebase setup adapter

**Actually merged:** #7521 as
`99d85042c87ff43d93aa94807f3d9dca528c9ae3`. B-REBASED foundation and
consumer conversions are not included in that merge.

Baseline: the actual #7509 finalization-consumer merge. This slice adds
only the module-private published-state adapter in `test_pr_rebase.py`.
Default/refspec cases copy the existing real refspec publication; snapshot
cases, including the legacy checked-out-head case, copy a separately built
snapshot publication with the original `Own change` title. The general
`Add feature` snapshot seed is deliberately not substituted: that would
change the published branch and commit subject. The new session seed uses
the existing run-local locking and hermetic HOME/Git/identity setup.

`_prepare` reuses only a matching scheme, title and derived branch when no
explicit branch is requested. Custom-branch cases copy the unpublished base
and still execute the real first publication. Four real-Git cases check both
schemes, title/branch/head/record preservation, snapshot-only local head refs,
and the absence or presence of the real `create_pr` call. Three refusal
canaries independently vary scheme, title and branch. AST comparison proves
all 21 original test bodies/decorators and other existing helpers unchanged.
There is no rebase-result cache, partial-success seed, production batching,
new skip or deadline change in this slice.

Native Windows diagnostics cover all 46 cases with exactly two work-stealing
workers. The setup/publication group passes 12 cases in 76.12 seconds
(80.38 including preparation/cleanup); the remaining groups pass 18, 10
and five non-native cases. The opt-in native consoleless two-cycle case
initially failed before observation because the cached test interpreter
lacked the newly declared `agent-worktree-identity` dependency. Restoring
that dependency, without source changes, lets that case pass in 13.78
seconds (16.80 inclusive). Every group stays below 240 seconds with the
existing resource limits and item marks. These are complete case diagnostics
across groups, not a single complete-module or whole-suite budget proof;
parent #5694/#5682 qualification remains open.

Documentation impact is confined to this execution ledger: no public command,
runtime, installation or configuration contract changes. Clean-room
provisioning and external deployment tests are inapplicable to this test-only
adapter. Its real publication/rebase consumers and native observer exercise
the changed setup directly; Linux/container evidence is not claimed.

### B-PUBLISHED finalization consumers

**Actually merged:** #7509 as
`f0f2cda72af7caeeefb6f66d5eff29945e7278dc`. Rebase setup adapters and
the remaining fixture/production contracts are not included in that merge.

Baseline: the actual #7497 B-MUX merge
`91e6b9bbea7e65e958a8903016e03a710dfe9d87`, following the
#7483 published-fixture foundation. Convert only the eleven named
`test_finalize_pr.py::TestPRFinalize` methods in the PUB inventory below.
Each substitutes the private published fixture argument/unpack and removes
its setup-only first `create_pr`. AST comparison proves all remaining body
statements, decorators and seven other cases unchanged.

The complete eighteen-case module and both mux-binding canaries pass
without skips on native Windows: 20 passed in 79.97 seconds, 91.20 including
preparation/cleanup. Exactly two work-stealing workers preserve the
30-second item, 240-second diagnostic group and existing resource limits.
Actual merge/reconciliation, dirty-state refusal and each race check remain
in their original test bodies. This is not whole-suite qualification.

Only this ledger needs documentation: runtime/command/install behavior
does not change. Provisioning clean-room and external deployment testing
are inapplicable to this test-only setup substitution; the native finalization
consumer module exercises the changed contract directly. Rebase setup
scheme/title preservation, landing reuse/consolidation and all other
residual contracts remain independent. Parent #5694/#5682 stays open.

### B-MUX: explicit no-mux boundary for Git-only finalization fixtures

**Actually merged:** #7497 as
`91e6b9bbea7e65e958a8903016e03a710dfe9d87`. The separate
B-PUBLISHED finalization substitutions are not included in that merge.

Add the opt-in `git_only_mux` fixture and apply it only to
`test_finalize_pr.py`, `test_finalize_diverged_branch.py` and
`test_finalize_landing.py`. These fixtures create Git repositories, not mux
sessions. Discovery and mux termination return absent without contacting the
host service. The handmade-PR landing case additionally checks the exact
worktree ID/path passed to its synthetic mux/process termination doubles.
Arguments are recorded and asserted after finalization returns, outside
production's broad cleanup exception handler; each expected call must occur
exactly once. Its real `validate_and_finalize` result assertion is unchanged.

Two regression cases put failing canaries behind the original discovery and
termination bindings, prove the fixture cannot reach either, and prove
teardown restores both. No native process/kernel/session-ownership module,
production function, subprocess admission rule or global autouse behavior
changes. These explicit Git-only doubles are not process-ownership evidence.

All four complete affected modules pass without skips on native Windows:
9 boundary/diverged cases, 18 PR-finalization cases and 24 landing cases
(51 total). Exactly two work-stealing workers retain 30-second item and
existing resource limits; diagnostic groups take 33.67/105.81/122.11 seconds
under the unchanged 240-second group bound, 266.05 seconds including aggregate
preparation/cleanup. Runtime clean-room/external deployment testing is
inapplicable to this test-only isolation boundary. No complete-suite
qualification is claimed.

Review found that an assertion inside the process-termination double was
swallowed by production's broad exception handler. After moving both argument
checks outside cleanup, all 26 boundary/landing cases pass without skips in
114.54 seconds. A disposable in-memory wrong-cleanup-path wrapper then makes
the real finalization case fail at the external path assertion, as intended
(31.12 seconds); no production file is edited. Both runs retain exactly two
workers and unchanged bounds; 156.70 seconds includes their preparation and
cleanup. The mutation helper is removed afterward.

Documentation impact is this ledger's explicit fixture scope and evidence
limits; no command/install/runtime behavior changes. B-PUBLISHED finalization
consumers remain unchanged in this slice, as do B-LANDING seed reuse and
assertion consolidation. Those follow independently after this boundary lands.
Parent #5694/#5682 and all remaining contracts stay open.

### B-PUBLISHED create/target/state consumers

**Actually merged:** #7490 as
`5ae60e3699ee2b8dd12128c39fd3d917479de98f`. Finalization and
rebase consumers are not included in that merge.

Baseline: the actual #7483 foundation merge
`a74f203c3927dd392878662bd66d70dfac6b2d01`. Convert exactly
27 named methods below: six in `TestCreatePR`, sixteen PUB-owned methods in
the retained `test_pr_ops.py::TestPRPublication`, and five in
`test_pr_publication_state.py`. Each changes only its fixture argument and
unpack, removing the setup-only first `create_pr` and its success assertion
where present. An AST comparison proves the remaining body and every
decorator unchanged. The five state methods consume an already-published
snapshot; their assertions concern provider/attribution/revision behavior,
not the setup title or branch spelling.

Both mixed PUB/LOAD methods retain their existing record reload behavior;
no B-OBSERVATION changes are included. Fork setup, transport stubs and all
subsequent native creates/pushes/refusals/locks remain under their original
case bodies. Fresh-publication controls still use `pr_repo`. The legacy
`test_pr_ops.py` path remains for exact-node selection compatibility.

All three complete affected modules pass without skips on native Windows:
31 create cases, 33 publication-target cases and 21 publication-state cases
(85 total). Their two-worker diagnostic groups take 117.31, 146.97 and
65.34 seconds, with 336.61 seconds including aggregate preparation/cleanup.
The 30-second item, 240-second diagnostic group and existing resource limits
are unchanged. No full-suite qualification is claimed.

The eleven finalization consumers await the independently owned B-MUX
fixture contract, while the rebase setup adapter needs its own scheme/title
preservation check. Neither is silently credited by this consumer slice.
Other fixture, production and selection waves and parent #5694/#5682 remain
open. This ledger is the documentation impact; runtime/install/command
behavior is unchanged. Provisioning clean-room and external-venue validation
are inapplicable to these test-only substitutions; real native Git/provider
boundary and race cases run in the complete affected modules.

### B-PUBLISHED foundation: private published topology, no consumer conversion

**Actually merged:** #7483 as
`a74f203c3927dd392878662bd66d70dfac6b2d01`. This settles only
the foundation; consumer conversions remain separate.

Baseline: the actual #7476 B-BASE merge
`42ea761856e212becd1a758df0f637f77fc72cd6`. This slice adds only
`_published_pr_seed`, `_published_refspec_pr_seed`, `published_pr_repo` and
`published_refspec_pr_repo`, together with their isolated session environment
and I's four published-copy cases. Existing consumers remain unchanged.
Snapshot and refspec seeds each execute the real initial `create_pr` once
under the run-local publication lock. Copies preserve the actual published
head and repair their private tracking path, remote slug and rewrite identity.
The refspec fixture returns and binds refspec configuration, not the base
copy helper's snapshot default.

Session setup precedes function-autouse isolation, so the shared setup context
reuses the existing HOME isolation generator, disables global/system Git config
and resident-monitor startup, and selects an absent identity key under that
temporary home. It restores all bindings on both success and failure. Two
native subprocess probes start with synthetic global signing/external-hook
configuration, a registry sentinel and an invalid operator key; actual seed
construction succeeds without using or changing that host-like state.

All 83 focused cases pass without skips on native Windows in 128.19 seconds
(134.27 inclusive of preparation and cleanup): the complete seed-cache,
base/published-copy and publication-check modules. Exactly two work-stealing
workers retain the 30-second item and unchanged resource limits under a
240-second diagnostic group. Ruff F/E9 and the thirteen-plugin install-contract
gate pass. This is fixture-contract evidence, not whole-suite qualification.
Clean-room provisioning and an external venue are inapplicable to this
test-only setup contract; the synthetic uncontained subprocess probes exercise
first-touch session setup directly.

Documentation impact: this ledger records the bounded test-fixture contract;
no runtime, command, installed behavior or CI selection changes. The remaining
B-PUBLISHED consumers, rebase/fork seeds and parent #5694/#5682 qualification
are explicitly unimplemented by this slice.

### B-BASE: private copies of the base Git topology

**Actually merged:** #7476 as
`42ea761856e212becd1a758df0f637f77fc72cd6`. This settles the base
copy contract only, not published/rebased/fork adoption or parent qualification.

Baseline: Dev `66ec88c76c013a7833a9c48a9602c094caf4ca4c`, the actual
#7471 B-SEED merge. Build the bare remote, anchor and two-commit linked
worktree once in the run-local seed. Each `pr_repo` call physically copies
all Git stores and repairs administrative links, origin/fork URLs and saved
record/rewrite identities. Config and tracking bindings remain per case.
Empty Git templates leave private hook directories; automatic maintenance is
disabled in this setup-only topology. Session construction temporarily ignores
global/system Git configuration, restoring the environment even on failure:
session fixtures run before function-scoped HOME isolation. No published/rebased/fork fixture
adoption or provider observation caching is included.

Only the existing `pr_repo` body changes; the other 25 existing conftest
function/class bodies are AST-identical. Existing test bodies, native
deadlines, parameters and tier/skip declarations are unchanged.
Five real-Git regressions cover private object files (no hardlinks or
alternates), repaired links, two-commit history, private config/index/hooks/
refs/tracking records, sibling isolation and byte-identical seeds.
Synthetic persisted PR inputs additionally test relocation with and without
a rewrite identity, stable stamped entry IDs, revision advancement and an
unrelated PR entry that must not be relocated. These inputs are not live
publication authority.

All 38 focused cases pass without skips on native Windows in 20.86 seconds
(25.33 inclusive): all five copy regressions, two config-isolation regressions,
all 29 merged publication regressions and unchanged record-only/real create-pr controls.
The native uncontained construction probe first observes synthetic global
signing/external-hook settings, builds without using either and observes the
unchanged restored settings afterward. Failure-path restoration is also covered.
Exactly two
work-stealing workers and unchanged 30-second item/resource limits apply,
with a 240-second diagnostic group. Ruff F/E9 and thirteen-plugin
install-contract checks pass.

Broader affected-consumer diagnostics are deliberately not qualification:
ten completed two-file groups pass 358 cases (maximum group 190.50 seconds
inclusive). The first diagnostic's 900-second aggregate expires in the next
group with 14.656 seconds remaining. A separate remaining-file diagnostic
then completes that group but reaches the unchanged 240-second limit in
the rebase pair. Neither incomplete diagnostic is a passing complete suite,
and no interrupted group or pre-existing exhaustive skip earns credit.
The #5682/#5694 budget obligation remains open for the subsequent fixture/
behavioral waves and repeated complete same-head qualification; limits and
selection are not relaxed here.

Fresh private Git construction/copy is directly exercised, including actual
publication and hook effects in the completed consumer groups. Deployment
clean-room testing is inapplicable to this test-only fixture; unavailable
Linux/external execution receives no credit. Next are separately scoped
published/rebased/fork setup consumers, not absorption of those contracts
into this base-copy wave.

### B-SEED: run-local seed publication only

**Actually merged:** #7471 as
`66ec88c76c013a7833a9c48a9602c094caf4ca4c`. This settles seed
publication only; adoption by copy fixtures and parent qualification remain open.

Baseline: Dev `2b3e36bc9f3bfae5b8fc692e627ecbd859cc7cc1`, the actual
#7465 B-RECORD foundation merge. Extract only `_seed_directory` and its
publication regressions from the pinned residual source. No existing fixture
uses this helper yet; B-BASE, published/rebased/fork copies and their isolation
regressions remain independently scoped.

Contained calls use the runner's private plugin sandbox, outside per-group
temporary directories. A mandatory cross-process sidecar lock spans build,
atomic readiness publication and consumption. Readiness must match the exact
version/key/path object, including an integer (not boolean or floating-point)
version. Invalid keys, missing/relative/non-directory sandbox roots,
symlink controls, incomplete builds and invalid readiness fail explicitly.
Uncontained calls retain independent `tmp_path_factory` directories.
These seeds hold test setup, never authority or mutable provider results.

All 25 pre-existing conftest functions/classes have identical AST bodies.
Twenty-nine seed-publication cases exercise independent group factories,
readiness shape/type/content failures, symlink predicates, missing published
directories, mandatory-lock failure, builder exceptions, abrupt process exit,
invalid keys and uncontained fallback. The real two-process test waits for an
observed failed native sidecar acquisition before allowing the builder to
finish: it proves actual contention, build-once publication and reuse after
group cleanup rather than relying on a startup-delay race.

All 31 targeted cases pass without skips on native Windows in 12.04 seconds
(15.11 inclusive): all 29 new publication cases, the unchanged record-only
construction regression and the unchanged real create-pr association control.
Exactly two work-stealing workers, 30-second items, the 240-second diagnostic
group and unchanged process/memory/storage limits apply. Ruff F/E9 and the
thirteen-plugin install-contract guard pass with zero violations. This is
focused contract evidence, not complete same-head Windows qualification.
Fresh private sandbox publication is exercised directly; no installer/runtime
behavior changes require a clean-room deployment. Linux/external execution
is unavailable and receives no credit. No production code, provider doubles,
existing fixture consumers, test tiers or CI admission changes are included.

### B-RECORD foundation: persisted records without publication setup

**Actually merged:** #7465 as
`2b3e36bc9f3bfae5b8fc692e627ecbd859cc7cc1`. This settles only the
foundation and named consumers below, not the remaining B-RECORD conversions
or the parent Windows qualification.

Baseline: Dev `8a70551c987775375310794e38d3c89aaee4607d`, the actual
#7457 final mechanical-family merge. Add function-private `record_repo` with
the existing base fixture's config, worktree identity and tracking bindings,
but no Git repository, commits, remote or publication. The local `recorded_pr`
fixture supplies explicit synthetic fork/head/base/patch inputs and stamps a
fresh frozen identity through the existing production helper. These inputs
are record-model test data, never live publication or merge authority.

Convert exactly 36 methods / 42 expanded cases: the fourteen
`TestSetPRAndStatus` methods except `test_set_pr_merges_with_create_pr`,
the nine B-RECORD `TestPRStatusLive` methods and four `TestPRThreads` methods
listed below, and all nine reassignment/stale-writer methods (fifteen cases)
now in `test_pr_record_reassignment.py`. The real create-pr association
control retains its original `pr_repo` and actual publication.

Complete real collection preserves every original 317 identity and adds two
fixture regressions. All original subject/assertion/decorator/parameter/mark/
base AST is preserved: only the named fixture arguments/unpacks and the
reassignment methods' setup-only `_fork_headed_rerun` calls change, with
additive missing-identity precondition assertions described below.
The other 275 original cases have identical full method and resolved-fixture
metadata. Collection passes take 23.92 and 13.61 seconds inclusive.

All 45 targeted cases pass without skips on native Windows in 17.38 seconds
(21.19 inclusive): all 42 converted consumers, both new construction
regressions that reject any Git invocation, and the retained real-publication
control. Exactly two work-stealing workers and unchanged 30-second item,
240-second diagnostic group and resource limits apply. Ruff F/E9 and the
thirteen-plugin install-contract guard pass with zero violations.
No provider double, production code, seed reuse, native-operation assertion,
test tier or CI admission changes enter this batch.

The shared synthetic fixture leaves `head_identity` absent from its first
persisted state. Record merging restores a nonempty durable identity, so
the case explicitly asserts both loaded snapshots lack it before the other
writer backfills. All 45 cases pass without skips in
18.18 seconds (22.17 inclusive); final collection takes 14.38 seconds inclusive.
A separate contained in-memory mutation suppressing only identity backfill
causes this case to fail at its original final preservation assertion, not
at the new preconditions (3.23 seconds pytest / 6.62 inclusive). This negative
control is expected failure evidence, not a passing suite or qualification.

This admits only the named B-RECORD foundation/consumers; other record-only
conversions in legacy/native-state files remain separately scoped. B-SEED,
B-BASE, B-PUBLISHED, B-REBASED and B-FORK are not implemented by this batch.
Fresh fixture construction is exercised directly in private native sandboxes;
installer clean-room provisioning is inapplicable and unavailable Linux/external
execution gets no credit. Repeated complete same-head Windows qualification
and the original #5694/#5682 completion gate remain open.

## Mechanical move mapping: preserved, not conflated with fixtures

### Final mechanical family: retain the legacy publication module path

**Actually merged:** #7457 as
`8a70551c987775375310794e38d3c89aaee4607d`. The original 317-case
mechanical family partition is complete; fixture-semantic contracts and
parent qualification remain open.

Baseline: Dev `98b77a6d2de17cec5c850b742ea1a0d08f7dab5e`, the actual
#7452 push-changes partition merge. The 33 remaining methods already form
one isolated publication-target family. Rename only
`test_pr_ops.py::TestPRFinalizeAndPush` to
`test_pr_ops.py::TestPRPublication`, with an accurate module docstring.
The complete class body AST and the merged `PRWorkflowSetup` stay identical.

Keep the existing module path deliberately. The exact-node consumer validates
that every requested baseline node's file exists; deleting this legacy module
would therefore refuse old selections before the new whole file could execute.
A still-existing changed whole file instead admits all replacement identities
without claiming their old coverage. File retirement is deferred to the
separate Wave 5 selection-compatibility contract, not hidden in a test move.
This refines the preceding whole-class-relocation proposal from observed
consumer behavior; no compatibility mechanism is changed here.

Real complete before/after collection proves all 317 nodes: 33 class-only
identity mappings and 284 unchanged, with identical method/decorator/base AST,
resolved fixture body/scope/closure, parameter values and effective marks.
Collection passes take 18.39 and 12.36 seconds inclusive of preparation/removal.
All 33 publication cases pass on native Windows without skips in 175.09 seconds
(180.89 inclusive), exactly two work-stealing workers and unchanged 30-second
item, 240-second diagnostic group and resource limits.

Four existing real-Git/contained selection controls pass without skips in
7.30 seconds (9.72 inclusive): both parameterizations of whole changed-file
replacement for retired baseline identities, plus the partial-split execution
and selection controls. No moved-node coverage is invented. Ruff F/E9 and
the thirteen-plugin install-contract guard pass.

All 317 original expanded cases now belong to bounded, cohesive mechanical
families: 284 move to dedicated modules and 33 remain at the legacy module
path under the publication class name. No production decision, helper, provider
double, fixture, seed, assertion, parameter, mark, tier or CI-admission change
enters this last mechanical slice. Clean-room provisioning is inapplicable;
unavailable Linux/external execution receives no credit. Fixture-semantic
contracts, selection compatibility and repeated complete same-head Windows
qualification remain open.

### Tenth admitted move: push-changes method family

**Actually merged:** #7452 as
`98b77a6d2de17cec5c850b742ea1a0d08f7dab5e`. The residual publication
family retains these merged bodies; fixture contracts and parent qualification
remain open.

Baseline: Dev `6177f31efc216f723e16a4d8670b01cb40b812c6`, after the actual
#7450 record reassignment partition merge. Thirteen methods move unchanged
from `test_pr_ops.py::TestPRFinalizeAndPush` to
`test_push_changes.py::TestPushChanges`: all `test_push_changes_*` methods
and `test_an_unreachable_recorded_fork_fails_push_changes_cleanly`. Their
existing parameterization expands to fifteen cases. Both classes retain the
identical merged `PRWorkflowSetup`; only newly unused imports and empty
headings/spacing disappear from the retained module.

Real before/after collection proves the complete 317-node bijection:
15 module/class-only moves and 302 unchanged identities, with identical
method/decorator/base AST, fixture body/scope/closure, parameter value and
effective mark. Complete collection passes take 18.89 and 12.86 seconds
including sandbox preparation and removal.

All fifteen moved cases pass on native Windows without skips in 97.31
seconds (101.72 inclusive), exactly two work-stealing workers and unchanged
30-second item, 240-second diagnostic group and resource limits. The two
existing real-Git/contained partial-split selection controls pass without skips
in 5.28 seconds (7.70 inclusive), preserving both changed whole files without
invented moved-node coverage credit. Ruff F/E9 passes; the install-contract
guard covers thirteen plugins with zero violations.

No production decision, helper, provider double, fixture, seed, assertion,
parameter, mark, tier or CI-admission change enters this partition. Clean-room
provisioning is inapplicable to unchanged relocation; unavailable Linux/external
execution receives no credit. Ten mechanical partitions now move 284 original
expanded cases, leaving 33 publication-target cases. Their whole-class
relocation, fixture contracts and repeated complete same-head Windows
qualification remain open.

### Ninth admitted move: record reassignment and stale-writer method family

**Actually merged:** #7450 as
`c3bfeb88e56c70a671c109c82ee1bde24d5c0c21`. Subsequent partitions retain
these merged bodies; fixture contracts and parent qualification remain open.

Baseline: Dev `58caadeacd0c1f0af8ee0f618e85b9dcb80f477e`, the actual
#7444 finalization partition merge. Nine methods move unchanged from
`test_pr_ops.py::TestPRFinalizeAndPush` to
`test_pr_record_reassignment.py::TestPRRecordReassignment`: all
`test_set_pr_*` and `test_a_stale_writer_*` methods. Their existing
parameterization expands to fifteen cases. Both classes retain the identical
merged `PRWorkflowSetup`.

Real before/after collection proves the complete 317-node bijection:
15 module/class-only moves and 302 unchanged identities, with identical
method/decorator/base AST, fixture body/scope/closure, parameter value and
effective mark. Complete collection passes take 22.22 and 12.88 seconds,
including sandbox preparation and removal.

All fifteen moved cases pass on native Windows without skips in 88.96
seconds (93.73 inclusive), exactly two work-stealing workers and unchanged
30-second item, 240-second diagnostic group and resource limits. The two
existing real-Git/contained partial-split selection controls pass without skips
in 5.05 seconds (7.42 inclusive), preserving both changed whole files without
invented moved-node coverage credit. Ruff F/E9 passes; the install-contract
guard covers thirteen plugins with zero violations.

No production decision, helper, provider double, fixture, seed, assertion,
parameter, mark, tier or CI-admission change enters this partition. Clean-room
provisioning is inapplicable to unchanged test relocation; unavailable
Linux/external execution receives no credit. Nine mechanical partitions now
move 269 original expanded cases, leaving 48 publication/push cases.
Their independent partitions, fixture contracts and repeated complete
same-head Windows qualification remain open.

### Eighth admitted move: finalization and reconciliation method family

**Actually merged:** #7444 as
`58caadeacd0c1f0af8ee0f618e85b9dcb80f477e`. Subsequent partitions retain
these merged bodies; fixture contracts and parent qualification remain open.

Baseline: Dev `07b22c1f5f8b4ba114de3241756565431e2cf3bc`.
Eighteen methods move unchanged from
`test_pr_ops.py::TestPRFinalizeAndPush` to
`test_finalize_pr.py::TestPRFinalize`: all `test_precondition_*`,
`test_finalize_*` and `test_reconcile_*` methods, plus
`test_push_changes_refuses_direct_to_master` and
`test_create_pr_path_still_works_when_required`. Both classes retain the
identical merged `PRWorkflowSetup` base.

Real before/after collection proves the complete 317-node bijection:
18 module/class-only moves and 299 unchanged identities. Every collected
method/decorator AST, inherited base body, resolved fixture body/scope/closure,
parameter value and effective mark is identical. Both complete collection
passes include sandbox preparation and removal: 17.38 and 11.84 seconds.
Only now-empty section headings and their inert class-body string are removed
from the retained module.

All eighteen moved cases pass on native Windows without skips in 120.07
seconds (124.30 including sandbox preparation and removal), exactly two
work-stealing workers with unchanged 30-second item and 240-second diagnostic
group limits. The two existing real-Git/contained partial-split selection
controls pass without skips in 4.17 seconds (6.67 inclusive), preserving both
changed whole files without invented moved-node coverage credit.
Ruff F/E9 passes; install-contract checks cover thirteen plugins with zero
violations.

No production decision, helper, provider double, fixture, seed, assertion,
parameter, mark, test-tier or CI-admission change enters this partition.
Clean-room provisioning is inapplicable to unchanged test-method relocation;
unavailable Linux/external execution receives no credit. Eight mechanical
partitions now move 254 original expanded cases, leaving 63 publication/push
cases. Their independent partitions, fixture contracts and repeated complete
same-head Windows qualification remain open.

### Seventh admitted move: source-attribution refresh method family

**Actually merged:** #7435 as
`d936b46029ba8b25bd91f755d7966e2fd38287f4`. The remaining mechanical
partitions start from these merged bodies; fixture semantics and complete
parent Windows qualification remain separate.

Baseline: Dev `039208cd6022e3cedb42b76f69a5d5ddb28dcb6c`, the actual
#7427 setup-helper merge. Ten methods move unchanged from
`test_pr_ops.py::TestPRFinalizeAndPush` to
`test_pr_source_attribution.py::TestPRSourceAttribution`. The method set is
`test_push_changes_refreshes_marker_and_preserves_body`, all six
`test_refresh_source_attribution_*` methods, and the
`test_frozen_codename_marker_*`, `test_explicit_config_addition_*` and
`test_finish_auto_open_rerun_*` methods. Both classes inherit the identical
merged `PRWorkflowSetup`; no helper or fixture changes enter this partition.

Real before/after collection proves the complete 317-node bijection:
ten module/class-only moves and 307 unchanged identities, with every
method/decorator AST, resolved fixture body/scope/closure, parameter value
and effective mark identical. Both collection passes and cleanup take
29.42 seconds. Only unused imports are removed after relocation.

All ten moved cases pass on native Windows without skips in 28.86 seconds
(32.27 including sandbox preparation and removal), exactly two work-stealing
workers with unchanged 30-second item and 240-second diagnostic group
limits. The two existing real-Git/contained partial-split selection controls
pass without skips in 5.07 seconds (7.14 inclusive), preserving both changed
whole files without invented moved-node coverage credit.

No production decision, native operation, assertion, provider double, shared
fixture, seed, test-tier or CI admission changes. Clean-room provisioning is
inapplicable to unchanged test-method relocation; unavailable Linux/external
execution receives no credit. Seven mechanical partitions now move 236
original expanded cases, leaving 81 finalize/push cases. Subsequent method
partitions, fixture contracts and repeated complete same-head Windows
qualification remain open.

### Sixth admitted move: complete role-resolution class

Baseline: Dev `26db968a27bdfd4a5c0b88fbbe4a1c17ffd1c8b5`, the actual
#7419 B-ROLE prerequisite merge. Move the complete, corrected
`TestCreatePRRoleResolution` class from `tests/test_pr_ops.py` into
`tests/test_create_pr_roles.py`; every method and helper AST is identical.
Only unused section headings disappear from the retained module.

Real before/after collection proves the full 317-expanded-node bijection:
five module-only moves and 312 unchanged identities, with identical fixture
bodies/scopes/closures, parameter values and effective marks. Native
Windows executes all five moved cases without skips in 22.74 seconds with
exactly two work-stealing workers and unchanged 30-second item and
240-second diagnostic group limits. Both full collection passes plus
execution, sandbox preparation and final cleanup take 53.62 seconds.
The two existing real-Git/contained partial-split controls pass without
skips in 5.12 seconds (7.44 inclusive), preserving both changed whole files
without invented moved-node coverage credit. Install-contract checks cover
thirteen plugins with zero violations.

No production behavior, shared fixture, provider correction, assertion,
parameter, mark, test-tier or CI-admission change is included. Clean-room
provisioning is inapplicable to a test-only whole-class move; unavailable
Linux/external execution receives no credit. Six mechanical partitions
now move 226 original cases; the setup-bearing finalize/push class retains
91. Its helper extraction, method partitions, fixture contracts and the
complete same-head Windows qualification remain open.

### Independent finalize/push setup-helper extraction

**Actually merged:** #7427 as
`039208cd6022e3cedb42b76f69a5d5ddb28dcb6c`. The method partitions below
start from these unchanged shared helper bodies; fixture-semantic ownership
and complete parent Windows qualification remain separate.

Baseline: Dev `7da41c6d06464012088b4e311e476144c4f3bfb0`, the actual
#7421 role-class merge. This extraction is independent of role-provider
semantics and changes only the finalize/push setup-helper location.

Extract only the finalize/push class's eight setup helpers into a shared,
non-test `PRWorkflowSetup` base. Every helper and test-method AST is identical;
the static method remains static. Real before/after collection preserves all
317 expanded identities and their resolved fixture bodies/scopes/closures,
parameter values and effective marks. It does not change any shared fixture,
provider double, seed, assertion, parameter, mark or production decision.

All 60 expanded consumers of those helpers pass on native Windows without
skips in six bounded ten-node groups, exactly two work-stealing workers:
71.53, 67.81, 91.83, 75.00, 75.05 and 63.89 seconds including each sandbox's
preparation and cleanup; 445.11 seconds in aggregate. The unchanged
30-second item, 240-second diagnostic group and resource bounds remain.
Ruff F/E9 passes; install-contract checks cover thirteen plugins with zero
violations. Both complete collection passes and cleanup took 25.69 seconds.
Clean-room provisioning is inapplicable to unchanged test-helper relocation;
unavailable Linux/external execution receives no credit. This prepares
separately reviewable method partitions; it is not those partitions or the
parent repeated complete same-head Windows qualification.

### B-ROLE prerequisite: current provider-result and review-exception contract

**Actually merged:** #7419 as
`26db968a27bdfd4a5c0b88fbbe4a1c17ffd1c8b5`, after exact-head full Copilot
approval and green required CI. Its child is finalized. The following
mechanical move starts from these corrected merged bodies; it does not
replay the provider correction.

The unchanged five-case `TestCreatePRRoleResolution` class in
`tests/test_pr_ops.py` reproduced four passes and one failure on native
Windows: its auto-open double returned a `SimpleNamespace` without
`head_sha`, so the real publication consumer raised `AttributeError`.
The double also retained the retired two-value `pull_review_gate` API.

This independent compatibility correction returns the actual `PullResult`
type with a distinct provider-reported head, implements the current
three-value `pull_review_exception` API, and asserts the exact
`(repo, number, expected_head_sha)` observation. The existing effective-role,
single-policy-read and bypass-note assertions remain. No production code,
class location, fixture seed, test tier, assertion removal or merge
permission changes.

Native Windows passes all five role cases and eight existing bypass-note
controls without skips: 13 passed in 28.15 seconds (32.03 including sandbox
preparation and cleanup), with exactly two work-stealing workers and the
unchanged 30-second item and 240-second diagnostic group limits.
Provisioning clean-room validation is inapplicable to a test-double-only
correction; Linux/external execution is unavailable and receives no credit.
The separate mechanical role-class move and complete same-head Windows
qualification remain open.

### Fifth admitted move: remaining attribution and tracking leaf classes

**Actually merged:** #7411 as
`5b466ebe68b0440e5c044118bdb3d5c7cffa29a9`, after exact-head full Copilot
approval and green required CI. Its child is finalized. The publication
rebase was followed by exact-head native reruns of all 46 cases without
skips: ten attribution cases in 16.16 seconds (22.11 inclusive), and 36
tracking cases in 75.92 seconds (82.02 inclusive). The parent qualification
remains open.

Baseline: Dev `f90914e793cae7c729f7bf293ccb083529f9d066`, the actual #7405
merge. This M-MOVE partition relocates eight complete leaf classes, leaving
only `TestCreatePRRoleResolution` and the setup-bearing
`TestPRFinalizeAndPush` in the monolith. No class is split in this partition.

For every method, only the module component changes:
`tests/test_pr_ops.py::<class>::<method><parameters>` ->
`tests/<destination>::<class>::<method><parameters>`.

| Destination under `tests/` | Whole classes | Expanded nodes |
|----------------------------|---------------|---------------:|
| `test_pr_attribution.py` | `TestAuditAttributionRisk`, `TestAttributionAuditCLI` | 10 |
| `test_pr_tracking.py` | `TestSetPRAndStatus`, `TestMultiPR`, `TestWorktreeToDictPRs`, `TestWorktreeToDictClaimsSummary`, `TestWorktreeToDictState`, `TestClassifyRecordsConvo` | 36 |

Real before/after collection proves the exact 317-node bijection: 46 moved
and 271 unchanged, with identical resolved fixture bodies/scopes/closures,
parameter values and effective marks. Every original top-level definition
remains exactly once with an identical AST. Only unused imports and orphan
section headings are removed from the retained module. No shared fixture,
native Git operation, assertion, test tier, production authority or CI
admission changes. B-ROLE and finalize/push helper extraction remain separate.

Native Windows with exactly two work-stealing workers passes every moved
case without skips: ten attribution cases in 19.12 seconds (23.09 inclusive),
and 36 tracking/listing cases in 73.12 seconds (78.64 inclusive). Unchanged
30-second item and 240-second diagnostic group limits apply. The nine
existing real-Git/contained selection controls pass without skips in
26.16 seconds (28.70 inclusive), retaining conservative whole-file
selection and strict unknown-node refusal outside those files. The
install-contract guard reports thirteen plugins and zero violations.

Clean-room provisioning is inapplicable to this test-only whole-class move.
No unavailable Linux/external result or dependency/service installation is
claimed. Five mechanical partitions now move 221 original expanded nodes,
leaving 96 in the two retained classes. Fixture semantics, setup-bearing
class decomposition, root-policy compatibility and repeated complete
same-head Windows qualification remain open; these focused passes do not
qualify the parent.

### Fourth admitted move: fork confirmation and identity flow

**Actually merged:** #7405 as
`f90914e793cae7c729f7bf293ccb083529f9d066`, after exact-head full Copilot
approval and green required CI. Its child is finalized. Subsequent
mechanical moves preserve those merged bodies; the parent qualification
remains open.

Baseline: Dev `de3652abbe3da7c0bd5c11299a0a7da0a84dc97d`, including the
actual #7402 merge. This M-MOVE partition moves only the complete
`TestCreatePRForkFlow` class into `tests/test_create_pr_fork.py`.
The adjacent role-resolution class and its B-ROLE provider correction
remain separate; no fixture correction is folded into this move.

For every method, the bijection is
`tests/test_pr_ops.py::TestCreatePRForkFlow::<method><parameters>` ->
`tests/test_create_pr_fork.py::TestCreatePRForkFlow::<method><parameters>`.
Real before/after collection proves all 317 expanded nodes: 18 moved and
299 unchanged, with identical fixture bodies/scopes/closures, parameters
and effective marks. Every original top-level definition remains exactly
once with an identical AST. No shared fixture, native operation, assertion,
test tier, production behavior, authority decision or CI admission changes.

Native Windows execution passes all 18 moved cases without skips, using
exactly two work-stealing workers and the unchanged 30-second item and
240-second diagnostic group limits: 87.21 seconds, 91.81 including sandbox
preparation/removal. The nine existing real-Git/contained split-selection
controls pass without skips in 27.58 seconds (30.03 inclusive), preserving
whole-file selection and strict refusal of unknown nodes elsewhere.
The install-contract guard reports thirteen plugins and zero violations.
Clean-room provisioning is inapplicable to a test-only whole-class move;
no unavailable Linux/external result or dependency installation is claimed.

The three earlier moves account for 157 expanded nodes; this partition
brings that total to 175, leaving 142 in the monolith. Remaining leaf
moves, the setup-bearing finalize/push class, fixture semantics, current
root-policy compatibility and repeated complete same-head Windows
qualification remain open. Focused passes do not satisfy the parent gate.

### Third admitted move: snapshot creation, refspec and head privacy

**Actually merged:** #7402 as
`48e548aee22be8a2ff8df74ef97ca25f5dbc0fbc`. Its exact-head full Copilot
approval, green required CI and managed merge are complete; the child was
finalized. Subsequent moves start from these merged bodies, without claiming
the complete parent Windows qualification.

Baseline: Dev `7c5b1631026e01a6f45cd73b61517475287f7aba`, the actual #7395
merge. This M-MOVE partition relocates four complete classes and changes no
class/function AST, fixture, parameter, assertion or effective mark.

For every method below, the mapping is
`tests/test_pr_ops.py::<class>::<method><parameters>` ->
`tests/<destination>::<class>::<method><parameters>`. Every other original
node remains unchanged, including all previously moved modules.

| Destination under `tests/` | Whole classes | Expanded nodes |
|----------------------------|---------------|---------------:|
| `test_create_pr.py` | `TestExistingFeaturePush`, `TestCreatePR` | 31 |
| `test_create_pr_refspec.py` | `TestCreatePRRefspec`, `TestCreatePRBranchLeakGuard` | 16 |

Real before/after collection proves the exact 317-node bijection: 47 moved,
270 unchanged, with identical resolved fixture bodies/scopes/closures,
parameter values and effective marks. Every original top-level definition
remains exactly once with an identical AST. No fixture seed, production
decision, test tier or Source-CI admission changes. The stale role-resolution
provider double remains in its separately owned B-ROLE prerequisite rather
than being silently repaired in a mechanical move.

Native Windows execution passes every moved case without skips using exactly
two workers: 31 snapshot/push cases in 131.10 seconds (136.47 including
sandbox preparation/removal), and 16 refspec/privacy cases in 73.36 seconds
(78.23 inclusive). The 30-second item and 240-second diagnostic group bounds
remain unchanged. The nine existing real-Git/contained split-selection
controls also pass without skips in 17.35 seconds (19.69 inclusive);
whole-file selection retains both old and new modules without inventing
measured-node credit, and unknown nodes elsewhere still fail.
The install-contract guard reports thirteen plugins and zero violations.

An accidentally broader root-module diagnostic additionally reports
75 passes, 18 existing tier skips and one failure:
`tools/test_coverage_selected_execution.py::test_ci_pilot_is_pr_only_and_does_not_increase_job_deadline`
still indexes the retired `smoke` job. This is the current-policy
compatibility residual already assigned to wave 5 and #6237, not a reason
to restore integration CI, add skips, change this partition's selection
controls or claim the root modules passed. The diagnostic is retained with
that owner separately from the nine focused selection results.

This test-only move has no fresh-install behavior to exercise. Linux and
external-venue results are not claimed: the available Linux environment has
no pytest installation and Docker is unavailable. No dependency or service
was installed. Remaining mechanical moves, fixture semantics, root-policy
compatibility and repeated complete same-head Windows qualification remain
open; these focused passes are not the parent performance proof.

### Second admitted move: CLI, claims and live status

**Actually merged:** #7395 as
`7c5b1631026e01a6f45cd73b61517475287f7aba`. The child worktree was
finalized after the exact-head full Copilot approval and required CI.
The following mechanical partition starts from those merged test bodies;
the complete same-head Windows qualification remains open.

Baseline: Dev `54c7879cfe9680dd87a02242a846081065a82cea`, including the
separately merged #7394 fixture correction. This M-MOVE partition relocates
nine complete leaf classes with every class/function AST unchanged; it
does not replay fixture changes or split a setup-bearing class.

For every method in the classes below, the bijection is
`tests/test_pr_ops.py::<class>::<method><parameters>` ->
`tests/<destination>::<class>::<method><parameters>`. The entire parameter
suffix, decorator list, signature, fixture closure and effective marks
remain unchanged. All other original nodes, including the 35 helpers
already moved by #7386, retain their identities.

| Destination under `tests/` | Whole classes | Expanded nodes |
|----------------------------|---------------|---------------:|
| `test_pr_claims.py` | `TestPrClaimHelpers`, `TestAbandonPr`, `TestReconcileActivePrSelfHeal` | 34 |
| `test_pr_status.py` | `TestRefreshHeadObservation`, `TestSelfMergeBypassNote`, `TestPRStatusLive`, `TestPRThreads` | 37 |
| `test_create_pr_cli.py` | `TestCreatePRCLIPolicyError`, `TestCreatePRCLIArgs` | 4 |

AST comparison retains every original definition exactly once across the
retained monolith and three destinations, including #7394's now-correct
provider doubles. Real before/after pytest collection has the exact
317-node bijection: 75 moved and 242 unchanged, with identical fixtures,
parameters and marks. The earlier failed attempt is not credited.
No fixture reuse, setup change, assertion change, new mark, tier admission,
production change or budget adjustment is included.

Native Windows two-worker execution passes every moved case without skips:
34 claims cases in 3.47 seconds (7.24 inclusive), 37 status cases in
35.65 seconds (40.36 inclusive), and four CLI cases in 13.80 seconds
(17.69 inclusive). Each diagnostic group retains the 30-second item and
240-second supervisor limits. Nine real-Git/contained selection controls
also pass, without skips, in 12.17 seconds. The retained monolith and three
new modules remain conservative whole-file selections; unchanged strict
controls refuse unknown nodes outside selected whole files. No old node
is renamed into fabricated measured coverage.

The install-contract guard reports thirteen plugins and zero violations.
This test-only move requires no clean-room provisioning, and no Linux or
external-venue result is claimed. The remaining mechanical classes,
immutable fixtures and parent complete same-head Windows qualification
remain separate open work.

### Corrective prerequisite: live-status provider doubles

**Actually merged:** #7394 as
`54c7879cfe9680dd87a02242a846081065a82cea`. This independent fixture
correction is no longer a prerequisite blocker. Further mechanical moves
start from its merged bodies, not the historical obsolete doubles.

After #7386, native validation of the next mechanical partition found
`TestPRStatusLive::test_live_self_merge_note_surfaced_when_bypassable`
failing on Dev `8f2f495df6b30855f38ca95c07a9301b8e482f6a`.
The three adjacent live-status note cases still implemented the retired
`pull_review_gate` double, whereas #7232's actual consumer requires
`pull_review_exception` with the expected head and safety filters.
This is a newly observed fixture discrepancy, not a reclassification of
the immutable census or permission to mix fixture semantics into M-MOVE.

The mechanical extraction was reversed before publication. Its separate
corrective prerequisite changes only those three test doubles and their
synthetic repository-token seam in the retained `test_pr_ops.py`:

- The bypassable case returns the current authorized/safe/reason tuple and
  verifies the actual repository, PR number, expected head, hold labels,
  WIP prefixes, endpoint and synthetic token passed by `pr_status`.
- The unauthorized case returns an explicit refusal, requires the same
  observed inputs and retains the no-note assertion. It cannot pass merely
  because an obsolete double was never invoked.
- The non-self-merge case retains its no-note assertion and fails if the
  review-exception method is consulted at all.

All production merge authority and safety decisions remain unchanged.
The independent correction must actually merge before retrying the
CLI/claim/status mechanical move from fresh Dev. Every original method,
parameter and fixture remains present; no skip or assertion removal is
authorized. The original 900/240-second parent qualification stays open.
Native Windows validation of all live-status, bypass-note, head-observation,
thread and create-PR CLI cases passes: 41 cases without skips in 43.32
seconds, 47.42 including sandbox preparation/removal, with exactly two
workers and unchanged 30-second item and 240-second diagnostic group bounds.
The install-contract guard reports thirteen plugins and zero violations.
This is a fixture/consumer correction, not a performance qualification or
production authority change; clean-room installation is inapplicable, and
no unavailable Linux/external environment is credited.

### First admitted move: naming, body and patch-identity helpers

**Actually merged:** #7386 as
`8f2f495df6b30855f38ca95c07a9301b8e482f6a`. The 35 helper cases and
unchanged shared `_git` relocation below are no longer residual. Remaining
M-MOVE classes and all fixture-semantic partitions stay open.

Baseline: current Dev `422b4827e9b58d3bf04699415c0b3845a99d8e3c`.
This is the M-MOVE helper partition only: seven whole classes move from
`tests/test_pr_ops.py` into `tests/test_pr_helpers.py`; `_git` moves
unchanged into `tests/pr_test_helpers.py`, imported by both consumers.
No production behavior, fixture, class setup, parameter, test assertion or
test mark changes. In particular, the historical informational contract mark
is not added in this strictly mechanical slice. The native Git cases remain
native Git cases, not units admitted to required Source CI.

The bijection is literal: for **every** `test_*` method in each class below,
`tests/test_pr_ops.py::<class>::<method>` becomes
`tests/test_pr_helpers.py::<class>::<method>`. Class and method names,
signatures, decorators, parameter values, helper methods and assertions are
unchanged. Every other node remains under its original identity.

| Class | Moved collected nodes |
|-------|----------------------:|
| `TestSlugify` | 5 |
| `TestRequiredBodySections` | 8 |
| `TestFeatureBranchName` | 2 |
| `TestWorktreeSuffixHelper` | 2 |
| `TestResolveHeadPattern` | 4 |
| `TestPRHeadName` | 11 |
| `TestPatchId` | 3 |

AST comparison against the baseline requires exactly one identical
class/function body for every original top-level definition across the three
resulting modules. Real pytest collection before and after requires the same
317 parameter-expanded nodes after this explicit 35-node mapping, with
identical fixture closure, parameters and effective marks for every node.
This is stronger than the historical census's unexpanded 308-name count.
Contained native Windows execution of all 35 moved cases and the 11 remaining
`TestExistingFeaturePush`/`TestCreatePRRefspec` consumers passes with exactly
two workers and unchanged 30-second item and 240-second diagnostic group
limits (46 passes, 63.36 seconds). This is not parent qualification.

Conservative coverage selection retains both changed test modules as whole
files; it does not rewrite old measured node identities or award new-node
coverage. The real-Git partial-split regression in
`tools/test_coverage_selected_execution.py` checks those exact boundaries.
The real contained partial-split consumer regression in
`tools/test_plugin_test_selection.py` executes both moved and remaining cases
through whole-file selection, while its existing unknown-node controls still
refuse nodes outside those explicitly selected files. Whole-file execution
replaces stale node evidence; it never credits the old node as collected.
The later COVERAGE batch owns upstream-drift reconciliation.
Shared fixtures, publication stubs, observation/role changes,
all remaining class moves and the full Windows qualification remain excluded.
The two focused producer cases and seven real contained consumer cases pass
on native Windows (nine passes, no skips, 17.61 seconds); unchanged install
contract validation reports zero violations across thirteen plugins.
Clean-room provisioning is inapplicable to this test-only relocation.
No Linux or external-venue result is claimed: the available Linux host has
no pytest environment, and the Docker engine is unavailable. No service or
dependency was installed for this slice.

AST-only inspection of the historical `test_pr_ops.py` and the twelve
destination modules finds **308 old test method/function names and 308 new,
no missing names, no duplicate names and no new names**. Of those, 220
test AST bodies (including decorators and signature) are identical; **88
are changed**. This is not collection/parameter parity, because fixture
selection, inherited setup and module-level marks/imports also matter.
The removal is M-MOVE; new-file mixed ownership is MX-MOVE, with the 88
changed-case identities and deltas inventoried below. Collection and
parameter bijection remain validation gates. Matching the count does not
qualify a mechanical split.

| Destination under `tests/` | Old class origin(s) | Cases |
|---|---|---:|
| `test_create_pr.py` | `TestCreatePR` | 29 |
| `test_create_pr_cli.py` | `TestCreatePRCLIArgs`, `TestCreatePRCLIPolicyError` | 4 |
| `test_create_pr_fork.py` | `TestCreatePRForkFlow`, `TestCreatePRRoleResolution` | 23 |
| `test_create_pr_refspec.py` | `TestCreatePRRefspec` | 9 |
| `test_finalize_pr.py` | `TestPRFinalizeAndPush` | 16 |
| `test_pr_attribution.py` | `TestAttributionAuditCLI`, `TestAuditAttributionRisk`, `TestCreatePRBranchLeakGuard`, `TestPRFinalizeAndPush` | 27 |
| `test_pr_claims.py` | `TestAbandonPr`, `TestPrClaimHelpers`, `TestReconcileActivePrSelfHeal` | 34 |
| `test_pr_helpers.py` | `TestFeatureBranchName`, `TestPRHeadName`, `TestPatchId`, `TestRequiredBodySections`, `TestResolveHeadPattern`, `TestSlugify`, `TestWorktreeSuffixHelper` | 35 |
| `test_pr_publication.py` | `TestPRFinalizeAndPush` | 32 |
| `test_pr_status.py` | `TestPRStatusLive`, `TestPRThreads`, `TestRefreshHeadObservation`, `TestSelfMergeBypassNote` | 36 |
| `test_pr_tracking.py` | `TestClassifyRecordsConvo`, `TestMultiPR`, `TestPRFinalizeAndPush`, `TestSetPRAndStatus`, `TestWorktreeToDictClaimsSummary`, `TestWorktreeToDictPRs`, `TestWorktreeToDictState` | 46 |
| `test_push_changes.py` | `TestExistingFeaturePush`, `TestPRFinalizeAndPush` | 17 |

Within this table the old unique test name selects the exact destination
identity; renamed classes are not dropped. `_git` moved into
`pr_test_helpers.py`; the old `TestPRFinalizeAndPush` container is divided
among finalization, attribution, publication, tracking and push modules.
Future extraction must retain every parameter and fixture dependency,
module marks and conservative changed-path selection for old/new node IDs.

## Published-only residual reconciliation

The original merge-base-to-publication diff has **70 scoped paths**.
There are **zero original-only scoped paths** absent from the continuation
census. Direct immutable blob comparison finds **69 of these 70 original
files byte-identical to source**, including the deleted monolithic test
file (absent at both). The only different file is `README.md`.
The original-to-source README diff contains only a 19-line **addition**
describing restart-check/current task policy and opaque claim metadata;
it deletes no original-publication content. Thus no published-only
file/body residual is demonstrated in this scope. This does not waive
the other tracks' original-only workflow/source checks.

## Exhaustive hunk ledger

Syntax: `ordinal | @@ old-range new-range @@ | SHA256-first12 | disposition`.
The path and ordinal are the unique hunk key; hashes bind full original
section headers and hunk text as specified above.

### README.md
1 | @@ -170,6 +170,15 @@ | ccfb951a7773 | P

### src/agent_worktrees/config.py
1 | @@ -44,6 +44,7 @@ | 29a5499ebca3 | Y
2 | @@ -1231,7 +1232,7 @@ | 031b5c2f50dd | Y

### src/agent_worktrees/finalize_open_pr_gate.py
1 | @@ -190,14 +190,23 @@ | df2d05c74e78 | G
2 | @@ -709,11 +718,24 @@ | e264aba325e3 | G

### src/agent_worktrees/finalize_ref.py
1 | @@ -5,9 +5,10 @@ | 256d56179603 | MX-FINAL
2 | @@ -54,14 +55,20 @@ | 8212a6b2090d | S
3 | @@ -109,20 +116,34 @@ | 1b6720427cd1 | G
4 | @@ -131,11 +152,16 @@ | c5fb5a5a358a | G
5 | @@ -192,25 +218,27 @@ | baebeecc3094 | F
6 | @@ -287,5 +315,3 @@ | 417c416b88a7 | T

### src/agent_worktrees/hooks.py
1 | @@ -208,7 +208,7 @@ | 6f89ba939dd6 | H
2 | @@ -228,6 +228,9 @@ | b2cc5d3bf595 | H
3 | @@ -242,7 +245,8 @@ | 3193ec163f3b | H
4 | @@ -271,10 +275,11 @@ | f31676db4c1d | H

### src/agent_worktrees/inrepo_config_source.py
1 | @@ -19,6 +19,8 @@ | 58752ad46f58 | C
2 | @@ -135,6 +137,41 @@ | 3aff517cc538 | C
3 | @@ -165,6 +202,8 @@ | 46e9bcea3520 | C

### src/agent_worktrees/pr_publish.py
1 | @@ -12,6 +12,7 @@ | 9f48f917730e | P
2 | @@ -103,30 +104,57 @@ | 056763e71706 | P
3 | @@ -226,9 +254,10 @@ | b6d5e2412796 | P
4 | @@ -259,10 +288,12 @@ | cbe868d345a0 | P
5 | @@ -460,44 +491,35 @@ | 7243241bf521 | P
6 | @@ -576,8 +598,11 @@ | 1e3a69595391 | P

### src/agent_worktrees/pr_rebase.py
1 | @@ -56,14 +56,22 @@ | 97f40887b5b4 | R
2 | @@ -142,10 +150,39 @@ | 86aa203ccba5 | R
3 | @@ -154,18 +191,19 @@ | 0ce8f37e6239 | R
4 | @@ -174,9 +212,7 @@ | c26b6b827271 | R
5 | @@ -189,8 +225,8 @@ | 8d767348c4e4 | R
6 | @@ -282,12 +318,16 @@ | 220060fbabcd | R

### src/agent_worktrees/pr_recovery.py
1 | @@ -4,6 +4,7 @@ | 3618d73e6523 | A
2 | @@ -38,6 +39,25 @@ | 5d938ea33dc6 | A
3 | @@ -93,23 +113,30 @@ | 1b8a7b45f251 | A
4 | @@ -127,10 +154,13 @@ | 1baf8b009c19 | A
5 | @@ -225,10 +255,8 @@ | 50089e4c8945 | A

### src/agent_worktrees/pr_rewrite.py
1 | @@ -133,8 +133,8 @@ | faa9728f2b5b | P

### tests/conftest.py
1 | @@ -2,7 +2,12 @@ | 7bcf5fcd6f50 | MX-SEEDS
2 | @@ -38,6 +43,15 @@ | e155c1cd5883 | B-MUX
3 | @@ -489,7 +503,7 @@ | 6005be75bf9d | M-MOVE
4 | @@ -499,25 +513,84 @@ | ff6fa983951d | MX-SEEDS
5 | @@ -542,6 +615,39 @@ | 8e6c0a8bba26 | B-BASE
6 | @@ -568,10 +674,31 @@ | 556f8f92ebde | B-BASE
7 | @@ -630,3 +757,136 @@ | 9ec390d0bd0e | MX-SEEDS

### tests/native_shell_support.py
1 | @@ -33,6 +33,11 @@ | 5cc200ecefc6 | N-SHELL

### tests/pr_test_helpers.py
1 | @@ -0,0 +1,99 @@ | a95367a4ca8c | MX-HELPERS

### tests/test_azure_pr_observation.py
1 | @@ -649,21 +649,31 @@ | 70602ce3a0c0 | N-HTTP-DEADLINE
2 | @@ -679,17 +689,31 @@ | 2951414a1028 | N-HTTP-DEADLINE
3 | @@ -705,7 +729,9 @@ | 75b7052a1c62 | N-HTTP-DEADLINE

### tests/test_config.py
1 | @@ -29,6 +29,48 @@ | d7f10a2b0421 | Y
2 | @@ -1625,6 +1667,113 @@ | 3adf98580005 | C

### tests/test_context_resolution.py
1 | @@ -11,6 +11,7 @@ | 1c97490d454e | B-CONTEXT
2 | @@ -22,6 +23,7 @@ | 4bd8d13e745a | B-CONTEXT
3 | @@ -34,31 +36,38 @@ | 88bc4d45b7a6 | B-CONTEXT
4 | @@ -98,6 +107,26 @@ | d6648c17f1af | B-CONTEXT

### tests/test_create_pr.py
1 | @@ -0,0 +1,590 @@ | 92e2285c383d | MX-MOVE

### tests/test_create_pr_cli.py
1 | @@ -0,0 +1,134 @@ | f4cdf59b5868 | M-MOVE

### tests/test_create_pr_fork.py
1 | @@ -0,0 +1,928 @@ | f6a2138257fd | MX-MOVE

### tests/test_create_pr_refspec.py
1 | @@ -0,0 +1,197 @@ | 4012a27fdfd5 | M-MOVE

### tests/test_creation_tail_repair.py
1 | @@ -5,6 +5,7 @@ | 3bc025536ed2 | B-CREATION
2 | @@ -14,13 +15,29 @@ | 06367589cd16 | B-CREATION
3 | @@ -28,15 +45,16 @@ | 54a1222ab76e | B-CREATION
4 | @@ -92,7 +110,7 @@ | 00d759b6a6f7 | B-CREATION
5 | @@ -115,8 +133,43 @@ | 04879390b95c | B-CREATION

### tests/test_doctor_pair_integrity.py
1 | @@ -151,6 +151,10 @@ | 2cdb6d736d8a | B-DOCTOR

### tests/test_finalize_blob_comparison.py
1 | @@ -0,0 +1,173 @@ | 7ab843710f0f | F

### tests/test_finalize_diverged_branch.py
1 | @@ -20,8 +20,12 @@ | fdf48b8c8a94 | B-MUX

### tests/test_finalize_landing.py
1 | @@ -1,6 +1,6 @@ | e5dc65b26f8b | B-LANDING
2 | @@ -15,12 +15,16 @@ | 9f397fc0fcdf | MX-LANDING
3 | @@ -33,37 +37,97 @@ | 2bf194cd8009 | B-LANDING
4 | @@ -118,6 +182,10 @@ | 4eae7163c347 | B-LANDING-ASSERT
5 | @@ -132,15 +200,6 @@ | 11b086e092b8 | B-LANDING-ASSERT
6 | @@ -170,8 +229,47 @@ | a426ea6abf02 | B-LANDING
7 | @@ -180,10 +278,10 @@ | d402ed898155 | B-LANDING
8 | @@ -196,17 +294,9 @@ | 8bb8b00d93c7 | MX-LANDING
9 | @@ -216,6 +306,15 @@ | c16dfeef0ccc | B-LANDING-ASSERT
10 | @@ -249,7 +348,8 @@ | 452ddd8fda01 | B-LANDING
11 | @@ -352,11 +452,12 @@ | 8be7ee6f2d46 | B-LANDING
12 | @@ -394,8 +495,10 @@ | a1b25e663710 | B-LANDING
13 | @@ -417,11 +520,11 @@ | e9a6bfa8e904 | B-LANDING
14 | @@ -441,7 +544,7 @@ | 37001266203c | B-LANDING
15 | @@ -451,14 +554,11 @@ | 662719ed9d1c | B-LANDING
16 | @@ -479,6 +579,17 @@ | 4c5bfd16a5af | B-MUX
17 | @@ -500,7 +611,8 @@ | 04dc9acbdf63 | B-LANDING

### tests/test_finalize_pr.py
1 | @@ -0,0 +1,353 @@ | c0e433678188 | MX-MOVE

### tests/test_finalize_precondition.py
1 | @@ -14,6 +14,7 @@ | 9614f34fbb34 | B-REFSPEC
2 | @@ -45,17 +46,17 @@ | 4ee664170a7f | B-REFSPEC
3 | @@ -73,15 +74,62 @@ | 05391bb6c5cd | B-REFSPEC
4 | @@ -459,50 +507,21 @@ | 4ce4848c0aa3 | B-INCONCLUSIVE

### tests/test_fork_pr.py
1 | @@ -1,6 +1,6 @@ | 61eb4014ab53 | M-MOVE

### tests/test_handoff_cutover.py
1 | @@ -710,6 +710,14 @@ | d32036b74513 | N-PANE-DIAGNOSTICS
2 | @@ -719,7 +727,8 @@ | 07e90266e9ac | N-PANE-DIAGNOSTICS
3 | @@ -736,9 +745,22 @@ | 0712a557abfa | N-PANE-DIAGNOSTICS
4 | @@ -746,6 +768,61 @@ | 4ebfa2a6b4ab | N-PANE-DIAGNOSTICS

### tests/test_hook_ipc.py
1 | @@ -630,22 +630,25 @@ | 1162ed14fe66 | N-SHELL
2 | @@ -1757,13 +1760,54 @@ | a5755ddc63e6 | W4-BIND

### tests/test_hooks.py
1 | @@ -3,6 +3,7 @@ | 28db68fa68e0 | H
2 | @@ -61,6 +62,59 @@ | e861f5f0e01d | H
3 | @@ -143,6 +197,7 @@ | 1f96e70e2d76 | H
4 | @@ -169,6 +224,7 @@ | ecafe81f7a57 | H
5 | @@ -206,20 +262,20 @@ | 7e7ffed26c65 | H
6 | @@ -240,7 +296,7 @@ | 58318bba580d | H

### tests/test_independent_execution_spaces.py
1 | @@ -1253,7 +1253,7 @@ | 66aa175f0637 | B-GENERATION
2 | @@ -1292,10 +1292,10 @@ | 5f400d7e7cfd | B-GENERATION

### tests/test_installer_powershell51.py
1 | @@ -1082,8 +1082,9 @@ | 8c5337406997 | N-STAMP
2 | @@ -1092,6 +1093,9 @@ | 1d066c838d38 | N-STAMP
3 | @@ -1107,10 +1111,11 @@ | f3e2a1b7f407 | N-STAMP

### tests/test_launch_request.py
1 | @@ -696,7 +696,9 @@ | 05a7137dc04d | N-UTF8-LAUNCH

### tests/test_lease_cli.py
1 | @@ -116,16 +116,21 @@ | b0a9e8dc6d5e | B-LEASE-CLI
2 | @@ -133,40 +138,17 @@ | d6602ae12a05 | B-LEASE-CLI

### tests/test_lease_store.py
1 | @@ -1,6 +1,8 @@ | 8cbfdead27e8 | B-LEASE-SEED
2 | @@ -55,8 +57,7 @@ | f71748e1bc0c | B-LEASE-SEED
3 | @@ -66,6 +67,11 @@ | 42e4c768d2ab | B-LEASE-SEED
4 | @@ -78,6 +84,25 @@ | dc2ade4ec393 | B-LEASE-SEED
5 | @@ -365,7 +390,7 @@ | cc4045e78a02 | B-LEASE-SEED
6 | @@ -373,10 +398,8 @@ | c7e018bfbb8d | B-LEASE-SEED
7 | @@ -398,6 +421,7 @@ | a2a462be8521 | B-LEASE-SEED
8 | @@ -424,27 +448,11 @@ | 6246565fedce | MX-LEASE
9 | @@ -454,13 +462,10 @@ | dd00fa7b0faa | MX-LEASE
10 | @@ -468,9 +473,20 @@ | 93e1649380ec | MX-LEASE
11 | @@ -478,10 +494,7 @@ | 2633778e1911 | B-LEASE-SEED
12 | @@ -490,7 +503,7 @@ | a0726c02fadc | B-LEASE-SEED
13 | @@ -499,7 +512,7 @@ | 36d54f5ce2af | B-LEASE-SEED

### tests/test_legacy_claim_handoff_accept.py
1 | @@ -83,6 +83,17 @@ | d0f461816e4b | B-LEGACY-CONFIG

### tests/test_local_cache_refresh.py
1 | @@ -5,6 +5,7 @@ | 95ac01d3a2b2 | B-CACHE-SEED
2 | @@ -15,6 +16,7 @@ | 2d65aae64a24 | B-CACHE-SEED
3 | @@ -29,17 +31,61 @@ | 4b0f60980da2 | B-CACHE-SEED
4 | @@ -231,10 +277,12 @@ | d52e7c979e0e | N-STAT
5 | @@ -251,6 +299,12 @@ | 15022e55a88e | N-STAT

### tests/test_module_invocation.py
1 | @@ -54,4 +54,4 @@ | 76ab5ad3fc80 | B-CLI-USAGE

### tests/test_native_shell_collection.py
1 | @@ -82,7 +82,10 @@ | 27ff0b61bebb | N-SHELL
2 | @@ -94,7 +97,8 @@ | 7d2c495f78d1 | N-SHELL

### tests/test_pr_attribution.py
1 | @@ -0,0 +1,900 @@ | c086b76afb9f | M-MOVE

### tests/test_pr_authority.py
1 | @@ -178,8 +178,9 @@ | ced29797ff22 | N-UTF8-AUTHORITY

### tests/test_pr_break_glass.py
1 | @@ -13,6 +13,7 @@ | 12af3ab74482 | B-PUBSTUB
2 | @@ -193,7 +194,7 @@ | 4286283f0def | B-PUBSTUB
3 | @@ -231,6 +232,51 @@ | 67b9fddab49e | MX-RECOVERED
4 | @@ -243,7 +289,7 @@ | 9a79c77f23d3 | B-PUBSTUB
5 | @@ -264,9 +310,14 @@ | 0c9b0014e03b | B-RECOVERED
6 | @@ -289,13 +340,17 @@ | 7d68dc870d90 | B-RECOVERED
7 | @@ -311,11 +366,11 @@ | 5256d84a80dd | B-RECOVERED
8 | @@ -332,10 +387,10 @@ | 1ef97d47dbab | B-RECOVERED
9 | @@ -371,9 +426,9 @@ | 6dbd7e7840b1 | B-RECOVERED
10 | @@ -414,8 +469,8 @@ | 3a89b12c13ea | B-RECOVERED
11 | @@ -439,8 +494,8 @@ | 2949e38887f6 | B-RECOVERED
12 | @@ -480,8 +535,8 @@ | 2eddf1a09066 | B-RECOVERED
13 | @@ -514,8 +569,8 @@ | d58aa2c92157 | B-RECOVERED

### tests/test_pr_claims.py
1 | @@ -0,0 +1,532 @@ | 7db236252f14 | M-MOVE

### tests/test_pr_custom_head.py
1 | @@ -8,6 +8,7 @@ | e4b49a73473e | B-PUBSTUB
2 | @@ -31,7 +32,7 @@ | fb488e13c0bc | B-PUBSTUB
3 | @@ -112,7 +113,6 @@ | 6342b54cc4ec | B-PUBSTUB

### tests/test_pr_helpers.py
1 | @@ -0,0 +1,320 @@ | aaa2c968bace | M-MOVE

### tests/test_pr_legacy_rewrite.py
1 | @@ -9,7 +9,7 @@ | 8bccd55b61c1 | B-REBASED
2 | @@ -36,8 +36,12 @@ | 78accabc51c8 | B-REBASED
3 | @@ -58,11 +62,13 @@ | b2f497e0ff1e | B-REBASED
4 | @@ -87,8 +93,8 @@ | 55122bb54c00 | B-REBASED
5 | @@ -115,8 +121,8 @@ | 75555b07f559 | B-REBASED
6 | @@ -138,10 +144,17 @@ | e97d13ff060d | B-RECORD

### tests/test_pr_ops.py
1 | @@ -1,6728 +0,0 @@ | a559045219dd | M-MOVE

### tests/test_pr_publication.py
1 | @@ -0,0 +1,667 @@ | 008a88588fac | MX-MOVE

### tests/test_pr_publication_state.py
1 | @@ -23,6 +23,11 @@ | 7881989cf69e | B-AUTH-PROFILE
2 | @@ -40,10 +45,8 @@ | e799e2e4ef36 | B-PUBLISHED
3 | @@ -81,10 +84,8 @@ | d082b7c0737b | B-PUBLISHED
4 | @@ -259,10 +260,8 @@ | 272bb749c01e | B-PUBLISHED
5 | @@ -286,10 +285,8 @@ | 46a31aed3856 | B-PUBLISHED
6 | @@ -309,10 +306,8 @@ | d9cd011f71a6 | B-PUBLISHED

### tests/test_pr_rebase.py
1 | @@ -16,6 +16,7 @@ | 95ce3dbe3ddd | B-PARTIAL
2 | @@ -33,12 +34,26 @@ | 1cc7e80a034b | B-PUBLISHED
3 | @@ -87,6 +102,31 @@ | d83620b1525c | B-PARTIAL
4 | @@ -104,8 +144,12 @@ | 8ffb62f1017e | B-PARTIAL
5 | @@ -120,15 +164,20 @@ | 5c8f08381031 | B-PARTIAL
6 | @@ -155,12 +204,20 @@ | bd3815a8dda8 | B-REPLAY-OBSERVE
7 | @@ -169,12 +226,20 @@ | ea9bfbef4b73 | B-REPLAY-OBSERVE
8 | @@ -409,19 +474,27 @@ | dc35785bd8c4 | B-REPLAY-OBSERVE
9 | @@ -430,9 +503,10 @@ | 517fd04c1940 | B-REPLAY-OBSERVE

### tests/test_pr_rebase_trees.py
1 | @@ -0,0 +1,89 @@ | f436e18008eb | R

### tests/test_pr_recovery.py
1 | @@ -10,10 +10,32 @@ | d2e2ef452b21 | B-ADVANCED
2 | @@ -22,22 +44,32 @@ | 618c4836a4fd | B-ADVANCED
3 | @@ -85,9 +117,9 @@ | 958be90d0ccf | B-ADVANCED
4 | @@ -126,14 +158,14 @@ | 442749b50c52 | B-RECOVERY-FAULT
5 | @@ -181,8 +213,11 @@ | e07278716974 | B-ADVANCED
6 | @@ -194,6 +229,8 @@ | c979a5ba60d7 | B-ADVANCED

### tests/test_pr_recovery_advanced_head.py
1 | @@ -12,7 +12,9 @@ | 4fb6a53f32e7 | B-RECOVERED
2 | @@ -20,7 +22,8 @@ | e44ed090d48b | MX-BACKED-TYPES
3 | @@ -50,9 +53,9 @@ | 36fcad68aee5 | B-RECOVERED
4 | @@ -69,9 +72,9 @@ | c4dc78bcd7eb | B-RECOVERED
5 | @@ -91,9 +94,9 @@ | f026f4c3977f | B-RECOVERED
6 | @@ -115,9 +118,9 @@ | 55d6d748d57d | B-RECOVERED
7 | @@ -128,9 +131,9 @@ | 8b16fcb7960e | B-RECOVERED
8 | @@ -160,9 +163,9 @@ | 34a62b1ee7be | B-RECOVERED
9 | @@ -181,10 +184,108 @@ | e31d21859fec | B-BACKED
10 | @@ -210,9 +311,9 @@ | 8a3045587fbd | B-BACKED
11 | @@ -226,9 +327,9 @@ | d23507476676 | B-BACKED
12 | @@ -241,11 +342,9 @@ | 3284500c7af5 | B-BACKED
13 | @@ -279,9 +378,9 @@ | 206d048fa357 | B-RECOVERED
14 | @@ -290,10 +389,10 @@ | a750fb2d566b | B-RECOVERED
15 | @@ -350,9 +449,9 @@ | e2d83ede56dd | B-RECOVERED
16 | @@ -377,10 +476,10 @@ | 6964c322e087 | B-BACKED
17 | @@ -408,11 +507,11 @@ | 6cd57b35e3cc | B-RECOVERED

### tests/test_pr_recovery_batch.py
1 | @@ -0,0 +1,136 @@ | f4261e280328 | A

### tests/test_pr_recovery_imperfect_evidence.py
1 | @@ -7,8 +7,11 @@ | 1f82f652d7f9 | MX-IMPERFECT
2 | @@ -18,15 +21,16 @@ | b05aada0c2ef | MX-IMPERFECT
3 | @@ -40,18 +44,30 @@ | 092a07413ebf | B-RECOVERY-FAULT
4 | @@ -70,6 +86,8 @@ | 42bbd46f07ad | B-RECOVERY-FAULT
5 | @@ -95,9 +113,9 @@ | 1be604af4451 | B-BACKED
6 | @@ -121,9 +139,9 @@ | 417e1e2cd3ce | B-BACKED

### tests/test_pr_remote_evidence.py
1 | @@ -0,0 +1,139 @@ | 0c6a534c0881 | P

### tests/test_pr_repo_fixture.py
1 | @@ -0,0 +1,125 @@ | 6289ec5ea07d | I

### tests/test_pr_rewrite.py
1 | @@ -32,8 +32,8 @@ | 3e7cb5c2595f | B-RECORD
2 | @@ -71,10 +71,10 @@ | cacb27cd8881 | B-REBASED
3 | @@ -141,8 +141,8 @@ | cf8a61d8f172 | B-REBASED
4 | @@ -183,8 +183,8 @@ | c672fa4253e7 | B-REBASED
5 | @@ -209,8 +209,8 @@ | 3063e2c62370 | B-REBASED
6 | @@ -254,8 +254,8 @@ | 1a6d9a479b5c | B-REBASED
7 | @@ -272,6 +272,37 @@ | dba58dd3f182 | B-PUB-REWRITE
8 | @@ -280,8 +311,8 @@ | 53e9d3d27be5 | B-REBASED
9 | @@ -301,8 +332,8 @@ | fc85fc6c6b4b | B-REBASED
10 | @@ -349,8 +380,13 @@ | 4c7c4be49253 | B-REBASED
11 | @@ -383,10 +419,12 @@ | 7cb2cdccfe1a | B-REBASED
12 | @@ -463,8 +501,8 @@ | fdac635c1963 | B-REBASED
13 | @@ -473,8 +511,8 @@ | af6183609178 | B-REBASED
14 | @@ -517,11 +555,11 @@ | dabddc30b60a | B-REBASED

### tests/test_pr_seed_cache.py
1 | @@ -0,0 +1,206 @@ | d865d4959914 | I

### tests/test_pr_status.py
1 | @@ -0,0 +1,849 @@ | 87e919872899 | MX-MOVE

### tests/test_pr_tracking.py
1 | @@ -0,0 +1,768 @@ | 0eadf9c2418b | MX-MOVE

### tests/test_providers.py
1 | @@ -23,6 +23,27 @@ | 48da769c0476 | B-PROVIDER-RECORD
2 | @@ -2628,6 +2649,25 @@ | 8535bee3e2f7 | B-PROVIDER-RECORD
3 | @@ -2683,6 +2723,7 @@ | 6ce40177a85f | B-PROVIDER-ASSERT
4 | @@ -2693,18 +2734,19 @@ | 6db6024d74df | MX-PROVIDER
5 | @@ -2717,7 +2759,7 @@ | d55489c89830 | B-PROVIDER-RECORD
6 | @@ -2806,7 +2848,7 @@ | e1263f17b7bb | B-PROVIDER-RECORD
7 | @@ -2815,7 +2857,7 @@ | 175eddfd6f21 | B-PROVIDER-RECORD
8 | @@ -2823,7 +2865,7 @@ | 8d23510dcef1 | B-PROVIDER-RECORD
9 | @@ -2833,7 +2875,7 @@ | 532bfaaa44aa | B-PROVIDER-RECORD
10 | @@ -2841,7 +2883,7 @@ | 7ef170f7d3d3 | B-PROVIDER-RECORD
11 | @@ -2853,7 +2895,7 @@ | cd910eaeb418 | B-PROVIDER-RECORD
12 | @@ -2862,7 +2904,7 @@ | 329ad9ce1638 | B-PROVIDER-RECORD
13 | @@ -2871,7 +2913,7 @@ | c49044e93f93 | B-PROVIDER-RECORD
14 | @@ -2882,19 +2924,22 @@ | 5fd5cf6df595 | MX-PROVIDER
15 | @@ -2902,61 +2947,14 @@ | 62224877e556 | MX-PROVIDER
16 | @@ -3034,20 +3032,6 @@ | fd53ec25f648 | B-PROVIDER-ASSERT
17 | @@ -3063,13 +3047,12 @@ | 9eaf749a5e63 | B-PROVIDER-RECORD
18 | @@ -3089,8 +3072,8 @@ | 557907e5dac3 | B-PROVIDER-RECORD
19 | @@ -3103,9 +3086,9 @@ | 16e7a8cfcc5f | B-PROVIDER-RECORD
20 | @@ -3120,10 +3103,10 @@ | 0c63e031b539 | B-PROVIDER-RECORD

### tests/test_publication_deadline.py
1 | @@ -117,8 +117,12 @@ | 7d5e82b9d533 | B-PUB-DEADLINE

### tests/test_push_changes.py
1 | @@ -0,0 +1,442 @@ | bbd311d2cee2 | MX-MOVE

### tests/test_reconcile.py
1 | @@ -20,15 +20,21 @@ | 6e3b0adf8f00 | B-RECONCILE
2 | @@ -221,19 +227,12 @@ | eac6a9ed22fa | B-RECONCILE
3 | @@ -269,13 +268,8 @@ | 49e516500c6d | B-RECONCILE
4 | @@ -392,6 +386,27 @@ | 3af47b3b333a | B-RECONCILE

### tests/test_registry_paths.py
1 | @@ -618,23 +618,6 @@ | 01a6986f9c36 | B-REGISTRY-ASSERT
2 | @@ -649,6 +632,9 @@ | a91b6e0ae2de | B-REGISTRY-ASSERT

### tests/test_repos.py
1 | @@ -25,9 +25,14 @@ | d02ece3774dc | B-REPOS-CONFIG
2 | @@ -55,14 +60,6 @@ | f871a97f23b9 | B-CLONE-IDENTITY
3 | @@ -727,7 +724,7 @@ | e11175750cdc | B-REPOS-CONFIG
4 | @@ -752,8 +749,8 @@ | 368d0a5dbb26 | B-REPOS-CONFIG
5 | @@ -767,7 +764,7 @@ | f528a0b031ab | B-REPOS-CONFIG
6 | @@ -781,7 +778,7 @@ | e38cae6428b6 | B-REPOS-CONFIG
7 | @@ -804,7 +801,7 @@ | f5de47ccb6a0 | B-REPOS-CONFIG
8 | @@ -834,7 +831,7 @@ | bbc5fd25a47f | B-REPOS-CONFIG
9 | @@ -848,7 +845,7 @@ | ce6f12634ba2 | B-REPOS-CONFIG
10 | @@ -871,7 +868,7 @@ | b3d04164682c | B-REPOS-CONFIG
11 | @@ -890,7 +887,7 @@ | 2e61d5b7ec26 | B-REPOS-CONFIG
12 | @@ -911,7 +908,7 @@ | e738a6fbae9c | B-REPOS-CONFIG
13 | @@ -930,7 +927,7 @@ | e62d0f771292 | B-REPOS-CONFIG
14 | @@ -952,7 +949,7 @@ | 882c2f32d7f5 | B-REPOS-CONFIG
15 | @@ -1741,4 +1738,4 @@ | a3ea2e00052b | B-REPOS-CONFIG

### tests/test_resource_producer_finalize.py
1 | @@ -4,20 +4,47 @@ | 187f789379dc | B-RESOURCE

### tests/test_terminal_conclusion.py
1 | @@ -2,6 +2,7 @@ | 442e73e83c30 | B-TERMINAL
2 | @@ -18,18 +19,18 @@ | 1df47011ba08 | B-TERMINAL
3 | @@ -48,6 +49,21 @@ | 7a0d2d1c7bfa | B-TERMINAL
4 | @@ -82,6 +98,51 @@ | 01b093edb6bf | B-TERMINAL
5 | @@ -93,8 +154,8 @@ | 7ca762844244 | B-TERMINAL
6 | @@ -108,8 +169,8 @@ | f8922c15f9fe | B-TERMINAL
7 | @@ -125,8 +186,8 @@ | 4a325141d943 | B-TERMINAL
8 | @@ -148,8 +209,8 @@ | 202804d6eab7 | B-TERMINAL
9 | @@ -163,8 +224,8 @@ | b2bbebb24214 | B-TERMINAL
10 | @@ -177,8 +238,8 @@ | 28b3781af1ca | B-TERMINAL
11 | @@ -190,8 +251,8 @@ | 2e878498c654 | B-TERMINAL
12 | @@ -202,8 +263,8 @@ | 2aa84383dd2f | B-TERMINAL
13 | @@ -223,9 +284,9 @@ | dcc35cf9202f | B-TERMINAL
14 | @@ -258,7 +319,7 @@ | c1d81602669e | B-TERMINAL
15 | @@ -269,7 +330,7 @@ | ecd19c3d9451 | B-TERMINAL
16 | @@ -311,14 +372,14 @@ | a0be95479069 | B-TERMINAL
17 | @@ -361,7 +422,7 @@ | 56afb72adc93 | B-TERMINAL
18 | @@ -370,7 +431,7 @@ | 1d97f7b006c6 | B-TERMINAL
19 | @@ -411,12 +472,12 @@ | c80775143a71 | B-TERMINAL
20 | @@ -454,12 +515,12 @@ | 6f52c72c785b | B-TERMINAL
21 | @@ -494,12 +555,12 @@ | 9e9c42c4b69f | B-TERMINAL
22 | @@ -520,9 +581,9 @@ | ea819a1a6024 | B-TERMINAL
23 | @@ -548,9 +609,9 @@ | 45b93b9aa0f3 | B-TERMINAL
24 | @@ -585,9 +646,9 @@ | 7fd05a7d86c5 | B-TERMINAL
25 | @@ -620,8 +681,8 @@ | 745f0309733f | B-TERMINAL
26 | @@ -650,9 +711,9 @@ | 759ea38c03ce | B-TERMINAL
27 | @@ -689,9 +750,9 @@ | 5bd46ba60923 | B-TERMINAL
28 | @@ -707,8 +768,8 @@ | e075ce1be2ac | B-TERMINAL
29 | @@ -721,8 +782,8 @@ | 92cb467944e6 | B-TERMINAL
30 | @@ -738,9 +799,9 @@ | 4a650b3df30c | B-TERMINAL
31 | @@ -769,8 +830,8 @@ | 952f19aabb5c | B-TERMINAL
32 | @@ -802,8 +863,8 @@ | 7abb5147b5b3 | B-TERMINAL
33 | @@ -816,9 +877,9 @@ | 3fa2d1c26a66 | B-TERMINAL
34 | @@ -849,8 +910,8 @@ | b8ef7adb8e3e | B-TERMINAL
35 | @@ -883,8 +944,8 @@ | 6f5f8878b05e | B-TERMINAL
36 | @@ -974,9 +1035,9 @@ | d09233b0d4dc | B-TERMINAL
37 | @@ -1063,9 +1124,9 @@ | 5b01bee39e29 | B-TERMINAL
38 | @@ -1106,9 +1167,9 @@ | 730b61739dc1 | B-TERMINAL
39 | @@ -1168,9 +1229,9 @@ | 93d86fb1e386 | B-TERMINAL
40 | @@ -1225,9 +1286,9 @@ | c4fc9f34576e | B-TERMINAL
41 | @@ -1502,8 +1563,8 @@ | b9a47478ddbc | B-TERMINAL
42 | @@ -1521,7 +1582,7 @@ | d5b3711be5f1 | B-TERMINAL
43 | @@ -1532,7 +1593,7 @@ | 4f98b3069c1f | B-TERMINAL
44 | @@ -1967,7 +2028,7 @@ | 2f26f4e3363a | B-TERMINAL
45 | @@ -1984,7 +2045,7 @@ | 5004194bf851 | B-TERMINAL
46 | @@ -2005,7 +2066,7 @@ | 1c3c45334c0d | B-TERMINAL
47 | @@ -2022,7 +2083,7 @@ | e94495ed8019 | B-TERMINAL
48 | @@ -2050,7 +2111,7 @@ | edf091b5aabb | B-TERMINAL
49 | @@ -2062,7 +2123,7 @@ | 133bf2503f00 | B-TERMINAL
50 | @@ -2082,13 +2143,13 @@ | 240899d4f2c5 | B-TERMINAL
51 | @@ -2104,7 +2165,7 @@ | 214e50e063fb | B-TERMINAL
52 | @@ -2120,7 +2181,7 @@ | 5b5fd286e0aa | B-TERMINAL
53 | @@ -2141,7 +2202,7 @@ | c40c1ae3225b | B-TERMINAL
54 | @@ -2155,7 +2216,7 @@ | 63f3ae46df92 | B-TERMINAL
55 | @@ -2173,7 +2234,7 @@ | 05a9d085566e | B-TERMINAL
56 | @@ -2191,7 +2252,7 @@ | c037782754ce | B-TERMINAL
57 | @@ -2210,7 +2271,7 @@ | 5d175cbb69d3 | B-TERMINAL
58 | @@ -2232,7 +2293,7 @@ | 4085c3e27d98 | B-TERMINAL
59 | @@ -2251,7 +2312,7 @@ | 5d98ff462406 | B-TERMINAL
60 | @@ -2275,7 +2336,7 @@ | 2f8c8e17d49a | B-TERMINAL
61 | @@ -2294,7 +2355,7 @@ | 6d07a105841f | B-TERMINAL
62 | @@ -2302,7 +2363,7 @@ | 6c36299c7d6e | B-TERMINAL

## Completion boundary

The census now covers ownership as well as enumeration. Every mixed row
has named symbol/subspan owners below, and still blocks implementation until
split; no validation or review gate is cleared by its candidate assignment.
No skips, exclusions or retirements have been enacted.
The parent still requires repeated complete same-head runs <=900 seconds
including preparation/cleanup, all groups <=240 seconds and exactly two
Worktrees workers. Retain the 30/300/900-second limits, 128 processes,
4096 MiB process-tree memory and 2048 MiB storage, native deadlines and
marks. This read-only census supplies **no performance or runtime evidence**.

## Exact hunk-header section suffix dictionary

This lossless dictionary completes the range headers above. In the fenced
block, a path sets the current path; a row `ordinals | suffix` assigns that
literal suffix to each listed ordinal. Append the substring **after `|`**
(including its initial space) to the ledger's closing `@@`. Unlisted
ordinals have an empty suffix. Apparent truncated function names below are
Git's actual section headers, not an abbreviation introduced by this report.

```text
README.md
1 | report-first doctor scope preserves same-machine alias relationships and require
src/agent_worktrees/config.py
1 | from .config_cache import (  # noqa: F401 (re-exported)
2 | def _load_yaml_safe(path: Path) -> dict[str, Any]:
src/agent_worktrees/finalize_open_pr_gate.py
1 | def _tracked_branch_ahead(
2 | def _extra_commit_count(
src/agent_worktrees/finalize_ref.py
1 | Split out of ``finalize.py`` (module-size guard) -- see ``resolve_finalize_ref``
2 | def squash_landed(branch: str, upstream: str, cwd: str) -> bool | None:
3 | def creation_point(ref: str, cwd: str) -> str:
4 | def _later_resets_to_source(entries: list[str], created: str, source: str, cwd:
5 | def _blobs_on_upstream(branch: str, upstream: str, cwd: str) -> bool:
6 | def warn_if_tracked_branch_diverged(
src/agent_worktrees/hooks.py
1 | def _registry_default_branch(name: str) -> str | None:
2,3 | def _default_branch(cwd: str | Path) -> str | None:
4 | def _protected_branches(cwd: str | Path) -> list[str]:
src/agent_worktrees/inrepo_config_source.py
1 | from __future__ import annotations
2 | def _read_committed_blob(
3 | def load_inrepo_config_from_committed_ref(
src/agent_worktrees/pr_publish.py
1 | from __future__ import annotations
2 | def _identity(url: str, cwd: str) -> str:
3,4 | def _select_push_target(repo, pr, worktree_path: str) -> PushTarget | None:
5 | def push_checked(record, remote: str, refspec: str, *, cwd: str,
6 | def record_remote(pr, remote: str, repo_remote: str, worktree_path: str) -> None
src/agent_worktrees/pr_rebase.py
1 | def _ancestor(old: str, new: str, cwd: str) -> bool:
2 | def _merge_tree(parent: str, onto: str, commit: str, cwd: str) -> tuple[str, boo
3,4,5 | def _conflict_lineage(
6 | def verify(record, repo, remote: str, refspec: str, expected: str, *, cwd: str,
src/agent_worktrees/pr_recovery.py
1 | from __future__ import annotations
2 | def _rev(ref: str, cwd: str) -> str:
3 | def selected_pr(worktree_id: str, branch: str, record):
4 | def retain_rewrite(
5 | def synced(record, pr, expected: str, head: str, *, cwd: str) -> RecoveryPoint |
src/agent_worktrees/pr_rewrite.py
1 | def push_changes(worktree_id: str, config: Config, record: tracking.WorktreeReco
tests/conftest.py
2 | def native_powershell():
3 | def make_session_dir(
4 | from agent_worktrees import git_ops  # noqa: E402
5,6 | def pr_repo(tmp_path: Path, monkeypatch):
7 | def _installer_processes_through_subprocess_run(request, monkeypatch):
tests/native_shell_support.py
1 | def _is_wsl_launcher(candidate: str) -> bool:
tests/test_azure_pr_observation.py
1 | def test_native_transport_keeps_real_auth_value_out_of_process_arguments():
2,3 | def test_native_transport_absolute_deadline_bounds_slow_stream(monkeypatch, fram
tests/test_config.py
1 | def _isolate_config_layers(tmp_path_factory, monkeypatch):
2 | class TestInRepoConfigCommittedRefResolution:
tests/test_context_resolution.py
1 | from __future__ import annotations
2 | from agent_worktrees import config as cfg
3 | def _git(*args: str, cwd) -> str:
4 | def adopted_repo(tmp_path: Path, monkeypatch):
tests/test_creation_tail_repair.py
1 | from __future__ import annotations
2 | from types import SimpleNamespace
3,4 | def _creation_fixture(tmp_path, monkeypatch, *, native_git=False):
5 | def creation(tmp_path, monkeypatch):
tests/test_doctor_pair_integrity.py
1 | def test_doctor_json_repairs_pair_from_untracked_knowledge_cwd(
tests/test_finalize_diverged_branch.py
1 | from __future__ import annotations
tests/test_finalize_landing.py
2 | from __future__ import annotations
3 | def _identity(repo: Path) -> None:
4,5 | def test_a_multi_commit_squash_later_edited_upstream_is_landed(env):
6 | def _merge_into_dev_and_pull_forward(env) -> str:
7 | def test_a_pr_merged_into_dev_while_main_is_behind_is_landed_on_its_base(env, pr
8 | def test_without_the_pr_base_a_dev_merge_is_refused_with_its_evidence(env, provi
9 | def test_explain_reports_every_base_and_check(env, provider):
10 | def _sync_worktree_branch_to_a_newer_main(env) -> str:
11 | def test_a_local_branch_shadowing_the_pr_base_never_certifies_its_commits(env, p
12,13 | def test_the_pull_forward_advice_runs_cleanly_past_a_squash_that_a_plain_rebase_
14 | def test_explain_with_an_empty_worktree_path_reads_the_anchor(env, provider):
15,16 | def test_a_pr_tracked_without_its_branch_finalizes_where_explain_says_it_landed(
17 | def test_branches_pulled_forward_onto_different_bases_are_all_published(env, pro
tests/test_finalize_precondition.py
1 | already-merged PR.
2 | def _init_identity(repo: Path) -> None:
3 | def refspec_worktree(tmp_path: Path) -> SimpleNamespace:
4 | class TestContentExceedsMergedHeadFailsClosed:
tests/test_handoff_cutover.py
1,2,3,4 | class TestPaneWrapperInitialPrompt:
tests/test_hook_ipc.py
1 | def test_installed_bin_layout_contains_all_lifecycle_helpers(tmp_path, monkeypat
2 | def test_session_start_catalog_matches_native_emitter(monkeypatch, tmp_path):
tests/test_hooks.py
2 | class TestConfigResolution:
3,4,5,6 | class TestRegistryDefaultBranch:
tests/test_independent_execution_spaces.py
1,2 | def test_session_register_deregister_fence_raced_fresh_foreign_record(
tests/test_installer_powershell51.py
1 | def test_early_installer_utilities_are_powershell_51_safe_ascii():
2,3 | def test_native_stamp_directory_copy_preserves_complete_tree(tmp_path: Path):
tests/test_launch_request.py
1 | def test_real_powershell_transport_preserves_encoded_intent(tmp_path):
tests/test_lease_cli.py
1 | def test_unbound_acquire_emits_structured_readiness_without_store_access(
2 | def test_acquire_defaults_have_no_disposition(remote: Path, capsys):
tests/test_lease_store.py
2 | def remote(tmp_path: Path) -> Path:
3 | def settings(remote: Path) -> LeaseSettings:
4 | def store(settings: LeaseSettings, now: datetime = BASE) -> GitLeaseStore:
5 | def test_applied_push_with_lost_status_is_reported_as_success(
6,7 | def test_squash_stale_collapses_an_old_released_lease(
8 | def test_squash_stale_never_touches_a_live_lease_regardless_of_age(
9 | def test_squash_stale_filters_by_kind(
10 | def test_squash_stale_dry_run_reports_without_pushing(
11,12,13 | def test_squash_stale_skips_a_ref_reacquired_mid_sweep(
tests/test_legacy_claim_handoff_accept.py
1 | def graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.Fixtu
tests/test_local_cache_refresh.py
1 | from concurrent.futures import ThreadPoolExecutor
2 | import pytest
3 | def write_json(path, data):
4,5 | def test_nonregular_input_is_rejected_before_open(copy_workspace, monkeypatch, k
tests/test_module_invocation.py
1 | def test_pr_watch_usage_does_not_crash_on_circular_import():
tests/test_native_shell_collection.py
1 | def test_native_shell_collection_unavailable(monkeypatch, shell_policy):
2 | def test_native_shell_collection_success_headless_bounded(monkeypatch, shell_pol
tests/test_pr_authority.py
1 | installer.register_project(sys.argv[3], 'unused', 'main')
tests/test_pr_break_glass.py
1 | import pytest
2,3 | def _recovered_pr(pr_repo, monkeypatch: pytest.MonkeyPatch, *, stub_attribution=
4,5 | def test_azure_recovery_preview_matches_ssh_destination(
6 | def test_real_recovery_requires_confirmation_and_retains_commits(
7 | def test_real_recovery_refuses_unseen_local_commit(
8 | def test_recovery_cli_returns_bound_preview_json(
9 | def test_recovery_cli_selects_target_project_from_other_cwd(pr_repo, monkeypatch
10 | def test_recovery_keeps_transport_and_protected_history_gates(
11 | def test_recovery_transport_pins_approved_objects(pr_repo, monkeypatch, race):
12 | def test_recovery_reconciles_failed_transport_before_audit(pr_repo, monkeypatch,
13 | def test_recovery_missing_tip_fetch_is_bounded_and_audited(pr_repo, monkeypatch)
tests/test_pr_custom_head.py
1 | import copy
2 | def _attested(pr_repo, monkeypatch):
3 | def test_public_explicit_rewrite_round_trip_and_stale_live_head(pr_repo, monkeyp
tests/test_pr_legacy_rewrite.py
1 | from agent_worktrees import config as cfg
2 | def legacy_record(wid, *, state="active", defect=""):
3 | def test_legacy_rewrite_single_public_command_after_rebase(pr_repo, scheme, monk
4 | def test_revised_legacy_record_can_intentionally_rewrite_its_own_head(pr_repo, r
5 | def test_revised_legacy_rewrite_requires_exact_live_authority(pr_repo, defect, m
6 | def test_legacy_rewrite_refuses_incompatible_evidence(pr_repo, defect, monkeypat
tests/test_pr_publication_state.py
1 | pytestmark = [
2 | def test_rewrite_provider_mismatch_never_resolves_credentials(pr_repo, monkeypat
3 | def test_provider_identity_generation_survives_network_window_snapshot(pr_repo,
4 | def test_publication_holds_authority_through_io_and_persistence(pr_repo, monkeyp
5 | def test_ordinary_publication_rechecks_authority_after_admission_wait(pr_repo, m
6 | def test_ownership_stamping_merges_attribution_only_revision(pr_repo):
tests/test_pr_rebase.py
1 | import pytest
2 | def _record(wid):
3 | def _partial_success_then_rebase(pr_repo, monkeypatch):
4 | def test_partial_success_before_rebase_recovers_only_verified_source_lineage(pr_
5 | def test_partial_success_recovery_rejects_unconfirmed_or_unrelated_remote_heads(
6,7 | def test_owned_pr_rebase_publishes_with_original_lease(pr_repo, monkeypatch, sch
8 | def test_owned_pr_rebase_publishes_explicit_conflict_continuation(pr_repo, comma
9 | def test_owned_pr_rebase_conflict_message_is_compared_without_stripping(monkeypa
tests/test_pr_recovery.py
1 | import pytest
2 | def _record(wid):
3 | def test_backup_failure_prevents_rebase_contract(tmp_path, monkeypatch, capsys):
4 | def test_backed_sync_publishes_legacy_pr_without_replay_journals(
5 | def test_recovery_ref_writes_ignore_inherited_foreign_git_context(pr_repo, monke
6 | def test_unrelated_branch_sync_keeps_completed_private_pr_authority(pr_repo):
tests/test_pr_recovery_advanced_head.py
1 | from agent_worktrees import config as cfg
2 | pytestmark = [
3 | def metadata(wt: Path) -> Path:
4 | def test_advanced_head_preview_is_no_effect_and_keeps_normal_refusal(
5 | def test_confirmed_advanced_head_retains_actual_objects_and_original_evidence(
6 | def test_changed_bound_inputs_require_new_confirmation(
7 | def test_provider_and_ref_must_agree_before_advanced_preview(
8 | def test_recovery_cas_never_falls_back_to_unapproved_fast_forward(
9 | def backed_publication(
10 | def test_proven_prior_publication_settles_without_push_or_forge_edit(
11 | def test_mutable_checkpoint_target_is_unusable_settlement_evidence(
12 | def test_changed_backed_evidence_invalidates_settlement_confirmation(
13 | def test_settlement_updates_base_and_patch_before_next_sync_publication(
14 | def test_remote_equal_to_source_without_proof_is_not_automatic_settlement(
15 | def test_plan_fields_bind_action_original_record_and_evidence() -> None:
16 | def test_remote_only_advanced_commit_is_fetched_only_after_confirmation(
17 | def test_settlement_failures_report_bookkeeping_effect_without_claiming_a_push(
tests/test_pr_recovery_imperfect_evidence.py
1 | from pathlib import Path
2 | pytestmark = [pytest.mark.timeout(120)]
3,4,5 | def test_unusable_checkpoint_produces_bound_rewrite_preview_and_confirmed_rewrit
6 | def test_changed_imperfect_evidence_invalidates_confirmation(
tests/test_pr_rewrite.py
1 | def record_for(wid):
2 | def test_existing_active_pr_can_refresh_ownership_and_rewrite(pr_repo, monkeypat
3 | def publish_and_rebase(pr_repo, scheme):
4 | def test_explicit_rewrite_public_cli_real_rebase(pr_repo, scheme, monkeypatch):
5 | def test_explicit_rewrite_refuses_moved_remote(pr_repo, scheme, during_push, mon
6 | def test_explicit_rewrite_ownership_and_destination_guards(pr_repo, defect, monk
7 | def test_explicit_rewrite_preserves_hook_rejection_and_dry_run(pr_repo, capsys):
8 | def test_low_level_rewrite_requires_exact_lease(monkeypatch):
9 | def test_explicit_rewrite_recorded_fork_and_repoint_refusal(pr_repo, scheme):
10 | def test_explicit_rewrite_serializes_set_pr_and_pins_owned_branch(pr_repo, monke
11 | def test_explicit_rewrite_configured_project_not_ambient(pr_repo, monkeypatch, c
12 | def test_explicit_rewrite_ownership_generation_survives_stale_save(pr_repo, monk
13 | def test_explicit_rewrite_rejects_same_record_sibling_head(pr_repo):
14 | def test_explicit_rewrite_rejects_repointed_origin_with_same_slug_and_head(pr_re
tests/test_providers.py
1 | from agent_worktrees.providers import (
2 | class _FakeReadyProvider:
3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20 | class TestCreatePRAutoOpen:
tests/test_publication_deadline.py
1 | def test_verified_rebase_retains_configured_bound(monkeypatch):
tests/test_reconcile.py
1 | REPO = Path(__file__).resolve().parents[3]
2,3,4 | def env(tmp_path: Path, monkeypatch):
tests/test_registry_paths.py
1 | def test_failed_identity_publication_leaves_no_partial_receipt(
2 | def test_namespaced_registration_persists_cell_config_dir(
tests/test_repos.py
1 | def _git(path: Path, *args: str) -> None:
2 | def _bare_anchor_from_clone(upstream: Path, dest: Path, branch: str = "main") ->
3 | def test_add_repo_persists_account(home: Path):
4 | def test_backfill_pins_a_resolvable_repo(home: Path, tmp_path: Path):
5 | def test_backfill_restricts_to_named_repo(home: Path, tmp_path: Path):
6 | def test_backfill_skips_org_owner_needing_clarify(home: Path, tmp_path: Path):
7 | def test_backfill_skips_ssh_remote(home: Path, tmp_path: Path):
8 | def test_backfill_distinguishes_http_from_ssh_remote(home: Path, tmp_path: Path)
9 | def test_backfill_pins_with_derived_host_not_hardcoded_github_com(home: Path, tm
10 | def test_backfill_unknown_name_never_falls_back_to_all(home: Path, tmp_path: Pat
11 | def test_add_repo_pins_credential_for_a_resolvable_owner(home: Path, tmp_path: P
12 | def test_add_repo_does_not_pin_when_account_needs_clarify(home: Path, tmp_path:
13 | def test_add_repo_does_not_pin_ssh_remote(home: Path, tmp_path: Path):
14 | def test_add_repo_pin_expands_home_relative_path(home: Path, tmp_path: Path, mon
15 | def test_sync_repo_fetches_its_own_checkout_despite_an_inherited_git_dir(
tests/test_resource_producer_finalize.py
1 | from __future__ import annotations
tests/test_terminal_conclusion.py
1 | from __future__ import annotations
2 | def _git(cwd: Path, *args: str) -> str:
3,4 | def _worker(tmp_path: Path, monkeypatch):
5 | def _conclude(record_path: Path, repo, **over):
6 | def test_live_session_is_preserved(tmp_path, monkeypatch):
7 | def test_different_lifecycle_head_is_preserved(tmp_path, monkeypatch):
8 | def test_pending_handoff_preserves_predecessor(tmp_path, monkeypatch):
9 | def test_follow_up_preserves_session_and_worktree(tmp_path, monkeypatch):
10 | def test_open_pr_preserves_session_and_worktree(tmp_path, monkeypatch, state):
11 | def test_non_cli_worktree_preserves_session(tmp_path, monkeypatch):
12 | def test_mismatched_worktree_path_is_preserved(tmp_path, monkeypatch):
13 | def test_duplicate_worktree_path_is_preserved(tmp_path, monkeypatch):
14 | def test_dispatch_attempt_policy_concludes_exact_acp_allocation(
15,16 | def test_dispatch_attempt_policy_releases_own_stale_session_claim(
17 | def test_dispatch_attempt_policy_releases_only_the_concluding_session_claim(
18,19 | def test_dispatch_attempt_policy_unrelated_live_claim_blocks_without_persisting(
20 | def test_dispatch_attempt_policy_does_not_persist_release_when_dirty_work_blocks
21 | def test_dispatch_attempt_policy_does_not_release_claim_on_reservation_mismatch(
22 | def test_disposable_cli_policy_does_not_release_session_claims(
23 | def test_dispatch_attempt_policy_rejects_wrong_reservation(
24 | def test_newer_dispatch_provenance_is_preserved_but_not_trusted(
25 | def test_blank_dispatch_provenance_is_preserved_but_not_trusted(
26 | def test_dispatch_provenance_strings_are_normalized(tmp_path, monkeypatch):
27 | def test_lifecycle_change_during_git_inspection_blocks_priming(
28 | def test_behind_branch_is_primed_without_rewriting_head(
29 | def test_dirty_author_work_is_preserved(tmp_path, monkeypatch):
30 | def test_committed_author_work_is_preserved(tmp_path, monkeypatch):
31 | def test_missing_checkout_with_committed_branch_is_preserved(
32 | def test_generated_overlay_is_preserved_as_dirty_work(tmp_path, monkeypatch):
33 | def test_repeated_conclusion_is_idempotent(tmp_path, monkeypatch):
34 | def test_terminal_managed_record_rejects_new_session_activation(
35 | def test_managed_gc_can_later_remove_primed_tree(tmp_path, monkeypatch):
36 | def test_exact_tracking_lookup_rejects_cross_project_collision(
37 | def test_cli_remove_is_idempotent_when_record_disappears_after_lookup(
38 | def test_cli_remove_is_idempotent_when_record_disappears_during_conclusion(
39 | def test_cli_remove_runs_exact_managed_sweep_after_eligibility(
40 | def test_cli_remove_surfaces_fresh_managed_sweep_skip(
41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62 | class TestReciprocalDisposal:
```

Read-only self-check: all **330** keys/range headers/hashes match the
regenerated scoped diff; **70** paths, zero missing/extra/duplicate keys.
The original 48 retained candidates and 1 proposed retirement are preserved.
The follow-up refines the other 281 hunks into named candidate ownership,
including explicit mixed-hunk split maps; no blocked-unclassified disposition
remains. Source HEAD remains pinned. Candidate ownership does not clear
implementation review, collection parity or qualification gates.

## Follow-up: completed behavioral ownership of the former 281 U hunks

Only the former U dispositions were refined. The original **48** candidate
rows and **1** retirement row retain their keys, hashes and assignments.
Every ID in this section is a **candidate contract**, not a claim of review,
passing execution, acceptable collection changes, merged code or measured
speed. An MX row names the exact extraction partition; it explicitly
**blocks implementation until split**, but does not leave ownership unknown.
New batch IDs are local to this census, not assertions that an external PR
or contributor owns them.

### Narrow additional batch contracts

Paths below are under `plugins/agent-worktrees/`. The ledger provides the
exhaustive hunk memberships; symbol names scope changed bodies, not nearby
unchanged context. Fixture construction may be reused; real decisions,
observations, confirmation checks, remote mutations, leases and publication
effects remain per-case and fresh.

| ID | Wave | Exact symbols / observable contract / prerequisites and exclusions |
|---|---:|---|
| B-SEED | 2 | `tests/conftest.py::_seed_directory`: run-local sandbox publication, exact readiness JSON, sidecar lock, interrupted/incomplete builders never consumed, control symlinks rejected; requires wave-1 private bounded sandbox, independently accompanies I's seed-cache regressions. Does not cache authority. |
| B-BASE | 2 | `conftest.py::_build_pr_repo_seed`, `_pr_repo_seed`, `_copy_pr_repo`, `pr_repo`: physical copies of Git stores, repaired administrative links and relocated remote/record/rewrite identities; empty template and maintenance settings leave hooks private. Depends B-SEED; I supplies copy-isolation assertions. |
| B-RECORD | 2 | `conftest.py::record_repo`, `test_pr_tracking.py::recorded_pr`, record-only conversions in status/tracking/legacy/native-state cases: persisted YAML identity/revision/lifecycle tests need no publication. Explicit synthetic heads are test inputs, never live authority. No conversion of a test whose subject is actual publication. |
| B-PUBLISHED | 2 | `_published_pr_seed`, `_published_refspec_pr_seed`, `published_pr_repo`, `published_refspec_pr_repo` and named consumers below: independent copied real published topology with remapped record identity; remove setup-only first `create_pr`, retain tested later create/push/reconcile/refusal. Depends B-BASE; I's published copies remain distinct evidence. |
| B-REBASED | 2 | `_rebased_refspec_pr_seed`, `_rebased_snapshot_pr_seed`, `_copy_rebased_pr_repo`, both concrete fixtures and `rebased_pr_repo`: reuse only completed setup rebase, select actual scheme, preserve recorded old head and private refs; rewrite/legacy cases still perform their own confirmation, native push, hook and lease checks. Depends B-PUBLISHED and actual `publish_and_rebase` setup helper. |
| B-FORK | 2 | `_fork_pr_seed`, `fork_pr_repo`, prepared-state branch of `PRWorkflowSetup._fork_headed_rerun` and listed consumers: branch exists only in copied fork, origin head absent, feedback commit private, no shared record authority; subsequent fork/repoint checks stay real. Depends B-PUBLISHED. |
| B-PUBSTUB | 3 | `pr_test_helpers.py::_stub_remote_target` and changed custom-head/break-glass/fork publication stubs: wrap real fresh `_remote_snapshot` and replace only scenario slug/optional identity after valid transport proof, not bypass URLs. Depends P; leaves real Git reads and race checks intact. |
| B-PUB-DEADLINE | 3 | `test_publication_deadline.py::test_verified_rebase_retains_configured_bound` stub recognizes absent NUL config query with `(1,"","")`; existing deadline assertion remains unchanged, depends P. Not a timeout increase. |
| B-PUB-REWRITE | 3 | `test_pr_rewrite.py::test_explicit_rewrite_uses_one_fresh_destination_snapshot`: two equal snapshots for dry-run, later repoint produces two fresh unequal identities/refusal, record and both remote heads unchanged. Depends P and B-REBASED. |
| B-ROLE | 5 | `test_create_pr_fork.py::TestCreatePRRoleResolution._fake_permission_provider` plus `test_maintain_auto_open_uses_effective_self_merge_flow`: actual-shaped PullResult and head-bound `pull_review_exception`, exact `(repo,number,head)` call. Current-policy compatibility, not a new merge bypass or cached review permission. |
| B-OBSERVATION | 5 | `test_pr_status.py::TestRefreshHeadObservation.test_concurrent_reassociation_rejects_returned_observation`: competing reassociation increments `pr_revision`, stale result rejected. Retain generation fencing rather than manufacturing unchanged-generation concurrency. |
| B-MUX | 2 | `conftest.py::git_only_mux`, module marks in finalized PR/landing/diverged tests and `test_finalize_landing.py` exact-ID/path termination doubles: Git-only fixtures cannot discover/kill real host mux/processes. Does not stub the kernel/native ownership suites. |
| B-AUTH-PROFILE | 2 | `test_pr_publication_state.py::_isolated_authority_profile`: function-private install profile; publication/lock/revalidation assertions remain actual consumers. Independent of publication seed reuse. |
| B-PARTIAL | 2 | `test_pr_rebase.py::_partial_success_seed`, `partial_success_repo`, partial-success refusal consumer: copy actual accepted-head history and saved JSON; foreign provider never observed, rejected outcomes leave immutable seed record/refs unchanged. Depends B-PUBLISHED/B-SEED, not R's batching. |
| B-REPLAY-OBSERVE | 3 | `test_pr_rebase.py::test_owned_pr_rebase_publishes_with_original_lease` and conflict-message case: observe exactly two range reads per verification, repeated decision gets new list objects; full IDs feed tree probe, raw message newline comparisons still inspect both commits. Depends R; no fixture-reuse ownership bundled. |
| B-ADVANCED | 2 | `test_pr_recovery.py::_advanced_pr_seed`, local `pr_repo`, `legacy_pr_repo`, `_advance_upstream`, `_prepare`, named consumers: setup-only upstream advancement copied; legacy branch still creates/rebases independently; seed tracking/refs survive case effects. Depends B-PUBLISHED/B-SEED. |
| B-RECOVERED | 2 | `test_pr_break_glass.py::_recovered_pr_seed`, `_copy_recovered_pr`, `recovered_pr` and direct advanced-head consumers: prepared divergent/refspec topology copied, remap rewrite identity, fresh observed remote tip, each case still executes recovery/confirmation/fetch/audit; seed records/refs unchanged. Depends B-BASE/B-SEED and B-PUBSTUB for its scenario adapter. |
| B-BACKED | 2 | `test_pr_recovery_advanced_head.py::backed_publication`, `_build_backed_pr_seed`, two seed and two concrete fixtures, `_copy_backed_pr`, copy-isolation regression and backed consumers: retain exact prior publication/checkpoint/base variant and unpublished content; mutating first copy's checkpoint/record/ref/head cannot affect second/seed. Depends B-RECOVERED; no sharing live checkpoint authority. |
| B-RECOVERY-FAULT | 3 | `test_pr_recovery.py::test_sync_aborts_before_rebase_when_backup_cannot_be_written` and imperfect-evidence `batch_probe`: inject failure at real `_git_bytes` batch transport; encoding failure targets exact NUL-framed local evidence request once and retains existing refusal/confirmation behavior. Depends A. |
| B-CONTEXT | 2 | `test_context_resolution.py::_context_repo_seed`, `_copy_context_repo`, `adopted_repo`, `test_context_repo_copies_preserve_seed_and_sibling`: worktree repair plus fresh registries/tracking; branch/config/file changes in first copy leave seed/second unchanged. Depends B-SEED, not authority caching. |
| B-CREATION | 2 | `test_creation_tail_repair.py::_creation_anchor_seed`, `_creation_fixture`, `native_creation`, isolation regression: private native object stores and refs; empty fixture topology explicitly on disk; changes in harness cannot affect knowledge/seed. Depends B-SEED, preserves fresh config reads and real creation-tail effects. |
| B-DOCTOR | 2 | `test_doctor_pair_integrity.py::test_doctor_json_repairs_pair_from_untracked_knowledge_cwd`: stub only unrelated `daemon_health.doctor_report`; pair-repair body still proves intended tracking repair, not daemon health. |
| B-LANDING | 2 | `test_finalize_landing.py` base/merged/handmade seed, copy, commit and consumer changes: preserve reflogs, private stores/no alternates/hardlinks, relocate URLs, copy known published-head marker; commit helper no longer returns an unused OID, actual OID callers explicitly resolve it. Real landing decisions remain per-case. Depends B-SEED. |
| B-LANDING-ASSERT | 2 | `test_the_probe_moves_no_ref` assertions moved into `test_a_multi_commit_squash_later_edited_upstream_is_landed`; `test_the_pr_base_is_asked_only_when_the_default_branch_lacks_the_work` relocated unchanged. Preserve no-ref-movement and provider-call-zero checks, not test deletion/waiver. Collection/node mapping must explicitly account for consolidation. |
| B-REFSPEC | 2 | `test_finalize_precondition.py::_refspec_worktree_seed`, `_copy_refspec_worktree`, `refspec_worktree`, isolation regression: retain all private objects/reflogs and unlanded base topology; copied commit/push cannot affect seed/sibling. Session seed uses existing factory, not shared mutable Git. |
| B-INCONCLUSIVE | 3 | `TestContentExceedsMergedHeadFailsClosed.test_every_inconclusive_lookup_fails_closed`: five old cases become labeled missing-head, missing-content, unresolvable-head, unresolvable-content, rev-list-error loop, each asserts True refusal. Preserve all five branches and report failing label; collection mapping required, not five cases silently dropped. Depends G when accompanying its actual consumer. |
| B-LEASE-SEED | 2 | `test_lease_store.py::_settings`, `_released_history_seed`, `released_history`, consumers: real released history copied per case; deepcopy result snapshots; each sweep/race operates on private remote; seed ref unchanged. No lease/store authority cached across operations. |
| B-LEASE-ASSERT | 2 | old stale-squash idempotence case and new `test_squash_stale_dry_run_then_apply_is_idempotent`: preview leaves ref, apply reports exact old/new OIDs, second apply empty and remote stable. It is asserted consolidation, not deletion. Depends B-LEASE-SEED only for setup. |
| B-LEASE-CLI | 2 | `test_lease_cli.py` renamed acquire/renew cases: explicitly retained disposition survives plain renewal; default acquire then flagged renewal settles durable at-rest verified by inspect. Maps the two removed renew identities to those exact assertions; no production CLI change. |
| B-LEGACY-CONFIG | 2 | `test_legacy_claim_handoff_accept.py::graph.committed_config`: only fixture-owned non-Git anchors return empty committed layer; all other anchors delegate original reader. On-disk fixture configs and fresh authority behavior remain. |
| B-CACHE-SEED | 2 | `test_local_cache_refresh.py::_copy_workspace_seed`, `copy_workspace`, `test_copy_workspace_stores_are_independent`: only empty Git setup reused; index/config/info-exclude/objects/refs mutate private copy, isolated home stays empty. Depends B-SEED. |
| N-STAT | 4 | `test_local_cache_refresh.py::test_nonregular_input_is_rejected_before_open`: preserve every actual `st_*` field in FIFO simulation, including native reparse attributes; assert rejection before open and field parity. Separate from cached fixture. |
| B-CLI-USAGE | 5 | `test_module_invocation.py::test_pr_watch_usage_does_not_crash_on_circular_import`: current wait/cursor/snapshot/status/threads usage string; preserve actual module subprocess and circular-import-negative behavior. No CLI implementation hunk claimed. |
| B-RECONCILE | 4 | `test_reconcile.py::_stage_installation_context`, both staging callers and regression: stage every native `.py`/`.ps1` sibling, preserve fixture override, repair missing guest helper; do not silently replace a current override or omit native dependencies. |
| B-REGISTRY-ASSERT | 2 | `test_registry_paths.py` standalone missing-receipt case moves into namespaced registration case before valid receipt publication; exact unreadable-identity exception retained. Combined identity must still exercise both refusal and later registration persistence. |
| B-REPOS-CONFIG | 2 | `test_repos.py::_init_config_repo` plus named backfill/add-repo consumers: real Git config only where consumed, plain directory for preflight-skipped cases; preserve credential host/account/unknown-name refusals. `_init_repo` continues identity/commit setup for real-history tests. Terminal LF has no new behavior. |
| B-CLONE-IDENTITY | 2 | `test_repos.py::_bare_anchor_from_clone`: remove clone-local user identity only if its actual commit-tree consumers provide their own identity; exact outstanding validation question is whether every such caller still resolves nonempty commits under isolated HOME. Candidate belongs here, not a broad Git batching batch; keep/reinstate identity if that gate fails. |
| B-RESOURCE | 2 | `test_resource_producer_finalize.py::_final_owner_seed`, `final_owner`: copy anchor/origin privately, construct fresh tracking/project/install dirs and relocate origin; transitive finalization retains real resource-owner decisions. Depends B-SEED and original `_env` builder, not mutable owner snapshots. |
| B-TERMINAL | 2 | `test_terminal_conclusion.py::_worker_seed`, `_worker`, copy-isolation regression and remaining 58 consumer/helper hunks (including `TestReciprocalDisposal._harness`): copied template-free Git topology, repaired links and URLs; fresh tracking per copy; actual live/foreign/dirty/claim/finality decisions and reciprocal disposal remain unchanged. Commit/push/removal of first copy leaves seed/second byte-identical. No cached liveness. |
| B-GENERATION | 5 | `test_independent_execution_spaces.py::test_session_register_deregister_fence_raced_fresh_foreign_record`: documented write-verb generation and required `min_version` both 3, preserving raced-foreign-record fence; not lowering API admission. |
| B-PROVIDER-RECORD | 2 | `test_providers.py::provider_record_repo`, `TestCreatePRAutoOpen._open_record`, `_ready_config`, fixture substitutions: test `_finish_auto_open` and draft/hold/provider behavior using persisted creating/open records instead of unrelated Git create setup; keep actual create/publication integration cases. |
| B-PROVIDER-ASSERT | 2 | `TestCreatePRAutoOpen` attribution/body controls: missing body empty, explicit disabled attribution exact `"Hello"`, malformed codename yields neither marker nor recorded attribution head; removed duplicate cases mapped to those assertions, never waive malformed-marker safety. Separate from record setup. |
| N-SHELL | 4 | `native_shell_support.py::admit_native_bash`, collection regression and `test_hook_ipc.py` lifecycle-helper test: validate host-native Bash without enabling optional tiers, reject WSL; explicit resolver still enforces opt-in; POSIX args and resolved PowerShell command use correct invocation boundary. No new skip or native-qualification credit. |
| N-PANE-DIAGNOSTICS | 4 | `test_handoff_cutover.py::TestPaneWrapperInitialPrompt` changed launch fixture and two added tests: no-window 15-second child invocation, exact pwsh prefix/argument handling, trace only under profiling/debug, bounded receipt/child/output-tail diagnostics distinguish before-receipt/before-child/after-child. Relates to native W4-PANE but independent diagnostic fixture contract. |
| N-HTTP-DEADLINE | 4 | `test_azure_pr_observation.py::test_native_transport_absolute_deadline_bounds_slow_stream`: immutable SSL fixture context, preconnect expiry case, request-arrival event, stoppable stream worker and bounded server cleanup; deadline includes opener delay, expired-before-connect sends no request. No increased deadline. |
| N-STAMP | 4 | `test_installer_powershell51.py::test_native_stamp_directory_copy_preserves_complete_tree`: independently parameterize powershell.exe/pwsh, preserve hidden/nested tree except `__pycache__`, verify bytes for retained paths. Native report's M6707 documents production exclusion already landed; these new test hunks remain candidates, not an already-merged test claim and not #6909 ownership. |
| N-UTF8-LAUNCH | 4 | `test_launch_request.py::test_real_powershell_transport_preserves_encoded_intent`: explicit Python UTF-8 mode in real shell round-trip, retain encoded intent assertions. Separate from attribution. |
| N-UTF8-AUTHORITY | 4 | `test_pr_authority.py` native installer-registration child: explicit UTF-8 Python mode and decoding, retain registration isolation/authority assertions. Separate from N-UTF8-LAUNCH's launch consumer. |
| W4-BIND | 4 | Reuses the **existing native-report ID**, only `test_hook_ipc.py:2`: actual native emitter trace phases start/discovered/resolved/complete with positive timestamps, timeout prints partial stderr and raises the same exception once/no retry. Depends native-report generator/emitter changes, not installer copier. |
| M-MOVE | 2 | Pure relocation/deletion/references/contract markers of `test_pr_ops.py` test definitions, `_git`, unmodified setup methods and 220 AST-identical tests. Exact destination table above, fixture semantic deltas below excluded. Collection/node/parameter bijection remains required; contract marks do not admit integration tests to required unit CI. |

### Mixed-hunk ownership partitions (unique changed-subspan owners)

Line numbers here refer to the pinned **source**; old removals map to the
named new symbol, except the explicit removed-case mappings. Context lines
are not new ownership. Imports are owned by their introduced name, comments
by the immediately described symbol. Shared imports have one inventory
owner; each extraction adds only the imports its actual consumer needs
and its current baseline lacks. Sharing a standard-library import does
not create a behavioral dependency between otherwise independent batches.

- **MX-FINAL**, `finalize_ref.py:1`: introduced `re` import -> S;
  introduced `git_tree_diff` import -> F. G also needs `re` for its own
  complete-ID validation; that import alone does not make G depend on S.
  No mixed finalization mega-batch.
- **MX-SEEDS**, `tests/conftest.py:1,4,7`:
  `json`, `os`, `re`, `contextmanager`, `_seed_directory` (542-576) ->
  B-SEED; `shutil`, `_build_pr_repo_seed` (579-618), `_pr_repo_seed`
  (622-627), `_copy_pr_repo` (630-703), `pr_repo` (763-764) -> B-BASE;
  `record_repo` (517-538) -> B-RECORD;
  `_published_pr_seed` (768-778), `published_pr_repo` (782-784),
  `_published_refspec_pr_seed` (871-887), `published_refspec_pr_repo`
  (891-892) -> B-PUBLISHED;
  `_rebased_refspec_pr_seed` (788-796), `_copy_rebased_pr_repo` (799-810),
  `rebased_refspec_pr_repo` (814-816), `_rebased_snapshot_pr_seed`
  (820-828), `rebased_snapshot_pr_repo` (832-833), `rebased_pr_repo`
  (837-838) -> B-REBASED;
  `_fork_pr_seed` (842-862), `fork_pr_repo` (866-867) -> B-FORK.
  Existing installer fixture immediately before these additions is context,
  not newly assigned installer work.
- **MX-HELPERS**, `pr_test_helpers.py:1`:
  `_git` (11-12), `PRWorkflowSetup._refspec_config`, `_fork_config`,
  `_move_head_to_fork`, `_required_config` -> M-MOVE;
  `_stub_remote_target` (15-30) and introduced `replace` import ->
  B-PUBSTUB; `_fork_headed_rerun`'s added already-prepared-state checks
  and conditional initial create -> B-FORK; its unchanged prior setup
  statements -> M-MOVE. Other new-file imports serve M-MOVE.
- **MX-MOVE**, the seven listed new-file hunks with modified methods:
  relocation/imports/class headers/inherited `PRWorkflowSetup` and contract
  marks -> M-MOVE, except finalization `usefixtures("git_only_mux")` ->
  B-MUX; each changed case's signature/setup/assertion subspan -> the
  exact owner(s) in the 88-case inventory below. Within any multi-owner
  case the fixture replacement/removal belongs to its fixture batch,
  `_stub_remote_target` call replacement belongs B-PUBSTUB, reload/
  revision changes belong B-OBSERVATION, and role-exception assertions
  belong B-ROLE. No changed statement is owned twice.
  `test_pr_tracking.py::recorded_pr` (16-25) -> B-RECORD;
  `TestCreatePRRoleResolution._fake_permission_provider` -> B-ROLE.
  The five M-MOVE-only new modules have no changed test AST bodies.
- **MX-LANDING**, `test_finalize_landing.py:2,8`:
  new `shutil`/`_seed_directory` imports and `merged_env` fixture
  substitution -> B-LANDING; module `git_only_mux` mark -> B-MUX;
  removed then relocated `test_the_pr_base_is_asked_only_when_the_default_branch_lacks_the_work`
  -> B-LANDING-ASSERT. Repeated code context owns nothing.
- **MX-LEASE**, `test_lease_store.py:8,9,10`:
  removal of `test_squash_stale_is_idempotent_on_an_already_squashed_ref`
  -> B-LEASE-ASSERT (its checks live in the renamed dry-run/apply case);
  `test_squash_stale_filters_by_kind` fixture/signature/unpack changes ->
  B-LEASE-SEED; removed repeated machine acquire/renew/release setup maps
  to `_released_history_seed`, also B-LEASE-SEED;
  dry-run method rename to `test_squash_stale_dry_run_then_apply_is_idempotent`
  and added apply/second-apply/OID assertions -> B-LEASE-ASSERT;
  its `released_history` argument/unpack and the following reacquired-ref
  case's argument substitution -> B-LEASE-SEED.
- **MX-RECOVERED**, `test_pr_break_glass.py:3`:
  `_recovered_pr_seed`, `_copy_recovered_pr`, `recovered_pr` ->
  B-RECOVERED; only `_copy_recovered_pr`'s `_stub_remote_target` call ->
  B-PUBSTUB. Its remote-tip observer is invocation-local B-RECOVERED
  fixture setup, not shared observed authority.
- **MX-BACKED-TYPES**, `test_pr_recovery_advanced_head.py:2`:
  replacement `RecoveredPR = tuple[Config,str,Path,Path,str,str]` ->
  B-RECOVERED; added seven-field `BackedPR` type -> B-BACKED. These are
  test tuple shapes, not new runtime evidence types.
- **MX-IMPERFECT**, `test_pr_recovery_imperfect_evidence.py:1,2`:
  imported recovered/backed fixtures/types and `backed_pr` signature/
  unpack replacement -> B-BACKED; `pr_rebase` import and new
  `encoding_probes` accumulator -> B-RECOVERY-FAULT, as used by the exact
  NUL request interception in hunks 3-4.
- **MX-PROVIDER**, `test_providers.py:4,14,15`:
  `provider_record_repo` signature/unpack and `_open_record` call changes
  -> B-PROVIDER-RECORD; explicit body and malformed-codename no-marker/
  no-attribution-head assertions -> B-PROVIDER-ASSERT.
  Removal of `test_codename_mode_skips_marker_for_a_malformed_codename`,
  `test_codename_mode_skip_does_not_record_attribution_head`, and
  `test_no_attribution_omits_marker` maps to the strengthened equivalent
  assertions above -> B-PROVIDER-ASSERT. Removing unused fixture parameters
  from `test_attribution_keys_are_consistent` -> B-PROVIDER-RECORD.

No MX item is a new unresolved ownership question. Implementation must first
split the identified statements/functions and retain their prerequisite
imports. The explicit B-CLONE-IDENTITY consumer question is a concrete batch
validation obligation, not an unknown census owner.

### Exact 88 changed-case identity inventory

For each group, every code-fenced test name expands to
`tests/test_pr_ops.py::<old-class>::<name>` ->
`tests/<new-file>::<new-class>::<name>`. Names are unchanged in this
308-case split; changed classes and files are stated explicitly.
Every other original test name has a unique unchanged-body destination in
the earlier 308 mapping. This inventory accounts for **6+1+11+26+14+24+6
= 88** changed bodies, not 88 new tests.

All 88 per-function decorator AST lists are unchanged. Eighty-five have no
decorators. The three parameterized methods retain exactly:

- `test_set_pr_reassignment_discards_previous_tip_and_lifecycle`:
  `identity=["number","repo","provider"]` and
  `competing_revision_increment=[0,1]` (same order/decorator product).
- `test_a_stale_writer_saving_after_a_reassignment_never_restores_the_old_pr`:
  `change=[{"provider":"ado"},{"number":99}]`.
- `test_push_changes_updates_a_fork_headed_pr_on_the_fork`:
  `scheme=["snapshot","refspec"]`.

The old module had no `pytestmark`; every destination adds the exact
`pytest.mark.contract("agent_worktrees.pr_ops.<suffix>")` for suffixes
`create`, `create_cli`, `create_fork`, `create_refspec`, `finalize`,
`attribution`, `claims`, `helpers`, `publication`, `status`, `tracking`,
`push` respectively. Finalize additionally adds
`pytest.mark.usefixtures("git_only_mux")`. Those module marks and inherited
`PRWorkflowSetup` dependencies are **not** credited as proven collection
parity. They belong to M-MOVE/B-MUX validation, with unchanged integration
admission and conservative old/new coverage-node mapping.

Delta codes below name exact changed subspans:

- **PUB** = B-PUBLISHED: fixture argument/unpack `pr_repo` ->
  `published_pr_repo`; remove setup-only initial create/success check,
  retain later subject operations/assertions.
- **FORK** = B-FORK: fixture argument/unpack or `_fork_headed_rerun`
  input `pr_repo` -> `fork_pr_repo`; prepared fixture owns old setup.
- **REC** = B-RECORD: `pr_repo` -> `record_repo` argument/unpack only.
- **RPR** = B-RECORD: `pr_repo`/`_fork_headed_rerun` setup ->
  `recorded_pr` argument/unpack; explicit `rec.pr.remote="fork"` where
  recorded below replaces the old scenario's inferred state.
- **STUB** = B-PUBSTUB: only old `push_slug` monkeypatch/import/local
  replaced by `_stub_remote_target(monkeypatch,"fork","alice/ext")`.
- **LOAD** = B-OBSERVATION: only new `tracking.load_record(rec.yaml_path)`
  after persisted changes; do not reuse a stale local record.

#### Old `TestCreatePR` -> `test_create_pr.py::TestCreatePR` (6)

| Name | Delta / owner |
|---|---|
| `test_idempotent_rerun` | PUB |
| `test_reused_open_pr_keeps_incremental_commits_unsquashed` | PUB |
| `test_reused_open_pr_with_empty_base_sha_still_records_patch_id` | PUB |
| `test_reused_open_pr_refuses_reuse_push_with_no_persisted_head_sha` | PUB |
| `test_reused_open_pr_refuses_to_overwrite_divergent_remote_head` | PUB |
| `test_reused_open_pr_refuses_to_resurrect_deleted_remote_head` | PUB |

#### Old `TestCreatePRRoleResolution` -> `test_create_pr_fork.py::TestCreatePRRoleResolution` (1)

| Name | Delta / owner |
|---|---|
| `test_maintain_auto_open_uses_effective_self_merge_flow` | B-ROLE: unchanged `pr_repo,monkeypatch` dependencies; add exact exception-call assertion using fake created head. Its fake helper changes return type and head-bound exception API, not fixture reuse. |

#### Old `TestPRFinalizeAndPush` -> `test_finalize_pr.py::TestPRFinalize` (11)

Each case retains its `PRWorkflowSetup` inherited scenario helpers; the
class also depends on B-MUX through its module mark.

| Name | Delta / owner |
|---|---|
| `test_precondition_ok_after_create_pr` | PUB |
| `test_precondition_detects_unpushed` | PUB |
| `test_precondition_ok_after_merge` | PUB |
| `test_finalize_refuses_dirty_worktree_at_merged_head` | PUB |
| `test_finalize_reruns_precondition_after_lock_acquired` | PUB |
| `test_finalize_catches_race_commit_landing_during_process_termination` | PUB |
| `test_finalize_rechecks_precondition_before_reconciliation` | PUB |
| `test_precondition_ok_after_merge_remote_branch_deleted` | PUB |
| `test_reconcile_aligns_worktree_base_after_merge` | PUB |
| `test_reconcile_fast_forwards_anchor_default_branch` | PUB |
| `test_reconcile_fast_forwards_base_when_head_on_base` | PUB |

#### Old `TestPRFinalizeAndPush` -> `test_pr_publication.py::TestPRPublication` (26)

`PRWorkflowSetup` remains the shared scenario base; `_branch_on_both` and
`_blocked_while` remain non-collected helpers. Distinct fixture and stub
subspans below have distinct owners.

| Name | Delta / owner |
|---|---|
| `test_a_branch_on_both_remotes_goes_where_the_prs_head_is` | PUB |
| `test_a_branch_on_both_remotes_without_a_deciding_head_refuses` | PUB |
| `test_without_a_fork_remote_the_repo_remote_is_used` | PUB |
| `test_an_unreadable_fork_remote_refuses_rather_than_guessing` | PUB |
| `test_a_disabled_fork_config_ignores_a_local_fork_remote` | PUB |
| `test_a_recorded_fork_remote_that_is_gone_refuses` | PUB |
| `test_a_legacy_fork_pr_whose_configured_fork_remote_is_gone_refuses` | PUB; LOAD |
| `test_a_legacy_targets_tips_and_identity_are_one_locked_snapshot` | PUB; LOAD |
| `test_retiring_a_pruned_pr_reads_its_remote_and_branch_in_one_locked_snapshot` | PUB |
| `test_a_deleted_head_is_gone_even_beside_an_archived_branch_of_the_same_name` | PUB |
| `test_the_publication_lock_is_reentrant_within_a_thread` | PUB |
| `test_a_missing_fork_remote_decides_nothing_even_when_origin_matches` | PUB |
| `test_a_fork_remote_without_the_branch_resolves_a_usable_target` | PUB |
| `test_the_reusing_fast_path_forwards_the_forks_identity` | PUB |
| `test_create_pr_rerun_updates_a_fork_headed_pr_on_its_fork` | FORK; STUB |
| `test_a_fork_repointed_to_the_same_slug_on_another_host_refuses` | FORK; STUB |
| `test_a_recorded_fork_remote_repointed_elsewhere_refuses` | FORK |
| `test_a_fork_remote_whose_push_url_names_another_repo_refuses` | FORK |
| `test_a_fork_repointed_after_it_was_chosen_is_refused_at_push_time` | FORK |
| `test_a_fresh_fork_push_repointed_before_the_lock_is_refused` | FORK |
| `test_a_fresh_fork_target_is_checked_by_its_full_identity_under_the_lock` | FORK; STUB; B-FORK owns explicit `rewrite_owner= rewrite_identity=""` setup for the legacy/unowned target scenario |
| `test_an_explicit_fork_owner_survives_a_rerun` | FORK; STUB |
| `test_create_pr_rerun_never_overwrites_a_fork_head_another_checkout_pushed` | FORK |
| `test_create_pr_rerun_refuses_when_the_recorded_fork_is_gone` | FORK |
| `test_a_probe_matches_the_exact_branch_not_a_suffix` | PUB |
| `test_a_role_overrides_fork_remote_is_found_too` | PUB |

#### Old status classes -> `test_pr_status.py` same classes (14)

| Old class = new class | Name | Delta / owner |
|---|---|---|
| `TestRefreshHeadObservation` | `test_concurrent_reassociation_rejects_returned_observation` | B-OBSERVATION: unchanged arguments; competing writer increments `current.active_pr().pr_revision`, preserving actual stale-observation contract. |
| `TestPRStatusLive` | `test_live_block_present_when_provider_ok` | REC |
| `TestPRStatusLive` | `test_live_verdict_reports_comment_when_review_blocking_false` | REC |
| `TestPRStatusLive` | `test_live_disabled_skips_provider` | REC |
| `TestPRStatusLive` | `test_live_enabled_still_reconciles` | REC |
| `TestPRStatusLive` | `test_live_best_effort_on_provider_error` | REC |
| `TestPRStatusLive` | `test_live_omitted_when_no_number` | REC |
| `TestPRStatusLive` | `test_live_self_merge_note_surfaced_when_bypassable` | REC |
| `TestPRStatusLive` | `test_live_self_merge_note_absent_when_not_bypassable` | REC |
| `TestPRStatusLive` | `test_live_self_merge_note_absent_on_non_self_merge_repo` | REC |
| `TestPRThreads` | `test_threads_listed` | REC |
| `TestPRThreads` | `test_threads_resolve` | REC |
| `TestPRThreads` | `test_threads_unsupported_degrades` | REC |
| `TestPRThreads` | `test_threads_no_pr` | REC |

#### Old `TestSetPRAndStatus` -> `test_pr_tracking.py::TestSetPRAndStatus` (14)

| Name | Delta / owner |
|---|---|
| `test_status_no_pr` | REC |
| `test_status_missing_record` | REC |
| `test_set_pr_creates_block` | REC |
| `test_set_pr_derives_repo_slug_from_github_url` | REC |
| `test_set_pr_derives_repo_slug_from_path_hosted_gitea_url` | REC |
| `test_set_pr_repo_change_clears_attribution_evidence` | REC |
| `test_set_pr_leaves_repo_unset_for_an_unparseable_url` | REC |
| `test_set_pr_freezes_attribution_and_stamps_pr_id` | REC |
| `test_set_pr_freezes_using_resolved_config_not_ambient` | REC |
| `test_set_pr_identity_change_clears_head_observation` | REC |
| `test_set_pr_invalid_state` | REC |
| `test_set_pr_state_transition` | REC |
| `test_set_pr_missing_record` | REC |
| `test_set_pr_backfills_pr_id_before_branch_correction_no_duplicate` | REC |

#### Old `TestPRFinalizeAndPush` -> `test_pr_tracking.py::TestPRTrackingUpdates` (10)

| Name | Delta / owner |
|---|---|
| `test_record_pushed_head_uses_the_locked_push_identity` | FORK |
| `test_a_stale_writer_never_erases_the_recorded_fork` | RPR |
| `test_a_stale_writer_never_erases_a_backfilled_fork_identity` | RPR |
| `test_set_pr_reassigning_a_record_clears_its_old_fork_target` | RPR; explicit remote setup |
| `test_set_pr_reassignment_discards_previous_tip_and_lifecycle` | RPR; explicit remote setup; two unchanged parameter decorators as enumerated above |
| `test_set_pr_reassigning_a_numberless_record_to_another_repo_clears_its_fork` | RPR; explicit remote setup |
| `test_set_pr_correcting_an_established_provider_clears_its_fork` | RPR; explicit remote setup |
| `test_a_stale_writer_of_another_provider_never_restores_its_fork` | RPR; explicit remote setup |
| `test_a_stale_writer_saving_after_a_reassignment_never_restores_the_old_pr` | RPR; explicit remote setup; unchanged `change` parameter decorator |
| `test_set_pr_correcting_only_the_repo_slug_case_keeps_the_fork` | RPR; explicit remote setup |

#### Old `TestPRFinalizeAndPush` -> `test_push_changes.py::TestPushChanges` (6)

| Name | Delta / owner |
|---|---|
| `test_push_changes_updates_feature_branch` | PUB |
| `test_push_changes_is_blocked_by_a_real_client_side_pre_push_hook` | PUB |
| `test_push_changes_blocks_a_leaking_recorded_branch` | PUB |
| `test_push_changes_blocks_recorded_machine_after_rename` | PUB |
| `test_push_changes_rejects_wrong_branch` | PUB |
| `test_push_changes_updates_a_fork_headed_pr_on_the_fork` | LOAD only; retains `pr_repo,scheme` dependencies and actual per-case fork creation/publication; unchanged `scheme` decorator |

### Cross-track and final admission boundary

The only imported native implementation ownership ID is W4-BIND, directly
supported by `windows-residual-native.md`. N-PANE-DIAGNOSTICS is a separate
test diagnostic contract adjacent to W4-PANE; N-STAMP's production dependency
is that report's actual M6707 reference. Nothing here is assigned to B6909:
none of these source hunks changes that owner's canonical snapshot copier.
Other native fixture batches are independently named above, not silently
absorbed into an external owner's implementation.

All 281 former U hunks now have either a narrow candidate ID or a complete
mixed-symbol ownership partition. That completes this bounded ownership
census. Actual split patches, review acceptance, parameter/collection
bijection (including explicitly consolidated tests), fixture mutation
isolation, native execution and parent same-head performance qualification
remain **unperformed**. No changed shared mutable authority is authorized;
no skip, retirement or reduced selection has been enacted.

Follow-up mechanical verification: **70 paths / 330 unique complete
header+hash keys**, no missing/extra/duplicate key, all 88 listed changed-case
identities equal the AST-derived changed set, all per-function decorators
unchanged, and the original 48 candidate + 1 retirement assignments preserved.
The 281 refined rows comprise **257 single-contract rows and 24 explicitly
partitioned mixed rows**. Final census: **329 retained candidates (including
those partitions), 1 proposed retirement, 0 blocked-unclassified, 0 invented
external-owner or already-merged claims**. Published-only provenance remains
complete as recorded above. No implementation or validation ran.
