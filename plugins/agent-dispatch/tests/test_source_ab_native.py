"""Distinct admitted Git Sources through actual generated application consumers."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

import pytest

from tests.test_source_caller_job_native import _NativeProcess

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows installed Source A/B")


def test_distinct_source_forward_reverse_and_failed_candidate_retry(tmp_path):
    a_name = os.environ.get("DISPATCH_SOURCE_A_SNAPSHOT")
    b_name = os.environ.get("DISPATCH_QUALIFIED_SOURCE_SNAPSHOT")
    root_name = os.environ.get("DISPATCH_AB_NATIVE_ROOT")
    evidence_name = os.environ.get("DISPATCH_SOURCE_EVIDENCE_ROOT")
    if not all((a_name, b_name, root_name, evidence_name)):
        pytest.skip("explicit admitted A/B, private native root and evidence destination required")
    from agent_procutil.daemon_instances import daemon_test_lifecycle
    from agent_dispatch.client import DispatchClient
    from zdd import diagnostics, routing

    root, evidence = Path(root_name), Path(evidence_name)
    selections = {}
    for label, name in (("a", a_name), ("b", b_name)):
        snapshot = Path(name)
        record = json.loads((snapshot / "install-source.json").read_text(encoding="utf-8"))
        application = snapshot / record["projects"]["agent-dispatch"]["application"]
        declaration = json.loads((application / "application.json").read_text(encoding="utf-8"))
        generation = declaration["construction"]["runtime_version"]
        slot = root / "versions" / generation
        shutil.copytree(application, slot)
        selections[label] = {
            "source": record["source_commit"], "generation": generation,
            "application": application, "slot": slot,
        }
    assert selections["a"]["source"] != selections["b"]["source"]
    assert selections["a"]["generation"] != selections["b"]["generation"]
    profile = root / "profile"
    profile.mkdir()
    (profile / "uv.toml").write_text("", encoding="utf-8")
    token, control = uuid.uuid4().hex, uuid.uuid4().hex
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("AGENT_DISPATCH") and key not in {
            "PYTHONPATH", "PYTHONHOME", "COPILOT_PLUGIN_ROOT", "COPILOT_AGENT_SESSION_ID",
            "UV_NO_CONFIG", "UV_CONFIG_FILE", "__PYVENV_LAUNCHER__",
        }
    }
    environment.update({
        "HOME": str(profile), "USERPROFILE": str(profile),
        "AGENT_DISPATCH_INSTALL_DIR": str(root),
        "AGENT_DISPATCH_RUN_DIR": str(root / "run"),
        "AGENT_DISPATCH_ROUTING_DIR": str(root / "routes"),
        "AGENT_DISPATCH_DB": str(root / "data" / "tasks.db"),
        "AGENT_DISPATCH_HOST": "127.0.0.1",
        "AGENT_DISPATCH_TOKEN": token, "AGENT_DISPATCH_CONTROL_TOKEN": control,
        "AGENT_DISPATCH_GC_INTERVAL": "0", "AGENT_DISPATCH_SELF_UPDATE": "0",
        "AGENT_DISPATCH_SELF_RETIRE_POLL_S": "1",
        "AGENT_DISPATCH_SELF_RETIRE_CONFIRMATIONS": "1",
        "UV_CONFIG_FILE": str(profile / "uv.toml"),
        "UV_PYTHON_INSTALL_DIR": str(root / "python"),
        "UV_CACHE_DIR": str(root / "cache" / "uv"),
    })
    host = shutil.which("pwsh")
    assert host
    phases, owners, cleanup, pins = [], [], [], []
    routes = root / "routes"
    proof = {
        "source_a": selections["a"]["source"], "source_b": selections["b"]["source"],
        "scope": "distinct admitted Sources; contained native consumers",
    }

    def invoke(phase, slot, arguments=(), *, success=True, global_command=None):
        command = global_command or slot / "install.ps1"
        child_environment = dict(environment)
        if global_command is not None:
            child_environment.pop("AGENT_DISPATCH_INSTALL_DIR", None)
        started = time.monotonic()
        result = subprocess.run(
            [host, "-NoProfile", "-File", str(command), *arguments],
            cwd=profile, env=child_environment, capture_output=True, text=True,
            timeout=300, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        elapsed = time.monotonic() - started
        phases.append({"phase": phase, "exit": result.returncode, "seconds": elapsed})
        (evidence / f"ab-{phase}.stdout.log").write_text(result.stdout, encoding="utf-8")
        (evidence / f"ab-{phase}.stderr.log").write_text(result.stderr, encoding="utf-8")
        assert (result.returncode == 0) is success, result.stderr[-12000:]
        assert elapsed <= 300
        return result

    def serving(generation):
        endpoint = routing.Endpoint.from_dict(routing.read_table(routes)["active"])
        assert endpoint.version == generation
        assert (root / "current-version").read_text(encoding="utf-8").strip() == generation
        assert (root / "last-known-good").read_text(encoding="utf-8").strip() == generation
        selected = selections["a"] if generation == selections["a"]["generation"] else selections["b"]
        helper = (root / "versions" / generation / "install.py").read_bytes()
        assert helper == (selected["application"] / "install.py").read_bytes()
        assert diagnostics.process_start_time(endpoint.pid) == endpoint.process_start_time
        pin = _NativeProcess(endpoint.pid, "windows:" + endpoint.process_start_time)
        assert pin.running()
        pins.append(pin)
        owners.append({
            **endpoint.to_dict(), "git_source": selected["source"],
            "consumed_helper_sha256": hashlib.sha256(helper).hexdigest(),
        })
        return endpoint, pin

    def retired(endpoint, pin):
        until = time.monotonic() + 30
        while pin.running():
            assert time.monotonic() < until, "outgoing owner did not self-retire"
            time.sleep(0.1)
        assert pin.exit_code() == 0
        phases.append({
            "phase": "outgoing-self-retired", "pid": endpoint.pid,
            "birth": endpoint.process_start_time, "exit": pin.exit_code(),
        })

    def client(endpoint):
        return DispatchClient(endpoint.base_url, token=token, control_token=control)

    try:
        with daemon_test_lifecycle(
            owner="dispatch-distinct-source-native", registry_root=tmp_path / "instances",
            cleanup_results=cleanup,
        ) as overlay:
            environment.update(overlay)
            invoke("install-a", selections["a"]["slot"])
            a, a_pin = serving(selections["a"]["generation"])
            with client(a) as api:
                task = api.create("held A/B fixture", repo="example.com/acme/widget")
                claimed = api.claim(worker_id="fixture-held", repo="example.com/acme/widget", lease_seconds=3600)
                assert claimed["id"] == task["id"]
                queued = api.create("reserved A/B fixture", repo="example.com/acme/widget")
                reservation = api.reserve_spawn(queued["id"])
                held_before = api.get(task["id"])
            unfinished = root / "data" / "unfinished-fixture.json"
            unfinished.write_text('{"unfinished":true}', encoding="utf-8")
            data_before = unfinished.read_bytes()

            def preserved(endpoint):
                with client(endpoint) as api:
                    assert api.health()["slot"]["role"] == "active"
                    assert api.get(task["id"]) == held_before
                    retained = api.reserve_spawn(queued["id"])
                    assert retained["reserved"] is False
                    assert retained["reservation"] == reservation["reservation"]
                    assert api.get(queued["id"])["status"] == "queued"
                assert unfinished.read_bytes() == data_before

            invoke("forward-b", selections["b"]["slot"])
            b, b_pin = serving(selections["b"]["generation"])
            assert b.pid != a.pid
            preserved(b)
            retired(a, a_pin)
            invoke("reverse-a", selections["a"]["slot"])
            restored, restored_pin = serving(selections["a"]["generation"])
            assert restored.pid != a.pid
            preserved(restored)
            retired(b, b_pin)

            candidate = selections["b"]["slot"].with_name(
                selections["b"]["generation"] + ".repair." + uuid.uuid4().hex,
            )
            shutil.copytree(selections["b"]["application"], candidate)
            wheel = next((candidate / "wheels").glob("agent_dispatch-*.whl"))
            original = wheel.read_bytes()
            wheel.write_bytes(b"not-a-wheel")
            before_route = routing.read_table(routes)
            before_markers = {
                name: (root / name).read_bytes() for name in ("current-version", "last-known-good")
            }
            invoke("failed-candidate-stage", candidate, ["--stage"], success=False)
            assert routing.read_table(routes) == before_route
            assert all((root / name).read_bytes() == value for name, value in before_markers.items())
            preserved(restored)
            wheel.write_bytes(original)
            invoke("candidate-stage-retry", candidate, ["--stage"])
            invoke("candidate-retry-activate", candidate)
            final, final_pin = serving(candidate.name)
            preserved(final)
            retired(restored, restored_pin)
            global_entry = profile / ".local" / "bin" / "agent-dispatch.ps1"
            version = invoke("root-unset-version", candidate, ["--version"], global_command=global_entry)
            assert candidate.name in "".join(version.stdout.split())
            health = invoke("root-unset-health", candidate, ["health"], global_command=global_entry)
            assert json.loads(health.stdout)["slot"]["pid"] == final.pid
            assert final_pin.running()
            proof["claims_reservations_unfinished_data_preserved"] = True
    finally:
        proof["phases"], proof["owners"] = phases, owners
        proof["cleanup"] = [asdict(item) for item in cleanup]
        proof["all_retained_native_handles_exited"] = all(not pin.running() for pin in pins)
        for pin in pins:
            pin.close()
        (evidence / "distinct-ab-native-proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    assert proof["all_retained_native_handles_exited"]
    assert cleanup and all(item.status in {"settled", "missing"} for item in cleanup)
