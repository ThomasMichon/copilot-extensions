"""Selected-service maintenance never depends on caller-side protected storage."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from agent_vault import cache, cache_maintenance, cache_targets, cli, config, extensions, service
from agent_vault.invocation import use_configuration_root
from agent_vault.rendezvous import Endpoint
from agent_vault.service_connections import VaultConnections

if TYPE_CHECKING:
    from test_cache_targets import selected as selected, write
    from test_service_cache import cached_service as cached_service
else:
    from .test_cache_targets import selected as selected, write
    from .test_service_cache import cached_service as cached_service

SelectedFixture = tuple[Path, Path, extensions.ExtensionRegistry]
ServiceFixture = tuple[service.VaultService, cache.PersistentCache, str, list[float]]


def configure(
    selected: SelectedFixture, monkeypatch: pytest.MonkeyPatch, *, entries: bool = True,
) -> Path:
    repository, _caller, _registry = selected
    write(repository, {
        "cache_maintenance": "selected-service",
        "cache_entries": [["/Account", "UserName"], "Account"] if entries else [],
    })
    monkeypatch.setattr(cache, "get_cache", lambda: pytest.fail("client touched its protected cache"))
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("client activated a service"))
    return repository


def native_transport(
    monkeypatch: pytest.MonkeyPatch,
    respond: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> None:
    endpoint = Endpoint("tcp", "127.0.0.1:10170", source="fixture")
    monkeypatch.setattr(cli, "_discover_endpoint", lambda *_a, **_kw: endpoint)
    monkeypatch.setattr(cli, "_send_tcp", lambda request, *_a, **_kw: respond(request))
    monkeypatch.setattr(
        cli, "_send_socket", lambda *_a, **_kw: pytest.fail("client tried another route"),
    )


@pytest.mark.parametrize("caller_database", [True, False])
def test_real_cli_and_json_transport_use_service_records_after_password_expiry(
    selected: SelectedFixture, cached_service: ServiceFixture,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], caller_database: bool,
) -> None:
    repository = configure(selected, monkeypatch)
    vault, _persistent, kpdb, clock = cached_service
    monkeypatch.setattr(vault.cli, "get_entry", lambda _db, entry, field: entry + ":" + field)
    context_reads: list[bool] = []
    discoveries: list[bool] = []

    async def exercise() -> None:
        connections = VaultConnections(vault, service.handle_client)
        listener = await asyncio.start_server(connections.callback("tcp"), "127.0.0.1", 0)
        host, port = listener.sockets[0].getsockname()[:2]

        def context(**_kwargs: object) -> SimpleNamespace:
            context_reads.append(True)
            return SimpleNamespace(
                kpdb=kpdb if caller_database else None, group="Allowed", vault_name=None, port=port,
                sources={"port": "fixture"},
            )

        monkeypatch.setattr(config, "resolve_context", context)
        endpoint = Endpoint("tcp", f"{host}:{port}", source="fixture")

        def discover(*_arguments: object, **_options: object) -> Endpoint:
            discoveries.append(True)
            return endpoint

        monkeypatch.setattr(cli, "_discover_endpoint", discover)
        monkeypatch.setattr(
            cli, "_send_socket", lambda *_a, **_kw: pytest.fail("client tried another endpoint"),
        )
        prefix = ["--configuration-root", str(repository)]
        try:
            assert await asyncio.to_thread(cli.main, [*prefix, "cache-populate"]) == 0
            assert "Cached 2 credential(s), 0 failed" in capsys.readouterr().out
            clock[0] += 10
            monkeypatch.setattr(
                vault, "ensure_unlocked", lambda *_a, **_kw: pytest.fail("verification tried to unlock"),
            )
            monkeypatch.setattr(
                vault.cli, "get_entry", lambda *_a: pytest.fail("verification read the backend"),
            )
            assert await asyncio.to_thread(cli.main, [*prefix, "cache-verify", "--json"]) == 0
            assert json.loads(capsys.readouterr().out) == {"present": 2, "total": 2, "missing": []}
            assert await asyncio.to_thread(cli.main, [*prefix, "cache-status", "--json"]) == 0
            status = json.loads(capsys.readouterr().out)
            assert (status["owner"], status["present"], status["configured_target_count"]) == (
                "selected-service", 2, 2,
            )
            assert context_reads == [True, True, True]
            assert discoveries == [True, True, True]
            assert Path.cwd() == selected[1]
        finally:
            await connections.close([listener], [], lambda: None)
            assert not connections.clients and not connections.workers and not connections.work

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["lost-reply", "declined"])
def test_qualified_extension_failure_never_replays_on_another_route(
    selected: SelectedFixture, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, failure: str,
) -> None:
    repository = configure(selected, monkeypatch)
    calls: list[str] = []
    endpoint = Endpoint("tcp", "127.0.0.1:10170", source="fixture")
    monkeypatch.setattr(cli, "_discover_endpoint", lambda *_a, **_kw: endpoint)
    monkeypatch.setattr(
        cli, "_send_tcp", lambda *_a, **_kw: pytest.fail("uncertain request reached a native route"),
    )

    def selected_transport(
        request: dict[str, Any], _timeout: float | None, _context: object,
    ) -> dict[str, Any] | None:
        calls.append(request["action"])
        if request["action"] == "ping":
            return {"ok": True, "accepting_work": True, "cache_maintenance": cache_maintenance.describe()}
        if failure == "lost-reply":
            raise TimeoutError("fixture-private-reply-diagnostic")
        return None

    selected[2].register_transport(
        selected_transport, priority=10, before_builtin=True, bind=lambda _ctx: selected_transport,
    )
    if failure == "lost-reply":
        selected[2].register_transport(
            lambda *_a: pytest.fail("uncertain request reached another extension"),
            priority=20, before_builtin=True,
        )
    assert cli.main(["--configuration-root", str(repository), "cache-populate"]) == 1
    assert calls == ["ping", "cache-maintenance"]
    assert "fixture-private-reply-diagnostic" not in caplog.text


def test_malformed_extension_qualification_refuses_without_fallback(
    selected: SelectedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = configure(selected, monkeypatch)
    endpoint = Endpoint("tcp", "127.0.0.1:10170", source="fixture")
    monkeypatch.setattr(cli, "_discover_endpoint", lambda *_a, **_kw: endpoint)
    monkeypatch.setattr(
        cli, "_send_tcp", lambda *_a, **_kw: pytest.fail("malformed qualification reached another route"),
    )

    def malformed(*_arguments: object) -> Any:
        return ["not-a-response-object"]

    selected[2].register_transport(malformed, before_builtin=True)
    assert cli.main(["--configuration-root", str(repository), "cache-populate"]) == 1


def test_qualification_exception_diagnostics_do_not_escape_the_real_command(
    selected: SelectedFixture, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str],
) -> None:
    repository = configure(selected, monkeypatch)
    calls: list[str] = []
    native_transport(monkeypatch, lambda _request: None)

    def broken(request: dict[str, Any], *_arguments: object) -> None:
        calls.append("broken:" + request["action"])
        raise RuntimeError("fixture-private-qualification-detail")

    def available(request: dict[str, Any], *_arguments: object) -> dict[str, Any]:
        calls.append("available:" + request["action"])
        if request["action"] == "ping":
            return {"ok": True, "accepting_work": True, "cache_maintenance": cache_maintenance.describe()}
        return {
            "ok": True, "contract": cache_maintenance.CONTRACT, "version": 1,
            "owner": "selected-service", "present": True,
        }

    selected[2].register_transport(
        broken, name="fixture-private-transport-name", priority=10, before_builtin=True,
    )
    selected[2].register_transport(
        available, priority=20, before_builtin=True, bind=lambda _ctx: available,
    )
    assert cli.main(["--configuration-root", str(repository), "cache-populate"]) == 0
    output = capsys.readouterr()
    assert calls == [
        "broken:ping", "available:ping",
        "available:cache-maintenance", "available:cache-maintenance",
    ]
    assert "Cached 2 credential(s), 0 failed" in output.out
    assert "fixture-private-" not in caplog.text + output.out + output.err


@pytest.mark.parametrize("failure", [
    "old-provider", "boolean-version", "wrong-owner", "unavailable",
    "rollback", "malformed-result", "needs-unlock",
])
def test_unsupported_or_failed_provider_never_uses_local_cache_or_replays(
    selected: SelectedFixture, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], failure: str,
) -> None:
    repository = configure(selected, monkeypatch)
    calls: list[str] = []

    def send(request: dict[str, Any]) -> dict[str, Any] | None:
        calls.append(request["action"])
        if request["action"] == "ping":
            descriptor = cache_maintenance.describe()
            if failure == "old-provider":
                return {"ok": True, "accepting_work": True}
            if failure == "boolean-version":
                descriptor["version"] = True
            if failure == "wrong-owner":
                descriptor["owner"] = "other"
            if failure == "unavailable":
                return None
            return {"ok": True, "accepting_work": True, "cache_maintenance": descriptor}
        if failure == "malformed-result":
            return {"ok": True, "present": "yes", "error": "fixture-private-diagnostic"}
        return {
            "ok": False, "error": "fixture-private-diagnostic",
            "needs_unlock": failure == "needs-unlock",
        }

    native_transport(monkeypatch, send)
    assert cli.main(["--configuration-root", str(repository), "cache-populate"]) == 1
    assert calls == (
        ["ping"] if failure in ("old-provider", "boolean-version", "wrong-owner", "unavailable")
        else ["ping", "cache-maintenance"]
    )
    output = capsys.readouterr()
    assert "fixture-private-diagnostic" not in output.out + output.err


def test_missing_records_keep_verification_exit_status_without_local_access(
    selected: SelectedFixture, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = configure(selected, monkeypatch)

    def send(request: dict[str, Any]) -> dict[str, Any]:
        if request["action"] == "ping":
            return {"ok": True, "accepting_work": True, "cache_maintenance": cache_maintenance.describe()}
        return {
            "ok": True, "contract": cache_maintenance.CONTRACT, "version": 1,
            "owner": "selected-service", "present": request["entry"].startswith("/"),
        }

    native_transport(monkeypatch, send)
    assert cli.main(["--configuration-root", str(repository), "cache-verify", "--json"]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "present": 1, "total": 2, "missing": ["Account [password]"],
    }


def test_policy_without_targets_neither_warms_nor_claims_protected_readiness(
    selected: SelectedFixture, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = configure(selected, monkeypatch, entries=False)
    calls: list[str] = []

    def send(request: dict[str, Any]) -> dict[str, Any]:
        calls.append(request["action"])
        return {"ok": True, "accepting_work": True, "cache_maintenance": cache_maintenance.describe()}

    native_transport(monkeypatch, send)
    assert cli.main(["--configuration-root", str(repository), "cache-status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["state"] == "no-targets" and "enabled" not in status
    assert calls == ["ping"]
    with use_configuration_root(str(repository)):
        assert cache_targets.configured_source() is None


@pytest.mark.parametrize("flag", ["--prompt", "--verify"])
def test_incompatible_population_flags_refuse_before_service_contact(
    selected: SelectedFixture, monkeypatch: pytest.MonkeyPatch, flag: str,
) -> None:
    repository = configure(selected, monkeypatch)
    monkeypatch.setattr(cli, "send_command", lambda *_a, **_kw: pytest.fail("unsupported flag used IPC"))
    assert cli.main(["--configuration-root", str(repository), "cache-populate", flag]) == 1


@pytest.mark.parametrize("command", [
    ["cache-verify", "--machine-fallback"],
    ["cache-machine-prepare", "--accept-machine-scope"],
])
def test_local_machine_operations_cannot_bypass_selected_service_ownership(
    selected: SelectedFixture, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], command: list[str],
) -> None:
    repository = configure(selected, monkeypatch)
    monkeypatch.setattr(
        cli, "send_command", lambda *_a, **_kw: pytest.fail("local operation contacted service"),
    )
    assert cli.main(["--configuration-root", str(repository), *command]) == 1
    assert "requires local cache ownership" in capsys.readouterr().err


@pytest.mark.parametrize("value", [True, None, [], "unknown"])
def test_invalid_maintenance_policy_refuses_before_extensions_or_local_cache(
    selected: SelectedFixture, monkeypatch: pytest.MonkeyPatch, value: object,
) -> None:
    repository = configure(selected, monkeypatch)
    write(repository, {"cache_maintenance": value, "cache_entries": ["entry"]})
    selected[2].register_cache_source(lambda _machine: pytest.fail("invalid policy used extension targets"))
    monkeypatch.setattr(cli, "send_command", lambda *_a, **_kw: pytest.fail("invalid policy used IPC"))
    assert cli.main(["--configuration-root", str(repository), "cache-verify"]) == 1
