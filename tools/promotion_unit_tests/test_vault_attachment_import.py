"""Actor-free controls of the actual create-only attachment owner."""

from __future__ import annotations

import ast
import base64
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
import importlib.util
import os
from pathlib import Path
from subprocess import CompletedProcess
import sys
from types import MethodType, ModuleType, SimpleNamespace
from typing import Any, Iterator

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins/agent-vault/src/agent_vault"


class MissingPath:
    suffix = ""

    def __init__(self, path: str) -> None:
        self.path = path

    def __str__(self) -> str:
        return self.path

    def exists(self) -> bool:
        return False


@dataclass
class Backend:
    paths: set[str] = field(default_factory=lambda: {"Example/"})
    unlocked: bool = True
    add_result: object = field(default_factory=lambda: CompletedProcess([], 0))
    imports: list[object] = field(default_factory=lambda: [True, True])
    calls: list[object] = field(default_factory=list)

    def has_password(self, _database: str) -> bool:
        return self.unlocked

    def _identity_listing(self, _database: str, _deadline: float) -> set[str]:
        self.calls.append("list")
        return self.paths

    def _identity_run(
        self, _database: str, args: list[str], _deadline: float
    ) -> object:
        self.calls.append(args)
        if isinstance(self.add_result, Exception):
            raise self.add_result
        return self.add_result

    def import_attachment_create_only(
        self,
        _database: str,
        _entry: str,
        name: str,
        data: bytes,
    ) -> tuple[bool, str]:
        self.calls.append((name, data))
        value = self.imports.pop(0)
        if isinstance(value, Exception):
            raise value
        return bool(value), "synthetic backend detail must not escape"


