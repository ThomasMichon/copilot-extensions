"""Actor-free controls of the real provider-only attachment export boundary."""

from __future__ import annotations

import ast
import base64
from contextlib import nullcontext
from dataclasses import dataclass, field
import importlib.util
import io
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
from typing import Any

import pytest

from .test_vault_attachment_import import owner as import_owner  # noqa: F401

SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-vault/src/agent_vault"


@dataclass
class Backend:
    unlocked: bool = True
    outcomes: list[object] = field(default_factory=lambda: [b"private", b"public"])
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    def has_password(self, _kpdb: str) -> bool:
        return self.unlocked

    def export_attachment_bounded(
        self, kpdb: str, entry: str, name: str,
    ) -> tuple[object, str]:
        self.calls.append((kpdb, entry, name))
        value = self.outcomes.pop(0)
        if isinstance(value, Exception):
            raise value
        return value, "synthetic backend secret/path must not escape"


@pytest.fixture
def owner(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    shared = request.getfixturevalue("import_owner")
    spec = importlib.util.spec_from_file_location(
        shared.package + ".attachment_export", SOURCE / "attachment_export.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    spec.loader.exec_module(module)
    return SimpleNamespace(core=module, shared=shared)


def methods(filename: str, names: set[str], namespace: dict[str, Any]) -> dict[str, Any]:
    tree = ast.parse((SOURCE / filename).read_bytes())
    nodes: list[ast.stmt] = [
        node for parent in tree.body if isinstance(parent, ast.ClassDef)
        for node in parent.body if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE / filename), "exec"), namespace)
    return namespace


@pytest.mark.parametrize(
    "group,entry,normalized",
    [
        ("Example", "item", "Example/item"),
        ("Example", "Example/item", "Example/item"),
        (None, "item", "item"),
        ("", "item", "item"),
        (None, "/item", "item"),
    ],
)
def test_export_uses_real_normalizer_without_daemon_context(
    owner: SimpleNamespace, group: str | None, entry: str, normalized: str,
) -> None:
    backend = Backend()
    result = owner.core.handle_request(
        backend, "synthetic-db", group, {"entry": entry, "key_name": "example.key"},
    )
    assert result == {
        "ok": True,
        "key_data": base64.b64encode(b"private").decode("ascii"),
        "pub_data": base64.b64encode(b"public").decode("ascii"),
    }
    assert backend.calls == [
        ("synthetic-db", normalized, "example.key"),
        ("synthetic-db", normalized, "example.key.pub"),
    ]
    assert owner.shared.ambient_group_reads == []


@pytest.mark.parametrize(
    "change",
    [
        {"entry": "../other"}, {"key_name": "../other"}, {"key_name": "--help"},
        {"key_name": "\ud800"}, {"entry": "\ud800"}, {"allow_prompt": True},
    ],
)
def test_invalid_export_refuses_before_backend(
    owner: SimpleNamespace, change: dict[str, object],
) -> None:
    backend = Backend()
    result = owner.core.handle_request(
        backend, "synthetic-db", None, {"entry": "item", "key_name": "key"} | change,
    )
    assert result == {"ok": False, "code": "invalid_export", "error": "invalid_export"}
    assert backend.calls == []


@pytest.mark.parametrize("failure", ["locked", "unsupported", "first", "second", "exception", "empty", "large", "text"])
def test_export_failure_never_returns_partial_material(
    owner: SimpleNamespace, failure: str,
) -> None:
    backend: Any = Backend()
    expected, count = "export_failed", 1
    if failure == "locked":
        backend.unlocked, expected, count = False, "needs_unlock", 0
    elif failure == "unsupported":
        backend = SimpleNamespace(has_password=lambda _kpdb: True, calls=[])
        expected, count = "backend_unsupported", 0
    elif failure == "second":
        backend.outcomes = [b"private", None]
        count = 2
    else:
        backend.outcomes = [{
            "first": None, "exception": OSError("synthetic secret/path"),
            "empty": b"", "large": b"x" * 16_385, "text": "not bytes",
        }[failure]]
    result = owner.core.handle_request(
        backend, "synthetic-db", None, {"entry": "item", "key_name": "key"},
    )
    assert result == {"ok": False, "code": expected, "error": expected}
    assert len(backend.calls) == count


