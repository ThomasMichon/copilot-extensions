from concurrent.futures import ThreadPoolExecutor
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from agent_chat_service import ChatError, ChatStore, json_bytes, strict_json


def run_artifact(argv, *, cwd=None):
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        argv, cwd=cwd, env=environment, capture_output=True, text=True,
        timeout=30, check=False, **options,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="session")
def installed_chat(tmp_path_factory):
    root = tmp_path_factory.mktemp("chat-wheels")
    project = Path(__file__).resolve().parents[1]
    wheels = root / "wheels"
    wheels.mkdir()
    sources = []
    for name, original in (
        ("source", project),
        ("helper-source", project.parents[1] / "libs" / "agent-procutil"),
        ("lease-source", project.parents[1] / "libs" / "single-instance-lease"),
        ("endpoint-source", project.parents[1] / "libs" / "endpoint-rendezvous"),
    ):
        source = root / name
        shutil.copytree(
            original, source,
            ignore=shutil.ignore_patterns("build", "dist", "tests", "*.egg-info", "__pycache__",
                                         ".venv", ".pytest_cache"),
        )
        sources.append(source)

    def build(source):
        run_artifact([
            sys.executable, "-I", "-c",
            "from setuptools.build_meta import build_wheel; import sys; build_wheel(sys.argv[1])",
            str(wheels),
        ], cwd=source)

    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(build, sources))
    built = list(wheels.glob("agent_chat_service-*.whl"))
    assert len(built) == 1 and len(list(wheels.glob("*.whl"))) == 4
    assert all(len(list(wheels.glob(prefix + "-*.whl"))) == 1 for prefix in (
        "agent_procutil", "agent_single_instance_lease", "agent_endpoint_rendezvous",
    ))
    uv = shutil.which("uv")
    assert uv is not None, "Artifact qualification requires the repository's uv tool"
    def install(item):
        name, role = item
        target = root / name
        run_artifact([uv, "venv", "--python", sys.executable, "--no-python-downloads", str(target)])
        scripts = target / ("Scripts" if os.name == "nt" else "bin")
        interpreter = scripts / ("python.exe" if os.name == "nt" else "python")
        run_artifact([
            uv, "pip", "install", "--python", str(interpreter), "--offline", "--no-index",
            "--find-links", str(wheels),
            *([] if role else ["--no-deps"]), str(built[0]) + (f"[{role}]" if role else ""),
        ])
        return name, (target, scripts, interpreter)

    with ThreadPoolExecutor(max_workers=3) as workers:
        installations = dict(workers.map(install, (
            ("core-installed", None), ("client-installed", "client"), ("service-installed", "service"),
        )))
    for source in sources:
        shutil.rmtree(source)
        assert not source.exists()
    target, scripts, interpreter = installations["client-installed"]
    assert json.loads(run_artifact([
        str(interpreter), "-I", "-c",
        "import importlib.util,json,sys; from pathlib import Path; "
        "import agent_chat_service, agent_procutil.owned_process; "
        "root=Path(sys.argv[1]).resolve(); "
        "assert Path(agent_chat_service.__file__).resolve().is_relative_to(root); "
        "assert Path(agent_procutil.owned_process.__file__).resolve().is_relative_to(root); "
        "assert importlib.util.find_spec('single_instance_lease') is None; "
        "assert importlib.util.find_spec('endpoint_rendezvous') is None; "
        "print(json.dumps({'source_independent':True}))",
        str(target),
    ], cwd=root))["source_independent"]
    core_target, _, core_python = installations["core-installed"]
    service_target, service_scripts, service_python = installations["service-installed"]
    return SimpleNamespace(
        root=root, target=target, interpreter=interpreter,
        console=scripts / ("agent-chat.exe" if os.name == "nt" else "agent-chat"),
        core_target=core_target, core_python=core_python, run=run_artifact,
        service_target=service_target, service_python=service_python,
        service_console=service_scripts / (
            "agent-chat-service.exe" if os.name == "nt" else "agent-chat-service"
        ),
    )


