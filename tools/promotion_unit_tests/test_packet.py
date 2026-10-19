"""Synthetic JSON-only unit-packet admission tests."""

from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

import promotion_unit_packet as packet
from promotion_unit_contract import PROFILE, UnitEvidenceError, manifest_digest

SOURCE = "a" * 40
TEST = "tools/promotion_unit_tests/test_example.py"
TARGET = "tools/example.py"
NODE = TEST + "::test_example"


def _write(path, data):
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    return raw


@pytest.fixture
def packet_case(tmp_path):
    directory, root = tmp_path / "packet", tmp_path / "source"
    directory.mkdir()
    sources = {}
    for name, content in ((TEST, b"def test_example(): pass\n"), (TARGET, b"VALUE = 1\n")):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        sources[name] = hashlib.sha256(content).hexdigest()
    manifest = dict(
        profile=PROFILE, nodes=[NODE], sources=sources, coverage_targets=[TARGET], max_seconds=300,
    )
    coverage = {
        "meta": {"version": "any-version"}, "totals": {},
        "files": {TARGET: {
            "executed_lines": [1], "summary": {}, "missing_lines": [], "excluded_lines": [],
            "contexts": {"1": ["", NODE + "|call"]}, "functions": {}, "classes": {},
        }},
    }
    coverage_raw = _write(directory / "coverage.json", coverage)
    proof = dict(
        profile=PROFILE, source_sha=SOURCE, manifest_sha256=manifest_digest(manifest),
        elapsed_seconds=0.125, collected=[NODE],
        reports=[dict(node=NODE, phase=phase, outcome="passed") for phase in ("setup", "call", "teardown")],
        effects=[], imported_sources=[TEST, TARGET], coverage_file="coverage.json",
        coverage_sha256=hashlib.sha256(coverage_raw).hexdigest(),
    )
    _write(directory / "unit-manifest.json", manifest)
    _write(directory / "unit-proof.json", proof)
    return directory, manifest, proof, coverage, root


def _verify(case, **kwargs):
    directory, manifest, _, _, _ = case
    return packet.verify_packet(
        directory, expected_source_sha=SOURCE, expected_manifest=manifest, **kwargs,
    )


def _coverage(case):
    directory, _, proof, coverage, _ = case
    proof["coverage_sha256"] = hashlib.sha256(_write(directory / "coverage.json", coverage)).hexdigest()
    _write(directory / "unit-proof.json", proof)


def test_packet_returns_frozen_keys_and_canonical_raw_byte_digests(packet_case):
    directory, manifest, proof, _, root = packet_case
    result = _verify(packet_case, source_root=root)
    assert set(result) == {
        "profile", "source_sha", "manifest_sha256", "manifest_file_sha256",
        "proof_sha256", "coverage_sha256", "packet_sha256", "manifest", "proof",
    }
    assert result["manifest"] == manifest and result["proof"] == proof
    assert result["manifest_sha256"] == manifest_digest(manifest)
    digests = {
        key: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for key, name in (
            ("manifest_file_sha256", "unit-manifest.json"),
            ("proof_sha256", "unit-proof.json"), ("coverage_sha256", "coverage.json"),
        )
    }
    assert result["packet_sha256"] == manifest_digest(digests)
    assert all(result[key] == value for key, value in digests.items())


def test_packet_manifest_whitespace_changes_raw_binding_not_trusted_identity(packet_case):
    directory, manifest, _, _, _ = packet_case
    original = _verify(packet_case)
    (directory / "unit-manifest.json").write_text(json.dumps(manifest, indent=2))
    changed = _verify(packet_case)
    assert changed["manifest_sha256"] == original["manifest_sha256"]
    assert changed["manifest_file_sha256"] != original["manifest_file_sha256"]
    assert changed["packet_sha256"] != original["packet_sha256"]


def test_packet_refuses_copied_self_declared_manifest_and_proof_digests(packet_case):
    directory, manifest, proof, _, _ = packet_case
    untrusted = copy.deepcopy(manifest)
    untrusted["max_seconds"] = 299
    proof["manifest_sha256"] = manifest_digest(untrusted)
    _write(directory / "unit-manifest.json", untrusted)
    _write(directory / "unit-proof.json", proof)
    with pytest.raises(UnitEvidenceError, match="trusted canonical manifest"):
        _verify(packet_case)


@pytest.mark.parametrize("value", [None, True, 1, "A" * 40, "a" * 39, "b" * 40])
def test_packet_requires_exact_expected_source_sha(packet_case, value):
    with pytest.raises(UnitEvidenceError):
        packet.verify_packet(packet_case[0], expected_source_sha=value, expected_manifest=packet_case[1])


