# Validation contract

Back to [the effort](README.md) and [implementation contract](implementation.md).
Tests below are implementation obligations, not claims that new tests exist
or have passed. New test basenames are proposed explicitly.

## Fixtures and assertions

| Case | Exact home / boundary | Required assertions |
|---|---|---|
| Locked 10 → point unlocked 11 → 20 ticks | New `worktree-manager/tests/production_picker/test_point_refresh_publication.py`; actual `PickerScreen`, `LiveLoader`, Inbox and `_tick` | Gate feed at 10; apply real point response at 11; drain UI Inbox then tick 20 times. Screen and loader retain unlocked 11. Do not mock `_apply_loader_records` away. |
| Source collision | Same new screen test; existing `test_data_ssh_sources.py` and `test_current_roster_reads.py` | Local and remote sources both contain ID `tree`; remote adapter updates only its key. Local point provider is a fail-on-call spy. Verify correct source/project command, metadata and failed/unsupported remote result. |
| Ordering and reset | New `test_source_publications.py`; existing `test_current_worktrees_feed.py` and `test_current_worktrees_feed_deadlines.py` | Point 11 then feed 10, 12 and delayed point 11; simultaneous refreshes; incomplete feeds; reload epoch cancellation; same-generation stale reconnect; new generation revision 1 only after complete reset. Old generation never resurrects deleted rows. Equal-version conflicting content is diagnosed. |
| UI threading and lifetime | New point/store tests; existing `test_inbox.py` | Worker never writes screen data/debug or committed store. Mutations happen during UI drain. Picker cancellation rejects late events and terminates only owned children; no indefinite point overlay survives source retirement. |
| Gated reclaim | New `plugins/agent-worktrees/tests/test_worktrees_observe.py`; existing `test_worktrees_runtime.py`, `test_worktrees_client.py`; new Manager `test_maintenance_publication_receipts.py` | Reclaim effect succeeds once; block owner commit. Receipt/UI remain pending; unblock actual owner publication and converge on the same connected source without reload or full scan. |
| Correlation and time bounds | Same owner tests | An unrelated larger project cursor cannot satisfy the request. Receipt proves exact identity and covered facts sampled after admission/effect. Server max wait/ID limits enforced with fake clock/events; handler never waits holding the owner state lock. Queue saturation/expiry gives explicit result. |
| Contention and rollback | Existing `test_worktrees_projection.py`, `test_worktrees_recovery.py`; new observe tests | Record-stamp contention does not mean published. SQLite contention/failure leaves old head/rows/observations visible; no receipt or feed commit precedes durability. Owner election loss produces superseded, retains #7933 rollback behavior. |
| Partial bulk/new owner | New observe and maintenance receipt tests | Mixed success/failure/pending items retain individual outcomes. A new live owner appearing after reclaim is never cleared by killed-PID evidence; publication may truthfully remain locked. Missing response never retries destructive effect. |
| 10 × 1,000 unchanged real observations | Extend `test_worktrees_producer.py`, `test_worktrees_current_rows.py`, `test_worktrees_projection.py`, `test_worktrees_feed.py` | Run actual `materialize` provenance through real projection + negotiated v2 feed. After initial baseline, ten publications yield zero semantic table insert/update/delete and zero semantic `worktree` frames. Observation writes/frames are counted separately, not concealed. |
| One meaningful change | Same producer chain, title and state variants | Changing one title yields exactly one semantic row update and one semantic row frame; no selection index rebuild for title-only change. State/index changes update only the required index work. No 1,000-row normalization disguised as a “delta”. |
| Freshness | Same chain plus Manager feed/store tests | Advance fake observation clock with real observation; freshness advances independently of semantic version. Heartbeat alone advances last-contact only. Partial observation renews only covered groups. Cached/unavailable data never gains fresh timestamp. |
| Reconnect/delete/order | Existing producer/feed tests and `test_current_worktrees_feed.py` | Full reconnect matches current durable state; reorder/front insertion/empty roster exact; deletion only at commit; abort before commit keeps old view; persisted order survives advisory reopen; generation reset with revision 1 is atomic. |
| Compatibility | Existing `test_worktrees_client.py`, `test_current_roster_reads.py`, `test_current_worktrees_feed.py`; new observe tests | v1↔v1 unchanged; new consumer/old producer does not send unknown fields or claim receipt; old consumer/new producer receives truthful v1. v2 unsupported/malformed/degraded is explicit; no silent fleet fallback. |

## Command-level validation

Run from an isolated source worktree, stage new tests before snapshot runners.
Use Python 3.11 or newer and runtime-local tooling. On Linux with
Docker/devcontainers available, prefer the documented devcontainer wrapper.
On Windows use the contained source runners, not an installed runtime suite.
Set runner fixture/scratch roots inside the owned checkout when the execution
environment requires it. No tests may touch live tracking/process/monitor state.

Proposed targeted commands using existing runner interfaces:

```powershell
python tools\check-effort-vision-structure.py efforts\active\source-qualified-publication\README.md
git diff --check

# F1/F2: actual listed test modules and new observe module.
python tools\run-plugin-tests.py agent-worktrees -k "worktrees_current_rows or worktrees_projection or worktrees_producer or worktrees_feed or worktrees_runtime or worktrees_client or worktrees_recovery or worktrees_observe"

# F3/F4: the standalone containment runner has no -k passthrough.
# Run its supported Manager selection once, not one full run per case.
python tools\run-standalone-tests.py worktree-manager

python tools\check-install-contract.py
```

Before running a selection, verify the runner's `--help` and select native/
integration tiers explicitly as required by current `TESTING.md`. Existing
held-feed tests include real child stdout tests; do not report a unit-only
selection as having exercised those subprocess controls. Run `ruff check
--select F,E9` with the test/runtime environment interpreter over the exact
changed Python paths. Add one changefile for each changed shipped product;
the plan-only effort has no runtime changefile.

### Simple end-to-end (required for implementation)

Add an explicitly marked contained native test that launches an owned fixture
owner endpoint and invokes the actual source CLI for
`list --json --current --worktree-id tree --refresh`. Feed that response
through the real Manager parser/loader/Inbox, not a mocked parser result.
Stub destructive reclaim process resolution at the fixture boundary; verify
one effect invocation, real JSON receipt transmission, gated publication and
bounded wait convergence. Include a synthetic remote transport argv fixture
with local provider forbidden; no real remote agent is needed.

Keep these in a new `test_worktrees_observe_native.py` and the Manager point
refresh module's explicit native lane, using repository containment/marks.
Record full command, selected/deselected counts and actual source revision.

### Fresh-environment and live qualification

F1's durable schema evolution requires a fresh fixture database, v1 database
migration, rollback/old-reader refusal, and restart/advisory read controls.
These are clean-state integration tests, not a host installer rollout.
No installer/bootstrap change is proposed, so a full machine clean-room run
is not required for the plan-only PR; re-evaluate if implementation broadens.

Live destructive reclaim, remote dispatch, shared install/update and
pre-existing agent interaction are expressly outside authorization. Record
live external validation as **not run: authorization boundary**, not as
passed or impossible. A later authorized adopter can qualify real local and
remote transports without changing the source contract or moving ownership.

## Plan-only evidence

Validate structure, links and privacy; review code citations against the
inspected source baseline. Unit/native/clean-room/live implementation tests
are intentionally not run before there is implementation. Source PR review
and required narrative CI remain mandatory. Do not claim the three issues
fixed, close them, or mark the effort Done on a plan-only merge.