@pytest.fixture
def owner(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    name = "promotion_vault_attachment"
    package = ModuleType(name)
    package.__dict__["__path__"] = []
    monkeypatch.setitem(sys.modules, name, package)
    config = ModuleType(name + ".config")
    config_tree = ast.parse((SOURCE / "config.py").read_bytes())
    config_nodes: list[ast.stmt] = [
        node for node in config_tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in ("_nonempty", "_clean_group", "normalize_entry")
    ]
    assert len(config_nodes) == 3
    ambient_group_reads: list[str] = []

    def daemon_config() -> SimpleNamespace:
        ambient_group_reads.append("load")
        return SimpleNamespace(group="DaemonGroup")

    config_namespace: dict[str, Any] = {"Any": Any, "load_config": daemon_config}
    exec(
        compile(
            ast.Module(body=config_nodes, type_ignores=[]),
            str(SOURCE / "config.py"), "exec",
        ),
        config_namespace,
    )
    config.__dict__.update(
        normalize_entry=config_namespace["normalize_entry"],
        resolve_context=lambda **_kwargs: SimpleNamespace(
            kpdb="synthetic-db", group="Example", vault_name=None,
        ),
    )
    monkeypatch.setitem(sys.modules, config.__name__, config)
    lock_calls: list[str] = []

    @contextmanager
    def lock(_database: str, _deadline: float) -> Iterator[None]:
        lock_calls.append("enter")
        try:
            yield
        finally:
            lock_calls.append("exit")

    locking = ModuleType(name + ".identity_key_lock")
    locking.__dict__["database_creation_lock"] = lock
    monkeypatch.setitem(sys.modules, locking.__name__, locking)
    loaded: dict[str, ModuleType] = {}
    for leaf in ("transport_binding", "attachment_import", "attachment_import_cli"):
        spec = importlib.util.spec_from_file_location(
            name + "." + leaf, SOURCE / (leaf + ".py")
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        spec.loader.exec_module(module)
        loaded[leaf] = module
    core, client = loaded["attachment_import"], loaded["attachment_import_cli"]
    monkeypatch.setattr(core, "time", SimpleNamespace(monotonic=lambda: 10.0))
    monkeypatch.setattr(client, "os", SimpleNamespace(name="posix", path=os.path))
    monkeypatch.setattr(
        client,
        "_read_file",
        lambda _path, *, private: b"private" if private else b"public",
    )
    calls: list[dict[str, Any]] = []
    state = SimpleNamespace(
        response={"ok": True, "status": "created"},
        running=True,
        versions=[1],
        accepting=True,
    )

    def mutate(request: dict[str, Any], _timeout: float | None) -> Any:
        calls.append(request)
        response = state.response
        if response is not None:
            response = {**response, "key_attachment_import_owner": "synthetic-owner"}
        return response

    def send(request: dict[str, Any], **options: Any) -> Any:
        assert (
            options["strict_endpoint"] is True and options["require_endpoint"] is True
        )
        calls.append(request)
        assert request["action"] == "ping"
        options["binding"].select(
            mutate, single_attempt=True,
            defaults={"kpdb": "synthetic-db", "group": "Example", "vault": None},
        )
        return {
            "ok": state.running,
            "accepting_work": state.accepting,
            "key_attachment_import_versions": state.versions,
            "key_attachment_import_owner": "synthetic-owner",
        }

    cli = ModuleType(name + ".cli")
    cli.__dict__.update(send_command=send)
    monkeypatch.setitem(sys.modules, cli.__name__, cli)
    return SimpleNamespace(
        core=core,
        client=client,
        cli=cli,
        calls=calls,
        state=state,
        lock_calls=lock_calls,
        package=name,
        binding=loaded["transport_binding"],
        ambient_group_reads=ambient_group_reads,
    )


def request() -> dict[str, object]:
    return {
        "entry": "new-key",
        "key_name": "example.key",
        "key_data": base64.b64encode(b"synthetic-private").decode("ascii"),
        "pub_data": base64.b64encode(b"synthetic-public").decode("ascii"),
        "allow_prompt": False,
    }


@pytest.mark.parametrize(
    "failure",
    [
        "locked",
        "entry",
        "group",
        "parent",
        "listing",
        "add-none",
        "add-nonzero",
        "add-exception",
        "first",
        "second",
        "attachment-exception",
    ],
)
def test_create_only_failures_stop_without_replay(
    owner: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    backend = Backend()
    expected = "creation_unconfirmed"
    if failure == "locked":
        backend.unlocked, expected = False, "needs_unlock"
    elif failure in ("entry", "group"):
        backend.paths.add("Example/new-key" + ("/" if failure == "group" else ""))
        expected = "entry_exists"
    elif failure == "parent":
        backend.paths.clear()
        expected = "parent_unavailable"
    elif failure == "listing":

        def broken(*_args: object) -> set[str]:
            raise RuntimeError("synthetic backend detail")

        monkeypatch.setattr(backend, "_identity_listing", broken)
        expected = "vault_read_failed"
    elif failure == "add-none":
        backend.add_result = None
    elif failure == "add-nonzero":
        backend.add_result = CompletedProcess([], 1)
    elif failure == "add-exception":
        backend.add_result = TimeoutError("synthetic backend detail")
    elif failure in ("first", "second", "attachment-exception"):
        outcomes: dict[str, list[object]] = {
            "first": [False],
            "second": [True, False],
            "attachment-exception": [TimeoutError("synthetic backend detail")],
        }
        backend.imports = outcomes[failure]
        expected = "attachment_creation_unconfirmed"
    result = owner.core.handle_request(backend, "synthetic-db", "Example", request())
    assert result == {"ok": False, "code": expected, "error": expected}
    additions = [call for call in backend.calls if isinstance(call, list)]
    attachments = [call for call in backend.calls if isinstance(call, tuple)]
    assert len(additions) == (
        0 if failure in ("locked", "entry", "group", "parent", "listing") else 1
    )
    assert len(attachments) == (
        2
        if failure == "second"
        else 1
        if failure in ("first", "attachment-exception")
        else 0
    )
    assert owner.lock_calls == ([] if failure == "locked" else ["enter", "exit"])


def test_create_only_success_uses_new_entry_and_two_nonoverwriting_writes(
    owner: SimpleNamespace,
) -> None:
    backend = Backend()
    assert owner.core.handle_request(backend, "synthetic-db", "Example", request()) == {
        "ok": True,
        "status": "created",
    }
    assert backend.calls == [
        "list",
        ["add", "-q", "synthetic-db", "Example/new-key"],
        ("example.key", b"synthetic-private"),
        ("example.key.pub", b"synthetic-public"),
    ]
    assert owner.lock_calls == ["enter", "exit"]


@pytest.mark.parametrize("unlocked", [True, False])
@pytest.mark.parametrize("expected_owner", ["synthetic-owner", "other-owner", None])
def test_actual_service_dispatch_does_not_invoke_unlock(
    owner: SimpleNamespace,
    unlocked: bool,
    expected_owner: str | None,
) -> None:
    tree = ast.parse((SOURCE / "service.py").read_bytes())
    node = next(
        function
        for parent in tree.body
        if isinstance(parent, ast.ClassDef)
        for function in parent.body
        if isinstance(function, ast.FunctionDef)
        and function.name == "_dispatch_request"
    )
    backend = Backend(unlocked=unlocked)
    instance = SimpleNamespace(
        cli=backend,
        _attachment_import_owner="synthetic-owner",
        _request_ctx=SimpleNamespace(),
        keepalive=lambda: None,
        _check_password_ttl=lambda: None,
        ensure_unlocked=lambda *_args, **_kwargs: pytest.fail(
            "create-only import invoked unlock"
        ),
    )
    namespace: dict[str, Any] = {
        "__name__": owner.package + ".service",
        "__package__": owner.package,
        "Any": Any,
        "UNLOCK_REQUIRED_ACTIONS": frozenset(),
        "log": SimpleNamespace(info=lambda *_args: None),
    }
    exec(
        compile(
            ast.Module(body=[node], type_ignores=[]), str(SOURCE / "service.py"), "exec"
        ),
        namespace,
    )
    result = namespace["_dispatch_request"](
        instance,
        request()
        | {
            "action": "import-key-create-only",
            "kpdb": "synthetic-db",
            "group": "Example",
            "expected_import_owner": expected_owner,
        },
        "synthetic-peer",
        "synthetic-transport",
    )
    assert result == (
        {"ok": True, "status": "created"}
        if unlocked and expected_owner == "synthetic-owner"
        else {"ok": False, "code": "import_owner_changed", "error": "import_owner_changed"}
        if expected_owner != "synthetic-owner"
        else {"ok": False, "code": "needs_unlock", "error": "needs_unlock"}
    ) | {"key_attachment_import_owner": "synthetic-owner"}
    assert len(backend.calls) == (
        4 if unlocked and expected_owner == "synthetic-owner" else 0
    )


@pytest.mark.parametrize(
    "change",
    [
        {"key_name": "../other"},
        {"entry": "../other"},
        {"allow_prompt": True},
        {"key_data": "not-base64"},
        {"pub_data": ""},
        {"key_data": "A" * 22_000},
        {"entry": "--help"},
        {"entry": "\ud800"},
        {"key_name": "bad:name"},
        {"key_name": "\u00e9" * 65},
    ],
)
def test_invalid_create_only_request_has_no_backend_effect(
    owner: SimpleNamespace,
    change: dict[str, object],
) -> None:
    backend = Backend()
    result = owner.core.handle_request(backend, "synthetic-db", "", request() | change)
    assert result == {"ok": False, "code": "invalid_import", "error": "invalid_import"}
    assert backend.calls == [] and owner.lock_calls == []


@pytest.mark.parametrize(
    "mode",
    [
        "created",
        "unavailable",
        "older",
        "rolled-back",
        "draining",
        "boolean-version",
        "partial",
        "collision",
        "unsupported-platform",
    ],
)
def test_create_only_cli_uses_additive_action_and_truthful_custody(
    owner: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    owner.state.running = mode != "unavailable"
    owner.client.os.name = "nt" if mode == "unsupported-platform" else "posix"
    owner.state.accepting = mode != "draining"
    owner.state.versions = (
        None if mode == "older" else [True] if mode == "boolean-version" else [1]
    )
    owner.state.response = {
        "created": {"ok": True, "status": "created"},
        "older": {"ok": False, "error": "Unknown action: import-key-create-only"},
        "rolled-back": {"ok": False, "error": "Unknown action: import-key-create-only"},
        "draining": None,
        "boolean-version": None,
        "partial": {"ok": False, "code": "attachment_creation_unconfirmed"},
        "collision": {"ok": False, "code": "entry_exists"},
        "unavailable": None,
        "unsupported-platform": None,
    }[mode]
    code = owner.client.command(
        SimpleNamespace(
            require_running=True,
            entry="new-key",
            key_file="/synthetic/example.key",
        )
    )
    output = capsys.readouterr()
    assert code == (0 if mode == "created" else 1)
    assert [call["action"] for call in owner.calls] == (
        []
        if mode == "unsupported-platform"
        else ["ping"]
        if mode in ("unavailable", "older", "draining", "boolean-version")
        else ["ping", "import-key-create-only"]
    )
    if mode in ("created", "rolled-back", "partial", "collision"):
        assert owner.calls[-1]["allow_prompt"] is False
        assert owner.calls[-1]["entry"] == "new-key"
        assert owner.calls[-1]["group"] == "Example"
    if mode != "created":
        assert ('"effect_status": "unknown"' in output.err) == (
            mode in ("rolled-back", "partial")
        )
    if mode in ("older", "boolean-version"):
        assert '"error": "create_only_unsupported"' in output.err
    assert "synthetic-private" not in output.out + output.err


def test_import_cli_preserves_ordinary_dispatch_and_requires_explicit_mode(
    owner: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tree = ast.parse((SOURCE / "cli.py").read_bytes())
    node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_import_key"
    )
    namespace: dict[str, Any] = {
        "__name__": owner.package + ".legacy",
        "__package__": owner.package,
        "Any": Any,
        "sys": sys,
        "Path": MissingPath,
    }
    exec(
        compile(
            ast.Module(body=[node], type_ignores=[]), str(SOURCE / "cli.py"), "exec"
        ),
        namespace,
    )
    # Refusal happens before the unchanged legacy body could read input or activate.
    assert namespace["cmd_import_key"](SimpleNamespace(require_running=True)) == 1
    assert "--create-only" in capsys.readouterr().err
    assert owner.calls == []
    assert namespace["cmd_import_key"](SimpleNamespace(key_file="/missing")) == 1
    assert "key file not found" in capsys.readouterr().err
    assert owner.calls == []
    assert (
        namespace["cmd_import_key"](
            SimpleNamespace(
                create_only=True,
                require_running=True,
                entry="new-key",
                key_file="/synthetic/example.key",
            )
        )
        == 0
    )
    assert [call["action"] for call in owner.calls] == [
        "ping",
        "import-key-create-only",
    ]


@pytest.mark.parametrize("overwrite", [True, False])
def test_attachment_backend_retains_legacy_force_and_omits_it_for_create_only(
    monkeypatch: pytest.MonkeyPatch,
    overwrite: bool,
) -> None:
    import contextlib
    import tempfile

    tree = ast.parse((SOURCE / "keepassxc.py").read_bytes())
    backend = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "KeePassXCBackend"
    )
    functions: list[ast.stmt] = [
        node
        for node in backend.body
        if isinstance(node, ast.FunctionDef)
        and node.name in ("import_attachment", "import_attachment_create_only")
    ]
    assert len(functions) == 2
    observed: list[list[str]] = []
    temporary = SimpleNamespace(name="synthetic-private-temp", write=lambda _data: None)
    monkeypatch.setattr(
        tempfile, "NamedTemporaryFile", lambda **_kwargs: nullcontext(temporary)
    )

    def run(arguments: list[str], **_options: object) -> SimpleNamespace:
        observed.append(arguments)
        return SimpleNamespace(returncode=0)

    namespace: dict[str, Any] = {
        "contextlib": contextlib,
        "os": SimpleNamespace(unlink=lambda _path: None),
        "no_window_kwargs": lambda: {},
        "subprocess": SimpleNamespace(run=run),
    }
    exec(
        compile(
            ast.Module(body=functions, type_ignores=[]),
            str(SOURCE / "keepassxc.py"),
            "exec",
        ),
        namespace,
    )
    instance = SimpleNamespace(
        _cli_path="synthetic-keepass",
        has_password=lambda _database: True,
        _master_pass={"synthetic-db": "synthetic-unlock"},
    )
    instance.import_attachment = MethodType(namespace["import_attachment"], instance)
    selected = "import_attachment" if overwrite else "import_attachment_create_only"
    assert (
        namespace[selected](
            instance,
            "synthetic-db",
            "Example/new-key",
            "example.key",
            b"synthetic",
        )[0]
        is True
    )
    assert ("-f" in observed[0]) is overwrite

    def cleanup_failed(_path: str) -> None:
        raise OSError("synthetic cleanup failure")

    namespace["os"].unlink = cleanup_failed
    arguments = (
        instance,
        "synthetic-db",
        "Example/new-key",
        "example.key",
        b"synthetic",
    )
    if overwrite:
        assert namespace[selected](*arguments)[0] is True
    else:
        with pytest.raises(OSError, match="synthetic cleanup failure"):
            namespace[selected](*arguments)


@pytest.mark.parametrize("platform,advertised", [("posix", True), ("nt", False)])
def test_capability_tracks_supported_cli_input_custody(
    platform: str, advertised: bool
) -> None:
    tree = ast.parse((SOURCE / "invocation.py").read_bytes())
    node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "invocation_contract"
    )
    namespace: dict[str, Any] = {
        "os": SimpleNamespace(name=platform),
        "Any": Any,
        "CACHE_TARGET_CAPABILITY": "synthetic-existing-capability",
    }
    exec(
        compile(
            ast.Module(body=[node], type_ignores=[]),
            str(SOURCE / "invocation.py"),
            "exec",
        ),
        namespace,
    )
    assert (
        "key-attachment-create-only-v1"
        in namespace["invocation_contract"]()["capabilities"]
    ) is advertised
    service = ast.parse((SOURCE / "service.py").read_bytes())
    health = next(
        function
        for parent in service.body
        if isinstance(parent, ast.ClassDef)
        for function in parent.body
        if isinstance(function, ast.FunctionDef)
        and function.name == "_generation_health"
    )
    namespace.update(
        os=SimpleNamespace(getpid=lambda: 123),
        _PROCESS_VERSION="synthetic",
        PROCESS_GENERATION="synthetic-generation",
        PROTOCOL=1,
        cache_warm_selection=SimpleNamespace(VERSION=1),
        supported_versions=lambda: [],
    )
    exec(
        compile(
            ast.Module(body=[health], type_ignores=[]),
            str(SOURCE / "service.py"),
            "exec",
        ),
        namespace,
    )
    for capable in (True, False):
        backend = (
            SimpleNamespace(import_attachment_create_only=lambda: None)
            if capable
            else SimpleNamespace()
        )
        result = namespace["_generation_health"](
            SimpleNamespace(
                cli=backend,
                _generation=None,
                _runtime_can_work=True,
                _attachment_import_owner="synthetic-owner",
            )
        )
        assert result["key_attachment_import_versions"] == ([1] if capable else [])


@pytest.fixture
def routed_owner(
    owner: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    """Execute the actual sender and registry, replacing only IPC/storage edges."""
    spec = importlib.util.spec_from_file_location(
        owner.package + ".extensions", SOURCE / "extensions.py"
    )
    assert spec is not None and spec.loader is not None
    extensions = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, extensions)
    spec.loader.exec_module(extensions)
    spec = importlib.util.spec_from_file_location(
        owner.package + ".command_transport", SOURCE / "command_transport.py"
    )
    assert spec is not None and spec.loader is not None
    transport = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, transport)
    spec.loader.exec_module(transport)
    registry = extensions.ExtensionRegistry()
    registry._loaded = True
    extensions.__dict__["_REGISTRY"] = registry
    context = SimpleNamespace(
        kpdb="synthetic-db", group="Example", vault_name="example",
        port=12345, sources={"port": "default"},
    )
    config = sys.modules[owner.package + ".config"]
    config.__dict__["resolve_context"] = lambda **_kwargs: context
    state = SimpleNamespace(
        discovery_calls=0,
        endpoint=SimpleNamespace(
            transport="tcp", source="local", tcp_host_port=("synthetic-host", 23456),
        ),
    )
    events: list[tuple[str, str]] = []

    def discover(_context: object, **_options: object) -> object:
        state.discovery_calls += 1
        return state.endpoint

    def native(
        request: dict[str, Any], _host: str, _port: int, _timeout: float | None,
    ) -> dict[str, Any]:
        events.append(("native", request["action"]))
        return {
            "ok": True, "status": "created", "accepting_work": True,
            "key_attachment_import_versions": [1],
            "key_attachment_import_owner": "synthetic-owner",
        }

    tree = ast.parse((SOURCE / "cli.py").read_bytes())
    nodes: list[ast.stmt] = [
        node for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        or isinstance(node, ast.FunctionDef) and node.name == "send_command"
    ]
    assert len(nodes) == 2
    namespace: dict[str, Any] = {
        "__name__": owner.package + ".cli", "__package__": owner.package,
        "config": config, "os": SimpleNamespace(environ={}),
        "IS_WSL": False, "IS_WINDOWS": False,
        "_discover_endpoint": discover, "_send_tcp": native,
        "_send_socket": lambda *_a, **_kw: pytest.fail("unexpected socket fallback"),
        "pipe_send": lambda *_a, **_kw: pytest.fail("unexpected pipe fallback"),
        "rendezvous": SimpleNamespace(
            EndpointUnavailable=RuntimeError, EndpointStateError=ValueError,
        ),
    }
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE / "cli.py"), "exec"),
        namespace,
    )
    monkeypatch.setattr(owner.cli, "send_command", namespace["send_command"])
    return SimpleNamespace(
        owner=owner, registry=registry, events=events, state=state,
        namespace=namespace, context=context,
    )


