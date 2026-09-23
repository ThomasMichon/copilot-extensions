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
from agent_machines import self_update as SU
from agent_machines import self_update_tasks
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

    def __init__(
        self, script: dict[tuple, RunOutcome] | None = None, default: RunOutcome | None = None
    ):
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
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "package",
                "id": "marlocarlo.psmux",
                "manager": "winget",
                "version": "3.3.5",
                "pin": True,
            },
            {
                "type": "file",
                "path": "$HOME/.psmux.conf",
                "strategy": "ensure-present",
                "content": "set -g mouse on\n",
            },
        ],
    )
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
        _pkg(
            tmp_path,
            "acme/a",
            [{"type": "package", "id": "x", "manager": "winget", "state": "maybe"}],
        )
    with pytest.raises(ManifestError):
        _pkg(tmp_path, "acme/b", [{"type": "file", "path": "/x", "strategy": "obliterate"}])


def test_resource_maintenance_safe_must_be_boolean(tmp_path):
    with pytest.raises(ManifestError, match="maintenance_safe must be a boolean"):
        _pkg(
            tmp_path,
            "acme/a",
            [{"type": "file", "path": "/x", "maintenance_safe": "yes"}],
        )


def test_package_process_guard_schema(tmp_path):
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "package",
                "id": "example.tool",
                "manager": "winget",
                "process_guard": {"names": ["example.exe"]},
            }
        ],
    )
    assert pkg.resources[0]["process_guard"]["names"] == ["example.exe"]

    invalid = [
        {"type": "package", "id": "x", "manager": "winget", "process_guard": ["x.exe"]},
        {"type": "package", "id": "x", "manager": "winget", "process_guard": {"names": []}},
        {
            "type": "package",
            "id": "x",
            "manager": "winget",
            "process_guard": {"names": ["x.exe"], "mode": "kill"},
        },
        {"type": "file", "path": "/x", "process_guard": {"names": ["x.exe"]}},
    ]
    for index, resource in enumerate(invalid):
        with pytest.raises(ManifestError):
            _pkg(tmp_path, f"acme/invalid-{index}", [resource])


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
    b = _pkg(
        tmp_path,
        "acme/b",
        [{"type": "package", "id": "z", "manager": "winget", "state": "absent"}],
    )
    findings = detect_conflicts([a, b], "box-1", "windows")
    assert any(f.level == "error" and f.code == "resource-conflict" for f in findings)


def test_package_version_conflict(tmp_path):
    a = _pkg(
        tmp_path, "acme/a", [{"type": "package", "id": "z", "manager": "winget", "version": "1.0"}]
    )
    b = _pkg(
        tmp_path, "acme/b", [{"type": "package", "id": "z", "manager": "winget", "version": "2.0"}]
    )
    findings = detect_conflicts([a, b], "box-1", "windows")
    assert any("conflicting" in f.message and f.level == "error" for f in findings)


def test_package_pin_ored_and_compatible_merge(tmp_path):
    a = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "package", "id": "z", "manager": "winget", "version": "3.3.5"}],
    )
    b = _pkg(
        tmp_path,
        "acme/b",
        [{"type": "package", "id": "z", "manager": "winget", "version": "3.3.5", "pin": True}],
    )
    resolved, findings = resolve_resources([a, b], "box-1", "windows")
    assert not findings
    pkg_res = next(r for r in resolved if r.type == "package")
    assert pkg_res.desired["pin"] is True  # OR of pins
    assert pkg_res.desired["version"] == "3.3.5"


def test_package_process_guards_merge_by_casefolded_union(tmp_path):
    a = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "package",
                "id": "z",
                "manager": "winget",
                "version": "3.3.5",
                "process_guard": {"names": ["PSMUX.EXE"]},
            }
        ],
    )
    b = _pkg(
        tmp_path,
        "acme/b",
        [
            {
                "type": "package",
                "id": "z",
                "manager": "winget",
                "version": "3.3.5",
                "process_guard": {"names": ["psmux.exe", "helper.exe"]},
            }
        ],
    )
    r1, f1 = resolve_resources([a, b], "box-1", "windows")
    r2, f2 = resolve_resources([b, a], "box-1", "windows")
    assert not f1 and not f2
    g1 = next(r for r in r1 if r.type == "package").desired["process_guard"]
    g2 = next(r for r in r2 if r.type == "package").desired["process_guard"]
    assert g1 == g2 == {"names": ["helper.exe", "psmux.exe"]}


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
    a = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "file", "path": "$HOME/.f", "strategy": "enforce", "content": "A"}],
    )
    b = _pkg(
        tmp_path,
        "acme/b",
        [{"type": "file", "path": "$HOME/.f", "strategy": "enforce", "content": "B"}],
    )
    findings = detect_conflicts([a, b], "box-1", "windows")
    assert any(f.level == "error" and "enforced to conflicting" in f.message for f in findings)


def test_file_format_conflict(tmp_path):
    a = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "file", "path": "$HOME/.f", "format": "text", "content": "A"}],
    )
    b = _pkg(
        tmp_path,
        "acme/b",
        [{"type": "file", "path": "$HOME/.f", "format": "json", "content": "{}"}],
    )
    findings = detect_conflicts([a, b], "box-1", "windows")
    assert any(f.code == "resource-conflict" and "formats" in f.message for f in findings)


def test_file_enforce_beats_ensure_present_advisory(tmp_path):
    a = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "file", "path": "$HOME/.f", "strategy": "enforce", "content": "A"}],
    )
    b = _pkg(
        tmp_path,
        "acme/b",
        [{"type": "file", "path": "$HOME/.f", "strategy": "ensure-present", "content": "B"}],
    )
    resolved, findings = resolve_resources([a, b], "box-1", "windows")
    fr = next(r for r in resolved if r.type == "file")
    assert fr.desired["strategy"] == "enforce"
    assert fr.desired["content"] == "A"
    assert any(f.level == "advisory" for f in findings)


def test_file_ensure_present_differing_is_advisory_and_deterministic(tmp_path):
    a = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "file", "path": "$HOME/.f", "strategy": "ensure-present", "content": "zzz"}],
    )
    b = _pkg(
        tmp_path,
        "acme/b",
        [{"type": "file", "path": "$HOME/.f", "strategy": "ensure-present", "content": "aaa"}],
    )
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
    return ResourceContext(
        home=tmp_path, repo_paths={"acme": tmp_path}, platform=plat, runner=runner
    )


def test_package_apply_dry_run_plans_install(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")
    runner = FakeRunner(default=RunOutcome(0, "no results", ""))  # not present
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "package",
                "id": "marlocarlo.psmux",
                "manager": "winget",
                "version": "3.3.5",
                "pin": True,
            }
        ],
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=True)
    res = next(r for r in results if r.type == "package")
    assert res.changed and res.dry_run and res.action == "install"
    # Dry-run gathers argv but never mutates.
    assert any("install" in c for c in res.commands)
    # Only the detect call ran; install/pin were planned, not executed.
    assert all("install" not in c for c in runner.calls)


def test_package_apply_installs_and_pins(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")

    class InstallingRunner:
        def __init__(self):
            self.installed = False
            self.pinned = False
            self.calls = []

        def __call__(self, argv):
            self.calls.append(argv)
            if argv[:2] == ["winget", "list"]:
                text = "psmux marlocarlo.psmux 3.3.5 winget" if self.installed else ""
                return RunOutcome(0, text, "")
            if argv[:3] == ["winget", "pin", "list"]:
                text = "psmux marlocarlo.psmux 3.3.5 winget Gating 3.3.5" if self.pinned else ""
                return RunOutcome(0, text, "")
            if argv[:2] == ["winget", "install"]:
                self.installed = True
            if argv[:3] == ["winget", "pin", "add"]:
                self.pinned = True
            return RunOutcome(0, "", "")

    runner = InstallingRunner()
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "package",
                "id": "marlocarlo.psmux",
                "manager": "winget",
                "version": "3.3.5",
                "pin": True,
            }
        ],
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)
    res = next(r for r in results if r.type == "package")
    assert res.changed and not res.dry_run and res.ok
    ran = [c[:2] for c in runner.calls]
    assert ["winget", "install"] in ran
    assert ["winget", "pin"] in ran


def test_package_apply_already_present_no_change(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")
    runner = FakeRunner(script={("winget", "list"): RunOutcome(0, "marlocarlo.psmux 3.3.5", "")})
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "package", "id": "marlocarlo.psmux", "manager": "winget", "version": "3.3.5"}],
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)
    res = next(r for r in results if r.type == "package")
    assert not res.changed and res.action == "none"


