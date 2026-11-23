"""Actor-free controls of the actual exclusive export composition."""

from __future__ import annotations

import argparse
import ast
import base64
import importlib.util
import json
from pathlib import Path
import stat
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from .test_vault_attachment_export import Backend, methods
from .test_vault_attachment_import import owner as import_owner  # noqa: F401
from .test_vault_attachment_import import routed_owner as import_route  # noqa: F401
from .vault_export_model import Filesystem, Node

SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-vault/src/agent_vault"


@pytest.fixture
def owner(request: pytest.FixtureRequest) -> SimpleNamespace:
    shared: SimpleNamespace = request.getfixturevalue("import_owner")
    return shared


@pytest.fixture
def consumer(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    route: SimpleNamespace = request.getfixturevalue("import_route")
    loaded: dict[str, ModuleType] = {}
    for leaf in ("attachment_export", "attachment_export_files", "attachment_export_cli"):
        spec = importlib.util.spec_from_file_location(
            route.owner.package + "." + leaf, SOURCE / (leaf + ".py"),
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        spec.loader.exec_module(module)
        loaded[leaf] = module
    filesystem = Filesystem()
    files = loaded["attachment_export_files"]
    monkeypatch.setattr(files, "os", filesystem.os)
    monkeypatch.setattr(files, "sys", SimpleNamespace(platform="linux"))
    backend = Backend()
    namespace = methods("service.py", {"_dispatch_request", "_generation_health"}, {
        "__name__": route.owner.package + ".service", "__package__": route.owner.package,
        "Any": Any, "os": SimpleNamespace(getpid=lambda: 1), "_PROCESS_VERSION": "inert",
        "PROCESS_GENERATION": "inert", "PROTOCOL": 1, "supported_versions": lambda: [],
        "cache_warm_selection": SimpleNamespace(VERSION=1),
    })
    instance = SimpleNamespace(
        cli=backend, _attachment_import_owner="synthetic-owner", _runtime_can_work=True,
        keepalive=lambda: None, _check_password_ttl=lambda: None,
        ensure_unlocked=lambda *_a, **_kw: pytest.fail("must not unlock"),
    )
    state = SimpleNamespace(outcome="success", versions=None)

    def selected(
        request: dict[str, Any], _timeout: float | None, _context: Any,
    ) -> dict[str, Any] | None:
        route.events.append(("selected", request["action"]))
        if request["action"] == "ping":
            result: dict[str, Any] = namespace["_generation_health"](instance)
            if state.versions is not None:
                result["key_attachment_export_versions"] = state.versions
            return result
        assert request["expected_export_owner"] == "synthetic-owner"
        assert "expected_import_owner" not in request
        assert request["kpdb"] == "synthetic-db"
        assert request["group"] == route.context.group
        if state.outcome == "changed":
            instance._attachment_import_owner = "other-owner"
        result = namespace["_dispatch_request"](instance, request, "peer", "inert")
        if state.outcome == "lost":
            return None
        if state.outcome == "raised":
            raise TimeoutError("synthetic credential path")
        if state.outcome == "rollback":
            return {"ok": False, "code": "exclusive_export_unsupported"}
        if state.outcome == "malformed":
            result["pub_data"] = "not-base64"
        if state.outcome == "oversize":
            result["key_data"] = "A" * 30_000
        return result

    def fallback(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("exclusive export fell through to another transport")

    route.registry.register_transport(
        selected, before_builtin=True, single_attempt=True, priority=1, bind=lambda _ctx: selected,
    )
    route.registry.register_transport(fallback, before_builtin=True, priority=2)
    route.registry.register_transport(fallback)
    return SimpleNamespace(
        route=route, filesystem=filesystem, files=files,
        client=loaded["attachment_export_cli"], backend=backend, service=instance, state=state,
        args=SimpleNamespace(
            require_running=True, entry="item", key_name="example.key", dest_dir="/safe/dest",
        ),
    )


@pytest.mark.parametrize("short_write", [False, True])
def test_exclusive_export_uses_real_route_provider_and_pinned_writer(
    consumer: SimpleNamespace, capsys: pytest.CaptureFixture[str], short_write: bool,
) -> None:
    case = consumer
    case.filesystem.fault = "short-write" if short_write else ""
    assert case.client.command(case.args) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {"version": 1, "status": "created"}
    assert output.err == ""
    assert case.route.events == [
        ("selected", "ping"), ("selected", "export-key-nonactivating"),
    ]
    assert case.backend.calls == [
        ("synthetic-db", "Example/item", "example.key"),
        ("synthetic-db", "Example/item", "example.key.pub"),
    ]
    assert {name: node.data for name, node in case.filesystem.destination.children.items()} == {
        "example.key": b"private", "example.key.pub": b"public",
    }
    assert all(
        stat.S_IMODE(node.mode) == 0o600
        for node in case.filesystem.destination.children.values()
    )
    assert case.filesystem.events[:2] == ["create:example.key", "create:example.key.pub"]
    assert case.filesystem.events[-1] == "sync:dest"
    assert case.filesystem.handles == {}
    assert case.route.state.discovery_calls == 1
    assert case.route.owner.ambient_group_reads == []


@pytest.mark.parametrize("outcome", ["lost", "raised", "changed", "rollback", "malformed", "oversize"])
def test_uncertain_or_invalid_export_never_creates_files_or_reselects(
    consumer: SimpleNamespace, capsys: pytest.CaptureFixture[str], outcome: str,
) -> None:
    case = consumer
    case.state.outcome = outcome
    assert case.client.command(case.args) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["effect_status"] == "unknown"
    assert json.loads(output.err)["file_effect_status"] == "not_started"
    assert "private" not in output.err and "credential path" not in output.err
    assert case.filesystem.destination.children == {}
    assert case.filesystem.handles == {}
    assert case.route.events == [
        ("selected", "ping"), ("selected", "export-key-nonactivating"),
    ]
    assert len(case.backend.calls) == (0 if outcome == "changed" else 2)


@pytest.mark.parametrize("versions", [[], [True], ["1"]])
def test_unsupported_export_capability_refuses_before_private_read(
    consumer: SimpleNamespace, versions: list[Any],
) -> None:
    case = consumer
    case.state.versions = versions
    assert case.client.command(case.args) == 1
    assert case.route.events == [("selected", "ping")]
    assert case.backend.calls == [] and case.filesystem.destination.children == {}
    assert case.filesystem.handles == {}


@pytest.mark.parametrize("failure", ["locked", "draining", "legacy", "windows", "require-running"])
def test_exclusive_export_preflight_never_activates_or_uses_legacy_fallback(
    consumer: SimpleNamespace, failure: str,
) -> None:
    case = consumer
    if failure == "locked":
        case.backend.unlocked = False
    elif failure == "draining":
        case.service._runtime_can_work = False
    elif failure == "legacy":
        case.route.registry._transports_before[0].single_attempt = False
    elif failure == "windows":
        case.files.os.name = "nt"
    else:
        case.args.require_running = False
    assert case.client.command(case.args) == 1
    assert case.backend.calls == [] and case.filesystem.destination.children == {}
    assert case.filesystem.handles == {}
    assert len(case.route.events) <= (2 if failure == "locked" else 1)


@pytest.mark.parametrize("kind", ["file", "symlink", "hardlink", "unsafe-parent", "public-directory"])
def test_existing_or_unsafe_destinations_refuse_before_health(
    consumer: SimpleNamespace, kind: str,
) -> None:
    case = consumer
    if kind == "unsafe-parent":
        case.filesystem.parent.uid = 2000
    elif kind == "public-directory":
        case.filesystem.destination.mode = stat.S_IFDIR | 0o755
    else:
        node = Node((stat.S_IFLNK if kind == "symlink" else stat.S_IFREG) | 0o600, 900)
        node.data = b"untouched"
        node.links = 2 if kind == "hardlink" else 1
        case.filesystem.destination.children["example.key.pub"] = node
    assert case.client.command(case.args) == 1
    assert case.route.events == [] and case.backend.calls == []
    assert case.filesystem.handles == {}
    if kind in ("file", "symlink", "hardlink"):
        assert case.filesystem.destination.children["example.key.pub"].data == b"untouched"


@pytest.mark.parametrize("fault", [
    "second-collision", "mode", "hardlink", "write", "zero", "interrupt",
    "swap", "parent-swap", "file-sync", "directory-sync", "close",
])
def test_output_failure_preserves_created_files_and_truthful_unknown_custody(
    consumer: SimpleNamespace, capsys: pytest.CaptureFixture[str], fault: str,
) -> None:
    case = consumer
    case.filesystem.fault = fault
    assert case.client.command(case.args) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["effect_status"] == "unknown"
    assert "synthetic" not in output.err
    assert case.filesystem.handles == {}
    assert "example.key" in case.filesystem.destination.children
    assert len(case.route.events) == 2
    if fault == "second-collision":
        assert case.filesystem.destination.children["example.key"].data == b""
        assert case.filesystem.destination.children["example.key.pub"].data == b"keep"
    if fault == "swap":
        assert case.filesystem.destination.children["example.key"].data == b"untouched"


def test_explicit_root_group_uses_selected_context_without_daemon_defaults(
    consumer: SimpleNamespace,
) -> None:
    case = consumer
    case.route.context.group = None
    assert case.client.command(case.args) == 0
    assert case.backend.calls[0] == ("synthetic-db", "item", "example.key")
    assert case.route.owner.ambient_group_reads == []


def export_parser(case: SimpleNamespace) -> argparse.ArgumentParser:
    tree = ast.parse((SOURCE / "cli.py").read_bytes())
    command = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_export_key"
    )
    main = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_main"
    )
    start = next(
        index for index, node in enumerate(main.body)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
        and any(isinstance(arg, ast.Constant) and arg.value == "export-key" for arg in node.value.args)
    )
    end = start
    for index, statement in enumerate(main.body[start:], start):
        if (
            isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Attribute)
            and statement.value.func.attr == "set_defaults"
        ):
            end = index + 1
            break
    assert end > start
    namespace: dict[str, Any] = {
        "__name__": case.route.owner.package + ".cli",
        "__package__": case.route.owner.package, "Any": Any, "sys": sys,
    }
    exec(compile(ast.Module(body=[command], type_ignores=[]), str(SOURCE / "cli.py"), "exec"), namespace)
    parser = argparse.ArgumentParser()
    namespace["sub"] = parser.add_subparsers(dest="command")
    exec(compile(
        ast.Module(body=main.body[start:end], type_ignores=[]), str(SOURCE / "cli.py"), "exec",
    ), namespace)
    return parser


