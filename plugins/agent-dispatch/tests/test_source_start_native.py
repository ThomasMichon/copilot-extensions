"""Explicit prepared-input qualification; no source/profile construction in pytest."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

import pytest


def _qualify_generated_source(tmp_path, *, repair_matrix=False):
    selected = os.environ.get("DISPATCH_QUALIFIED_SOURCE_SNAPSHOT")
    evidence = os.environ.get("DISPATCH_SOURCE_EVIDENCE_ROOT")
    if not selected or not evidence:
        pytest.skip("explicit admitted Source/profile and owned evidence root required")
    from agent_procutil.daemon_instances import daemon_test_lifecycle
    from agent_dispatch.client import DispatchClient
    from zdd import diagnostics, routing

    snapshot, evidence_root = Path(selected), Path(evidence)
    record = json.loads((snapshot / "install-source.json").read_text(encoding="utf-8"))
    application = snapshot / record["projects"]["agent-dispatch"]["application"]
    declaration = json.loads((application / "application.json").read_text(encoding="utf-8"))
    version = declaration["construction"]["runtime_version"]
    root = Path(os.environ["DISPATCH_NATIVE_INSTALL_ROOT"]) if os.environ.get(
        "DISPATCH_NATIVE_INSTALL_ROOT",
    ) else tmp_path / "app"
    slot = root / "versions" / version
    if slot.exists():
        for name in ("application.json", "runtime-source.json", "uv.lock", "pyproject.toml"):
            assert (slot / name).read_bytes() == (application / name).read_bytes()
    else:
        shutil.copytree(application, slot)
    profile = root / "profile"
    profile.mkdir(exist_ok=True)
    run = root / "run"
    routes = root / "routes"
    (profile / "uv.toml").write_text("", encoding="utf-8")
    token = uuid.uuid4().hex
    control_token = uuid.uuid4().hex
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("AGENT_DISPATCH")
        and key not in {
            "PYTHONPATH", "PYTHONHOME", "COPILOT_PLUGIN_ROOT", "COPILOT_AGENT_SESSION_ID",
            "UV_NO_CONFIG", "UV_CONFIG_FILE",
        }
    }
    environment.update({
        "HOME": str(profile), "USERPROFILE": str(profile),
        "AGENT_DISPATCH_INSTALL_DIR": str(root), "AGENT_DISPATCH_RUN_DIR": str(run),
        "AGENT_DISPATCH_ROUTING_DIR": str(routes),
        "AGENT_DISPATCH_DB": str(root / "data" / "tasks.db"),
        "AGENT_DISPATCH_HOST": "127.0.0.1",
        "AGENT_DISPATCH_TOKEN": token,
        "AGENT_DISPATCH_CONTROL_TOKEN": control_token,
        "AGENT_DISPATCH_GC_INTERVAL": "0",
        "AGENT_DISPATCH_SELF_UPDATE": "0",
        "AGENT_DISPATCH_SELF_RETIRE_POLL_S": "1",
        "AGENT_DISPATCH_SELF_RETIRE_CONFIRMATIONS": "1",
        "UV_CONFIG_FILE": str(profile / "uv.toml"),
        "UV_PYTHON_INSTALL_DIR": str(root / "python"),
        "UV_CACHE_DIR": str(root / "cache" / "uv"),
    })
    host = shutil.which("pwsh") if os.name == "nt" else None
    if os.name == "nt" and host is None:
        pytest.skip("native PowerShell required")
    command = [host, "-NoProfile", "-File", str(slot / "install.ps1")] if host else [
        "sh", str(slot / "install.sh"),
    ]
    results = []
    cleanup = []

    def invoke(phase, arguments=(), *, candidate=None, success=True):
        selected_command = command
        if candidate is not None:
            selected_command = [*command[:-1], str(candidate / "install.ps1")] if host else [
                "sh", str(candidate / "install.sh"),
            ]
        started = time.monotonic()
        result = subprocess.run(
            [*selected_command, *arguments], cwd=profile, env=environment,
            capture_output=True, text=True, timeout=300,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        elapsed = time.monotonic() - started
        results.append({"phase": phase, "seconds": elapsed, "exit": result.returncode})
        (evidence_root / f"native-{root.name}-{phase}.stdout.log").write_text(result.stdout, encoding="utf-8")
        (evidence_root / f"native-{root.name}-{phase}.stderr.log").write_text(result.stderr, encoding="utf-8")
        assert (result.returncode == 0) is success, result.stderr[-12000:]
        assert elapsed <= 300
        return result

    try:
        with daemon_test_lifecycle(
            owner="dispatch-source-native", registry_root=tmp_path / "instances",
            cleanup_results=cleanup,
        ) as overlay:
            environment.update(overlay)
            if not repair_matrix:
                invoke("stage", ["--stage"])
                assert not (root / "current-version").exists()
                assert routing.read_table(routes) is None
                assert not (run / "endpoint.json").exists()
            invoke("install")
            active = routing.Endpoint.from_dict(routing.read_table(routes)["active"])
            assert active is not None
            assert active.version == version
            birth = diagnostics.process_start_time(active.pid)
            assert birth and birth == active.process_start_time
            with DispatchClient(active.base_url, token=token, control_token=control_token) as client:
                health = client.health()
                assert health["slot"]["role"] == "active"
                assert health["version"] == version
                task = client.create("unfinished fixture work", repo="example.com/acme/widget")
                claimed = client.claim(worker_id="fixture-held", repo="example.com/acme/widget")
                assert claimed["id"] == task["id"]
                queued = client.create("reserved fixture work", repo="example.com/acme/widget")
                reservation = client.reserve_spawn(queued["id"])
                before = client.get(task["id"])
                if not repair_matrix:
                    invoke("repeat")
                    assert routing.Endpoint.from_dict(routing.read_table(routes)["active"]) == active
                    assert diagnostics.process_start_time(active.pid) == birth
                    assert client.get(task["id"]) == before
                    assert client.get(queued["id"])["status"] == "queued"
                    assert reservation
            if repair_matrix:
                if os.name != "nt":
                    pytest.fail("native identity/exit portion requires Windows")
                from agent_dispatch.cutover_process import _identity_running

                def exited(endpoint):
                    started = time.monotonic()
                    while _identity_running(endpoint.pid, endpoint.process_start_time) is not False:
                        assert time.monotonic() - started < 30, "outgoing owner did not self-retire"
                        time.sleep(0.1)
                    results.append({
                        "phase": "outgoing-native-exit", "pid": endpoint.pid,
                        "birth": endpoint.process_start_time, "seconds": time.monotonic() - started,
                    })

                replacement = slot.with_name(version + ".repair." + uuid.uuid4().hex)
                shutil.copytree(application, replacement)
                invoke("repair-stage", ["--stage"], candidate=replacement)
                assert routing.Endpoint.from_dict(routing.read_table(routes)["active"]) == active
                invoke("repair-update", candidate=replacement)
                successor = routing.Endpoint.from_dict(routing.read_table(routes)["active"])
                assert successor.pid != active.pid
                assert successor.version == replacement.name
                assert diagnostics.process_start_time(successor.pid) == successor.process_start_time
                exited(active)
                invoke("reverse")
                restored = routing.Endpoint.from_dict(routing.read_table(routes)["active"])
                assert restored.version == version and restored.pid != active.pid
                exited(successor)
                with DispatchClient(restored.base_url, token=token, control_token=control_token) as client:
                    assert client.get(task["id"]) == before
                    assert client.get(queued["id"])["status"] == "queued"
                    retained = client.reserve_spawn(queued["id"])
                    assert retained["reserved"] is False
                    assert retained["reservation"] == reservation["reservation"]
                failed = slot.with_name(version + ".repair." + uuid.uuid4().hex)
                shutil.copytree(application, failed)
                wheel = next((failed / "wheels").glob("agent_dispatch-*.whl"))
                original = wheel.read_bytes()
                wheel.write_bytes(b"not-a-wheel")
                route_before = routing.read_table(routes)
                marker_before = (root / "current-version").read_bytes()
                invoke("failed-stage", ["--stage"], candidate=failed, success=False)
                assert routing.read_table(routes) == route_before
                assert (root / "current-version").read_bytes() == marker_before
                wheel.write_bytes(original)
                invoke("stage-retry", ["--stage"], candidate=failed)
                assert routing.read_table(routes) == route_before
                assert (root / "current-version").read_bytes() == marker_before
            assert (root / "current-version").read_text(encoding="utf-8").strip() == version
    finally:
        (evidence_root / f"native-source-results-{root.name}.json").write_text(
            json.dumps({
                "source": record["source_commit"], "generation": version, "install_root": str(root),
                "commands": results, "cleanup": [asdict(item) for item in cleanup],
                "scope": "generated source frontdoors under renewed contained native test lifecycle",
            }, indent=2), encoding="utf-8",
        )
    assert cleanup and all(item.status in {"settled", "missing"} for item in cleanup)


def test_generated_source_stage_install_repeat_native(tmp_path):
    _qualify_generated_source(tmp_path)


def test_generated_source_repair_reverse_native(tmp_path):
    _qualify_generated_source(tmp_path, repair_matrix=True)
