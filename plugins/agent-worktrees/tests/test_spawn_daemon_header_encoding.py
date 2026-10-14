"""The vendored agent_procutil writes a one-line, always-encodable daemon header.

A valid POSIX path may hold undecodable bytes (lone surrogates) or line
breaks; spawn_daemon must still launch the daemon and keep its durable
launch header on a single line.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

import agent_procutil

pytestmark = pytest.mark.guard


@pytest.mark.parametrize(
    ("cwd", "escaped"),
    [("/srv/dir\udcff", b"cwd=/srv/dir\\udcff "), ("/srv/dir\nx", b"cwd=/srv/dir\\nx ")],
)
def test_daemon_log_header_escapes_cwd(cwd, escaped):
    header = agent_procutil._daemon_log_header("T", cwd, "py", None)
    assert header.count(b"\n") == 1 and header.endswith(b"\n")
    assert escaped in header


def test_spawn_daemon_survives_undecodable_multiline_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_procutil.subprocess, "Popen", lambda argv, **kw: Mock(pid=1))
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    log_path = tmp_path / "d.log"

    agent_procutil.spawn_daemon(["/opt/svc\udcff\r\nbin"], cwd=tmp_path, log_path=log_path)

    data = log_path.read_bytes()
    assert data.count(b"\n") == 1
    assert b"executable=/opt/svc\\udcff\\r\\nbin" in data
