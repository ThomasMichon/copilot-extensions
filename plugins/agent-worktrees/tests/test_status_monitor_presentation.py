"""Successive native sweeps reach the real managed status publication seam."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_remote_driver.mux_observation_types import Observation
from agent_worktrees import __main__ as monitor
from agent_worktrees import mux_link, mux_status_link, status_bar_cli
from agent_worktrees import status_monitor_presentation as presentation
from agent_worktrees.status_monitor_presentation import run_monitor_sweep


class Segments:
    value = "first"

    def observation_blocked(self, project: str, path: str) -> bool:
        return False

    def get(self, path: str) -> str:
        return self.value


def test_successive_native_sweeps_publish_full_payload_and_changed_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = mux_link.ManagedMuxCache()
    assert cache.apply_observation({
        "project": "project", "worktree_id": "one", "worktree_path": str(tmp_path),
        "mux_session": "wt-one", "session_incarnation": "$7:100",
        "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    })["applied"]
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    monkeypatch.setattr(monitor, "_monitor_list_sessions", lambda *a: {"wt-one": (1, "$7:100")})
    monkeypatch.setattr(monitor, "_monitor_mux_set", lambda *a: pytest.fail("native CLI"))
    monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)
    monkeypatch.setattr(monitor, "_wait_for_lifecycle_priority", lambda *a: None)
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "context")
    requests: list[dict[str, Any]] = []

    def publish(payload: dict[str, Any], **kwargs: Any) -> dict[str, bool]:
        requests.append(payload)
        return {"applied": True}

    monkeypatch.setattr(mux_status_link, "push_status_via_daemon", publish)
    segments = Segments()
    ctx_done: set[str] = set()
    published: dict[tuple[str, str], str] = {}
    options = {
        "managed_mux_cache": cache, "segment_cache": segments,
        "published": published, "incarnations": {},
    }
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", ctx_done, **options,
    ) == 1
    segments.value = "second"
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", ctx_done, **options,
    ) == 1
    assert requests[0]["values"] == {
        "@aw_updater": "monitor", "@aw_updater_prefix": "fixture",
        "@aw_seg": "first", "@aw_ctx": "context",
    }
    assert requests[1]["values"] == {"@aw_seg": "second"}
    assert ctx_done == {"wt-one"}
    assert all(request["session_incarnation"] == "$7:100" for request in requests)


def test_unknown_native_observation_does_not_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    monkeypatch.setattr(monitor, "_monitor_list_sessions", lambda *a: None)
    monkeypatch.setattr(
        mux_status_link, "publish_managed_session_status",
        lambda **k: pytest.fail("unknown native observation"),
    )
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(),
        managed_mux_cache=mux_link.ManagedMuxCache(), segment_cache=Segments(),
    ) == -1


@pytest.mark.parametrize("native_present", [True, False])
def test_legacy_mapping_uses_observed_native_discovery_without_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_present: bool,
) -> None:
    cache = mux_link.ManagedMuxCache()
    original: dict[str, Any] = {
        "project": "project", "worktree_id": "one", "worktree_path": str(tmp_path),
        "mux_session": "wt-one", "session_incarnation": "legacy-launch",
        "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    assert cache.apply_observation(original)["applied"]
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    monkeypatch.setattr(monitor, "_monitor_list_sessions", lambda *a: {"wt-one": (1, "$7:100")})
    monkeypatch.setattr(monitor, "_monitor_mux_set", lambda *a: pytest.fail("native CLI"))
    monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)
    monkeypatch.setattr(monitor, "_wait_for_lifecycle_priority", lambda *a: None)
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "new-context")
    monkeypatch.setattr(presentation, "observe_psmux", lambda *a, **k: (
        Observation("present", incarnation="$7:100", clients=1) if native_present
        else Observation("unknown", "timeout")
    ))
    discoveries: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []

    def discover(payload: dict[str, Any]) -> dict[str, Any]:
        discoveries.append(payload)
        return {"applied": True, "entry": {
            **original, "session_incarnation": "$7:100", "mapping_revision": 2,
        }}

    def publish(payload: dict[str, Any], **kwargs: Any) -> dict[str, bool]:
        requests.append(payload)
        return {"applied": True}

    monkeypatch.setattr(mux_status_link, "push_discovery_via_daemon", discover)
    monkeypatch.setattr(mux_status_link, "push_status_via_daemon", publish)
    ctx_done = {"wt-one"}
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", ctx_done,
        managed_mux_cache=cache, segment_cache=Segments(),
        incarnations={"wt-one": "legacy-launch"},
        published={("wt-one", "@aw_ctx"): "old-context", ("wt-one", "@aw_seg"): "first"},
    ) == 1
    if native_present:
        assert discoveries[0]["session_incarnation"] == "$7:100"
        assert requests[0]["mapping_revision"] == 2
        assert requests[0]["session_incarnation"] == "$7:100"
        assert requests[0]["values"]["@aw_ctx"] == "new-context"
    else:
        assert discoveries == [] and requests == []
        retained = cache.get("project", "one")
        assert retained is not None and retained["session_incarnation"] == "legacy-launch"
