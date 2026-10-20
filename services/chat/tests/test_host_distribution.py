import json
import os
from pathlib import Path
import subprocess
import time

from endpoint_rendezvous import read_endpoint
from agent_procutil import spawn_sync_in_kill_on_close_job
from agent_procutil.owned_process import run_owned

from test_hosting import configuration


def test_fresh_installed_host_source_removed_independent_participants_and_crash_restart(
    tmp_path, installed_chat,
):
    run = installed_chat.run
    target = installed_chat.service_target
    interpreter = installed_chat.service_python
    config = configuration(tmp_path)
    config_file = tmp_path / "host.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")
    core_refusal = subprocess.run([
        str(installed_chat.core_python), "-I", "-m", "agent_chat_service.hosting",
        "--config", str(config_file),
    ], cwd=tmp_path, capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert core_refusal.returncode == 1 and core_refusal.stdout == ""
    assert json.loads(core_refusal.stderr) == {"error": "host_service_dependencies_missing"}
    assert not Path(config["data_dir"]).exists()
    assert all(not (installed_chat.root / name).exists() for name in (
        "source", "helper-source", "lease-source", "endpoint-source",
    ))
    console = installed_chat.service_console
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
class UnixClient(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(endpoint["endpoint"])
scheme="Bear"+"er "
with open(sys.argv[2],encoding="ascii") as stream:
    credential=stream.read().strip()
results=[]
for payload in json.loads(sys.stdin.buffer.read(65537)):
    if endpoint["transport"]=="tcp":
        host,port=endpoint["endpoint"].rsplit(":",1)
        client=http.client.HTTPConnection(host,int(port),timeout=10)
    else:
        client=UnixClient("localhost",timeout=10)
    try:
        client.request("POST","/api/v1/chat",json.dumps(payload).encode("utf-8"),
                       {"Content-Type":"application/json","Authorization":scheme+credential})
        response=client.getresponse()
        assert response.status==200,response.status
        raw=response.read(1048577)
        assert len(raw)<=1048576
        results.append(json.loads(raw))
    finally:
        client.close()
print(json.dumps(results),flush=True)
"""

    def requests(endpoint, values):
        result = run_owned([
            str(interpreter), "-I", "-c", probe,
            json.dumps(endpoint), config["token_file"],
        ], input_text=json.dumps(values), timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    post = {"op": "post", "channel": "room", "codename": "agent-a",
            "text": "independent installed participant", "request_id": "installed-host-request"}
    read = {"op": "read", "channel": "room", "codename": "agent-b"}
    process, job, endpoint = start()
    try:
        joined, posted = requests(endpoint, [
            {"op": "join", "channel": "room", "actor": {"codename": "agent-a"}}, post,
        ])
        assert joined["membership"]["active"]
        event = posted["event"]
        joined, page, repeated, committed, empty = requests(endpoint, [
            {"op": "join", "channel": "room", "actor": {"codename": "agent-b"}},
            read, read,
            {"op": "cursor", "channel": "room", "codename": "agent-b",
             "expected_cursor": 0, "next_cursor": event["id"]},
            read,
        ])
        assert joined["membership"]["active"]
        assert page["messages"] == [event] and page["cursor"] == 0
        assert repeated == page and committed["cursor"] == page["next_cursor"]
        assert empty["messages"] == []
        stop(process, job)
        assert process.poll() is not None
    finally:
        if job is not None and job.handle or process.poll() is None:
            stop(process, job)
    process, job, endpoint = start()
    try:
        duplicate, left = requests(endpoint, [
            post, {"op": "leave", "channel": "room", "codename": "agent-a"},
        ])
        assert duplicate == {"event": event, "duplicate": True} and not left["active"]
        empty, left = requests(endpoint, [
            read, {"op": "leave", "channel": "room", "codename": "agent-b"},
        ])
        assert empty["messages"] == [] and not left["active"]
    finally:
        stop(process, job)
