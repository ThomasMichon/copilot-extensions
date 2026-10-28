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


def test_core_cli_selects_only_inside_the_resolved_toolchain(tmp_path, monkeypatch):
    import sys

    import artifact_builder_cli as cli

    lock = SimpleNamespace(
        venv_python=tmp_path / "toolchain" / "bin" / "python", record={"locked": True}
    )
    monkeypatch.setattr(cli, "resolve_toolchain_lock", lambda *args, **kwargs: lock)
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
            str(tmp_path / "toolchain"),
            "--python",
            "/selected/python",
        ],
    )
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
    assert argv[argv.index("--python") + 1] == "/selected/python"
    assert argv[argv.index("--toolchain-venv") + 1] == str(tmp_path / "toolchain")
    assert options["capture_output"] and options["check"] is False
