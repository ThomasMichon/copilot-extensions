"""Opt-in first-touch proof with a test-owned host controller and real Docker."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import yaml

from agent_dispatch import restricted_spawn
from agent_dispatch.client import DispatchClient, ResolvingDispatchClient
from agent_dispatch.supervisor import Supervisor
from agent_dispatch.tracking import session_activity
from agent_procutil import no_window_flags

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("AGENT_DISPATCH_RESTRICTED_E2E") != "1",
        reason="set AGENT_DISPATCH_RESTRICTED_E2E=1 for real restricted-supervisor acceptance",
    ),
    pytest.mark.timeout(300),
]

_FIXTURES = Path(__file__).with_name("fixtures")
_REPO = "example.com/acme/restricted-acceptance"


def _run(*argv, **kwargs):
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=45,
        creationflags=no_window_flags(), **kwargs,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _wait(probe, *, timeout=30, diagnostic=lambda: ""):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(0.1)
    pytest.fail("real integration boundary did not become ready before its deadline\n" + diagnostic())


def _provider_runtime(python: str, destination: Path):
    identity = json.loads(_run(python, "-c", (
        "import json,sys,agent_containers;from importlib.metadata import version;"
        "print(json.dumps({'prefix':sys.prefix,'version':version('agent-containers'),"
        "'source':agent_containers.__file__}))"
    )))
    slot = destination / "versions" / identity["version"]
    shutil.copytree(Path(identity["prefix"]), slot, symlinks=True)
    runtime = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    source = _run(str(runtime), "-c", "import agent_containers;print(agent_containers.__file__)").strip()
    assert Path(source).resolve() == Path(identity["source"]).resolve()
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": identity["version"],
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
    }), encoding="utf-8")
    (destination / "current-version").write_text(identity["version"], encoding="utf-8")


def test_real_supervisor_delivers_rotates_and_completes_own_task(tmp_path, monkeypatch):
    evidence = tmp_path
    if override := os.environ.get("AGENT_DISPATCH_E2E_EVIDENCE_DIR"):
        evidence = Path(override)
        assert evidence.is_absolute()
        evidence.mkdir(exist_ok=False)
    trace_path = evidence / "stages.jsonl"

    def trace(stage, event):
        with trace_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps({
                "stage": stage, "event": event, "monotonic": time.monotonic(),
                "utc": datetime.now(timezone.utc).isoformat(),
            }) + "\n")

    run_delivery = restricted_spawn._run

    def traced_delivery(argv, payload=None):
        stage = next((part for part in argv if part in {
            "dispatch-prepare", "dispatch-project", "create", "send", "status", "resume",
        }), "unknown")
        trace(stage, "start")
        try:
            result = run_delivery(argv, payload)
        except BaseException:
            trace(stage, "failed")
            raise
        trace(stage, "complete")
        return result

    monkeypatch.setattr(restricted_spawn, "_run", traced_delivery)
    trace("acceptance", "start")
    bridge_python = os.environ["AGENT_DISPATCH_E2E_BRIDGE_PYTHON"]
    provider_python = os.environ["AGENT_DISPATCH_E2E_PROVIDER_PYTHON"]
    central_image = os.environ["AGENT_DISPATCH_E2E_CENTRAL_IMAGE"]
    suffix = uuid.uuid4().hex[:12]
    network, central, worker = (f"restricted-proof-{part}-{suffix}" for part in ("net", "central", "worker"))
    management = f"restricted-proof-management-{suffix}"
    bridge_root, provider_root = tmp_path / "bridge", tmp_path / "provider"
    bridge_root.mkdir()
    provider_root.mkdir()
    providers = bridge_root / "providers.d"
    providers.mkdir()
    fleet_token, control_token, bridge_token = (uuid.uuid4().hex for _ in range(3))
    (bridge_root / "auth.yaml").write_text(yaml.safe_dump({"token": bridge_token}), encoding="utf-8")
    scheme = "Bear" + "er "
    bridge_headers = {"Authorization": scheme + bridge_token}
    assert bridge_headers["Authorization"].endswith(bridge_token)
    config_path = provider_root / "containers.yaml"
    program = base64.b64encode((_FIXTURES / "restricted_task_agent.cjs").read_bytes()).decode("ascii")
    fleet = {
        "image": "node:22-bookworm-slim", "security_profile": "restricted", "dispatch_only": True,
        "exec_user": "node", "workspace_folder": "/workspace", "network": network,
        "memory": "1g", "cpus": 1.0, "pids_limit": 64,
        "workspace_size": "128m", "home_size": "64m",
        "acp_command": f"""/usr/local/bin/node -e 'eval(Buffer.from("{program}","base64").toString())' --""",
    }
    config_path.write_text(yaml.safe_dump({"fleets": {"acceptance": fleet}}), encoding="utf-8")
    trace("provider-runtime-copy", "start")
    _provider_runtime(provider_python, provider_root)
    trace("provider-runtime-copy", "complete")
    for name, value in {
        "AGENT_CONTAINERS_CONFIG": str(config_path), "AGENT_CONTAINERS_HOME": str(provider_root),
        "AGENT_CONTAINERS_STATE_DIR": str(provider_root), "AGENT_CONTAINERS_NO_SELFPROVISION": "1",
        "AGENT_BRIDGE_CONFIG_DIR": str(bridge_root), "AGENT_BRIDGE_PROVIDERS_DIR": str(providers),
        "AGENT_BRIDGE_TOKEN": bridge_token, "AGENT_BRIDGE_SELF_RETIRE": "0",
        "AGENT_BRIDGE_NO_ROUTING_TABLE": "1",
    }.items():
        monkeypatch.setenv(name, value)
    # A private legacy manifest exercises the supported compatibility boundary;
    # no live plugin activation/settings or installed registry is changed.
    (providers / "container.json").write_text(json.dumps({
        "namespace": "container", "command": [provider_python, "-m", "agent_containers"],
        "restricted": True,
    }), encoding="utf-8")
    monkeypatch.setattr(
        restricted_spawn, "agent_bridge_launch_prefix",
        lambda: [bridge_python, "-m", "agent_bridge"],
    )
    provider_launcher = (
        "import json;from agent_containers.config import load_config;"
        "from agent_containers.fleet import _image_run;"
        f"f=load_config().fleets['acceptance'];_image_run('acceptance',f,{worker!r},"
        "workspace_folder='/workspace',exec_user='node')"
    )
    process = None
    session_id = None
    bridge_url = None
    try:
        _run("docker", "network", "create", "--internal", network)
        _run("docker", "network", "create", management)
        uid, gid = _run(
            "docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--entrypoint", "python", central_image, "-c",
            "import os;print(os.getuid(),os.getgid())",
        ).split()
        _run(
            "docker", "run", "-d", "--name", central, "--network", management,
            "--network-alias", "coordinator", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--memory", "1g", "--cpus", "1",
            "--pids-limit", "64", "--tmpfs",
            f"/home/dispatch/.agent-dispatch:rw,nosuid,size=128m,uid={uid},gid={gid},mode=0700",
            "--tmpfs", "/tmp:rw,nosuid,size=64m", "-p", "127.0.0.1::8787",
            "-e", "AGENT_DISPATCH_HOST=0.0.0.0", "-e", "AGENT_DISPATCH_PORT=8787",
            "-e", "AGENT_DISPATCH_DB=/home/dispatch/.agent-dispatch/queue.db",
            "-e", f"AGENT_DISPATCH_TOKEN={fleet_token}",
            "-e", f"AGENT_DISPATCH_CONTROL_TOKEN={control_token}", central_image,
        )
        state = json.loads(_run("docker", "inspect", central))[0]["State"]
        assert state["Running"], _run("docker", "logs", central)
        _run("docker", "network", "connect", "--alias", "coordinator", network, central)
        port = _run("docker", "port", central, "8787/tcp").strip().rsplit(":", 1)[1]
        central_url = f"http://127.0.0.1:{port}"

        def central_ready():
            state = json.loads(_run("docker", "inspect", central))[0]["State"]
            assert state["Running"], _run("docker", "logs", central)
            try:
                response = httpx.get(
                    central_url + "/health", headers={"Authorization": scheme + fleet_token},
                    timeout=1, trust_env=False,
                )
                return response.status_code == 200
            except httpx.TransportError:
                return False

        _wait(central_ready)
        _run(provider_python, "-c", provider_launcher)
        physical = json.loads(_run("docker", "inspect", worker))[0]["Id"]
        bridge_log = evidence / "server.log"
        with bridge_log.open("wb") as log:
            process = subprocess.Popen(
                [bridge_python, str(_FIXTURES / "restricted_bridge_server.py")],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                cwd=tmp_path, creationflags=no_window_flags(),
            )

            def bridge_ready():
                assert process.poll() is None, bridge_log.read_text(encoding="utf-8")
                listener = bridge_root / "listener.json"
                if not listener.exists():
                    return None
                url = f"http://127.0.0.1:{json.loads(listener.read_text())['port']}"
                try:
                    response = httpx.get(
                        url + "/health", headers=bridge_headers, timeout=1, trust_env=False,
                    )
                    return url if response.status_code == 200 and response.json()["ready"] else None
                except httpx.TransportError:
                    return None

            bridge_url = _wait(
                bridge_ready, diagnostic=lambda: bridge_log.read_text(encoding="utf-8"),
            )
            monkeypatch.setenv("AGENT_BRIDGE_BASE_URL", bridge_url)
            with DispatchClient(central_url, token=fleet_token, control_token=control_token) as client:
                other = client._unwrap(client._http.post("/tasks", json={
                    "title": "unclaimable earlier task", "repo": _REPO,
                    "labels": ["restricted"], "requires": ["unavailable-capability"],
                }))
                task = client._unwrap(client._http.post("/tasks", json={
                    "title": "restricted first touch", "repo": _REPO, "labels": ["restricted"],
                    "payload_inline": json.dumps({"other": other["id"]}),
                }))

                def bridge_state(sid):
                    response = httpx.get(
                        f"{bridge_url}/api/v1/sessions/{sid}", headers=bridge_headers,
                        timeout=10, trust_env=False,
                    )
                    response.raise_for_status()
                    return response.json()

                supervisor = Supervisor(
                    ResolvingDispatchClient(lambda: DispatchClient(
                        central_url, token=fleet_token, control_token=control_token,
                    )), spawn_fn=restricted_spawn.make_spawn({
                        "command": [provider_python, "-m", "agent_containers"],
                        "name": worker, "instance_id": physical,
                        "coordinator_url": "http://coordinator:8787",
                    }), repo=_REPO, labels=["restricted"], supervisor_id="docker-acceptance",
                    machine="fixture-host", recover=False, nudge=False, heartbeat=True,
                    consistency_sweep=False, publish_activity=True,
                    local_body_verdict_fn=lambda sid: (
                        "live" if bridge_state(sid)["status"] in {"running", "idle"} else "unknown"
                    ),
                    local_acp_session_fn=lambda sid: bridge_state(sid)["acp_session_id"],
                    local_body_activity_fn=lambda sid: session_activity(bridge_state(sid)),
                )
                monkeypatch.setattr(
                    supervisor.client, "heartbeat",
                    lambda *_args, **_kwargs: pytest.fail("restricted worker used generic heartbeat"),
                )
                polled = supervisor.poll_once()
                assert polled == [task["id"]], json.dumps({
                    "reservations": client.list_reservations(task_id=task["id"]),
                    "bridge_log": bridge_log.read_text(encoding="utf-8"),
                })
                receipt = client.list_reservations(task_id=task["id"])[0]
                info = receipt["agent_info"]
                session_id = info["session_id"]
                deferred = client.list_reservations(task_id=other["id"])[0]
                assert deferred["state"] == "deferred"
                assert deferred["agent_info"] is None and deferred["session_handle"] is None
                assert info["delivered"] is True and receipt["driver"] == "restricted-bridge"
                assert receipt["worktree"] is None and receipt["allocation_project"] is None
                assert client.get(task["id"])["owner_session_id"] == info["native_session_id"]

                def awaiting_renewal():
                    if client.get(task["id"])["status"] != "started":
                        return False
                    return any(
                        row["summary"] == "Waiting for trusted credential rotation"
                        for row in client.progress_log(task["id"])
                    )

                _wait(awaiting_renewal)
                before_activity = client.get(task["id"])
                assert supervisor.hold_live_leases() == 0
                active = client.get(task["id"])
                assert active["activity"] == "ACTIVE"
                assert active["lease_expires_at"] == before_activity["lease_expires_at"]
                assert active["generation"] == info["task_generation"]
                assert active["owner_session_id"] == info["native_session_id"]
                assert supervisor.renew_restricted_grants() == 1
                _run(
                    "docker", "exec", "-u", "node", physical, "/usr/local/bin/node", "-e",
                    "require('fs').writeFileSync('/workspace/complete','release')",
                )
                completed = _wait(
                    lambda: (current if (current := client.get(task["id"]))["status"] == "completed" else None),
                )
                def final_proof():
                    proof = json.loads(_run(
                        "docker", "exec", "-u", "node", physical, "/usr/local/bin/node", "-e",
                        "process.stdout.write(require('fs').readFileSync('/workspace/proof.json','utf8'))",
                    ))
                    return proof if proof["phase"] == "completed" else None

                proof = _wait(final_proof)
                assert proof["native"] == info["native_session_id"] and proof["rotated"] is True
                assert completed["generation"] == info["task_generation"]
                assert completed["result"]["native"] == info["native_session_id"]
                assert completed["result"]["rotated"] is True and completed["result"]["prompts"] == 1
                assert all(item["status"] == 403 for item in completed["result"]["refused"])
                assert client.get(other["id"])["status"] == "queued"
                assert supervisor.renew_restricted_grants() == 0
                inspected = _run("docker", "inspect", physical)
                assert fleet_token not in inspected and control_token not in inspected
                assert bridge_state(session_id)["acp_session_id"] == info["native_session_id"]
                trace("acceptance", "complete")
    finally:
        trace("cleanup", "start")
        try:
            if bridge_url:
                response = httpx.get(
                    f"{bridge_url}/api/v1/sessions", headers=bridge_headers,
                    timeout=10, trust_env=False,
                )
                response.raise_for_status()
                for session in response.json()["sessions"]:
                    ended = httpx.delete(
                        f"{bridge_url}/api/v1/sessions/{session['session_id']}",
                        params={"force": "true"}, headers=bridge_headers,
                        timeout=30, trust_env=False,
                    )
                    assert ended.status_code == 204, ended.text
        finally:
            try:
                if process is not None:
                    process.terminate()
                    process.wait(timeout=15)
            finally:
                for name in (worker, central):
                    subprocess.run(
                        ["docker", "rm", "-f", "-v", name], capture_output=True, text=True,
                        timeout=30, creationflags=no_window_flags(),
                    )
                for name in (network, management):
                    subprocess.run(
                        ["docker", "network", "rm", name], capture_output=True, text=True,
                        timeout=30, creationflags=no_window_flags(),
                    )
                trace("cleanup", "complete")
