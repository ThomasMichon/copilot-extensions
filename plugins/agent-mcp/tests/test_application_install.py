"""Installed application callbacks retain optional serving and slot identity."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import importlib.util
import json
import os
import struct
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_mcp
from agent_mcp import __main__ as main
from agent_mcp import config, cutover
from agent_mcp import serve


@pytest.mark.parametrize("pid", [4321, 0, -1, b"\x01", OSError("native probe refused")])
def test_darwin_data_owner_uses_native_peer_pid(tmp_path, monkeypatch, pid):
    monkeypatch.setattr(serve, "_HAS_AF_UNIX", True)
    monkeypatch.setattr(cutover.sys, "platform", "darwin")
    monkeypatch.delattr(cutover._socket, "SO_PEERCRED", raising=False)
    monkeypatch.setattr(cutover._socket, "AF_UNIX", 1, raising=False)
    queries = []

    class Peer:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, value):
            assert value == 0.25

        def connect(self, path):
            assert path == str(tmp_path / "serve.sock")

        def getsockopt(self, level, option, size):
            queries.append((level, option, size))
            if isinstance(pid, OSError):
                raise pid
            if isinstance(pid, bytes):
                return pid
            return struct.pack("i", pid)

    monkeypatch.setattr(cutover._socket, "socket", lambda *args: Peer())
    if isinstance(pid, int) and pid > 0:
        assert cutover.source_data_owner(tmp_path / "serve.sock") == pid
    else:
        with pytest.raises(cutover.ControlError, match="identity"):
            cutover.source_data_owner(tmp_path / "serve.sock")
    assert queries == [(0, 2, 4)]


@pytest.mark.skipif(sys.platform != "darwin", reason="requires native Darwin Unix peer identity")
def test_darwin_data_owner_identifies_actual_listener(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixed = Path("serve.sock")
    with cutover._socket.socket(cutover._socket.AF_UNIX, cutover._socket.SOCK_STREAM) as listener:
        listener.bind(str(fixed))
        listener.listen(1)
        try:
            assert cutover.source_data_owner(fixed) == os.getpid()
        finally:
            fixed.unlink()
    assert cutover.source_data_owner(fixed) is None


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX socket links")
@pytest.mark.parametrize("target_kind", ["missing", "socket", "file", "foreign-missing", "foreign-socket"])
def test_source_socket_link_retains_missing_target_and_refuses_foreign_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_kind: str,
) -> None:
    home = tmp_path.resolve() / ("long-native-socket-" + "x" * 110)
    home.mkdir()
    monkeypatch.chdir(home)
    monkeypatch.setattr(serve, "_HAS_AF_UNIX", True)
    fixed = home / "serve.sock"
    target = home / "serve-g1.sock"
    if target_kind.startswith("foreign"):
        target = home / "foreign" / target.name
        target.parent.mkdir()
    with contextlib.ExitStack() as resources:
        if target_kind.endswith("socket"):
            listener = resources.enter_context(
                cutover._socket.socket(cutover._socket.AF_UNIX, cutover._socket.SOCK_STREAM),
            )
            listener.bind(str(target.relative_to(home)))
        elif target_kind == "file":
            target.write_text("not a socket", encoding="utf-8")
        fixed.symlink_to(target.relative_to(home))
        before = fixed.lstat()
        if target_kind in {"missing", "socket"}:
            state = cutover.source_handle_state(fixed)
            assert state == (
                "present", before.st_dev, before.st_ino, before.st_mtime_ns, target.name,
            )
            assert os.readlink(fixed) == target.name
            if target_kind == "missing":
                assert cutover.source_data_owner(Path(fixed.name)) is None
                assert not target.exists()
        else:
            with pytest.raises(cutover.ControlError, match="foreign socket target"):
                cutover.source_handle_state(fixed)
        assert fixed.lstat().st_ino == before.st_ino


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX socket links")
def test_source_socket_link_change_during_target_read_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path.resolve()
    monkeypatch.setattr(serve, "_HAS_AF_UNIX", True)
    fixed = home / "serve.sock"
    fixed.symlink_to("serve-g1.sock")
    readlink = os.readlink
    changed = False

    def replace_link(path: str | Path, *args, **kwargs) -> str:
        nonlocal changed
        value = readlink(path, *args, **kwargs)
        if Path(path) == fixed and not changed:
            changed = True
            fixed.unlink()
            fixed.symlink_to("serve-g2.sock")
        return value

    monkeypatch.setattr(cutover.os, "readlink", replace_link)
    with pytest.raises(cutover.ControlError, match="changed during admission"):
        cutover.source_handle_state(fixed)
    assert readlink(fixed) == "serve-g2.sock"


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX socket links")
def test_source_socket_target_io_failure_is_not_treated_as_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path.resolve()
    monkeypatch.setattr(serve, "_HAS_AF_UNIX", True)
    fixed = home / "serve.sock"
    target = home / "serve-g1.sock"
    fixed.symlink_to(target.name)
    path_stat = Path.stat

    def denied(path: Path, *args, **kwargs):
        if path == target:
            raise PermissionError("target inspection denied")
        return path_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(PermissionError, match="target inspection denied"):
        cutover.source_handle_state(fixed)


def _installed_callback(tmp_path, monkeypatch, layout):
    package = "1.2.3"
    generation = package
    if layout != "release-nested":
        generation += "+source." + "a" * 40 + "." + "0" * 15 + "1"
    if layout == "source-nested":
        generation += ".profile." + "0" * 15 + "2"
    root = tmp_path / "installation"
    slot = root / "versions" / generation
    prefix = slot if layout == "source-flat" else slot / ".venv"
    installed = prefix / "lib" / "site-packages" / "agent_mcp" / "cutover.py"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(Path(cutover.__file__).read_bytes())
    spec = importlib.util.spec_from_file_location("agent_mcp.cutover", installed)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    monkeypatch.setattr(agent_mcp, "cutover", module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    version = importlib.metadata.version
    monkeypatch.setattr(
        importlib.metadata, "version",
        lambda name: package if name == "agent-mcp" else version(name),
    )
    bridges = tmp_path / "data" / "bridges"
    monkeypatch.setenv("AGENT_MCP_HOME", str(bridges.parent))
    monkeypatch.setenv("AGENT_MCP_INSTALL_DIR", str(root))
    monkeypatch.setattr(config, "BRIDGES_DIR", bridges)
    monkeypatch.setattr(config, "discover_plugin_bridge_candidates", lambda **_: [])
    for name in ("AGENT_MCP_NO_SERVE", "AGENT_MCP_NO_CUTOVER"):
        monkeypatch.delenv(name, raising=False)
    return module, root, slot, generation, bridges


@pytest.mark.parametrize("layout,configured,wanted,disabled", [
    ("source-flat", True, True, None),
    ("source-nested", True, True, None),
    ("release-nested", True, False, None),
    ("source-nested", False, True, None),
    ("source-nested", True, True, "AGENT_MCP_NO_SERVE"),
    ("source-nested", True, True, "AGENT_MCP_NO_CUTOVER"),
])
@pytest.mark.parametrize("incumbent", [None, "incumbent-generation"])
def test_application_callbacks_preserve_optional_serving(
    tmp_path, monkeypatch, capsys, layout, configured, wanted, disabled, incumbent,
):
    module, root, slot, generation, bridges = _installed_callback(
        tmp_path, monkeypatch, layout,
    )
    original_config = json.dumps({
        "server": {"type": "stdio", "command": ["fixture-upstream"]},
        "auth": {"kind": "none"},
    })
    if configured:
        bridges.mkdir(parents=True)
        (bridges / "example.json").write_text(original_config, encoding="utf-8")
    observations = []

    def observe():
        observations.append("observed")
        return wanted

    starts = []

    def start(actual_layout, physical_version, **kwargs):
        starts.append((actual_layout, physical_version))
        return {"ok": True, "version": physical_version}

    monkeypatch.setattr(module, "source_serving_intent", observe, raising=False)
    monkeypatch.setattr(module, "_run_slot_startup", start, raising=False)
    monkeypatch.setattr(
        module, "spawn_passive_daemon",
        lambda *args, **kwargs: pytest.fail("setup must not spawn a resident"),
    )
    marker = root / "current-version"
    if incumbent:
        marker.write_text(incumbent, encoding="utf-8-sig")
    installation = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }
    data_home = bridges.parent
    data_existed = data_home.exists()
    original_data = {
        path.relative_to(data_home): path.read_bytes()
        for path in data_home.rglob("*") if path.is_file()
    }
    environment = dict(os.environ)
    assert main.main(["__source-setup"]) == 0
    setup = json.loads(capsys.readouterr().out)
    assert setup["ok"] and setup["wanted"] == (configured and wanted)
    assert observations == (["observed"] if configured else [])
    assert not starts
    assert {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path != slot / ".mcp-serving-intent.json"
    } == installation
    assert data_home.exists() == data_existed
    assert {
        path.relative_to(data_home): path.read_bytes()
        for path in data_home.rglob("*") if path.is_file()
    } == original_data
    assert dict(os.environ) == environment
    assert not (root / "deploy-manifest.json").exists()
    intent = json.loads((slot / ".mcp-serving-intent.json").read_text(encoding="utf-8"))
    assert intent == {"generation": generation, "wanted": configured and wanted}
    with pytest.raises(module.ControlError, match="selected generation"):
        main.main(["__source-start"])
    assert not starts
    marker.write_text(generation, encoding="utf-8-sig")
    if disabled:
        monkeypatch.setenv(disabled, "1")
    assert main.main(["__source-start"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"]
    expected = (
        [((generation, slot, root), generation)]
        if configured and wanted and not disabled else []
    )
    assert starts == expected
    assert marker.read_text(encoding="utf-8-sig") == generation
    assert not (root / "deploy-manifest.json").exists()
    if configured:
        assert (bridges / "example.json").read_text(encoding="utf-8") == original_config


def test_application_setup_observes_an_explicit_precurrent_incumbent(tmp_path, monkeypatch):
    from zdd import routing

    module, root, slot, generation, bridges = _installed_callback(
        tmp_path, monkeypatch, "source-nested",
    )
    bridges.mkdir(parents=True)
    (bridges / "example.json").write_text(json.dumps({
        "server": {"type": "stdio", "command": ["fixture-upstream"]},
    }), encoding="utf-8")
    fixed = tmp_path / "configured-sockets" / "explicit.sock"
    fixed.parent.mkdir()
    monkeypatch.setenv("AGENT_MCP_SERVE_SOCKET", str(fixed))
    owner = routing.Endpoint(
        bind="127.0.0.1", port=23456, pid=1111, version="incumbent",
        generation=1, process_start_time="101",
    )
    observations = []

    def active(home, **kwargs):
        observations.append(home)
        return owner if home == fixed.parent else None

    monkeypatch.setattr(routing, "read_active_endpoint", active)
    assert module.source_setup()["wanted"] is True
    assert observations == [fixed.parent]
    starts = []
    monkeypatch.setattr(module, "_run_slot_startup", lambda *args, **kwargs: starts.append(args) or {"ok": True})
    (root / "current-version").write_text(generation, encoding="utf-8")
    assert module.source_start()["ok"]
    assert starts == [((generation, slot, root), generation)]


def test_application_setup_retains_separate_legacy_intent_without_adoption(tmp_path, monkeypatch):
    module, _root, _slot, _generation, bridges = _installed_callback(
        tmp_path, monkeypatch, "source-nested",
    )
    bridges.mkdir(parents=True)
    (bridges / "example.json").write_text(json.dumps({
        "server": {"type": "stdio", "command": ["fixture-upstream"]},
    }), encoding="utf-8")
    monkeypatch.delenv("AGENT_MCP_SERVE_SOCKET", raising=False)
    legacy = bridges.parent / "serve.sock.endpoint"
    original = b"opaque legacy state must not be rewritten"
    legacy.write_bytes(original)
    assert module.source_setup()["wanted"] is True
    assert module.source_fixed_socket() == bridges.parent / "current" / "serve.sock"
    assert legacy.read_bytes() == original


def test_application_start_refuses_a_different_selected_slot(
    tmp_path, monkeypatch, capsys,
):
    module, root, _slot, generation, bridges = _installed_callback(
        tmp_path, monkeypatch, "source-nested",
    )
    bridges.mkdir(parents=True)
    (bridges / "example.json").write_text(json.dumps({
        "server": {"type": "stdio", "command": ["fixture-upstream"]},
        "auth": {"kind": "none"},
    }), encoding="utf-8")
    monkeypatch.setattr(module, "source_serving_intent", lambda: True, raising=False)
    monkeypatch.setattr(
        module, "_run_slot_startup",
        lambda *args, **kwargs: pytest.fail("unselected slot must not start"),
        raising=False,
    )
    marker = root / "current-version"
    marker.write_text(generation, encoding="utf-8")
    assert main.main(["__source-setup"]) == 0
    capsys.readouterr()
    marker.write_text("different-generation", encoding="utf-8")
    with pytest.raises(module.ControlError, match="selected generation"):
        main.main(["__source-start"])
    assert marker.read_text(encoding="utf-8") == "different-generation"


@pytest.mark.parametrize("foreign", ["module", "root"])
def test_application_setup_refuses_before_writing_an_unowned_slot(
    tmp_path, monkeypatch, foreign,
):
    module, root, slot, generation, _bridges = _installed_callback(
        tmp_path, monkeypatch, "source-nested",
    )
    (root / "current-version").write_text(generation, encoding="utf-8")
    if foreign == "module":
        monkeypatch.setattr(module, "__file__", str(tmp_path / "checkout" / "cutover.py"))
    else:
        monkeypatch.setenv("AGENT_MCP_INSTALL_DIR", str(tmp_path / "another-installation"))
    monkeypatch.setattr(
        module, "_configured_readiness",
        lambda: pytest.fail("unowned callback must refuse before configuration discovery"),
    )
    with pytest.raises(module.ControlError, match="installed slot|installation root"):
        main.main(["__source-setup"])
    assert not (slot / ".mcp-serving-intent.json").exists()
    assert not list(slot.glob("*.tmp"))


@pytest.mark.parametrize("mismatch", [None, "generic", "pid", "version", "ready", "birth", "installation"])
def test_source_startup_health_pins_only_the_incoming_owner(
    tmp_path, monkeypatch, mismatch,
):
    from zdd import routing
    from zdd import diagnostics

    module, root, slot, generation, _bridges = _installed_callback(
        tmp_path, monkeypatch, "source-nested",
    )
    (root / "current-version").write_text(generation, encoding="utf-8")
    data_home = tmp_path / "data"
    fixed = tmp_path / "sockets" / "current" / "serve.sock"
    monkeypatch.setenv("AGENT_MCP_HOME", str(data_home))
    monkeypatch.setenv("AGENT_MCP_SERVE_SOCKET", str(fixed))
    monkeypatch.setattr(serve, "_HAS_AF_UNIX", False)
    monkeypatch.setattr(module, "pick_free_port", lambda: 23456)
    monkeypatch.setattr(
        diagnostics, "process_start_time",
        lambda pid: None if mismatch == "birth" else "100",
    )
    candidate = SimpleNamespace(pid=4321, returncode=None)
    candidate.poll = lambda: candidate.returncode
    candidate.wait = lambda timeout: 0
    candidate.terminate = lambda: pytest.fail("fixture shutdown must settle its incoming owner")
    requests = []
    seen = {}

    def spawn(context, port):
        seen["context"] = context
        serve._endpoint_path(context.new_socket_path).write_text(
            json.dumps({"port": 23457, "token": "incoming-data"}), encoding="utf-8",
        )
        return candidate

    def control(host, port, token, request, **kwargs):
        requests.append((host, port, token, request["op"]))
        assert port == 23456 and token == seen["context"].new_control_token
        if request["op"] == "shutdown":
            candidate.returncode = 0
            return {"ok": True}
        reply = {
            "ok": True, "pong": True, "ready": True, "pid": candidate.pid,
            "version": generation, "data_socket": str(seen["context"].new_socket_path),
            "runtime_root": str(root),
        }
        if mismatch == "generic":
            reply = {"ok": True, "pong": True}
        elif mismatch in {"pid", "version", "ready"}:
            reply[mismatch] = {"pid": 9876, "version": "outgoing-version", "ready": False}[mismatch]
        elif mismatch == "installation":
            reply["runtime_root"] = str(tmp_path / "other-installation")
        if mismatch:
            candidate.returncode = 0
        return reply

    monkeypatch.setattr(module, "spawn_passive_daemon", spawn)
    monkeypatch.setattr(module, "control_request", control)
    if mismatch:
        with pytest.raises(module.ControlError, match="identity is unavailable|failed readiness"):
            module._run_slot_startup((generation, slot, root), generation, health_timeout=1)
        assert requests[-1][-1] == "shutdown"
        assert not serve._endpoint_path(fixed).exists()
        assert routing.read_table(fixed.parent, strict=True) is None
    else:
        result = module._run_slot_startup(
            (generation, slot, root), generation, health_timeout=1,
        )
        assert result["ok"] and result["pid"] == candidate.pid
        assert [request[-1] for request in requests] == ["ping"]
        active = routing.read_active_endpoint(fixed.parent, verify_listener=False)
        assert active.pid == candidate.pid and active.version == generation
        assert active.process_start_time == "100"
        assert serve._read_endpoint(fixed) == serve._read_endpoint(result["data_socket"])
    assert seen["context"].config_home == data_home
    assert seen["context"].data_root == fixed.parent
    assert not (root / "deploy-manifest.json").exists()


def _published_incumbent(tmp_path, monkeypatch, *, incumbent=True, same_generation=False):
    import zdd
    from zdd import diagnostics, routing

    module, root, slot, generation, bridges = _installed_callback(
        tmp_path, monkeypatch, "source-nested",
    )
    (root / "current-version").write_text(generation, encoding="utf-8")
    (slot / ".mcp-serving-intent.json").write_text(json.dumps({
        "generation": generation, "wanted": True,
    }), encoding="utf-8")
    bridges.mkdir(parents=True)
    (bridges / "example.json").write_text(json.dumps({
        "server": {"type": "stdio", "command": ["fixture-upstream"]},
        "auth": {"kind": "none"},
    }), encoding="utf-8")
    home = tmp_path / "sockets" / "current"
    home.mkdir(parents=True)
    fixed = home / "serve.sock"
    monkeypatch.setenv("AGENT_MCP_SERVE_SOCKET", str(fixed))
    monkeypatch.setattr(serve, "_HAS_AF_UNIX", False)
    births, actors, requests = {1111: "101", 3333: "303"}, [], []
    locked = [False]
    original_lock = zdd.CutoverLock

    class Lock:
        def __init__(self, *args, **kwargs):
            self.inner = original_lock(*args, **kwargs)

        def __enter__(self):
            self.inner.__enter__()
            locked[0] = True
            return self

        def __exit__(self, *args):
            try:
                return self.inner.__exit__(*args)
            finally:
                locked[0] = False

    monkeypatch.setattr(zdd, "CutoverLock", Lock)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: births.get(pid))
    monkeypatch.setattr(routing, "_pid_alive", lambda pid: pid in births)
    ports = iter(range(23000, 23100))
    monkeypatch.setattr(module, "pick_free_port", lambda: next(ports))
    old_version = generation if same_generation else "incumbent-generation"
    if incumbent:
        routing.publish_active(
            home, bind="127.0.0.1", port=12002, pid=1111, version=old_version,
            generation=1, process_start_time="101",
        )
        serve._endpoint_path(fixed).write_text(
            json.dumps({"port": 12001, "token": "old-data"}), encoding="utf-8",
        )
        serve._control_token_path(home, 1111).write_text("old-control", encoding="utf-8")

    def data_owner(path):
        endpoint = serve._read_endpoint(path)
        if endpoint is None:
            return None
        if endpoint["port"] == 12001:
            return 1111
        if endpoint["port"] == 33001:
            return 3333
        return next((actor.pid for actor in actors if actor.data_port == endpoint["port"]), None)

    monkeypatch.setattr(module, "source_data_owner", data_owner)

    class Actor:
        def __init__(self, context, port):
            self.context, self.port = context, port
            self.pid = 2200 + len(actors)
            self.data_port = 30000 + self.pid
            self.returncode, self.waited = None, False
            births[self.pid] = str(self.pid)
            serve._endpoint_path(context.new_socket_path).write_text(
                json.dumps({"port": self.data_port, "token": "incoming-data"}),
                encoding="utf-8",
            )
            serve._control_token_path(home, self.pid).write_text(
                context.new_control_token, encoding="utf-8",
            )

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            assert self.returncode == 0, "native exit was not confirmed"
            self.waited = True
            return self.returncode

        def terminate(self):
            pytest.fail("the fixture incoming owner should settle its authenticated shutdown")

    def spawn(context, port):
        assert locked[0]
        actor = Actor(context, port)
        actors.append(actor)
        return actor

    def control(host, port, token, request, **kwargs):
        assert locked[0], "owner readiness must be checked under the cutover lock"
        requests.append((port, request["op"]))
        if port == 12002:
            assert token == "old-control" and request == {"op": "ping"}
            return {
                "ok": True, "pong": True, "ready": True, "pid": 1111,
                "version": old_version, "data_socket": str(fixed),
                "runtime_root": str(root),
            }
        actor = next(actor for actor in actors if actor.port == port)
        assert token == actor.context.new_control_token
        if request["op"] == "shutdown":
            actor.returncode = 0
            births.pop(actor.pid, None)
            return {"ok": True}
        assert request == {"op": "ping"}
        return {
            "ok": True, "pong": True, "ready": True, "pid": actor.pid,
            "version": generation, "data_socket": str(actor.context.new_socket_path),
            "runtime_root": str(root),
        }

    monkeypatch.setattr(module, "spawn_passive_daemon", spawn)
    monkeypatch.setattr(module, "control_request", control)
    return SimpleNamespace(
        module=module, root=root, slot=slot, generation=generation, home=home, fixed=fixed,
        actors=actors, requests=requests, births=births, control=control,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX socket links")
@pytest.mark.parametrize("changed", [False, True])
def test_incoming_publication_replaces_retained_missing_socket_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: bool,
) -> None:
    from zdd import routing

    fixture = _published_incumbent(tmp_path.resolve(), monkeypatch, incumbent=False)
    monkeypatch.chdir(fixture.home)
    monkeypatch.setattr(serve, "_HAS_AF_UNIX", True)
    fixture.fixed.symlink_to("serve-g1.sock")
    before = fixture.module.source_handle_state(fixture.fixed)
    spawn = fixture.module.spawn_passive_daemon

    with contextlib.ExitStack() as resources:
        def spawn_socket(context, port):
            assert fixture.fixed.resolve(strict=False) != context.new_socket_path
            actor = spawn(context, port)
            listener = resources.enter_context(
                cutover._socket.socket(cutover._socket.AF_UNIX, cutover._socket.SOCK_STREAM),
            )
            listener.bind(context.new_socket_path.name)
            assert not fixture.fixed.exists()
            return actor

        def data_owner(path: Path) -> int | None:
            target = path.resolve(strict=False)
            return next((
                actor.pid for actor in fixture.actors if actor.context.new_socket_path == target
            ), None)

        monkeypatch.setattr(fixture.module, "spawn_passive_daemon", spawn_socket)
        monkeypatch.setattr(fixture.module, "source_data_owner", data_owner)
        control = fixture.control

        def guarded_control(host, port, token, request, **kwargs):
            reply = control(host, port, token, request, **kwargs)
            if changed and request["op"] == "ping":
                fixture.fixed.unlink()
                fixture.fixed.symlink_to("racing.sock")
            return reply

        monkeypatch.setattr(fixture.module, "control_request", guarded_control)
        if changed:
            with pytest.raises(fixture.module.ControlError, match="ambiguous"):
                fixture.module.source_start()
            assert os.readlink(fixture.fixed) == "racing.sock"
            assert routing.read_table(fixture.home, strict=True) is None
            assert not (fixture.home / "serve-g1.sock").exists()
            return
        result = fixture.module.source_start()
        assert result["ok"]
        assert len(fixture.actors) == 1
        actor = fixture.actors[0]
        assert result["pid"] == actor.pid
        assert fixture.fixed.resolve(strict=True) == actor.context.new_socket_path
        assert fixture.module.source_handle_state(fixture.fixed) != before
        assert not (fixture.home / "serve-g1.sock").exists()
        active = routing.read_active_endpoint(
            fixture.home, verify_listener=False,
        )
        assert active.pid == actor.pid and active.generation == 2


@pytest.mark.parametrize("identity", ["other", "missing", "invalid"])
def test_same_generation_foreign_installation_is_not_reused_or_replaced(
    tmp_path, monkeypatch, identity,
):
    from zdd import routing

    fixture = _published_incumbent(tmp_path, monkeypatch, same_generation=True)
    before_route = routing.read_table(fixture.home, strict=True)
    before_handle = fixture.module.source_handle_state(fixture.fixed)

    def control(*args, **kwargs):
        reply = fixture.control(*args, **kwargs)
        if identity == "other":
            reply["runtime_root"] = str(tmp_path / "other-installation")
        elif identity == "invalid":
            reply["runtime_root"] = []
        else:
            reply.pop("runtime_root", None)
        return reply

    monkeypatch.setattr(fixture.module, "control_request", control)
    with pytest.raises((fixture.module.ControlError, routing.ActivePublicationRefused),
                       match="installation"):
        fixture.module._run_slot_startup(
            (fixture.generation, fixture.slot, fixture.root), fixture.generation,
            health_timeout=1,
        )
    assert not fixture.actors
    assert routing.read_table(fixture.home, strict=True) == before_route
    assert fixture.module.source_handle_state(fixture.fixed) == before_handle


def test_installed_startup_publishes_only_the_exact_requested_handle(tmp_path, monkeypatch):
    from zdd import routing

    fixture = _published_incumbent(tmp_path, monkeypatch, incumbent=False)
    requested = tmp_path / "requested" / "exact.sock"
    requested.parent.mkdir()
    result = fixture.module._run_slot_startup(
        (fixture.generation, fixture.slot, fixture.root), fixture.generation,
        health_timeout=1, fixed_socket=requested,
    )
    assert result["ok"]
    assert serve._read_endpoint(requested) == serve._read_endpoint(result["data_socket"])
    assert routing.read_active_endpoint(requested.parent, verify_listener=False).pid == result["pid"]
    assert not serve._endpoint_path(fixture.fixed).exists()
    assert routing.read_table(fixture.home, strict=True) is None
    assert fixture.actors[0].context.fixed_socket == requested
    assert fixture.actors[0].context.config_home == tmp_path / "data"


def test_source_start_missing_route_preserves_the_live_generation_handle(tmp_path, monkeypatch):
    from zdd import routing

    fixture = _published_incumbent(tmp_path, monkeypatch)
    incumbent_socket = fixture.home / "serve-g1.sock"
    incumbent_endpoint = serve._endpoint_path(incumbent_socket)
    fixed_endpoint = serve._endpoint_path(fixture.fixed)
    incumbent_endpoint.write_bytes(fixed_endpoint.read_bytes())
    old_target = incumbent_endpoint.read_bytes()
    old_fixed = fixture.module.source_handle_state(fixture.fixed)
    (fixture.home / "active.json").unlink()
    assert fixture.module.source_data_owner(fixture.fixed) == 1111
    with pytest.raises(routing.ActivePublicationRefused):
        fixture.module.source_start()
    assert not fixture.actors, "unattributable live data must refuse before passive spawn"
    assert not fixture.requests
    assert incumbent_endpoint.read_bytes() == old_target
    assert fixture.module.source_handle_state(fixture.fixed) == old_fixed
    assert routing.read_table(fixture.home, strict=True) is None


@pytest.mark.parametrize("retained_route", [False, True])
def test_source_start_replaces_dead_handle_without_incumbent_route(
    tmp_path, monkeypatch, retained_route,
):
    from zdd import routing

    fixture = _published_incumbent(tmp_path, monkeypatch)
    table = routing.read_table(fixture.home, strict=True)
    previous = table.pop("active")
    if retained_route:
        table["previous"] = previous
        (fixture.home / "active.json").write_text(json.dumps(table), encoding="utf-8")
    else:
        (fixture.home / "active.json").unlink()
    owner = fixture.module.source_data_owner
    monkeypatch.setattr(
        fixture.module, "source_data_owner",
        lambda path: None if serve._read_endpoint(path)["port"] == 12001 else owner(path),
    )

    result = fixture.module.source_start()

    assert result["ok"]
    assert routing.read_active_endpoint(fixture.home, verify_listener=False).pid == result["pid"]
    assert serve._read_endpoint(fixture.fixed) == serve._read_endpoint(result["data_socket"])
    assert fixture.actors[0].context.config_home == tmp_path / "data"
    assert (12002, "shutdown") not in fixture.requests


def test_source_start_dead_handle_refuses_unavailable_native_inspection(tmp_path, monkeypatch):
    fixture = _published_incumbent(tmp_path, monkeypatch)
    (fixture.home / "active.json").unlink()
    original = fixture.module.source_handle_state(fixture.fixed)

    def unavailable(path):
        raise fixture.module.ControlError("Native TCP listener inspection failed")

    monkeypatch.setattr(fixture.module, "source_data_owner", unavailable)
    with pytest.raises(fixture.module.ControlError, match="listener inspection"):
        fixture.module.source_start()
    assert not fixture.actors
    assert fixture.module.source_handle_state(fixture.fixed) == original


def test_source_start_dead_handle_preserves_listener_appearing_before_publication(
    tmp_path, monkeypatch,
):
    from zdd import routing

    fixture = _published_incumbent(tmp_path, monkeypatch)
    (fixture.home / "active.json").unlink()
    original = fixture.module.source_handle_state(fixture.fixed)
    started = [False]

    def control(*args, **kwargs):
        reply = fixture.control(*args, **kwargs)
        started[0] = True
        return reply

    monkeypatch.setattr(fixture.module, "control_request", control)
    monkeypatch.setattr(fixture.module, "source_data_owner", lambda path: 3333 if started[0] else None)
    with pytest.raises(routing.ActivePublicationRefused, match="serving owner"):
        fixture.module.source_start()
    assert fixture.module.source_handle_state(fixture.fixed) == original
    assert fixture.actors[0].returncode == 0 and fixture.actors[0].waited
    assert (12002, "shutdown") not in fixture.requests


@pytest.mark.parametrize("occupied_kind", ["socket", "endpoint"])
def test_source_start_does_not_reuse_an_occupied_generation_path(
    tmp_path, monkeypatch, occupied_kind,
):
    fixture = _published_incumbent(tmp_path, monkeypatch)
    occupied_socket = fixture.home / "serve-g2.sock"
    occupied = (occupied_socket if occupied_kind == "socket"
                else serve._endpoint_path(occupied_socket))
    original = b"existing generation owned by another instance"
    occupied.write_bytes(original)
    result = fixture.module.source_start()
    assert result["ok"]
    assert fixture.actors[0].context.new_socket_path != occupied_socket
    assert occupied.read_bytes() == original
    assert serve._read_endpoint(fixture.fixed) == serve._read_endpoint(result["data_socket"])
    assert (12002, "shutdown") not in fixture.requests


@pytest.mark.parametrize("incumbent", [False, True])
def test_source_start_restores_uncommitted_publication_and_retry_succeeds(
    tmp_path, monkeypatch, incumbent,
):
    from zdd import routing

    fixture = _published_incumbent(tmp_path, monkeypatch, incumbent=incumbent)
    original_handle = fixture.module.source_handle_state(fixture.fixed)
    flip = serve.flip_data_handle

    def fail_before_replace(*args, **kwargs):
        raise OSError("injected failure before data replacement")

    monkeypatch.setattr(serve, "flip_data_handle", fail_before_replace)
    with pytest.raises(OSError, match="before data replacement"):
        fixture.module.source_start()
    active = routing.Endpoint.from_dict(routing.read_table(fixture.home, strict=True).get("active", {}))
    assert (active.pid if active else None) == (1111 if incumbent else None)
    assert fixture.module.source_handle_state(fixture.fixed) == original_handle
    assert fixture.actors[0].returncode == 0 and fixture.actors[0].waited
    monkeypatch.setattr(serve, "flip_data_handle", flip)
    result = fixture.module.source_start()
    assert result["ok"] and result["pid"] == fixture.actors[1].pid
    assert routing.read_active_endpoint(fixture.home, verify_listener=False).pid == result["pid"]
    assert serve._read_endpoint(fixture.fixed) == serve._read_endpoint(result["data_socket"])
    assert (12002, "shutdown") not in fixture.requests


def test_source_start_recognizes_observable_commit_after_flip_raises(tmp_path, monkeypatch):
    from zdd import routing

    fixture = _published_incumbent(tmp_path, monkeypatch)
    flip = serve.flip_data_handle

    def fail_after_replace(*args, **kwargs):
        flip(*args, **kwargs)
        raise OSError("injected failure after data replacement")

    monkeypatch.setattr(serve, "flip_data_handle", fail_after_replace)
    result = fixture.module.source_start()
    assert result["ok"] and result["pid"] == fixture.actors[0].pid
    assert fixture.actors[0].poll() is None
    assert routing.read_active_endpoint(fixture.home, verify_listener=False).pid == result["pid"]
    assert serve._read_endpoint(fixture.fixed) == serve._read_endpoint(result["data_socket"])
    assert all(operation != "shutdown" for _port, operation in fixture.requests)


def test_source_start_retains_a_committed_owner_when_readiness_becomes_unknown(
    tmp_path, monkeypatch,
):
    from zdd import routing

    fixture = _published_incumbent(tmp_path, monkeypatch)
    flip = serve.flip_data_handle

    def unknown_after_replace(*args, **kwargs):
        flip(*args, **kwargs)
        fixture.births.pop(fixture.actors[0].pid)
        raise OSError("injected observation failure after data replacement")

    monkeypatch.setattr(serve, "flip_data_handle", unknown_after_replace)
    with pytest.raises(fixture.module.ControlError, match="committed.*readiness is unconfirmed"):
        fixture.module.source_start()
    actor = fixture.actors[0]
    assert actor.poll() is None
    active = routing.read_active_endpoint(fixture.home, verify_listener=False)
    assert active.pid == actor.pid and active.version == fixture.generation
    assert serve._read_endpoint(fixture.fixed) == serve._read_endpoint(actor.context.new_socket_path)
    assert all(operation != "shutdown" for _port, operation in fixture.requests)


def test_source_start_failure_leaves_a_foreign_newer_owner_untouched(tmp_path, monkeypatch):
    from zdd import routing

    fixture = _published_incumbent(tmp_path, monkeypatch)

    def foreign_publication(*args, **kwargs):
        routing.publish_active(
            fixture.home, bind="127.0.0.1", port=33002, pid=3333, version="foreign-generation",
            demote_existing=True, process_start_time="303",
        )
        serve._endpoint_path(fixture.fixed).write_text(
            json.dumps({"port": 33001, "token": "foreign-data"}), encoding="utf-8",
        )
        raise OSError("injected foreign publication before replacement")

    monkeypatch.setattr(serve, "flip_data_handle", foreign_publication)
    with pytest.raises(OSError):
        fixture.module.source_start()
    active = routing.read_active_endpoint(fixture.home, verify_listener=False)
    assert (active.pid, active.process_start_time, active.version) == (
        3333, "303", "foreign-generation",
    )
    assert serve._read_endpoint(fixture.fixed)["port"] == 33001
    assert fixture.actors[0].poll() is None
    assert all(operation != "shutdown" for _port, operation in fixture.requests)


def test_source_start_recovery_cannot_overwrite_a_racing_foreign_route(tmp_path, monkeypatch):
    from zdd import routing

    fixture = _published_incumbent(tmp_path, monkeypatch)
    original_handle = fixture.module.source_handle_state(fixture.fixed)
    publish = routing.publish_active_with_previous_guarded

    def racing_restore(*args, **kwargs):
        if kwargs["pid"] == 1111:
            routing.publish_active(
                fixture.home, bind="127.0.0.1", port=33002, pid=3333,
                version="foreign-generation", demote_existing=True, process_start_time="303",
            )
        return publish(*args, **kwargs)

    def fail_before_replace(*args, **kwargs):
        raise OSError("injected pre-commit replacement failure")

    monkeypatch.setattr(routing, "publish_active_with_previous_guarded", racing_restore)
    monkeypatch.setattr(serve, "flip_data_handle", fail_before_replace)
    with pytest.raises(routing.ActivePublicationRefused):
        fixture.module.source_start()
    active = routing.read_active_endpoint(fixture.home, verify_listener=False)
    assert (active.pid, active.process_start_time, active.version) == (
        3333, "303", "foreign-generation",
    )
    assert fixture.module.source_handle_state(fixture.fixed) == original_handle
    assert fixture.actors[0].poll() is None
    assert all(operation != "shutdown" for _port, operation in fixture.requests)


def test_source_start_reuses_the_exact_healthy_selected_native_owner(tmp_path, monkeypatch):
    fixture = _published_incumbent(tmp_path, monkeypatch, same_generation=True)
    route = (fixture.home / "active.json").read_bytes()
    fixed = serve._endpoint_path(fixture.fixed).read_bytes()
    result = fixture.module.source_start()
    assert result["ok"] and result["pid"] == 1111
    assert result["version"] == fixture.generation and result["data_socket"] == str(fixture.fixed)
    assert not fixture.actors
    assert (fixture.home / "active.json").read_bytes() == route
    assert serve._endpoint_path(fixture.fixed).read_bytes() == fixed
    assert fixture.requests == [(12002, "ping")]


@pytest.mark.parametrize("socket_inode", [False, True])
def test_source_reuse_checks_an_unredirected_unix_data_handle(tmp_path, monkeypatch, socket_inode):
    from agent_mcp import source_serving

    fixed = tmp_path / "serve.sock"
    monkeypatch.setattr(serve, "_HAS_AF_UNIX", True)
    monkeypatch.setattr(Path, "is_socket", lambda path: socket_inode and path == fixed)
    assert source_serving.data_handle_matches(fixed, fixed) is socket_inode


@pytest.mark.parametrize("defect", ["not-ready", "missing-token", "birth", "foreign-data"])
def test_source_start_does_not_reuse_partial_same_generation_proof(tmp_path, monkeypatch, defect):
    from zdd import routing

    fixture = _published_incumbent(tmp_path, monkeypatch, same_generation=True)
    original_handle = fixture.module.source_handle_state(fixture.fixed)
    if defect == "not-ready":
        def not_ready(*args, **kwargs):
            reply = fixture.control(*args, **kwargs)
            if args[1] == 12002:
                reply["ready"] = False
            return reply
        monkeypatch.setattr(fixture.module, "control_request", not_ready)
    elif defect == "missing-token":
        serve._control_token_path(fixture.home, 1111).unlink()
    elif defect == "birth":
        fixture.births.pop(1111)
    else:
        monkeypatch.setattr(fixture.module, "source_data_owner", lambda path: 3333)
    if defect in {"birth", "foreign-data", "missing-token"}:
        with pytest.raises((routing.ActivePublicationRefused, fixture.module.ControlError)):
            fixture.module.source_start()
        assert not fixture.actors, "unqualified incumbent ownership must refuse before spawn"
        assert fixture.module.source_handle_state(fixture.fixed) == original_handle
        assert routing.read_active_endpoint(fixture.home, verify_listener=False).pid == 1111
    else:
        result = fixture.module.source_start()
        assert result["ok"] and result["pid"] == fixture.actors[0].pid != 1111
    assert (12002, "shutdown") not in fixture.requests


def test_source_start_does_not_reuse_an_owner_after_selection_changes(tmp_path, monkeypatch):
    fixture = _published_incumbent(tmp_path, monkeypatch, same_generation=True)

    def changed_selection(*args, **kwargs):
        reply = fixture.control(*args, **kwargs)
        if args[1] == 12002:
            (fixture.root / "current-version").write_text("other-generation", encoding="utf-8")
        return reply

    monkeypatch.setattr(fixture.module, "control_request", changed_selection)
    with pytest.raises(fixture.module.ControlError, match="selected generation"):
        fixture.module.source_start()
    assert not fixture.actors


@pytest.mark.parametrize("mismatch", [None, "ready", "pid", "version", "birth", "route", "data", "installation"])
def test_outgoing_self_retirement_requires_the_selected_ready_data_owner(
    tmp_path, monkeypatch, mismatch,
):
    from zdd import diagnostics, routing

    installation = tmp_path / "installation"
    installation.mkdir()
    (installation / "current-version").write_text("incoming", encoding="utf-8-sig")
    home = tmp_path / "sockets" / "current"
    home.mkdir(parents=True)
    fixed = home / "serve.sock"
    incoming = home / "serve-g2.sock"
    server = serve.Server(fixed, enable_lease=False, version="outgoing")
    server._runtime_root = installation
    monkeypatch.setenv("AGENT_MCP_SERVE_SOCKET", str(fixed))
    monkeypatch.setattr(serve, "_HAS_AF_UNIX", False)
    endpoint = routing.Endpoint(
        bind="127.0.0.1", port=23456, pid=4321, version="incoming",
        generation=2, process_start_time="100",
    )
    table = {"active": endpoint.to_dict()}
    monkeypatch.setattr(routing, "read_table", lambda *args, **kwargs: table)
    monkeypatch.setattr(
        diagnostics, "process_start_time",
        lambda pid: None if mismatch == "birth" else "100",
    )
    serve._control_token_path(home, endpoint.pid).write_text("control-token", encoding="utf-8")
    for path in (fixed, incoming):
        serve._endpoint_path(path).write_text(
            json.dumps({"port": 23457, "token": "incoming-data"}), encoding="utf-8",
        )
    if mismatch == "data":
        serve._endpoint_path(fixed).write_text(
            json.dumps({"port": 23458, "token": "outgoing-data"}), encoding="utf-8",
        )

    def control(host, port, token, request, **kwargs):
        assert (host, port, token, request) == (
            "127.0.0.1", endpoint.port, "control-token", {"op": "ping"},
        )
        reply = {
            "ok": True, "pong": True, "ready": True, "pid": endpoint.pid,
            "version": "incoming", "data_socket": str(incoming),
            "runtime_root": str(installation),
        }
        if mismatch in {"ready", "pid", "version"}:
            reply[mismatch] = {"ready": False, "pid": 9876, "version": "outgoing"}[mismatch]
        if mismatch == "installation":
            reply["runtime_root"] = str(tmp_path / "other-installation")
        if mismatch == "route":
            table["active"] = {**endpoint.to_dict(), "pid": 9876}
        return reply

    monkeypatch.setattr(cutover, "control_request", control)
    assert server._ready_replacement() is (mismatch is None)


def test_incoming_spawn_retains_the_original_configuration_home(tmp_path, monkeypatch):
    import agent_procutil

    home = tmp_path / "configuration"
    sockets = tmp_path / "sockets"
    context = cutover._CutoverContext(
        sockets, sockets / "serve-g2.sock", sockets / "serve-g2.log",
        "incoming-control", None, {}, config_home=home,
    )
    captured = {}

    def spawn(argv, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(pid=4321)

    monkeypatch.setattr(agent_procutil, "spawn_daemon", spawn)
    cutover.spawn_passive_daemon(context, 23456)
    assert captured["cwd"] == home.resolve()
    assert captured["env_overrides"]["AGENT_MCP_HOME"] == str(home.resolve())
    assert captured["instance"].runtime_root == sockets.resolve()


def test_incoming_health_requires_a_serving_data_listener(tmp_path):
    server = serve.Server(
        tmp_path / "serve-g2.sock", enable_lease=False, version="incoming",
        control_token="control-token",
    )
    request = {"op": "ping", "token": "control-token"}
    assert server._control_dispatch(request)["ready"] is False
    server._server = SimpleNamespace(is_serving=lambda: True)
    reply = server._control_dispatch(request)
    assert reply["ready"] is True
    assert reply["version"] == "incoming"
    assert reply["data_socket"] == str(server.socket_path)
    server._draining = True
    assert server._control_dispatch(request)["ready"] is False


async def test_paused_ordinary_start_cannot_replace_committed_successor(tmp_path, monkeypatch):
    from agent_procutil.daemon_instances import daemon_test_lifecycle, inspect_instances
    from zdd.diagnostics import process_start_time
    from zdd import routing

    birth = process_start_time(os.getpid())
    fixture = _published_incumbent(tmp_path, monkeypatch, incumbent=False)
    fixture.births[os.getpid()] = birth
    (fixture.root / "current-version").write_text("outgoing", encoding="utf-8")
    server = serve.Server(fixture.fixed, version="outgoing")
    server._runtime_root = fixture.root
    server._version = "outgoing"
    server._stop.set()
    entered, release = threading.Event(), threading.Event()
    acquire = server._acquire_lease

    def paused_acquire():
        entered.set()
        assert release.wait(timeout=5)
        return acquire()

    monkeypatch.setattr(server, "_acquire_lease", paused_acquire)
    with daemon_test_lifecycle(
        owner="mcp-startup-race", registry_root=tmp_path / "instances",
    ) as environment:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        task = asyncio.create_task(asyncio.to_thread(
            lambda: asyncio.run(server.serve_forever()),
        ))
        try:
            async def until_paused():
                while not entered.is_set():
                    if task.done():
                        await task
                        pytest.fail("ordinary startup exited before lease admission")
                    await asyncio.sleep(0.001)
            await asyncio.wait_for(until_paused(), 5)
            (fixture.root / "current-version").write_text(
                fixture.generation, encoding="utf-8",
            )
            result = fixture.module.source_start()
            assert result["ok"]
            endpoint = serve._endpoint_path(fixture.fixed)
            before = endpoint.read_bytes()
            route = routing.read_table(fixture.home, strict=True)
            release.set()
            await asyncio.wait_for(asyncio.shield(task), 5)
            assert endpoint.read_bytes() == before
            assert routing.read_table(fixture.home, strict=True) == route
            assert server._server is None
            assert server._lease is None
            assert inspect_instances(tmp_path / "instances")[0]["state"] == "finished"
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("passive", [False, True])
async def test_startup_transaction_keeps_passive_admission_independent(
    tmp_path, monkeypatch, passive,
):
    from agent_procutil.daemon_instances import daemon_test_lifecycle
    from zdd import CutoverLock, routing

    root = tmp_path / "installation"
    root.mkdir()
    (root / "current-version").write_text("incoming", encoding="utf-8")
    fixed = tmp_path / "sockets" / "current" / "serve.sock"
    fixed.parent.mkdir(parents=True)
    server = serve.Server(fixed, version="incoming", passive=passive)
    server._runtime_root = root
    server._stop.set()
    with daemon_test_lifecycle(
        owner="mcp-startup-contention", registry_root=tmp_path / "instances",
    ) as environment:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        with CutoverLock(fixed.parent):
            await asyncio.wait_for(server.serve_forever(), 5)
        assert (server._server is not None) is passive
        assert server._lease is None
        assert serve._read_endpoint(fixed) is None
        assert routing.read_table(fixed.parent) is None


async def test_ordinary_startup_releases_transaction_before_resident_wait(tmp_path, monkeypatch):
    from agent_procutil.daemon_instances import daemon_test_lifecycle
    from zdd import CutoverLock

    root = tmp_path / "installation"
    root.mkdir()
    (root / "current-version").write_text("incoming", encoding="utf-8")
    fixed = tmp_path / "sockets" / "current" / "serve.sock"
    server = serve.Server(fixed, version="incoming")
    server._runtime_root = root
    with daemon_test_lifecycle(
        owner="mcp-startup-release", registry_root=tmp_path / "instances",
    ) as environment:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        task = asyncio.create_task(server.serve_forever())
        try:
            async def until_ready():
                while server._control_server is None or server._lease is None:
                    if task.done():
                        await task
                    await asyncio.sleep(0.001)
            await asyncio.wait_for(until_ready(), 5)
            with CutoverLock(fixed.parent):
                assert not task.done()
        finally:
            server._stop.set()
            await asyncio.wait_for(asyncio.shield(task), 5)
        assert server._lease is None
        assert serve._read_endpoint(fixed) is None


@pytest.mark.parametrize("boundary", ["listener", "route"])
async def test_selection_change_during_bind_refuses_route_and_settles_listeners(
    tmp_path, monkeypatch, boundary,
):
    from agent_procutil.daemon_instances import daemon_test_lifecycle
    from zdd import CutoverLock, routing

    root = tmp_path / "installation"
    root.mkdir()
    selector = root / "current-version"
    selector.write_text("outgoing", encoding="utf-8")
    fixed = tmp_path / "sockets" / "current" / "serve.sock"
    server = serve.Server(fixed, version="outgoing")
    server._runtime_root = root
    server._stop.set()
    start_control = server._start_control_listener
    close_pool = server.pool.close_all

    async def close_after_transaction_release():
        with CutoverLock(fixed.parent):
            pass
        await close_pool()

    monkeypatch.setattr(server.pool, "close_all", close_after_transaction_release)

    async def supersede_before_control():
        assert server._server.is_serving()
        if boundary == "listener":
            selector.write_text("incoming", encoding="utf-8")
        await start_control()

    monkeypatch.setattr(server, "_start_control_listener", supersede_before_control)
    if boundary == "route":
        route_lock = routing._routing_lock

        @contextlib.contextmanager
        def supersede_under_route_lock(*args, **kwargs):
            with route_lock(*args, **kwargs):
                selector.write_text("incoming", encoding="utf-8")
                yield

        monkeypatch.setattr(routing, "_routing_lock", supersede_under_route_lock)
    with daemon_test_lifecycle(
        owner="mcp-startup-selection", registry_root=tmp_path / "instances",
    ) as environment:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        with pytest.raises(
            (cutover.ControlError, routing.ActivePublicationRefused), match="selected generation",
        ):
            await asyncio.wait_for(server.serve_forever(), 5)
        assert not server._server.is_serving()
        assert not server._control_server.is_serving()
        assert server._lease is None
        assert routing.read_table(fixed.parent) is None
        assert not serve._control_token_path(fixed.parent, os.getpid()).exists()
        with CutoverLock(fixed.parent):
            assert serve._read_endpoint(fixed) is None


@pytest.mark.portfolio_tier("T3")
@pytest.mark.timeout(30)
def test_native_ordinary_listener_publication_and_transaction_release(tmp_path):
    from agent_procutil.daemon_instances import daemon_test_lifecycle

    program = """
