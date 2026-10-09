"""Publication-time process identity remains separate from routing generation."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from zdd import diagnostics, routing


@pytest.mark.parametrize(
    "publish",
    [
        routing.publish_active,
        routing.publish_active_with_previous,
        routing.publish_active_with_previous_guarded,
    ],
)
@pytest.mark.parametrize("baseline", [None, "12345"])
def test_publication_captures_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, publish: Callable[..., object],
    baseline: str | None,
) -> None:
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "12345")
    publish(
        tmp_path, bind="127.0.0.1", port=1234, pid=42, process_start_time=baseline,
    )
    table = routing.read_table(tmp_path)
    assert table is not None
    assert table["active"]["process_start_time"] == "12345"
    endpoint = routing.read_active_endpoint(tmp_path, verify_listener=False)
    assert endpoint is not None
    assert endpoint.process_start_time == "12345"


@pytest.mark.parametrize("current", ["67890", None])
@pytest.mark.parametrize(
    "publish",
    [
        routing.publish_active,
        routing.publish_active_with_previous,
        routing.publish_active_with_previous_guarded,
    ],
)
def test_spawn_identity_mismatch_preserves_existing_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, current: str | None,
    publish: Callable[..., object],
) -> None:
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "12345")
    routing.publish_active(tmp_path, bind="127.0.0.1", port=1234, pid=42)
    original = routing.routing_table_path(tmp_path).read_bytes()
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: current)
    kwargs = {"require_expected_active": False} if (
        publish is routing.publish_active_with_previous_guarded
    ) else {}
    with pytest.raises(routing.ActivePublicationRefused, match="process identity"):
        publish(
            tmp_path, bind="127.0.0.1", port=5678, pid=42,
            process_start_time="12345", **kwargs,
        )
    assert routing.routing_table_path(tmp_path).read_bytes() == original


def test_previous_preserves_its_own_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = {42: "12345", 43: "67890"}
    monkeypatch.setattr(diagnostics, "process_start_time", tokens.get)
    old = routing.publish_active(tmp_path, bind="127.0.0.1", port=1234, pid=42)
    active, previous = routing.publish_active_with_previous(
        tmp_path, bind="127.0.0.1", port=5678, pid=43, demote_existing=True,
    )
    assert previous == old
    assert previous.process_start_time == "12345"
    assert active.process_start_time == "67890"
    table = routing.read_table(tmp_path)
    assert table is not None
    assert table["previous"]["process_start_time"] == "12345"


def test_guarded_publication_detects_same_pid_identity_change(tmp_path: Path) -> None:
    old = routing.Endpoint("127.0.0.1", 1234, pid=42, generation=1, process_start_time="1")
    replacement = routing.Endpoint(
        "127.0.0.1", 1234, pid=42, generation=1, process_start_time="2",
    )
    routing.routing_table_path(tmp_path).write_text(
        json.dumps({"active": replacement.to_dict()}),
        encoding="utf-8",
    )
    with pytest.raises(routing.ActivePublicationRefused, match="endpoint changed"):
        routing.publish_active_with_previous_guarded(
            tmp_path, bind="127.0.0.1", port=5678, expected_active=old,
        )


def test_legacy_endpoint_remains_readable() -> None:
    endpoint = routing.Endpoint.from_dict({"bind": "127.0.0.1", "port": 1234, "pid": 42})
    assert endpoint is not None
    assert endpoint.process_start_time is None
    assert "process_start_time" not in endpoint.to_dict()


@pytest.mark.parametrize("token", [0, False, "", [], "not-a-token"])
def test_invalid_publication_identity_does_not_create_route(
    tmp_path: Path, token: object,
) -> None:
    with pytest.raises(ValueError, match="numeric string"):
        routing.publish_active(
            tmp_path, bind="127.0.0.1", port=1234, pid=42, process_start_time=token,
        )
    assert not routing.routing_table_path(tmp_path).exists()


@pytest.mark.parametrize("pid", [None, 0, -1])
def test_explicit_identity_requires_a_positive_pid(tmp_path: Path, pid: int | None) -> None:
    with pytest.raises(ValueError, match="positive pid"):
        routing.publish_active(
            tmp_path, bind="127.0.0.1", port=1234, pid=pid, process_start_time="12345",
        )
    assert not routing.routing_table_path(tmp_path).exists()


@pytest.mark.parametrize("token", [0, False, "", [], "not-a-token"])
def test_malformed_identity_is_not_accepted(token: object) -> None:
    assert routing.Endpoint.from_dict(
        {"bind": "127.0.0.1", "port": 1234, "pid": 42, "process_start_time": token},
    ) is None


def test_unavailable_identity_is_logged_without_breaking_legacy_routing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: None)
    endpoint = routing.publish_active(tmp_path, bind="127.0.0.1", port=1234, pid=42)
    assert endpoint.process_start_time is None
    assert "identity-bound supervision must reject this route" in caplog.text


def test_real_current_process_identity_is_published(tmp_path: Path) -> None:
    token = diagnostics.process_start_time(os.getpid())
    if token is None:
        pytest.skip("process identity backend unavailable on this platform")
    endpoint = routing.publish_active(tmp_path, bind="127.0.0.1", port=1234, pid=os.getpid())
    assert endpoint.process_start_time == token
