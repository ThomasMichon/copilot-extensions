"""Bounded local object batches retain complete evidence and framing refusal."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


@pytest.fixture
def evidence(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    source = Path(__file__).resolve().parents[1] / "agent_bridge_contract_git.py"
    spec = importlib.util.spec_from_file_location("unit_bridge_contract_git", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "require_local_object_reads", lambda: None)
    return module


def _packet(queries: list[str], missing: str) -> bytes:
    output = bytearray()
    for query in queries:
        if query == missing:
            output.extend((query + " missing\n").encode("ascii"))
        else:
            body = query.encode("ascii")
            output.extend(f"{query} blob {len(body)}\n".encode("ascii") + body + b"\n")
    return bytes(output)


def test_large_object_inventory_is_bounded_complete_and_deduplicated(
    evidence: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    objects = [f"{index:040x}" for index in range(600)]
    missing = "f" * 40
    calls: list[list[str]] = []
    roots: list[str] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        queries = kwargs["input"].decode("utf-8").splitlines()
        calls.append(queries)
        roots.append(argv[argv.index("-C") + 1])
        assert argv[-2:] == ["cat-file", "--batch"]
        assert kwargs["timeout"] == 10 and kwargs["check"] is True
        assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
        return subprocess.CompletedProcess(argv, 0, _packet(queries, missing), b"")

    monkeypatch.setattr(
        evidence, "subprocess", SimpleNamespace(run=run, CREATE_NO_WINDOW=0x08000000),
    )
    evidence.prefetch_objects([*objects, missing, *objects])
    assert len(calls) > 1 and all(len(batch) <= 256 for batch in calls)
    assert [query for batch in calls for query in batch] == sorted({*objects, missing})
    for oid in objects:
        assert evidence.blob_sha256(oid) == hashlib.sha256(oid.encode("ascii")).hexdigest()
    assert evidence.blob_sha256(missing) is None
    alternate = evidence.REPO / "alternate-evidence"
    calls.clear()
    roots.clear()
    observed = list(evidence._batch(objects, root=alternate))
    assert [query for query, *_ in observed] == objects
    assert len(calls) > 1 and all(len(batch) <= 256 for batch in calls)
    assert roots == [str(alternate)] * len(calls)


@pytest.mark.parametrize("corruption", ["header", "truncated", "trailing"])
def test_later_batch_failure_does_not_publish_partial_object_evidence(
    evidence: ModuleType, monkeypatch: pytest.MonkeyPatch, corruption: str,
) -> None:
    objects = [f"{index:040x}" for index in range(600)]
    calls = 0

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        nonlocal calls
        calls += 1
        packet = _packet(kwargs["input"].decode("utf-8").splitlines(), "f" * 40)
        if calls == 2:
            packet = {
                "header": b"invalid\n",
                "truncated": packet[:-1],
                "trailing": packet + b"extra",
            }[corruption]
        return subprocess.CompletedProcess(argv, 0, packet, b"")

    monkeypatch.setattr(
        evidence, "subprocess", SimpleNamespace(run=run, CREATE_NO_WINDOW=0x08000000),
    )
    with pytest.raises(RuntimeError, match="(?i)local Git object batch"):
        evidence.prefetch_objects(objects)
    assert evidence._OBJECT_TYPES == {}
    assert evidence._BLOB_HASHES == {}
