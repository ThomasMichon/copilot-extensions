"""Actor-free coverage of the actual Vault transport orchestration body."""

from __future__ import annotations

import ast
from contextlib import nullcontext
import sys
from pathlib import Path
from types import MethodType, ModuleType, SimpleNamespace
from typing import Any, Callable, NoReturn, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def boundary(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    source = ROOT / "plugins/agent-vault/src/agent_vault/cli.py"
    tree = ast.parse(source.read_bytes(), filename=str(source))
    selected: list[ast.stmt] = [
        node for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        or isinstance(node, ast.FunctionDef) and node.name == "send_command"
    ]
    assert len(selected) == 2
    calls: list[str] = []
    state = SimpleNamespace(response=None, discovered=True, invalid=False)

    class DiscoveryRefusal(RuntimeError):
        pass

    class InvalidDiscovery(RuntimeError):
        pass

    original = DiscoveryRefusal("selected endpoint is unavailable")
    invalid = InvalidDiscovery("selected endpoint state is invalid")

    def discover(_context: object, *, strict: bool) -> None:
        assert strict is True
        calls.append("discovery")
        if not state.discovered:
            return None
        if state.invalid:
            raise invalid
        raise original

    def native(*_arguments: object, **_options: object) -> NoReturn:
        pytest.fail("unavailable discovery reached a native or legacy dial")

    def preferred(
        request: dict[str, Any], _timeout: float | None,
        context: SimpleNamespace, **options: object,
    ) -> dict[str, Any] | None:
        assert options == {"before_builtin": True}
        assert request["kpdb"] == "synthetic-vault"
        assert context.group == "Example"
        calls.append("preferred")
        response: dict[str, Any] | None = state.response
        return response

    package_name = "promotion_vault_transport"
    package = ModuleType(package_name)
    package.__dict__["__path__"] = [str(ROOT / "plugins/agent-vault/src/agent_vault")]
    extensions = ModuleType(f"{package_name}.extensions")
    extensions.__dict__.update(
        TransportContext=SimpleNamespace,
        get_registry=lambda: SimpleNamespace(try_transports=preferred),
    )
    monkeypatch.setitem(sys.modules, package_name, package)
    monkeypatch.setitem(sys.modules, extensions.__name__, extensions)
    transport_source = ROOT / "plugins/agent-vault/src/agent_vault/command_transport.py"
    transport_module = ModuleType(f"{package_name}.command_transport")
    transport_module.__package__ = package_name
    monkeypatch.setitem(sys.modules, transport_module.__name__, transport_module)
    exec(compile(transport_source.read_bytes(), str(transport_source), "exec"), transport_module.__dict__)
    namespace: dict[str, Any] = {
        "__name__": f"{package_name}.cli", "__package__": package_name,
        "config": SimpleNamespace(resolve_context=lambda: SimpleNamespace(
            kpdb="synthetic-vault", group="Example", vault_name=None,
            port=10170, sources={"port": "default"},
        )),
        "os": SimpleNamespace(environ={}),
        "rendezvous": SimpleNamespace(
            EndpointUnavailable=DiscoveryRefusal, EndpointStateError=InvalidDiscovery,
        ),
        "IS_WSL": False, "IS_WINDOWS": False,
        "_discover_endpoint": discover, "_send_socket": native, "_send_tcp": native,
        "pipe_send": native,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)
    return SimpleNamespace(
        send=namespace["send_command"], original=original,
        error=DiscoveryRefusal, state_error=InvalidDiscovery, invalid=invalid,
        state=state, calls=calls, package=package_name,
        namespace=namespace, transport=transport_module, extensions=extensions,
    )


@pytest.mark.parametrize("mode", [
    "preferred", "declined", "absent", "invalid-preferred", "invalid-declined",
])
def test_unavailable_discovery_preserves_preferred_authority_without_native_fallback(
    boundary: SimpleNamespace, mode: str,
) -> None:
    request = {"action": "get", "entry": "Example/service"}
    boundary.state.discovered = mode != "absent"
    boundary.state.invalid = mode.startswith("invalid-")
    if mode in ("preferred", "invalid-preferred"):
        boundary.state.response = {"ok": True, "value": "synthetic-value"}
        assert boundary.send(
            request, strict_endpoint=True, require_endpoint=True,
        ) is boundary.state.response
    else:
        expected = boundary.state_error if boundary.state.invalid else boundary.error
        with pytest.raises(expected) as caught:
            boundary.send(request, strict_endpoint=True, require_endpoint=True)
        if mode == "invalid-declined":
            assert caught.value is boundary.invalid
        elif mode == "declined":
            assert caught.value is boundary.original
    assert boundary.calls == ["discovery", "preferred"]


def test_legacy_strict_call_retains_its_existing_absent_advertisement_default(
    boundary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary.state.discovered = False

    def legacy(*_arguments: object, **_options: object) -> dict[str, Any]:
        boundary.calls.append("legacy")
        return {"ok": True, "value": "legacy-value"}

    monkeypatch.setitem(boundary.send.__globals__, "_send_socket", legacy)
    result = boundary.send({"action": "get"}, strict_endpoint=True)
    assert result["value"] == "legacy-value"
    assert boundary.calls == ["discovery", "preferred", "legacy"]


def transport_registry_method(warnings: list[object]) -> Callable[..., Any]:
    source = ROOT / "plugins/agent-vault/src/agent_vault/extensions.py"
    tree = ast.parse(source.read_bytes(), filename=str(source))
    registry = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ExtensionRegistry")
    method = next(
        node for node in registry.body if isinstance(node, ast.FunctionDef) and node.name == "try_transports"
    )
    future = next(node for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == "__future__")
    namespace: dict[str, Any] = {
        "cast": cast, "ClientTransport": Callable[..., Any],
        "log": SimpleNamespace(warning=lambda *values: warnings.append(values)),
    }
    exec(compile(ast.Module(body=[future, method], type_ignores=[]), str(source), "exec"), namespace)
    return cast(Callable[..., Any], namespace["try_transports"])


@pytest.mark.parametrize("failure", ["lost-reply", "declined"])
def test_retained_extension_failure_never_reenters_transport_selection(
    boundary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    warnings: list[object] = []
    calls: list[str] = []

    def selected(request: dict[str, Any], _timeout: object, _context: object) -> dict[str, Any] | None:
        calls.append(request["action"])
        if request["action"] == "ping":
            return {"ok": True, "_transport": "ext:unqualified"}
        if failure == "lost-reply":
            raise TimeoutError("private-reply-detail")
        return None

    def other(*_arguments: object) -> NoReturn:
        pytest.fail("uncertain request reached an unqualified transport")

    instance = SimpleNamespace(
        _transports_before=[
            SimpleNamespace(name="selected", fn=selected, bind=lambda _ctx: selected),
            SimpleNamespace(name="unqualified", fn=other),
        ],
        _transports_after=[],
    )
    instance.try_transports = MethodType(transport_registry_method(warnings), instance)
    monkeypatch.setattr(boundary.extensions, "get_registry", lambda: instance)
    route = boundary.transport.CommandRoute()
    assert boundary.send(
        {"action": "ping"}, strict_endpoint=True, require_endpoint=True, _route=route,
    )["ok"]
    assert route.sender is not None
    assert boundary.send(
        {"action": "cache-maintenance"}, strict_endpoint=True, require_endpoint=True, _route=route,
    ) is None
    assert calls == ["ping", "cache-maintenance"]
    assert boundary.calls == ["discovery"]
    assert warnings == []


@pytest.mark.parametrize("retained", [True, False])
@pytest.mark.parametrize("before_builtin", [True, False])
def test_qualification_logging_is_sanitized_without_changing_legacy_selection(
    retained: bool, before_builtin: bool,
) -> None:
    warnings: list[object] = []
    calls: list[str] = []
    selected: list[Callable[..., Any]] = []

    def broken(*_arguments: object) -> NoReturn:
        calls.append("broken")
        raise RuntimeError("private-qualification-detail")

    def available(*_arguments: object) -> dict[str, Any]:
        calls.append("available")
        return {"ok": True}

    ranked = [
        SimpleNamespace(name="private-transport-name", fn=broken),
        SimpleNamespace(name="available", fn=available, bind=lambda _ctx: available),
    ]
    instance = SimpleNamespace(
        _transports_before=ranked if before_builtin else [],
        _transports_after=[] if before_builtin else ranked,
    )
    invoke = MethodType(transport_registry_method(warnings), instance)
    assert invoke(
        {"action": "ping"}, 1.0, SimpleNamespace(),
        before_builtin=before_builtin, on_selected=selected.append if retained else None,
    )["ok"]
    assert calls == ["broken", "available"]
    assert selected == ([available] if retained else [])
    assert len(warnings) == 1
    assert ("private-" in str(warnings)) is (not retained)


@pytest.mark.parametrize("explicit_null", [False, True])
def test_maintenance_projection_omits_only_an_unselected_database_default(
    boundary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, explicit_null: bool,
) -> None:
    monkeypatch.setattr(boundary.namespace["config"], "resolve_context", lambda: SimpleNamespace(
        kpdb=None, group="Example", vault_name=None, port=10170, sources={"port": "default"},
    ))
    observations: list[dict[str, Any]] = []

    def preferred(request: dict[str, Any], *_arguments: object, **_options: object) -> dict[str, Any]:
        observations.append(dict(request))
        return {"ok": True}

    monkeypatch.setattr(
        boundary.extensions, "get_registry", lambda: SimpleNamespace(try_transports=preferred),
    )
    request: dict[str, Any] = {"action": "cache-maintenance"}
    if explicit_null:
        request["kpdb"] = None
    assert boundary.send(request, strict_endpoint=True, require_endpoint=True)["ok"]
    assert ("kpdb" in observations[0]) is explicit_null
    assert observations[0]["group"] == "Example"
    if explicit_null:
        assert observations[0]["kpdb"] is None


def test_restricted_reader_requires_advertisement_on_both_readiness_and_read(
    boundary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ROOT / "plugins/agent-vault/src/agent_vault/credential_reads.py"
    tree = ast.parse(source.read_bytes(), filename=str(source))
    selected: list[ast.stmt] = [
        node for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        or isinstance(node, ast.FunctionDef) and node.name == "read_credential"
    ]
    assert len(selected) == 2
    observations: list[tuple[str, dict[str, object]]] = []

    def send(request: dict[str, Any], **options: object) -> dict[str, Any]:
        observations.append((request["action"], options))
        return {"ok": True, "value": "synthetic-value"}

    cli = ModuleType(f"{boundary.package}.cli")
    cli.__dict__.update(send_command=send)
    cache = ModuleType(f"{boundary.package}.cache")
    cache.__dict__.update(get_cache=lambda: SimpleNamespace(enabled=False))
    read_client = ModuleType(f"{boundary.package}.read_client")
    read_client.__dict__.update(ReadSession=lambda **_kw: SimpleNamespace(
        activate=nullcontext, warning=None,
    ))
    monkeypatch.setitem(sys.modules, cli.__name__, cli)
    monkeypatch.setitem(sys.modules, cache.__name__, cache)
    monkeypatch.setitem(sys.modules, read_client.__name__, read_client)
    monkeypatch.setattr(sys.modules[boundary.package], "cli", cli, raising=False)
    namespace: dict[str, Any] = {
        "__name__": f"{boundary.package}.credential_reads",
        "__package__": boundary.package, "os": SimpleNamespace(environ={}),
        "config": SimpleNamespace(
            PINNED_CONTEXT_ENV="SYNTHETIC_PIN",
            resolve_context=lambda: SimpleNamespace(group="Example"),
            normalize_entry=lambda entry, group: entry,
        ),
        "canonical_field": lambda value: value,
        "legacy_cache_entry": lambda *_args: None,
        "contextlib": SimpleNamespace(nullcontext=nullcontext),
        "CredentialRead": lambda code, **values: SimpleNamespace(code=code, **values),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)
    result = namespace["read_credential"](SimpleNamespace(
        entry="Example/service", field="password", require_running=True,
    ))
    assert result.code == 0
    assert observations == [
        ("ping", {"timeout": 5.0, "strict_endpoint": True, "require_endpoint": True}),
        ("get", {"timeout": None, "strict_endpoint": True, "require_endpoint": True}),
    ]
