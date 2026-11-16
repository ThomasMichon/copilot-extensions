"""Credential-read activation remains an explicit caller choice."""

from __future__ import annotations

import io
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from agent_vault import cache, cli, config


@pytest.fixture
def isolated_reads(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    persistent = SimpleNamespace(enabled=False, replacement_lock=nullcontext)
    monkeypatch.setattr(cache, "get_cache", lambda: persistent)
    monkeypatch.setattr(config, "resolve_context", lambda: SimpleNamespace(group="Example"))
    monkeypatch.setattr(
        cli, "ensure_service", lambda: pytest.fail("read attempted service activation"),
    )
    return persistent


def _read(
    monkeypatch: pytest.MonkeyPatch, command: str, *options: str,
) -> int:
    if command == "get":
        return cli.main(["get", "service", *options])
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO('[{"entry":"service"}]'))
    return cli.main(["batch-get", *options])


@pytest.mark.parametrize("command", ["get", "batch-get"])
@pytest.mark.parametrize("running", [None, {"ok": False}, {"ok": True, "accepting_work": False}])
def test_unavailable_or_draining_service_never_activates(
    isolated_reads: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], command: str, running: object,
) -> None:
    calls = []

    def send(request: dict, **options: object) -> object:
        calls.append(request)
        assert request == {"action": "ping"}
        assert options == {"timeout": 5.0, "strict_endpoint": True}
        return running

    monkeypatch.setattr(cli, "send_command", send)
    assert _read(monkeypatch, command, "--require-running") == 1
    captured = capsys.readouterr()
    assert "must already be running" in captured.out + captured.err
    assert calls == [{"action": "ping"}]
    if command == "batch-get":
        assert json.loads(captured.out)["results"][0]["value"] is None


@pytest.mark.parametrize("command", ["get", "batch-get"])
@pytest.mark.parametrize("response", [{"ok": True, "value": "synthetic-value"}, None])
def test_selected_service_read_or_loss_never_falls_back_to_activation(
    isolated_reads: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], command: str, response: dict | None,
) -> None:
    actions = []

    def send(request: dict, **options: object) -> dict | None:
        actions.append(request["action"])
        assert options["strict_endpoint"] is True
        if request["action"] == "ping":
            return {"ok": True}
        assert request == {
            "action": "get", "entry": "service", "field": "password", "refresh": True,
        }
        return response

    monkeypatch.setattr(cli, "send_command", send)
    assert _read(monkeypatch, command, "--require-running", "--refresh") == (
        0 if response else 1
    )
    captured = capsys.readouterr()
    assert actions == ["ping", "get"]
    assert "synthetic-value" not in captured.err
    if response:
        assert "synthetic-value" in captured.out


@pytest.mark.parametrize("command", ["get", "batch-get"])
def test_cached_read_needs_neither_running_service_nor_activation(
    isolated_reads: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], command: str,
) -> None:
    isolated_reads.enabled = True
    isolated_reads.get = lambda *_a, **_kw: "synthetic-cached-value"
    monkeypatch.setattr(
        cli, "send_command", lambda *_a, **_kw: pytest.fail("cache hit contacted service"),
    )
    assert _read(monkeypatch, command, "--require-running") == 0
    assert "synthetic-cached-value" in capsys.readouterr().out


def test_prompt_is_explicit_and_does_not_authorize_service_activation(
    isolated_reads: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def send(request: dict, **options: object) -> dict:
        assert options["strict_endpoint"] is True
        if request["action"] == "ping":
            return {"ok": True}
        assert request["allow_prompt"] is True
        return {"ok": False, "error": "locked"}

    monkeypatch.setattr(cli, "send_command", send)
    assert _read(monkeypatch, "get", "--require-running", "--prompt") == 1


@pytest.mark.parametrize("command", ["get", "batch-get"])
def test_legacy_read_keeps_existing_activation_and_transport_defaults(
    isolated_reads: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, command: str,
) -> None:
    calls = []
    monkeypatch.setattr(cli, "ensure_service", lambda: calls.append("ensure") or True)

    def send(request: dict, **options: object) -> dict:
        calls.append(request["action"])
        assert options == {"timeout": None}
        return {"ok": True, "value": "synthetic-value"}

    monkeypatch.setattr(cli, "send_command", send)
    assert _read(monkeypatch, command) == 0
    assert calls == ["ensure", "get"]
