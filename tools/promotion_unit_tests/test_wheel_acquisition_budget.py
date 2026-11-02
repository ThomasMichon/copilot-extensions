"""Pure shared transfer-budget and worker-policy controls; native overlap is separate."""
from dataclasses import replace
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "libs" / "installer-engine"))
import installer_wheel_acquisition as acquisition


def limits(**changes):
    return replace(acquisition.AcquisitionLimits(
        max_packages=16, max_requests=4, max_redirects=2,
        max_candidates_per_project=8, max_index_bytes=8,
        max_total_metadata_bytes=12, max_wheel_bytes=10,
        max_closure_bytes=16, max_temporary_bytes=20,
    ), **changes)


@pytest.mark.parametrize("workers", [1, 4, 8])
def test_worker_policy_keeps_serial_and_bounded_parallel_selection(workers):
    selected = limits(max_workers=workers)
    selected.verify()
    assert selected.max_workers == workers
    assert limits().max_workers == 8


@pytest.mark.parametrize("workers", [0, -1, True, 9, "8"])
def test_worker_policy_refuses_noninteger_unbounded_or_empty_selection(workers):
    with pytest.raises(acquisition.WheelError, match="positive|eight workers"):
        limits(max_workers=workers).verify()


@pytest.mark.parametrize("kind,maximum", [
    ("requests", 4), ("redirects", 2), ("metadata_bytes", 12), ("wheel_bytes", 13),
])
def test_transfer_budgets_are_shared_inclusive_and_never_partially_reserved(kind, maximum):
    budget = acquisition._Budget(limits(), time.monotonic() + 5, control_bytes=7)
    budget.reserve(kind, maximum - 1)
    budget.reserve(kind, 1)
    assert budget.current(kind) == maximum
    with pytest.raises(acquisition.WheelError, match="budget"):
        budget.reserve(kind, 1)
    assert budget.current(kind) == maximum


def test_peer_failure_cancels_further_reservations_without_replacing_the_cause():
    budget = acquisition._Budget(limits(), time.monotonic() + 5)
    original = acquisition.WheelError("selected wheel digest changed")
    budget.fail(original)
    budget.fail(acquisition.WheelError("peer cancellation"))
    assert budget.failure is original
    with pytest.raises(acquisition.WheelError, match="cancelled"):
        budget.reserve("requests", 1)
    assert budget.current("requests") == 0


def test_peer_failure_while_waiting_for_budget_lock_cannot_admit_another_request():
    budget = acquisition._Budget(limits(), time.monotonic() + 5)

    class PeerFailsBeforeEntry:
        def __enter__(self):
            budget.failed.set()

        def __exit__(self, *_args):
            return False

    budget.lock = PeerFailsBeforeEntry()
    with pytest.raises(acquisition.WheelError, match="cancelled"):
        budget.reserve("requests", 1)
    assert budget.counts["requests"] == 0


@pytest.mark.parametrize("workers,count,parallel", [(1, 3, False), (8, 1, False), (8, 3, True)])
def test_download_orchestration_bounds_workers_and_preserves_input_order(
    tmp_path, monkeypatch, workers, count, parallel,
):
    calls = []

    class Pool:
        def __init__(self, *, max_workers):
            assert parallel and max_workers == count
            calls.append("pool")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            calls.append("settled")

        def map(self, function, items):
            return map(function, items)

    def download(_root, item, _feeds, _origins, _cutoffs, _limits, _deadline, _context, budget):
        budget.reserve("requests", 1)
        return {"selected": item}

    monkeypatch.setattr(acquisition, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(acquisition, "_download_one", download)
    selected = list(reversed(range(count)))
    result = acquisition._download(
        tmp_path / "downloads", selected, (), (), {}, limits(max_workers=workers),
        time.monotonic() + 5, None, 0,
    )
    assert result["files"] == [{"selected": value} for value in selected]
    assert result["request_count"] == count
    assert calls == (["pool", "settled"] if parallel else [])


def test_download_reports_original_failure_only_after_pool_settlement(tmp_path, monkeypatch):
    original = acquisition.WheelError("request budget exceeded")
    settled = []

    class Pool:
        def __init__(self, **_options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            settled.append(True)

        def map(self, function, items):
            return map(function, items)

    def download(*args):
        args[-1].fail(original)
        raise acquisition.WheelError("peer cancellation")

    monkeypatch.setattr(acquisition, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(acquisition, "_download_one", download)
    with pytest.raises(acquisition.WheelError) as raised:
        acquisition._download(
            tmp_path / "downloads", [1, 2], (), (), {}, limits(),
            time.monotonic() + 5, None, 0,
        )
    assert raised.value is original and settled == [True]
