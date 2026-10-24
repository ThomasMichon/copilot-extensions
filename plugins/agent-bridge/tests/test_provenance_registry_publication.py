"""Exercise the existing capture owner's metadata publication and recovery."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

import agent_bridge_contract_publication as publication
import agent_bridge_contract_registry as codec
from test_provenance_registry_codec import publish as seed, registry


@pytest.fixture
def planned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict, dict, dict]:
    original = registry()
    seed(tmp_path, {codec.REGISTRY: codec.encode(original)})
    snapshot = codec.load(tmp_path)
    updated = copy.deepcopy(original)
    updated["contracts"][0]["provenance"] *= 12
    monkeypatch.setattr(codec, "MAX_ARTIFACT_BYTES", 3500)
    monkeypatch.setattr(codec, "CHUNK_BYTES", 800)
    outputs = codec.plan(tmp_path, snapshot, updated)
    return tmp_path, original, updated, outputs


def test_provenance_publication_serializes_on_stable_kernel_lock(planned: tuple) -> None:
    root, original, _updated, outputs = planned
    with publication._lock(root):
        with pytest.raises(OSError):
            publication.publish(root, "a" * 40, outputs, validate=lambda: None)
    assert codec.load(root).materialized == original
    assert not (root / publication.JOURNAL).exists()


def test_provenance_publication_refuses_index_changed_after_plan(planned: tuple) -> None:
    root, _original, _updated, outputs = planned
    snapshot = codec.load(root)
    (root / codec.REGISTRY).write_bytes(codec.encode(registry()) + b" ")
    with pytest.raises(ValueError, match="metadata changed"):
        publication.publish(root, "a" * 40, outputs, validate=lambda: snapshot.verify(root))
    assert not (root / publication.JOURNAL).exists()


def test_provenance_publication_recovers_same_plan_after_interruption(
    planned: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, original, updated, outputs = planned
    before = (root / codec.REGISTRY).read_bytes()
    replace = publication.os.replace

    def interrupted(source: Path, target: Path) -> None:
        if target == root / codec.REGISTRY:
            raise OSError("synthetic interruption before publishing index")
        replace(source, target)

    def validate() -> None:
        assert codec.load(root).materialized in (original, updated)

    with monkeypatch.context() as fault:
        fault.setattr(publication.os, "replace", interrupted)
        with pytest.raises(OSError, match="synthetic interruption"):
            publication.publish(root, "a" * 40, outputs, validate=validate)
    assert codec.load(root).materialized == original
    journal = root / publication.JOURNAL
    assert list(journal.glob("*/manifest.json"))
    assert any(path.read_bytes() == before for path in journal.glob("*/old-*.json"))
    assert all((root / path).read_bytes() == data for path, data in outputs.items()
               if path != codec.REGISTRY)
    publication.publish(root, "a" * 40, outputs, validate=validate)
    assert codec.load(root).materialized == updated
    assert not journal.exists()


def test_provenance_publication_refuses_alternate_plan_during_recovery(
    planned: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, original, _updated, outputs = planned
    with monkeypatch.context() as fault:
        fault.setattr(publication.os, "replace", lambda *_: (_ for _ in ()).throw(
            OSError("synthetic interruption"),
        ))
        with pytest.raises(OSError, match="synthetic"):
            publication.publish(root, "a" * 40, outputs, validate=lambda: None)
    with pytest.raises(ValueError, match="explicit recovery"):
        publication.publish(root, "b" * 40, outputs, validate=lambda: None)
    assert codec.load(root).materialized == original
    assert list((root / publication.JOURNAL).glob("*/manifest.json"))


def test_provenance_publication_pins_historical_inputs_until_index_commit(
    planned: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, original, _updated, outputs = planned
    historical = "plugins/agent-bridge/contract/fixtures/preserved.json"
    seed(root, {historical: b'{"preserved":true}\n'})
    original_bytes = (root / historical).read_bytes()
    store = publication._store

    def changed(root: Path, path: str, data: bytes) -> Path:
        result = store(root, path, data)
        if path.endswith("/manifest.json"):
            (root / historical).write_bytes(b'{"preserved":false}\n')
        return result

    monkeypatch.setattr(publication, "_store", changed)
    with pytest.raises(ValueError, match="publication input changed"):
        publication.publish(
            root, "a" * 40, outputs, validate=lambda: None,
            input_hashes={historical: codec.digest(original_bytes)},
        )
    assert codec.load(root).materialized == original


def test_provenance_publication_rechecks_history_before_index_without_per_output_rereads(
    planned: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, original, _updated, outputs = planned
    historical = "plugins/agent-bridge/contract/fixtures/preserved.json"
    seed(root, {historical: b'{"preserved":true}\n'})
    before = (root / historical).read_bytes()
    read = codec.read_file
    reads = 0

    def counted(root: Path, relative: str) -> bytes:
        nonlocal reads
        if relative == historical:
            reads += 1
        return read(root, relative)

    link = publication.os.link

    def changed(source: Path, target: Path) -> None:
        link(source, target)
        (root / historical).write_bytes(b'{"preserved":false}\n')

    monkeypatch.setattr(codec, "read_file", counted)
    monkeypatch.setattr(publication.os, "link", changed)
    with pytest.raises(ValueError, match="publication input changed"):
        publication.publish(
            root, "a" * 40, outputs, validate=lambda: None,
            input_hashes={historical: codec.digest(before)},
        )
    assert codec.load(root).materialized == original
    assert reads == 2
    assert list((root / publication.JOURNAL).glob("*/manifest.json"))
