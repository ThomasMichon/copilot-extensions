"""Actual retained CLI transition, fresh readers and native epoch separation."""

from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any

import pytest

from agent_pull_requests.native_read import NativeReadHandler
from agent_pull_requests import watch_replay_store
from agent_pull_requests.terminal_retention import replay_epoch
from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_replay import ObservationTarget
from agent_pull_requests_service.__main__ import main
from agent_pull_requests_service.config import HostConfig, load_config

if TYPE_CHECKING:
    from services.pr.tests.test_host import Configuration, configuration as configuration, workspace_config
    from services.pr.tests.test_retained_live_cache import generation, materialized, reader
    from services.pr.tests.test_terminal_transition import cache_bytes, command
else:
    from test_host import Configuration, configuration as configuration, workspace_config
    from test_retained_live_cache import generation, materialized, reader
    from test_terminal_transition import cache_bytes, command

Document = dict[str, Any]


def recorded(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Document, HostConfig, list[Document]]:
    path, value = workspace_config(configuration)
    value["workspaces"]["generation_associations"] = True
    path.write_text(json.dumps(value))
    provisioned = subprocess.run(
        [sys.executable, "-m", "agent_pull_requests_service", "provision", "--config", str(path)],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW") if os.name == "nt" else 0,
    )
    assert provisioned.returncode == 0, provisioned.stderr
    baseline = load_config(path)
    values = [
        generation(0, inner_offset=0.1, padding=12000),
        generation(0, inner_offset=0.2, padding=12000),
    ]
    current: Document = values[0]
    monkeypatch.setattr(NativeReadHandler, "read", lambda *_args, **_kwargs: {
        "generation": copy.deepcopy(current), "acquisition": {"source": "synthetic-owner"},
    })
    previous = None
    for current in values:
        result = reader(baseline).execute("load", {
            "number": current["scope"]["number"], "expected_current": previous, "budget_seconds": 30,
        }, expires_at=int(time.time()) + 60, checkpoint=lambda: None)
        assert result["result"]["publication"]["status"] == "published"
        previous = current["id"]
    value["workspaces"]["terminal_reads"] = True
    path.write_text(json.dumps(value))
    return path, value, baseline, values


