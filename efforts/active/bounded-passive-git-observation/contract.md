# R1/R2 implementation contract

Back to [the effort](README.md). All new interfaces and implementation choices
below are **agent-recommended**, subject to source review and exact-hunk
integration admission. Paths use `A = plugins/agent-worktrees/src/agent_worktrees`
and `T = plugins/agent-worktrees/tests`.

## Current causal counterexamples

Source baseline: `68f40c7209f1ed6c66d381bc80ac02611bb3d680`.

| Current path | Failure and retained contract |
|-------------|-------------------------------|
| `A/git_ops.py`, `_get_current_branch_safe`, `classify_worktree` | The initial `rev-parse --abbrev-ref HEAD` has no timeout and precedes the timeout-catching block and ACTIVE check. A stalled first call never reaches the existing UNKNOWN fallback. |
| `A/git_ops.py`, `_classify_git_state._g` | Every subsequent command gets a fresh 15 seconds. Several slow successful calls exceed a single 15-second observation ceiling. |
| `A/status_bar_cli.py`, `_resolve_remote_default_branch` | `_has` and `symbolic-ref` have no timeout. `ls-remote` alone has 10 seconds. A stalled explicit-hint probe blocks before even reaching the bounded network call. |
| `A/status_bar_cli.py`, `_render_status_segment`, `_status_segment_json` | Both resolve defaults and possibly an untracked branch before their classifier exception handlers. Bounding only `classify_worktree` leaves these prefixes unbounded. |
| `A/__main__.py`, `_monitor_sweep` served-session block | `project_lock` encloses config selection, context/segment rendering, optional title persistence and handoff work. Direct segment rendering can perform the unbounded default probe while resident hook admission needs the same lock. |
| `A/__main__.py`, `_monitor_sweep` warm-project block; `A/worktrees_producer.py`, `_publish_active_project` | `project_lock` encloses `roster_publisher()`. That callback gathers records/session/mux/Git facts and materializes/publishes. A direct-render fix alone does not remove this second observation path from the admission lock. |
| `A/git_ops.py`, `_classify_git_state` status read | `git status --porcelain` inherits `core.fsmonitor`, including per-worktree policy. After stop, the next passive classification may start a new daemon. |
| `A/tracking_session_registry.py`, `stop_fsmonitor_daemon`; `A/session_catalog.py`, `_maybe_reap_fsmonitor` | Cleanup only stops the watcher, after a fresh mux observation and real-session check, with a 300-second cooldown. The cooldown does not prevent restart. |
| `A/push_timeout.py`, `run_bounded` | Existing Windows kill-on-close Job ownership is useful, but `communicate(timeout=5)` after the main wait is an additional allowance. Simply passing `kill_tree=True` does not establish one total deadline. |

## R1a — deadline propagation

### Budget and API

- Retain the existing `_CLASSIFY_GIT_TIMEOUT = 15.0` value as the default
  **total Git-observation ceiling**, not a new configurable SLA or a new
  startup/hook/UI deadline. General `git(timeout=None)` and foreground
  network/push behavior remain unchanged.
- Add keyword-only `observation_deadline: float | None = None` to
  `classify_worktree`. It is an absolute `time.monotonic()` instant, internal
  to one process. If absent, set `D = now + 15.0`; if supplied, use
  `min(supplied, now + 15.0)`. Never renew a supplied deadline.
- Give `_get_current_branch_safe`/its public `current_branch` wrapper an
  optional timeout and contained-run option; preserve existing no-argument
  behavior for unrelated callers. Classification passes the remaining
  budget and containment, including to the first call.
- Pass the same `D` to `_classify_git_state`; `_g` recomputes remaining time
  immediately before every spawn. Exhaustion raises `TimeoutExpired`
  without starting a new process. No duplicated fresh 15-second allowances.
- Add an optional `observation_deadline` to `git()` solely to forward that
  same absolute instant to the opt-in contained runner. Passing a relative
  timeout alone must not recreate the cleanup allowance at each helper layer.
  Non-observation callers omit it and retain their current contract.
