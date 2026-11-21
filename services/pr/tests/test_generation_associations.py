"""Explicit fresh-host selection and actual CLI/process compatibility controls."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any

import pytest

from agent_pull_requests import generation_associations
from agent_pull_requests.native_read import NativeReadHandler
from agent_pull_requests.review_workspace import build_generation
from agent_pull_requests.watch_replay_store import ReplayStoreInvalid
from agent_pull_requests_service.__main__ import main
from agent_pull_requests_service.config import load_config

if TYPE_CHECKING:
    from services.pr.tests.test_host import Configuration, configuration as configuration, workspace_config
else:
    from test_host import Configuration, configuration as configuration, workspace_config


def command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW") if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("native_targets", [False, True])
def test_actual_cli_provision_load_and_fresh_reader_preserve_original_generation(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, native_targets: bool,
) -> None:
    path, value = workspace_config(configuration)
    value["workspaces"]["generation_associations"] = True
    if native_targets:
        value["workspaces"]["native_generation_associations"] = True
    path.write_text(json.dumps(value))
    result = command("-m", "agent_pull_requests_service", "provision", "--config", str(path))
    assert result.returncode == 0, result.stderr
    config = load_config(path)
    assert config.workspaces is not None
    evidence_path = (
        Path(__file__).resolve().parents[3]
        / "plugins/agent-pull-requests/docs/review-evidence.example.json"
    )
    generation = build_generation(json.loads(evidence_path.read_bytes()))["generation"]
    monkeypatch.setattr(NativeReadHandler, "read", lambda *_args, **_kwargs: {
        "generation": copy.deepcopy(generation), "acquisition": {"source": "synthetic"},
    })
    workspace = config.workspaces.operations(config.store(), lambda: None, 60)[
        ("caller-a", "repository-a")
    ]
    workspace.validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)
    result_value = workspace.execute("load", {
        "number": generation["scope"]["number"], "expected_current": None, "budget_seconds": 30,
    }, expires_at=int(time.time()) + 60, checkpoint=lambda: None)
    assert result_value["result"]["publication"]["status"] == "published"
    synchronized = workspace.execute("sync", {
        "number": generation["scope"]["number"], "expected_current": generation["id"],
        "previous_id": generation["id"], "budget_seconds": 30,
    }, expires_at=int(time.time()) + 60, checkpoint=lambda: None)
    assert synchronized["result"]["publication"]["status"] == "published"
    assert synchronized["result"]["conversation"]["status"] == "unchanged"
    original = config.store().path.read_bytes()
    program = """
import base64, json, sys, time
from pathlib import Path
from agent_pull_requests_service.config import load_config
from agent_pull_requests.workspace_operations import CHUNK_BYTES
c = load_config(Path(sys.argv[1]))
w = c.workspaces.operations(c.store(), lambda: None, 60)[("caller-a", "repository-a")]
options = {"expires_at": int(time.time()) + 60, "checkpoint": lambda: None}
w.validate_storage(**options)
number = int(sys.argv[2])
d = w.execute("lookup", {"number": number}, **options)["result"]["descriptor"]
data = b""
for offset in range(0, d["bytes"], CHUNK_BYTES):
    part = w.execute("chunk", {"number": number, "generation_id": d["id"], "offset": offset}, **options)
    data += base64.b64decode(part["result"]["data"])