def test_winget_parser_requires_exact_id_and_reads_version_column():
    out = RunOutcome(
        0,
        "Name        Id                       Version Available Source\n"
        "-----------------------------------------------------------\n"
        "Other       prefix.marlocarlo.psmux  9.9.9             winget\n"
        "psmux beta  marlocarlo.psmux         3.3.5   3.3.7     winget\n",
        "",
    )
    assert R._parse_winget(out, "marlocarlo.psmux") == {
        "present": True,
        "version": "3.3.5",
    }
    assert R._parse_winget(out, "missing.psmux") == {
        "present": False,
        "version": None,
    }


def test_package_apply_exact_package_and_pin_are_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")
    runner = FakeRunner(
        script={
            ("winget", "list"): RunOutcome(0, "psmux marlocarlo.psmux 3.3.5 3.3.7 winget", ""),
            ("winget", "pin", "list"): RunOutcome(
                0, "psmux marlocarlo.psmux 3.3.5 winget Gating 3.3.5", ""
            ),
        }
    )
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "package",
                "id": "marlocarlo.psmux",
                "manager": "winget",
                "version": "3.3.5",
                "pin": True,
            },
        ],
    )
    res = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)[0]
    assert res.status == "ok"
    assert res.action == "none"
    assert [call[:3] for call in runner.calls] == [
        ["winget", "list", "--id"],
        ["winget", "pin", "list"],
    ]


def test_winget_present_wrong_version_uses_verified_exact_upgrade(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/tool.exe")

    class UpgradeRunner:
        def __init__(self):
            self.version = "3.3.3"
            self.calls = []

        def __call__(self, argv):
            self.calls.append(argv)
            if argv[:2] == ["winget", "list"]:
                return RunOutcome(
                    0,
                    "Name  Id               Version Available Source\n"
                    "------------------------------------------------\n"
                    f"psmux marlocarlo.psmux {self.version}   3.3.7     winget\n",
                    "",
                )
            if argv[:3] == ["winget", "pin", "list"]:
                return RunOutcome(
                    0,
                    "Name  Id               Version Source Pin type Pinned version\n"
                    "-------------------------------------------------------------\n"
                    f"psmux marlocarlo.psmux {self.version}   winget Gating   3.3.5\n",
                    "",
                )
            if argv[:2] == ["winget", "upgrade"]:
                self.version = "3.3.5"
                return RunOutcome(0, "Successfully installed", "")
            return RunOutcome(0, "", "")

    runner = UpgradeRunner()
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "package",
                "id": "marlocarlo.psmux",
                "manager": "winget",
                "version": "3.3.5",
                "pin": True,
            }
        ],
    )
    res = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)[0]
    assert res.ok and res.action == "update"
    upgrade = next(call for call in runner.calls if call[:2] == ["winget", "upgrade"])
    assert "--exact" in upgrade
    assert "--include-pinned" in upgrade
    assert upgrade[upgrade.index("--version") + 1] == "3.3.5"
    assert not any(call[:2] == ["winget", "install"] for call in runner.calls)
    assert sum(call[:2] == ["winget", "list"] for call in runner.calls) == 2
    assert not any(call[:3] == ["winget", "pin", "add"] for call in runner.calls)


def test_maintenance_safe_package_realigns_pinned_installed_version_without_flag(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/tool.exe")

    class UpgradeRunner:
        def __init__(self):
            self.version = "3.3.3"
            self.calls = []

        def __call__(self, argv):
            self.calls.append(argv)
            if argv[:2] == ["winget", "list"]:
                return RunOutcome(
                    0,
                    "Name  Id               Version Available Source\n"
                    "------------------------------------------------\n"
                    f"psmux marlocarlo.psmux {self.version}   3.3.7     winget\n",
                    "",
                )
            if argv[:2] == ["winget", "upgrade"]:
                self.version = "3.3.5"
                return RunOutcome(0, "Successfully installed", "")
            if argv[:3] == ["winget", "pin", "list"]:
                return RunOutcome(
                    0,
                    "Name  Id               Version Source Pin type Pinned version\n"
                    "-------------------------------------------------------------\n"
                    "psmux marlocarlo.psmux 3.3.5   winget Gating   3.3.5\n",
                    "",
                )
            return RunOutcome(0, "", "")

    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "package", "id": "marlocarlo.psmux", "manager": "winget", "version": "3.3.5",
          "pin": True}],
    )
    runner = UpgradeRunner()
    result = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False, maintenance_safe=True
    )[0]
    assert result.status == "changed"
    assert result.action == "update"
    assert result.skipped_reason is None
    assert any(call[:2] == ["winget", "upgrade"] for call in runner.calls)


def test_maintenance_safe_package_skips_first_install_without_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")
    runner = FakeRunner(default=RunOutcome(0, "no results", ""))
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "package", "id": "marlocarlo.psmux", "manager": "winget"}],
    )
    result = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False, maintenance_safe=True
    )[0]
    assert result.status == "skipped"
    assert result.skipped_reason == (
        "not maintenance-safe (would install; declare maintenance_safe: true "
        "to include it in unattended runs)"
    )
    assert runner.calls == [[
        "winget", "list", "--id", "marlocarlo.psmux", "--exact", "--accept-source-agreements",
    ]]


def test_maintenance_safe_package_honors_explicit_flag_for_first_install(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")

    class InstallingRunner:
        def __init__(self):
            self.installed = False
            self.calls = []

        def __call__(self, argv):
            self.calls.append(argv)
            if argv[:2] == ["winget", "list"]:
                return RunOutcome(
                    0,
                    "marlocarlo.psmux 3.3.5" if self.installed else "no results",
                    "",
                )
            if argv[:2] == ["winget", "install"]:
                self.installed = True
            return RunOutcome(0, "", "")

    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "package", "id": "marlocarlo.psmux", "manager": "winget",
          "maintenance_safe": True}],
    )
    runner = InstallingRunner()
    result = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False, maintenance_safe=True
    )[0]
    assert result.status == "changed"
    assert result.action == "install"
    assert any(call[:2] == ["winget", "install"] for call in runner.calls)


@pytest.mark.parametrize("dry_run", [True, False])
def test_package_process_guard_defers_live_wrong_version(tmp_path, monkeypatch, dry_run):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/tool.exe")
    runner = FakeRunner(
        script={
            ("winget", "list"): RunOutcome(0, "psmux marlocarlo.psmux 3.3.3 3.3.7 winget", ""),
            ("winget", "pin", "list"): RunOutcome(
                0, "psmux marlocarlo.psmux 3.3.3 winget Gating 3.3.5", ""
            ),
            ("tasklist",): RunOutcome(
                0,
                '"psmux.exe","123","Console","1","10,000 K"\n'
                '"other.exe","456","Console","1","2,000 K"\n',
                "",
            ),
        }
    )
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "package",
                "id": "marlocarlo.psmux",
                "manager": "winget",
                "version": "3.3.5",
                "pin": True,
                "process_guard": {"names": ["psmux.exe"]},
            }
        ],
    )
    res = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=dry_run)[0]
    assert res.ok and res.status == "deferred" and res.action == "defer"
    assert res.changed is False
    assert res.deferred_reason == "process guard matched running: psmux.exe"
    assert "deferred update from 3.3.3 to 3.3.5" == res.detail
    assert res.commands[0][:2] == ["winget", "upgrade"]
    assert not any(call[:2] == ["winget", "upgrade"] for call in runner.calls)


def test_package_process_guard_defers_when_probe_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/tool.exe")
    runner = FakeRunner(
        script={
            ("winget", "list"): RunOutcome(0, "psmux marlocarlo.psmux 3.3.3 3.3.7 winget", ""),
            ("tasklist",): RunOutcome(1, "", "access denied"),
        }
    )
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "package",
                "id": "marlocarlo.psmux",
                "manager": "winget",
                "version": "3.3.5",
                "process_guard": {"names": ["psmux.exe"]},
            }
        ],
    )
    res = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)[0]
    assert res.status == "deferred"
    assert "state is unknown" in (res.deferred_reason or "")
    assert "access denied" in (res.deferred_reason or "")