import asyncio, json, os
from pathlib import Path
from agent_mcp import cutover, serve
from agent_procutil.daemon_instances import inspect_instances
from zdd import CutoverLock, routing

async def main():
    root = Path.cwd()
    (root / "current-version").write_text("fixture", encoding="utf-8")
    server = serve.Server(Path("serve.sock"), version="fixture")
    server._runtime_root = root
    task = asyncio.create_task(server.serve_forever())
    try:
        async def ready():
            while server._control_server is None or server._lease is None:
                if task.done():
                    await task
                await asyncio.sleep(0.001)
        await asyncio.wait_for(ready(), 5)
        endpoint = routing.read_active_endpoint(root, verify_listener=False)
        assert endpoint.pid == os.getpid()
        owner = cutover.source_data_owner(Path("serve.sock"))
        assert owner == os.getpid()
        with CutoverLock(root):
            assert not task.done()
    finally:
        server.stop()
        await asyncio.wait_for(asyncio.shield(task), 5)
    assert server._lease is None
    assert not server._server.is_serving()
    assert not server._control_server.is_serving()
    assert not Path("serve.sock").exists()
    assert not serve._endpoint_path(Path("serve.sock")).exists()
    assert not routing.read_table(root).get("active")
    return {"ready": True, "native_owner": owner, "pid": os.getpid(), "settled": True}

