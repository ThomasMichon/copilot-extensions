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
sys.path.insert(0, str(ROOT / "libs" / "versioned-runtime"))

from application_sources import (
    application_sources, canonical_application, declared_projects, is_application, project_locations,
)
from installer_wheel_inputs import _check_deadline
from runtime_profile import ProfileError, _requirement


@pytest.mark.parametrize(("selected", "present", "retained"), [
    (("worktree-manager",), True, ("apps/worktree-manager", "worktree-manager")),
    (("worktree-manager",), False, ("worktree-manager",)),
    (("plugins/other",), True, ("plugins/other",)),
], ids=["selected-output", "missing-output", "different-source"])
def test_local_source_application_exports_follow_the_selected_producer(
    monkeypatch: pytest.MonkeyPatch, selected: tuple[str, ...], present: bool,
    retained: tuple[str, ...],
) -> None:
    from materialize_source_applications import retain_outputs

    monkeypatch.setattr(Path, "is_dir", lambda _path: present)
    assert retain_outputs(Path("candidate"), selected) == retained


def test_legacy_promotion_does_not_query_source_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from materialize_source_applications import bind_promotion_source, promotion_outputs

    monkeypatch.setattr(Path, "is_file", lambda _path: False)

    def forbidden() -> str:
        raise AssertionError("an unselected source app must not query Git identity")

    assert bind_promotion_source(Path("candidate"), forbidden) == []
    assert promotion_outputs(Path("candidate")) == ()


@pytest.mark.parametrize("packages", [(), ("example_core",), ("example_core", "example_context")],
                         ids=["missing", "single", "multiple"])
def test_source_manifest_retains_notices_for_each_embedded_import_root(
    tmp_path: Path, packages: tuple[str, ...],
) -> None:
    from materialize_source_applications import _project_manifest
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib

    project = tmp_path / "application"
    library = project / "libs/example"
    (library / "src").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "application"\nversion = "1"\n'
        '[dependency-groups]\nfirst-party-core = ["example-core"]\ndev = ["pytest"]\n'
        '[tool.uv.sources]\nexample-core = {path = "libs/example", editable = false}\n'
        '[tool.setuptools.package-data]\napplication = ["data/*.toml"]\n',
        encoding="utf-8",
    )
    (library / "pyproject.toml").write_text(
        '[project]\nname = "example-core"\nversion = "1"\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
        + ('[tool.setuptools.package-data]\nexample_core = ["data/*.txt"]\n'
           if packages else ""),
        encoding="utf-8",
    )
    for package in packages:
        root = library / "src" / package
        root.mkdir()
        (root / "__init__.py").write_text("", encoding="utf-8")
    if not packages:
        with pytest.raises(ValueError, match="embedded application library"):
            _project_manifest(project)
        return
    metadata = tomllib.loads(_project_manifest(project))
    expected = {"application": ["data/*.toml"], "example_core": ["LICENSE", "data/*.txt"]}
    if "example_context" in packages:
        expected["example_context"] = ["LICENSE"]
    assert metadata["tool"]["setuptools"]["package-data"] == expected
    assert metadata["dependency-groups"] == {"dev": ["pytest"]}
    assert "sources" not in metadata["tool"].get("uv", {})


@pytest.mark.parametrize("package", ["1.2.3", "1.2.3+local"])
def test_pure_source_identity_roundtrip_preserves_version_and_payload_binding(package: str) -> None:
    from versioned_runtime_identity import (
        SourceIdentity, SourceIdentityError, parse_source_identity, runtime_version,
    )

    identity = SourceIdentity(
        "example", package, "a" * 40, "b" * 40, "c" * 64,
        runtime_version(package, "a" * 40, "c" * 64),
    )
    assert parse_source_identity(
        identity.document(), package_version=package, plugin="example",
    ) == identity
    with pytest.raises(SourceIdentityError, match="immutable inputs"):
        parse_source_identity({**identity.document(), "source_commit": "d" * 40})
    with pytest.raises(SourceIdentityError, match="plugin and package"):
        parse_source_identity(identity.document(), plugin="other")


def test_pure_source_fingerprint_preserves_framing_and_relocation(tmp_path: Path) -> None:
    import hashlib
    from versioned_runtime_fingerprint import fingerprint_source

    observed: list[tuple[Path, bytes]] = []
    roots = [tmp_path / "first", tmp_path / "relocated"]
    for root in roots:
        root.mkdir()
        (root / "a").write_bytes(b"first\0content")
        (root / "b").write_bytes(b"second")
    digest = fingerprint_source([roots[0]], on_file=lambda *entry: observed.append(entry))
    expected = hashlib.sha256()
    for label, content in ((b"./a", b"first\0content"), (b"./b", b"second")):
        for value in (label, b"file", content):
            expected.update(len(value).to_bytes(8, "big") + value)
    assert digest == expected.hexdigest()
    assert observed == [(roots[0] / "a", b"first\0content"), (roots[0] / "b", b"second")]
    assert fingerprint_source([roots[1]], on_file=lambda *_entry: None) == digest
    (roots[1] / "a").write_bytes(b"changed")
    assert fingerprint_source([roots[1]], on_file=lambda *_entry: None) != digest


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
    assert install_callbacks("agent-logger") == ([], [])
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
