"""Compatibility declarations exercise the real loader, resolver and handler."""

from __future__ import annotations

import pytest

from agent_machines import manifest, resources
from agent_machines.manifest import ManifestError, load_package
from agent_machines.resources import ResourceContext, RunOutcome, apply_resources, resolve_resources

from ._helpers import base_package, write_package


PACKAGE_ID = "marlocarlo.psmux"


def package(tmp_path, name="example/tools", **changes):
    declaration = {
        "type": "package", "id": PACKAGE_ID, "manager": "winget",
        "minimum_version": "3.3.5", "blocked_versions": ["3.3.6"],
    }
    declaration.update(changes)
    return load_package(write_package(
        tmp_path / name.replace("/", "-"), "tools.yaml",
        base_package(name=name, schema_version=5, resources=[declaration]),
    ))


class Winget:
    def __init__(self, version="3.3.8", *, installed=True, pinned=False):
        self.version = version
        self.installed = installed
        self.pinned = pinned
        self.result_version = "3.4.0"
        self.calls = []
        self.running = ""
        self.probe_exit = 0
        self.remove_pin = True

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[:2] == ["winget", "list"]:
            row = f"PSMux {PACKAGE_ID}" if self.installed else ""
            if self.version is not None and row:
                row += f" {self.version}"
            return RunOutcome(self.probe_exit, row)
        if argv[:3] == ["winget", "pin", "list"]:
            return RunOutcome(0, f"PSMux {PACKAGE_ID} Gating 3.3.8" if self.pinned else "")
        if argv[:3] == ["winget", "pin", "remove"]:
            if self.remove_pin:
                self.pinned = False
        elif argv[:2] in (["winget", "install"], ["winget", "upgrade"]):
            self.installed = True
            self.version = self.result_version
        elif argv[0] == "tasklist":
            return RunOutcome(0, self.running)
        else:
            raise AssertionError(f"Unexpected package mutation: {argv}")
        return RunOutcome(0)

    @property
    def mutations(self):
        return [
            argv for argv in self.calls
            if argv[:2] in (["winget", "install"], ["winget", "upgrade"])
            or argv[:3] == ["winget", "pin", "remove"]
        ]


def apply(tmp_path, monkeypatch, runner, **declaration):
    monkeypatch.setattr(resources.shutil, "which", lambda name: name)
    return apply_resources(
        [package(tmp_path, **declaration)], "box-1", "windows",
        ResourceContext(tmp_path, {}, "windows", runner), dry_run=False,
    )[0]


@pytest.mark.parametrize("version", ["3.3.5", "3.3.8", "3.4.0", "3.10.0", "9.0.0"])
def test_compatible_installed_versions_are_preserved(tmp_path, monkeypatch, version):
    runner = Winget(version)
    result = apply(tmp_path, monkeypatch, runner)
    assert result.status == "ok" and not result.changed
    assert runner.mutations == []


@pytest.mark.parametrize("version", ["3.3.4", "3.3.6", "3.3.6.0", "3.3.6.1"])
def test_incompatible_versions_use_normal_upgrade_and_verify(tmp_path, monkeypatch, version):
    runner = Winget(version)
    result = apply(tmp_path, monkeypatch, runner)
    assert result.action == "update" and result.ok
    assert runner.version == "3.4.0"
    assert len(runner.mutations) == 1
    assert "--version" not in runner.mutations[0]


@pytest.mark.parametrize("version", [None, "unknown", "3.4.0-preview"])
def test_unknown_version_blocks_instead_of_replacing(tmp_path, monkeypatch, version):
    runner = Winget(version)
    result = apply(tmp_path, monkeypatch, runner)
    assert result.status == "blocked"
    assert runner.mutations == []


def test_unknown_installed_state_defers_without_mutation(tmp_path, monkeypatch):
    runner = Winget()
    runner.probe_exit = 1
    result = apply(tmp_path, monkeypatch, runner)
    assert result.status == "deferred"
    assert runner.mutations == []


@pytest.mark.parametrize("installed", [False, True])
def test_a_successful_manager_exit_does_not_override_an_incompatible_result(
    tmp_path, monkeypatch, installed,
):
    runner = Winget("3.3.4", installed=installed)
    runner.result_version = "3.3.6"
    result = apply(tmp_path, monkeypatch, runner)
    assert result.status == "error"
    assert "postcondition" in result.detail


def test_live_process_guard_defers_upgrade_and_pin_removal(tmp_path, monkeypatch):
    runner = Winget("3.3.6", pinned=True)
    runner.running = '"psmux.exe","123","Console","1","1 K"\n'
    result = apply(
        tmp_path, monkeypatch, runner, pin=False,
        process_guard={"names": ["psmux.exe"]},
    )
    assert result.status == "deferred"
    assert runner.mutations == []
    assert runner.pinned