def test_package_process_guard_defers_when_package_probe_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/tool.exe")
    runner = FakeRunner(
        script={
            ("winget", "list"): RunOutcome(1, "", "source unavailable"),
        }
    )
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "package",
                "id": "marlocarlo.psmux",
                "manager": "winget",
                "version": "3.3.5",
                "process_guard": {"names": ["psmux.exe"]},
            }
        ],
    )
    res = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)[0]
    assert res.status == "deferred"
    assert "installed state is unknown" in res.detail
    assert not any(call[0] in {"tasklist"} for call in runner.calls)
    assert not any(
        len(call) > 1 and call[:2] in (["winget", "install"], ["winget", "upgrade"])
        for call in runner.calls
    )


def test_package_process_guard_does_not_block_first_install(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/tool.exe")
    runner = FakeRunner(
        script={
            ("winget", "list"): RunOutcome(
                -1978335212,
                "No installed package found matching input criteria.",
                "",
            ),
        }
    )
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "package",
                "id": "example.tool",
                "manager": "winget",
                "version": "1.0.0",
                "process_guard": {"names": ["example.exe"]},
            }
        ],
    )
    res = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=True)[0]
    assert res.action == "install"
    assert not any(call[0] == "tasklist" for call in runner.calls)


def test_package_apply_force_replaces_wrong_winget_pin(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")

    class ReplacingPinRunner:
        def __init__(self):
            self.pin = "3.3.7"
            self.calls = []

        def __call__(self, argv):
            self.calls.append(argv)
            if argv[:2] == ["winget", "list"]:
                return RunOutcome(0, "psmux marlocarlo.psmux 3.3.5 3.3.7 winget", "")
            if argv[:3] == ["winget", "pin", "list"]:
                return RunOutcome(
                    0,
                    f"psmux marlocarlo.psmux 3.3.5 winget Gating {self.pin}",
                    "",
                )
            if argv[:3] == ["winget", "pin", "add"]:
                self.pin = "3.3.5"
            return RunOutcome(0, "", "")

    runner = ReplacingPinRunner()
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "package",
                "id": "marlocarlo.psmux",
                "manager": "winget",
                "version": "3.3.5",
                "pin": True,
            },
        ],
    )
    res = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)[0]
    assert res.ok
    pin_add = next(call for call in runner.calls if call[:3] == ["winget", "pin", "add"])
    assert "--exact" in pin_add
    assert "--force" in pin_add
    assert pin_add[pin_add.index("--version") + 1] == "3.3.5"
    assert sum(call[:3] == ["winget", "pin", "list"] for call in runner.calls) == 2


def test_winget_nonzero_install_is_ok_only_after_exact_postcondition(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")

    class PostconditionRunner:
        def __init__(self):
            self.list_calls = 0
            self.calls = []

        def __call__(self, argv):
            self.calls.append(argv)
            if argv[:2] == ["winget", "list"]:
                self.list_calls += 1
                if self.list_calls == 1:
                    return RunOutcome(0, "No installed package found.", "")
                return RunOutcome(0, "psmux marlocarlo.psmux 3.3.5 winget", "")
            if argv[:2] == ["winget", "install"]:
                return RunOutcome(2316632146, "", "An existing package is already installed.")
            return RunOutcome(0, "", "")

    runner = PostconditionRunner()
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {"type": "package", "id": "marlocarlo.psmux", "manager": "winget", "version": "3.3.5"},
        ],
    )
    res = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)[0]
    assert res.ok
    assert res.status == "changed"
    assert "verified package postcondition" in res.detail


def test_winget_nonzero_upgrade_fails_when_postcondition_is_wrong(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")
    runner = FakeRunner(
        script={
            ("winget", "list"): RunOutcome(0, "psmux marlocarlo.psmux 3.3.3 winget", ""),
            ("winget", "upgrade"): RunOutcome(
                2316632146, "", "An existing package is already installed."
            ),
        }
    )
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {"type": "package", "id": "marlocarlo.psmux", "manager": "winget", "version": "3.3.5"},
        ],
    )
    res = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)[0]
    assert not res.ok
    assert res.status == "error"
    assert "2316632146" in res.detail


def test_winget_uninstall_requires_verified_absence(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")
    runner = FakeRunner(
        script={
            ("winget", "list"): RunOutcome(0, "psmux marlocarlo.psmux 3.3.5 winget", ""),
            ("winget", "uninstall"): RunOutcome(0, "Successfully uninstalled", ""),
        }
    )
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {"type": "package", "id": "marlocarlo.psmux", "manager": "winget", "state": "absent"},
        ],
    )
    res = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)[0]
    assert not res.ok
    assert res.status == "error"
    assert "without satisfying the exact postcondition" in res.detail
    assert sum(call[:2] == ["winget", "list"] for call in runner.calls) == 2


def test_package_apply_absent_uninstalls(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")

    class UninstallRunner:
        def __init__(self):
            self.present = True

        def __call__(self, argv):
            if argv[:2] == ["winget", "list"]:
                if self.present:
                    return RunOutcome(0, "marlocarlo.psmux 3.3.5", "")
                return RunOutcome(
                    2316632084,
                    "No installed package found matching input criteria.",
                    "",
                )
            if argv[:2] == ["winget", "uninstall"]:
                self.present = False
            return RunOutcome(0, "", "")

    runner = UninstallRunner()
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "package", "id": "marlocarlo.psmux", "manager": "winget", "state": "absent"}],
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)
    res = next(r for r in results if r.type == "package")
    assert res.action == "uninstall" and res.changed


def test_package_apply_missing_binary_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: None)
    runner = FakeRunner()
    pkg = _pkg(tmp_path, "acme/a", [{"type": "package", "id": "z", "manager": "winget"}])
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)
    res = next(r for r in results if r.type == "package")
    assert res.skipped_reason and "PATH" in res.skipped_reason
    assert runner.calls == []  # never invoked


def test_package_apply_wrong_platform_filtered(tmp_path):
    # winget only applies on windows; on linux the resource is filtered out entirely.
    pkg = _pkg(tmp_path, "acme/a", [{"type": "package", "id": "z", "manager": "winget"}])
    results = apply_resources(
        [pkg], "box-1", "linux", _ctx(tmp_path, FakeRunner(), plat="linux"), dry_run=True
    )
    assert not [r for r in results if r.type == "package"]


# --------------------------------------------------------------------------- #
# Apply -- file handler
# --------------------------------------------------------------------------- #
def test_file_apply_text_enforce_writes_and_backs_up(tmp_path):
    target = tmp_path / ".psmux.conf"
    target.write_text("old\n", encoding="utf-8")
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "file", "path": "$HOME/.psmux.conf", "strategy": "enforce", "content": "new\n"}],
    )
    results = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, FakeRunner()), dry_run=False
    )
    res = next(r for r in results if r.type == "file")
    assert res.changed and target.read_text(encoding="utf-8") == "new\n"
    assert res.backup_path  # existing file was backed up before overwrite


def test_maintenance_safe_file_requires_opt_in(tmp_path):
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "file", "path": "$HOME/.psmux.conf", "strategy": "enforce", "content": "new\n"}],
    )
    result = apply_resources(
        [pkg],
        "box-1",
        "windows",
        _ctx(tmp_path, FakeRunner()),
        dry_run=False,
        maintenance_safe=True,
    )[0]
    assert result.status == "skipped"
    assert "maintenance_safe: true" in (result.skipped_reason or "")
    assert not (tmp_path / ".psmux.conf").exists()


def test_maintenance_safe_file_applies_with_opt_in(tmp_path):
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "file", "path": "$HOME/.psmux.conf", "strategy": "enforce",
          "content": "new\n", "maintenance_safe": True}],
    )
    result = apply_resources(
        [pkg],
        "box-1",
        "windows",
        _ctx(tmp_path, FakeRunner()),
        dry_run=False,
        maintenance_safe=True,
    )[0]
    assert result.status == "changed"
    assert (tmp_path / ".psmux.conf").read_text(encoding="utf-8") == "new\n"


def test_file_apply_ensure_present_leaves_existing(tmp_path):
    target = tmp_path / ".psmux.conf"
    target.write_text("mine\n", encoding="utf-8")
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "file",
                "path": "$HOME/.psmux.conf",
                "strategy": "ensure-present",
                "content": "default\n",
            }
        ],
    )
    results = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, FakeRunner()), dry_run=False
    )
    res = next(r for r in results if r.type == "file")
    assert not res.changed and target.read_text(encoding="utf-8") == "mine\n"