def test_actual_transition_and_fresh_reader_preserve_all_original_bytes(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, value, baseline, values = recorded(configuration, monkeypatch)
    expected = [materialized(reader(baseline), selected) for selected in values]
    before = json.loads(baseline.store().path.read_bytes())
    objects = cache_bytes(baseline)
    assert before["generation_associations"]["records"][0]["cursor"] == (
        before["generation_associations"]["records"][1]["cursor"]
    )
    result = command(path)
    assert result.returncode == 0, result.stderr
    program = """
import base64, json, sys, time
from agent_pull_requests_service.config import load_config
from agent_pull_requests.workspace_operations import CHUNK_BYTES
c = load_config(sys.argv[1])
w = c.workspaces.operations(c.store(), lambda: None, 60)[("caller-a", "repository-a")]
options = {"expires_at": int(time.time()) + 60, "checkpoint": lambda: None}
w.validate_storage(**options)
result = []
requests = json.loads(sys.argv[2])
current = w.execute("lookup", {"number": requests[-1]["number"]}, **options)["result"]["descriptor"]
assert current["id"] == requests[-1]["generation_id"]
for p in requests:
    d = w.execute("lookup", p, **options)["result"]["descriptor"]
    content = b""
    for offset in range(0, d["bytes"], CHUNK_BYTES):
        part = w.execute("chunk", {**p, "offset": offset}, **options)["result"]
        content += base64.b64decode(part["data"])
    result.append([d, base64.b64encode(content).decode()])
print(json.dumps(result))
"""
    restarted = subprocess.run(
        [sys.executable, "-I", "-c", program, str(path), json.dumps([
            {"number": selected["scope"]["number"], "generation_id": selected["id"]}
            for selected in values
        ])],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW") if os.name == "nt" else 0,
    )
    assert restarted.returncode == 0, restarted.stderr
    assert [(d, base64.b64decode(content)) for d, content in json.loads(restarted.stdout)] == expected
    adopted = load_config(path)
    document = json.loads(adopted.store().path.read_bytes())
    assert document["terminal_retention"]["admissions"] == []
    for field in ("replay", "reviews", "conversations", "generation_associations"):
        assert document[field] == before[field]
    assert cache_bytes(adopted) == objects
    assert command(path).returncode == 0
    assert json.loads(adopted.store().path.read_bytes()) == document
    value["workspaces"]["terminal_reads"] = False
    path.write_text(json.dumps(value))
    from agent_pull_requests.watch_replay_store import ReplayStoreInvalid
    with pytest.raises(ReplayStoreInvalid):
        reader(load_config(path)).validate_storage(
            expires_at=int(time.time()) + 60, checkpoint=lambda: None,
        )


def test_retained_sync_uses_terminal_publication_and_keeps_native_pre_io_fence(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _, _, values = recorded(configuration, monkeypatch)
    assert command(path).returncode == 0
    config = load_config(path)
    owner = config.store()
    scope = config.bindings[0].scope
    number = values[0]["scope"]["number"]
    expires = int(time.time()) + 60
    receipt = owner.execute(scope, "register", {
        "request_id": "after-recorded-transition", "issued_at": time.time(),
        "number": number, "until": ("head_changed",), "deadline_at": time.time() + 60,
    }, expires_at=expires)
    target = ObservationTarget(scope.account, scope.repo, number, frozenset({receipt["registration_id"]}))
    assert replay_epoch(json.loads(owner.path.read_bytes()), target) is None
    latest = generation(2, padding=12000)
    monkeypatch.setattr(NativeReadHandler, "read", lambda *_args, **_kwargs: {
        "generation": copy.deepcopy(latest), "acquisition": {"source": "synthetic-owner"},
    })
    result = reader(config).execute("sync", {
        "number": number, "previous_id": values[0]["id"],
        "expected_current": values[-1]["id"], "budget_seconds": 30,
    }, expires_at=expires, checkpoint=lambda: None)["result"]
    assert result["publication"]["status"] == "published"
    assert result["comparison"]["generation_id"] == latest["id"]
    document = json.loads(owner.path.read_bytes())
    assert len(document["generation_associations"]["records"]) == 2
    assert len(document["terminal_retention"]["admissions"]) == 1
    assert replay_epoch(document, target) is not None
    assert owner.observe(target, PRSnapshot(head_sha="a" * 40), expires_at=expires) == {
        "status": "stale_observation",
    }
    assert command(path).returncode == 0
    for selected in [*values, latest]:
        assert materialized(reader(load_config(path)), selected)[0]["id"] == selected["id"]


def test_combined_selection_refuses_writer_only_core_and_fresh_provision(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, value = workspace_config(configuration)
    value["workspaces"].update(generation_associations=True, terminal_reads=True)
    path.write_text(json.dumps(value))
    selected = load_config(path)
    with pytest.raises(ValueError, match="existing retained volume"):
        selected.provision()
    assert not Path(value["root"]).exists()
    from agent_pull_requests import recorded_terminal_cache
    monkeypatch.setattr(recorded_terminal_cache, "contract", lambda: {"protocol": "unsupported"})
    with pytest.raises(ValueError, match="core unsupported"):
        load_config(path)
    assert main(["adopt-terminal", "--config", str(path)]) == 1
    assert not Path(value["root"]).exists()


@pytest.mark.parametrize("uncertain", [False, True])
@pytest.mark.parametrize("boundary", ["retention", "recovery", "cache"])
def test_actual_adoption_boundaries_reconcile_original_recorded_bindings(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
    uncertain: bool, boundary: str,
) -> None:
    path, _, baseline, values = recorded(configuration, monkeypatch)
    original = json.loads(baseline.store().path.read_bytes())
    objects = cache_bytes(baseline)
    write = watch_replay_store._atomic_write_json
    marker = {
        "retention": "terminal_retention_version", "recovery": "terminal_recovery_version",
        "cache": "terminal_cache_retirement_version",
    }[boundary]

    def interrupted(destination: Path, value: Document) -> None:
        if marker in value:
            if uncertain:
                write(destination, value)
            raise OSError("synthetic original adoption interruption")
        write(destination, value)

    with monkeypatch.context() as patch:
        patch.setattr(watch_replay_store, "_atomic_write_json", interrupted)
        assert main(["adopt-terminal", "--config", str(path)]) == 1
    result = command(path)
    assert result.returncode == 0, result.stderr
    adopted = load_config(path)
    after = json.loads(adopted.store().path.read_bytes())
    assert cache_bytes(adopted) == objects
    for field in ("replay", "conversations", "generation_associations"):
        assert after[field] == original[field]
    assert after["terminal_retention"]["admissions"] == []
    assert len(after["terminal_retention"]["generations"]) == 2
    for selected in values:
        assert materialized(reader(adopted), selected)[0]["id"] == selected["id"]