- Add optional `observation_deadline` to `_resolve_remote_default_branch`
  and `_detect_upstream_branch`. Independent resolver entry creates the
  same default ceiling; nested callers retain their supplied deadline.
  The optional network query keeps its existing 10-second cap, further
  limited by the remaining observation budget.
- `_render_status_segment` and `_status_segment_json` establish `D` before
  default/branch probing and pass it through resolution and classification.
  Their compatibility overrides and tests accept/forward it without
  changing defaults, network opt-in or default-branch precedence.
- This is a Git-observation ceiling, not a claim that session scans,
  rendering, filesystem access or a complete roster traversal fit 15 seconds.
  Existing shorter caller deadlines, where passed, dominate.

### Failure and classification

Keep filesystem GONE/zombie and positively known ACTIVE semantics. A known
live session remains ACTIVE even if optional branch metadata times out;
branch metadata is then unavailable, not an invented detached branch or drift.
For non-active classification, a timed-out first or later call returns
UNKNOWN. Preserve completed observations such as branch/drift only when
actually obtained. A non-timeout detached HEAD still follows existing logic.

Preserve `fetch_requested` as **actually attempted**, not merely requested:
initial/default timeout before fetch leaves it false. A timeout on/after an
attempt leaves requested/failed true; a nonzero fetch remains explicitly
failed while offline classification may continue. Do not represent default
probe timeout as a successful config fallback, clean status, zero counts,
ORPHAN, MERGED, FINAL or fresh removal evidence. The segment and JSON
siblings must expose UNKNOWN consistently for an existing checkout, rather
than turn this timeout into an empty/missing row. Existing unrelated errors
retain their documented behavior.

Move the initial/default probes inside their appropriate bounded failure
handler. Separate timeout from genuine nonzero "ref absent" so branch
precedence and actual classifier outcomes remain unchanged when probes finish.
No new state enum, durable receipt or public roster schema is proposed.

### Contained cleanup

Reuse `git(kill_tree=True)` and the existing `push_timeout.run_bounded`
ownership boundary, with an **opt-in absolute deadline** for observation
callers. Extend that helper only after its owner admits this narrow hunk;
legacy push callers retain existing behavior.

The observation form includes termination, pipe draining/closing and root
reaping in `D`. Retain the existing five-second cleanup allowance as a
*reservation within*, not after, the 15-second total: cap command waiting at
`D - 5s`, reuse unused time across sequential commands, and refuse to spawn
when no command-wait budget remains. Cleanup waits are capped by remaining
`D`; no fixed additional five seconds after exhaustion and no negative/zero
timeout passed to start a child.

Acquire containment before the child executes. On deadline/cancellation,
close the owned Job/process-group boundary, drain/close both pipes and reap
the owned root. Preserve headlessness, contained-test suppression, and the
rule that only the owner terminates its group. Never kill an ambient group,
process name or unverifiable PID. If an existing contained mode cannot prove
independent descendant ownership, explicitly refuse that observation launch
or extend its existing owner seam with review; do not silently use an
uncontained fallback or claim descendant cleanup from a mocked root kill.
OS scheduling/uninterruptible I/O cannot be made a hard real-time guarantee;
record actual completion/cleanup evidence and fail qualification on residuals.

## R1b — observe outside mutation admission

Do not simply move `_render_status_segment` or `roster_publisher()` outside
the lock: both currently depend on process-global active-project/config
context, and rendering can persist titles. A lock drop without explicit
inputs creates cross-project writes and stale publication.

1. Under the existing lock, capture the selected project/config values,
   immutable target record/path/branch/remote/default and current owner/
   generation/revision identity using the existing owners' contracts.
2. Release mutation admission. Observe Git/network using explicit captured
   inputs and `D`; no `cfg.set_active_project`, title persistence, handoff,
   tracking mutation or publication occurs in this phase.
