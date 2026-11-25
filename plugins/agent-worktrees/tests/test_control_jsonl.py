"""Absolute caller budgets cover connection, send and response acquisition."""

from __future__ import annotations

import json
import socket
from types import SimpleNamespace, TracebackType

import pytest

from agent_worktrees import control_jsonl as control


@pytest.mark.parametrize("absolute,expected", [(True, 0.2), (False, 5.0)])
def test_optional_absolute_deadline_does_not_restart_after_connect(
    monkeypatch: pytest.MonkeyPatch, absolute: bool, expected: float,
) -> None:
    now = [0.0]
    timeouts: list[float] = []

    class Stream:
        def __enter__(self) -> Stream:
            return self

        def __exit__(
            self, exc_type: type[BaseException] | None,
            exc_value: BaseException | None, traceback: TracebackType | None,
        ) -> None:
            pass

        def settimeout(self, value: float) -> None:
            timeouts.append(value)

        def sendall(self, value: bytes) -> None:
            assert json.loads(value)["action"] == "read"

        def recv(self, _size: int) -> bytes:
            return b'{"version":1,"result":{"ok":true}}\n'

    def connect(_address: tuple[str, int], timeout: float) -> Stream:
        assert timeout == (1.0 if absolute else 5.0)
        now[0] += 0.8
        return Stream()

    monkeypatch.setattr(socket, "create_connection", connect)
    result = control.request(
        SimpleNamespace(host="127.0.0.1", port=1234, token="fixture", timeout=5.0),
        "read", {}, control.Codec(4096, json.dumps, json.loads, "fixture"),
        clock=lambda: now[0], deadline=1.0 if absolute else None,
    )
    assert result == {"ok": True}
    assert timeouts == pytest.approx([expected, expected])


def test_expired_connection_budget_sends_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    sent: list[bytes] = []

    class Stream:
        def __enter__(self) -> Stream:
            now[0] = 1.1
            return self

        def __exit__(
            self, exc_type: type[BaseException] | None,
            exc_value: BaseException | None, traceback: TracebackType | None,
        ) -> None:
            pass

        def sendall(self, value: bytes) -> None:
            sent.append(value)

    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: Stream())
    with pytest.raises(control.StreamError, match="deadline expired"):
        control.request(
            SimpleNamespace(host="127.0.0.1", port=1234, token="fixture", timeout=5.0),
            "read", {}, control.Codec(4096, json.dumps, json.loads, "fixture"),
            clock=lambda: now[0], deadline=1.0,
        )
    assert sent == []
