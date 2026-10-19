"""Retain the lowest compatible carrier version after extracting selection."""
from __future__ import annotations

from typing import Any

import pytest

from agent_bridge.remote_operation_versions import request_operation_version


@pytest.mark.parametrize(("payload", "expected"), [
    ({"operation": "session.status"}, 1),
    ({"operation": "session.create"}, 2),
    ({"operation": "session.end"}, 2),
    ({"operation": "session.stop"}, 2),
    ({"operation": "live_session.send"}, 2),
    ({"operation": "session.create", "copilot_args": []}, 3),
    ({"operation": "live_session.send", "qualified_peer": True}, 4),
    ({"operation": "live_session.send", "qualified_peer": True, "wait": False}, 4),
    ({"operation": "live_session.send", "wait": True, "wait_timeout": 0}, 5),
    ({"operation": "live_session.send", "qualified_peer": True, "wait": True}, 5),
])
def test_request_operation_version(payload: dict[str, Any], expected: int) -> None:
    assert request_operation_version(payload) == expected