@pytest.mark.parametrize("mutation", [
    "profile", "source", "manifest-digest", "coverage-digest", "coverage-path",
    "missing-field", "extra-field", "collected-type", "duplicate-node", "unexpected-node",
    "missing-node", "reports-type", "missing-phase", "duplicate-phase", "unknown-phase",
    "unexpected-report-node", "skip", "effects", "effects-type", "imports-type",
    "duplicate-import", "unknown-import", "missing-test-import",
])
def test_packet_refuses_incomplete_or_relabelled_unit_proof(packet_case, mutation):
    directory, _, proof, _, _ = packet_case
    if mutation == "profile":
        proof["profile"] = "other"
    elif mutation == "source":
        proof["source_sha"] = "b" * 40
    elif mutation == "manifest-digest":
        proof["manifest_sha256"] = "b" * 64
    elif mutation == "coverage-digest":
        proof["coverage_sha256"] = "b" * 64
    elif mutation == "coverage-path":
        proof["coverage_file"] = "../coverage.json"
    elif mutation == "missing-field":
        del proof["profile"]
    elif mutation == "extra-field":
        proof["run_attempt"] = 1
    elif mutation == "collected-type":
        proof["collected"] = [True]
    elif mutation == "duplicate-node":
        proof["collected"] *= 2
    elif mutation == "unexpected-node":
        proof["collected"] = [TEST + "::other"]
    elif mutation == "missing-node":
        proof["collected"] = []
    elif mutation == "reports-type":
        proof["reports"] = {}
    elif mutation == "missing-phase":
        proof["reports"].pop()
    elif mutation == "duplicate-phase":
        proof["reports"].append(proof["reports"][0])
    elif mutation == "unknown-phase":
        proof["reports"][0]["phase"] = "collection"
    elif mutation == "unexpected-report-node":
        proof["reports"][0]["node"] = TEST + "::other"
    elif mutation == "skip":
        proof["reports"][1]["outcome"] = "skipped"
    elif mutation == "effects":
        proof["effects"] = ["subprocess"]
    elif mutation == "effects-type":
        proof["effects"] = False
    elif mutation == "imports-type":
        proof["imported_sources"] = [False]
    elif mutation == "duplicate-import":
        proof["imported_sources"].append(TEST)
    elif mutation == "unknown-import":
        proof["imported_sources"].append("tools/unadmitted.py")
    else:
        proof["imported_sources"] = [TARGET]
    _write(directory / "unit-proof.json", proof)
    with pytest.raises(UnitEvidenceError):
        _verify(packet_case)


@pytest.mark.parametrize("elapsed", [True, 1, "1", -0.1, 300.0, float("nan"), float("inf")])
def test_packet_rejects_elapsed_type_aliases_and_nonfinite_or_over_budget_values(packet_case, elapsed):
    directory, _, proof, _, _ = packet_case
    proof["elapsed_seconds"] = elapsed
    _write(directory / "unit-proof.json", proof)
    with pytest.raises(UnitEvidenceError):
        _verify(packet_case)


@pytest.mark.parametrize("mutation", [
    "missing-file", "extra-file", "alias-file", "missing-context", "unknown-context",
    "wrong-phase", "setup-only", "unexecuted-context", "missing-executed-context",
    "bool-line", "float-line", "duplicate-line", "alias-line", "bool-context",
    "duplicate-context", "summary-type", "missing-overlap",
])
def test_packet_refuses_incomplete_or_foreign_production_coverage(packet_case, mutation):
    _, _, _, coverage, _ = packet_case
    record = coverage["files"][TARGET]
    if mutation == "missing-file":
        coverage["files"] = {}
    elif mutation == "extra-file":
        coverage["files"]["tools/other.py"] = copy.deepcopy(record)
    elif mutation == "alias-file":
        coverage["files"]["tools/./example.py"] = coverage["files"].pop(TARGET)
    elif mutation == "missing-context":
        del record["contexts"]
    elif mutation == "unknown-context":
        record["contexts"]["1"] = [TEST + "::other|call"]
    elif mutation == "wrong-phase":
        record["contexts"]["1"] = [NODE + "|collection"]
    elif mutation == "setup-only":
        record["contexts"]["1"] = ["", NODE + "|setup"]
    elif mutation == "unexecuted-context":
        record["contexts"]["2"] = [NODE + "|call"]
    elif mutation == "missing-executed-context":
        record["executed_lines"].append(2)
    elif mutation == "bool-line":
        record["executed_lines"] = [True]
    elif mutation == "float-line":
        record["executed_lines"] = [1.0]
    elif mutation == "duplicate-line":
        record["executed_lines"] = [1, 1]
    elif mutation == "alias-line":
        record["contexts"] = {"01": [NODE + "|call"]}
    elif mutation == "bool-context":
        record["contexts"]["1"] = [False]
    elif mutation == "duplicate-context":
        record["contexts"]["1"] = [NODE + "|call", NODE + "|call"]
    elif mutation == "summary-type":
        record["summary"] = []
    else:
        record["missing_lines"] = [1]
    _coverage(packet_case)
    with pytest.raises(UnitEvidenceError):
        _verify(packet_case)