def test_explicit_unpin_migrates_metadata_without_replacing_a_compatible_package(
    tmp_path, monkeypatch,
):
    runner = Winget(pinned=True)
    result = apply(tmp_path, monkeypatch, runner, pin=False)
    assert result.action == "unpin" and result.ok
    assert runner.mutations == [["winget", "pin", "remove", "--id", PACKAGE_ID, "--exact"]]
    assert not runner.pinned and runner.version == "3.3.8"
    runner.calls.clear()
    assert apply(tmp_path, monkeypatch, runner, pin=False).status == "ok"
    assert runner.mutations == []


def test_omitting_pin_preserves_existing_pin_metadata(tmp_path, monkeypatch):
    runner = Winget(pinned=True)
    assert apply(tmp_path, monkeypatch, runner).status == "ok"
    assert runner.pinned and runner.mutations == []


def test_legacy_presence_without_a_floor_does_not_acquire_an_upgrade_policy(tmp_path, monkeypatch):
    runner = Winget(None)
    legacy = load_package(write_package(tmp_path, "legacy.yaml", base_package(
        resources=[{"type": "package", "id": PACKAGE_ID, "manager": "winget"}],
    )))
    monkeypatch.setattr(resources.shutil, "which", lambda name: name)
    result = apply_resources(
        [legacy], "box-1", "windows", ResourceContext(tmp_path, {}, "windows", runner),
        dry_run=False,
    )[0]
    assert result.status == "ok" and not result.changed
    assert runner.mutations == []


def test_unpin_is_verified_instead_of_trusting_exit_zero(tmp_path, monkeypatch):
    runner = Winget(pinned=True)
    runner.remove_pin = False
    result = apply(tmp_path, monkeypatch, runner, pin=False)
    assert result.status == "error"
    assert runner.pinned


def test_constraints_merge_conservatively_and_reject_a_conflicting_exact_pin(tmp_path):
    lower = package(tmp_path, name="example/lower")
    higher = package(
        tmp_path, name="example/higher", minimum_version="3.4.0",
        blocked_versions=["3.4.1"], authority=1,
    )
    exact = load_package(write_package(
        tmp_path / "exact", "tools.yaml",
        base_package(name="example/exact", resources=[{
            "type": "package", "id": PACKAGE_ID, "manager": "winget",
            "version": "3.3.8", "pin": True,
        }]),
    ))
    resolved, findings = resolve_resources([lower, higher], "box-1", "windows")
    assert not findings
    assert resolved[0].desired["minimum_version"] == "3.4.0"
    assert resolved[0].desired["blocked_versions"] == ["3.3.6", "3.4.1"]
    _, findings = resolve_resources([lower, higher, exact], "box-1", "windows")
    assert any(item.level == "error" and "compatibility" in item.message for item in findings)


def test_direct_application_cannot_mutate_an_unsatisfiable_exact_pin(tmp_path, monkeypatch):
    floor = package(tmp_path, minimum_version="3.4.0")
    exact = load_package(write_package(tmp_path / "exact", "tools.yaml", base_package(
        name="example/exact", resources=[{
            "type": "package", "id": PACKAGE_ID, "manager": "winget",
            "version": "3.3.8", "pin": True,
        }],
    )))
    monkeypatch.setattr(resources.shutil, "which", lambda name: name)
    runner = Winget("3.4.0")
    result = apply_resources(
        [floor, exact], "box-1", "windows", ResourceContext(tmp_path, {}, "windows", runner),
        dry_run=False,
    )[0]
    assert result.status == "blocked" and runner.calls == []


@pytest.mark.parametrize("schema", [1, 2, 3, 4])
def test_new_constraints_require_schema_five(tmp_path, schema):
    path = write_package(tmp_path, "tools.yaml", base_package(
        schema_version=schema, resources=[{
            "type": "package", "id": PACKAGE_ID, "manager": "winget",
            "minimum_version": "3.3.5",
        }],
    ))
    with pytest.raises(ManifestError, match="schema_version 5"):
        load_package(path)


def test_prior_engine_rejects_new_declaration_before_any_application(tmp_path, monkeypatch):
    loaded = package(tmp_path)
    monkeypatch.setattr(manifest, "SUPPORTED_SCHEMA_VERSIONS", (1, 2, 3, 4))
    with pytest.raises(ManifestError, match="unsupported schema_version"):
        load_package(loaded.source_path)


@pytest.mark.parametrize("changes", [
    {"minimum_version": 3.5},
    {"minimum_version": None},
    {"blocked_versions": "3.3.6"},
    {"blocked_versions": ["3.3.6-preview"]},
    {"version": "3.3.8"},
    {"pin": True},
    {"pin": 0},
])
def test_invalid_or_ambiguous_constraints_are_rejected(tmp_path, changes):
    with pytest.raises(ManifestError):
        package(tmp_path, **changes)