def test_file_apply_ensure_present_creates_when_missing(tmp_path):
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "file",
                "path": "$HOME/.psmux.conf",
                "strategy": "ensure-present",
                "content": "default\n",
            }
        ],
    )
    results = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, FakeRunner()), dry_run=False
    )
    res = next(r for r in results if r.type == "file")
    assert res.changed and (tmp_path / ".psmux.conf").read_text(encoding="utf-8") == "default\n"


def test_file_apply_dry_run_does_not_write(tmp_path):
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "file", "path": "$HOME/.psmux.conf", "strategy": "enforce", "content": "x\n"}],
    )
    results = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, FakeRunner()), dry_run=True
    )
    res = next(r for r in results if r.type == "file")
    assert res.changed and res.dry_run
    assert not (tmp_path / ".psmux.conf").exists()


def test_file_apply_enforce_marker_blocks_unmanaged_existing_file(tmp_path):
    """A whole-file `enforce` with `marker` must never clobber a pre-existing
    file that lacks the marker -- it reports `blocked` (not ok) instead
    (dotfiles#2071)."""
    target = tmp_path / "uv.toml"
    target.write_text("# hand-authored, not ours\n", encoding="utf-8")
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "file",
                "path": "$HOME/uv.toml",
                "strategy": "enforce",
                "marker": "# Managed by acme.",
                "content": "# Managed by acme.\n[[index]]\n",
            }
        ],
    )
    results = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, FakeRunner()), dry_run=False
    )
    res = next(r for r in results if r.type == "file")
    assert res.status == "blocked"
    assert not res.ok
    assert not res.changed
    assert target.read_text(encoding="utf-8") == "# hand-authored, not ours\n"


def test_file_apply_enforce_marker_dry_run_also_blocks(tmp_path):
    target = tmp_path / "uv.toml"
    target.write_text("# hand-authored, not ours\n", encoding="utf-8")
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "file",
                "path": "$HOME/uv.toml",
                "strategy": "enforce",
                "marker": "# Managed by acme.",
                "content": "# Managed by acme.\n[[index]]\n",
            }
        ],
    )
    results = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, FakeRunner()), dry_run=True
    )
    res = next(r for r in results if r.type == "file")
    assert res.status == "blocked"
    assert not res.ok


def test_file_apply_enforce_marker_converges_when_owned(tmp_path):
    """An existing file that already carries the marker is our own prior
    write, so `enforce` still converges/updates it normally."""
    target = tmp_path / "uv.toml"
    target.write_text("# Managed by acme.\nstale\n", encoding="utf-8")
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "file",
                "path": "$HOME/uv.toml",
                "strategy": "enforce",
                "marker": "# Managed by acme.",
                "content": "# Managed by acme.\nfresh\n",
            }
        ],
    )
    results = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, FakeRunner()), dry_run=False
    )
    res = next(r for r in results if r.type == "file")
    assert res.ok and res.changed
    assert target.read_text(encoding="utf-8") == "# Managed by acme.\nfresh\n"
    assert res.backup_path


def test_file_apply_enforce_marker_writes_when_absent(tmp_path):
    """No existing file at all: `marker` is irrelevant, just write it."""
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "file",
                "path": "$HOME/uv.toml",
                "strategy": "enforce",
                "marker": "# Managed by acme.",
                "content": "# Managed by acme.\n[[index]]\n",
            }
        ],
    )
    results = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, FakeRunner()), dry_run=False
    )
    res = next(r for r in results if r.type == "file")
    assert res.ok and res.changed
    assert (tmp_path / "uv.toml").read_text(encoding="utf-8") == "# Managed by acme.\n[[index]]\n"


def test_file_apply_json_enforce_deep_merges(tmp_path):
    target = tmp_path / "cfg.json"
    target.write_text('{"a": 1, "keep": true}', encoding="utf-8")
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "file",
                "path": "$HOME/cfg.json",
                "format": "json",
                "strategy": "enforce",
                "content": '{"a": 2}',
            }
        ],
    )
    results = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, FakeRunner()), dry_run=False
    )
    res = next(r for r in results if r.type == "file")
    assert res.changed
    import json

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["a"] == 2 and data["keep"] is True  # enforce merges, keeps siblings


def test_file_apply_unknown_repo_anchor_skips(tmp_path):
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "file", "path": "$REPO(ghost)/x", "strategy": "enforce", "content": "y"}],
    )
    results = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, FakeRunner()), dry_run=False
    )
    res = next(r for r in results if r.type == "file")
    assert res.skipped_reason and "anchor" in res.skipped_reason


# --------------------------------------------------------------------------- #
# managed-block file strategy
# --------------------------------------------------------------------------- #
_PSMUX_BLOCK = "agent-worktrees mux keybinds (opt-in)"
_PSMUX_BODY = (
    "set -g prefix C-b\n"
    "unbind-key -a -T root\n"
    "bind-key -T root WheelUpPane   send-keys -M\n"
    "bind-key -T root WheelDownPane send-keys -M\n"
    "set -g paste-detection off"
)


def _managed_block_decl(**over):
    decl = {
        "type": "file",
        "path": "$HOME/.psmux.conf",
        "strategy": "managed-block",
        "block": _PSMUX_BLOCK,
        "content": _PSMUX_BODY,
    }
    decl.update(over)
    return decl


def test_managed_block_requires_block(tmp_path):
    with pytest.raises(ManifestError):
        _pkg(
            tmp_path,
            "acme/a",
            [{"type": "file", "path": "$HOME/.f", "strategy": "managed-block", "content": "x"}],
        )


def test_managed_block_rejects_json_format(tmp_path):
    with pytest.raises(ManifestError):
        _pkg(tmp_path, "acme/a", [_managed_block_decl(format="json")])


def test_managed_block_creates_file_with_markers(tmp_path):
    pkg = _pkg(tmp_path, "acme/a", [_managed_block_decl()])
    results = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, FakeRunner()), dry_run=False
    )
    res = next(r for r in results if r.type == "file")
    assert res.changed
    text = (tmp_path / ".psmux.conf").read_text(encoding="utf-8")
    assert "# >>> agent-worktrees mux keybinds (opt-in) >>>" in text
    assert "# <<< agent-worktrees mux keybinds (opt-in) <<<" in text
    assert "set -g prefix C-b" in text
    assert text.endswith("\n")


def test_managed_block_preserves_unrelated_content_and_backs_up(tmp_path):
    target = tmp_path / ".psmux.conf"
    target.write_text("set -g mouse on\n# my own tweak\n", encoding="utf-8")
    pkg = _pkg(tmp_path, "acme/a", [_managed_block_decl()])
    results = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, FakeRunner()), dry_run=False
    )
    res = next(r for r in results if r.type == "file")
    assert res.changed and res.backup_path
    text = target.read_text(encoding="utf-8")
    assert text.startswith("set -g mouse on\n# my own tweak\n")
    assert "# >>> agent-worktrees mux keybinds (opt-in) >>>" in text


def test_managed_block_idempotent_refresh(tmp_path):
    pkg = _pkg(tmp_path, "acme/a", [_managed_block_decl()])
    ctx = _ctx(tmp_path, FakeRunner())
    apply_resources([pkg], "box-1", "windows", ctx, dry_run=False)
    first = (tmp_path / ".psmux.conf").read_text(encoding="utf-8")
    results = apply_resources([pkg], "box-1", "windows", ctx, dry_run=False)
    res = next(r for r in results if r.type == "file")
    assert not res.changed and res.action == "none"
    assert (tmp_path / ".psmux.conf").read_text(encoding="utf-8") == first


def test_managed_block_refresh_does_not_accumulate_blank_lines(tmp_path):
    pkg = _pkg(tmp_path, "acme/a", [_managed_block_decl()])
    ctx = _ctx(tmp_path, FakeRunner())
    for _ in range(3):
        apply_resources([pkg], "box-1", "windows", ctx, dry_run=False)
    text = (tmp_path / ".psmux.conf").read_text(encoding="utf-8")
    assert text.count("# >>> agent-worktrees mux keybinds (opt-in) >>>") == 1


def test_managed_block_absent_removes_only_the_block(tmp_path):
    target = tmp_path / ".psmux.conf"
    pkg_present = _pkg(tmp_path, "acme/a", [_managed_block_decl()])
    ctx = _ctx(tmp_path, FakeRunner())
    # Seed the file with the block plus a user line.
    target.write_text("keep me\n", encoding="utf-8")
    apply_resources([pkg_present], "box-1", "windows", ctx, dry_run=False)
    assert "# >>> agent-worktrees" in target.read_text(encoding="utf-8")

    pkg_absent = _pkg(tmp_path, "acme/b", [_managed_block_decl(state="absent")])
    results = apply_resources([pkg_absent], "box-1", "windows", ctx, dry_run=False)
    res = next(r for r in results if r.type == "file")
    assert res.changed and res.action == "remove-block"
    text = target.read_text(encoding="utf-8")
    assert "# >>> agent-worktrees" not in text
    assert "keep me" in text


