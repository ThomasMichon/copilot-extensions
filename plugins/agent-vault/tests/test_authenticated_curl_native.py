"""Real native curl, isolated loopback HTTP and synthetic credentials only."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from typing import Iterator

import pytest

from agent_vault import authenticated_curl as http
from agent_vault import cli, config
from agent_vault.extensions import ExtensionRegistry

pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
    pytest.mark.effect("network"),
]
SECRET = "synthetic-native-http-credential"


@contextmanager
def server(
    requests: list[str],
    *,
    body: bytes = b'{"ok":true}',
    location: str = "",
    echo: bool = False,
    status: int = 200,
    bodies: list[bytes] | None = None,
) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            size = int(self.headers.get("Content-Length", "0"))
            received = self.rfile.read(size)
            if bodies is not None:
                bodies.append(received)
            self.do_GET()

        def do_GET(self) -> None:
            header = self.headers.get("Authorization", "")
            requests.append(header)
            result = header.encode() if echo else body
            self.send_response(302 if location else status)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            try:
                self.wfile.write(result)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, message_format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as instance:
        thread = Thread(target=lambda: instance.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{instance.server_port}/v1/"
        finally:
            instance.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


def configure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    url: str,
    scheme: str = "bearer",
) -> None:
    path = tmp_path / "bindings.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "bindings": [
                    {
                        "url_prefix": url,
                        "entry": "API/service",
                        "scheme": scheme,
                        "allow_injection": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(http, "binding_file", lambda: path)
    registry = ExtensionRegistry()
    registry._loaded = True
    monkeypatch.setattr("agent_vault.extensions._REGISTRY", registry)
    monkeypatch.setattr(config, "resolve_context", lambda **kwargs: None)
    monkeypatch.setattr(
        http,
        "read_credential",
        lambda args: SimpleNamespace(
            code=0,
            value="sample-user" if args.field == "username" else SECRET,
        ),
    )
    try:
        http.curl_binary()
    except http.BindingError:
        pytest.skip("native curl is not installed; this is not platform qualification")


@pytest.mark.parametrize("scheme", ["token", "bearer", "basic"])
def test_system_curl_auth_is_private_and_reaches_only_the_bound_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsysbinary: pytest.CaptureFixture[bytes],
    scheme: str,
) -> None:
    observed: list[str] = []
    launched: list[tuple] = []
    original = asyncio.create_subprocess_exec

    async def launch(*argv: str, **kwargs: object) -> asyncio.subprocess.Process:
        launched.append(argv)
        assert SECRET not in json.dumps(argv)
        environment = kwargs["env"]
        credential_in_environment = any(SECRET in value for value in environment.values())
        assert not credential_in_environment
        assert "SSLKEYLOGFILE" not in environment
        assert "SYNTHETIC_AUTH_JSON" not in environment
        assert "SYNTHETIC_AUTH_BASE64" not in environment
        return await original(*argv, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    monkeypatch.setenv("SYNTHETIC_API_CREDENTIAL", SECRET)
    monkeypatch.setenv("SYNTHETIC_AUTH_JSON", json.dumps({"token": SECRET}))
    monkeypatch.setenv(
        "SYNTHETIC_AUTH_BASE64", "value=" + base64.b64encode(SECRET.encode()).decode()
    )
    environment = dict(os.environ)
    with server(observed) as url:
        configure(monkeypatch, tmp_path, url, scheme)
        files = set(tmp_path.iterdir())
        code = cli.main(["curl", "--", "-sS", "-w", r"\n%{http_code}", url])
        assert set(tmp_path.iterdir()) == files
    assert code == 0
    assert capsysbinary.readouterr().out == b'{"ok":true}\n200'
    assert len(observed) == 1 and len(launched) == 1
    assert dict(os.environ) == environment
    assert launched[0][1] == "--disable"
    assert "--config" in launched[0] and "-" in launched[0]
    if scheme == "basic":
        assert base64.b64decode(observed[0].split(" ", 1)[1]).decode() == "sample-user:" + SECRET
    else:
        assert observed[0].split(" ", 1)[1] == SECRET


def test_basic_mixed_unicode_json_echo_never_reaches_stdout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    value = "synthetic-\u00e9\U0001f600-password"
    observed: list[str] = []
    with server(observed, body=json.dumps({"echo": value}).encode()) as url:
        configure(monkeypatch, tmp_path, url, "basic")
        monkeypatch.setattr(
            http,
            "read_credential",
            lambda args: SimpleNamespace(
                code=0, value="sample-user" if args.field == "username" else value
            ),
        )
        assert cli.main(["curl", "--", url]) == 2
    captured = capsysbinary.readouterr()
    assert len(observed) == 1 and captured.out == b""
    assert b"response withheld" in captured.err and value.encode() not in captured.err


def test_cross_host_redirect_sends_no_second_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    first: list[str] = []
    second: list[str] = []
    with server(second) as target:
        with server(first, location=target.replace("127.0.0.1", "localhost")) as url:
            configure(monkeypatch, tmp_path, url)
            code = cli.main(["curl", "--", url])
    output = capsysbinary.readouterr()
    assert code == 2 and second == [] and len(first) == 1
    assert output.out == b"" and SECRET.encode() not in output.err
    assert b"redirect refused" in output.err


@pytest.mark.parametrize("empty_field", ["username", "password"])
def test_basic_auth_supports_an_empty_username_or_password(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsysbinary: pytest.CaptureFixture[bytes],
    empty_field: str,
) -> None:
    values = {"username": SECRET, "password": SECRET}
    values[empty_field] = ""
    requests: list[str] = []
    with server(requests) as url:
        configure(monkeypatch, tmp_path, url, "basic")
        monkeypatch.setattr(
            http,
            "read_credential",
            lambda args: SimpleNamespace(code=0, value=values[args.field]),
        )
        assert cli.main(["curl", "--", url]) == 0
    assert base64.b64decode(requests[0].split(" ", 1)[1]).decode() == (
        values["username"] + ":" + values["password"]
    )
    assert capsysbinary.readouterr().out == b'{"ok":true}'


def test_post_payload_remains_literal_data_not_curl_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    bodies: list[bytes] = []
    payload = '{"argument":"@- --location"}'
    with server([], bodies=bodies) as url:
        configure(monkeypatch, tmp_path, url)
        assert (
            cli.main(
                [
                    "curl",
                    "--",
                    "-X",
                    "POST",
                    "-H",
                    "Content-Type: application/json",
                    "--data-raw",
                    payload,
                    url,
                ]
            )
            == 0
        )
    assert bodies == [payload.encode()]
    assert capsysbinary.readouterr().out == b'{"ok":true}'


def test_native_echo_is_withheld_before_stdout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    with server([], echo=True) as url:
        configure(monkeypatch, tmp_path, url)
        assert cli.main(["curl", "--", url]) == 2
    output = capsysbinary.readouterr()
    assert output.out == b""
    assert SECRET.encode() not in output.err
    assert b"response withheld" in output.err


def test_native_curl_ignores_curlrc_path_shadow_and_tls_key_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    leaked = tmp_path / "unexpected-output"
    (tmp_path / ".curlrc").write_text(f'output = "{leaked}"\nheader = "Authorization: wrong"\n')
    shadow = tmp_path / ("curl.exe" if os.name == "nt" else "curl")
    shadow.write_text("this is not a native curl executable")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("CURL_HOME", str(tmp_path))
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "tls-secrets"))
    original = asyncio.create_subprocess_exec

    async def launch(*argv: str, **kwargs: object) -> asyncio.subprocess.Process:
        assert Path(argv[0]) != shadow
        assert "SSLKEYLOGFILE" not in kwargs["env"]
        return await original(*argv, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    requests: list[str] = []
    with server(requests) as url:
        configure(monkeypatch, tmp_path, url)
        assert cli.main(["curl", "--", url]) == 0
    assert capsysbinary.readouterr().out == b'{"ok":true}'
    assert requests[0].split(" ", 1)[1] == SECRET
    assert not leaked.exists() and not (tmp_path / "tls-secrets").exists()


def test_oversized_native_response_is_bounded_and_child_reaped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    processes: list[asyncio.subprocess.Process] = []
    original = asyncio.create_subprocess_exec

    async def launch(*argv: str, **kwargs: object) -> asyncio.subprocess.Process:
        process = await original(*argv, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    with server([], body=b"x" * (http._BODY_LIMIT + 65536)) as url:
        configure(monkeypatch, tmp_path, url)
        assert cli.main(["curl", "--", url]) == 2
    output = capsysbinary.readouterr()
    assert output.out == b"" and b"capture limit" in output.err
    assert processes and all(process.returncode is not None for process in processes)


def test_http_failure_and_write_out_preserve_safe_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    with server([], status=401) as url:
        configure(monkeypatch, tmp_path, url)
        assert cli.main(["curl", "--", "-f", "-w", "%{http_code}", url]) == 22
    assert capsysbinary.readouterr().out == b"401"
