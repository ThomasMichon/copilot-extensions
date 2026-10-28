"""Core composition decisions without a resolver, build backend or publisher."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import build_python_artifacts as builder
import core_python_artifacts as core
import promotion_python_artifacts as artifacts
import pytest
import runtime_profile_release as release
from python_project_inventory import runtime_roots
from standalone_python_artifacts import artifact_label, project_key


def _source(root):
    for relative, name, dependencies in (
        ("plugins/sample", "sample", ["helper>=1; sys_platform == 'linux'"]),
        ("libs/helper", "helper", []),
    ):
        directory = root / relative
        directory.mkdir(parents=True)
        (directory / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n'
            f'[project]\nname = "{name}"\nversion = "1.0"\n'
            f"dependencies = {json.dumps(dependencies)}\n",
        )
    path = root / ".github" / "plugin" / "marketplace.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"metadata":{"version":"2.0-dev7"}}')


def test_core_identity_is_not_a_plugin_or_standalone_alias():
    assert artifact_label({"composition": "core"}) == "composition-core"
    assert project_key({"composition": "core"}) == "composition:core"
    for record in (
        {"composition": "unknown"},
        {"composition": "core", "plugin": "sample"},
        {"composition": "core", "component": "pr-service"},
    ):
        with pytest.raises(ValueError):
            artifact_label(record)


def test_shared_inventory_preserves_root_extras_and_heavy_environment_separation():
    root = Path("/candidate")
    catalog = {
        "agent-dispatch": (root / "plugins" / "agent-dispatch" / "pyproject.toml", {}),
        "agent-containers": (root / "plugins" / "agent-containers", {}),
        "index-service": (root / "services" / "index", {}),
        "index-engine": (root / "plugins" / "agent-index" / "server", {}),
        "helper": (root / "libs" / "helper", {}),
    }
    groups = runtime_roots(root, catalog)
    assert {name for name, _extra, _condition in groups["core"]} == {
        "agent-dispatch",
        "agent-containers",
    }
    assert (
        "agent-dispatch",
        "mcp",
        frozenset(
            {
                'sys_platform != "win32" or platform_machine != "ARM64"',
            }
        ),
    ) in groups["core"]
    assert groups["index-service"] == [
        ("index-service", "", frozenset()),
        ("index-service", "native", frozenset()),
    ]
    assert groups["index-engine"] == [("index-engine", "", frozenset())]


def test_core_closure_retains_foreign_target_edges_and_hashes_its_selection(
    tmp_path, monkeypatch
):
    _source(tmp_path)
    monkeypatch.setattr(builder, "REPO", tmp_path)
    closure = core.source_closure(tmp_path, builder)
    assert closure.version == "2.0-dev7"
    assert [item["name"] for item in closure.selection["projects"]] == [
        "helper",
        "sample",
    ]
    assert closure.selection["edges"] == [
        {
            "name": "sample",
            "extra": "",
            "requirement": 'helper>=1; sys_platform == "linux"',
        }
    ]
    changed = copy.deepcopy(closure.selection)
    changed["roots"] = []
    assert core.payload_hash(builder, closure) != core.payload_hash(
        builder, closure, changed
    )


@pytest.mark.parametrize("fault", ["duplicate", "version", "dynamic", "foreign-source"])
def test_core_refuses_ambiguous_or_unattributed_source_closure(tmp_path, fault):
    _source(tmp_path)
    path = tmp_path / "libs" / "helper" / "pyproject.toml"
    if fault == "duplicate":
        extra = tmp_path / "libs" / "duplicate"
        extra.mkdir()
        (extra / "pyproject.toml").write_bytes(path.read_bytes())
    elif fault == "version":
        path.write_text(path.read_text().replace('version = "1.0"', 'version = "0.5"'))
    elif fault == "dynamic":
        path.write_text(
            path.read_text().replace(
                "[project]", '[project]\ndynamic = ["dependencies"]'
            )
        )
    else:
        plugin = tmp_path / "plugins" / "sample" / "pyproject.toml"
        plugin.write_text(
            plugin.read_text() + '[tool.uv.sources]\nhelper={path="../../../foreign"}\n'
        )
    with pytest.raises(artifacts.PromotionArtifactError):
        core.source_closure(tmp_path, builder)


@pytest.mark.parametrize(
    "value",
    [
        "[]",
        "invalid",
        '{"schema":"copilot-extensions.core-composition","version":true}',
    ],
    ids=["non-object", "malformed", "bool-version"],
)
def test_core_marker_requires_an_explicit_supported_record(tmp_path, value):
    marker = tmp_path / core.SELECTION_PATH
    marker.parent.mkdir()
    marker.write_text(value)
    with pytest.raises(artifacts.PromotionArtifactError):
        core.enabled(tmp_path)


def test_core_builder_uses_the_shared_locked_set_owner(tmp_path, monkeypatch):
    _source(tmp_path)
    monkeypatch.setattr(builder, "REPO", tmp_path)
    calls = []

    def build(**kwargs):
        calls.append(kwargs)
        return kwargs

    monkeypatch.setattr(builder, "build_project_artifacts", build)
    lock = object()
    result = core.build_core_artifacts(
        out_dir=tmp_path / "out", toolchain=lock, builder=builder
    )
    assert result["identity"] == {"composition": "core"}
    assert result["schema_version"] == 5 and result["toolchain"] is lock
    assert len(result["projects"]) == len(set(result["projects"])) == 2
    assert len(calls) == 1


@pytest.mark.parametrize(
    "core_manifest", [False, True], ids=["legacy-optional", "core-required"]
)
def test_only_complete_core_bindings_require_every_shared_library(
    monkeypatch, core_manifest
):
    digest, identity = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    manifest = {
        **({"composition": "core"} if core_manifest else {"plugin": "sample"}),
        "version": "1.0",
        "payload_hash": "sha256:" + "c" * 64,
        "wheels": [
            {
                "name": "helper",
                "role": "vendored-lib",
                "filename": "helper.whl",
                "sha256": digest,
            }
        ],
    }
    record = {
        "artifact": {"artifact_id": identity, "manifest_sha256": digest},
        "release": {
            "identity": f"{artifact_label(manifest)}:1.0",
            "sha256": manifest["payload_hash"],
        },
        "component": artifact_label(manifest),
        "wheels": [],
    }
    monkeypatch.setattr(
        release,
        "release_profiles",
        lambda index: (SimpleNamespace(record=lambda: record),),
    )
    if core_manifest:
        with pytest.raises(release.ProfileError, match="omits"):
            release.validate_release_bindings({}, {identity: (manifest, digest)})
    else:
        release.validate_release_bindings({}, {identity: (manifest, digest)})


@pytest.mark.parametrize(
    "requirement,accepted",
    [
        ("helper", True),
        ("helper>=0.1.0.dev1", True),
        ("helper>=1.0", False),
    ],
    ids=["unbounded", "declared-dev-floor", "incompatible-floor"],
)
def test_development_versions_use_explicit_prerelease_admission(
    tmp_path, monkeypatch, requirement, accepted
):
    from packaging.specifiers import SpecifierSet

    _source(tmp_path)
    path = tmp_path / "libs" / "helper" / "pyproject.toml"
    path.write_text(
        path.read_text().replace('version = "1.0"', 'version = "0.1.0.dev7"')
    )
    path = tmp_path / "plugins" / "sample" / "pyproject.toml"
    path.write_text(
        path.read_text().replace("helper>=1; sys_platform == 'linux'", requirement)
    )

    def implicit(*args):
        raise AssertionError(
            "candidate versions must use explicit prerelease admission"
        )

    monkeypatch.setattr(SpecifierSet, "__contains__", implicit)
    if accepted:
        assert len(core.source_closure(tmp_path, builder).projects) == 2
    else:
        with pytest.raises(artifacts.PromotionArtifactError, match="incompatible"):
            core.source_closure(tmp_path, builder)


@pytest.mark.parametrize(
    "selector", [None, "/selected/python"], ids=["uv-default", "explicit"]
)
def test_core_cli_selects_only_inside_the_resolved_toolchain(
    tmp_path, monkeypatch, selector
):
    import sys

    import artifact_builder_cli as cli

    lock = SimpleNamespace(
        venv_python=tmp_path / "toolchain" / "bin" / "python",
        record={"locked": True, "provenance": {"python": "/uv-default/python"}},
        marker_environment={"python_version": "3.11"},
    )
    monkeypatch.setattr(cli, "resolve_toolchain_lock", lambda *args, **kwargs: lock)
    arguments = [
        "builder",
        "core",
        "--core",
        "--out-dir",
        str(tmp_path / "out"),
        "--toolchain-venv",
        str(tmp_path / "toolchain"),
    ]
    if selector is not None:
        arguments.extend(["--python", selector])
    monkeypatch.setattr(sys, "argv", arguments)
    monkeypatch.setattr(sys, "executable", "/calling/python")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "composition": "core",
                    "build_toolchain": lock.record,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(
        cli, "subprocess", SimpleNamespace(run=run, CREATE_NO_WINDOW=0), raising=False
    )

    def ambient(**kwargs):
        raise AssertionError(
            "the calling interpreter must not select core dependencies"
        )

    assert cli.main(None, "", core_builder=ambient) == 0
    argv, options = calls[0]
    assert argv[0] == str(lock.venv_python)
    assert argv[argv.index("--python") + 1] == (selector or "/uv-default/python")
    assert argv[argv.index("--toolchain-venv") + 1] == str(tmp_path / "toolchain")
    assert options["capture_output"] and options["check"] is False


@pytest.mark.parametrize(
    "already_locked", [False, True], ids=["reentry", "already-locked"]
)
def test_core_cli_refuses_unsupported_build_python_before_execution(
    tmp_path,
    monkeypatch,
    capsys,
    already_locked,
):
    import sys

    import artifact_builder_cli as cli

    venv = tmp_path / "toolchain"
    lock = SimpleNamespace(
        venv_python=venv / "bin" / "python",
        marker_environment={"python_version": "3.10"},
    )
    monkeypatch.setattr(cli, "resolve_toolchain_lock", lambda *args, **kwargs: lock)
    monkeypatch.setattr(
        sys, "prefix", str(venv if already_locked else tmp_path / "caller")
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "builder",
            "core",
            "--core",
            "--out-dir",
            str(tmp_path / "out"),
            "--toolchain-venv",
            str(venv),
        ],
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("an unsupported core build interpreter must not execute")

    monkeypatch.setattr(cli, "_core_in_toolchain", forbidden)
    assert cli.main(None, "", core_builder=forbidden) == 1
    assert "core construction requires Python 3.11 or newer" in capsys.readouterr().err


@pytest.mark.parametrize("conditional", [False, True], ids=["direct", "conditional"])
def test_missing_canonical_local_dependency_cannot_be_treated_as_external(
    tmp_path, conditional
):
    _source(tmp_path)
    plugin = tmp_path / "plugins" / "sample"
    local = plugin / "libs" / "local"
    local.mkdir(parents=True)
    (local / "pyproject.toml").write_text(
        '[project]\nname="local-helper"\nversion="1.0"\n'
    )
    mapping = '{path="libs/local"}'
    if conditional:
        mapping = '[{path="libs/local",marker="sys_platform == \'linux\'"}]'
    manifest = plugin / "pyproject.toml"
    manifest.write_text(
        manifest.read_text().replace(
            "helper>=1; sys_platform == 'linux'",
            "local-helper",
        )
        + "\n[tool.uv.sources]\nlocal-helper="
        + mapping
        + "\n"
    )
    with pytest.raises(artifacts.PromotionArtifactError, match="canonical"):
        core.source_closure(tmp_path, builder)


@pytest.mark.parametrize("conditional", [False, True], ids=["direct", "conditional"])
def test_external_wheel_source_declarations_remain_data_not_first_party_sources(
    tmp_path, conditional
):
    _source(tmp_path)
    mapping = '{url="https://example.invalid/qualified_helper-1.0-py3-none-any.whl"}'
    if conditional:
        mapping = "[" + mapping[:-1] + ",marker=\"sys_platform == 'linux'\"}]"
    manifest = tmp_path / "plugins" / "sample" / "pyproject.toml"
    manifest.write_text(
        manifest.read_text().replace(
            "helper>=1; sys_platform == 'linux'",
            "qualified-helper",
        )
        + "\n[tool.uv.sources]\nqualified-helper="
        + mapping
        + "\n"
    )
    closure = core.source_closure(tmp_path, builder)
    assert [item["name"] for item in closure.selection["projects"]] == ["sample"]
    assert closure.selection["edges"] == [
        {
            "name": "sample",
            "extra": "",
            "requirement": "qualified-helper",
        }
    ]
    assert "example.invalid" in manifest.read_text()


@pytest.mark.parametrize(
    "extra",
    ["service-gateway", "service_gateway", "Service-Gateway"],
    ids=["canonical", "underscore", "mixed-case"],
)
def test_first_party_extras_match_canonical_declarations(tmp_path, extra):
    _source(tmp_path)
    manifest = tmp_path / "libs" / "helper" / "pyproject.toml"
    manifest.write_text(
        manifest.read_text() + "\n[project.optional-dependencies]\nservice-gateway=[]\n"
    )
    manifest = tmp_path / "plugins" / "sample" / "pyproject.toml"
    manifest.write_text(
        manifest.read_text().replace(
            "helper>=1; sys_platform == 'linux'",
            f"helper[{extra}]>=1",
        )
    )
    closure = core.source_closure(tmp_path, builder)
    assert {item["name"] for item in closure.selection["projects"]} == {
        "helper",
        "sample",
    }


@pytest.mark.parametrize("admitted", [False, True], ids=["refused", "admitted"])
def test_local_wheel_exception_stays_with_its_existing_owner(
    tmp_path, monkeypatch, admitted
):
    import seasoned_uv

    _source(tmp_path)
    manifest = tmp_path / "plugins" / "sample" / "pyproject.toml"
    manifest.write_text(
        manifest.read_text().replace(
            "helper>=1; sys_platform == 'linux'", "external-wheel"
        )
        + '\n[tool.uv.sources]\nexternal-wheel={path="vendor/external.whl"}\n'
    )
    calls = []

    def qualified(project, dependency, path):
        calls.append((project, dependency, path))
        return admitted

    monkeypatch.setattr(seasoned_uv, "_bundled_dbus_next", qualified)
    if admitted:
        closure = core.source_closure(tmp_path, builder)
        assert [item["name"] for item in closure.selection["projects"]] == ["sample"]
    else:
        with pytest.raises(artifacts.PromotionArtifactError, match="canonical"):
            core.source_closure(tmp_path, builder)
    assert calls == [
        ("sample", "external-wheel", manifest.parent / "vendor" / "external.whl")
    ]