@pytest.mark.parametrize(
    "outcome", ["lost", "raised", "other-success", "other-refusal", "rollback", "created"],
)
def test_preferred_mutation_never_falls_through_after_acceptance(
    routed_owner: SimpleNamespace, capsys: pytest.CaptureFixture[str], outcome: str,
) -> None:
    route = routed_owner

    def other(*_args: object) -> dict[str, Any]:
        pytest.fail("mutation fell through to another extension")

    def preferred(
        request: dict[str, Any], _timeout: float | None, context: Any,
    ) -> dict[str, Any] | None:
        route.events.append(("preferred", request["action"]))
        if request["action"] == "ping":
            # A callback-supplied label must not substitute for its actual identity.
            return {
                "ok": True, "accepting_work": True,
                "key_attachment_import_versions": [1],
                "key_attachment_import_owner": "synthetic-owner",
                "_transport": "unrelated-label",
            }
        assert request["expected_import_owner"] == "synthetic-owner"
        assert request["kpdb"] == "synthetic-db"
        assert context.port == 23456
        if outcome == "raised":
            raise TimeoutError("synthetic lost mutation reply")
        if outcome == "lost":
            return None
        if outcome == "rollback":
            return {"ok": False, "code": "create_only_unsupported"}
        return {
            "ok": outcome != "other-refusal", "status": "created", "code": "entry_exists",
            "key_attachment_import_owner": (
                "synthetic-owner" if outcome == "created" else "other-owner"
            ),
        }

    route.registry.register_transport(
        preferred, before_builtin=True, single_attempt=True, priority=1, bind=lambda _ctx: preferred,
    )
    route.registry.register_transport(other, before_builtin=True, priority=2)
    route.registry.register_transport(other)
    code = route.owner.client.command(SimpleNamespace(
        require_running=True, entry="new-key", key_file="/synthetic/example.key",
    ))
    output = capsys.readouterr()
    assert code == (0 if outcome == "created" else 1)
    assert route.events == [
        ("preferred", "ping"), ("preferred", "import-key-create-only"),
    ]
    assert route.state.discovery_calls == 1
    if outcome != "created":
        assert '"effect_status": "unknown"' in output.err


