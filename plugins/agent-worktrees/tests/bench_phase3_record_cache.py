"""picker-performance-and-responsiveness Phase 3 — remaining measurement.

Re-measures the sustained CPU/time cost removed by ``record_cache``'s
``copy_result=False`` fast path, at the scale the original diagnosis
observed (100+ tracked records), using the real ``tracking``/``record_cache``
code paths rather than a synthetic microbenchmark unrelated to production
code.

Methodology: build a fleet of N real on-disk ``WorktreeRecord`` YAML files
via the same ``tracking_lifecycle.create_new_record`` factory production
code uses, warm the cache once (first parse), then repeatedly call
``tracking.find_worktree_id_by_cwd`` -- the actual documented hot path
(``sessions.verify_worktree_active`` -> ``reclaim.resolve_bound_copilots``
-> here) -- simulating M live-session resolutions per sweep, M*SWEEPS times
total. Measures wall-clock time for:

  - "before" : the pre-Phase-3 behavior, simulated by monkeypatching
    ``find_worktree_id_by_cwd``'s internal ``list_records(..., copy_records=False)``
    call to force ``copy_records=True`` (the old unconditional deep-copy
    path) for this run only.
  - "after"  : today's code, unmodified (``copy_records=False``).

Both runs exercise the identical call sequence against the identical
on-disk fleet, so the delta isolates exactly the cost this phase removed.

Not a pytest test (deliberately not named ``test_*``): it is a multi-second
manual measurement script, run on demand --
``python tests/bench_phase3_record_cache.py`` from the plugin root (with
``agent-worktrees`` and its local libs installed editable, as ``tests/``
already requires for the rest of the suite) -- not part of the regular
suite.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path

from agent_worktrees import record_cache, tracking
from agent_worktrees.tracking_lifecycle import create_new_record

N_RECORDS = 120  # "100+ tracked records" per the effort's own target scale
N_SESSIONS_PER_SWEEP = 40  # live sessions resolved per sweep (M)
N_SWEEPS = 25
WARMUP_SWEEPS = 2


def build_fleet(tracking_path: Path) -> list[str]:
    tracking_path.mkdir(parents=True, exist_ok=True)
    cwds = []
    for i in range(N_RECORDS):
        wt_id = f"wt-{i:04d}"
        wt_path = str(tracking_path.parent / "worktrees-on-disk" / wt_id)
        create_new_record(
            wt_id, f"worktree/{wt_id}", wt_path, "bench-repo",
            "bench-machine", "windows", tracking_path,
        )
        cwds.append(wt_path)
    return cwds


def run_sweeps(cwds: list[str], *, sweeps: int, sessions_per_sweep: int) -> float:
    record_cache.clear()
    # Warm the cache the same way a long-lived daemon would (first sweep
    # always pays the cold-parse cost regardless of copy_result).
    for _ in range(WARMUP_SWEEPS):
        for cwd in cwds[:sessions_per_sweep]:
            tracking.find_worktree_id_by_cwd(cwd, project="bench-repo")

    start = time.perf_counter()
    for _ in range(sweeps):
        for j in range(sessions_per_sweep):
            cwd = cwds[j % len(cwds)]
            tracking.find_worktree_id_by_cwd(cwd, project="bench-repo")
    elapsed = time.perf_counter() - start
    return elapsed


def main() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        tracking_path = tmp_path / "config" / "bench-repo" / "worktrees"
        cwds = build_fleet(tracking_path)

        orig_project_dir = tracking.cfg.project_dir

        def _fake_project_dir(project=None):
            return tmp_path / "config" / (project or "bench-repo")

        tracking.cfg.project_dir = _fake_project_dir

        try:
            # "after" -- today's code, unmodified (copy_records=False fast path).
            after_times = []
            for _ in range(3):
                after_times.append(
                    run_sweeps(cwds, sweeps=N_SWEEPS, sessions_per_sweep=N_SESSIONS_PER_SWEEP)
                )

            # "before" -- force the old unconditional-deepcopy behavior by
            # wrapping list_records so copy_records is always True, exactly
            # simulating pre-Phase-3 find_worktree_id_by_cwd.
            orig_list_records = tracking.list_records

            def _list_records_always_copy(tracking_path_arg, **kwargs):
                kwargs["copy_records"] = True
                return orig_list_records(tracking_path_arg, **kwargs)

            tracking.list_records = _list_records_always_copy
            before_times = []
            try:
                for _ in range(3):
                    before_times.append(
                        run_sweeps(cwds, sweeps=N_SWEEPS, sessions_per_sweep=N_SESSIONS_PER_SWEEP)
                    )
            finally:
                tracking.list_records = orig_list_records
        finally:
            tracking.cfg.project_dir = orig_project_dir

    total_calls = N_SWEEPS * N_SESSIONS_PER_SWEEP
    print(f"Fleet: {N_RECORDS} records, {N_SESSIONS_PER_SWEEP} sessions/sweep, "
          f"{N_SWEEPS} sweeps ({total_calls} find_worktree_id_by_cwd calls per run)")
    print()
    print(f"BEFORE (copy_records=True, pre-Phase-3):  "
          f"median={statistics.median(before_times) * 1000:.1f}ms  "
          f"runs={[f'{t * 1000:.1f}ms' for t in before_times]}")
    print(f"AFTER  (copy_records=False, Phase 3 fix):  "
          f"median={statistics.median(after_times) * 1000:.1f}ms  "
          f"runs={[f'{t * 1000:.1f}ms' for t in after_times]}")
    before_med = statistics.median(before_times)
    after_med = statistics.median(after_times)
    reduction = (before_med - after_med) / before_med * 100
    print()
    print(f"Reduction: {reduction:.1f}% "
          f"({before_med * 1000:.1f}ms -> {after_med * 1000:.1f}ms for {total_calls} calls)")
    per_call_before_us = before_med / total_calls * 1_000_000
    per_call_after_us = after_med / total_calls * 1_000_000
    print(f"Per-call: {per_call_before_us:.1f}us -> {per_call_after_us:.1f}us")


if __name__ == "__main__":
    main()
