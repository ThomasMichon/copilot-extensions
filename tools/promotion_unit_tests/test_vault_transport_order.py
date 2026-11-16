"""Actor-free coverage of the actual Vault transport orchestration body."""

from __future__ import annotations

import ast
from contextlib import nullcontext
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, NoReturn

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
    state = SimpleNamespace(response=None, discovered=True)

    class DiscoveryRefusal(RuntimeError):
        pass

    original = DiscoveryRefusal("selected endpoint is unavailable")

    def discover(_context: object, *, strict: bool) -> None:
        assert strict is True
        calls.append("discovery")
        if not state.discovered:
            return None
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
    package.__dict__["__path__"] = []
    extensions = ModuleType(f"{package_name}.extensions")
    extensions.__dict__.update(
        TransportContext=SimpleNamespace,
        get_registry=lambda: SimpleNamespace(try_transports=preferred),
    )
    monkeypatch.setitem(sys.modules, package_name, package)
    monkeypatch.setitem(sys.modules, extensions.__name__, extensions)
    namespace: dict[str, Any] = {
        "__name__": f"{package_name}.cli", "__package__": package_name,
        "config": SimpleNamespace(resolve_context=lambda: SimpleNamespace(
            kpdb="synthetic-vault", group="Example", vault_name=None,
            port=10170, sources={"port": "default"},
        )),
        "os": SimpleNamespace(environ={}),
        "rendezvous": SimpleNamespace(EndpointUnavailable=DiscoveryRefusal),
        "IS_WSL": False, "IS_WINDOWS": False,
        "_discover_endpoint": discover, "_send_socket": native, "_send_tcp": native,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)
    return SimpleNamespace(
        send=namespace["send_command"], original=original,
        error=DiscoveryRefusal, state=state, calls=calls, package=package_name,
    )


@pytest.mark.parametrize("mode", ["preferred", "declined", "absent"])
def test_unavailable_discovery_preserves_preferred_authority_without_native_fallback(
    boundary: SimpleNamespace, mode: str,
) -> None:
    request = {"action": "get", "entry": "Example/service"}
    boundary.state.discovered = mode != "absent"
    if mode == "preferred":
        boundary.state.response = {"ok": True, "value": "synthetic-value"}
        assert boundary.send(
            request, strict_endpoint=True, require_endpoint=True,
        ) is boundary.state.response
    else:
        with pytest.raises(boundary.error) as caught:
            boundary.send(request, strict_endpoint=True, require_endpoint=True)
        if mode == "declined":
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
    monkeypatch.setitem(sys.modules, cli.__name__, cli)
    monkeypatch.setitem(sys.modules, cache.__name__, cache)
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
