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


@pytest.mark.parametrize("interruption", ["mutable-postimage", "chunk-link"])
def test_identical_artifact_resumes_native_interrupted_publication(tmp_path, monkeypatch, interruption):
    import copy
    import agent_bridge_contract_publication as native

    root = tmp_path / "source"
    root.mkdir()
    source = "1" * 40
    before = {
        "schema_version": 2, "deferred_contracts": [],
        "contracts": [{
            "id": "agent-bridge.http-wire", "provenance": [{"fixture": "original"}],
            "source_paths": [], "fixtures": [],
        }],
    }
    primary = artifact.evidence.PRIMARY
    old = b'{"fixture":"original"}\n'
    before["contracts"][0]["fixtures"] = [{"path": primary, "sha256": artifact.codec.digest(old)}]
    for path, data in ((artifact.codec.REGISTRY, artifact.codec.encode(before)), (primary, old)):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    baseline = artifact.codec.load(root)
    data = artifact.codec.encode({
        "captured_from": {"commit": source, "protocol_generation": 52},
        "checked_probes": artifact.evidence.PROBES, "execution_closure": {},
    })
    after = copy.deepcopy(before)
    after["contracts"][0]["fixtures"][0]["sha256"] = artifact.codec.digest(data)
    outputs = {primary: data}
    chunk_path = None
    if interruption == "chunk-link":
        chunk = artifact.codec.encode({
            "schema": artifact.codec.CHUNK_SCHEMA, "schema_version": 1,
            "contract": "agent-bridge.http-wire", "record_offset": 0,
            "provenance": [{"fixture": "original"}, {"fixture": "captured"}],
        })
        digest = artifact.codec.digest(chunk)
        chunk_path = artifact.codec.PROVENANCE + "agent-bridge.http-wire/" + digest + ".json"
        outputs[chunk_path] = chunk
        after["contracts"][0]["provenance"] = []
        after["contracts"][0]["provenance_chunks"] = [{
            "contract": "agent-bridge.http-wire", "path": chunk_path,
            "sha256": digest, "record_count": 2,
        }]
    else:
        after["contracts"][0]["provenance"].append({"fixture": "captured"})
    outputs[artifact.codec.REGISTRY] = artifact.codec.encode(after)
    directory = tmp_path / "artifact"
    directory.mkdir()
    for path, content in outputs.items():
        target = directory / "outputs" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    manifest = artifact.codec.encode({
        "schema": artifact.SCHEMA, "version": 1, "source_commit": source,
        "generation": 52, "case_count": sum(artifact.evidence.PROBES.values()),
        "input_preimages": artifact._preimages(baseline, outputs),
        "outputs": {path: artifact.codec.digest(content) for path, content in outputs.items()},
    })
    (directory / artifact.MANIFEST).write_bytes(manifest)
    monkeypatch.setattr(artifact, "_head", lambda *args: None)
    monkeypatch.setattr(artifact.evidence, "baseline", lambda *args: baseline)
    monkeypatch.setattr(artifact.evidence, "validate_outputs", lambda *args: None)
    replace, link = native.os.replace, native.os.link

    def interrupt_replace(left, right):
        replace(left, right)
        if Path(right) == root / primary:
            raise OSError("interrupted private fixture")

    def interrupt_link(left, right):
        link(left, right)
        raise OSError("interrupted private fixture")

    with monkeypatch.context() as fault:
        fault.setattr(
            native.os, "replace" if interruption == "mutable-postimage" else "link",
            interrupt_replace if interruption == "mutable-postimage" else interrupt_link,
        )
        with pytest.raises(OSError, match="interrupted private fixture"):
            artifact.materialize(root, source, directory, artifact.codec.digest(manifest))
    assert (root / native.JOURNAL).is_dir()
    artifact.materialize(root, source, directory, artifact.codec.digest(manifest))
    assert not (root / native.JOURNAL).exists()
    assert artifact.codec.load(root).materialized["contracts"][0]["provenance"] == [
        {"fixture": "original"}, {"fixture": "captured"},
    ]
    if chunk_path:
        assert (root / chunk_path).stat().st_nlink == 1