3. Reacquire the existing lock, restore the explicit project scope and
   revalidate target identity plus the existing owner/generation/revision
   fence. Discard/retry later if changed, without renewing `D` or overwriting
   newer facts. Apply eligible title/status/publication effects only then.
4. Preserve lifecycle priority and governance checks immediately before
   mutations. Failure publishes truthful degraded evidence through the
   existing publication owner; it must not fabricate successful observation
   freshness. Retain existing failed-publication and owner-recovery behavior.

Use one small internal observation input/result at this seam, not a new
framework, watcher, persistent cache, transport or receipt store. Exact
snapshot/fence representation is an **integration dependency**, not a second
API defined here: the roster/publication and resident-control owners must
admit it before R1b. The full-roster callback requires their gather/commit
split as well as the served-segment path. Without that handoff, ship only
admitted R1a and leave #7944's concurrency acceptance explicitly open.

## R2 — invocation-local passive policy

Choose `git -c core.fsmonitor=false status --porcelain` for classification's
status invocation. This is simpler than toggling per-worktree configuration
at session start/end and cannot race a genuine live owner or overwrite an
operator's setting. No lifetime policy/installer/session hook is added.

Apply the prefix only in `_classify_git_state`'s status read (including
passive live-session display classification). It prevents this invocation
from *using/starting* the daemon; it does not stop an already-running live
daemon. Explicit interactive Git keeps inherited policy and can start/use
the watcher. Metadata-only probes need no fsmonitor policy change.

Do not change the global `git()` prefix, global/local/worktree config, the
fresh-mux/real-session protection, cleanup cooldown or `stop_fsmonitor_daemon`.
The existing cleanup still owns stopping pre-existing dark watchers; R2
removes the passive restart source. Extra `git config`, daemon-status or
process-discovery calls on each classification are prohibited.

## Minimum hunks, reservations and staged graph

| Slice | Proposed hunks/tests | Ownership boundary |
|-------|----------------------|--------------------|
| P0 | This effort folder only | Plan author; no shared index rewrite or plugin changefile needed |
| R1a | `A/git_ops.py`: first helper, classifier handler/closure, remaining-budget `_g`; `A/status_bar_cli.py`: default and both segment prefixes; `A/push_timeout.py`: opt-in deadline/drain; `T/test_git_ops.py`, `T/test_status_segment.py` | Admit helper API/containment owner before edit; retain unbounded general Git/push defaults |
| R1b | `A/__main__.py`: `_monitor_sweep` served and warm-project blocks; `A/status_monitor_cli.py`: only connecting the existing admission/snapshot seam if needed; `T/test_status_monitor.py`, proposed `T/native/test_git_observation_deadline.py` | Shared monitor hunks require coordinator + publication/control owners' admission; `A/worktrees_producer.py` gather/commit change remains publication-owner work |
| R2 | `A/git_ops.py`: status argv hunk only, after R1a; `T/test_git_ops.py`, `T/test_session_catalog.py`, proposed `T/native/test_passive_fsmonitor_cycle.py` | No session/config lifetime implementation or cleanup-policy takeover |

Graph: reviewed P0 -> independent coordinator admission -> R1a -> R1b
(after publication/control handoff) -> R2 -> combined qualification.
R2 may follow R1a while R1b waits, but no simultaneous `git_ops.py` writers.
Implementation PRs include their plugin changefiles and update authoritative
Git/status documentation only after behavior exists.

`current_roster`, entry/feed/publication semantic APIs and receipt storage,
resident control, native lifecycle ingestion, controller transitivity and
direct-target freshness are outside these hunks. #2619 retains those native
event/controller requirements plus the long lost-event backstop. #6086 and
`process-spawn-reduction` retain representative quiet/concurrent workload
counts, cumulative spawn rate versus peak-live counts, CPU and full campaign
acceptance. No numerical spawn-rate target or whole-campaign closure is
invented by this plan.
