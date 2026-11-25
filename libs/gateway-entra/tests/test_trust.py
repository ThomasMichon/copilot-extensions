import ctypes
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import URLError

import pytest

from gateway_entra import EntraApplication, EntraTrustError, fetch_trust, trust

TENANT = "11111111-1111-4111-8111-111111111111"
AUDIENCE = "22222222-2222-4222-8222-222222222222"


def application():
    return EntraApplication(TENANT, AUDIENCE, "GatewayClient", "example.read")


def keys():
    return {"keys": [
        {"kty": "RSA", "kid": "selected", "n": "AQAB", "e": "AQAB",
         "x5c": ["certificate-metadata"], "issuer": "provider-metadata"},
    ]}


def response(monkeypatch, value, *, status=200, url=None, failure=None, read_failure=None):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def geturl(self):
            return url or application().jwks_url

        def read(self, limit):
            calls.append(("read", limit))
            if read_failure:
                raise read_failure
            return value if type(value) is bytes else json.dumps(value).encode("ascii")

    class Opener:
        def open(self, target, *, timeout):
            calls.append(("open", target, timeout))
            if failure:
                raise failure
            result = Response()
            result.status = status
            return result

    monkeypatch.setattr(trust, "build_opener", lambda handler: Opener())
    monkeypatch.setattr(trust, "_acquire_keys", lambda tenant, timeout: trust._read_keys(tenant, timeout))
    return calls


def test_bound_acquisition_keeps_application_authority_and_only_public_rsa(monkeypatch):
    calls = response(monkeypatch, keys())
    times = iter((1000.5, 1001.5))
    snapshot = fetch_trust(application(), clock=lambda: next(times))
    assert snapshot.issuer == application().issuer
    assert snapshot.audience == AUDIENCE
    assert snapshot.fetched_at == 1000
    assert snapshot.trust_valid_until == 1300
    assert calls == [
        ("open", application().jwks_url, 10),
        ("read", trust.MAX_RESPONSE_BYTES + 1),
    ]
    assert json.loads(snapshot.jwks)["keys"] == [
        {"kty": "RSA", "kid": "selected", "n": "AQAB", "e": "AQAB",
         "alg": "RS256", "use": "sig", "key_ops": ["verify"]},
    ]


@pytest.mark.parametrize("change", [
    {"d": "private-key"}, {"kty": "EC"}, {"alg": "HS256"},
    {"key_ops": ["sign"]}, {"jku": "https://example.com/keys"},
])
def test_refuses_nonpublic_or_unselected_key_semantics(monkeypatch, change):
    value = keys()
    value["keys"][0].update(change)
    response(monkeypatch, value)
    with pytest.raises(EntraTrustError, match="^invalid-public-trust$"):
        fetch_trust(application(), clock=lambda: 1000)


@pytest.mark.parametrize("raw", [
    b'{"keys":[],"keys":[]}',
    b'{"keys":[{"kty":"RSA","kid":"selected","kid":"other","n":"AQAB","e":"AQAB"}]}',
    b" " * (trust.MAX_RESPONSE_BYTES + 1),
], ids=["duplicate-root", "duplicate-key", "oversized"])
def test_refuses_ambiguous_or_unbounded_response(monkeypatch, raw):
    response(monkeypatch, raw)
    with pytest.raises(EntraTrustError, match="^invalid-public-trust$"):
        fetch_trust(application(), clock=lambda: 1000)


@pytest.mark.parametrize("value", [
    {"keys": []},
    {"keys": keys()["keys"] * 2},
    {"keys": keys()["keys"] * (trust.MAX_KEYS + 1)},
], ids=["empty", "duplicate-kid", "too-many"])
def test_refuses_unusable_key_set(monkeypatch, value):
    response(monkeypatch, value)
    with pytest.raises(EntraTrustError, match="^invalid-public-trust$"):
        fetch_trust(application(), clock=lambda: 1000)


@pytest.mark.parametrize("settings", [
    {"status": 302}, {"url": "https://example.com/keys"},
    {"failure": URLError("private diagnostic")},
    {"read_failure": IncompleteRead(b'{"keys":[', 10)},
])
def test_transport_failure_does_not_become_trust(monkeypatch, settings):
    response(monkeypatch, keys(), **settings)
    with pytest.raises(EntraTrustError, match="^public-trust-unavailable$"):
        fetch_trust(application(), clock=lambda: 1000)


@pytest.mark.parametrize("completed", [999, 1300, float("nan")])
def test_clock_change_does_not_extend_trust(monkeypatch, completed):
    response(monkeypatch, keys())
    times = iter((1000, completed))
    with pytest.raises(EntraTrustError, match="^invalid-clock$"):
        fetch_trust(application(), clock=lambda: next(times))


def test_generic_tenant_and_nonapplication_audience_cannot_select_discovery():
    with pytest.raises(EntraTrustError, match="^invalid-application$"):
        EntraApplication("common", AUDIENCE, "GatewayClient", "example.read")
    with pytest.raises(EntraTrustError, match="^invalid-application$"):
        EntraApplication(TENANT, "https://example.com/unrelated", "GatewayClient", "example.read")


