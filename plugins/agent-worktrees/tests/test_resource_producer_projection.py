"""Malformed raw YAML remains owned without breaking JSON claim projections."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import claims_cli, output, resource_producer as producer, tracking
from test_resource_producer import reserve
from test_resource_producer import owner as owner
from test_worktree_status_compute import _wire_common_internals


YAML_VALUES = [
    "2026-10-12",
    "2026-10-12T09:00:00Z",
    "!!binary aGVsbG8=",
    "!!set {item: null}",
    "{1: non-string-key}",
    ".nan",
    ".inf",
    "&loop {loop: *loop}",
]
ERROR = {"error": "invalid-producer-metadata"}


def damage(path: Path, value: object) -> str:
    raw = yaml.safe_load(path.read_text())
    raw["resources"][0]["producer"]["recovery"] = {"damaged": value}
    path.write_text(yaml.safe_dump(raw))
    return yaml.safe_dump(raw["resources"][0]["producer"])


@pytest.mark.parametrize("yaml_value", YAML_VALUES)
def test_full_and_public_json_preserve_invalid_raw_ledger(
    owner: tuple, yaml_value: str,
) -> None:
    path, _ = owner
    reserve(path)
    raw_metadata = damage(path, yaml.safe_load(yaml_value))
    record = tracking.load_record(path)
    claim = record.resources[0]
    before = path.read_bytes()
    assert not producer.valid_metadata(claim.producer)
    for projection in (claim.to_dict(), claim.to_public_dict()):
        rendered = json.loads(json.dumps(projection, allow_nan=False))
        assert rendered["producer"] == ERROR
        assert tracking._parse_claim_mapping(rendered).is_unsettled
    assert path.read_bytes() == before
    assert yaml.safe_dump(claim.producer) == raw_metadata
    assert claim.has_producer and claim.is_unsettled
    tracking.save_record(record, path)
    current = tracking.load_record(path).resources[0]
    assert yaml.safe_dump(current.producer) == raw_metadata
    assert current.has_producer and current.is_unsettled


def test_valid_wire_metadata_is_unchanged_and_not_a_mutable_ledger_alias(owner: tuple) -> None:
    path, _ = owner
    pending = reserve(path)
    claim = tracking.load_record(path).resources[0]
    for projection in (claim.to_dict(), claim.to_public_dict()):
        assert projection["producer"] == pending["producer"]
        projection["producer"]["recovery"]["changed"] = True
        assert claim.producer == pending["producer"]
    legacy = tracking.ResourceClaim(kind="container", ref="container:fleet")
    assert "producer" not in legacy.to_dict()
    assert "producer" not in legacy.to_public_dict()


def test_actual_claim_show_json_reports_error_without_losing_obligation(
    owner: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = owner
    reserve(path)
    damage(path, yaml.safe_load("2026-10-12"))
    before = path.read_bytes()
    monkeypatch.setattr(
        claims_cli.state_root_mod, "coordination_readiness",
        lambda config: SimpleNamespace(as_dict=lambda: {"ready": True}),
    )
    monkeypatch.setattr(
        claims_cli, "_core_helper",
        lambda name, fallback: lambda *args: {"available": True, "assigned": [], "owned": []},
    )
    with output.capture_json_output() as captured:
        assert claims_cli._claims_show(argparse.Namespace(json=True), "owner") == 0
    rendered = json.loads(captured.getvalue())
    assert rendered["outbound"][0]["producer"] == ERROR
    assert path.read_bytes() == before
    assert tracking.load_record(path).resources[0].is_unsettled


def test_actual_status_bundle_survives_later_json_encoding(
    owner: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import __main__ as main

    path, _ = owner
    reserve(path)
    damage(path, yaml.safe_load("!!binary aGVsbG8="))
    before = path.read_bytes()
    _wire_common_internals(monkeypatch, main)
    bundle = main._worktree_status_compute("p", "owner")
    rendered = json.loads(json.dumps(bundle, allow_nan=False))
    claims = rendered["facts"]["claims"]
    assert claims["confirmed"] is True
    assert claims["value"]["resources"][0]["producer"] == ERROR
    assert path.read_bytes() == before
    assert tracking.load_record(path).resources[0].is_unsettled