@pytest.mark.parametrize("expected_owner", ["synthetic-owner", "changed-owner", None])
def test_service_checks_incarnation_before_backend(
    owner: SimpleNamespace, expected_owner: str | None,
) -> None:
    backend = Backend()
    namespace = methods("service.py", {"_dispatch_request"}, {
        "__name__": owner.shared.package + ".service", "__package__": owner.shared.package,
        "Any": Any,
    })
    instance = SimpleNamespace(
        cli=backend, _attachment_import_owner="synthetic-owner",
        keepalive=lambda: None, _check_password_ttl=lambda: None,
        ensure_unlocked=lambda *_a, **_kw: pytest.fail("must not unlock"),
    )
    result = namespace["_dispatch_request"](instance, {
        "action": "export-key-nonactivating", "entry": "item", "key_name": "key",
        "kpdb": "synthetic-db", "group": None, "expected_export_owner": expected_owner,
    }, "peer", "inert")
    assert result["key_attachment_export_owner"] == "synthetic-owner"
    assert result["ok"] is (expected_owner == "synthetic-owner")
    assert len(backend.calls) == (2 if result["ok"] else 0)
    if not result["ok"]:
        assert result["code"] == "export_owner_changed"


def test_draining_service_refuses_without_dispatch() -> None:
    namespace = methods("service.py", {"handle_request"}, {"Any": Any})
    instance = SimpleNamespace(
        _runtime_can_work=False,
        _handle_admitted_request=lambda *_a: pytest.fail("draining provider dispatched"),
    )
    assert namespace["handle_request"](instance, {"action": "export-key-nonactivating"}) == {
        "ok": False, "error": "service_transition", "retryable": True,
    }


@pytest.mark.parametrize("supported", [True, False])
def test_actual_health_advertises_only_supported_provider(supported: bool) -> None:
    namespace = methods("service.py", {"_generation_health"}, {
        "Any": Any, "os": SimpleNamespace(getpid=lambda: 1), "_PROCESS_VERSION": "inert",
        "PROCESS_GENERATION": "inert", "PROTOCOL": 1, "supported_versions": lambda: [],
        "cache_warm_selection": SimpleNamespace(capabilities=lambda: {}),
    })
    instance = SimpleNamespace(
        cli=Backend() if supported else SimpleNamespace(),
        _runtime_can_work=True, _attachment_import_owner="synthetic-owner",
    )
    result = namespace["_generation_health"](instance)
    assert result["key_attachment_export_versions"] == ([1] if supported else [])
    assert result["key_attachment_export_owner"] == "synthetic-owner"


@pytest.mark.parametrize("bounded", [True, False])
@pytest.mark.parametrize("outcome", ["success", "oversize", "cleanup", "read", "subprocess"])
def test_concrete_backend_read_bound_and_cleanup(
    owner: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, bounded: bool, outcome: str,
) -> None:
    import tempfile

    read_sizes: list[int | None] = []
    calls: list[Any] = []
    data = b"x" * (16_385 if outcome == "oversize" else 16_384)

    class Reader(io.BytesIO):
        def read(self, size: int | None = -1, /) -> bytes:
            read_sizes.append(size)
            if outcome == "read":
                raise OSError("synthetic read detail")
            return super().read(size)

    def run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((argv, kwargs))
        if outcome == "subprocess":
            raise TimeoutError("synthetic subprocess detail")
        return SimpleNamespace(returncode=0)

    def unlink(path: str) -> None:
        calls.append(path)
        if outcome == "cleanup":
            raise OSError("synthetic cleanup detail")

    monkeypatch.setattr(
        tempfile, "NamedTemporaryFile",
        lambda **_kwargs: nullcontext(SimpleNamespace(name="synthetic-private-temp")),
    )
    namespace = methods("keepassxc.py", {"export_attachment", "export_attachment_bounded"}, {
        "__name__": owner.shared.package + ".keepassxc", "__package__": owner.shared.package,
        "subprocess": SimpleNamespace(run=run), "os": SimpleNamespace(unlink=unlink),
        "no_window_kwargs": lambda: {}, "open": lambda *_args: Reader(data),
    })
    instance = SimpleNamespace(
        _cli_path="synthetic-cli", _master_pass={"synthetic-db": "synthetic-unlock"},
        has_password=lambda _kpdb: True,
    )
    instance.export_attachment = MethodType(namespace["export_attachment"], instance)
    selected = "export_attachment_bounded" if bounded else "export_attachment"
    if bounded and outcome == "cleanup":
        with pytest.raises(OSError, match="synthetic cleanup detail"):
            namespace[selected](instance, "synthetic-db", "item", "key")
    else:
        result, _message = namespace[selected](instance, "synthetic-db", "item", "key")
        failed = outcome in ("read", "subprocess") or (bounded and outcome == "oversize")
        assert result == (None if failed else data)
    assert calls[-1] == "synthetic-private-temp"
    assert read_sizes == ([] if outcome == "subprocess" else [16_385 if bounded else -1])
    assert calls[0][0] == [
        "synthetic-cli", "attachment-export", "-q", "synthetic-db", "item", "key",
        "synthetic-private-temp",
    ]
    assert calls[0][1]["timeout"] == 10