result = asyncio.run(main())
records = inspect_instances(Path.cwd() / "instances")
assert records and all(record["state"] == "finished" for record in records)
print(json.dumps(result))
"""
    cleanup = []
    with daemon_test_lifecycle(
        owner="mcp-native-startup", registry_root=tmp_path / "instances",
        cleanup_results=cleanup,
    ) as environment:
        result = subprocess.run(
            [sys.executable, "-c", program], cwd=tmp_path,
            env={**os.environ, **environment, "AGENT_MCP_HOME": str(tmp_path)},
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        proof = json.loads(result.stdout)
        assert proof["ready"] and proof["settled"]
        assert proof["native_owner"] == proof["pid"] != os.getpid()
    assert cleanup and all(outcome.status != "held" for outcome in cleanup)


async def test_replacement_inspection_settles_before_cancelled_sweeper_exits(
    tmp_path, monkeypatch,
):
    server = serve.Server(tmp_path / "serve.sock", enable_lease=False)
    server._runtime_root = tmp_path
    entered, release = threading.Event(), threading.Event()

    def inspect():
        entered.set()
        assert release.wait(timeout=5)
        return False

    monkeypatch.setattr(server, "_ready_replacement", inspect)
    monkeypatch.setattr(serve, "_SWEEP_INTERVAL", 0.001)
    task = asyncio.create_task(server._sweep_loop())
    try:
        async def until_entered():
            while not entered.is_set():
                await asyncio.sleep(0.001)
        await asyncio.wait_for(until_entered(), timeout=5)
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=5)
