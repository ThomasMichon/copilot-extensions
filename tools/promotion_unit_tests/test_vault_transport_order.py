"""Actor-free coverage of the actual Vault transport orchestration body."""

from __future__ import annotations

import ast
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
    state = SimpleNamespace(response=None)

    class DiscoveryRefusal(RuntimeError):
        pass

    original = DiscoveryRefusal("selected endpoint is unavailable")

    def discover(_context: object, *, strict: bool) -> NoReturn:
        assert strict is True
        calls.append("discovery")
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
        error=DiscoveryRefusal, state=state, calls=calls,
    )


@pytest.mark.parametrize("preferred", [True, False], ids=["preferred", "declined"])
def test_unavailable_discovery_preserves_preferred_authority_without_native_fallback(
    boundary: SimpleNamespace, preferred: bool,
) -> None:
    request = {"action": "get", "entry": "Example/service"}
    if preferred:
        boundary.state.response = {"ok": True, "value": "synthetic-value"}
        assert boundary.send(request, strict_endpoint=True) is boundary.state.response
    else:
        with pytest.raises(boundary.error) as caught:
            boundary.send(request, strict_endpoint=True)
        assert caught.value is boundary.original
    assert boundary.calls == ["discovery", "preferred"]