def test_v1_app_id_uri_audience_cannot_select_v2_trust():
    with pytest.raises(EntraTrustError, match="^invalid-application$"):
        EntraApplication(TENANT, f"api://{AUDIENCE}", "GatewayClient", "example.read")


def test_total_deadline_interrupts_slow_progress_and_settles_owned_child(monkeypatch, tmp_path):
    started_body = threading.Event()

    class SlowResponse(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            started_body.set()
            try:
                for _ in range(500):
                    self.wfile.write(b"1\r\n \r\n")
                    self.wfile.flush()
                    time.sleep(0.02)
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = HTTPServer(("127.0.0.1", 0), SlowResponse)
    thread = threading.Thread(target=server.serve_forever)
    worker = tmp_path / "slow_trust_worker.py"
    worker.write_text(
        "import http.client,importlib.util,sys\n"
        f"spec=importlib.util.spec_from_file_location('owned_trust',{str(Path(trust.__file__).resolve())!r})\n"
        "module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)\n"
        "class Response:\n"
        " def __init__(self,url,timeout):\n"
        f"  self.connection=http.client.HTTPConnection('127.0.0.1',{server.server_port},timeout=timeout)\n"
        "  self.connection.request('GET','/keys'); self.response=self.connection.getresponse(); self.url=url; self.status=self.response.status\n"
        " def __enter__(self): return self\n"
        " def __exit__(self,*args): self.connection.close()\n"
        " def geturl(self): return self.url\n"
        " def read(self,limit): return self.response.read(limit)\n"
        "class Opener:\n"
        " def open(self,url,timeout): return Response(url,timeout)\n"
        "module.build_opener=lambda handler:Opener()\n"
        "raise SystemExit(module._fetch_worker(float(sys.argv[1])))\n",
        encoding="utf-8",
    )
    original = trust.subprocess.Popen
    children = []

    def launch(command, **options):
        child = original([command[0], "-I", str(worker), command[-1]], **options)
        children.append(child)
        return child

    monkeypatch.setattr(trust.subprocess, "Popen", launch)
    thread.start()
    try:
        started = time.monotonic()
        with pytest.raises(EntraTrustError, match="^public-trust-unavailable$"):
            fetch_trust(application(), timeout=2)
        assert time.monotonic() - started < 6
        assert started_body.is_set()
        assert children and all(child.poll() is not None for child in children)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.skipif(
    os.name != "nt" and not hasattr(os, "pidfd_open"),
    reason="requires a native handle to the exact owned worker",
)
def test_worker_deadline_survives_parent_termination(tmp_path):
    source = str(Path(trust.__file__).resolve())
    worker = tmp_path / "blocked_trust_worker.py"
    entered = tmp_path / "entered"
    process_record = tmp_path / "worker.json"
    worker.write_text(
        "import importlib.util,pathlib,sys,time\n"
        f"spec=importlib.util.spec_from_file_location('owned_trust',{source!r})\n"
        "module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)\n"
        "def blocked(tenant,timeout):\n"
        f" pathlib.Path({str(entered)!r}).write_text('entered')\n"
        " time.sleep(15)\n"
        " return b'{}'\n"
        "module._read_keys=blocked\n"
        "raise SystemExit(module._fetch_worker(float(sys.argv[1])))\n",
        encoding="utf-8",
    )
    parent = tmp_path / "acquisition_parent.py"
    parent.write_text(
        "import importlib.util,json,pathlib,sys\n"
        f"spec=importlib.util.spec_from_file_location('owned_trust',{source!r})\n"
        "module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)\n"
        "original=module.subprocess.Popen\n"
        "def launch(command,**options):\n"
        f" child=original([command[0],'-I',{str(worker)!r},command[-1]],**options)\n"
        f" pathlib.Path({str(process_record)!r}).write_text(json.dumps({{'pid':child.pid}}))\n"
        " return child\n"
        "module.subprocess.Popen=launch\n"
        f"module._acquire_keys({TENANT!r},2)\n",
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), "-I", str(parent)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    handle = None
    try:
        startup = time.monotonic() + 5
        while not entered.exists() and process.poll() is None and time.monotonic() < startup:
            time.sleep(0.01)
        assert entered.exists() and process.poll() is None
        pid = json.loads(process_record.read_text())["pid"]
        if os.name == "nt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x100001, False, pid)
            assert handle

            def exited(seconds):
                return kernel.WaitForSingleObject(handle, int(seconds * 1000)) == 0

            def terminate():
                assert kernel.TerminateProcess(handle, 99)

            def close():
                kernel.CloseHandle(handle)
        else:
            handle = os.pidfd_open(pid)

            def exited(seconds):
                return bool(select.select([handle], [], [], seconds)[0])

            def terminate():
                signal.pidfd_send_signal(handle, signal.SIGKILL)

            def close():
                os.close(handle)
        process.kill()
        process.communicate(timeout=5)
        assert exited(4), "orphaned worker exceeded its independent acquisition deadline"
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
        if handle is not None:
            try:
                if not exited(0):
                    terminate()
                    assert exited(5)
            finally:
                close()