def test_managed_block_dry_run_does_not_write(tmp_path):
    pkg = _pkg(tmp_path, "acme/a", [_managed_block_decl()])
    results = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, FakeRunner()), dry_run=True
    )
    res = next(r for r in results if r.type == "file")
    assert res.changed and res.dry_run
    assert not (tmp_path / ".psmux.conf").exists()


def test_managed_block_distinct_blocks_same_file_compatible(tmp_path):
    a = _pkg(tmp_path, "acme/a", [_managed_block_decl(block="block-one", content="a")])
    b = _pkg(tmp_path, "acme/b", [_managed_block_decl(block="block-two", content="b")])
    resolved, findings = resolve_resources([a, b], "box-1", "windows")
    assert not findings  # two distinct blocks in one file coexist
    assert len([r for r in resolved if r.type == "file"]) == 2


def test_managed_block_same_block_conflicting_content_errors(tmp_path):
    a = _pkg(tmp_path, "acme/a", [_managed_block_decl(content="a")])
    b = _pkg(tmp_path, "acme/b", [_managed_block_decl(content="b")])
    findings = detect_conflicts([a, b], "box-1", "windows")
    assert any(f.level == "error" and "conflicting content" in f.message for f in findings)


def test_managed_block_vs_whole_file_same_path_errors(tmp_path):
    a = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "file", "path": "$HOME/.psmux.conf", "strategy": "enforce", "content": "whole"}],
    )
    b = _pkg(tmp_path, "acme/b", [_managed_block_decl()])
    findings = detect_conflicts([a, b], "box-1", "windows")
    assert any(
        f.level == "error" and "whole-file and managed-block" in f.message for f in findings
    )


# --------------------------------------------------------------------------- #
# registry handler (Windows, driven through the injected runner)
# --------------------------------------------------------------------------- #
def test_registry_bad_value_type_rejected(tmp_path):
    with pytest.raises(ManifestError):
        _pkg(
            tmp_path,
            "acme/a",
            [
                {
                    "type": "registry",
                    "path": "HKCU:/Software/X",
                    "name": "n",
                    "value_type": "Nonsense",
                }
            ],
        )


def test_registry_identity_canonicalizes_hive_and_case(tmp_path):
    a = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "registry",
                "path": "HKCU:/Software/App",
                "name": "Flag",
                "value": "1",
                "value_type": "DWord",
            }
        ],
    )
    b = _pkg(
        tmp_path,
        "acme/b",
        [
            {
                "type": "registry",
                "path": "HKEY_CURRENT_USER\\software\\app",
                "name": "flag",
                "value": "0",
                "value_type": "DWord",
            }
        ],
    )
    findings = detect_conflicts([a, b], "box-1", "windows")
    assert any(f.level == "error" and "conflicting values" in f.message for f in findings)


def test_registry_apply_writes_value(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/Windows/System32/reg.exe")
    runner = FakeRunner(script={("reg", "query"): RunOutcome(1, "", "not found")})
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "registry",
                "path": "HKCU:/Software/App",
                "name": "Flag",
                "value": "1",
                "value_type": "DWord",
            }
        ],
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)
    res = next(r for r in results if r.type == "registry")
    assert res.changed and res.ok and res.action == "write"
    add = next(c for c in runner.calls if c[:2] == ["reg", "add"])
    assert "HKEY_CURRENT_USER\\Software\\App" in add
    assert "/t" in add and "REG_DWORD" in add


def test_registry_apply_already_correct_no_change(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/reg.exe")
    query_out = RunOutcome(0, "HKEY_CURRENT_USER\\Software\\App\n    Flag    REG_DWORD    1\n", "")
    runner = FakeRunner(script={("reg", "query"): query_out})
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "registry",
                "path": "HKCU:/Software/App",
                "name": "Flag",
                "value": "1",
                "value_type": "DWord",
            }
        ],
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)
    res = next(r for r in results if r.type == "registry")
    assert not res.changed and res.action == "none"
    assert not any(c[:2] == ["reg", "add"] for c in runner.calls)


def test_registry_apply_absent_deletes(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/reg.exe")
    query_out = RunOutcome(0, "    Flag    REG_SZ    x\n", "")
    runner = FakeRunner(script={("reg", "query"): query_out})
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "registry", "path": "HKCU:/Software/App", "name": "Flag", "state": "absent"}],
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)
    res = next(r for r in results if r.type == "registry")
    assert res.changed and res.action == "delete"
    assert any(c[:2] == ["reg", "delete"] for c in runner.calls)


def test_registry_present_absent_conflict(tmp_path):
    a = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "registry", "path": "HKCU:/Software/App", "name": "Flag", "value": "1"}],
    )
    b = _pkg(
        tmp_path,
        "acme/b",
        [{"type": "registry", "path": "HKCU:/Software/App", "name": "Flag", "state": "absent"}],
    )
    findings = detect_conflicts([a, b], "box-1", "windows")
    assert any(f.level == "error" and "present and absent" in f.message for f in findings)


def test_registry_filtered_on_linux(tmp_path):
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "registry", "path": "HKCU:/Software/App", "name": "n", "value": "1"}],
    )
    results = apply_resources(
        [pkg], "box-1", "linux", _ctx(tmp_path, FakeRunner(), plat="linux"), dry_run=True
    )
    assert not [r for r in results if r.type == "registry"]


# --------------------------------------------------------------------------- #
# feature handler (Windows optional-feature / capability; Linux systemd)
# --------------------------------------------------------------------------- #
def test_feature_requires_manager(tmp_path):
    with pytest.raises(ManifestError):
        _pkg(tmp_path, "acme/a", [{"type": "feature", "id": "Microsoft-Hyper-V"}])


def test_feature_bad_manager_rejected(tmp_path):
    with pytest.raises(ManifestError):
        _pkg(tmp_path, "acme/a", [{"type": "feature", "id": "x", "manager": "chocolatey"}])


def test_feature_windows_optional_feature_enables(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/Windows/System32/dism.exe")
    runner = FakeRunner(
        script={
            ("dism", "/online", "/get-featureinfo"): RunOutcome(
                0, "Feature Name : Microsoft-Hyper-V\nState : Disabled\n", ""
            )
        }
    )
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "feature", "id": "Microsoft-Hyper-V", "manager": "windows-optional-feature"}],
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)
    res = next(r for r in results if r.type == "feature")
    assert res.changed and res.action == "enable" and res.ok
    assert any(c[:3] == ["dism", "/online", "/enable-feature"] for c in runner.calls)


def test_feature_already_enabled_no_change(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/dism.exe")
    runner = FakeRunner(
        script={("dism", "/online", "/get-featureinfo"): RunOutcome(0, "State : Enabled\n", "")}
    )
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "feature", "id": "Microsoft-Hyper-V", "manager": "windows-optional-feature"}],
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)
    res = next(r for r in results if r.type == "feature")
    assert not res.changed and res.action == "none"


def test_feature_capability_absent_removes(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/dism.exe")
    runner = FakeRunner(
        script={
            ("dism", "/online", "/get-capabilityinfo"): RunOutcome(0, "State : Installed\n", "")
        }
    )
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "feature",
                "id": "OpenSSH.Client~~~~0.0.1.0",
                "manager": "windows-capability",
                "state": "absent",
            }
        ],
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)
    res = next(r for r in results if r.type == "feature")
    assert res.changed and res.action == "disable"
    assert any(c[:3] == ["dism", "/online", "/remove-capability"] for c in runner.calls)


def test_feature_linux_systemd_enables(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "/usr/bin/systemctl")
    runner = FakeRunner(script={("systemctl", "is-enabled"): RunOutcome(1, "disabled\n", "")})
    pkg = _pkg(
        tmp_path, "acme/a", [{"type": "feature", "id": "docker", "manager": "linux-systemd"}]
    )
    results = apply_resources(
        [pkg], "box-1", "linux", _ctx(tmp_path, runner, plat="linux"), dry_run=False
    )
    res = next(r for r in results if r.type == "feature")
    assert res.changed and res.action == "enable"
    assert any(c[:2] == ["systemctl", "enable"] for c in runner.calls)


