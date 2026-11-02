"""A parent's delayed phase write cannot erase the bound child's birth."""

import json

import pytest

from zdd import breadcrumb, diagnostics


@pytest.fixture
def registered(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "100")
    record = breadcrumb.write_breadcrumb(
        tmp_path, state="started", self_retirement=True, new_pid=202,
        new_port=61002, publication_witness="original-publication",
        new_process_start_time="200",
    )
    return {
        "state": "started", "self_retirement": True, "pid": record["pid"],
        "started_at": record["started_at"], "new_pid": record["new_pid"],
        "new_port": record["new_port"], "publication_witness": record["publication_witness"],
        "new_process_start_time": None,
    }


@pytest.mark.parametrize("state", ["started", "flipped", "committed"])
def test_parent_phase_preserves_exact_child_registration(tmp_path, registered, state):
    record = breadcrumb.write_breadcrumb(tmp_path, **{**registered, "state": state})
    assert record["new_process_start_time"] == "200"
    assert breadcrumb.read_breadcrumb(tmp_path) == record
    assert record["owner_process_start_time"] == "100"


@pytest.mark.parametrize("changes", [
    {"started_at": "different-attempt"},
    {"started_at": None},
    {"pid": 303},
    {"new_pid": 303},
    {"new_pid": None},
    {"new_port": 61003},
    {"publication_witness": "different-publication"},
    {"self_retirement": False},
    {"self_retirement": 1},
])
def test_changed_binding_does_not_inherit_child_birth(tmp_path, registered, changes):
    record = breadcrumb.write_breadcrumb(tmp_path, **{**registered, **changes})
    assert record["new_process_start_time"] is None


def test_explicit_caller_birth_is_not_replaced(tmp_path, registered):
    record = breadcrumb.write_breadcrumb(
        tmp_path, **{**registered, "new_process_start_time": "300"},
    )
    assert record["new_process_start_time"] == "300"


@pytest.mark.parametrize("changes", [
    {"owner_process_start_time": None},
    {"owner_process_start_time": "invalid"},
    {"new_process_start_time": None},
    {"new_process_start_time": ""},
    {"new_process_start_time": "invalid"},
    {"self_retirement": False},
])
def test_unbound_registration_never_supplies_birth(tmp_path, registered, changes):
    record = breadcrumb.read_breadcrumb(tmp_path)
    record.update(changes)
    breadcrumb.breadcrumb_path(tmp_path).write_text(json.dumps(record), encoding="utf-8")
    rewritten = breadcrumb.write_breadcrumb(tmp_path, **registered)
    assert rewritten["new_process_start_time"] is None


def test_changed_original_owner_birth_does_not_inherit_registration(
    tmp_path, monkeypatch, registered,
):
    owner_start_time = breadcrumb._owner_start_time
    monkeypatch.setattr(breadcrumb, "_owner_start_time", lambda *args: "300")
    record = breadcrumb.write_breadcrumb(tmp_path, **registered)
    assert record["new_process_start_time"] is None
    assert record["owner_process_start_time"] == "300"
    monkeypatch.setattr(breadcrumb, "_owner_start_time", owner_start_time)