@pytest.mark.parametrize("name", ["unit-manifest.json", "unit-proof.json", "coverage.json"])
def test_packet_rejects_duplicate_json_keys(packet_case, name):
    path = packet_case[0] / name
    raw = path.read_text()
    path.write_text('{"duplicate":0,"duplicate":1,' + raw[1:])
    if name == "coverage.json":
        proof = packet_case[2]
        proof["coverage_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        _write(packet_case[0] / "unit-proof.json", proof)
    with pytest.raises(UnitEvidenceError, match="duplicate"):
        _verify(packet_case)


@pytest.mark.parametrize("name", ["unit-manifest.json", "unit-proof.json", "coverage.json"])
def test_packet_rejects_nonobject_or_malformed_json(packet_case, name):
    path = packet_case[0] / name
    path.write_bytes(b"[]")
    with pytest.raises(UnitEvidenceError, match="object"):
        _verify(packet_case)
    path.write_bytes(b"{")
    with pytest.raises(UnitEvidenceError, match="invalid packet JSON"):
        _verify(packet_case)


@pytest.mark.parametrize("name", ["unit-manifest.json", "unit-proof.json", "coverage.json"])
def test_packet_requires_each_regular_bounded_file(packet_case, monkeypatch, name):
    directory = packet_case[0]
    size = max(path.stat().st_size for path in directory.iterdir())
    monkeypatch.setattr(packet, "MAX_PACKET_FILE_BYTES", size)
    (directory / name).write_bytes(b" " * (size + 1))
    with pytest.raises(UnitEvidenceError, match="bounded"):
        _verify(packet_case)


@pytest.mark.parametrize("name", ["unit-manifest.json", "unit-proof.json", "coverage.json"])
def test_packet_rejects_extra_or_missing_packet_files(packet_case, name):
    directory = packet_case[0]
    (directory / "unexpected.json").write_text("{}")
    with pytest.raises(UnitEvidenceError, match="exactly"):
        _verify(packet_case)
    (directory / "unexpected.json").rename(directory.parent / "unexpected.json")
    (directory / name).rename(directory.parent / ("moved-" + name))
    with pytest.raises(UnitEvidenceError, match="exactly"):
        _verify(packet_case)


def test_packet_rejects_indirect_or_nonregular_files_without_opening_them(packet_case, monkeypatch):
    directory = packet_case[0]
    original = packet.Path.lstat

    def indirect(path, *args, **kwargs):
        if path == directory / "unit-proof.json":
            return SimpleNamespace(st_mode=0o120777, st_size=0)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(packet.Path, "lstat", indirect)
    with pytest.raises(UnitEvidenceError, match="regular, unlinked"):
        _verify(packet_case)


@pytest.mark.parametrize("path", [
    "tools/./example.py", "tools//example.py", "tools/../example.py",
    "C:example.py", "C:/example.py", "tools\\example.py", "tools/null\0.py",
])
def test_packet_rejects_manifest_path_aliases(packet_case, path):
    manifest = copy.deepcopy(packet_case[1])
    manifest["sources"][path] = manifest["sources"].pop(TARGET)
    manifest["coverage_targets"] = [path]
    with pytest.raises(UnitEvidenceError):
        packet.verify_packet(packet_case[0], expected_source_sha=SOURCE, expected_manifest=manifest)


@pytest.mark.parametrize("value", [True, 300.0, "300"])
def test_packet_rejects_manifest_budget_type_aliases(packet_case, value):
    manifest = copy.deepcopy(packet_case[1])
    manifest["max_seconds"] = value
    with pytest.raises(UnitEvidenceError):
        packet.verify_packet(packet_case[0], expected_source_sha=SOURCE, expected_manifest=manifest)


def test_packet_does_not_require_a_specific_coverage_metadata_version(packet_case):
    coverage = packet_case[3]
    coverage["meta"] = {"future-format": {"fields": [1, "metadata"]}}
    coverage["totals"] = {"additional": {"summary": True}}
    _coverage(packet_case)
    assert _verify(packet_case)["profile"] == PROFILE


def test_packet_checks_optional_source_bytes(packet_case):
    root = packet_case[4]
    _verify(packet_case, source_root=root)
    (root / TARGET).write_text("VALUE = 2\n")
    with pytest.raises(UnitEvidenceError, match="changed without admission"):
        _verify(packet_case, source_root=root)