def test_feature_windows_manager_filtered_on_linux(tmp_path):
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "feature", "id": "Microsoft-Hyper-V", "manager": "windows-optional-feature"}],
    )
    results = apply_resources(
        [pkg], "box-1", "linux", _ctx(tmp_path, FakeRunner(), plat="linux"), dry_run=True
    )
    assert not [r for r in results if r.type == "feature"]


def test_feature_present_absent_conflict(tmp_path):
    a = _pkg(
        tmp_path, "acme/a", [{"type": "feature", "id": "X", "manager": "windows-optional-feature"}]
    )
    b = _pkg(
        tmp_path,
        "acme/b",
        [{"type": "feature", "id": "X", "manager": "windows-optional-feature", "state": "absent"}],
    )
    findings = detect_conflicts([a, b], "box-1", "windows")
    assert any(f.level == "error" and "present and absent" in f.message for f in findings)


def test_feature_missing_binary_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: None)
    runner = FakeRunner()
    pkg = _pkg(
        tmp_path, "acme/a", [{"type": "feature", "id": "X", "manager": "windows-optional-feature"}]
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)
    res = next(r for r in results if r.type == "feature")
    assert res.skipped_reason and "PATH" in res.skipped_reason
    assert runner.calls == []


# --------------------------------------------------------------------------- #
# power-setting handler (Windows, driven through the injected runner)
# --------------------------------------------------------------------------- #
def _power_query(ac: int, dc: int) -> RunOutcome:
    return RunOutcome(
        0,
        f"    Current AC Power Setting Index: 0x{ac:08x}\n"
        f"    Current DC Power Setting Index: 0x{dc:08x}\n",
        "",
    )


def test_power_setting_requires_value_and_validates_symbols(tmp_path):
    with pytest.raises(ManifestError):
        _pkg(
            tmp_path,
            "acme/a",
            [
                {
                    "type": "power-setting",
                    "subgroup": "SUB_BUTTONS",
                    "setting": "PBUTTONACTION",
                }
            ],
        )
    with pytest.raises(ManifestError):
        _pkg(
            tmp_path,
            "acme/b",
            [
                {
                    "type": "power-setting",
                    "subgroup": "SUB_BUTTONS",
                    "setting": "PBUTTONACTION",
                    "ac": "reboot",
                }
            ],
        )
    with pytest.raises(ManifestError):
        _pkg(
            tmp_path,
            "acme/c",
            [
                {
                    "type": "power-setting",
                    "subgroup": "SUB_BUTTONS",
                    "setting": "LIDACTION",
                    "ac": "turn-off-display",
                }
            ],
        )
    with pytest.raises(ManifestError):
        _pkg(
            tmp_path,
            "acme/d",
            [
                {
                    "type": "power-setting",
                    "subgroup": "SUB_BUTTONS",
                    "setting": "LIDACTION",
                    "ac": 0,
                    "state": "absent",
                }
            ],
        )


def test_power_setting_merges_ac_dc_and_normalizes_symbols(tmp_path):
    a = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "power-setting",
                "subgroup": "SUB_BUTTONS",
                "setting": "PBUTTONACTION",
                "ac": "hibernate",
            }
        ],
    )
    b = _pkg(
        tmp_path,
        "acme/b",
        [
            {
                "type": "power-setting",
                "subgroup": "sub_buttons",
                "setting": "pbuttonaction",
                "dc": 2,
            }
        ],
    )
    resolved, findings = resolve_resources([a, b], "box-1", "windows")
    assert not findings
    power = next(r for r in resolved if r.type == "power-setting")
    assert power.desired["ac"] == 2
    assert power.desired["dc"] == 2


def test_power_setting_conflicting_source_value_errors(tmp_path):
    a = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "power-setting",
                "subgroup": "SUB_BUTTONS",
                "setting": "LIDACTION",
                "dc": "sleep",
            }
        ],
    )
    b = _pkg(
        tmp_path,
        "acme/b",
        [
            {
                "type": "power-setting",
                "subgroup": "SUB_BUTTONS",
                "setting": "LIDACTION",
                "dc": "hibernate",
            }
        ],
    )
    findings = detect_conflicts([a, b], "box-1", "windows")
    assert any(
        finding.level == "error" and "conflicting DC values" in finding.message
        for finding in findings
    )


def test_power_setting_alias_and_guid_share_collision_identity(tmp_path):
    a = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "power-setting",
                "subgroup": "SUB_BUTTONS",
                "setting": "PBUTTONACTION",
                "ac": "sleep",
            }
        ],
    )
    b = _pkg(
        tmp_path,
        "acme/b",
        [
            {
                "type": "power-setting",
                "subgroup": "4f971e89-eebd-4455-a8de-9e59040e7347",
                "setting": "7648efa3-dd9c-4e3e-b566-50f929386280",
                "ac": "hibernate",
            }
        ],
    )
    findings = detect_conflicts([a, b], "box-1", "windows")
    assert any("conflicting AC values" in finding.message for finding in findings)


def test_power_setting_apply_sets_and_verifies(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/Windows/System32/powercfg.exe")

    class PowerRunner:
        def __init__(self):
            self.ac = 1
            self.dc = 2
            self.calls = []

        def __call__(self, argv):
            self.calls.append(argv)
            if argv[1] == "/QH":
                return _power_query(self.ac, self.dc)
            if argv[1] == "/SETACVALUEINDEX":
                self.ac = int(argv[-1])
            elif argv[1] == "/SETDCVALUEINDEX":
                self.dc = int(argv[-1])
            return RunOutcome(0, "", "")

    runner = PowerRunner()
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "power-setting",
                "id": "power-button",
                "subgroup": "SUB_BUTTONS",
                "setting": "PBUTTONACTION",
                "ac": "hibernate",
                "dc": "hibernate",
            }
        ],
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)
    result = next(r for r in results if r.type == "power-setting")
    assert result.changed and result.ok and result.detail == "applied and verified"
    assert any(call[1] == "/SETACVALUEINDEX" for call in runner.calls)
    assert not any(call[1] == "/SETDCVALUEINDEX" for call in runner.calls)
    assert any(call[1] == "/SETACTIVE" for call in runner.calls)
    assert runner.ac == runner.dc == 2


def test_power_setting_already_correct_no_change(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/powercfg.exe")
    localized = RunOutcome(
        0,
        "    Valor maximo: 0xffffffff\n"
        "    Indice actual de CA: 0x00000000\n"
        "    Indice actual de CC: 0x00000001\n",
        "",
    )
    runner = FakeRunner(script={("powercfg", "/QH"): localized})
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "power-setting",
                "subgroup": "SUB_BUTTONS",
                "setting": "LIDACTION",
                "ac": "do-nothing",
                "dc": "sleep",
            }
        ],
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)
    result = next(r for r in results if r.type == "power-setting")
    assert not result.changed and result.action == "none"
    assert len(runner.calls) == 1


def test_power_setting_non_current_scheme_does_not_activate(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/powercfg.exe")
    runner = FakeRunner(
        script={
            ("powercfg", "/QH"): _power_query(1, 1),
            ("powercfg", "/GETACTIVESCHEME"): RunOutcome(
                0,
                "Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e\n",
                "",
            ),
        }
    )
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "power-setting",
                "scheme": "SCHEME_MAX",
                "subgroup": "SUB_BUTTONS",
                "setting": "PBUTTONACTION",
                "ac": "hibernate",
            }
        ],
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=True)
    result = next(r for r in results if r.type == "power-setting")
    assert result.changed and result.dry_run
    assert any(command[1] == "/SETACVALUEINDEX" for command in result.commands)
    assert not any(command[1] == "/SETACTIVE" for command in result.commands)
    assert [call[1] for call in runner.calls] == ["/QH", "/GETACTIVESCHEME"]


def test_power_setting_post_apply_mismatch_is_error(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/powercfg.exe")
    runner = FakeRunner(script={("powercfg", "/QH"): _power_query(1, 1)})
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "power-setting",
                "subgroup": "SUB_BUTTONS",
                "setting": "PBUTTONACTION",
                "ac": "hibernate",
            }
        ],
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)
    result = next(r for r in results if r.type == "power-setting")
    assert result.status == "error"
    assert "did not match for: ac" in result.detail


