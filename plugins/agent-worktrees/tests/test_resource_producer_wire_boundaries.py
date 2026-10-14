"""Byte-bounded producer ingress and deep-copy bundle exports."""

from __future__ import annotations

import argparse
import copy
import io
import json
from types import SimpleNamespace

import pytest

from agent_worktrees import claim_handoffs, output, resource_producer as producer
from agent_worktrees import resource_producer_cli as cli
from test_resource_producer import bind, reserve
from test_resource_producer import owner as owner


class BoundedInput(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.requested: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.requested.append(size)
        assert size == producer.MAX_INPUT_BYTES + 1
        return super().read(size)


@pytest.mark.parametrize("raw", [
    b"x" * (producer.MAX_INPUT_BYTES + 100),
    ("😀" * producer.MAX_INPUT_BYTES).encode("utf-8"),
    b'{"key":"\xff"}',
], ids=["ascii-overflow", "utf8-overflow", "invalid-utf8"])
def test_cli_rejects_oversize_bytes_or_invalid_utf8_before_authority(
    monkeypatch: pytest.MonkeyPatch, raw: bytes,
) -> None:
    stream = BoundedInput(raw)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(buffer=stream))
    monkeypatch.setattr(
        cli.worktree_ops_cli, "_resolve_owner_ref",
        lambda: pytest.fail("invalid input reached owner resolution"),
    )
    with output.capture_json_output() as captured:
        assert cli.cmd_resource_producer(
            argparse.Namespace(operation="reserve", owner_ref=None),
        ) == 1
    error = json.loads(captured.getvalue())
    assert error["error"] == "rejected"
    assert stream.requested == [producer.MAX_INPUT_BYTES + 1]
    assert stream.tell() <= producer.MAX_INPUT_BYTES + 1
    if len(raw) > producer.MAX_INPUT_BYTES:
        assert error["message"] == "producer stdin exceeds size limit"


@pytest.mark.parametrize("character", ["a", "é", "😀"], ids=["ascii", "two-byte", "four-byte"])
def test_exact_byte_limit_decodes_before_owner_resolution(
    monkeypatch: pytest.MonkeyPatch, character: str,
) -> None:
    width = len(character.encode("utf-8"))
    count, padding = divmod(producer.MAX_INPUT_BYTES - len(b'{"value":""}'), width)
    raw = json.dumps({"value": character * count + "a" * padding}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert len(raw) == producer.MAX_INPUT_BYTES
    stream = BoundedInput(raw)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(buffer=stream))
    reached: list[bool] = []

    def owner_ref() -> None:
        reached.append(True)
        return None

    monkeypatch.setattr(cli.worktree_ops_cli, "_resolve_owner_ref", owner_ref)
    with output.capture_json_output() as captured:
        assert cli.cmd_resource_producer(
            argparse.Namespace(operation="reserve", owner_ref=None),
        ) == 1
    assert json.loads(captured.getvalue())["message"] == "no durable producer owner resolved from current directory"
    assert reached == [True]
    assert stream.requested == [producer.MAX_INPUT_BYTES + 1]


def test_exported_bundle_cannot_mutate_nested_producer_or_later_export(owner: tuple) -> None:
    path, _ = owner
    reservation = bind(path, reserve(path))
    bundle, _ = claim_handoffs.offer("m/p/owner", "m/p/consumer", [reservation["ref"]], machine="m")
    original = copy.deepcopy(bundle.claims)
    registry_before = claim_handoffs.registry_path().read_bytes()
    ledger_before = path.read_bytes()
    exported = bundle.to_dict()
    exported["claims"][0]["producer"]["identity"]["immutable_id"] = "changed"
    exported["claims"][0]["producer"]["recovery"]["nested"] = {"items": ["changed"]}
    assert bundle.claims == original
    assert bundle.to_dict()["claims"] == list(original)
    assert claim_handoffs.registry_path().read_bytes() == registry_before
    assert path.read_bytes() == ledger_before
