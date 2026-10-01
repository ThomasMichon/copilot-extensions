"""Client-side CLI-mode reservation lifecycle gates."""

from __future__ import annotations

import pytest

from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.protocol import CLI_MODE_UNCLAIMED_RELEASE_PROTOCOL_VERSION


def _client_for_protocol(version: int) -> tuple[BridgeClient, list[tuple[str, str]]]:
    client = BridgeClient("http://127.0.0.1:0", "t")
    client.health = lambda: {"protocol_version": version, "min_protocol_version": 1}  # type: ignore[method-assign]
    requests: list[tuple[str, str]] = []

    def _request(method: str, path: str, *args, **kwargs):
        requests.append((method, path))
        return {"removed": 1}

    client._request = _request  # type: ignore[method-assign]
    return client, requests


def test_unclaimed_only_release_sends_delete_to_capable_daemon() -> None:
    client, requests = _client_for_protocol(CLI_MODE_UNCLAIMED_RELEASE_PROTOCOL_VERSION)

    removed = client.release_cli_mode_reservation(
        "wt-A", reservation_id="r1", unclaimed_only=True,
    )

    assert removed == 1
    assert requests == [
        (
            "DELETE",
            "/api/v1/live-sessions/cli-mode-reservations/wt-A?"
            "reservation_id=r1&unclaimed_only=true",
        ),
    ]


def test_unclaimed_only_release_skips_delete_against_older_daemon() -> None:
    client, requests = _client_for_protocol(
        CLI_MODE_UNCLAIMED_RELEASE_PROTOCOL_VERSION - 1,
    )

    with pytest.raises(BridgeClientError) as raised:
        client.release_cli_mode_reservation(
            "wt-A", reservation_id="r1", unclaimed_only=True,
        )

    assert raised.value.status == 426
    assert "Skipping DELETE" in raised.value.detail
    assert requests == []


def test_plain_release_keeps_legacy_behavior_against_older_daemon() -> None:
    client, requests = _client_for_protocol(
        CLI_MODE_UNCLAIMED_RELEASE_PROTOCOL_VERSION - 1,
    )

    removed = client.release_cli_mode_reservation("wt-A", reservation_id="r1")

    assert removed == 1
    assert requests == [
        ("DELETE", "/api/v1/live-sessions/cli-mode-reservations/wt-A?reservation_id=r1"),
    ]
