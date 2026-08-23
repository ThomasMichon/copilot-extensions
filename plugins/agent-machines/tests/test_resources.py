"""Declarative-resource tests: schema, collision resolution, and apply.

Covers the ``resources:`` subsystem end to end -- manifest parsing/validation,
cross-package collision detection (packages and files), path anchoring, and the
package/file apply handlers driven through an injectable runner so no real
package manager or network is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_machines import resources as R
from agent_machines.manifest import ManifestError, load_package
from agent_machines.resources import (
    ResourceContext,
    RunOutcome,
    apply_resources,
    detect_conflicts,
    resolve_file_path,
    resolve_resources,
)

from ._helpers import base_package, write_package


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class FakeRunner:
    """A scripted package-manager runner: maps argv[:2] tuple -> RunOutcome."""

    def __init__(self, script: dict[tuple, RunOutcome] | None = None,
                 default: RunOutcome | None = None):
        self.script = script or {}
        self.default = default or RunOutcome(0, "", "")
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> RunOutcome:
        self.calls.append(argv)
        for prefix, outcome in self.script.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return outcome
        return self.default


def _pkg(tmp_path: Path, name: str, resources: list[dict], gate=None, **over):
    data = base_package(name=name, gate=gate or ["box-1"], resources=resources, **over)
    path = write_package(tmp_path / name.replace("/", "_"), "pkg.yaml", data)
    return load_package(path, source_repo=name.split("/")[0])


# --------------------------------------------------------------------------- #
# Schema: manifest parse + validation
# --------------------------------------------------------------------------- #
def test_load_package_parses_resources(tmp_path):
    pkg = _pkg(tmp_path, "acme/a", [
        {"type": "package", "id": "marlocarlo.psmux", "manager": "winget",
         "version": "3.3.5", "pin": True},
        {"type": "file", "path": "$HOME/.psmux.conf", "strategy": "ensure-present",
         "content": "set -g mouse on\n"},
    ])
    assert len(pkg.resources) == 2
    assert pkg.resources[0]["id"] == "marlocarlo.psmux"


def test_no_resources_key_defaults_empty(tmp_path):
    pkg = _pkg(tmp_path, "acme/a", [])
    assert pkg.resources == []
    # A package that never declares resources still loads (backward compat).
    data = base_package(name="acme/b")
    path = write_package(tmp_path / "b", "pkg.yaml", data)
    assert load_package(path).resources == []


def test_unknown_resource_type_rejected(tmp_path):
    with pytest.raises(ManifestError):
        _pkg(tmp_path, "acme/a", [{"type": "widget", "id": "x"}])


def test_missing_required_field_rejected(tmp_path):
    with pytest.raises(ManifestError):
        _pkg(tmp_path, "acme/a", [{"type": "package", "id": "x"}])  # no manager
    with pytest.raises(ManifestError):
        _pkg(tmp_path, "acme/b", [{"type": "file", "strategy": "enforce"}])  # no path


def test_bad_state_and_strategy_rejected(tmp_path):
    with pytest.raises(ManifestError):
        _pkg(tmp_path, "acme/a",
             [{"type": "package", "id": "x", "manager": "winget", "state": "maybe"}])
    with pytest.raises(ManifestError):
        _pkg(tmp_path, "acme/b",
             [{"type": "file", "path": "/x", "strategy": "obliterate"}])


def test_resources_must_be_list(tmp_path):
    data = base_package(name="acme/a")
    data["resources"] = {"not": "a list"}
    path = write_package(tmp_path / "a", "pkg.yaml", data)
    with pytest.raises(ManifestError):
        load_package(path)


# --------------------------------------------------------------------------- #
# Path anchoring
# --------------------------------------------------------------------------- #
def test_resolve_file_path_anchors(tmp_path):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repos = {"acme": repo}
    assert resolve_file_path("$HOME/.psmux.conf", home, repos) == home / ".psmux.conf"
    assert resolve_file_path("$REPO(acme)/tools/x", home, repos) == repo / "tools" / "x"
    assert resolve_file_path("/etc/thing", home, repos) == Path("/etc/thing")
    # Unknown repo anchor -> unresolvable (None), so apply skips rather than guessing.
    assert resolve_file_path("$REPO(ghost)/x", home, repos) is None


# --------------------------------------------------------------------------- #
# Collision resolution -- packages
# --------------------------------------------------------------------------- #
def test_package_present_absent_conflict(tmp_path):
    a = _pkg(tmp_path, "acme/a", [{"type": "package", "id": "z", "manager": "winget"}])
    b = _pkg(tmp_path, "acme/b",
             [{"type": "package", "id": "z", "manager": "winget", "state": "absent"}])
    findings = detect_conflicts([a, b], "box-1", "windows")
    assert any(f.level == "error" and f.code == "resource-conflict" for f in findings)


def test_package_version_conflict(tmp_path):
    a = _pkg(tmp_path, "acme/a",
             [{"type": "package", "id": "z", "manager": "winget", "version": "1.0"}])
    b = _pkg(tmp_path, "acme/b",
             [{"type": "package", "id": "z", "manager": "winget", "version": "2.0"}])
    findings = detect_conflicts([a, b], "box-1", "windows")
    assert any("conflicting" in f.message and f.level == "error" for f in findings)


def test_package_pin_ored_and_compatible_merge(tmp_path):
    a = _pkg(tmp_path, "acme/a",
             [{"type": "package", "id": "z", "manager": "winget", "version": "3.3.5"}])
    b = _pkg(tmp_path, "acme/b",
             [{"type": "package", "id": "z", "manager": "winget", "version": "3.3.5",
               "pin": True}])
    resolved, findings = resolve_resources([a, b], "box-1", "windows")
    assert not findings
    pkg_res = next(r for r in resolved if r.type == "package")
    assert pkg_res.desired["pin"] is True  # OR of pins
    assert pkg_res.desired["version"] == "3.3.5"


def test_different_managers_are_distinct_identities(tmp_path):
    a = _pkg(tmp_path, "acme/a", [{"type": "package", "id": "z", "manager": "winget"}])
    b = _pkg(tmp_path, "acme/b", [{"type": "package", "id": "z", "manager": "pipx"}])
    resolved, findings = resolve_resources([a, b], "box-1", "windows")
    assert not findings
    assert len([r for r in resolved if r.type == "package"]) == 2


# --------------------------------------------------------------------------- #
# Collision resolution -- files
# --------------------------------------------------------------------------- #
def test_file_enforce_content_conflict(tmp_path):
    a = _pkg(tmp_path, "acme/a",
             [{"type": "file", "path": "$HOME/.f", "strategy": "enforce", "content": "A"}])
    b = _pkg(tmp_path, "acme/b",
             [{"type": "file", "path": "$HOME/.f", "strategy": "enforce", "content": "B"}])
    findings = detect_conflicts([a, b], "box-1", "windows")
    assert any(f.level == "error" and "enforced to conflicting" in f.message
               for f in findings)


def test_file_format_conflict(tmp_path):
    a = _pkg(tmp_path, "acme/a",
             [{"type": "file", "path": "$HOME/.f", "format": "text", "content": "A"}])
    b = _pkg(tmp_path, "acme/b",
             [{"type": "file", "path": "$HOME/.f", "format": "json", "content": "{}"}])
    findings = detect_conflicts([a, b], "box-1", "windows")
    assert any(f.code == "resource-conflict" and "formats" in f.message for f in findings)


def test_file_enforce_beats_ensure_present_advisory(tmp_path):
    a = _pkg(tmp_path, "acme/a",
             [{"type": "file", "path": "$HOME/.f", "strategy": "enforce", "content": "A"}])
    b = _pkg(tmp_path, "acme/b",
             [{"type": "file", "path": "$HOME/.f", "strategy": "ensure-present",
               "content": "B"}])
    resolved, findings = resolve_resources([a, b], "box-1", "windows")
    fr = next(r for r in resolved if r.type == "file")
    assert fr.desired["strategy"] == "enforce"
    assert fr.desired["content"] == "A"
    assert any(f.level == "advisory" for f in findings)


def test_file_ensure_present_differing_is_advisory_and_deterministic(tmp_path):
    a = _pkg(tmp_path, "acme/a",
             [{"type": "file", "path": "$HOME/.f", "strategy": "ensure-present",
               "content": "zzz"}])
    b = _pkg(tmp_path, "acme/b",
             [{"type": "file", "path": "$HOME/.f", "strategy": "ensure-present",
               "content": "aaa"}])
    r1, f1 = resolve_resources([a, b], "box-1", "windows")
    r2, f2 = resolve_resources([b, a], "box-1", "windows")  # order flipped
    c1 = next(r for r in r1 if r.type == "file").desired["content"]
    c2 = next(r for r in r2 if r.type == "file").desired["content"]
    assert c1 == c2  # deterministic regardless of package order
    assert all(f.level == "advisory" for f in f1)
    assert [f.message for f in f1] == [f.message for f in f2]


# --------------------------------------------------------------------------- #
# Apply -- package handler (through an injected runner)
# --------------------------------------------------------------------------- #
def _ctx(tmp_path, runner, plat="windows"):
    return ResourceContext(home=tmp_path, repo_paths={"acme": tmp_path},
                           platform=plat, runner=runner)


def test_package_apply_dry_run_plans_install(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")
    runner = FakeRunner(default=RunOutcome(0, "no results", ""))  # not present
    pkg = _pkg(tmp_path, "acme/a",
               [{"type": "package", "id": "marlocarlo.psmux", "manager": "winget",
                 "version": "3.3.5", "pin": True}])
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner),
                              dry_run=True)
    res = next(r for r in results if r.type == "package")
    assert res.changed and res.dry_run and res.action == "install"
    # Dry-run gathers argv but never mutates.
    assert any("install" in c for c in res.commands)
    # Only the detect call ran; install/pin were planned, not executed.
    assert all("install" not in c for c in runner.calls)


def test_package_apply_installs_and_pins(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")
    runner = FakeRunner(default=RunOutcome(0, "no results", ""))
    pkg = _pkg(tmp_path, "acme/a",
               [{"type": "package", "id": "marlocarlo.psmux", "manager": "winget",
                 "version": "3.3.5", "pin": True}])
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner),
                              dry_run=False)
    res = next(r for r in results if r.type == "package")
    assert res.changed and not res.dry_run and res.ok
    ran = [c[:2] for c in runner.calls]
    assert ["winget", "install"] in ran
    assert ["winget", "pin"] in ran


def test_package_apply_already_present_no_change(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")
    runner = FakeRunner(
        script={("winget", "list"): RunOutcome(0, "marlocarlo.psmux 3.3.5", "")})
    pkg = _pkg(tmp_path, "acme/a",
               [{"type": "package", "id": "marlocarlo.psmux", "manager": "winget",
                 "version": "3.3.5"}])
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner),
                              dry_run=False)
    res = next(r for r in results if r.type == "package")
    assert not res.changed and res.action == "none"


def test_package_apply_absent_uninstalls(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")
    runner = FakeRunner(
        script={("winget", "list"): RunOutcome(0, "marlocarlo.psmux 3.3.5", "")})
    pkg = _pkg(tmp_path, "acme/a",
               [{"type": "package", "id": "marlocarlo.psmux", "manager": "winget",
                 "state": "absent"}])
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner),
                              dry_run=False)
    res = next(r for r in results if r.type == "package")
    assert res.action == "uninstall" and res.changed


def test_package_apply_missing_binary_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: None)
    runner = FakeRunner()
    pkg = _pkg(tmp_path, "acme/a",
               [{"type": "package", "id": "z", "manager": "winget"}])
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner),
                              dry_run=False)
    res = next(r for r in results if r.type == "package")
    assert res.skipped_reason and "PATH" in res.skipped_reason
    assert runner.calls == []  # never invoked


def test_package_apply_wrong_platform_filtered(tmp_path):
    # winget only applies on windows; on linux the resource is filtered out entirely.
    pkg = _pkg(tmp_path, "acme/a",
               [{"type": "package", "id": "z", "manager": "winget"}])
    results = apply_resources([pkg], "box-1", "linux",
                              _ctx(tmp_path, FakeRunner(), plat="linux"), dry_run=True)
    assert not [r for r in results if r.type == "package"]


# --------------------------------------------------------------------------- #
# Apply -- file handler
# --------------------------------------------------------------------------- #
def test_file_apply_text_enforce_writes_and_backs_up(tmp_path):
    target = tmp_path / ".psmux.conf"
    target.write_text("old\n", encoding="utf-8")
    pkg = _pkg(tmp_path, "acme/a",
               [{"type": "file", "path": "$HOME/.psmux.conf", "strategy": "enforce",
                 "content": "new\n"}])
    results = apply_resources([pkg], "box-1", "windows",
                              _ctx(tmp_path, FakeRunner()), dry_run=False)
    res = next(r for r in results if r.type == "file")
    assert res.changed and target.read_text(encoding="utf-8") == "new\n"
    assert res.backup_path  # existing file was backed up before overwrite


def test_file_apply_ensure_present_leaves_existing(tmp_path):
    target = tmp_path / ".psmux.conf"
    target.write_text("mine\n", encoding="utf-8")
    pkg = _pkg(tmp_path, "acme/a",
               [{"type": "file", "path": "$HOME/.psmux.conf", "strategy": "ensure-present",
                 "content": "default\n"}])
    results = apply_resources([pkg], "box-1", "windows",
                              _ctx(tmp_path, FakeRunner()), dry_run=False)
    res = next(r for r in results if r.type == "file")
    assert not res.changed and target.read_text(encoding="utf-8") == "mine\n"


def test_file_apply_ensure_present_creates_when_missing(tmp_path):
    pkg = _pkg(tmp_path, "acme/a",
               [{"type": "file", "path": "$HOME/.psmux.conf", "strategy": "ensure-present",
                 "content": "default\n"}])
    results = apply_resources([pkg], "box-1", "windows",
                              _ctx(tmp_path, FakeRunner()), dry_run=False)
    res = next(r for r in results if r.type == "file")
    assert res.changed and (tmp_path / ".psmux.conf").read_text(encoding="utf-8") == "default\n"


def test_file_apply_dry_run_does_not_write(tmp_path):
    pkg = _pkg(tmp_path, "acme/a",
               [{"type": "file", "path": "$HOME/.psmux.conf", "strategy": "enforce",
                 "content": "x\n"}])
    results = apply_resources([pkg], "box-1", "windows",
                              _ctx(tmp_path, FakeRunner()), dry_run=True)
    res = next(r for r in results if r.type == "file")
    assert res.changed and res.dry_run
    assert not (tmp_path / ".psmux.conf").exists()


def test_file_apply_json_enforce_deep_merges(tmp_path):
    target = tmp_path / "cfg.json"
    target.write_text('{"a": 1, "keep": true}', encoding="utf-8")
    pkg = _pkg(tmp_path, "acme/a",
               [{"type": "file", "path": "$HOME/cfg.json", "format": "json",
                 "strategy": "enforce", "content": '{"a": 2}'}])
    results = apply_resources([pkg], "box-1", "windows",
                              _ctx(tmp_path, FakeRunner()), dry_run=False)
    res = next(r for r in results if r.type == "file")
    assert res.changed
    import json
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["a"] == 2 and data["keep"] is True  # enforce merges, keeps siblings


def test_file_apply_unknown_repo_anchor_skips(tmp_path):
    pkg = _pkg(tmp_path, "acme/a",
               [{"type": "file", "path": "$REPO(ghost)/x", "strategy": "enforce",
                 "content": "y"}])
    results = apply_resources([pkg], "box-1", "windows",
                              _ctx(tmp_path, FakeRunner()), dry_run=False)
    res = next(r for r in results if r.type == "file")
    assert res.skipped_reason and "anchor" in res.skipped_reason


# --------------------------------------------------------------------------- #
# Reserved types (registry / feature) -- recognized, planned, not applied
# --------------------------------------------------------------------------- #
def test_reserved_type_plans_but_skips_apply(tmp_path):
    pkg = _pkg(tmp_path, "acme/a",
               [{"type": "registry", "path": "HKCU:/Software/X", "id": "x"}])
    resolved, findings = resolve_resources([pkg], "box-1", "windows")
    assert not findings
    assert any(r.type == "registry" for r in resolved)
    results = apply_resources([pkg], "box-1", "windows",
                              _ctx(tmp_path, FakeRunner()), dry_run=True)
    res = next(r for r in results if r.type == "registry")
    assert res.skipped_reason and "no handler" in res.skipped_reason


# --------------------------------------------------------------------------- #
# PSMux acceptance case -- the exact adopter fixture
# --------------------------------------------------------------------------- #
def test_psmux_acceptance_fixture(tmp_path, monkeypatch):
    """PSMux installed + pinned at 3.3.5, plus its local ``~/.psmux.conf``."""
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")
    runner = FakeRunner(default=RunOutcome(0, "no installed package found", ""))
    pkg = _pkg(tmp_path, "acme/psmux", [
        {"type": "package", "id": "marlocarlo.psmux", "manager": "winget",
         "version": "3.3.5", "state": "present", "pin": True},
        {"type": "file", "id": "psmux-settings", "path": "$HOME/.psmux.conf",
         "format": "text", "strategy": "ensure-present", "content": "set -g mouse on\n"},
    ])
    # No collisions in the canonical fixture.
    assert detect_conflicts([pkg], "box-1", "windows") == []

    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner),
                              dry_run=False)
    pkg_res = next(r for r in results if r.type == "package")
    file_res = next(r for r in results if r.type == "file")
    assert pkg_res.action == "install" and pkg_res.ok
    assert ["winget", "pin"] in [c[:2] for c in runner.calls]
    assert file_res.changed
    assert (tmp_path / ".psmux.conf").read_text(encoding="utf-8") == "set -g mouse on\n"