def test_legacy_preferred_transport_refuses_before_private_payload(
    routed_owner: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    route = routed_owner

    def legacy(request: dict[str, Any], *_args: object) -> dict[str, Any]:
        route.events.append(("legacy", request["action"]))
        assert "key_data" not in request
        return {
            "ok": True, "accepting_work": True,
            "key_attachment_import_versions": [1],
            "key_attachment_import_owner": "synthetic-owner",
        }

    route.registry.register_transport(legacy, before_builtin=True)
    assert route.owner.client.command(SimpleNamespace(
        require_running=True, entry="new-key", key_file="/synthetic/example.key",
    )) == 1
    assert route.events == [("legacy", "ping")]
    output = capsys.readouterr()
    assert '"error": "mutation_transport_unsupported"' in output.err
    assert '"effect_status": "not_started"' in output.err


@pytest.mark.parametrize("native_route", ["tcp", "unix", "pipe", "extension"])
def test_selected_connector_and_context_survive_rebinding_and_loss(
    routed_owner: SimpleNamespace, native_route: str,
) -> None:
    route = routed_owner
    binding = route.owner.binding.MutationBinding()
    observed: list[dict[str, Any]] = []
    route.state.endpoint.transport = "tcp" if native_route == "extension" else native_route
    route.state.endpoint.address = "synthetic-endpoint"
    route.namespace["IS_WINDOWS"] = native_route == "pipe"
    route.state.endpoint.source = "windows"

    def selected(request: dict[str, Any], *_args: object, **_kwargs: object) -> Any:
        observed.append(request)
        if request["action"] == "ping":
            return {
                "ok": True, "accepting_work": True,
                "key_attachment_import_versions": [1],
                "key_attachment_import_owner": "synthetic-owner",
            }
        return None

    def other(*_args: object, **_kwargs: object) -> Any:
        pytest.fail("bound mutation re-resolved its selected connector")

    def pipe_selected(
        _path: str, request: dict[str, Any], timeout: float | None,
    ) -> Any:
        return selected(request, timeout)

    route.namespace.update(
        _send_tcp=selected, _send_socket=selected, pipe_send=pipe_selected,
    )
    if native_route == "extension":
        route.registry.register_transport(
            selected, before_builtin=True, single_attempt=True, bind=lambda _ctx: selected,
        )
    health = route.owner.cli.send_command(
        {"action": "ping", "health_only": True},
        strict_endpoint=True, require_endpoint=True, binding=binding,
    )
    binding.accept_health(health)
    route.namespace.update(_send_tcp=other, _send_socket=other, pipe_send=other)
    route.registry._transports_before.clear()
    route.registry.register_transport(other, before_builtin=True)
    route.context.kpdb, route.context.port = "other-database", 34567
    route.state.endpoint = None
    assert binding.send_once({"action": "import-key-create-only"}, 1.0) is None
    assert route.state.discovery_calls == 1
    assert [request["action"] for request in observed] == ["ping", "import-key-create-only"]
    # Windows-side selection must not inherit the guest's database defaults.
    assert all("kpdb" not in request for request in observed)
    with pytest.raises(ValueError, match="unused mutation-capable"):
        binding.send_once({"action": "import-key-create-only"}, 1.0)
    assert len(observed) == 2


@pytest.mark.parametrize("selection", ["windows-native", "windows-extension", "changed-group"])
def test_import_command_refuses_cross_boundary_or_changed_context_before_private_payload(
    routed_owner: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], selection: str,
) -> None:
    route = routed_owner
    observed: list[dict[str, Any]] = []

    def selected(request: dict[str, Any], *_args: object) -> dict[str, Any]:
        observed.append(request)
        assert request["action"] == "ping"
        assert "key_data" not in request
        return {
            "ok": True, "accepting_work": True,
            "key_attachment_import_versions": [1],
            "key_attachment_import_owner": "synthetic-owner",
        }

    route.namespace["_send_tcp"] = selected
    if selection.startswith("windows-"):
        route.state.endpoint.source = "windows"
    else:
        context = vars(route.context) | {"group": "other-group"}
        config = sys.modules[route.owner.package + ".config"]
        monkeypatch.setitem(
            config.__dict__, "resolve_context",
            lambda **options: route.context if options.get("strict") else SimpleNamespace(**context),
        )
    if selection == "windows-extension":
        route.registry.register_transport(
            selected, before_builtin=True, single_attempt=True, bind=lambda _ctx: selected,
        )
    assert route.owner.client.command(SimpleNamespace(
        require_running=True, entry="new-key", key_file="/synthetic/example.key",
    )) == 1
    assert len(observed) == 1
    output = capsys.readouterr()
    assert '"error": "mutation_transport_unsupported"' in output.err
    assert '"effect_status": "not_started"' in output.err


