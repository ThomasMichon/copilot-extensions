"""Job-only numeric diagnostics never widen custody or qualify an observed run."""

from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    import plugin_test_job_observation as observation
else:
    from tools import plugin_test_job_observation as observation


class Kernel:
    def __init__(self) -> None:
        self.pids = [17]
        self.opened: list[int] = []
        self.closed: list[int] = []
        self.queries: list[int] = []
        self.is_member = True
        self.exited = False
        self.birth = 123456789012345
        self.close_ok = True
        self.now = 10.0

    def members(self, job: int, capacity: int) -> list[int]:
        assert job == 7
        if len(self.pids) > capacity:
            raise OSError("fixture incomplete census")
        return list(self.pids)

    def open(self, pid: int) -> int:
        assert pid in self.pids
        self.opened.append(pid)
        return pid + 100

    def inside(self, handle: int, job: int) -> bool:
        assert handle - 100 in self.pids and job == 7
        return self.is_member

    def times(self, handle: int) -> tuple[int, int, int, int]:
        self.queries.append(handle)
        return self.birth, 333 if self.exited else 0, 100, 200

    def exit_code(self, handle: int) -> int | None:
        assert handle == 117
        return 259 if self.exited else None

    def close(self, handle: int) -> bool:
        self.closed.append(handle)
        return self.close_ok


@pytest.fixture
def kernel(monkeypatch: pytest.MonkeyPatch) -> Kernel:
    result = Kernel()
    monkeypatch.setattr(observation, "_api", lambda: result)
    monkeypatch.setattr(
        observation,
        "_wait_api",
        lambda: SimpleNamespace(
            pending=False,
            poll=lambda target, deadline: None,
            finish=lambda: None,
        ),
    )
    monkeypatch.setattr(
        observation,
        "time",
        SimpleNamespace(
            monotonic=lambda: result.now,
            time_ns=lambda: 100,
        ),
    )
    return result


