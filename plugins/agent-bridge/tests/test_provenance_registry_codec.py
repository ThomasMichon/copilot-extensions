"""Lossless representation checks, without runtime or capture permission."""

from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest

import agent_bridge_contract_registry as codec


def registry() -> dict:
    return {
        "schema_version": 1,
        "contracts": [{
            "id": "agent-bridge.http-wire",
            "provenance": [{"ordinal": n, "capture_method": "exact \u03bb"} for n in range(8)],
        }],
        "deferred_contracts": [],
    }


def publish(root: Path, outputs: dict[str, bytes]) -> None:
    for relative, data in outputs.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


@pytest.fixture
def archived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict]:
    original = registry()
    publish(tmp_path, {codec.REGISTRY: codec.encode(original)})
    snapshot = codec.load(tmp_path)
    updated = copy.deepcopy(original)
    updated["contracts"][0]["provenance"] *= 12
    # Lower only the planner threshold; the production guard remains 1 MiB.
    monkeypatch.setattr(codec, "MAX_ARTIFACT_BYTES", 3500)
    monkeypatch.setattr(codec, "CHUNK_BYTES", 800)
    outputs = codec.plan(tmp_path, snapshot, updated)
    assert list(outputs)[-1] == codec.REGISTRY
    assert len(outputs) > 2
    publish(tmp_path, outputs)
    return tmp_path, updated


def test_provenance_codec_legacy_read_and_plan_do_not_write(tmp_path: Path) -> None:
    original = registry()
    data = codec.encode(original)
    publish(tmp_path, {codec.REGISTRY: data})
    snapshot = codec.load(tmp_path)
    assert snapshot.materialized == original
    assert codec.plan(tmp_path, snapshot, original) == {codec.REGISTRY: data}
    assert snapshot.inputs == {codec.REGISTRY: data}
    snapshot.verify(tmp_path)
    assert not (tmp_path / codec.PROVENANCE).exists()


def test_provenance_codec_roundtrip_order_duplicates_and_append(archived: tuple[Path, dict]) -> None:
    root, expected = archived
    snapshot = codec.load(root)
    assert snapshot.materialized == expected
    assert snapshot.index["schema_version"] == 2
    assert snapshot.index["contracts"][0]["provenance"] == []
    previous_chunks = snapshot.index["contracts"][0]["provenance_chunks"]
    updated = copy.deepcopy(expected)
    updated["contracts"][0]["provenance"].append({"ordinal": 999})
    outputs = codec.plan(root, snapshot, updated)
    assert set(outputs) == {codec.REGISTRY}
    publish(root, outputs)
    actual = codec.load(root)
    assert actual.materialized == updated
    assert actual.index["contracts"][0]["provenance_chunks"] == previous_chunks
    for path, data in snapshot.inputs.items():
        if path != codec.REGISTRY:
            assert (root / path).read_bytes() == data


def test_provenance_codec_refuses_archived_record_edits(archived: tuple[Path, dict]) -> None:
    root, expected = archived
    snapshot = codec.load(root)
    updated = copy.deepcopy(expected)
    updated["contracts"][0]["provenance"][0]["ordinal"] = 999
    with pytest.raises(ValueError, match="immutable provenance prefix"):
        codec.plan(root, snapshot, updated)
    snapshot.verify(root)


@pytest.mark.parametrize("member", ["index", "chunk"])
def test_provenance_codec_pins_all_inputs(
    archived: tuple[Path, dict], member: str,
) -> None:
    root, _ = archived
    snapshot = codec.load(root)
    path = codec.REGISTRY if member == "index" else next(
        p for p in snapshot.inputs if p != codec.REGISTRY
    )
    (root / path).write_bytes(snapshot.inputs[path] + b" ")
    with pytest.raises(ValueError, match="metadata changed"):
        snapshot.verify(root)


