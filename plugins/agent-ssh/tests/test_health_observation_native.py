"""Finite real-client control without an SSH account, key or production route."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import socket
import threading
import time

import pytest

from agent_ssh.__main__ import main


@pytest.mark.skipif(shutil.which("ssh") is None, reason="Native OpenSSH client unavailable")
def test_real_cli_bounds_a_live_unresponsive_ssh_route(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    profile = tmp_path / "direct.conf"
    identity = tmp_path / "synthetic key"
    known_hosts = tmp_path / "known hosts"
    identity.write_text("not a private key", encoding="utf-8")
    known_hosts.write_text("# no host keys\n", encoding="utf-8")
    stopped = threading.Event()
    accepted = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        port = listener.getsockname()[1]
        profile.write_text(
            "Host fixture\n  HostName 127.0.0.1\n  User fixture\n"
            f'  Port {port}\n  IdentityFile "{identity}"\n',
            encoding="utf-8",
        )
        original = {path: path.read_bytes() for path in (profile, identity, known_hosts)}

        def serve() -> None:
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            with connection:
                accepted.set()
                connection.sendall(b"SSH-2.0-fixture\r\n")
                stopped.wait(5)

        server = threading.Thread(target=serve)
        server.start()
        started = time.monotonic()
        try:
            code = main([
                "observe", "--alias", "fixture", "--profile", str(profile),
                "--known-hosts", str(known_hosts), "--timeout-ms", "2000", "--json",
            ])
            elapsed = time.monotonic() - started
        finally:
            stopped.set()
            server.join(timeout=6)
        assert not server.is_alive()
        assert accepted.is_set(), "The real SSH client must reach the private fixture"
        assert code == 1 and elapsed < 3
        result = json.loads(capsys.readouterr().out)
        assert result["reason"] == "timeout" and result["observed_at_ms"] is None
        assert {path: path.read_bytes() for path in original} == original
