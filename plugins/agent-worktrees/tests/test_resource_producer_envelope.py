"""Malformed handoff entries and producer envelope versions fail explicitly."""

from __future__ import annotations

import argparse
import copy
import io

import pytest

from agent_worktrees import claim_handoff_admission as admission, claim_handoffs
from agent_worktrees import resource_producer as producer, resource_producer_cli as cli
from agent_worktrees import tracking_producer_write as writer
from test_resource_producer import call, reserve
from test_resource_producer import owner as owner


@pytest.mark.parametrize("entry,message", [
    (None, "claim bundle entry must be a mapping"),
    ([], "claim bundle entry must be a mapping"),
    ({}, "claim bundle entry requires a nonempty string ref"),
    ({"ref": None}, "claim bundle entry requires a nonempty string ref"),
    ({"ref": 1}, "claim bundle entry requires a nonempty string ref"),
    ({"ref": []}, "claim bundle entry requires a nonempty string ref"),
    ({"ref": ""}, "claim bundle entry requires a nonempty string ref"),
    ({"ref": " "}, "claim bundle entry requires a nonempty string ref"),
    ({"ref": "r", "producer": None}, "claim bundle producer metadata must be a mapping: r"),
    ({"ref": "r", "producer": []}, "claim bundle producer metadata must be a mapping: r"),
])
def test_malformed_bundle_entry_raises_domain_error_without_mutation(
    entry: object, message: str,
) -> None:
    before = copy.deepcopy(entry)
    with pytest.raises(claim_handoffs.ClaimHandoffError) as caught:
        admission.validate_producer_uniqueness([entry], error_type=claim_handoffs.ClaimHandoffError)
    assert str(caught.value) == message
    assert entry == before


def test_handler_rejects_old_version_and_reports_canonical_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(producer, "VERSION", 2)
    result = writer.apply_resource_producer({"schema": producer.SCHEMA, "version": 1})
    assert result["error"] == "rejected"
    assert result["version"] == producer.VERSION
    assert result["message"] == "unsupported resource-producer schema/version"


def test_cli_capabilities_and_rejection_share_canonical_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(producer, "VERSION", 2)
    emitted: list[dict] = []
    monkeypatch.setattr(cli.output, "_json_output", emitted.append)
    assert cli.cmd_resource_producer(argparse.Namespace(operation="capabilities")) == 0
    assert emitted.pop()["version"] == producer.VERSION
    monkeypatch.setattr(cli.sys, "stdin", io.TextIOWrapper(io.BytesIO(b"[]")))
    assert cli.cmd_resource_producer(argparse.Namespace(operation="reserve", owner_ref=None)) == 1
    assert emitted.pop()["version"] == producer.VERSION


def test_current_request_and_success_version_match(owner: tuple) -> None:
    path, _ = owner
    reservation = reserve(path)
    assert reservation["version"] == reservation["producer"]["version"] == producer.VERSION
    inspected = call(path, "inspect", {"token": reservation["token"]})
    assert inspected["version"] == producer.VERSION
