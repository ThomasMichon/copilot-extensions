"""Strict payload export/readback commands over an actual owner snapshot."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from test_agenda_v2_core import STORE, lane as lane

CLI = importlib.import_module("agenda_export_cli")

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


def test_actual_export_and_readback_share_a_complete_manifest(
    lane: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str],
) -> None:
    destination = tmp_path_factory.mktemp("agenda-export-cli")
    store = STORE.read(lane)["store_id"]
    selection = ["--destination", str(destination), "--worktree", "fixture-lane",
                 "--store-id", store, "--expected-revision", "1"]
    assert CLI.main(["export", "--state-home", str(lane), *selection]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["ok"] is True and exported["schema"] == CLI.SCHEMA
    before = {p.name: p.read_bytes() for p in destination.iterdir() if p.is_file()}
    assert CLI.main(["verify", *selection]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["manifest"] == exported["manifest"]
    assert {p.name: p.read_bytes() for p in destination.iterdir() if p.is_file()} == before
    assert CLI.main(["export", "--state-home", str(lane), *selection]) == 2
    refused = json.loads(capsys.readouterr().out)
    assert refused["ok"] is False and "manifest" not in refused
    assert {p.name: p.read_bytes() for p in destination.iterdir() if p.is_file()} == before


def test_capability_is_passive_and_parser_rejects_misplaced_selectors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert CLI.main(["info"]) == 0
    assert json.loads(capsys.readouterr().out)["capabilities"] == [CLI.CAPABILITY]
    with pytest.raises(SystemExit) as failed:
        CLI.main(["info", "--state-home", "not-admitted"])
    assert failed.value.code == 2
