"""Actor-free controls for cache-reference admission and handle custody."""
from contextlib import contextmanager
import os
import sys
from typing import Iterator

import pytest

from agent_procutil import python_cache_processes as processes


class Reader:
    principal = "owner"

    def __init__(self) -> None:
        self.records = {10: (100, "owner"), 20: (200, "other")}
        self.open: set[int] = set()
        self.calls = 0
        self.denied = False

    def snapshot(self) -> dict[int, tuple[int, str]]:
        self.calls += 1
        return dict(self.records)

    @contextmanager
    def pin(self, pid: int, birth: int) -> Iterator[int]:
        assert self.records[pid][0] == birth
        if self.denied:
            raise PermissionError("must not publish private diagnostics")
        self.open.add(pid)
        try:
            yield pid
        finally:
            self.open.remove(pid)

    def texts(self, pid: int) -> Iterator[str]:
        yield "-X pycache_prefix=cache\\unsealed-" + "a" * 32
        yield "PYTHONPYCACHEPREFIX=cache\\installed-" + "B" * 64


def test_observation_keeps_birth_pins_and_only_returns_cache_names() -> None:
    reader = Reader()
    with processes.observe(reader=reader) as report:
        assert report.mode == "process-scan"
        assert report.names == {"unsealed-" + "a" * 32, "installed-" + "b" * 64}
        assert report.processes == 1 and report.excluded == 1
        assert reader.open == {10}
        assert report.verify()
    assert not reader.open


def test_recorded_foreign_principal_consumer_is_not_excluded() -> None:
    reader = Reader()
    with processes.observe(reader=reader, required_pids={20}) as report:
        assert report.mode == "process-scan"
        assert report.processes == 2 and report.excluded == 0
        assert reader.open == {10, 20}
    assert not reader.open


def test_opaque_relevant_process_holds_without_leaking_details() -> None:
    reader = Reader()
    reader.denied = True
    with processes.observe(reader=reader) as report:
        assert report.mode == "incomplete"
        assert not report.verify()
        assert report.reason == "process-reference-unavailable"
        assert "private" not in repr(report)
    assert not reader.open


@pytest.mark.parametrize("change", ["birth", "owner"])
def test_changed_census_invalidates_the_decision(change: str) -> None:
    reader = Reader()
    with processes.observe(reader=reader) as report:
        if change == "birth":
            reader.records[10] = (101, "owner")
        else:
            reader.records[10] = (100, "other")
        assert not report.verify()
        assert report.reason == "process-identity-changed"
    assert not reader.open


def test_new_relevant_process_is_inspected_and_pinned_before_revalidation() -> None:
    reader = Reader()
    with processes.observe(reader=reader) as report:
        reader.records[30] = (300, "owner")
        reader.records[40] = (400, "other")
        assert report.verify()
        assert reader.open == {10, 30}
        assert report.processes == 2
        del reader.records[30]
        assert report.verify()
    assert not reader.open


def test_deadline_failure_closes_pins_and_refuses() -> None:
    reader = Reader()
    calls = 0

    def check() -> None:
        nonlocal calls
        calls += 1
        if calls > 2:
            raise TimeoutError("deadline")

    with processes.observe(reader=reader, check=check) as report:
        assert report.mode == "incomplete"
        assert not report.verify()
    assert not reader.open


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf")])
def test_invalid_budget_refuses_before_native_observation(seconds: float) -> None:
    reader = Reader()
    with pytest.raises(ValueError), processes.observe(reader=reader, seconds=seconds):
        pass
    assert not reader.calls


def test_stable_process_with_changed_arguments_is_rechecked(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = Reader()
    with processes.observe(reader=reader) as report:
        monkeypatch.setattr(reader, "texts", lambda pid: iter(["installed-" + "c" * 64]))
        assert report.verify()
        assert "installed-" + "c" * 64 in report.names


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows process parameters")
def test_native_current_process_identity_and_parameter_reads() -> None:
    from agent_procutil.python_cache_windows import Reader as NativeReader

    reader = NativeReader(check=lambda: None)
    birth = reader._births()[os.getpid()]
    with reader.pin(os.getpid(), birth) as handle:
        texts = list(reader.texts(handle))
    assert len(texts) == 2
    assert "python" in texts[0].lower()
    assert "PATH=" in texts[1].upper()
