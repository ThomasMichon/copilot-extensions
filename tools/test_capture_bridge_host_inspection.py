"""Focused failures for the additive H1 inspection capture owner."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest

import capture_bridge_host_inspection as capture
import agent_bridge_contract_git as git


def _vector() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((capture.ROOT / capture.INPUT).read_bytes()))


def test_real_h1_inspection_matches_independent_vector() -> None:
    assert asyncio.run(capture.observe(_vector())) == _vector()["expected"]


def test_h1_capture_does_not_hide_dispatch_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_remote_driver.session_host.host import SessionHost

    original = SessionHost._handle_front

    async def altered(host: Any, reader: asyncio.StreamReader, writer: Any) -> None:
        await original(host, reader, writer)
        host._ack_cursor = 1

    monkeypatch.setattr(SessionHost, "_handle_front", altered)
    with pytest.raises(ValueError, match="custody"):
        asyncio.run(capture.observe(_vector()))


def test_h1_capture_refuses_attempted_child_input(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_remote_driver.session_host.host import SessionHost

    original = SessionHost._handle_front

    async def altered(host: Any, reader: asyncio.StreamReader, writer: Any) -> None:
        await original(host, reader, writer)
        host._child.stdin.write(b"unexpected input")

    monkeypatch.setattr(SessionHost, "_handle_front", altered)
    with pytest.raises(AssertionError, match="child/process effect"):
        asyncio.run(capture.observe(_vector()))


@pytest.mark.parametrize("fixtures", [None, [{}], [{"path": []}]])
def test_h1_optional_validator_defers_malformed_shape_to_registry_owner(
    tmp_path: Path, fixtures: Any,
) -> None:
    errors: list[str] = []
    capture.validate_packets(tmp_path, {"fixtures": fixtures}, errors)
    assert errors == []


def test_h1_capture_does_not_relabel_changed_historical_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_remote_driver.session_host import protocol

    original = protocol.encode

    def altered(kind: protocol.MsgType, payload: bytes = b"") -> bytes:
        value = original(kind, payload)
        return value + b"changed" if kind == protocol.MsgType.ATTACH else value

    monkeypatch.setattr(protocol, "encode", altered)
    with pytest.raises(ValueError, match="historical H1 encoding"):
        asyncio.run(capture.observe(_vector()))


def test_h1_capture_requires_matching_independent_request() -> None:
    vector = copy.deepcopy(_vector())
    vector["nonce"] = "1" * 32
    observed = asyncio.run(capture.observe(vector))
    assert observed != vector["expected"]


@pytest.mark.parametrize("fault", ["none", "incomplete", "observation", "binding", "source", "input"])
def test_packet_validation_keeps_source_bound_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    vector = _vector()
    target = tmp_path / capture.INPUT
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(vector), encoding="utf-8")
    proof = dict.fromkeys((
        *capture.SOURCES, capture.INPUT, capture.SCRIPT, capture.CURRENT + "messages.json",
    ), "a" * 64)
    proof[capture.INPUT] = git.sha256_bytes(target.read_bytes())
    if fault == "input":
        target.write_text(json.dumps({**vector, "uncaptured": True}), encoding="utf-8")
    fixtures = []
    for source in capture.SOURCES:
        relative = capture.CURRENT + "inspection-" + Path(source).stem + "-v1.json"
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        packet = {
            "captured_from": {
                "commit": "b" * 40, "source_path": source, "source_sha256": proof[source],
            },
            "scope": capture.SCOPE, "inputs": proof, "observed": vector["expected"],
        }
        if fault == "observation":
            packet["observed"] = {}
        if fault == "source":
            packet["captured_from"]["source_path"] = capture.SOURCES[0]
        target.write_text(json.dumps(packet), encoding="utf-8")
        fixtures.append({"path": relative})
    if fault == "incomplete":
        fixtures.pop()
    monkeypatch.setattr(git, "ensure_commit_available", lambda commit: True)
    monkeypatch.setattr(
        git, "git_file_sha256",
        lambda commit, source: "c" * 64 if fault == "binding" else proof[source],
    )
    errors: list[str] = []
    capture.validate_packets(tmp_path, {"fixtures": fixtures}, errors)
    assert bool(errors) == (fault != "none")