def test_actual_export_parser_dispatches_exclusive_and_rejects_unknown_arguments(
    consumer: SimpleNamespace,
) -> None:
    parser = export_parser(consumer)
    args = parser.parse_args([
        "export-key", "item", "/safe/dest", "example.key", "--exclusive", "--require-running",
    ])
    assert args.func(args) == 0
    with pytest.raises(SystemExit) as error:
        parser.parse_args([
            "export-key", "item", "/safe/dest", "example.key", "--unrecognized",
        ])
    assert error.value.code == 2
    assert len(consumer.route.events) == 2


def test_require_running_without_exclusive_refuses_before_ordinary_activation(
    consumer: SimpleNamespace,
) -> None:
    parser = export_parser(consumer)
    args = parser.parse_args(["export-key", "item", "/safe/dest", "example.key", "--require-running"])
    assert args.func(args) == 1
    assert consumer.route.events == []


def test_ordinary_export_keeps_existing_activation_write_and_mode_behavior() -> None:
    events: list[object] = []

    class LegacyPath:
        def __init__(self, path: str) -> None:
            self.path = path

        def is_dir(self) -> bool:
            return True

        def __truediv__(self, name: str) -> LegacyPath:
            return LegacyPath(self.path + "/" + name)

        def write_bytes(self, data: bytes) -> int:
            events.append((self.path, data))
            return len(data)

        def chmod(self, mode: int) -> None:
            events.append((self.path, mode))

    def send(request: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        events.append(request)
        return {
            "ok": True, "key_data": base64.b64encode(b"old-private").decode(),
            "pub_data": base64.b64encode(b"old-public").decode(),
        }

    def unlocked() -> bool:
        events.append("ordinary-unlock")
        return True

    node = next(
        node for node in ast.parse((SOURCE / "cli.py").read_bytes()).body
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_export_key"
    )
    namespace: dict[str, Any] = {
        "Any": Any, "Path": LegacyPath, "IS_WINDOWS": False, "sys": sys,
        "_ensure_unlocked_service": unlocked,
        "send_command": send,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE / "cli.py"), "exec"), namespace)
    assert namespace["cmd_export_key"](SimpleNamespace(
        entry="item", dest_dir="/legacy", key_name="example.key",
    )) == 0
    assert events == [
        "ordinary-unlock",
        {"action": "export-key", "entry": "item", "key_name": "example.key"},
        ("/legacy/example.key", b"old-private"), ("/legacy/example.key.pub", b"old-public"),
        ("/legacy/example.key", 0o600), ("/legacy/example.key.pub", 0o644),
    ]
