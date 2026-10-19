"""Negative effect admission uses synthetic audit events, never real actors."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from promotion_unit_contract import UnitEvidenceError
from promotion_unit_effects import UnitRun


@pytest.mark.parametrize("event", [
    "subprocess.Popen", "socket.__new__", "os.system", "os.posix_spawn",
    "os.exec", "os.fork", "ctypes.dlopen",
])
def test_prohibited_actor_events_are_recorded_even_if_exception_is_caught(tmp_path, event):
    run = UnitRun(Mock(), tmp_path, {}, tmp_path)
    run.active = True
    with pytest.raises(UnitEvidenceError, match="not an eligible unit effect"):
        run.audit(event, ())
    assert run.effects == [event]


def test_only_declared_local_python_source_is_admitted(tmp_path):
    admitted = "helper.py"
    run = UnitRun(Mock(), tmp_path, {admitted: "a" * 64}, tmp_path)
    run.active = True
    run.audit("open", (str(tmp_path / admitted), "r", 0))
    assert run.opened_sources == {admitted}
    with pytest.raises(UnitEvidenceError, match="unadmitted source"):
        run.audit("open", (str(tmp_path / "other.py"), "r", 0))


def test_actual_git_storage_is_not_a_synthetic_fixture(tmp_path):
    run = UnitRun(Mock(), tmp_path, {}, tmp_path)
    run.active = True
    with pytest.raises(UnitEvidenceError, match="Git storage"):
        run.audit("open", (str(tmp_path / ".git/config"), "r", 0))


def test_writes_are_limited_to_synthetic_temporary_storage(tmp_path):
    run = UnitRun(Mock(), tmp_path, {}, tmp_path / "synthetic")
    run.active = True
    run.audit("open", (str(tmp_path / "synthetic/data.json"), "w", 0))
    with pytest.raises(UnitEvidenceError, match="write outside"):
        run.audit("open", (str(tmp_path / "host-data.json"), "w", 0))


def test_fixture_alias_cannot_escape_synthetic_temporary_storage(tmp_path):
    temporary = tmp_path / "synthetic"
    run = UnitRun(Mock(), tmp_path, {}, temporary)
    run.active = True
    run.audit("os.symlink", ("fixture0", str(temporary / "fixture-current"), -1))
    with pytest.raises(UnitEvidenceError, match="indirection outside"):
        run.audit("os.symlink", ("../../host", str(temporary / "fixture-current"), -1))


def test_framework_activity_outside_execution_does_not_claim_a_unit_effect(tmp_path):
    run = UnitRun(Mock(), tmp_path, {}, tmp_path)
    run.audit("subprocess.Popen", ())
    run.audit("open", (str(Path("unadmitted.py")), "r", 0))
    assert run.effects == []


def test_xpass_cannot_be_serialized_as_unqualified_passing_evidence(tmp_path):
    run = UnitRun(Mock(), tmp_path, {}, tmp_path)
    run.pytest_runtest_logreport(SimpleNamespace(
        nodeid="test.py::test_case", when="call", outcome="passed", wasxfail="known failure",
    ))
    assert run.effects == ["xfail/xpass outcome is not positive unit evidence"]
