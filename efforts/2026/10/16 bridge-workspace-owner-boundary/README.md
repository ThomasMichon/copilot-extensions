# Bridge Workspace Owner Boundary

- **Slug:** `bridge-workspace-owner-boundary`
- **Repo:** copilot-extensions
- **Branch(es):** isolated repair; publication owned by coordinator
- **Created:** 2026-10-14
- **Status:** Done
- **Vision:** Below-altitude repair of existing independent-plugin and owner-process contracts
- **Umbrella issue:** #6757

## Guiding Intent

Restore correct managed dispatch and resume without requiring sibling Python
packages in bridge's interpreter. Preserve standalone existing-checkout
CodeSpace operation and every applicable ownership fence. Completion requires
released-source proof, not merely a local candidate or an opened PR.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Repair worker | Local implementation and bounded validation | #6757 |
| Coordinator | Independent review, publication and authorized rollout | #6757 |

## Coordination

- **Topology:** One isolated repair, no child workers.
- **Host (owns PRs):** Coordinator; no publication by repair worker.
- **Delegates:** Repair worker owns transport and coupled workspace lifecycle.
- **Handoff:** Return local diff and evidence before any commit or push.
- Existing exact-record preflight work (#6704, #6696) retains its owner.

## Context

Transport reconstructs a Worktrees interpreter using `AGENT_RT_ROOT`, which
belongs to the invoking bridge installation. It then invokes `agent_worktrees`
in that interpreter. Existing attributable owner-launch helpers already provide
the required cross-process boundary. CodeSpace namespace specs separately carry
their provider-owned existing-checkout launch command and workspace.

## Request

> One agent-* should never directly import another agent-*. Reused logic either calls cross-process or uses release-time vendoring.

Public-safe scope summary: fix bridge transport/session-workspace CLI boundaries;
touch CodeSpaces only for an exactly coupled defect. Standalone existing-checkout
CodeSpace dispatch/resume must not require Worktrees unless managed-workspace
ownership is requested. Preserve claims, fences, provenance and exact workspace
identity. No installed edits, host installs, service restarts, runtime updates,
credentials, external paid execution, child workers, destructive Git, or
publication before coordinator review.

Follow-up direction (public-safe summary): repair the exact automatic-cleanup
regression without weakening pending/retrying dispatch preservation, changing
intentional explicit operator cleanup semantics, or moving owner record reads
into bridge. Add actual owner-harness and cross-process zero-turn forwarding
proof with all removals intercepted. Keep this effort Active for integration.

## Plan

### Phase 1 - Local repair and evidence
- [x] Read owning contracts and deduplicate/claim the generic repair.
- [x] Replace transport and coupled lifecycle interpreter reconstruction with owner execution.
- [x] Prove managed identity, metadata and explicit owner failure contracts.
- [x] Prove standalone CodeSpace routing without Worktrees and retain claim gates.
- [x] Update directly related documentation and add a patch changefile.
- [x] Return local evidence for independent coordinator review without committing.
- [x] Repair exact automatic-cleanup dispatch protection in the owner and prove cross-process parity.

### Phase 2 - Coordinator-owned completion
- [x] Independently review the local candidate and reconcile review findings.
- [x] Publish and merge the repair through the normal PR flow after authorization.
- [x] Confirm release promotion contains the reviewed repair.
- [x] Verify released source and correct dispatch/resume through authorized rollout.

## Validation Plan

- [x] Run bounded existing transport, ownership, acquisition and CodeSpace suites.
- [x] Run real isolated owner subprocesses with no sibling package in bridge's interpreter.
- [x] Run touched-code F/E9, architecture/bridge and install-contract guards.
- [x] Record clean-room applicability and external-lane constraints explicitly.
- [x] Coordinator records released-source and authorized running-state proof.

## Proposal

Reuse existing owner-prefix/environment helpers, including same-cell validation
and the legacy installed owner's runtime resolver. Remove transport's duplicate
runtime-root/slot algorithm. Keep remote provider workspace ownership separate
from local managed-worktree allocation. No new shared library or installer flow.

## Journal

### 2026-10-16 - Released owner boundary and preserved-session resume verified

- Released `main` snapshot `ad7376904b0e622f7f44abf516cbef560b322f36`
  contained the exact reviewed transport, session manager, lifecycle-owner and
  automatic-cleanup source blobs. The subsequent release
  `6b2fa0ac2887a8fda669dfb8356e5c6083f6729d` also delivered the already-owned
  hidden-installer diagnostic correction #6853.
- The operator-authorized unified update installed bridge `0.9.64-dev1` and
  Worktrees `1.24.68-dev1`. Actual activated source contains the independent
  owner launch and automatic cleanup guard; bridge health is available.
- A preserved managed local session resumed in its original worktree, retaining
  its previous turn and entering a new owning-agent turn. This exercises the
  failed owner-resolution boundary rather than substituting a new session.
  Existing-checkout CodeSpace independence remains covered by the real
  provider-routing tests; no external compute claim is made.
- The aggregate update returned nonzero because three other runtime installers
  failed, and anchor synchronization conservatively skipped a stale index.
  Those results are not whole-host success. They do not erase the verified
  activation and live managed-resume proof for this repair.
- This bounded owner-boundary objective is Done. The consuming application
  prototype and unrelated runtime reconciliation remain separate objectives.

### 2026-10-15 - Repair merged; promotion and running-state gates remain

- #6776 actually merged as `a4c6dac741bf3b67f92ea6d42d17d0d22568d40d`,
  after current-head approval with zero findings and successful required CI.
  Source attribution passed independently of review.
- The operator authorized normal unified rollout after release promotion and
  verification of the preserved managed dispatch. No installed files or shared
  daemon state will be patched manually.
- Post-merge CI run `38665055817` is pending. Promotion, released-source
  identity, installed-state proof and actual resume remain open; merge is not
  deployment and does not complete this effort.

### 2026-10-14 - Independent review and real owner-boundary verification

- Current-head provider review identified refusal reasons emitted on owner
  stdout being discarded when stderr was empty. Bridge failure diagnostics
  now retain stderr when present and otherwise report stdout. The real
  pending/retrying owner-harness cases assert their refusal reason reaches
  bridge logs; all 11 boundary tests pass, with one unrelated collection skip.
  Current-head review and required CI remain publication gates.
- Independent review identified automatic exact cleanup missing the bulk
  dispatch-allocation exclusion. The owner now checks fresh provenance under
  its finalization/record locks for the explicit automatic cleanup mode.
  Pending, retrying and opaque allocations are retained; manual cleanup
  behavior and the existing concluded-allocation GC route remain unchanged.
- The coordinator independently ran the real cross-process bridge callback
  against the owner parser/harness: 11 tests passed, with one unrelated
  collection skip. The owner automatic-cleanup suite passed all 10 tests.
  Fixture removals were intercepted; no operator workspace was deleted.
- The reviewer closed the named regression without a remaining high-confidence
  finding. This closes local review, not publication, release or live recovery.

### 2026-10-14 - Root cause and reservation

- Claimed #6757 after scoped public deduplication and identity verification.
- Root cause is bridge runtime-root leakage, not an absent bridge dependency.
- #6704 changes exact-record preflight, not this transport command construction.
- Implementation is explicitly local-only pending coordinator review.

### 2026-10-14 - Local candidate and validation

- Transport now reuses the existing owner launch prefix and scrubbed owner
  environment. Removed duplicate marker/slot reconstruction and sibling
  `PYTHONPATH` injection. Same-cell receipt validation and legacy owner-runtime
  revalidation remain in their existing owners.
- Coupled zero-turn cleanup invokes the same boundary for only the exact
  project/worktree, without force and with normal owner prune-safety checks.
- No CodeSpaces source change was necessary: existing provider-owned command
  routing already avoids local Worktrees allocation. Real subprocess tests
  exercise first spawn and persisted-target replay with no Worktrees import,
  while provider launch-policy refusal and existing exclusive-claim tests retain
  their independent gates.
- New tests create disposable, pip-free owner venvs. Bridge's real interpreter
  demonstrably fails `-I -m agent_worktrees`; the independent owner process
  nevertheless resolves new/resumed managed workspaces with exact project,
  worktree and caller metadata. Missing or refused owners never start a worker.
- Validation commands below ran from the candidate repository with unchanged
  runner budgets. All final selections exited zero:

| Command | Result |
|---------|--------|
| `python tools/run-plugin-tests.py agent-bridge -k 'test_transport_owner_boundary'` | 7 passed; one unrelated collection skip |
| `python tools/run-plugin-tests.py agent-bridge -k 'TestSpawnLocal or TestLocalResolveBridgeFallback or TestLocalResolvePassesProject or test_remote_resolver_ownership'` | 81 passed; one unrelated collection skip |
| `python tools/run-plugin-tests.py agent-bridge -k 'test_worktree_owner_launch or TestCodespaceExclusiveClaim or TestClaimCodespaceHelper or test_venue_launch_policy'` | 54 passed; one unrelated collection skip |
| `python tools/run-plugin-tests.py agent-bridge -k 'test_acquisition_authority or test_worktree_ownership or test_workspace_creation or test_handoff_cli'` | 108 passed; four skips |
| `ruff check --select F,E9` on all six changed Python files | Passed |
| `python tools/check-install-contract.py` | 13 plugins conformant |
| `python tools/check-agent-bridge-contracts.py` | 2 contracts, 63 fixtures conformant |
| `python tools/check-headless-launch.py` | 13 adopters conformant |
| `python tools/check-docs-consistency.py` | Passed |
| `python tools/check-effort-vision-structure.py efforts/active/bridge-workspace-owner-boundary/README.md` | Passed |
| `check-module-size.py`'s `check(..., only_paths=...)` for the three changed production modules | Passed without baseline changes |
| `git diff --check` | Passed |

- Earlier runs exposed obsolete interpreter-specific fixture imports/assertions;
  those were corrected to mock the owner boundary, not machine filesystem
  availability. Two combined selections hit the 300-second containment limit;
  a shared-consumer selection also reported all items complete but reached that
  outer limit. These were not counted as passes. The smaller final selections
  above completed normally; no time, process, memory or storage budget was widened.
- Clean-room provisioning is not applicable: no installer, provisioning hook,
  runtime selection contract or dependency manifest changed. Disposable fresh
  venv and real subprocess checks cover the touched first-use execution boundary.
- Live external-venue and running-release checks were not run: this slice
  explicitly prohibits host/runtime updates, service restarts and external
  execution. Coordinator retains independent review, publication, authorized
  rollout and released-source/running-state proof.
- Documentation impact is the plugin's workspace-command ownership contract;
  daemon lifecycle and graceful-cutover protocol are unchanged.
- Local evidence is complete for coordinator review. No commit, push, PR,
  runtime update or service-state change was performed. Phase 2 remains owned
  by the coordinator; this effort is not Done or released.

### 2026-10-14 - Exact automatic-cleanup dispatch preservation

- Independent review reproduced a real regression: bulk cleanup excludes
  `dispatch_attempt`, while ordinary exact `reap_one` intentionally does not.
  A zero-turn retry can have no claimant, so claimant liveness cannot substitute
  for dispatch allocation ownership.
- Added owner-local `cleanup --worktree-id <id> --automatic`. Its dispatch guard
  reads the fresh record while holding the existing finalization/record locks.
  Pending, retrying and opaque dispatch provenance is preserved. The CLI
  rejects unscoped automatic cleanup and automatic force. Explicit operator
  exact cleanup remains unchanged, and concluded managed dispatch records still
  use the existing managed-GC route.
- Bridge forwards the exact project/worktree with `--automatic --json
  --include-unused`; it never reads Worktrees state and never retries an older
  owner's unsupported mode without the guard.
- Scoped open-PR deduplication found no competing automatic exact-cleanup
  repair. The nearby #6542/#6487 managed cleanup and terminal-conclusion work
  retains its ownership; those implementation files were not modified.
- Both affected plugins have patch changefiles. No dependency manifest,
  installed payload, runtime, shared service or external venue was changed.
- Actual owner-harness validation intercepts all removal seams and checks
  fixture directories/records remain present. The cross-process proof imports
  production bridge in its own test interpreter and drives the real Worktrees
  parser, cleanup and locked record decision in a separate owner interpreter.
  No sibling package or fake dependency is added to either runtime.

| Command | Result |
|---------|--------|
| `python tools/run-plugin-tests.py agent-worktrees -k 'test_cmd_cleanup_skips_unconcluded_dispatch_attempt_worktree'` | 1 passed |
| `python tools/run-plugin-tests.py agent-worktrees -k 'test_cleanup_automatic'` | 10 passed: pending/retrying/opaque negatives, normal-unused positive, explicit cleanup compatibility, lock-time provenance change, concluded managed-GC positive, invalid modes, bulk preservation |
| Set `COPILOT_EXTENSIONS_WORKTREE_OWNER_TEST_PYTHON` to `.test-venvs/win32/agent-worktrees/Scripts/python.exe`; `python tools/run-plugin-tests.py agent-bridge -k 'test_transport_owner_boundary'` | 11 passed; one unrelated collection skip; all three actual owner-process cases executed |
| `python tools/run-plugin-tests.py agent-worktrees -k 'test_cleanup_revalidation_manual or test_cleanup_revalidation_auto_clean'` | 49 passed |
| `python tools/run-plugin-tests.py agent-worktrees -k 'test_cleanup.py or test_dispatch_attempt_policy_concludes_exact_acp_allocation'` | 39 passed, including canonical dispatch conclusion |
| `ruff check --select F,E9` on the six Python files changed in this correction | Passed |
| `python tools/check-install-contract.py` | 13 plugins conformant |
| Scoped `check-module-size.py` API for the three production modules changed in this correction | Passed without baseline changes |
| `python tools/check-agent-bridge-contracts.py`; `python tools/check-effort-vision-structure.py efforts/active/bridge-workspace-owner-boundary/README.md`; `git diff HEAD --check` | Passed |

- Provisioning clean-room remains inapplicable: the correction changes an owner
  CLI cleanup mode, not install or provisioning. Released-source and running
  integration proof remain pending coordinator review and authorized rollout.
