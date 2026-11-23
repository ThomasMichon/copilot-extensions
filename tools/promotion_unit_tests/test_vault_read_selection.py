"""Actor-free controls for the production implicit-read transport scope."""

from __future__ import annotations

import ast
from contextlib import nullcontext
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "promotion_vault_read_selection"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "plugins/agent-vault/src/agent_vault")]
sys.modules[PACKAGE] = package
caller = importlib.import_module(f"{PACKAGE}.read_client")
transport = importlib.import_module(f"{PACKAGE}.command_transport")
selection = importlib.import_module(f"{PACKAGE}.cache_warm_selection")
invocation = importlib.import_module(f"{PACKAGE}.invocation")


def production_functions(name: str, functions: set[str], namespace: dict[str, Any]) -> None:
    source = ROOT / f"plugins/agent-vault/src/agent_vault/{name}.py"
    tree = ast.parse(source.read_bytes(), filename=str(source))
    body: list[ast.stmt] = [
        node for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        or isinstance(node, ast.FunctionDef) and node.name in functions
    ]
    assert len(body) == len(functions) + 1
    exec(compile(ast.Module(body=body, type_ignores=[]), str(source), "exec"), namespace)


@pytest.fixture
def peer(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(
        requests=[], discoveries=[], endpoint=19071, ready=True, bound=True,
        versions=[1], read_versions=[1], receipt=True, outcome="success", writes=[],
        root=str(Path(ROOT.anchor) / "selected"), native=True, owner_reads=0,
    )
    context = SimpleNamespace(
        kpdb="fixture-db", group="Example", vault_name="first", port=19071,
        sources={"port": "flag"},
    )
    config = ModuleType(f"{PACKAGE}.config")

    def owner(**_options: object) -> tuple[str, bool]:
        state.owner_reads += 1
        return state.root, state.native

    config.__dict__.update(
        configuration_selection=owner, PINNED_CONTEXT_ENV="VAULT_READ_TEST_PIN",
        resolve_context=lambda: context, normalize_entry=lambda entry, group: f"{group}/{entry}",
    )
    cli = ModuleType(f"{PACKAGE}.cli")
    cache = ModuleType(f"{PACKAGE}.cache")
    persistent = SimpleNamespace(
        enabled=False, get=lambda *_a, **_kw: None,
        put=lambda *args, **_kw: state.writes.append(args) or True,
    )
    cache.__dict__["get_cache"] = lambda: persistent
    for module in (config, cli, cache):
        monkeypatch.setitem(sys.modules, module.__name__, module)
        monkeypatch.setattr(package, module.__name__.rsplit(".", 1)[-1], module, raising=False)

    def discover(_context: object, *, strict: bool) -> SimpleNamespace:
        state.discoveries.append((state.endpoint, strict))
        return SimpleNamespace(
            transport="tcp", source="fixture", tcp_host_port=("127.0.0.1", state.endpoint),
        )

    def reply(request: dict[str, Any], _host: str, port: int, _timeout: float | None) -> Any:
        state.requests.append((port, dict(request), invocation.configuration_root()))
        if request["action"] == "ping":
            return {
                "ok": True, "accepting_work": state.ready,
                "cache_warm_selection_versions": state.versions,
                "cache_warm_read_selection_versions": state.read_versions,
            }
        if state.outcome == "lost":
            return None
        if state.outcome == "invalid":
            return {"ok": "false", "value": "must-not-cache", "exists": "false"}
        response: dict[str, Any] = (
            {"ok": False, "error": "locked", "needs_unlock": True}
            if state.outcome == "refused" else
            {"ok": True, **({"value": "fixture-value", "generation": 7}
                           if request["action"] == "get" else {"exists": True})}
        )
        if selection.FIELD in request and state.receipt:
            response["cache_warm"] = {
                "state": "idle", "target_policy": invocation.CACHE_TARGET_CAPABILITY,
                "request_selection_version": 1,
            }
        return response

    def preferred(request: dict[str, Any], timeout: float | None, _context: object, **_kw: Any) -> Any:
        return None if state.bound else reply(request, "127.0.0.1", 19070, timeout)

    boundary = transport.TransportBoundary(
        resolve_context=lambda: context, discover=discover,
        socket_send=lambda *_a, **_kw: pytest.fail("unexpected socket"),
        tcp_send=reply, pipe_send=lambda *_a: pytest.fail("unexpected pipe"),
        registry=lambda: SimpleNamespace(try_transports=preferred),
        transport_context=SimpleNamespace, is_windows=True, is_wsl=False,
        host="127.0.0.1", unavailable=RuntimeError, invalid=ValueError,
    )
    cli.__dict__["send_command"] = lambda request, timeout=5.0, **options: transport.send_command(
        boundary, request, timeout, **options,
    )
    cli.__dict__.update(
        IS_WSL=False, config=config, time=SimpleNamespace(
            sleep=lambda _delay: setattr(state, "ready", True),
        ),
        start_service=lambda *_a: pytest.fail("unexpected activation"),
    )
    production_functions("cli", {"ensure_service", "_ready_for_work"}, cli.__dict__)
    reads = {
        "__name__": f"{PACKAGE}.credential_reads", "__package__": PACKAGE,
        "config": config, "os": caller.os,
        "canonical_field": lambda value: value, "attribute_name": lambda value: value,
        "legacy_cache_entry": lambda *_a: None,
        "CacheDecryptionUnavailable": type("CacheDecryptionUnavailable", (RuntimeError,), {}),
        "contextlib": SimpleNamespace(nullcontext=nullcontext),
        "CredentialRead": lambda code, **values: SimpleNamespace(
            **{"warning": None, "value": None, **values}, code=code,
        ),
    }
    production_functions("credential_reads", {"read_credential"}, reads)
    state.cli, state.context, state.config, state.cache = cli, context, config, persistent
    state.read = lambda **options: reads["read_credential"](SimpleNamespace(
        entry="entry", field="password", **options,
    ))
    return state


def test_read_reuses_actual_readiness_ping_and_pins_context_source_and_connector(
    peer: SimpleNamespace,
) -> None:
    session = caller.ReadSession(context=peer.context)
    with session.activate():
        assert peer.cli.ensure_service()
    peer.endpoint = 19072
    with session.activate():
        response = peer.cli.send_command({"action": "get", "entry": "entry"})
    assert response["value"] == "fixture-value" and session.warning is None
    assert [port for port, _, _ in peer.requests] == [19071, 19071]
    assert [request["action"] for _, request, _ in peer.requests] == ["ping", "get"]
    request = peer.requests[-1][1]
    assert request[selection.FIELD]["root"] == peer.root
    assert request[selection.FIELD]["native_default"] is True
    assert request["kpdb"] == "fixture-db" and request["group"] == "Example"
    assert peer.owner_reads == 1 and len(peer.discoveries) == 1
    assert all(root == peer.root for _, _, root in peer.requests)
    assert invocation.configuration_root() is None


def test_mixed_read_capabilities_and_legacy_unbound_peers_keep_baseline_with_warning(
    peer: SimpleNamespace,
) -> None:
    for versions, reads, bound in (([], [1], True), ([1], [], True), ([1], [True], True),
                                   ([1], [1], False)):
        peer.versions, peer.read_versions, peer.bound = versions, reads, bound
        session = caller.ReadSession(context=peer.context)
        with session.activate():
            assert peer.cli.ensure_service()
            assert peer.cli.send_command({"action": "get"})["ok"]
        assert selection.FIELD not in peer.requests[-1][1]
        assert session.warning == caller.UNSUPPORTED


def test_missing_read_receipt_and_uncertain_outcomes_never_replay(
    peer: SimpleNamespace,
) -> None:
    peer.receipt = False
    for outcome in ("success", "lost", "refused"):
        peer.outcome = outcome
        peer.requests.clear()
        session = caller.ReadSession(context=peer.context)
        with session.activate():
            assert peer.cli.ensure_service()
            peer.cli.send_command({"action": "get"})
        assert session.warning == caller.UNCONFIRMED
        assert [request["action"] for _, request, _ in peer.requests] == ["ping", "get"]


def test_pinned_bootstrap_does_not_discover_an_unrelated_configuration_owner(
    peer: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VAULT_READ_TEST_PIN", "immutable-fixture-context")
    result = peer.read(require_running=True)
    assert result.code == 0 and result.warning == caller.UNSUPPORTED
    assert peer.owner_reads == 0 and selection.FIELD not in peer.requests[-1][1]
    assert peer.discoveries == [(19071, True)]


def test_cache_fast_paths_do_not_select_or_contact_service(peer: SimpleNamespace) -> None:
    peer.cache.enabled = True
    peer.cache.get = lambda *_a, **_kw: "fixture-cached"
    result = peer.read()
    assert result.code == 0 and result.value == "fixture-cached"
    peer.cache.get = lambda *_a, **_kw: None
    assert peer.read(cache_only=True).code == 1
    assert not peer.requests and peer.owner_reads == 0 and not peer.writes


def test_restricted_read_retains_strict_advertisement_and_rejects_malformed_values(
    peer: SimpleNamespace,
) -> None:
    peer.cache.enabled = True
    result = peer.read(require_running=True, strict_cache=True)
    assert result.code == 0 and result.warning is None
    assert peer.writes == [("Example/entry", "password", "fixture-value", 7)]
    assert peer.discoveries == [(19071, True)]
    peer.outcome = "invalid"
    peer.writes.clear()
    result = peer.read(require_running=True, strict_cache=True)
    assert result.code == 1 and "service_response_invalid" in result.error and not peer.writes
    result = peer.read()
    assert result.code == 1 and "service_response_invalid" in result.error and not peer.writes


def test_has_reuses_readiness_and_warns_without_polluting_stdout(
    peer: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    assert caller.command_has(SimpleNamespace(entry="entry")) == 0
    output = capsys.readouterr()
    assert output.out == "true\n" and not output.err
    assert [request["action"] for _, request, _ in peer.requests] == ["ping", "has"]
    peer.receipt = False
    assert caller.command_has(SimpleNamespace(entry="entry")) == 0
    output = capsys.readouterr()
    assert output.out == "true\n" and caller.UNCONFIRMED in output.err
    peer.outcome = "invalid"
    assert caller.command_has(SimpleNamespace(entry="entry")) == 1
    output = capsys.readouterr()
    assert not output.out and "invalid service response" in output.err


def test_transitioning_peer_is_not_retained_before_readiness(peer: SimpleNamespace) -> None:
    peer.ready = False
    result = peer.read()
    assert result.code == 0
    assert [request["action"] for _, request, _ in peer.requests] == ["ping", "ping", "get"]
    assert len(peer.discoveries) == 2


def test_read_scope_does_not_capture_explicit_or_unrelated_operations() -> None:
    calls: list[str] = []

    class ExplicitBoundary(RuntimeError):
        pass

    def native(*_args: Any, **_kwargs: Any) -> Any:
        raise ExplicitBoundary

    def scoped(request: dict[str, Any], _timeout: float | None, **_flags: bool) -> dict[str, Any]:
        calls.append(request["action"])
        return {"ok": True}

    boundary = SimpleNamespace(
        resolve_context=native, host="127.0.0.1", discover=native,
        unavailable=ExplicitBoundary, invalid=ValueError,
    )
    with transport.use_read_sender(scoped):
        for action in ("ping", "get", "has"):
            assert transport.send_command(boundary, {"action": action})["ok"]
        for action, options in (
            ("unlock", {}), ("search", {}),
            ("get", {"_route": transport.CommandRoute()}),
            ("ping", {"_context": SimpleNamespace(port=19071)}),
        ):
            with pytest.raises(ExplicitBoundary):
                transport.send_command(boundary, {"action": action}, **options)
    with pytest.raises(ExplicitBoundary):
        transport.send_command(boundary, {"action": "get"})
    assert calls == ["ping", "get", "has"]