@pytest.fixture
def fixed_rpc_provider(tmp_path, installed_chat, rpc_endpoint):
    script = tmp_path / "fixed-rpc.py"
    script.write_text("""
import sys
from agent_chat_service.store import json_bytes,strict_json
from agent_chat_service.transport import STDIO_REQUEST,STDIO_RESPONSE,_http_exchange
request=strict_json(sys.stdin.buffer.read(65537))
assert set(request)=={"schema","version","endpoint","payload"}
assert request["schema"]==STDIO_REQUEST and type(request["version"]) is int and request["version"]==1
assert request["endpoint"]==sys.argv[1]
status,data=_http_exchange({
    "endpoint":sys.argv[1],"payload":request["payload"],
    "credential":"synthetic-credential","timeout":15,
})
sys.stdout.buffer.write(json_bytes({
    "schema":STDIO_RESPONSE,"version":1,"status":status,
    "result":strict_json(data,max_bytes=1048576),
}))
sys.stdout.buffer.flush()
""", encoding="utf-8")
    return [str(installed_chat.interpreter), "-I", str(script), rpc_endpoint.endpoint]


@pytest.fixture
def rpc_endpoint(tmp_path):
    store = ChatStore(tmp_path / "backend", "example-installation")
    state = SimpleNamespace(
        mode="normal", requests=[], authenticated=0,
        read_entered=threading.Event(), release_read=threading.Event(),
        read_hold_deadline=None,
        trickle_progress=threading.Event(), stop_trickle=threading.Event(),
    )
    expected_prefix = "Bear" + "er "
    expected = expected_prefix + "synthetic-credential"

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            raw = self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 65537))
            status = 200
            if self.path != "/api/v1/chat" or self.headers.get("Authorization") != expected:
                status, value = 401, {"error": "unauthorized"}
            else:
                state.authenticated += 1
                request = strict_json(raw)
                state.requests.append(request)
                if state.mode == "hold-read" and request["op"] == "read":
                    state.mode = "normal"
                    state.read_hold_deadline = time.monotonic() + 10
                    state.read_entered.set()
                    assert state.release_read.wait(10), "fixture read was not released"
                try:
                    value = store.dispatch(request)
                except ChatError as error:
                    status, value = error.status, error.as_dict()
                if state.mode == "drop-post" and request["op"] == "post":
                    state.mode = "normal"
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
            if state.mode == "server-error":
                status, value = 500, {"error": "unavailable"}
            elif state.mode == "redirect":
                status, value = 302, {"error": "redirect"}
            elif state.mode == "oversized":
                status, value = 200, {"value": "x" * 1048576}
            elif state.mode == "large":
                status, value = 200, {"value": "x" * 65536}
            data = json_bytes(value)
            self.send_response(status)
            self.send_header(
                "Content-Type", "text/plain" if state.mode == "wrong-type" else "application/json",
            )
            self.send_header("Content-Length", str(len(data)))
            if status == 302:
                self.send_header("Location", "https://example.invalid/not-authorized")
            if state.mode == "trickle-header":
                headers = b"".join(self._headers_buffer) + b"\r\n"
                self._headers_buffer = []
                self.trickle(headers)
                return
            self.end_headers()
            if state.mode == "trickle-body":
                self.trickle(data)
                return
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

        def trickle(self, data):
            try:
                for index, value in enumerate(data):
                    self.wfile.write(bytes([value]))
                    self.wfile.flush()
                    if index >= 2:
                        state.trickle_progress.set()
                    if state.stop_trickle.wait(.1):
                        break
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    # Only idle fixture shutdown polling, not any request or transport deadline.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        yield SimpleNamespace(endpoint=endpoint, store=store, state=state)
    finally:
        state.release_read.set()
        state.stop_trickle.set()
        server.shutdown()
        server.server_close()
        thread.join(5)
        store.close()
