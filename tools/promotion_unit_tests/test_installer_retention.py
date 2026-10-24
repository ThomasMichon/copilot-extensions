"""Retained-input work bounds; native construction stays in the development suite."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from subprocess import CompletedProcess
import tempfile

import pytest


NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)
SETTINGS = """PipInstallSettings {
    settings: PipSettings {
        index_locations: IndexLocations { indexes: [Index {
 exclude_newer: None,
 }], flat_index: [], },
        exclude_newer: ExcludeNewer {
            global: None,
            package: ExcludeNewerPackage({}),
        },
    },
}"""


def source_project(path, name="fixture-source"):
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(
        '[build-system]\nrequires=[]\nbuild-backend="fixture_backend"\nbackend-path=["."]\n'
        f'[project]\nname="{name}"\nversion="1.0"\ndependencies=[]\n',
        encoding="utf-8",
    )
    (path / "fixture_backend.py").write_text("synthetic retained source\n", encoding="utf-8")
    return path


@pytest.fixture
def retention_policy(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "libs" / "installer-engine" / "installer_dependency_policy.py"
    spec = importlib.util.spec_from_file_location("retention_unit_policy", path)
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)

    @contextmanager
    def temporary_inputs(_parent):
        with tempfile.TemporaryDirectory(prefix="retained-", dir=tmp_path) as directory:
            yield directory

    def private_root(parent):
        root = parent / "inputs"
        root.mkdir()
        return root

    monkeypatch.setattr(policy, "_temporary_inputs", temporary_inputs)
    monkeypatch.setattr(policy, "_private_retained_root", private_root)
    monkeypatch.setattr(policy, "_native_retained_path", str)
    monkeypatch.setattr(policy._configuration, "native_retained_path", str)
    monkeypatch.setattr(policy, "_select_uv_executable", lambda value: value)
    monkeypatch.setattr(
        policy.subprocess, "run",
        lambda command, **_kwargs: CompletedProcess(command, 0, SETTINGS, ""),
    )
    return policy


def test_retained_overlapping_projects_compare_each_file_pair_once(tmp_path, monkeypatch, retention_policy):
    policy = retention_policy
    parent = source_project(tmp_path / "parent", name="fixture-parent")
    child = source_project(parent / "child", name="fixture-child")
    read_bytes = Path.read_bytes
    reads = {}

    def observe(path):
        if path.name == "fixture_backend.py":
            key = Path(str(path).removeprefix("\\\\?\\"))
            reads[key] = reads.get(key, 0) + 1
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", observe)
    with policy.retain_uv_install(
        "attributed-uv", ["--no-config", str(parent), str(child)],
        env={}, cwd=tmp_path, now=NOW, trusted_source_roots=(parent, child),
    ) as transaction:
        for project in (parent, child):
            original = project / "fixture_backend.py"
            retained = transaction.sources[project] / original.name
            assert reads[original] == 1
            assert reads[retained] == 1
            assert transaction.source_hashes[original] == hashlib.sha256(read_bytes(original)).hexdigest()


def test_retained_metadata_remapping_work_is_independent_of_scalar_count(tmp_path, monkeypatch, retention_policy):
    policy = retention_policy
    layout = policy._configuration.retained_source_layout

    def count_mirrors(scalars):
        source = source_project(tmp_path / f"source-{scalars}")
        manifest = source / "pyproject.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + "keywords=" + json.dumps([f"keyword-{index}" for index in range(scalars)]) + "\n",
            encoding="utf-8",
        )
        calls = []

        def observe(*args, **kwargs):
            anchors, mirror = layout(*args, **kwargs)

            def counted(path):
                calls.append(path)
                return mirror(path)

            return anchors, counted

        with monkeypatch.context() as patch:
            patch.setattr(policy._configuration, "retained_source_layout", observe)
            with policy.retain_uv_install(
                "attributed-uv", ["--no-config", str(source)],
                env={}, cwd=tmp_path, now=NOW, trusted_source_roots=(source,),
            ) as transaction:
                assert transaction.source_hashes[source / "fixture_backend.py"]
        return len(calls)

    assert count_mirrors(64) == count_mirrors(1)


def test_retained_remapping_preserves_nested_native_uri_and_escaped_forms(tmp_path, retention_policy):
    policy = retention_policy
    parent = source_project(tmp_path / "parent", name="fixture-parent")
    child = source_project(parent / "child", name="fixture-child")
    forms = [
        str(child / "fixture_backend.py"),
        child.as_uri() + "/fixture_backend.py",
        child.as_posix() + "/fixture_backend.py",
        json.dumps(str(child))[1:-1],
    ]
    manifest = parent / "pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "\n[tool.fixture]\npaths=" + json.dumps([forms]) + "\n",
        encoding="utf-8",
    )
    with policy.retain_uv_install(
        "attributed-uv", ["--no-config", str(parent), str(child)],
        env={}, cwd=tmp_path, now=NOW, trusted_source_roots=(parent, child),
    ) as transaction:
        retained = transaction.sources[child]
        data = policy._toml(transaction.sources[parent] / "pyproject.toml")
        assert data["tool"]["fixture"]["paths"] == [[
            str(retained / "fixture_backend.py"),
            retained.as_uri() + "/fixture_backend.py",
            retained.as_posix() + "/fixture_backend.py",
            json.dumps(str(retained))[1:-1],
        ]]