print(json.dumps({"descriptor": d, "generation": json.loads(data)}))
"""
    restarted = command("-c", program, str(path), str(generation["scope"]["number"]))
    assert restarted.returncode == 0, restarted.stderr
    readback = json.loads(restarted.stdout)
    assert readback["generation"] == generation
    assert readback["descriptor"]["id"] == generation["id"]
    assert config.store().path.read_bytes() == original
    row = json.loads(original)["generation_associations"]["records"][0]
    assert row["generation_id"] == generation["id"] and row["publication"] == "confirmed"
    if native_targets:
        assert row["native_target"] == {
            "account": "account-a", "repo": "example/repository", "number": generation["scope"]["number"],
        }
    assert main(["provision", "--config", str(path)]) == 1
    assert config.store().path.read_bytes() == original
    value["workspaces"]["generation_associations"] = False
    value["workspaces"].pop("native_generation_associations", None)
    path.write_text(json.dumps(value))
    old = load_config(path)
    assert old.workspaces is not None
    with pytest.raises(ReplayStoreInvalid, match="reader unavailable"):
        old.workspaces.operations(old.store(), lambda: None, 60)[
            ("caller-a", "repository-a")
        ].validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)
    assert config.store().path.read_bytes() == original


def test_native_selection_refuses_existing_v1_and_old_core_without_adoption(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import generation_native_targets

    path, value = workspace_config(configuration)
    value["workspaces"]["generation_associations"] = True
    path.write_text(json.dumps(value))
    baseline = load_config(path)
    baseline.provision()
    before = baseline.store().path.read_bytes()
    value["workspaces"]["native_generation_associations"] = True
    path.write_text(json.dumps(value))
    selected = load_config(path)
    assert selected.workspaces is not None
    with pytest.raises(ReplayStoreInvalid, match="reader unavailable"):
        selected.workspaces.operations(selected.store(), lambda: None, 60)[
            ("caller-a", "repository-a")
        ].validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)
    assert main(["provision", "--config", str(path)]) == 1
    assert baseline.store().path.read_bytes() == before
    monkeypatch.setattr(generation_native_targets, "contract", lambda: {"protocol": "old"})
    with pytest.raises(ValueError, match="core unsupported"):
        load_config(path)
    value["workspaces"].pop("native_generation_associations")
    path.write_text(json.dumps(value))
    assert load_config(path).workspaces is not None
    assert baseline.store().path.read_bytes() == before


@pytest.mark.parametrize("associations", [False, True])
def test_unselected_native_feature_does_not_send_new_keywords_to_old_core(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, associations: bool,
) -> None:
    from agent_pull_requests_service import config as hosting
    from agent_pull_requests.watch_replay_store import ReplayStore
    from agent_pull_requests.workspace_operations import WorkspaceOperations

    path, value = workspace_config(configuration)
    if associations:
        value["workspaces"]["generation_associations"] = True
        path.write_text(json.dumps(value))

    def old_store(*args: Any, **kwargs: Any) -> ReplayStore:
        assert "native_generation_associations" not in kwargs
        assert "retained_active_reviews" not in kwargs
        return ReplayStore(*args, **kwargs)

    def old_workspace(*args: Any, **kwargs: Any) -> WorkspaceOperations:
        assert "generation_native_target" not in kwargs
        assert "retained_active_reviews" not in kwargs
        return WorkspaceOperations(*args, **kwargs)

    monkeypatch.setattr(hosting, "ReplayStore", old_store)
    monkeypatch.setattr(hosting, "WorkspaceOperations", old_workspace)
    config = load_config(path)
    config.provision()
    assert config.workspaces is not None
    config.workspaces.operations(config.store(), lambda: None, 60)[
        ("caller-a", "repository-a")
    ].validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)


def test_selection_never_adopts_an_existing_baseline_or_weakens_terminal_refusal(
    configuration: Configuration,
) -> None:
    path, value = workspace_config(configuration)
    baseline = load_config(path)
    baseline.provision()
    original = baseline.store().path.read_bytes()
    value["workspaces"]["generation_associations"] = True
    path.write_text(json.dumps(value))
    selected = load_config(path)
    assert selected.workspaces is not None
    with pytest.raises(ReplayStoreInvalid, match="storage unavailable"):
        selected.workspaces.operations(selected.store(), lambda: None, 60)[
            ("caller-a", "repository-a")
        ].validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)
    assert main(["provision", "--config", str(path)]) == 1
    assert baseline.store().path.read_bytes() == original
    value["workspaces"]["terminal_reads"] = True
    path.write_text(json.dumps(value))
    combined = load_config(path)
    with pytest.raises(ValueError, match="existing retained volume"):
        combined.provision()
    assert main(["adopt-terminal", "--config", str(path)]) == 1
    assert baseline.store().path.read_bytes() == original


def test_unsupported_selected_core_refuses_but_unselected_baseline_still_provisions(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, value = workspace_config(configuration)
    monkeypatch.setattr(generation_associations, "contract", lambda: {"protocol": "unsupported"})
    value["workspaces"]["generation_associations"] = True
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="core unsupported"):
        load_config(path)
    assert not Path(value["root"]).exists()
    value["workspaces"].pop("generation_associations")
    path.write_text(json.dumps(value))
    config = load_config(path)
    config.provision()
    original = config.store().path.read_bytes()
    assert "generation_associations" not in json.loads(original)
    assert config.workspaces is not None
    config.workspaces.operations(config.store(), lambda: None, 60)[
        ("caller-a", "repository-a")
    ].validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)
    assert config.store().path.read_bytes() == original
