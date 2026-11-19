"""Private native-fixture access to the existing owned systemd user socket."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import stat
import sys
from types import SimpleNamespace

import pytest

from agent_procutil import linux_daemon_scope
import plugin_test_lifecycle as lifecycle


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native scope uses Unix socket aliases")
def test_native_scope_bus_keeps_runtime_private_and_connects_to_the_selected_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = tmp_path / "native-private"
    runtime = tmp_path / "sandbox" / "run"
    runtime.mkdir(parents=True, mode=0o700)
    monkeypatch.chdir(tmp_path)
    selected = f"/run/user/{int(getattr(os, 'getuid')())}/systemd/private"
    monkeypatch.setattr(linux_daemon_scope, "caller_unit", lambda: "fixture.service")
    monkeypatch.setattr(
        lifecycle, "Path", lambda value: native if str(value) == selected else Path(value),
    )
    environment = {
        "XDG_RUNTIME_DIR": str(runtime), "DBUS_SESSION_BUS_ADDRESS": "selected-user-bus",
    }
    original = dict(environment)
    family = int(getattr(socket, "AF_UNIX"))
    with socket.socket(family) as server, socket.socket(family) as client:
        server.settimeout(2)
        client.settimeout(2)
        server.bind(native.name)
        server.listen(1)
        lifecycle._bind_native_scope_bus(environment, runtime.parent)
        alias = runtime / "systemd" / "private"
        assert alias.is_symlink() and alias.resolve() == native
        assert environment == original
        client.connect(str(alias.relative_to(tmp_path)))
        connection, _ = server.accept()
        with connection:
            connection.sendall(b"owned-socket")
            assert client.recv(32) == b"owned-socket"


@pytest.mark.parametrize("failure", ["missing", "ordinary", "foreign", "outside", "existing"])
def test_native_scope_bus_refuses_unqualified_inputs_without_replacing_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    runtime = (tmp_path if failure == "outside" else sandbox) / "run"
    runtime.mkdir()
    native = tmp_path / "native-private"
    selected = "/run/user/1001/systemd/private"
    original_lstat = Path.lstat

    def metadata(path: Path) -> os.stat_result:
        if path != native:
            return original_lstat(path)
        if failure == "missing":
            raise FileNotFoundError
        return os.stat_result((
            stat.S_IFREG if failure == "ordinary" else stat.S_IFSOCK,
            0, 0, 1, 1002 if failure == "foreign" else 1001, 0, 0, 0, 0, 0,
        ))

    monkeypatch.setattr(lifecycle, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(lifecycle, "os", SimpleNamespace(getuid=lambda: 1001))
    monkeypatch.setattr(lifecycle, "Path", lambda value: native if str(value) == selected else Path(value))
    monkeypatch.setattr(Path, "lstat", metadata)
    monkeypatch.setattr(linux_daemon_scope, "caller_unit", lambda: "fixture.service")
    existing = runtime / "systemd"
    if failure == "existing":
        existing.mkdir()
        (existing / "private").write_bytes(b"preserved")
    environment = {"XDG_RUNTIME_DIR": str(runtime), "DBUS_SESSION_BUS_ADDRESS": "selected"}
    with pytest.raises((ValueError, FileExistsError)):
        lifecycle._bind_native_scope_bus(environment, sandbox)
    if failure == "existing":
        assert (existing / "private").read_bytes() == b"preserved"
    else:
        assert not existing.exists()
