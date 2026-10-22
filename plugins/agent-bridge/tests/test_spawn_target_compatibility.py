"""Preparation metadata survives old-reader rollback and current hydration."""

import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

from agent_bridge import session_core
from agent_bridge.session_manager import SessionManager
from agent_bridge.spawn_target import REQUEST_OVERRIDES_KEY, SpawnTarget


@pytest.fixture
def prior_reader(monkeypatch):
    # Frozen from 3b6a374b622bfd91300c04ec682ed9b7eefb7e1c, before this slice.
    source = Path(__file__).parent / "fixtures" / "spawn_target_http43.py"
    assert hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == (
        "98e5b82ff63ffc528f458c6a7f87d8a2b8859f40b101f644bf2ca54d78d66e66"
    )
    spec = importlib.util.spec_from_file_location("frozen_spawn_target_http43", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module.SpawnTarget


def test_ordinary_target_has_the_preceding_serialized_shape(prior_reader):
    assert json.loads(SpawnTarget(type="ssh", host="example").to_json()) == json.loads(
        prior_reader(type="ssh", host="example").to_json(),
    )


@pytest.mark.parametrize("prepared", [False, True])
def test_preceding_target_reader_hydrates_new_writer_sqlite_rows(
    tmp_db, monkeypatch, prior_reader, prepared,
):
    overrides = {"env": {"KEEP": "value"}, "copilot_args": ["--model", "example"]}
    target = SpawnTarget(
        type="ssh", host="example", cwd="/checkout",
        venue={REQUEST_OVERRIDES_KEY: overrides},
        static_plugin_args={"base": ["--allow-all"], "automatic": ["--plugin-dir", "/old"]}
        if prepared else None,
        local_plugin_preparation=prepared,
        prepared_plugins={"ephemeral": True}, plugin_preparation_request={"ephemeral": True},
    )
    raw = target.to_json()
    data = json.loads(raw)
    assert not set(data) & {
        "static_plugin_args", "local_plugin_preparation", "native_resume_id",
        "prepared_plugins", "plugin_preparation_request",
    }
    assert target.venue == {REQUEST_OVERRIDES_KEY: overrides}
    assert "plugin_preparation" not in overrides
    tmp_db.create_session(
        "rollback", "fixture", None, "/checkout", "ssh", "stopped", time.time(),
        target_json=raw, acp_session_id="retained-native",
    )
    with monkeypatch.context() as previous:
        previous.setattr(session_core, "SpawnTarget", prior_reader)
        recovered = SessionManager(tmp_db).get_session("rollback")
        assert recovered.acp_session_id == "retained-native"
        assert recovered.target.host == "example"
        tmp_db.update_session_target("rollback", recovered.target.to_json(), "/checkout")
    current = SessionManager(tmp_db).get_session("rollback").target
    assert current.static_plugin_args == target.static_plugin_args
    assert current.local_plugin_preparation is prepared
    assert current.prepared_plugins is None and current.plugin_preparation_request is None
    assert current.venue[REQUEST_OVERRIDES_KEY]["env"] == overrides["env"]
    assert current.venue[REQUEST_OVERRIDES_KEY]["copilot_args"] == overrides["copilot_args"]


def test_preparation_metadata_does_not_turn_a_local_target_into_a_provider():
    target = SpawnTarget(
        cwd="/checkout", static_plugin_args={"base": [], "automatic": []},
        local_plugin_preparation=True,
    )
    restored = SpawnTarget.from_json(target.to_json())
    assert restored.local_plugin_preparation
    assert not SessionManager._provider_backed_target(restored)
    restored.static_plugin_args = None
    restored.local_plugin_preparation = False
    assert not SpawnTarget.from_json(restored.to_json()).local_plugin_preparation
    overrides = json.loads(restored.to_json())["venue"][REQUEST_OVERRIDES_KEY]
    assert "plugin_preparation" not in overrides


def test_invalid_preparation_metadata_fails_explicitly():
    with pytest.raises(ValueError, match="preparation metadata"):
        SpawnTarget.from_json(json.dumps({
            "venue": {REQUEST_OVERRIDES_KEY: {"plugin_preparation": "invalid"}},
        }))
