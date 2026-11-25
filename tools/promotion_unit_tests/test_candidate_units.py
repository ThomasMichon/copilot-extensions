"""Candidate admission uses composed bytes without broadening positive selection."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

import pytest

import installer_engine_ref
from promotion_candidate_units import candidate_manifest, derive_manifest
from promotion_unit_contract import CANDIDATE_PROFILE, COMPACT_PROFILE, PROFILE, UnitEvidenceError, admit_manifest, manifest_digest, verify_sources, windows_native_manifest

DEV, TREE = "a" * 40, "b" * 40
TEST = "tools/promotion_unit_tests/test_example.py"
TARGET = "services/chat/src/example/__init__.py"
NODE = TEST + "::test_example"
JsonObject = dict[str, Any]


def inputs() -> tuple[JsonObject, dict[str, bytes], dict[str, bytes], dict[str, str]]:
    development = {
        TEST: b"def test_example(): assert True\n",
        TARGET: b'__version__ = "0.1.0-dev1"\ndef value(): return 1\n',
    }
    template = {
        "profile": PROFILE, "nodes": [NODE], "coverage_targets": [TARGET],
        "sources": {name: hashlib.sha256(raw).hexdigest() for name, raw in development.items()},
        "max_seconds": 300,
    }
    candidate = {**development, TARGET: development[TARGET].replace(b"0.1.0-dev1", b"0.1.20-dev1")}
    return template, development, candidate, {name: name for name in development}


def derive(template: JsonObject, development: dict[str, bytes], candidate: dict[str, bytes], origins: dict[str, str]) -> JsonObject:
    return candidate_manifest(
        template, dev_sha=DEV, candidate_tree=TREE, development_sources=development,
        candidate_sources=candidate, origins=origins,
    )


def test_candidate_manifest_admits_actual_stamp_not_raw_source_relabeling() -> None:
    template, development, candidate, origins = inputs()
    manifest = derive(template, development, candidate, origins)
    assert manifest["profile"] == CANDIDATE_PROFILE
    assert manifest["nodes"] == template["nodes"] and manifest["max_seconds"] == 300
    assert manifest["sources"][TARGET] != template["sources"][TARGET]
    assert manifest["composition"]["source_manifest_sha256"] == manifest_digest(template)
    assert manifest["composition"]["development_sources"] == template["sources"]
    assert manifest["composition"]["candidate_tree"] == TREE
    with pytest.raises(UnitEvidenceError, match="development unit manifest"):
        windows_native_manifest(manifest, platform="win32")
    with pytest.raises(UnitEvidenceError, match="profile"):
        derive(windows_native_manifest(template, platform="win32"), development, candidate, origins)


def test_compact_template_keeps_candidate_selection_origins_and_digest() -> None:
    template, development, candidate, origins = inputs()
    expected = derive(template, development, candidate, origins)
    compact = {**template, "profile": COMPACT_PROFILE, "node_groups": [[TEST, ["test_example"]]]}
    del compact["nodes"]
    actual = derive(json.loads(json.dumps(compact)), development, candidate, origins)
    assert actual == expected
    assert actual["composition"]["source_manifest_sha256"] == manifest_digest(template)
    assert template["profile"] == PROFILE


@pytest.mark.parametrize("change", ["dev-bytes", "missing-source", "extra-source", "wrong-origin", "bad-copy"])
def test_candidate_manifest_refuses_unadmitted_closure(change: str) -> None:
    template, development, candidate, origins = inputs()
    if change == "dev-bytes":
        development[TARGET] += b"# drift\n"
    elif change == "missing-source":
        del candidate[TARGET]
    elif change == "extra-source":
        candidate["tools/extra.py"] = b"pass\n"
    elif change == "wrong-origin":
        origins[TARGET] = TEST
    else:
        candidate["plugins/example/libs/example.py"] = b"not a copy\n"
        origins["plugins/example/libs/example.py"] = TARGET
    with pytest.raises(UnitEvidenceError):
        derive(template, development, candidate, origins)


@pytest.mark.parametrize("field", ["dev_sha", "candidate_tree", "source_manifest_sha256", "origins", "development_sources"])
def test_candidate_composition_fields_are_not_optional_or_self_declared(field: str) -> None:
    manifest = derive(*inputs())
    del manifest["composition"][field]
    with pytest.raises(UnitEvidenceError):
        admit_manifest(manifest)


def test_candidate_copy_keeps_origin_and_does_not_add_test_nodes() -> None:
    template, development, candidate, origins = inputs()
    copy_path = "plugins/example/libs/chat/src/example/__init__.py"
    candidate[copy_path] = candidate[TARGET]
    origins[copy_path] = TARGET
    manifest = derive(template, development, candidate, origins)
    assert manifest["coverage_targets"] == sorted([TARGET, copy_path])
    assert manifest["nodes"] == [NODE]
    assert manifest["composition"]["origins"][copy_path] == TARGET


def test_declared_materialized_vendor_copy_extends_only_the_admitted_closure(tmp_path: Path) -> None:
    development_root, candidate_root = tmp_path / "dev", tmp_path / "candidate"
    source = "libs/example/src/example.py"
    copy_path = "plugins/consumer/libs/example/src/example.py"
    data = {TEST: b"def test_example(): assert True\n", source: b"def value(): return 1\n"}
    template = {
        "profile": PROFILE, "nodes": [NODE], "coverage_targets": [source], "max_seconds": 300,
        "sources": {name: hashlib.sha256(raw).hexdigest() for name, raw in data.items()},
    }
    for root, path_spec in ((development_root, "../../libs/example"), (candidate_root, "libs/example")):
        for name, raw in data.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        project = root / "plugins/consumer/pyproject.toml"
        project.parent.mkdir(parents=True, exist_ok=True)
        project.write_text(f'[tool.uv.sources]\nexample = {{path = "{path_spec}"}}\n')
    path = candidate_root / copy_path
    path.parent.mkdir(parents=True)
    path.write_bytes(data[source])
    manifest, original, composed = derive_manifest(
        template, development_root=development_root, candidate_root=candidate_root,
        dev_sha=DEV, candidate_tree=TREE,
    )
    assert original == data and composed[copy_path] == data[source]
    assert manifest["composition"]["origins"][copy_path] == source
    assert manifest["nodes"] == [NODE]


def test_vendor_copies_outside_the_main_release_scope_are_not_required(tmp_path: Path) -> None:
    development_root, candidate_root = tmp_path / "dev", tmp_path / "candidate"
    source, library_test = "libs/example/src/example.py", "libs/example/tests/test_lib.py"
    copy_path = "plugins/consumer/libs/example/src/example.py"
    dropped = "plugins/consumer/libs/example/tests/test_lib.py"
    data = {TEST: b"def test_example(): assert True\n", source: b"def value(): return 1\n",
            library_test: b"def test_lib(): assert True\n"}
    template = {
        "profile": PROFILE, "nodes": [NODE], "coverage_targets": [source], "max_seconds": 300,
        "sources": {name: hashlib.sha256(raw).hexdigest() for name, raw in data.items()},
    }
    for root, path_spec in ((development_root, "../../libs/example"), (candidate_root, "libs/example")):
        for name, raw in data.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        project = root / "plugins/consumer/pyproject.toml"
        project.parent.mkdir(parents=True, exist_ok=True)
        project.write_text(f'[tool.uv.sources]\nexample = {{path = "{path_spec}"}}\n')
    (candidate_root / copy_path).parent.mkdir(parents=True)
    (candidate_root / copy_path).write_bytes(data[source])
    (development_root / "tools").mkdir(exist_ok=True)
    (development_root / "tools/promotion-release-scope.json").write_text(json.dumps({
        "schema": "copilot-extensions.promotion-release-scope/v1",
        "release_roots": ["libs", "plugins", "tools"], "excluded": {},
    }))
    (development_root / "tools/promotion-release-scope-patterns.json").write_text(json.dumps({
        "schema": "copilot-extensions.promotion-release-scope-patterns/v1",
        "excluded_patterns": {"plugins/*/libs/*/tests": "vendored library tests"},
    }))
    manifest, _original, composed = derive_manifest(
        template, development_root=development_root, candidate_root=candidate_root,
        dev_sha=DEV, candidate_tree=TREE,
    )
    assert manifest["composition"]["origins"][copy_path] == source
    assert dropped not in composed and dropped not in manifest["composition"]["origins"]
    (candidate_root / copy_path).unlink()
    with pytest.raises(UnitEvidenceError, match="missing candidate source"):
        derive_manifest(
            template, development_root=development_root, candidate_root=candidate_root,
            dev_sha=DEV, candidate_tree=TREE,
        )


def test_candidate_selection_cannot_change_the_original_budget() -> None:
    template, development, candidate, origins = inputs()
    before = copy.deepcopy(template)
    derive(template, development, candidate, origins)
    assert template == before
    template["max_seconds"] = 301
    with pytest.raises(UnitEvidenceError):
        derive(template, development, candidate, origins)


@pytest.mark.parametrize("damage", ["none", "changed-copy", "missing-copy", "retargeted-installer"])
def test_declared_native_engine_data_copies_are_bound_before_execution(
    tmp_path: Path, damage: str,
) -> None:
    development_root, candidate_root = tmp_path / "dev", tmp_path / "candidate"
    template, development, candidate, _ = inputs()
    copies = {}
    for ext in ("sh", "ps1"):
        origin = "libs/installer-engine/installer-engine." + ext
        development[origin] = candidate[origin] = b"# inert canonical writer text\n"
        for product in ("agent-vault", "agent-ssh", "agent-pull-requests"):
            script = f"plugins/{product}/scripts/install.{ext}"
            development[script] = (installer_engine_ref.canonical_line(ext) + "\n").encode()
            candidate[script] = (installer_engine_ref.local_line(ext) + "\n").encode()
            copy_path = f"plugins/{product}/scripts/installer-engine.{ext}"
            candidate[copy_path] = candidate[origin]
            copies[copy_path] = origin
    template["sources"] = {
        name: hashlib.sha256(raw).hexdigest() for name, raw in development.items()
    }
    changed = "plugins/agent-vault/scripts/installer-engine.ps1"
    if damage == "changed-copy":
        candidate[changed] += b"# changed\n"
    elif damage == "missing-copy":
        del candidate[changed]
    elif damage == "retargeted-installer":
        candidate["plugins/agent-vault/scripts/install.ps1"] = b". './foreign.ps1'\n"
    for root, values in ((development_root, development), (candidate_root, candidate)):
        for name, raw in values.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    if damage != "none":
        with pytest.raises(UnitEvidenceError, match="canonical origin|missing candidate source|materialization route"):
            derive_manifest(
                template, development_root=development_root, candidate_root=candidate_root,
                dev_sha=DEV, candidate_tree=TREE,
            )
        return
    manifest, original, composed = derive_manifest(
        template, development_root=development_root, candidate_root=candidate_root,
        dev_sha=DEV, candidate_tree=TREE,
    )
    assert original == development and composed == candidate
    assert set(manifest["sources"]) - set(template["sources"]) == set(copies)
    assert {name: manifest["composition"]["origins"][name] for name in copies} == copies
    assert manifest["nodes"] == template["nodes"]
    assert manifest["coverage_targets"] == template["coverage_targets"]
    verify_sources(candidate_root, manifest["sources"])
    spec = importlib.util.spec_from_file_location(
        "candidate_native_command_reader",
        Path(__file__).resolve().parents[2] / "libs/installer-engine/installer_engine_commands.py",
    )
    assert spec is not None and spec.loader is not None
    reader = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = reader
    spec.loader.exec_module(reader)
    for name in copies:
        payload = candidate_root / name.rpartition("/scripts/")[0]
        assert reader._engine(payload, name.rpartition(".")[2]) == candidate[name].decode()
    (candidate_root / changed).write_bytes(candidate[changed] + b"# later drift\n")
    with pytest.raises(UnitEvidenceError, match="changed without admission"):
        verify_sources(candidate_root, manifest["sources"])
