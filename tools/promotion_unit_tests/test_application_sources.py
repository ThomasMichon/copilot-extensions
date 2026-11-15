"""Actor-free controls for explicit associated application inputs."""
from __future__ import annotations

import ast
from pathlib import Path
import sys
import time
from typing import Any, Callable, cast

import pytest
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs" / "installer-engine"))

from application_sources import (
    application_sources, canonical_application, declared_projects, is_application, project_locations,
)
from installer_wheel_inputs import _check_deadline
from runtime_profile import ProfileError, _requirement


@pytest.fixture(autouse=True)
def marker_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "packaging.markers.default_environment",
        lambda: {"extra": "", "python_full_version": "3.12.0"},
    )


@pytest.fixture(scope="module")
def registry_roots() -> Callable[..., list[str]]:
    source = ROOT / "tools" / "local_install_profile.py"
    tree = ast.parse(source.read_bytes(), filename=str(source))
    selected: list[ast.stmt] = [
        node for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        or isinstance(node, ast.FunctionDef) and node.name == "registry_roots"
    ]
    assert len(selected) == 2
    namespace: dict[str, Any] = {
        "__name__": "unit_application_sources", "_check_deadline": _check_deadline,
        "_requirement": _requirement, "canonicalize_name": canonicalize_name,
        "ProfileError": ProfileError,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)
    return cast(Callable[..., list[str]], namespace["registry_roots"])


@pytest.mark.parametrize(("selected", "owner"), [
    (False, None),
    (True, "standalone:fleet-manager"),
    (True, None),
    (True, "different"),
], ids=["default", "selected", "missing", "different"])
def test_declared_application_is_required_only_when_selected(
    registry_roots: Callable[..., list[str]], selected: bool, owner: str | None,
) -> None:
    sources = application_sources({"tool": {"copilot-extensions": {
        "application-sources": {"sdk": "standalone:fleet-manager"},
    }}})
    wheels: dict[str, dict[str, Any]] = {"consumer": {
        "version": "1", "provides_extras": ["feature"],
        "requires_dist": ['sdk>=1; extra == "feature"'],
        "application_sources": sources,
    }}
    if owner is not None:
        wheels["sdk"] = {
            "version": "1", "provides_extras": [],
            "requires_dist": ["external>=1"], "source_project": owner,
        }
    root = "consumer[feature]==1" if selected else "consumer==1"
    if selected and owner != "standalone:fleet-manager":
        with pytest.raises(ProfileError, match="selected associated application"):
            registry_roots(wheels, root, {}, deadline=time.monotonic() + 5)
    else:
        assert registry_roots(
            wheels, root, {}, deadline=time.monotonic() + 5,
        ) == (["external>=1"] if selected else [])


@pytest.mark.parametrize("sources", [
    False,
    {"sdk": "../outside"},
    {"some-sdk": "plugin", "SOME_SDK": "plugin"},
], ids=["table", "selector", "collision"])
def test_application_source_declaration_refuses_invalid_or_ambiguous_values(sources: object) -> None:
    with pytest.raises(ValueError):
        application_sources({"tool": {"copilot-extensions": {
            "application-sources": sources,
        }}})


def test_undeclared_application_name_is_not_a_source_exemption(
    registry_roots: Callable[..., list[str]],
) -> None:
    assert registry_roots({
        "consumer": {
            "version": "1", "provides_extras": [], "requires_dist": ["sdk>=1"],
            "application_sources": application_sources({}),
        },
    }, "consumer==1", {}, deadline=time.monotonic() + 5) == ["sdk>=1"]


def test_repository_declares_independent_projects_without_scanning_their_marketplace(
    tmp_path: Path,
) -> None:
    from promotion_python_artifacts import candidate_projects

    (tmp_path / "pyproject.toml").write_text(
        '[tool.copilot-extensions.python-projects]\n'
        'first = ".marketplace/first"\nsecond = "applications/second"\n',
    )
    for relative, version in (
        (".marketplace/first", "1.0"), ("applications/second", "2.0"),
        (".marketplace/unselected", "3.0"), ("plugins/ordinary", "4.0"),
    ):
        directory = tmp_path / relative
        directory.mkdir(parents=True)
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "{directory.name}"\nversion = "{version}"\n',
        )
    first, second = tmp_path / ".marketplace/first", tmp_path / "applications/second"
    assert candidate_projects(tmp_path) == {"first": "1.0", "second": "2.0", "ordinary": "4.0"}
    assert canonical_application(tmp_path, "first") == first
    assert canonical_application(tmp_path, "second") == second
    assert is_application(tmp_path, first) and is_application(tmp_path, second)
    assert not is_application(tmp_path, tmp_path / ".marketplace/unselected")
    for relative in ("libs/example", "applications/second/libs/example"):
        (tmp_path / "pyproject.toml").write_text(
            f'[tool.copilot-extensions.python-projects]\nexample = "{relative}"\n',
        )
        with pytest.raises(ValueError, match="cannot be a vendored library"):
            is_application(tmp_path, tmp_path / relative)