def report(
    observer: observation.JobObservation,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output = StringIO()
    observer.finish(output)
    lines = output.getvalue().splitlines()
    return (
        [
            json.loads(line.split(" ", 1)[1])
            for line in lines[:-1]
            if not line.startswith(
                ("[CI-JOB-PROCESS-SUMMARY] ", "[CI-JOB-WAIT-SUMMARY] ")
            )
        ],
        json.loads(lines[-1].split(" ", 1)[1]),
    )


def test_pins_original_member_through_exit_without_pid_reopen(kernel: Kernel) -> None:
    observer = observation.JobObservation(7, 2)
    observer.sample(11)
    observer.sample(11)
    kernel.pids = []
    kernel.exited = True
    rows, summary = report(observer)
    assert kernel.opened == [17] and kernel.closed == [117]
    assert len(rows) == 3 and all(row["birth_100ns"] == kernel.birth for row in rows)
    assert rows[-1]["exit_code"] == 259 and rows[-1]["exited"] is True
    assert rows[-1]["exit_100ns"] == 333
    assert rows[0]["exit_code"] is None and rows[0]["exited"] is False
    assert rows[0]["kernel_100ns"] == 100 and rows[0]["user_100ns"] == 200
    assert (
        summary["diagnostic_only"]
        and not summary["errors"]
        and not summary["unclosed_handles"]
    )


@pytest.mark.parametrize("exit_during", ["counters", "liveness"])
def test_final_sample_preserves_exit_snapshot_without_extending_live_cpu(
    kernel: Kernel, monkeypatch: pytest.MonkeyPatch, exit_during: str,
) -> None:
    observer = observation.JobObservation(7, 2)
    observer.sample(20)
    kernel.now = 10.5
    observer.sample(20)
    original_times, original_exit_code = kernel.times, kernel.exit_code

    def times(handle: int) -> tuple[int, int, int, int]:
        if exit_during == "counters":
            kernel.exited = True
        birth, exited, kernel_cpu, user_cpu = original_times(handle)
        if exit_during == "liveness" and kernel.exited:
            kernel_cpu, user_cpu = 300, 400
        return birth, exited, kernel_cpu, user_cpu

    def exit_code(handle: int) -> int | None:
        if exit_during == "liveness":
            kernel.exited = True
        return original_exit_code(handle)

    monkeypatch.setattr(kernel, "times", times)
    monkeypatch.setattr(kernel, "exit_code", exit_code)
    kernel.pids = []
    kernel.now = 11
    observer.sample(20)
    row = observer.summaries[(17, kernel.birth)]
    assert row["exited"] is True and row["exit_code"] == 259
    assert row["exit_100ns"] == 333 and row["unchanged_cpu_since_ms"] is None
    expected_cpu = (100, 200) if exit_during == "counters" else (300, 400)
    assert (row["kernel_100ns"], row["user_100ns"]) == expected_cpu
    assert row["longest_unchanged_cpu_start_ms"] == 0
    assert row["longest_unchanged_cpu_end_ms"] == 500
    assert not observer.pins and kernel.closed == [117]
    _, summary = report(observer)
    assert not summary["errors"] and not summary["unclosed_handles"]


def test_final_counter_refresh_refuses_a_different_birth(
    kernel: Kernel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer = observation.JobObservation(7, 2)
    observer.sample(20)
    original_times = kernel.times
    reads = 0

    def times(handle: int) -> tuple[int, int, int, int]:
        nonlocal reads
        reads += 1
        birth, exited, kernel_cpu, user_cpu = original_times(handle)
        return birth + int(reads > 1), exited, kernel_cpu, user_cpu

    def exit_code(handle: int) -> int:
        assert handle == 117
        kernel.exited = True
        return 259

    monkeypatch.setattr(kernel, "times", times)
    monkeypatch.setattr(kernel, "exit_code", exit_code)
    kernel.pids = []
    kernel.now = 11
    observer.sample(20)
    assert len(observer.rows) == 1 and 17 in observer.pins
    assert observer.errors == {"sample-unavailable"}
    monkeypatch.setattr(kernel, "times", original_times)
    rows, summary = report(observer)
    assert all(row["birth_100ns"] == kernel.birth for row in rows)
    assert rows[-1]["exited"] is True and rows[-1]["exit_100ns"] == 333
    assert summary["unclosed_handles"] == 0 and kernel.closed == [117]


def test_membership_race_never_reads_foreign_process_counters(kernel: Kernel) -> None:
    kernel.is_member = False
    observer = observation.JobObservation(7, 2)
    observer.sample(11)
    rows, summary = report(observer)
    assert not rows and not kernel.queries
    assert kernel.closed == [117]
    assert summary["errors"] == ["member-no-longer-in-job"]


def test_incomplete_census_and_expired_cycle_do_not_open_processes(
    kernel: Kernel,
) -> None:
    observer = observation.JobObservation(7, 1)
    kernel.pids = [17, 18, 19]
    observer.sample(11)
    kernel.pids = [17]
    observer.sample(10)
    rows, summary = report(observer)
    assert not rows and not kernel.opened
    assert summary["errors"] == ["poll-budget-exhausted", "sample-unavailable"]


def test_birth_mismatch_is_unavailable_not_a_second_identity(kernel: Kernel) -> None:
    observer = observation.JobObservation(7, 2)
    observer.sample(11)
    kernel.birth += 1
    observer.sample(11)
    rows, summary = report(observer)
    assert len(rows) == 1
    assert summary["errors"] == ["final-sample-unavailable", "sample-unavailable"]
    assert kernel.closed == [117]


def test_row_cap_is_explicit_and_still_closes_pins(
    kernel: Kernel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observation, "MAX_ROWS", 2)
    observer = observation.JobObservation(7, 2)
    for _ in range(5):
        observer.sample(11)
    rows, summary = report(observer)
    assert len(rows) == 1 and summary["truncated"]
    assert summary["discarded_rows"] == 5 and summary["summary_rows"] == 1
    assert summary["rows"] == 2
    assert kernel.closed == [117] and not observer.pins


def test_process_summary_retains_early_exit_after_churn_and_pid_reuse(
    kernel: Kernel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observation, "MAX_ROWS", 6)

    def counters(handle: int) -> tuple[int, int, int, int]:
        assert handle == 117
        return (
            kernel.birth,
            333 if kernel.exited else 0,
            round(kernel.now * 100),
            round(kernel.now * 200),
        )

    monkeypatch.setattr(kernel, "times", counters)
    observer = observation.JobObservation(7, 2)
    first_birth = kernel.birth
    observer.sample(20)
    kernel.now = 10.5
    kernel.pids = []
    kernel.exited = True
    observer.sample(20)
    kernel.birth += 1
    kernel.now = 11
    kernel.pids = [17]
    kernel.exited = False
    for _ in range(10):
        observer.sample(20)
        kernel.now += 0.1
    kernel.pids = []
    kernel.exited = True
    output = StringIO()
    observer.finish(output)
    lines = output.getvalue().splitlines()
    summaries = [
        json.loads(line.split(" ", 1)[1])
        for line in lines
        if line.startswith("[CI-JOB-PROCESS-SUMMARY] ")
    ]
    assert {row["birth_100ns"] for row in summaries} == {first_birth, first_birth + 1}
    early = next(row for row in summaries if row["birth_100ns"] == first_birth)
    assert early["first_at_ms"] == 0 and early["at_ms"] == 500
    assert early["first_kernel_100ns"] == 1000 and early["kernel_100ns"] == 1050
    assert early["first_user_100ns"] == 2000 and early["user_100ns"] == 2100
    assert early["exited"] is True and early["exit_code"] == 259
    assert early["sample_count"] == 2 and early["exit_100ns"] == 333
    assert all(row["birth_100ns"] != first_birth for _, row in observer.rows)
    assert len(lines) - 1 <= 6 and kernel.opened == [17, 17]
    assert kernel.closed == [117, 117] and not observer.pins


def test_process_summary_capacity_loss_is_explicit_without_evicting_earlier_births(
    kernel: Kernel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observation, "MAX_ROWS", 4)
    observer = observation.JobObservation(7, 2)
    first_birth = kernel.birth
    for index in range(4):
        kernel.birth = first_birth + index
        kernel.pids = [17]
        kernel.exited = False
        observer.sample(20)
        kernel.now += 0.1
        kernel.pids = []
        kernel.exited = True
        observer.sample(20)
    output = StringIO()
    observer.finish(output)
    lines = output.getvalue().splitlines()
    summaries = [
        json.loads(line.split(" ", 1)[1])
        for line in lines
        if line.startswith("[CI-JOB-PROCESS-SUMMARY] ")
    ]
    summary = json.loads(lines[-1].split(" ", 1)[1])
    assert {row["birth_100ns"] for row in summaries} == {first_birth, first_birth + 1}
    assert (
        summary["summary_truncated"] is True and summary["summary_dropped_samples"] > 0
    )
    assert summary["rows"] == len(lines) - 1 <= 4
    assert summary["summary_rows"] == 2 and not summary["unclosed_handles"]
    assert kernel.closed == [117] * 4 and not observer.pins


def _process_row(
    birth: int,
    at_ms: int,
    kernel_time: int,
    *,
    exited: bool = False,
) -> observation.Row:
    return {
        "pid": 17,
        "birth_100ns": birth,
        "at_ms": at_ms,
        "kernel_100ns": kernel_time,
        "user_100ns": 200,
        "exited": exited,
        "exit_100ns": 333 if exited else None,
        "exit_code": 0 if exited else None,
    }


def test_summary_retains_sampled_unchanged_cpu_span_without_extending_through_exit(
    kernel: Kernel,
) -> None:
    observer = observation.JobObservation(7, 2)
    for at_ms, cpu, exited in (
        (0, 100, False),
        (400, 100, False),
        (900, 100, False),
        (1000, 110, False),
        (1100, 110, False),
        (100_000, 110, True),
    ):
        observer._retain_summary(
            17,
            kernel.birth,
            _process_row(kernel.birth, at_ms, cpu, exited=exited),
        )
    row = observer.summaries[(17, kernel.birth)]
    assert row["longest_unchanged_cpu_start_ms"] == 0
    assert row["longest_unchanged_cpu_end_ms"] == 900
    assert row["longest_unchanged_kernel_100ns"] == 100
    assert row["longest_unchanged_user_100ns"] == 200
    assert row["unchanged_cpu_since_ms"] is None
    report(observer)
    assert not kernel.queries and not kernel.opened


@pytest.mark.parametrize("already_exited", [False, True])
def test_summary_does_not_infer_unchanged_cpu_before_two_live_equal_samples(
    kernel: Kernel,
    already_exited: bool,
) -> None:
    observer = observation.JobObservation(7, 2)
    for at_ms, cpu in ((0, 100), (500, 101)):
        observer._retain_summary(
            17,
            kernel.birth,
            _process_row(kernel.birth, at_ms, cpu, exited=already_exited),
        )
    row = observer.summaries[(17, kernel.birth)]
    assert row["longest_unchanged_cpu_start_ms"] is None
    assert row["longest_unchanged_cpu_end_ms"] is None
    assert row["longest_unchanged_kernel_100ns"] is None
    assert row["longest_unchanged_user_100ns"] is None
    report(observer)
    assert not kernel.queries and not kernel.opened


def test_summary_unchanged_cpu_span_survives_timeline_churn_and_stays_birth_scoped(
    kernel: Kernel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observation, "MAX_ROWS", 6)
    observer = observation.JobObservation(7, 2)
    for at_ms, exited in ((0, False), (400, False), (500, True)):
        row = _process_row(kernel.birth, at_ms, 100, exited=exited)
        observer._append("PROCESS", row)
        observer._retain_summary(17, kernel.birth, row)
    for index in range(10):
        row = _process_row(kernel.birth + 1, 1000 + index * 100, 500)
        observer._append("PROCESS", row)
        observer._retain_summary(17, kernel.birth + 1, row)
    first = observer.summaries[(17, kernel.birth)]
    second = observer.summaries[(17, kernel.birth + 1)]
    assert first["longest_unchanged_cpu_start_ms"] == 0
    assert first["longest_unchanged_cpu_end_ms"] == 400
    assert second["longest_unchanged_cpu_start_ms"] == 1000
    assert second["longest_unchanged_cpu_end_ms"] == 1900
    assert all(row["birth_100ns"] != kernel.birth for _, row in observer.rows)
    _, summary = report(observer)
    assert summary["rows"] <= 6 and summary["summary_rows"] == 2
    assert not kernel.queries and not kernel.opened


def test_failed_foreign_handle_close_does_not_promote_it_to_observable(
    kernel: Kernel,
) -> None:
    kernel.is_member = False
    kernel.close_ok = False
    observer = observation.JobObservation(7, 2)
    observer.sample(11)
    observer.sample(11)
    output = StringIO()
    with pytest.raises(OSError, match="closure remains unconfirmed"):
        observer.finish(output)
    assert not kernel.queries
    assert "handle-close-unconfirmed" in output.getvalue()
    assert '"unclosed_handles": 1' in output.getvalue()


@pytest.mark.parametrize("failure", ["none", "job", "observation", "both"])
def test_original_owner_tears_down_before_reporting_and_retains_unreaped_custody(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    if TYPE_CHECKING:
        import plugin_test_containment as containment
    else:
        from tools import plugin_test_containment as containment

    events: list[str] = []
    now = [0.0]
    returns = iter((None, 0))
    job_owner: Any = object()

    class Job:
        _handle = 7

        def __init__(self, _limits: Any) -> None:
            pass

        def assign(self, pid: int) -> None:
            assert pid == 17
            events.append("assign")

        def usage(self) -> tuple[int, int]:
            return 0, 0

        def close(self) -> None:
            events.append("job-close")
            if failure in ("job", "both"):
                raise containment.UnreapedProcessError("fixture failure", job=job_owner)

    class Observer:
        def __init__(self, job: int, limit: int) -> None:
            assert job == 7 and limit == 128

        def sample(self, deadline: float) -> None:
            assert 0 < deadline <= 0.02
            events.append("sample")

        def finish(self, _output: Any) -> None:
            events.append("finish")
            if failure in ("observation", "both"):
                raise OSError("fixture close failure")

    def sleep(seconds: float) -> None:
        assert seconds == 0.25
        now[0] += seconds

    monkeypatch.setattr(containment, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        containment, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=sleep)
    )
    monkeypatch.setattr(containment, "_WindowsJob", Job)
    monkeypatch.setattr(observation, "JobObservation", Observer)
    monkeypatch.setattr(containment, "_tree_size", lambda _path: 0)
    monkeypatch.setattr(containment, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(
        containment, "_reap_controller", lambda _process: events.append("reap")
    )
    monkeypatch.setattr(
        containment,
        "subprocess",
        SimpleNamespace(
            Popen=lambda *a, **k: SimpleNamespace(
                pid=17,
                poll=lambda: next(returns),
            )
        ),
    )

    def execute() -> int:
        return containment._run_contained_process(
            ["unused"],
            cwd=tmp_path,
            env={},
            sandbox=tmp_path,
            limits=containment.Limits(wall_seconds=300),
            job_observation=True,
        )

    if failure == "none":
        assert execute() == 0
    else:
        with pytest.raises(containment.UnreapedProcessError) as error:
            execute()
        if failure in ("job", "both"):
            assert error.value.job is job_owner
    assert events == ["assign", "sample", "job-close", "reap", "finish"]


def test_wait_samples_share_total_ring_and_correlate_original_member_after_counters(
    kernel: Kernel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observation, "MAX_ROWS", 4)
    monkeypatch.setattr(observation, "WAIT_MIN_AGE_SECONDS", 0)

    def poll(
        target: tuple[int, int, int, int] | None, deadline: float
    ) -> observation.Row:
        assert target == (17, 117, kernel.birth, 0) and deadline == 11
        assert kernel.queries == [117, 117]
        return {"pid": 17, "birth_100ns": kernel.birth, "wait_status": 5}

    monkeypatch.setattr(
        observation,
        "_wait_api",
        lambda: SimpleNamespace(
            pending=False,
            poll=poll,
            finish=lambda: None,
        ),
    )
    observer = observation.JobObservation(7, 2)
    observer.sample(11)
    rows, summary = report(observer)
    assert len(rows) == 2 and rows[0]["wait_status"] == 5
    assert rows[0]["at_ms"] == rows[0]["sample_at_ms"] == 0
    assert summary["rows"] == 3 and summary["process_rows"] == summary["wait_rows"] == 1
    assert summary["summary_rows"] == 1
    assert summary["discarded_rows"] == 1 and summary["truncated"]


def test_wait_summary_retains_first_latest_after_churn_and_pid_reuse(
    kernel: Kernel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observation, "MAX_ROWS", 16)
    observer = observation.JobObservation(7, 2)
    for birth in (kernel.birth, kernel.birth + 1):
        for index in range(3):
            observer.wait_started_ms = index * 100
            kernel.now += 0.1
            observer._append_wait(
                {"pid": 17, "birth_100ns": birth, "stage": index, "wait_status": 5}
            )
    for index in range(30):
        observer._append("PROCESS", _process_row(kernel.birth + 2, index, 100))
    assert all(kind == "PROCESS" for kind, _ in observer.rows)
    output = StringIO()
    observer.finish(output)
    lines = output.getvalue().splitlines()
    retained = [
        json.loads(line.split(" ", 1)[1])
        for line in lines
        if line.startswith("[CI-JOB-WAIT-SUMMARY] ")
    ]
    assert len(retained) == 4
    for birth in (kernel.birth, kernel.birth + 1):
        pair = [row for row in retained if row["birth_100ns"] == birth]
        assert [row["stage"] for row in pair] == [0, 2]
        assert [row["sample_at_ms"] for row in pair] == [0, 200]
        assert [row["first_sample"] for row in pair] == [True, False]
        assert [row["latest_sample"] for row in pair] == [False, True]
        assert all(row["sample_count"] == 3 for row in pair)
    frame = json.loads(lines[-1].split(" ", 1)[1])
    assert frame["wait_summary_rows"] == 4
    assert frame["rows"] == len(lines) - 1 <= 16
    assert not frame["wait_summary_truncated"] and not kernel.opened


def test_wait_summary_capacity_loss_is_explicit_and_preserves_earlier_birth(
    kernel: Kernel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observation, "MAX_ROWS", 8)
    observer = observation.JobObservation(7, 2)
    for birth in (kernel.birth, kernel.birth, kernel.birth + 1, kernel.birth + 2):
        observer._append_wait(
            {"pid": 17, "birth_100ns": birth, "stage": 1, "cancelled_pending": True}
        )
    output = StringIO()
    observer.finish(output)
    lines = output.getvalue().splitlines()
    retained = [
        json.loads(line.split(" ", 1)[1])
        for line in lines
        if line.startswith("[CI-JOB-WAIT-SUMMARY] ")
    ]
    assert len(retained) == 2
    assert all(row["birth_100ns"] == kernel.birth for row in retained)
    assert all(row["cancelled_pending"] is True for row in retained)
    frame = json.loads(lines[-1].split(" ", 1)[1])
    assert frame["wait_summary_capacity"] == 2
    assert frame["wait_summary_dropped_samples"] == 2
    assert frame["wait_summary_truncated"] and frame["truncated"]
    assert frame["rows"] == len(lines) - 1 <= 8
    assert not frame["unclosed_wait_requests"] and not kernel.opened


def test_wait_closure_failure_reports_and_retains_observer_without_losing_process_cleanup(
    kernel: Kernel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def finish() -> None:
        raise OSError("fixture unsettled callback")

    monkeypatch.setattr(
        observation,
        "_wait_api",
        lambda: SimpleNamespace(
            pending=True,
            poll=lambda target, deadline: None,
            finish=finish,
        ),
    )
    observer = observation.JobObservation(7, 2)
    observer.sample(11)
    output = StringIO()
    with pytest.raises(OSError, match="closure remains unconfirmed"):
        observer.finish(output)
    assert kernel.closed == [117] and not observer.pins
    assert '"unclosed_wait_requests": 1' in output.getvalue()
    assert '"wait_pending_polls": 1' in output.getvalue()
    assert "wait-close-unconfirmed" in output.getvalue()


def test_wait_api_unavailability_does_not_remove_process_observation(
    kernel: Kernel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> None:
        raise OSError("fixture native unavailable")

    monkeypatch.setattr(observation, "_wait_api", unavailable)
    observer = observation.JobObservation(7, 2)
    observer.sample(11)
    rows, summary = report(observer)
    assert len(rows) == 2 and summary["errors"] == ["wait-api-unavailable"]
    assert summary["wait_rows"] == summary["unclosed_wait_requests"] == 0


def test_wait_targets_rotate_members_and_each_members_thread_cursor(
    kernel: Kernel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel.pids = [17, 18]
    monkeypatch.setattr(observation, "WAIT_MIN_AGE_SECONDS", 0)
    monkeypatch.setattr(kernel, "exit_code", lambda handle: None)
    selected: list[tuple[int, int, int, int] | None] = []
    monkeypatch.setattr(
        observation,
        "_wait_api",
        lambda: SimpleNamespace(
            pending=False,
            poll=lambda target, deadline: selected.append(target),
            finish=lambda: None,
        ),
    )
    observer = observation.JobObservation(7, 2)
    for _ in range(4):
        observer.sample(11)
    assert selected == [
        (17, 117, kernel.birth, 0),
        (18, 118, kernel.birth, 0),
        (17, 117, kernel.birth, 1),
        (18, 118, kernel.birth, 1),
    ]
    report(observer)
    assert sorted(kernel.closed) == [117, 118]


def test_newly_eligible_members_share_short_tail_with_existing_rotation(
    kernel: Kernel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_members = [17, 18, 19, 20, 21]
    kernel.pids = list(old_members)
    monkeypatch.setattr(kernel, "exit_code", lambda handle: None)
    selected: list[tuple[int, int, int, int]] = []
    completed = 0
    probe = SimpleNamespace(pending=False)

    def poll(target: tuple[int, int, int, int] | None, deadline: float) -> None:
        nonlocal completed
        assert deadline == kernel.now + 0.02
        if probe.pending:
            assert target is None
            completed += 1
            probe.pending = False
        else:
            assert target is not None
            selected.append(target)
            probe.pending = True

    probe.poll = poll
    probe.finish = lambda: None
    monkeypatch.setattr(observation, "_wait_api", lambda: probe)
    observer = observation.JobObservation(7, 7)
    observer.sample(kernel.now + 0.02)
    kernel.now = 12
    kernel.pids.extend([22, 23])
    observer.sample(kernel.now + 0.02)
    assert not selected
    for pid in old_members:
        observer.pins[pid].wait_cursor = 4
    observer.next_wait = 20
    for index in range(8):
        kernel.now = 20 + index * 0.25
        observer.sample(kernel.now + 0.02)
    assert [target[0] for target in selected[::2]] == [22, 23]
    assert all(target[0] in old_members for target in selected[1::2])
    assert all(target[3] == 0 for target in selected[::2])
    assert all(target[3] >= 4 for target in selected[1::2])
    assert len(selected) == completed == 4 and not probe.pending
    _, summary = report(observer)
    assert summary["wait_pending_polls"] == 4
    assert not summary["errors"] and summary["unclosed_wait_requests"] == 0
    assert kernel.opened == kernel.pids
    assert sorted(kernel.closed) == [pid + 100 for pid in kernel.pids]


def test_no_wait_call_after_process_counters_exhaust_original_deadline(
    kernel: Kernel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[object] = []
    monkeypatch.setattr(observation, "WAIT_MIN_AGE_SECONDS", 0)
    monkeypatch.setattr(
        observation,
        "_wait_api",
        lambda: SimpleNamespace(
            pending=False,
            poll=lambda target, deadline: selected.append(target),
            finish=lambda: None,
        ),
    )
    observer = observation.JobObservation(7, 2)
    original_times = kernel.times

    def times(handle: int) -> tuple[int, int, int, int]:
        value = original_times(handle)
        if len(kernel.queries) == 2:
            kernel.now = 11
        return value

    monkeypatch.setattr(kernel, "times", times)
    observer.sample(11)
    assert not selected
    report(observer)


def test_young_members_keep_process_sampling_without_native_wait_calls(
    kernel: Kernel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[object] = []
    monkeypatch.setattr(
        observation,
        "_wait_api",
        lambda: SimpleNamespace(
            pending=False,
            poll=lambda target, deadline: selected.append(target),
            finish=lambda: None,
        ),
    )
    observer = observation.JobObservation(7, 2)
    observer.sample(11)
    kernel.now = 17.999
    observer.sample(19)
    rows, summary = report(observer)
    assert not selected
    assert len(rows) == 3 and all(row["kernel_100ns"] == 100 for row in rows)
    assert summary["wait_rows"] == 0 and summary["wait_min_age_ms"] == 8000
    assert kernel.opened == [17] and kernel.closed == [117]


def test_wait_eligibility_starts_at_each_members_retained_age(
    kernel: Kernel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[tuple[int, int, int, int] | None] = []
    monkeypatch.setattr(kernel, "exit_code", lambda handle: None)

    def poll(target: tuple[int, int, int, int] | None, deadline: float) -> None:
        assert deadline == kernel.now + 1
        assert observer.rows[-1][0] == "PROCESS"
        selected.append(target)

    monkeypatch.setattr(
        observation,
        "_wait_api",
        lambda: SimpleNamespace(
            pending=False,
            poll=poll,
            finish=lambda: None,
        ),
    )
    observer = observation.JobObservation(7, 2)
    observer.sample(11)
    kernel.now = 18
    kernel.pids = [17, 18]
    observer.sample(19)
    assert selected == [(17, 117, kernel.birth, 0)]
    kernel.now = 26
    observer.sample(27)
    observer.sample(27)
    assert selected == [
        (17, 117, kernel.birth, 0),
        (18, 118, kernel.birth, 0),
        (17, 117, kernel.birth, 1),
    ]
    report(observer)
    assert sorted(kernel.closed) == [117, 118]


@pytest.mark.parametrize("unavailable", [False, True])
def test_slow_retained_counters_preserve_progress_to_discovery_and_waits(
    kernel: Kernel,
    monkeypatch: pytest.MonkeyPatch,
    unavailable: bool,
) -> None:
    kernel.pids = [17, 18]
    monkeypatch.setattr(kernel, "exit_code", lambda handle: None)
    selected: list[tuple[int, int, int, int] | None] = []
    monkeypatch.setattr(
        observation,
        "_wait_api",
        lambda: SimpleNamespace(
            pending=False,
            poll=lambda target, deadline: selected.append(target),
            finish=lambda: None,
        ),
    )
    observer = observation.JobObservation(7, 3)
    observer.sample(11)
    assert not selected
    kernel.now = 18
    kernel.pids.append(19)
    kernel.queries.clear()
    original_times = kernel.times

    def times(handle: int) -> tuple[int, int, int, int]:
        value = original_times(handle)
        kernel.now += 0.025
        if unavailable and handle == 117:
            raise OSError("fixture slow unavailable member")
        return value

    monkeypatch.setattr(kernel, "times", times)
    for _ in range(4):
        observer.sample(kernel.now + 0.020)
    assert kernel.queries[:2] == [117, 118]
    assert kernel.opened == [17, 18, 19]
    assert selected and selected[0] is not None
    assert selected[0][0] == (18 if unavailable else 17)
    report(observer)
    assert sorted(kernel.closed) == [117, 118, 119]