@pytest.mark.parametrize("failure", [
    "missing", "hash", "foreign", "count", "bool-count", "unknown", "escape",
    "duplicate", "version", "recursive", "offset", "noncanonical", "symlink", "hardlink",
])
def test_provenance_codec_refuses_incomplete_history(
    archived: tuple[Path, dict], failure: str,
) -> None:
    root, _ = archived
    index = codec.decode((root / codec.REGISTRY).read_bytes())
    contract = index["contracts"][0]
    descriptor = contract["provenance_chunks"][0]
    path = root / descriptor["path"]
    content = path.read_bytes()
    if failure == "missing":
        path.unlink()
    elif failure == "hash":
        path.write_bytes(content + b" ")
    elif failure == "foreign":
        descriptor["contract"] = "another.contract"
    elif failure in {"count", "bool-count"}:
        descriptor["record_count"] = True if failure == "bool-count" else 999
    elif failure == "unknown":
        descriptor["extra"] = "unreviewed"
    elif failure == "escape":
        descriptor["path"] = "../outside.json"
    elif failure == "duplicate":
        contract["provenance_chunks"].append(copy.deepcopy(descriptor))
    elif failure in {"version", "recursive", "offset", "noncanonical"}:
        value = codec.decode(content)
        if failure == "version":
            value["schema_version"] = 999
        elif failure == "recursive":
            value["provenance_chunks"] = [copy.deepcopy(descriptor)]
        elif failure == "offset":
            value["record_offset"] = 123
        content = codec.encode(value) + (b" " if failure == "noncanonical" else b"")
        descriptor["sha256"] = codec.digest(content)
        descriptor["path"] = codec._chunk_path(contract["id"], descriptor["sha256"])
        path = root / descriptor["path"]
        path.write_bytes(content)
    else:
        alias = root / "alias.json"
        alias.write_bytes(content)
        path.unlink()
        if failure == "symlink":
            path.symlink_to(alias)
        else:
            os.link(alias, path)
    publish(root, {codec.REGISTRY: codec.encode(index)})
    with pytest.raises((ValueError, OSError)):
        codec.load(root)


@pytest.mark.parametrize("data", [
    b'{"schema_version":1,"schema_version":2}',
    b'{"schema_version":NaN}',
    b'{"schema_version":true,"contracts":[],"deferred_contracts":[]}',
    b'{"schema_version":999,"contracts":[],"deferred_contracts":[]}',
])
def test_provenance_codec_refuses_ambiguous_json(tmp_path: Path, data: bytes) -> None:
    publish(tmp_path, {codec.REGISTRY: data})
    with pytest.raises(ValueError):
        codec.load(tmp_path)


@pytest.mark.parametrize("relative", [
    ".", "..", "a/../x", "/x", "a\\x", "a//x", "a:x", ".git/config",
])
def test_provenance_codec_refuses_non_artifact_paths(tmp_path: Path, relative: str) -> None:
    with pytest.raises(ValueError, match="unsafe capture path"):
        codec.safe(tmp_path, relative)


def test_provenance_codec_real_history_fits_without_any_record_change(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[3]
    snapshot = codec.load(source)
    updated = copy.deepcopy(snapshot.materialized)
    # Exercise above the real guard without shrinking it or dropping duplicates.
    rows = updated["contracts"][0]["provenance"]
    rows.extend(copy.deepcopy(rows))
    assert len(codec.encode(updated)) > 1 << 20
    publish(tmp_path, snapshot.inputs)
    outputs = codec.plan(tmp_path, snapshot, updated)
    assert all(len(data) <= 1 << 20 for data in outputs.values())
    publish(tmp_path, outputs)
    actual = codec.load(tmp_path)
    assert actual.materialized == updated
    for path, data in snapshot.inputs.items():
        if path != codec.REGISTRY:
            assert actual.inputs[path] == data
    assert codec.plan(tmp_path, actual, updated) == {codec.REGISTRY: outputs[codec.REGISTRY]}
