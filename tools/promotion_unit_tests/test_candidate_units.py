"""Candidate admission uses composed bytes without broadening positive selection."""
from __future__ import annotations

import copy
import hashlib

import pytest

from promotion_candidate_units import CANDIDATE_PROFILE, candidate_manifest, derive_manifest
from promotion_unit_contract import PROFILE, UnitEvidenceError, admit_manifest, manifest_digest

DEV, TREE = "a" * 40, "b" * 40
TEST = "tools/promotion_unit_tests/test_example.py"
TARGET = "services/chat/src/example/__init__.py"
NODE = TEST + "::test_example"


def inputs():
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


def derive(template, development, candidate, origins):
    return candidate_manifest(
        template, dev_sha=DEV, candidate_tree=TREE, development_sources=development,
        candidate_sources=candidate, origins=origins,
    )


def test_candidate_manifest_admits_actual_stamp_not_raw_source_relabeling():
    template, development, candidate, origins = inputs()
    manifest = derive(template, development, candidate, origins)
    assert manifest["profile"] == CANDIDATE_PROFILE
    assert manifest["nodes"] == template["nodes"] and manifest["max_seconds"] == 300
    assert manifest["sources"][TARGET] != template["sources"][TARGET]
    assert manifest["composition"]["source_manifest_sha256"] == manifest_digest(template)
    assert manifest["composition"]["development_sources"] == template["sources"]
    assert manifest["composition"]["candidate_tree"] == TREE


@pytest.mark.parametrize("change", ["dev-bytes", "missing-source", "extra-source", "wrong-origin", "bad-copy"])
def test_candidate_manifest_refuses_unadmitted_closure(change):
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
def test_candidate_composition_fields_are_not_optional_or_self_declared(field):
    manifest = derive(*inputs())
    del manifest["composition"][field]
    with pytest.raises(UnitEvidenceError):
        admit_manifest(manifest)


def test_candidate_copy_keeps_origin_and_does_not_add_test_nodes():
    template, development, candidate, origins = inputs()
    copy_path = "plugins/example/libs/chat/src/example/__init__.py"
    candidate[copy_path] = candidate[TARGET]
    origins[copy_path] = TARGET
    manifest = derive(template, development, candidate, origins)
    assert manifest["coverage_targets"] == sorted([TARGET, copy_path])
    assert manifest["nodes"] == [NODE]
    assert manifest["composition"]["origins"][copy_path] == TARGET


def test_declared_materialized_vendor_copy_extends_only_the_admitted_closure(tmp_path):
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


def test_candidate_selection_cannot_change_the_original_budget():
    template, development, candidate, origins = inputs()
    before = copy.deepcopy(template)
    derive(template, development, candidate, origins)
    assert template == before
    template["max_seconds"] = 301
    with pytest.raises(UnitEvidenceError):
        derive(template, development, candidate, origins)