def test_power_setting_query_failure_is_error(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/powercfg.exe")
    runner = FakeRunner(script={("powercfg", "/QH"): RunOutcome(1, "", "invalid setting")})
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "power-setting",
                "subgroup": "SUB_BUTTONS",
                "setting": "LIDACTION",
                "ac": 0,
            }
        ],
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=True)
    result = next(r for r in results if r.type == "power-setting")
    assert result.status == "error"
    assert "could not read" in result.detail


def test_power_setting_filtered_on_linux(tmp_path):
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {
                "type": "power-setting",
                "subgroup": "SUB_BUTTONS",
                "setting": "LIDACTION",
                "ac": 0,
            }
        ],
    )
    results = apply_resources(
        [pkg],
        "box-1",
        "linux",
        _ctx(tmp_path, FakeRunner(), plat="linux"),
        dry_run=True,
    )
    assert not [r for r in results if r.type == "power-setting"]


def test_maintenance_safe_registry_requires_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/reg.exe")
    runner = FakeRunner()
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "registry", "path": r"HKCU:\Software\App", "name": "Setting", "value": "1"}],
    )
    result = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False, maintenance_safe=True
    )[0]
    assert result.status == "skipped"
    assert "maintenance_safe: true" in (result.skipped_reason or "")
    assert runner.calls == []


def test_maintenance_safe_registry_applies_with_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/reg.exe")

    class RegistryRunner:
        def __init__(self):
            self.calls = []

        def __call__(self, argv):
            self.calls.append(argv)
            if argv[:2] == ["reg", "query"]:
                return RunOutcome(1, "", "not found")
            return RunOutcome(0, "", "")

    runner = RegistryRunner()
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "registry", "path": r"HKCU:\Software\App", "name": "Setting", "value": "1",
          "maintenance_safe": True}],
    )
    result = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False, maintenance_safe=True
    )[0]
    assert result.status == "changed"
    assert result.action == "write"
    assert any(call[:2] == ["reg", "add"] for call in runner.calls)


def test_maintenance_safe_feature_requires_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "/usr/bin/systemctl")
    runner = FakeRunner()
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "feature", "manager": "linux-systemd", "id": "demo.service"}],
    )
    result = apply_resources(
        [pkg], "box-1", "linux", _ctx(tmp_path, runner, plat="linux"),
        dry_run=False, maintenance_safe=True
    )[0]
    assert result.status == "skipped"
    assert "maintenance_safe: true" in (result.skipped_reason or "")
    assert runner.calls == []


def test_maintenance_safe_feature_applies_with_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "/usr/bin/systemctl")

    class FeatureRunner:
        def __init__(self):
            self.enabled = False
            self.calls = []

        def __call__(self, argv):
            self.calls.append(argv)
            if argv[:2] == ["systemctl", "is-enabled"]:
                return RunOutcome(1, "disabled\n", "") if not self.enabled else RunOutcome(
                    0, "enabled\n", ""
                )
            if argv[:2] == ["systemctl", "enable"]:
                self.enabled = True
            return RunOutcome(0, "", "")

    runner = FeatureRunner()
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "feature", "manager": "linux-systemd", "id": "demo.service",
          "maintenance_safe": True}],
    )
    result = apply_resources(
        [pkg], "box-1", "linux", _ctx(tmp_path, runner, plat="linux"),
        dry_run=False, maintenance_safe=True
    )[0]
    assert result.status == "changed"
    assert result.action == "enable"
    assert any(call[:2] == ["systemctl", "enable"] for call in runner.calls)


def test_maintenance_safe_power_setting_requires_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/powercfg.exe")
    runner = FakeRunner(script={("powercfg", "/QH"): _power_query(0, 0)})
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "power-setting", "subgroup": "SUB_BUTTONS", "setting": "LIDACTION", "ac": 1}],
    )
    result = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False, maintenance_safe=True
    )[0]
    assert result.status == "skipped"
    assert "maintenance_safe: true" in (result.skipped_reason or "")
    assert runner.calls == []


def test_maintenance_safe_power_setting_applies_with_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/powercfg.exe")

    class PowerRunner:
        def __init__(self):
            self.ac = 0
            self.dc = 0
            self.calls = []

        def __call__(self, argv):
            self.calls.append(argv)
            if argv[:2] == ["powercfg", "/QH"]:
                return _power_query(self.ac, self.dc)
            if argv[:2] == ["powercfg", "/SETACVALUEINDEX"]:
                self.ac = int(argv[-1])
                return RunOutcome(0, "", "")
            if argv[:2] == ["powercfg", "/SETACTIVE"]:
                return RunOutcome(0, "", "")
            raise AssertionError(argv)

    runner = PowerRunner()
    pkg = _pkg(
        tmp_path,
        "acme/a",
        [{"type": "power-setting", "subgroup": "SUB_BUTTONS", "setting": "LIDACTION", "ac": 1,
          "maintenance_safe": True}],
    )
    result = apply_resources(
        [pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False, maintenance_safe=True
    )[0]
    assert result.status == "changed"
    assert result.action == "set"
    assert any(call[:2] == ["powercfg", "/SETACVALUEINDEX"] for call in runner.calls)


# --------------------------------------------------------------------------- #
# PSMux acceptance case -- the exact adopter fixture
# --------------------------------------------------------------------------- #
def test_psmux_acceptance_fixture(tmp_path, monkeypatch):
    """PSMux installed + pinned at 3.3.5, plus the opt-in keybind managed block.

    This mirrors what a downstream repo declares to replace a custom
    ``~/.psmux.conf`` persistence script: a pinned package resource and a
    ``managed-block`` file resource whose derived markers match the existing
    agent-worktrees opt-in block, so the block is engine-owned while the rest of
    the user's config is preserved.
    """
    monkeypatch.setattr(R.shutil, "which", lambda _b: "C:/winget.exe")

    class AcceptanceRunner:
        def __init__(self):
            self.installed = False
            self.pinned = False
            self.calls = []

        def __call__(self, argv):
            self.calls.append(argv)
            if argv[:2] == ["winget", "list"]:
                text = "psmux marlocarlo.psmux 3.3.5 winget" if self.installed else ""
                return RunOutcome(0, text, "")
            if argv[:3] == ["winget", "pin", "list"]:
                text = "psmux marlocarlo.psmux 3.3.5 winget Gating 3.3.5" if self.pinned else ""
                return RunOutcome(0, text, "")
            if argv[:2] == ["winget", "install"]:
                self.installed = True
            if argv[:3] == ["winget", "pin", "add"]:
                self.pinned = True
            return RunOutcome(0, "", "")

    runner = AcceptanceRunner()
    # A pre-existing user config: the block must slot in without clobbering it.
    (tmp_path / ".psmux.conf").write_text("set -g mouse on\n", encoding="utf-8")
    pkg = _pkg(
        tmp_path,
        "acme/psmux",
        [
            {
                "type": "package",
                "id": "marlocarlo.psmux",
                "manager": "winget",
                "version": "3.3.5",
                "state": "present",
                "pin": True,
            },
            {
                "type": "file",
                "id": "psmux-keybinds",
                "path": "$HOME/.psmux.conf",
                "strategy": "managed-block",
                "block": _PSMUX_BLOCK,
                "content": _PSMUX_BODY,
            },
        ],
    )
    # No collisions in the canonical fixture.
    assert detect_conflicts([pkg], "box-1", "windows") == []

    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)
    pkg_res = next(r for r in results if r.type == "package")
    file_res = next(r for r in results if r.type == "file")
    assert pkg_res.action == "install" and pkg_res.ok
    assert ["winget", "pin"] in [c[:2] for c in runner.calls]
    assert file_res.changed and file_res.action == "write-block"
    text = (tmp_path / ".psmux.conf").read_text(encoding="utf-8")
    assert text.startswith("set -g mouse on\n")  # user content preserved
    assert "# >>> agent-worktrees mux keybinds (opt-in) >>>" in text
    assert "unbind-key -a -T root" in text
    assert "set -g paste-detection off" in text


def test_self_update_resource_registers_opted_in_task(tmp_path, monkeypatch):
    pkg = _pkg(tmp_path, "acme/watchdog", [{"type": "self-update", "tier": "watchdog"}])
    monkeypatch.setattr(
        "agent_machines.resource_self_update._self_update.reconcile_scheduled_task",
        lambda tier, **kwargs: SU.ScheduledTaskReconcileResult(
            tier=tier,
            desired_state="present",
            status="changed",
            changed=True,
            detail="registered the Scheduled Task",
        ),
    )
    results = apply_resources(
        [pkg],
        "box-1",
        "windows",
        _ctx(tmp_path, FakeRunner()),
        dry_run=False,
    )
    res = results[0]
    assert res.type == "self-update"
    assert res.action == "install"
    assert res.changed is True
    assert res.detail == "registered the Scheduled Task"