@pytest.mark.parametrize("locations", [
    False, {"standalone:example": "apps/example"},
    {"app": "../outside"}, {"app": "/outside"}, {"app": "C:/outside"},
    {"app": "apps\\example"}, {"app": "apps/./example"}, {"app": "apps//example"},
    {"app": ".git/example"}, {"app": "apps/example/"},
    {"first": "apps/example", "second": "apps/example"},
])
def test_project_declarations_refuse_unsafe_or_aliased_locations(locations: object) -> None:
    with pytest.raises(ValueError):
        project_locations({"tool": {"copilot-extensions": {"python-projects": locations}}})


@pytest.mark.parametrize("name,relative", [
    ("ordinary", "apps/replacement"),
    ("renamed", "plugins/ordinary"),
    ("worktree-manager", "apps/replacement"),
])
def test_project_declarations_cannot_replace_builtin_identity(
    tmp_path: Path, name: str, relative: str,
) -> None:
    directory = tmp_path / "plugins/ordinary"
    directory.mkdir(parents=True)
    (directory / "pyproject.toml").write_text('[project]\nname="ordinary"\nversion="1"\n')
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.copilot-extensions.python-projects]\n{name} = "{relative}"\n',
    )
    with pytest.raises(ValueError, match="collides"):
        declared_projects(tmp_path)


@pytest.mark.parametrize("declaration", [
    {"setup": [], "start": []},
    {"setup": ["prepare", "--existing-config"], "start": ["serve"]},
])
def test_project_owned_callbacks_use_declared_arguments_without_a_central_app_name(
    declaration: dict[str, list[str]],
) -> None:
    from runtime_application_project import install_callbacks

    metadata = {"tool": {"copilot-extensions": {"application": declaration}}}
    assert install_callbacks("example-worker", metadata=metadata) == (
        declaration["setup"], declaration["start"],
    )
    assert install_callbacks("agent-worktrees") == (["__source-setup"], ["__source-start"])
    assert install_callbacks("example-worker") == (None, None)


@pytest.mark.parametrize("declaration", [
    False, {}, {"setup": []}, {"setup": [], "start": [], "unknown": []},
    {"setup": "prepare", "start": []}, {"setup": [], "start": [False]},
    {"setup": [], "start": ["serve\0"]},
])
def test_project_owned_callbacks_refuse_incomplete_or_nonargument_values(declaration: object) -> None:
    from runtime_application_project import install_callbacks

    with pytest.raises(ValueError, match="requires setup and start"):
        install_callbacks("example-worker", metadata={
            "tool": {"copilot-extensions": {"application": declaration}},
        })


@pytest.mark.parametrize("budgets", [
    None, [], False, {"other": 60}, {"start": False}, {"start": "600"},
    {"start": 0}, {"setup": -1}, {"start": float("inf")},
    {"start": float("nan")}, {"setup": 3601}, {"start": 10 ** 400},
])
def test_project_callback_budgets_refuse_invalid_values_before_emission(budgets: object) -> None:
    from runtime_application_project import install_callbacks

    with pytest.raises(ValueError, match="callback timeouts"):
        install_callbacks("example-worker", metadata={
            "tool": {"copilot-extensions": {"application": {
                "setup": ["prepare"], "start": ["serve"],
                "callback_timeout_seconds": budgets,
            }}},
        })


def test_project_callback_budgets_preserve_declared_arguments() -> None:
    from runtime_application_project import install_callbacks

    assert install_callbacks("example-worker", metadata={
        "tool": {"copilot-extensions": {"application": {
            "setup": ["prepare"], "start": ["serve"],
            "callback_timeout_seconds": {"start": 600},
        }}},
    }) == (["prepare"], ["serve"])
