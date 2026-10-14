"""spawn_daemon's launch header is one line and never fails to encode.

POSIX paths may legally carry undecodable bytes (lone surrogates under
``surrogateescape``) or line breaks; neither may stop the daemon launching or
split the durable one-line header.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

import agent_procutil as pu

STAMP = "2026-01-01T00:00:00+0000"


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("dir\udcff", b"dir\\udcff"),
        ("dir\nnext", b"dir\\nnext"),
        ("dir\r\nnext", b"dir\\r\\nnext"),
        ("dir\u2028next", b"dir\\u2028next"),
        ("dir\x1b[0m", b"dir\\x1b[0m"),
    ],
)
@pytest.mark.parametrize("field", ["cwd", "executable"])
def test_daemon_log_header_escapes_awkward_fields(field, raw, escaped):
    fields = {"cwd": "/srv/daemon", "executable": "py"}
    fields[field] = raw

    header = pu._daemon_log_header(STAMP, fields["cwd"], fields["executable"], None)

    _assert_one_line(header)
    assert f"{field}=".encode() + escaped in header


@pytest.mark.parametrize(
    ("raw", "rendered"),
    [
        ("svc\udcff", b"display=svc\\udcff\n"),
        ("svc\x1b[0m", b"display=svc\\x1b[0m\n"),
        ("svc\r\nserve\u2028x", b"display=svc serve x\n"),
    ],
)
def test_daemon_log_header_display_label_is_one_encodable_line(raw, rendered):
    header = pu._daemon_log_header(STAMP, "/srv/daemon", "py", raw)

    _assert_one_line(header)
    assert header.endswith(rendered)


def _assert_one_line(header: bytes) -> None:
    assert header.endswith(b"\n")
    assert header.count(b"\n") == 1 and b"\r" not in header
    assert len(header.decode("utf-8").splitlines()) == 1


def test_daemon_log_header_keeps_ordinary_fields_verbatim():
    header = pu._daemon_log_header(STAMP, r"C:\srv\däemon", "/usr/bin/py", "svc  serve")
    assert header == (
        f"{STAMP} spawn_daemon cwd=C:\\srv\\däemon executable=/usr/bin/py display=svc serve\n"
    ).encode()


def test_spawn_daemon_launches_with_undecodable_or_multiline_executable(
    tmp_path, monkeypatch
):
    calls = []

    def fake_popen(argv, **kwargs):
        calls.append(list(argv))
        return Mock(pid=4242)

    monkeypatch.setattr(pu.subprocess, "Popen", fake_popen)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    executable = "/opt/svc\udcff\nbin"
    log_path = tmp_path / "d.log"

    pu.spawn_daemon([executable, "serve"], cwd=tmp_path, log_path=log_path)

    assert calls == [[executable, "serve"]], "the child still gets the real executable"
    data = log_path.read_bytes()
    assert data.count(b"\n") == 1
    assert b"executable=/opt/svc\\udcff\\nbin" in data
