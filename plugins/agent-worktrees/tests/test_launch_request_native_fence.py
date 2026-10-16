"""Structured seed staging negotiates the same native-provider admission fence."""

from __future__ import annotations

import pytest

from agent_worktrees import launch_request, locks, status_monitor_runtime, tracking_write


@pytest.mark.parametrize("version", [2, 3])
def test_structured_staging_never_dials_a_pre_native_fence_authority(
    monkeypatch: pytest.MonkeyPatch, version: int,
) -> None:
    endpoint = {
        "tracking_write_endpoint": "127.0.0.1:49152",
        "tracking_write_token": "fixture-token",
        "tracking_write_verbs": ["launch_request_stage"],
        "tracking_write_verb_versions": {"launch_request_stage": version},
    }
    monkeypatch.setattr(locks, "read_lock", lambda *_: endpoint)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: False)
    monkeypatch.setattr(
        tracking_write, "_send_tracking_write_request",
        lambda *args, **kwargs: pytest.fail("dialed a pre-native-fence wrapper"),
    )
    direct: list[str] = []
    monkeypatch.setattr(
        tracking_write, "run_direct",
        lambda verb, args, **kwargs: direct.append(verb) or {"accepted": True},
    )
    assert launch_request._dispatch("launch_request_stage", {}) == {"accepted": True}
    assert direct == ["launch_request_stage"]


def test_structured_staging_advertises_native_fence_protocol() -> None:
    assert tracking_write._VERB_VERSIONS["launch_request_stage"] == 4
