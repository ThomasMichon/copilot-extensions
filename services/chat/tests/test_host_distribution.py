import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from endpoint_rendezvous import read_endpoint
from agent_procutil import spawn_sync_in_kill_on_close_job

from test_distribution import run
from test_hosting import configuration


def test_fresh_installed_host_source_removed_independent_participants_and_crash_restart(tmp_path):
    project = Path(__file__).resolve().parents[1]
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    sources = []
    for name, original in (
        ("host-source", project),
        ("lease-source", project.parents[1] / "libs" / "single-instance-lease"),
        ("endpoint-source", project.parents[1] / "libs" / "endpoint-rendezvous"),
    ):
        source = tmp_path / name
        shutil.copytree(
            original, source,
            ignore=shutil.ignore_patterns("build", "dist", "*.egg-info", "__pycache__",
                                         ".venv", ".pytest_cache", ".ruff_cache"),
        )
        sources.append(source)
        run([
            sys.executable, "-I", "-c",
            "from setuptools.build_meta import build_wheel; import sys; build_wheel(sys.argv[1])",
            str(wheels),
        ], cwd=source)
    uv = shutil.which("uv")
    assert uv is not None
    target = tmp_path / "installed"
    run([uv, "venv", "--python", sys.executable, "--no-python-downloads", str(target)])
    scripts = target / ("Scripts" if os.name == "nt" else "bin")
    interpreter = scripts / ("python.exe" if os.name == "nt" else "python")
    wheel, = wheels.glob("agent_chat_service-*.whl")
    config = configuration(tmp_path)
    config_file = tmp_path / "host.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")
    run([uv, "pip", "install", "--python", str(interpreter), "--offline", "--no-index",
         "--no-deps", str(wheel)])
    core_refusal = subprocess.run([
        str(interpreter), "-I", "-m", "agent_chat_service.hosting",
        "--config", str(config_file),
    ], cwd=tmp_path, capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert core_refusal.returncode == 1 and core_refusal.stdout == ""
    assert json.loads(core_refusal.stderr) == {"error": "host_service_dependencies_missing"}
    assert not Path(config["data_dir"]).exists()
    run([uv, "pip", "install", "--python", str(interpreter), "--offline", "--no-index",
         "--find-links", str(wheels), str(wheel) + "[service]"])
    for source in sources:
        shutil.rmtree(source)
        assert not source.exists()
    console = scripts / ("agent-chat-service.exe" if os.name == "nt" else "agent-chat-service")
    assert "--config" in run([str(console), "--help"], cwd=tmp_path)
    assert json.loads(run([
        str(interpreter), "-I", "-c",
        "import importlib.util,json,sys; from pathlib import Path; "
        "import agent_chat_service.hosting as host; import single_instance_lease; "
        "import endpoint_rendezvous; "
        "assert all(Path(m.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve()) "
        "for m in (host,single_instance_lease,endpoint_rendezvous)); "
        "assert importlib.util.find_spec('agent_procutil') is None; "
        "print(json.dumps({'installed':True}))", str(target),
    ], cwd=tmp_path))["installed"]
    run_dir = Path(config["data_dir"]) / "run"
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

    def start():
        previous = read_endpoint(run_dir)
        stdout = (tmp_path / "host-stdout").open("wb")
        stderr = (tmp_path / "host-stderr").open("wb")
        try:
            process, job = spawn_sync_in_kill_on_close_job([
                str(interpreter), "-I", "-m", "agent_chat_service.hosting",
                "--config", str(config_file),
            ], cwd=tmp_path, env=environment, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, require_job=True, allow_breakaway=False, **options)
        finally:
            stdout.close()
            stderr.close()
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError((tmp_path / "host-stderr").read_text(encoding="utf-8"))
                endpoint = read_endpoint(run_dir)
                if endpoint is not None and (previous is None or endpoint.pid != previous.pid):
                    return process, job, {"transport": endpoint.transport,
                                          "endpoint": endpoint.address}
                time.sleep(.02)
            raise AssertionError("installed host did not publish its bound endpoint")
        except BaseException:
            stop(process, job)
            raise

    def stop(process, job):
        try:
            if job is not None:
                job.terminate_and_wait(5)
            elif process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        finally:
            if job is not None:
                job.close()

    probe = """
import http.client,json,socket,sys
endpoint=json.loads(sys.argv[1])
if endpoint["transport"]=="tcp":
    host,port=endpoint["endpoint"].rsplit(":",1)
    client=http.client.HTTPConnection(host,int(port),timeout=10)
else:
    class Client(http.client.HTTPConnection):
        def connect(self):
            self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            self.sock.settimeout(10)
            self.sock.connect(endpoint["endpoint"])
    client=Client("localhost",timeout=10)
try:
    scheme="Bear"+"er "
    with open(sys.argv[2],encoding="ascii") as stream:
        credential=stream.read().strip()
    client.request("POST","/api/v1/chat",sys.stdin.buffer.read(65537),
                   {"Content-Type":"application/json","Authorization":scheme+credential})
    response=client.getresponse()
    assert response.status==200,response.status
    print(response.read(1048577).decode("utf-8"),flush=True)
finally:
    client.close()
"""

    def request(endpoint, value):
        result = subprocess.run([
            str(interpreter), "-I", "-c", probe,
            json.dumps(endpoint), config["token_file"],
        ], input=json.dumps(value), cwd=tmp_path, env=environment, capture_output=True,
            text=True, encoding="utf-8", timeout=15, **options)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    process, job, endpoint = start()
    try:
        for actor in ("agent-a", "agent-b"):
            assert request(endpoint, {
                "op": "join", "channel": "room", "actor": {"codename": actor},
            })["membership"]["active"]
        post = {"op": "post", "channel": "room", "codename": "agent-a",
                "text": "independent installed participant", "request_id": "installed-host-request"}
        event = request(endpoint, post)["event"]
        read = {"op": "read", "channel": "room", "codename": "agent-b"}
        page = request(endpoint, read)
        assert page["messages"] == [event] and page["cursor"] == 0
        assert request(endpoint, read) == page
        request(endpoint, {
            "op": "cursor", "channel": "room", "codename": "agent-b",
            "expected_cursor": 0, "next_cursor": page["next_cursor"],
        })
        assert request(endpoint, read)["messages"] == []
        stop(process, job)
        assert process.poll() is not None
    finally:
        if job is not None and job.handle or process.poll() is None:
            stop(process, job)
    process, job, endpoint = start()
    try:
        assert request(endpoint, post) == {"event": event, "duplicate": True}
        assert request(endpoint, read)["messages"] == []
        for actor in ("agent-a", "agent-b"):
            assert not request(endpoint, {
                "op": "leave", "channel": "room", "codename": actor,
            })["active"]
    finally:
        stop(process, job)