def test_self_update_resource_defers_registration_with_install_retry(tmp_path, monkeypatch):
    pkg = _pkg(tmp_path, "acme/watchdog", [{"type": "self-update", "tier": "watchdog"}])
    monkeypatch.setattr(
        "agent_machines.resource_self_update._self_update.reconcile_scheduled_task",
        lambda tier, **kwargs: SU.ScheduledTaskReconcileResult(
            tier=tier,
            desired_state="present",
            status="deferred",
            changed=False,
            detail=(
                "Scheduled Task registration needs elevation -- run once from an "
                "elevated PowerShell to install the Scheduled Task: "
                "agent-machines self-update install --tier watchdog"
            ),
            commands=[["agent-machines", "self-update", "install", "--tier", tier]],
            attempted_elevation=True,
        ),
    )
    results = apply_resources(
        [pkg],
        "box-1",
        "windows",
        _ctx(tmp_path, FakeRunner()),
        dry_run=False,
    )
    res = results[0]
    assert res.status == "deferred"
    assert res.deferred_reason is not None and "elevated PowerShell" in res.deferred_reason
    assert res.commands == [["agent-machines", "self-update", "install", "--tier", "watchdog"]]


def test_self_update_resource_removes_opted_out_task_without_retry_command(tmp_path, monkeypatch):
    pkg = _pkg(
        tmp_path,
        "acme/watchdog",
        [{"type": "self-update", "tier": "watchdog", "state": "absent"}],
    )
    monkeypatch.setattr(
        "agent_machines.resource_self_update._self_update.reconcile_scheduled_task",
        lambda tier, **kwargs: SU.ScheduledTaskReconcileResult(
            tier=tier,
            desired_state="absent",
            status="changed",
            changed=True,
            detail="removed the Scheduled Task",
        ),
    )
    results = apply_resources(
        [pkg],
        "box-1",
        "windows",
        _ctx(tmp_path, FakeRunner()),
        dry_run=False,
    )
    res = results[0]
    assert res.action == "uninstall"
    assert res.changed is True
    assert res.commands == []


def test_self_update_resource_applies_on_linux_and_wsl(tmp_path, monkeypatch):
    """Regression: the self-update resource's platform gate hard-coded
    'windows' only, so a Linux/WSL package opting a machine into the
    watchdog/sweep tiers was silently dropped during resolution -- even
    after self_update_tasks.py grew a systemd --user backend, the resource
    itself never reached it because it never resolved on those platforms."""
    pkg = _pkg(tmp_path, "acme/watchdog", [{"type": "self-update", "tier": "watchdog"}])
    monkeypatch.setattr(
        "agent_machines.resource_self_update._self_update.reconcile_scheduled_task",
        lambda tier, **kwargs: SU.ScheduledTaskReconcileResult(
            tier=tier,
            desired_state="present",
            status="changed",
            changed=True,
            detail="registered the systemd --user timer",
        ),
    )
    for plat in ("linux", "wsl"):
        results = apply_resources(
            [pkg],
            "box-1",
            plat,
            _ctx(tmp_path, FakeRunner(), plat=plat),
            dry_run=False,
        )
        assert len(results) == 1, f"self-update resource did not resolve on {plat!r}"
        res = results[0]
        assert res.type == "self-update"
        assert res.action == "install"
        assert res.changed is True


def test_self_update_resource_absent_on_unsupported_platform(tmp_path):
    """Only Windows and Linux/WSL have a scheduling backend; any other
    platform must not resolve the resource at all rather than silently
    reporting an opt-in that can never register anything."""
    pkg = _pkg(tmp_path, "acme/watchdog", [{"type": "self-update", "tier": "watchdog"}])
    results = apply_resources(
        [pkg],
        "box-1",
        "darwin",
        _ctx(tmp_path, FakeRunner(), plat="darwin"),
        dry_run=False,
    )
    assert results == []


def test_self_update_resource_dry_run_queries_systemd_timer_on_linux(tmp_path, monkeypatch):
    # Regression: apply()'s dry-run path called query_scheduled_task() (always
    # the Windows PowerShell/Scheduled Task probe) directly, unconditionally,
    # instead of dispatching by platform the way reconcile_scheduled_task()
    # does -- so a Linux/WSL dry-run tried to run pwsh/Get-ScheduledTask (or
    # errored) instead of reporting the systemd --user timer's real state.
    # Mirrors the identical fix/test for fleet-update
    # (test_fleet_update_resource_dry_run_queries_systemd_timer_on_linux).
    #
    # Distinguishing the two paths (review finding): query_task_state() now
    # probes availability first (a single "systemctl --user is-system-running"
    # call, reported "running" by this fake), then -- with no unit files on
    # disk -- query_systemd_timer() returns present=False without any further
    # runner call (self_update_tasks.py's early-return branch). The buggy
    # code instead unconditionally called query_scheduled_task() ->
    # _run_powershell(), which -- via the blanket shutil_which patch below --
    # resolves "pwsh" to "/usr/bin/systemctl" and calls
    # runner(["/usr/bin/systemctl", "-NoProfile", "-NonInteractive",
    # "-Command", script]). A runner that tolerantly returned success for
    # *any* argv (the original fake) could not tell that apart from a real
    # systemd call, so the test passed even against the pre-fix code. This
    # runner instead only recognizes the exact systemd --user argv shapes
    # and raises on anything else, including that Windows-shaped call, so a
    # regression back to the unconditional Windows probe fails loudly here.
    from agent_machines.resources import RunOutcome

    monkeypatch.setattr(self_update_tasks.sys, "platform", "linux")
    monkeypatch.setattr(SU, "shutil_which", lambda _b: "/usr/bin/systemctl")

    class LinuxRunner:
        def __call__(self, argv):
            if argv == ["systemctl", "--user", "is-system-running"]:
                return RunOutcome(0, "running\n", "")
            if len(argv) == 3 and argv[:2] == ["systemctl", "--user"]:
                return RunOutcome(0, "", "")
            raise AssertionError(
                f"unexpected non-systemd command dispatched on Linux: {argv!r} "
                "-- the Windows Scheduled Task path must never run here"
            )

    pkg = _pkg(tmp_path, "acme/watchdog", [{"type": "self-update", "tier": "watchdog"}])
    results = apply_resources(
        [pkg],
        "box-1",
        "linux",
        _ctx(tmp_path, LinuxRunner(), plat="linux"),
        dry_run=True,
    )
    res = results[0]
    assert res.type == "self-update"
    assert res.action == "install"
    assert res.changed is True
    # Backend-label fix (review finding): the dry-run detail must name the
    # backend actually queried (systemd --user timer on Linux), not the
    # Windows-only "Scheduled Task" wording unconditionally used before.
    assert "systemd --user timer" in res.detail
    assert "Scheduled Task" not in res.detail


def test_self_update_resource_dry_run_reports_unavailable_without_systemd_manager(
    tmp_path, monkeypatch
):
    """Review finding: query_task_state()'s Linux branch called
    query_systemd_timer() unconditionally, without the
    linux_systemd_user_available() guard reconcile_scheduled_task()/
    scheduled_task_status() already use -- so a dry-run on a host with no
    reachable systemd --user manager could crash (missing systemctl) or
    misreport state, instead of the "skipped, unavailable" outcome the
    non-dry-run reconcile path already reports correctly for this exact
    case (self_update_tasks._reconcile_linux_timer)."""
    monkeypatch.setattr(self_update_tasks.sys, "platform", "linux")
    monkeypatch.setattr(SU, "shutil_which", lambda _b: None)  # no systemctl on PATH

    pkg = _pkg(tmp_path, "acme/watchdog", [{"type": "self-update", "tier": "watchdog"}])

    class NoCallRunner:
        def __call__(self, argv):
            raise AssertionError(
                f"the systemd-availability guard should short-circuit before any "
                f"command runs, but got: {argv!r}"
            )

    results = apply_resources(
        [pkg],
        "box-1",
        "linux",
        _ctx(tmp_path, NoCallRunner(), plat="linux"),
        dry_run=True,
    )
    res = results[0]
    assert res.type == "self-update"
    assert res.action == "none"
    assert res.changed is False
    assert res.skipped_reason is not None
    assert "systemd" in res.detail

