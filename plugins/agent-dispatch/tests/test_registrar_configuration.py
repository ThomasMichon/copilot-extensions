"""Configured supervisor intent stays owned by the existing pointer registry."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path

import pytest

from agent_dispatch import registrar_discovery as rd
from agent_dispatch.registrar_configuration import ConfiguredSupervisorCohort, read_pointer_registry


def member(machine: str = "host", execution_machine: str = "host-linux", env: str = "linux") -> dict:
    return {"machine": machine, "execution_machine": execution_machine, "env": env}


def write_configuration(base: Path, members: object, *, revision: object = 0) -> Path:
    path = rd.pointers_file(base)
    path.write_text(json.dumps({
        "version": 1, "pointers": [],
        "configured_supervisors": {"revision": revision, "members": members},
        "description": "source-owned metadata",
    }), encoding="utf-8")
    return path


def observe(base: Path) -> ConfiguredSupervisorCohort:
    return rd.RegistrarSources(base=base).configured_supervisor_cohort()


@pytest.mark.parametrize("present", [False, True])
def test_legacy_and_missing_registry_do_not_certify_empty_membership(tmp_path: Path, present: bool) -> None:
    if present:
        rd.pointers_file(tmp_path).write_text("[]", encoding="utf-8")
    result = observe(tmp_path)
    assert result.state == "unconfirmed"
    assert not result.complete and not result.members
    assert result.revision is None
    assert rd.load_pointers(tmp_path) == []


def test_explicit_empty_configuration_is_intent_not_enrollment(tmp_path: Path) -> None:
    path = write_configuration(tmp_path, [])
    before = path.read_bytes()
    result = observe(tmp_path)
    assert result.complete and result.revision == 0 and result.members == ()
    assert result.source_revision
    assert path.read_bytes() == before
    assert rd.load_pointers(tmp_path) == []


def test_complete_cohort_preserves_logical_and_execution_identities(tmp_path: Path) -> None:
    write_configuration(tmp_path, [
        member("host", "host-windows", "windows"),
        member("host", "host-linux", "linux"),
        member("other", "other-macos", "macos"),
    ], revision=7)
    result = observe(tmp_path)
    assert result.complete and result.revision == 7
    assert [(item.machine, item.execution_machine, item.env) for item in result.members] == [
        ("host", "host-linux", "linux"),
        ("host", "host-windows", "windows"),
        ("other", "other-macos", "macos"),
    ]


def test_same_execution_machine_with_distinct_environments_is_two_scopes(tmp_path: Path) -> None:
    write_configuration(tmp_path, [member(env="one"), member(env="two")])
    result = observe(tmp_path)
    assert result.complete and len(result.members) == 2


@pytest.mark.parametrize("invalid", [
    None, {}, "host", True, 1, [],
    {"machine": "host", "env": "linux"},
    {"execution_machine": "host-linux", "env": "linux"},
    {"machine": "host", "execution_machine": "host-linux"},
    member(execution_machine=""), member(machine=" "), member(env=" linux"),
    {**member(), "execution_machine": None},
    {**member(), "machine": True},
    {**member(), "extra": "not a scope"},
])
def test_invalid_member_refuses_entire_cohort_not_valid_subset(tmp_path: Path, invalid: object) -> None:
    write_configuration(tmp_path, [member(), invalid])
    result = observe(tmp_path)
    assert result.state == "unavailable"
    assert not result.complete and result.members == () and result.revision is None
    assert result.source_revision
    assert rd.load_pointers(tmp_path) == []


@pytest.mark.parametrize("revision", [None, -1, True, "1", 1.0])
def test_invalid_revision_refuses_membership(tmp_path: Path, revision: object) -> None:
    write_configuration(tmp_path, [member()], revision=revision)
    assert observe(tmp_path).state == "unavailable"


@pytest.mark.parametrize("members", [None, {}, "host"])
def test_members_must_be_an_explicit_whole_list(tmp_path: Path, members: object) -> None:
    write_configuration(tmp_path, members)
    assert observe(tmp_path).state == "unavailable"


def test_duplicate_execution_scope_cannot_hide_under_other_logical_identity(tmp_path: Path) -> None:
    write_configuration(tmp_path, [member(), member(machine="alias")])
    assert observe(tmp_path).state == "unavailable"


@pytest.mark.parametrize("payload", [
    {"version": 2, "pointers": []},
    {"version": True, "pointers": []},
    {"version": "1", "pointers": []},
    {"version": 1, "pointers": None},
    {"version": 1, "pointers": [{"name": "bad"}]},
])
def test_unsupported_or_invalid_registry_does_not_supply_membership(tmp_path: Path, payload: dict) -> None:
    rd.pointers_file(tmp_path).write_text(json.dumps(payload), encoding="utf-8")
    result = observe(tmp_path)
    assert result.state == "unavailable" and result.source_revision is None
    with pytest.raises(rd.RegistrarError):
        rd.load_pointers(tmp_path)


def test_observation_is_fresh_not_last_known_good(tmp_path: Path) -> None:
    path = write_configuration(tmp_path, [member()], revision=3)
    owner = rd.RegistrarSources(base=tmp_path)
    first = owner.configured_supervisor_cohort()
    path.write_text("{invalid", encoding="utf-8")
    failed = owner.configured_supervisor_cohort()
    assert first.complete and failed.state == "unavailable" and failed.members == ()
    write_configuration(tmp_path, [member(env="changed")], revision=4)
    changed = owner.configured_supervisor_cohort()
    assert changed.complete and changed.revision == 4
    assert changed.source_revision != first.source_revision
    assert first.members[0].env == "linux"


def test_read_error_is_not_missing_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(_path: Path) -> bytes:
        raise PermissionError("synthetic refusal")
    monkeypatch.setattr(Path, "read_bytes", denied)
    result = observe(tmp_path)
    assert result.state == "unavailable" and result.reason == "pointer-registry-unavailable"


def test_pointer_writers_preserve_whole_configuration_and_change_source_revision(tmp_path: Path) -> None:
    path = write_configuration(tmp_path, [member()], revision=9)
    initial = observe(tmp_path)
    rd.add_pointer("one", tmp_path / "one", owner="source", aliases=["owner/repo"], base=tmp_path)
    rd.ensure_pointer(rd.Pointer("two", str(tmp_path / "two")), tmp_path)
    rd.remove_pointer("one", tmp_path)
    rd.save_pointers([rd.Pointer("three", str(tmp_path / "three"))], tmp_path)
    current = read_pointer_registry(path)
    assert current.cohort.complete and current.cohort.revision == 9
    assert current.cohort.members == initial.members
    assert current.cohort.source_revision != initial.source_revision
    assert current.envelope and current.envelope["description"] == "source-owned metadata"
    assert [pointer.name for pointer in current.pointers] == ["three"]
    before = path.read_bytes()
    rd.save_pointers(current.pointers, tmp_path)
    assert path.read_bytes() == before


def test_invalid_cohort_metadata_is_preserved_not_replaced_with_empty(tmp_path: Path) -> None:
    path = write_configuration(tmp_path, [member(), member(machine="alias")])
    before = json.loads(path.read_text(encoding="utf-8"))["configured_supervisors"]
    rd.add_pointer("one", tmp_path / "one", base=tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["configured_supervisors"] == before
    assert observe(tmp_path).state == "unavailable"


def test_existing_pointer_lock_serializes_concurrent_envelope_writers(tmp_path: Path) -> None:
    write_configuration(tmp_path, [member()])
    def add(name: str) -> None:
        rd.add_pointer(name, tmp_path / name, base=tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(add, ["one", "two"]))
    assert {pointer.name for pointer in rd.load_pointers(tmp_path)} == {"one", "two"}
    assert observe(tmp_path).complete


def test_owner_observation_has_no_declaration_scan_or_runtime_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_configuration(tmp_path, [member()])
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("membership intent must not discover or enroll runtime work")
    monkeypatch.setattr(rd.RegistrarSources, "refresh", forbidden)
    monkeypatch.setattr(rd, "discover_trusted", forbidden)
    assert observe(tmp_path).complete


def test_source_revision_covers_exact_bytes_not_normalized_newlines(tmp_path: Path) -> None:
    path = write_configuration(tmp_path, [member()])
    original = path.read_bytes()
    result = observe(tmp_path)
    assert result.source_revision == hashlib.sha256(original).hexdigest()
    path.write_bytes(original + b"\r\n")
    changed = observe(tmp_path)
    assert changed.complete and changed.members == result.members
    assert changed.source_revision == hashlib.sha256(original + b"\r\n").hexdigest()
    assert changed.source_revision != result.source_revision


def test_failed_pointer_replace_preserves_config_and_surfaces_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_dispatch import registrar_configuration as configuration
    path = write_configuration(tmp_path, [member()])
    original = path.read_bytes()
    def fail_replace(_source: str, _target: Path) -> None:
        raise PermissionError("synthetic replace refusal")
    monkeypatch.setattr(configuration.os, "replace", fail_replace)
    with pytest.raises(PermissionError, match="replace refusal"):
        rd.add_pointer("one", tmp_path / "one", base=tmp_path)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".pointers-*"))
    assert observe(tmp_path).complete