@pytest.mark.parametrize("field", ["kpdb", "group", "vault"])
def test_bound_mutation_cannot_override_health_context(
    routed_owner: SimpleNamespace, field: str,
) -> None:
    route = routed_owner
    binding = route.owner.binding.MutationBinding()
    health = route.owner.cli.send_command(
        {"action": "ping", "health_only": True},
        strict_endpoint=True, require_endpoint=True, binding=binding,
    )
    binding.accept_health(health)
    with pytest.raises(ValueError, match="cannot override"):
        binding.send_once({"action": "import-key-create-only", field: "other"}, 1.0)
    assert route.events == [("native", "ping")]


def test_mutation_binding_refuses_a_maintenance_route_before_selection(
    routed_owner: SimpleNamespace,
) -> None:
    route = routed_owner
    binding = route.owner.binding.MutationBinding()
    component = sys.modules[route.owner.package + ".command_transport"]
    with pytest.raises(ValueError, match="cannot share a maintenance route"):
        route.owner.cli.send_command(
            {"action": "ping", "health_only": True},
            binding=binding, _route=component.CommandRoute(),
        )
    assert route.events == []
    assert route.state.discovery_calls == 0
    binding.require_unselected()


@pytest.mark.parametrize("group,entry,expected", [
    (None, "new-key", "new-key"),
    ("", "new-key", "new-key"),
    ("CallerGroup", "new-key", "CallerGroup/new-key"),
    ("CallerGroup", "/new-key", "new-key"),
    ("CallerGroup", "Fixed/new-key", "Fixed/new-key"),
])
def test_actual_command_dispatch_and_normalizer_preserve_selected_group(
    routed_owner: SimpleNamespace, capsys: pytest.CaptureFixture[str],
    group: str | None, entry: str, expected: str,
) -> None:
    route = routed_owner
    route.context.group = group
    backend = Backend(paths={"CallerGroup/", "DaemonGroup/", "Fixed/"})
    tree = ast.parse((SOURCE / "service.py").read_bytes())
    node = next(
        function
        for parent in tree.body if isinstance(parent, ast.ClassDef)
        for function in parent.body if isinstance(function, ast.FunctionDef)
        and function.name == "_dispatch_request"
    )
    namespace: dict[str, Any] = {
        "__name__": route.owner.package + ".service",
        "__package__": route.owner.package,
        "Any": Any,
    }
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE / "service.py"), "exec"),
        namespace,
    )
    service = SimpleNamespace(
        cli=backend, _attachment_import_owner="synthetic-owner",
        keepalive=lambda: None, _check_password_ttl=lambda: None,
    )

    def selected(request: dict[str, Any], *_args: object) -> dict[str, Any]:
        if request["action"] == "ping":
            return {
                "ok": True, "accepting_work": True,
                "key_attachment_import_versions": [1],
                "key_attachment_import_owner": "synthetic-owner",
            }
        assert request["group"] == group
        result: dict[str, Any] = namespace["_dispatch_request"](
            service, request, "synthetic-peer", "synthetic-transport",
        )
        return result

    route.namespace["_send_tcp"] = selected
    assert route.owner.client.command(SimpleNamespace(
        require_running=True, entry=entry, key_file="/synthetic/example.key",
    )) == 0
    assert backend.calls == [
        "list",
        ["add", "-q", "synthetic-db", expected],
        ("example.key", b"private"),
        ("example.key.pub", b"public"),
    ]
    assert route.owner.ambient_group_reads == []
    assert '"status": "created"' in capsys.readouterr().out
