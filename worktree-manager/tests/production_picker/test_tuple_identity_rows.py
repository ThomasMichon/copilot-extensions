"""Tuple metadata stays separate from the Picker's declared transport/source identity."""

from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from worktree_manager import engine_client as ec
from worktree_manager.production_picker.picker_tui import data_local, data_ssh, derive
from worktree_manager.production_picker.picker_tui.engine_worktree_actions import (
    PickerScreenWorktreeActionsMixin,
)
from worktree_manager.worktree_rows_contract import CurrentSnapshot, parse_current_rows


def tuple_row():
    return {
        "id": "historical-guest-aaaa", "repo": "project", "state": "wip",
        "machine_identity_version": 1, "machine": "physical-host-wsl",
        "environment": "wsl", "execution_machine": "custom-guest", "platform": "wsl",
    }


def envelope(rows):
    return {
        "ok": True, "contract_version": 1, "worktrees": rows,
        "projection": {
            "schema_version": 1, "quality": "current", "generation": "a" * 32,
            "revision": 1, "observed_at": 123, "roster_count": len(rows),
            "selected_count": len(rows),
        },
    }


def action_engine():
    class Harness(PickerScreenWorktreeActionsMixin):
        src = SimpleNamespace(REPO="project")
        app = SimpleNamespace(notify=Mock())
        _run_bg = Mock()

    return Harness()


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_typed_model_uses_execution_owner_without_rewriting_raw_tuple(environment):
    raw = tuple_row()
    raw.update(environment=environment, platform=environment)
    before = deepcopy(raw)
    model = ec.worktree_from_dict(raw)
    assert model.machine == "custom-guest"
    assert model.raw is raw
    assert model.raw == before


@pytest.mark.parametrize("machine", ["legacy-owner", None, "", 17])
def test_legacy_model_keeps_historical_coercion_and_normalized_shape(machine):
    raw = {"id": "legacy", "machine": machine, "environment": "ignored", "platform": "wsl"}
    assert ec.worktree_from_dict(raw).machine == str(machine or "")
    normalized = derive.norm(raw, "selected-owner", "WSL")
    assert normalized["machine"] == "selected-owner"
    assert "machine_identity" not in normalized
    assert action_engine()._wt_action_ctx({"raw": raw})["machine"] == str(machine or "")


@pytest.mark.parametrize("local", [True, False])
def test_actual_current_json_load_keeps_declared_source_and_full_tuple(monkeypatch, local):
    raw = tuple_row()
    before = deepcopy(raw)
    payload = json.dumps(envelope([raw]))
    calls = []

    def runner(command, timeout):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, payload, "")

    if local:
        monkeypatch.setattr(ec, "engine_base_command", lambda: ["synthetic-engine"])
        monkeypatch.setattr(data_local.context, "project", lambda: "project")
        rows = data_local.load("custom-guest", "WSL", runner=runner)
    else:
        source = data_ssh.Source("custom-guest", "WSL", ["synthetic-engine"], alias="peer")
        rows = data_ssh._fetch(source, runner=runner)
    assert len(calls) == 1 and len(rows) == 1
    row = rows[0]
    assert row["machine"] == "custom-guest" and row["env"] == "WSL"
    assert row["source_id"] == data_ssh.machine_source_id("custom-guest", "WSL")
    assert row["machine_identity"] == {
        key: raw[key] for key in (
            "machine_identity_version", "machine", "environment",
            "execution_machine", "platform",
        )
    }
    assert all(row["raw"][key] == value for key, value in before.items())
    assert raw == before


def test_action_context_uses_execution_fallback_and_keeps_physical_metadata():
    raw = tuple_row()
    before = deepcopy(raw)
    context = action_engine()._wt_action_ctx({"raw": raw})
    assert context["machine"] == "custom-guest"
    assert context["physical_machine"] == "physical-host-wsl"
    assert context["execution_machine"] == "custom-guest"
    assert context["environment"] == "wsl"
    assert raw == before


def test_tuple_cannot_retarget_an_already_selected_source():
    raw = tuple_row()
    selected = derive.norm(raw, "declared-source-alias", "WSL")
    assert selected["machine"] == "declared-source-alias"
    assert selected["source_id"] == data_ssh.machine_source_id("declared-source-alias", "WSL")
    context = action_engine()._wt_action_ctx(selected)
    assert context["machine"] == "declared-source-alias"
    assert context["physical_machine"] == "physical-host-wsl"
    assert context["execution_machine"] == "custom-guest"


@pytest.mark.parametrize("boundary", ["model", "current", "normalize", "action"])
@pytest.mark.parametrize("field,value", [
    ("machine_identity_version", True), ("machine_identity_version", 2),
    ("machine", ""), ("environment", "win"), ("environment", "future"),
    ("execution_machine", None), ("execution_machine", " guest"),
    ("platform", "windows"),
])
def test_malformed_tuple_refuses_before_model_filter_or_action_context(boundary, field, value):
    raw = tuple_row()
    raw[field] = value
    before = deepcopy(raw)
    with pytest.raises(ValueError, match="machine identity"):
        if boundary == "model":
            ec.worktree_from_dict(raw)
        elif boundary == "current":
            parse_current_rows(envelope([{"id": "valid-legacy"}, raw]))
        elif boundary == "normalize":
            derive.norm(raw, "selected-owner", "WSL")
        else:
            action_engine()._wt_action_ctx({"machine": "selected-owner", "raw": raw})
    assert raw == before


def test_invalid_current_json_is_an_explicit_engine_error(monkeypatch):
    raw = tuple_row()
    del raw["execution_machine"]
    monkeypatch.setattr(ec, "engine_base_command", lambda: ["synthetic-engine"])
    with pytest.raises(ec.EngineError, match="machine identity execution_machine"):
        ec.list_worktree_rows(
            "project",
            runner=lambda command, timeout: subprocess.CompletedProcess(
                command, 0, json.dumps(envelope([raw])), "",
            ),
        )


def test_invalid_tuple_cannot_complete_a_current_stream_publication():
    raw = tuple_row()
    value = envelope([{"id": "valid-legacy"}, raw])
    snapshot = CurrentSnapshot()
    assert snapshot.receive({
        "type": "begin", "contract_version": 1, "projection": value["projection"],
        "selected_count": 2,
    }) is None
    assert snapshot.receive({
        "type": "worktree", "wt": value["worktrees"][0], "projection": value["projection"],
    }) is None
    raw["machine_identity_version"] = True
    with pytest.raises(ValueError, match="machine identity"):
        snapshot.receive({
            "type": "worktree", "wt": raw, "projection": value["projection"],
        })
    assert snapshot._complete is False


def test_invalid_action_is_visibly_refused_before_background_or_template_effects():
    raw = tuple_row()
    raw["machine_identity_version"] = True
    engine = action_engine()
    engine._run_wt_action(
        SimpleNamespace(label="Inspect", source="sample"),
        {"machine": "selected-owner", "raw": raw},
    )
    engine._run_bg.assert_not_called()
    engine.app.notify.assert_called_once()
    assert "machine identity" in engine.app.notify.call_args.args[0]
    assert engine.app.notify.call_args.kwargs["severity"] == "error"
