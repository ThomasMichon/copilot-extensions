"""Negative effect admission uses synthetic audit events, never real actors."""

from pathlib import Path
import os
import sys
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


@pytest.mark.parametrize("flags", [os.O_RDONLY | os.O_TRUNC, os.O_RDONLY | os.O_APPEND])
def test_readonly_mutating_open_flags_are_refused_before_any_file_open(tmp_path, flags):
    run = UnitRun(Mock(), tmp_path, {}, tmp_path / "synthetic")
    run.active = True
    with pytest.raises(UnitEvidenceError, match="write outside"):
        run.audit("open", (str(tmp_path / "host-data.json"), "r", flags))


def test_fixture_alias_cannot_escape_synthetic_temporary_storage(tmp_path):
    temporary = tmp_path / "synthetic"
    run = UnitRun(Mock(), tmp_path, {}, temporary)
    run.active = True
    run.audit("os.symlink", ("fixture0", str(temporary / "fixture-current"), -1))
    with pytest.raises(UnitEvidenceError, match="indirection outside"):
        run.audit("os.symlink", ("../../host", str(temporary / "fixture-current"), -1))


def test_hardlink_relative_source_is_resolved_against_its_own_directory(tmp_path):
    run = UnitRun(Mock(), tmp_path, {}, tmp_path)
    run.active = True
    with pytest.raises(UnitEvidenceError, match="indirection outside"):
        run.audit("os.link", ("host-file.txt", str(tmp_path / "alias"), -1, -1))


def test_directory_relative_cleanup_is_bound_to_the_actual_private_descriptor(tmp_path, monkeypatch):
    run = UnitRun(Mock(), tmp_path, {}, tmp_path)
    run.active = True
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "readlink", lambda path: str(tmp_path))
    run.audit("os.remove", ("fixture.json", 41))
    monkeypatch.setattr(os, "readlink", lambda path: str(tmp_path.parent))
    with pytest.raises(UnitEvidenceError, match="mutation outside"):
        run.audit("os.remove", ("host.json", 41))


@pytest.mark.parametrize("flags", [os.O_RDONLY, os.O_WRONLY | os.O_TRUNC])
def test_unattributed_relative_low_level_opens_are_refused(tmp_path, flags):
    run = UnitRun(Mock(), tmp_path, {}, tmp_path)
    run.active = True
    with pytest.raises(UnitEvidenceError, match="unattributable descriptor-relative open"):
        run.audit("open", ("data.txt", None, flags))


def test_descriptor_relative_open_validates_the_actual_target_before_execution(tmp_path, monkeypatch):
    temporary = tmp_path / "synthetic"
    original = Mock()
    monkeypatch.setattr(os, "open", original)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "readlink", lambda path: str(tmp_path))
    run = UnitRun(Mock(), tmp_path, {}, temporary)
    run.active = True
    with pytest.raises(UnitEvidenceError, match="write outside"):
        run.guarded_open("data.txt", os.O_WRONLY | os.O_TRUNC, dir_fd=41)
    original.assert_not_called()
    assert run.effects == ["write outside synthetic unit temporary storage"]
    assert run._open_paths == []


def test_guarded_open_keeps_descriptor_and_flag_semantics_and_clears_its_context(tmp_path, monkeypatch):
    flags = os.O_RDONLY | os.O_NOFOLLOW
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "readlink", lambda path: str(tmp_path))
    run = UnitRun(Mock(), tmp_path, {}, tmp_path)
    run.active = True

    def original(name, actual_flags, mode, *, dir_fd):
        assert name == "data.txt" and actual_flags == flags and mode == 0o640 and dir_fd == 41
        run.audit("open", (name, None, actual_flags))
        raise OSError("synthetic open refusal")

    run._os_open = original
    with pytest.raises(OSError, match="synthetic open refusal"):
        run.guarded_open("data.txt", flags, 0o640, dir_fd=41)
    assert run.effects == []
    assert run._open_paths == []


def test_framework_activity_outside_execution_does_not_claim_a_unit_effect(tmp_path):
    run = UnitRun(Mock(), tmp_path, {}, tmp_path)
    run.audit("subprocess.Popen", ())
    run.audit("open", (str(Path("unadmitted.py")), "r", 0))
    assert run.effects == []


@pytest.mark.parametrize("reason", ["known failure", ""])
def test_xpass_cannot_be_serialized_as_unqualified_passing_evidence(tmp_path, reason):
    run = UnitRun(Mock(), tmp_path, {}, tmp_path)
    run.pytest_runtest_logreport(SimpleNamespace(
        nodeid="test.py::test_case", when="call", outcome="passed", wasxfail=reason,
    ))
    assert run.effects == ["xfail/xpass outcome is not positive unit evidence"]


def test_only_the_actual_python_test_frame_starts_a_call_context(tmp_path):
    def body():
        pass

    run = UnitRun(Mock(), tmp_path, {}, tmp_path)
    item = SimpleNamespace(obj=body, nodeid="test.py::test_case")
    run.pytest_runtest_call(item)
    assert run.dynamic_context(SimpleNamespace(f_code=body.__code__)) == item.nodeid + "|call"
    assert run.dynamic_context(SimpleNamespace(f_code=UnitRun.pytest_runtest_logreport.__code__)) is None
    run.pytest_runtest_teardown(item)
    assert run.dynamic_context(SimpleNamespace(f_code=body.__code__)) is None
