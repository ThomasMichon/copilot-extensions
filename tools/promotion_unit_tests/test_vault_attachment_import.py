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
    config.__dict__.update(
        normalize_entry=lambda entry, group: f"{group}/{entry}" if group else entry,
        resolve_context=lambda **_kwargs: SimpleNamespace(
            kpdb="synthetic-db", group="Example"
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
    for leaf in ("attachment_import", "attachment_import_cli"):
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

    def send(request: dict[str, Any], **options: Any) -> Any:
        assert (
            options["strict_endpoint"] is True and options["require_endpoint"] is True
        )
        calls.append(request)
        return (
            {
                "ok": state.running,
                "accepting_work": state.accepting,
                "key_attachment_import_versions": state.versions,
            }
            if request["action"] == "ping"
            else state.response
        )

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
def test_actual_service_dispatch_does_not_invoke_unlock(
    owner: SimpleNamespace,
    unlocked: bool,
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
        },
        "synthetic-peer",
        "synthetic-transport",
    )
    assert result == (
        {"ok": True, "status": "created"}
        if unlocked
        else {"ok": False, "code": "needs_unlock", "error": "needs_unlock"}
    )
    assert len(backend.calls) == (4 if unlocked else 0)


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
        assert owner.calls[-1]["entry"] == "Example/new-key"
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
            )
        )
        assert result["key_attachment_import_versions"] == ([1] if capable else [])
