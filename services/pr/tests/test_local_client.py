"""Client pagination and recovery diagnostics retain durable delivery meaning."""

from pathlib import Path
from typing import Any

import pytest

from agent_pull_requests_service.__main__ import main
from agent_pull_requests_service.local_client import LocalClient, LocalClientError


@pytest.mark.parametrize("status", ["cancelled", "expired"])
def test_follow_drains_all_inactive_delivered_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str,
) -> None:
    acknowledged: list[str] = []
    def request(_self: LocalClient, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if operation == "acknowledge":
            acknowledged.append(payload["event_id"])
            return {"status": "acknowledged"}
        position = len(acknowledged)
        return {
            "status": status, "more": position < 2,
            "events": [{"event_id": f"event-{position}", "sequence": position + 1}],
        }
    monkeypatch.setattr(LocalClient, "request", request)
    assert main([
        "follow", "--socket", str(tmp_path / "local.sock"), "--namespace", "test",
        "--target", "repository", "--registration-id", "original", "--limit", "1",
        "--timeout", "5", "--interval", "1", "--ack",
    ]) == 0
    assert acknowledged == ["event-0", "event-1", "event-2"]


def test_history_gap_cli_diagnostic_does_not_instruct_blind_identity_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def request(_self: LocalClient, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise LocalClientError("repository_history_gap")
    monkeypatch.setattr(LocalClient, "request", request)
    assert main([
        "query", "--socket", str(tmp_path / "local.sock"),
        "--namespace", "test", "--target", "repository",
    ]) == 2
    error = capsys.readouterr().err
    assert "repository_history_gap" in error and "original identities retained" not in error
