"""The artifact adapter rejects untrusted input before native publication."""

import hashlib
import importlib.util
import os
from pathlib import Path
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[3] / "tools"
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("http52_artifact", TOOLS / "bridge_http52_artifact.py")
artifact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifact)


def _artifact(tmp_path):
    root = tmp_path / "artifact"
    target = root / "outputs" / "example.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"example":true}\n')
    (root / artifact.MANIFEST).write_bytes(b"{}\n")
    return root, target, {"example.json": hashlib.sha256(target.read_bytes()).hexdigest()}


def test_output_inventory_round_trips_only_declared_plain_files(tmp_path):
    root, target, hashes = _artifact(tmp_path)
    assert artifact._read_outputs(root, hashes) == {"example.json": target.read_bytes()}


@pytest.mark.parametrize("fault", ["bytes", "extra", "traversal", "alias"])
def test_output_inventory_refuses_untrusted_artifacts(tmp_path, fault):
    root, target, hashes = _artifact(tmp_path)
    if fault == "bytes":
        target.write_bytes(b"changed")
    elif fault == "extra":
        (root / "unexpected.json").write_bytes(b"{}")
    elif fault == "traversal":
        hashes["../unexpected.json"] = "0" * 64
    else:
        os.link(target, root / "alias.json")
    with pytest.raises(ValueError):
        artifact._read_outputs(root, hashes)


@pytest.mark.parametrize("fault", ["changed", "unexpected-created"])
def test_native_preimages_refuse_changed_or_new_inputs(tmp_path, fault):
    target = tmp_path / "source.json"
    target.write_bytes(b"original\n")
    expected = {"source.json": hashlib.sha256(target.read_bytes()).hexdigest()}
    if fault == "changed":
        target.write_bytes(b"changed\n")
    else:
        expected["source.json"] = None
    with pytest.raises(ValueError, match="preimage"):
        artifact._preimage_hashes(tmp_path, expected)


def test_foreign_source_receipt_cannot_reach_publication(tmp_path, monkeypatch):
    root = tmp_path / "artifact"
    root.mkdir()
    data = artifact.codec.encode({
        "schema": artifact.SCHEMA, "version": 1, "source_commit": "2" * 40,
        "generation": 52, "case_count": sum(artifact.evidence.PROBES.values()),
        "input_preimages": {}, "outputs": {},
    })
    (root / artifact.MANIFEST).write_bytes(data)
    monkeypatch.setattr(artifact, "_head", lambda *args: None)
    monkeypatch.setattr(artifact, "publish", lambda *a, **k: pytest.fail("published foreign input"))
    with pytest.raises(ValueError, match="foreign artifact"):
        artifact.materialize(tmp_path, "1" * 40, root, hashlib.sha256(data).hexdigest())
