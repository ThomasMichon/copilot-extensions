"""Legacy presentation recovery exercises the actual registry admission owner."""

from pathlib import Path
from typing import Any

import pytest
from agent_remote_driver.mux_observation_types import Observation, Pane

from worktree_manager import mux_daemon, mux_discovery
from worktree_manager.mux_mapping_registry import MuxMappingRegistry


def _payload(path: Path) -> dict[str, Any]:
    return {
        "project": "project", "worktree_id": "one", "worktree_path": str(path),
        "mux_session": "wt-one", "mux_bin": "psmux.exe",
        "session_incarnation": "$7:100", "attached_clients": 1,
    }


def test_real_legacy_admission_recovers_and_same_name_replacement_advances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = MuxMappingRegistry(tmp_path / "mappings.json")
    payload = _payload(tmp_path)
    assert registry.register({**payload, "session_incarnation": "legacy-launch",
                              "mapping_revision": 9})["applied"]
    monkeypatch.setattr(mux_discovery, "observe_psmux", lambda *a, **k: Observation(
        "present", incarnation=payload["session_incarnation"], clients=1,
        panes=(Pane("%1", False, True, str(tmp_path)),),
    ))
    # Ordinary discovery still retains its incumbent guard.
    assert mux_discovery.discover_mapping(registry, payload)["reason"] == "incumbent-mapping"
    compute = mux_daemon.build_compute(registry)
    admitted = compute(mux_discovery.KIND, payload)
    assert admitted["applied"] and admitted["entry"]["mapping_revision"] == 10
    assert admitted["entry"]["session_incarnation"] == "$7:100"
    current = registry.get("project", "one")
    assert current is not None and current["native_session_incarnation"] == "$7:100"
    assert current["last_status_rendered_at"] is None
    payload["session_incarnation"] = "$8:200"
    replacement = compute(mux_discovery.KIND, payload)
    assert replacement["applied"] and replacement["entry"]["mapping_revision"] == 11
    assert replacement["entry"]["session_incarnation"] == "$8:200"


@pytest.mark.parametrize("held", ["tombstone", "foreign-path", "foreign-session", "unknown-native"])
def test_recovery_keeps_incumbent_and_native_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: str,
) -> None:
    registry = MuxMappingRegistry(tmp_path / "mappings.json")
    payload = _payload(tmp_path)
    incumbent = {**payload, "session_incarnation": "legacy-launch", "mapping_revision": 9}
    if held == "tombstone":
        incumbent["live"] = False
    elif held == "foreign-path":
        incumbent["worktree_path"] = str(tmp_path / "other")
    elif held == "foreign-session":
        incumbent["mux_session"] = "wt-other"
    assert registry.register(incumbent)["applied"]
    before = registry.get("project", "one")
    monkeypatch.setattr(mux_discovery, "observe_psmux", lambda *a, **k: (
        Observation("unknown", "timeout") if held == "unknown-native" else Observation(
            "present", incarnation="$7:100", clients=1,
            panes=(Pane("%1", False, True, str(tmp_path)),),
        )
    ))
    assert not mux_daemon.build_compute(registry)(mux_discovery.KIND, payload)["applied"]
    assert registry.get("project", "one") == before


def test_native_recheck_cannot_overwrite_concurrent_incumbent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = MuxMappingRegistry(tmp_path / "mappings.json")
    payload = _payload(tmp_path)
    assert registry.register({**payload, "session_incarnation": "legacy-launch",
                              "mapping_revision": 9})["applied"]
    calls = 0

    def observe(*args: Any, **kwargs: Any) -> Observation:
        nonlocal calls
        calls += 1
        if calls == 2:
            assert registry.register({**payload, "session_incarnation": "new-owner",
                                      "mapping_revision": 10})["applied"]
        return Observation(
            "present", incarnation="$7:100", clients=1,
            panes=(Pane("%1", False, True, str(tmp_path)),),
        )

    monkeypatch.setattr(mux_discovery, "observe_psmux", observe)
    result = mux_daemon.build_compute(registry)(mux_discovery.KIND, payload)
    assert not result["applied"] and result["reason"] == "mapping-changed"
    current = registry.get("project", "one")
    assert current is not None and current["session_incarnation"] == "new-owner"
