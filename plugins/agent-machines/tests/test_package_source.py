"""``package.source``: install a uv-tool/pipx package from a path or git URL."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from agent_machines import resources as R
from agent_machines.manifest import ManifestError, RequirementPackage, load_package
from agent_machines.resources import ResourceContext, RunOutcome, apply_resources

from ._helpers import base_package, write_package

GIT = {"git": "https://example.com/tools.git", "ref": "v1"}


def _pkg(tmp_path: Path, name: str, **decl: Any) -> RequirementPackage:
    resource = {"type": "package", "id": "acme-tool", "manager": "uv-tool", **decl}
    data = base_package(name=name, resources=[resource])
    path = write_package(tmp_path / name.replace("/", "_"), "pkg.yaml", data)
    return load_package(path, source_repo=name.split("/")[0])


def _apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pkg: RequirementPackage, listed: str = ""
) -> tuple[list[str], list[list[str]]]:
    monkeypatch.setattr(shutil, "which", lambda b: f"C:/{b}.exe")
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> RunOutcome:
        calls.append(argv)
        return RunOutcome(0, listed if argv[-1] == "list" else "", "")

    ctx = ResourceContext(home=tmp_path, repo_paths={}, platform="windows", runner=runner)
    results = apply_resources([pkg], "box-1", "windows", ctx, dry_run=False)
    return [r.action for r in results], calls


def test_relative_path_source_is_anchored_at_the_declaring_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _pkg(tmp_path, "acme/a", source={"path": "tool"})
    actions, calls = _apply(tmp_path, monkeypatch, pkg)

    assert pkg.source_path is not None
    expected = str((pkg.source_path.parent / "tool").resolve())
    assert actions == ["install"]
    assert calls[-1] == ["uv", "tool", "install", "--force", expected]


@pytest.mark.parametrize(
    ("manager", "source", "expected"),
    [
        ("pipx", GIT, ["pipx", "install", "--force", "git+https://example.com/tools.git@v1"]),
        (
            "uv-tool",
            {"git": "git+https://example.com/tools.git"},
            ["uv", "tool", "install", "--force", "git+https://example.com/tools.git"],
        ),
    ],
)
def test_git_source_installs_url_with_optional_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager: str,
    source: dict[str, str],
    expected: list[str],
) -> None:
    pkg = _pkg(tmp_path, "acme/a", manager=manager, source=source)
    actions, calls = _apply(tmp_path, monkeypatch, pkg)
    assert actions == ["install"] and calls[-1] == expected


def test_version_mismatch_reinstalls_from_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _pkg(tmp_path, "acme/a", version="2.0.0", source=GIT)
    actions, calls = _apply(tmp_path, monkeypatch, pkg, listed="acme-tool v1.0.0\n")

    assert actions == ["update"]
    assert calls[-1] == ["uv", "tool", "install", "--force", "git+https://example.com/tools.git@v1"]


def test_conflicting_sources_across_packages_are_an_error(tmp_path: Path) -> None:
    packages = [
        _pkg(tmp_path, "acme/a", source=GIT),
        _pkg(tmp_path, "acme/b", source={"git": "https://example.com/tools.git", "ref": "v2"}),
    ]
    _, findings = R.resolve_resources(packages, "box-1", "windows")
    assert [(f.level, f.code) for f in findings] == [("error", "resource-conflict")]


@pytest.mark.parametrize(
    ("manager", "source"),
    [
        ("winget", {"path": "C:/tool"}),
        ("uv-tool", {"path": "a", "git": "https://example.com/x.git"}),
        ("uv-tool", {"ref": "v1"}),
        ("uv-tool", {"git": "--upload-pack=x"}),
        ("uv-tool", {"git": "https://example.com/x.git", "ref": "v1@evil"}),
        ("uv-tool", {"path": "a", "branch": "main"}),
    ],
)
def test_invalid_package_source_is_rejected(
    tmp_path: Path, manager: str, source: dict[str, str]
) -> None:
    with pytest.raises(ManifestError):
        _pkg(tmp_path, "acme/a", manager=manager, source=source)
