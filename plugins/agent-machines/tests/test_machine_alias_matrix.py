"""Discovery accepts the full shared legacy-format grid in the actual context."""

from __future__ import annotations

import pytest
import yaml
from machine_transport import legacy_alias_forms

from agent_machines import discover, identity
from agent_machines.manifest import ManifestError
from ._helpers import base_package, write_package

CELLS = [
    (platform, alias)
    for platform in ("windows", "wsl")
    for base in ("Example-Host", "Generated-Host", "Legacy-Alias", "Example Workstation")
    for form in legacy_alias_forms(base, platform)
    for alias in (form, form.swapcase())
]


@pytest.mark.parametrize("actual", ["windows", "wsl"])
@pytest.mark.parametrize("name", ["example-host", "EXAMPLE-HOST"])
@pytest.mark.parametrize("scoped", [False, True])
def test_default_discovery_overlapping_key_profile_preserves_context_and_manual_scope(
    tmp_path, monkeypatch, actual, name, scoped,
):
    root = tmp_path / "example"
    expected = "example-host-wsl" if actual == "wsl" else "example-host"
    write_package(root, "shared.yaml", base_package(name="example/shared", gate=[expected]))
    if scoped:
        machines = {
            "example-host": {
                "execution_platform": "windows", "physical_host": "example-host",
                "ssh": {"environments": [{"name": "windows", "alias": name}]},
            },
            "example-host-wsl": {
                "execution_platform": "wsl", "physical_host": "example-host",
                "ssh": {"environments": [{"name": "wsl", "alias": "guest-login"}]},
            },
        }
    else:
        machines = {"example-host": {"ssh": {"environments": [
            {"name": "windows", "alias": name}, {"name": "wsl", "alias": "guest-login"},
        ]}}}
    (root / "machines.yaml").write_text(yaml.safe_dump({"machines": machines}), encoding="utf-8")
    monkeypatch.setattr(identity.platform, "node", lambda: name)
    monkeypatch.setattr(identity, "detect_platform", lambda: actual)
    monkeypatch.setattr(discover, "current_platform", lambda: actual)
    monkeypatch.setattr(discover, "user_package_root", lambda *a, **kw: tmp_path / "absent")
    registry = {
        "schema_version": 1, "srcroot": {actual: str(tmp_path)},
        "repos": {"example": {"class": "worktree"}},
    }
    projects = {"schema_version": 2, "projects": {"example": {"config_dir": str(tmp_path / "state")}}}
    result = discover.discover(registry=registry, projects=projects)
    assert [package.name for repo in result for package in repo.packages] == ["example/shared"]
    assert identity.resolve_machine(topology_repos=[root]).canonical == expected
    if scoped and actual == "wsl":
        with pytest.raises(ManifestError, match="conflicts"):
            identity.resolve_machine(name, topology_repos=[root], execution_platform="wsl")
        machines["example-host"]["physical_host"] = "foreign-host"
        (root / "machines.yaml").write_text(yaml.safe_dump({"machines": machines}), encoding="utf-8")
        with pytest.raises(ManifestError, match="conflicts"):
            identity.resolve_machine(topology_repos=[root])


@pytest.mark.parametrize("platform,alias", CELLS)
def test_discovery_alias_matrix(tmp_path, monkeypatch, platform, alias):
    root = tmp_path / "example"
    write_package(root, "shared.yaml", base_package(name="example/shared", gate=["*"]))
    (root / "machines.yaml").write_text(yaml.safe_dump({"machines": {
        "Example-Host": {
            "hostname": "Generated-Host", "alias": "Legacy-Alias",
            "display_name": "Example Workstation", "ssh": {"environments": [
                {"name": "windows", "alias": "native-profile"},
                {"name": "wsl", "alias": "guest-profile"},
            ]},
        },
    }}), encoding="utf-8")
    monkeypatch.setattr(identity.platform, "node", lambda: alias)
    monkeypatch.setattr(identity, "detect_platform", lambda: platform)
    monkeypatch.setattr(discover, "current_platform", lambda: platform)
    monkeypatch.setattr(discover, "user_package_root", lambda *_args, **_kwargs: tmp_path / "absent")
    selected = identity.resolve_machine(topology_repos=[root])
    expected = "Example-Host-wsl" if platform == "wsl" else "Example-Host"
    assert selected.canonical == expected
    assert selected.raw == alias
    registry = {
        "schema_version": 1,
        "srcroot": {platform: str(tmp_path) for platform in ("windows", "wsl", "linux")},
        "repos": {"example": {"class": "worktree"}},
    }
    projects = {"schema_version": 2, "projects": {"example": {"config_dir": str(tmp_path / "state")}}}
    result = discover.discover(registry=registry, projects=projects)
    assert [package.name for repo in result for package in repo.packages] == ["example/shared"]
