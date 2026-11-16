"""Credential-read activation remains an explicit caller choice."""

from __future__ import annotations

import io
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_vault import cache, cli, config, extensions, rendezvous


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
        result = cli.main(["get", "service", *options])
    else:
        monkeypatch.setattr(sys, "stdin", io.StringIO('[{"entry":"service"}]'))
        result = cli.main(["batch-get", *options])
    assert isinstance(result, int)
    return result


@pytest.mark.parametrize("command", ["get", "batch-get"])
@pytest.mark.parametrize("running", [None, {"ok": False}, {"ok": True, "accepting_work": False}])
def test_unavailable_or_draining_service_never_activates(
    isolated_reads: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], command: str, running: object,
) -> None:
    calls = []

    def send(request: dict[str, Any], **options: object) -> object:
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
    capsys: pytest.CaptureFixture[str], command: str, response: dict[str, Any] | None,
) -> None:
    actions = []

    def send(request: dict[str, Any], **options: object) -> dict[str, Any] | None:
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
    def send(request: dict[str, Any], **options: object) -> dict[str, Any]:
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
    def ensure() -> bool:
        calls.append("ensure")
        return True

    monkeypatch.setattr(cli, "ensure_service", ensure)

    def send(request: dict[str, Any], **options: object) -> dict[str, Any]:
        calls.append(request["action"])
        assert options == {"timeout": None}
        return {"ok": True, "value": "synthetic-value"}

    monkeypatch.setattr(cli, "send_command", send)
    assert _read(monkeypatch, command) == 0
    assert calls == ["ensure", "get"]


@pytest.mark.parametrize("response", [None, {"ok": True, "value": "selected-value"}])
def test_real_transport_keeps_strict_tcp_selection_off_legacy_socket(
    isolated_reads: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    response: dict[str, Any] | None,
) -> None:
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "IS_WSL", False)
    monkeypatch.setenv("AGENT_VAULT_ENDPOINT", "tcp:127.0.0.1:12345")
    monkeypatch.setattr(config, "resolve_context", lambda: SimpleNamespace(
        kpdb=None, group="Example", vault_name=None, port=10170,
        sources={"port": "default"},
    ))
    registry = extensions.ExtensionRegistry()
    registry._loaded = True
    monkeypatch.setattr(extensions, "_REGISTRY", registry)
    monkeypatch.setattr(
        cli, "_send_socket", lambda *_a, **_kw: pytest.fail("selected TCP dial used legacy socket"),
    )
    calls = []

    def tcp(
        request: dict[str, Any], host: str, port: int, timeout: float | None,
    ) -> dict[str, Any] | None:
        calls.append((request["action"], host, port))
        return response

    monkeypatch.setattr(cli, "_send_tcp", tcp)
    actual = cli.send_command({"action": "get"}, strict_endpoint=True)
    assert actual == (
        {**response, "_transport": "discovered-tcp"} if response is not None else None
    )
    assert calls == [("get", "127.0.0.1", 12345)]


@pytest.mark.parametrize("with_extension", [False, True])
def test_unsupported_native_selection_refuses_but_keeps_configured_extension_priority(
    isolated_reads: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    with_extension: bool,
) -> None:
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "IS_WSL", False)
    monkeypatch.setattr(config, "resolve_context", lambda: SimpleNamespace(
        kpdb=None, group="Example", vault_name=None, port=10170,
        sources={"port": "default"},
    ))
    endpoint = rendezvous.Endpoint.parse(r"pipe:\\.\pipe\synthetic-selected-vault")
    monkeypatch.setattr(cli, "_discover_endpoint", lambda *_a, **_kw: endpoint)
    registry = extensions.ExtensionRegistry()
    registry._loaded = True
    if with_extension:
        registry.register_transport(
            lambda *_args: {"ok": True, "value": "extension-value"}, before_builtin=True,
        )
    monkeypatch.setattr(extensions, "_REGISTRY", registry)
    for name in ("_send_socket", "_send_tcp"):
        monkeypatch.setattr(
            cli, name, lambda *_a, **_kw: pytest.fail("unsupported selection dialed another daemon"),
        )
    actual = cli.send_command({"action": "get"}, strict_endpoint=True)
    if with_extension:
        assert actual is not None and actual["value"] == "extension-value"
    else:
        assert actual is None


@pytest.mark.parametrize("case", ["unreachable", "local-foreign", "windows-foreign"])
def test_present_unusable_advertisement_never_becomes_legacy_discovery(
    isolated_reads: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, case: str,
) -> None:
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "IS_WSL", True)
    monkeypatch.delenv("AGENT_VAULT_ENDPOINT", raising=False)
    monkeypatch.setattr(config, "resolve_context", lambda: SimpleNamespace(
        kpdb=None, group="Example", vault_name=None, port=10170,
        sources={"port": "default"},
    ))
    local, windows = tmp_path / "local", tmp_path / "windows"
    local.mkdir()
    windows.mkdir()
    monkeypatch.setattr(config, "run_dir", lambda: local)
    monkeypatch.setattr(config, "installation_id", lambda: "expected/agent-vault")
    monkeypatch.setattr(cli, "_windows_run_dirs", lambda: [windows])
    rendezvous.write_endpoint(
        windows if case == "windows-foreign" else local,
        "tcp", "127.0.0.1:12345",
        installation_id="expected/agent-vault" if case == "unreachable" else "other/agent-vault",
    )
    monkeypatch.setattr(rendezvous, "connect_probe", lambda _endpoint: False)
    registry = extensions.ExtensionRegistry()
    registry._loaded = True
    monkeypatch.setattr(extensions, "_REGISTRY", registry)
    for name in ("_send_socket", "_send_tcp"):
        monkeypatch.setattr(
            cli, name, lambda *_a, **_kw: pytest.fail("unusable advertisement used another daemon"),
        )
    with pytest.raises(rendezvous.EndpointUnavailable, match="Selected vault endpoint"):
        cli.send_command({"action": "get"}, strict_endpoint=True)
